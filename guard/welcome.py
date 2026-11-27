"""Приветствие новичков и капча.

Новичок зашёл (protect.on_join, вход уже не повторный и не выгнан режимом атаки):
  * капча включена: новичку нельзя писать, пока не нажмёт правильную кнопку. Виды: кнопка «Я не бот»,
    пример (выбрать ответ), эмодзи (нажать названный). Не успел или ошибся: удалить из чата, оставить
    без права писать или забанить (как выбрал админ). Прошёл: права как у всех, дальше приветствие;
  * приветствие: текст чата 👋 (премиум-эмодзи, картинка, видео или гифка, кнопки), удаляется через
    заданное время, новое убирает прошлое, чтобы при наплыве не было стены;
  * после приветствия Бакси добавляет свою фразу (событие join), если это не выключено.
Пока в чате включено приветствие или капча, Бакси сам на вход не здоровается (событие greet_mode).

Капчи хранятся в базе: после перезапуска бота просроченные досматривает планировщик (sweep)."""

import asyncio
import logging
import random

from aiogram import Bot
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, User

from guard import actions, baxi, db, out, perms, texts

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS captcha (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    msg_id   INTEGER NOT NULL DEFAULT 0,
    answer   TEXT    NOT NULL,
    name     TEXT    NOT NULL DEFAULT '',
    until    INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS captcha_until ON captcha (until);
"""
MODES = {"": "выкл", "button": "кнопка «Я не бот»", "math": "пример", "emoji": "эмодзи"}
FAILS = {"kick": ("удалю из чата", "удалён из чата"), "mute": ("не дам писать", "остался без права писать"),
         "ban": ("забаню", "заблокирован")}
EMOJI = {"🍎": "яблоко", "🚗": "машину", "🐶": "собаку", "⚽": "мяч", "🌙": "луну", "🎸": "гитару", "🍕": "пиццу",
         "🐱": "кошку", "✈️": "самолёт", "🌵": "кактус", "🎈": "шарик", "🔑": "ключ"}
NO_WRITE = ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
                           can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
                           can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
                           can_add_web_page_previews=False)
_tasks: set[asyncio.Task] = set()


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()


def greets(chat: dict) -> bool:
    """Модератор сам встречает новичков в этом чате (приветствие или капча)."""
    return bool(chat.get("welcome_on") or chat.get("captcha"))


async def sync_baxi(chat: dict) -> None:
    """Сказать Бакси, кто встречает новичков: пока мы, он сам на вход не здоровается."""
    try:
        await baxi.send_event({"type": "greet_mode", "chat_id": chat["chat_id"], "user_id": 0, "on": greets(chat),
                               "ts": db.now()})
    except Exception:
        log.exception("Не передал Бакси режим приветствия")


async def sync_all() -> None:
    """При запуске: чаты, где встречаем мы (если Бакси перезапускался с чистой базой или пропустил)."""
    if not baxi.configured():
        return
    for chat in await db.all_chats():
        if greets(chat):
            await sync_baxi(chat)


# ---------- вход ----------

async def on_join(bot: Bot, chat: dict, user: User) -> None:
    if not greets(chat) or chat["attack_until"] > db.now():
        return
    try:
        if chat["captcha"] and not perms.is_owner(user.id) and not await db.rights_of(chat["chat_id"], user.id):
            if await start_captcha(bot, chat, user):
                return
        await greet(bot, chat, user)
    except Exception:
        log.exception("Не встретил новичка %s в %s", user.id, chat["chat_id"])


async def greet(bot: Bot, chat: dict, user: User) -> None:
    """Приветствие (если включено) и фраза Бакси (если не выключена)."""
    chat_id = chat["chat_id"]
    name = out.full_name(user)
    if chat["welcome_on"]:
        try:
            count = await bot.get_chat_member_count(chat_id)
        except Exception:
            count = 0
        if chat["welcome_clean"]:
            prev = int(await db.kv_get(f"welcome_last:{chat_id}", "0") or 0)
            if prev:
                try:
                    await bot.delete_message(chat_id, prev)
                except Exception:
                    pass
        sent = await texts.send(bot, chat, "welcome", ttl=chat["welcome_ttl"] * 60, user=out.mention(user.id, name),
                                name=out.esc(name), username=f"@{user.username}" if user.username else out.esc(name),
                                chat=out.esc(chat.get("title") or ""), count=str(count or "новый"))
        if sent:
            await db.kv_set(f"welcome_last:{chat_id}", str(sent.message_id))
    if chat["welcome_baxi"]:
        await baxi.send_event({"type": "join", "chat_id": chat_id, "user_id": user.id, "name": name[:64],
                               "ts": db.now()})


# ---------- капча ----------

def make_task(mode: str, user_id: int) -> tuple[str, InlineKeyboardMarkup, str]:
    """(текст задания, кнопки, правильный ответ). Кнопки нажимает только сам новичок."""
    def b(text: str, value: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"cp:{user_id}:{value}")
    if mode == "math":
        x, y = random.randint(1, 9), random.randint(1, 9)
        right = x + y
        wrong = random.sample([n for n in range(2, 19) if n != right], 3)
        options = [right, *wrong]
        random.shuffle(options)
        return (f"Сколько будет {x} + {y}?", InlineKeyboardMarkup(inline_keyboard=[[b(str(n), str(n)) for n in options]]),
                str(right))
    if mode == "emoji":
        options = random.sample(list(EMOJI), 6)
        target = random.choice(options)
        rows = [[b(e, str(list(EMOJI).index(e))) for e in options[:3]], [b(e, str(list(EMOJI).index(e)))
                                                                          for e in options[3:]]]
        return f"Нажми на {EMOJI[target]}.", InlineKeyboardMarkup(inline_keyboard=rows), str(list(EMOJI).index(target))
    return ("Нажми кнопку ниже.", InlineKeyboardMarkup(inline_keyboard=[[b("✅ Я не бот", "ok")]]), "ok")


async def start_captcha(bot: Bot, chat: dict, user: User) -> bool:
    """True: капча выдана. False: не вышло (нет прав ограничивать), тогда просто приветствуем."""
    chat_id = chat["chat_id"]
    try:
        await bot.restrict_chat_member(chat_id, user.id, NO_WRITE, use_independent_chat_permissions=True)
    except Exception as exc:
        log.info("Капча: не ограничил %s в %s: %s", user.id, chat_id, exc)
        return False
    task, markup, answer = make_task(chat["captcha"], user.id)
    minutes = max(1, chat["captcha_min"])
    sent = await texts.send(bot, chat, "captcha", ttl=0, extra=markup, user=out.mention(user.id, out.full_name(user)),
                            task=task, minutes=str(minutes), fail=FAILS.get(chat["captcha_fail"], FAILS["kick"])[0])
    await db.conn().execute("INSERT OR REPLACE INTO captcha (chat_id, user_id, msg_id, answer, name, until) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (chat_id, user.id, sent.message_id if sent else 0, answer,
                                                          out.full_name(user)[:64], db.now() + minutes * 60))
    await db.conn().commit()
    task_ = asyncio.create_task(_expire_later(bot, chat_id, user.id, minutes * 60 + 1))
    _tasks.add(task_)
    task_.add_done_callback(_tasks.discard)
    return True


async def pending(chat_id: int, user_id: int) -> dict | None:
    async with db.conn().execute("SELECT * FROM captcha WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _take(chat_id: int, user_id: int) -> dict | None:
    """Забрать капчу (один раз: кнопка и таймер не сработают дважды)."""
    row = await pending(chat_id, user_id)
    if row is None:
        return None
    cur = await db.conn().execute("DELETE FROM captcha WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    await db.conn().commit()
    return row if cur.rowcount else None


async def answer(bot: Bot, chat_id: int, user: User, value: str) -> str:
    """Новичок нажал кнопку. 'pass', 'fail' или 'none' (капчи уже нет)."""
    row = await pending(chat_id, user.id)
    if row is None:
        return "none"
    if value == row["answer"]:
        if await _take(chat_id, user.id):
            await passed(bot, chat_id, user, row)
        return "pass"
    if await _take(chat_id, user.id):
        await failed(bot, chat_id, user.id, row)
    return "fail"


async def passed(bot: Bot, chat_id: int, user: User, row: dict) -> None:
    try:
        allowed = (await bot.get_chat(chat_id)).permissions or ChatPermissions(can_send_messages=True,
                                                                               can_send_other_messages=True)
        await bot.restrict_chat_member(chat_id, user.id, allowed, use_independent_chat_permissions=True)
    except Exception as exc:
        log.warning("Капча: не вернул права %s в %s: %s", user.id, chat_id, exc)
    if row["msg_id"]:
        try:
            await bot.delete_message(chat_id, row["msg_id"])
        except Exception:
            pass
    chat = await db.get_chat(chat_id)
    if chat:
        await greet(bot, chat, user)


async def failed(bot: Bot, chat_id: int, user_id: int, row: dict) -> None:
    chat = await db.get_chat(chat_id)
    if row["msg_id"]:
        try:
            await bot.delete_message(chat_id, row["msg_id"])
        except Exception:
            pass
    if chat is None:
        return
    how = chat["captcha_fail"] if chat["captcha_fail"] in FAILS else "kick"
    try:
        if how == "kick":
            await actions.kick(bot, chat, user_id, bot.id, "не прошёл капчу", quiet=True)
        elif how == "ban":
            await actions.ban(bot, chat, user_id, bot.id, reason="не прошёл капчу")
    except Exception as exc:
        log.info("Капча: не наказал %s в %s: %s", user_id, chat_id, exc)
    await texts.send(bot, chat, "captcha_fail", user=out.mention(user_id, row["name"]), fail=FAILS[how][1])


async def _expire_later(bot: Bot, chat_id: int, user_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    row = await pending(chat_id, user_id)
    if row and row["until"] <= db.now() and await _take(chat_id, user_id):
        await failed(bot, chat_id, user_id, row)


async def sweep(bot: Bot) -> None:
    """Планировщик: просроченные капчи (например, бот перезапускался, пока новичок думал)."""
    async with db.conn().execute("SELECT * FROM captcha WHERE until <= ? LIMIT 100", (db.now() - 5,)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        if await _take(row["chat_id"], row["user_id"]):
            await failed(bot, row["chat_id"], row["user_id"], row)


def cancel_all() -> None:
    for t in list(_tasks):
        t.cancel()
