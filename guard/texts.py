"""Тексты бота в чате: сервисные сообщения модерации, профиль, задолженность, списки игр.

Админ чата (право ⚙️ настройки) меняет их в личке: ⚙️ Мои чаты → чат → ✉️ Тексты. У каждого текста:
свой текст (HTML как прислали: премиум-эмодзи, жирный, ссылки), картинка над ним и кнопки-ссылки.
Владелец бота меняет тексты для всех чатов сразу: /admin → ✉️ Тексты бота (это чат 0).
Порядок: текст чата → текст бота для всех → стандартный. Подстановки в фигурных скобках бот заменяет сам.
Цвет и премиум-значок кнопок ставит только владелец бота (см. richtext.parse_buttons)."""

import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from guard import db, out, richtext
from guard.richtext import Button

log = logging.getLogger(__name__)

# ключ: (раздел, название, стандартный текст, {подстановка: (что это, пример)})
TEXTS: dict[str, tuple[str, str, str, dict[str, tuple[str, str]]]] = {
    "mod_ban": ("Модерация", "🚫 Блокировка",
                "🚫 Пользователь {user} заблокирован.\n\nПричина: {reason}\nСрок: {duration}\nМодератор: {admin}",
                {"user": ("кого", "<b>Олег</b>"), "reason": ("причина", "спам"), "duration": ("срок", "навсегда"),
                 "admin": ("кто наказал", "Аня")}),
    "mod_unban": ("Модерация", "🔓 Разблокировка",
                  "🔓 Пользователь {user} разблокирован.\n\nМодератор: {admin}",
                  {"user": ("кого", "<b>Олег</b>"), "admin": ("кто снял", "Аня")}),
    "mod_mute": ("Модерация", "🔇 Ограничение сообщений",
                 "🔇 Пользователю {user} выдано ограничение сообщений.\n\nСрок: {duration}\nПричина: {reason}\n"
                 "Модератор: {admin}",
                 {"user": ("кому", "<b>Олег</b>"), "duration": ("срок", "1 час"), "reason": ("причина", "флуд"),
                  "admin": ("кто наказал", "Аня")}),
    "mod_unmute": ("Модерация", "🔊 Снятие ограничения",
                   "🔊 Ограничение пользователя {user} снято.\n\nМодератор: {admin}",
                   {"user": ("с кого", "<b>Олег</b>"), "admin": ("кто снял", "Аня")}),
    "mod_warn": ("Модерация", "⚠️ Предупреждение",
                 "⚠️ Пользователь {user} получил предупреждение.\n\nПричина: {reason}\n"
                 "Всего предупреждений: {count}\nМодератор: {admin}",
                 {"user": ("кому", "<b>Олег</b>"), "reason": ("причина", "мат"), "count": ("сколько стало", "2/3"),
                  "admin": ("кто выдал", "Аня")}),
    "mod_unwarn": ("Модерация", "✅ Снятие предупреждения",
                   "✅ Предупреждение пользователя {user} снято.\n\nОсталось предупреждений: {count}",
                   {"user": ("с кого", "<b>Олег</b>"), "count": ("сколько осталось", "1/3")}),
    "mod_kick": ("Модерация", "👢 Исключение",
                 "👢 Пользователь {user} исключён из чата.\n\nПричина: {reason}\nМодератор: {admin}",
                 {"user": ("кого", "<b>Олег</b>"), "reason": ("причина", "флуд"), "admin": ("кто выгнал", "Аня")}),

    "role_mod": ("Роли и доверие", "🛡 Назначен модератор",
                 "🛡 Пользователь {user} назначен модератором.\n\nНазначил: {admin}",
                 {"user": ("кого", "<b>Олег</b>"), "admin": ("кто назначил", "Аня")}),
    "role_admin": ("Роли и доверие", "⭐ Назначен админ",
                   "⭐ Пользователь {user} назначен админом бота.\n\nНазначил: {admin}",
                   {"user": ("кого", "<b>Олег</b>"), "admin": ("кто назначил", "Аня")}),
    "role_removed": ("Роли и доверие", "🚫 Права сняты",
                     "🚫 У пользователя {user} сняты права ({role}).\n\nСнял: {admin}",
                     {"user": ("с кого", "<b>Олег</b>"), "role": ("какая роль была", "модератор"),
                      "admin": ("кто снял", "Аня")}),
    "trust_add": ("Роли и доверие", "🤍 В белом списке",
                  "🤍 Пользователь {user} добавлен в белый список: ему можно ссылки и пересылки.\n\nДобавил: {admin}",
                  {"user": ("кого", "<b>Олег</b>"), "admin": ("кто добавил", "Аня")}),
    "trust_remove": ("Роли и доверие", "🖤 Убран из белого списка",
                     "🖤 Пользователь {user} убран из белого списка.\n\nУбрал: {admin}",
                     {"user": ("кого", "<b>Олег</b>"), "admin": ("кто убрал", "Аня")}),

    "profile": ("Профиль", "👤 Профиль участника",
                "👤 <b>Профиль</b>\n➖➖➖➖➖➖➖\nИмя: {mention}\nUsername: {username}\nID: <code>{user_id}</code>\n"
                "Статус: {status}\n➖➖➖➖➖➖➖\n📊 <b>Активность</b>\nДата вступления: {join_date}\n"
                "Сообщений: {messages} · за сутки {messages_24h}\nПоследняя активность: {last_active}\n"
                "➖➖➖➖➖➖➖\n⭐ <b>Показатели</b>\nРепутация: {reputation}\nРанг: {rank}\n"
                "Достижения: {achievements}\nПредупреждения: {warnings}\n➖➖➖➖➖➖➖\n💰 <b>Финансы</b>\n"
                "Баланс: {balance}\nЗадолженность: {debt}",
                {"mention": ("имя ссылкой", "<a href='tg://user?id=1'>Олег</a>"), "name": ("полное имя", "Олег Петров"),
                 "username": ("юзернейм", "@oleg"), "user_id": ("ID", "123456789"), "status": ("роль в чате", "Участник"),
                 "join_date": ("с какого числа в чате", "01.05.2026"), "messages": ("всего сообщений", "1 240"),
                 "messages_24h": ("сообщений за сутки", "37"), "last_active": ("последняя активность", "сегодня в 18:30"),
                 "reputation": ("репутация", "15"), "rank": ("ранг", "12 · Знаток"),
                 "achievements": ("достижения", "пока нет"), "warnings": ("предупреждения", "1/3"),
                 "balance": ("монеты", "3 500 🪙"), "debt": ("задолженность", "нет")}),

    "debt": ("Задолженность", "💳 Задолженность",
             "За пользователем {user} числится задолженность.\n\nДата: {date}\nСпонсор: {sponsor}\nПриз: {prize}",
             {"user": ("кто должен получить", "<b>Олег</b>"), "date": ("дата и время", "21.11.2026 21:05"),
              "sponsor": ("спонсор", "Аня"), "prize": ("приз", "500 🪙")}),

    "game_list": ("Игры", "📋 Список игры",
                  "🎰 <b>{game}</b>{fk}\n➖➖➖➖➖➖➖\n{list}\n➖➖➖➖➖➖➖\nИгроков: {count}\n{how}",
                  {"game": ("название игры", "Рулетка"), "fk": ("строка ФК", " · ФК 30"),
                   "list": ("сам список", "1. Олег @oleg\n2. Аня @anya"), "count": ("сколько записалось", "2"),
                   "how": ("как записаться или хэштег", "#рулетка")}),
    "game_closed": ("Игры", "🔒 Список закрыт",
                    "🔒 Список закрыт.\n\nКоличество игроков: {count}\nВсем удачи! 🍀",
                    {"count": ("сколько игроков", "36")}),
    "game_random": ("Игры", "🎲 Рандом",
                    "🎲 Рандом от {from} до {to}\n\nВыпало число: <b>{number}</b>",
                    {"from": ("от", "1"), "to": ("до", "36"), "number": ("выпавшее число", "17")}),

    "activity": ("Активность", "📊 Активность чата",
                 "📊 <b>Активность</b> · {chat_name}\n➖➖➖➖➖➖➖\n🗓 {period}\n💬 Сообщений: <b>{messages}</b>\n"
                 "👥 Писали: <b>{members}</b>\n➖➖➖➖➖➖➖\n🏆 <b>Самые активные</b>\n{top}",
                 {"chat_name": ("название чата", "Круиз"), "period": ("за какой период", "Сегодня с 00:00"),
                  "messages": ("сколько сообщений", "1 239"), "members": ("сколько человек писали", "46"),
                  "top": ("топ-10 строками", "🥇 Олег · 146\n🥈 Аня · 127\n🥉 Ира · 117\n4. Макс · 90")}),
    "welcome": ("Приветствие", "👋 Приветствие новичка",
                "👋 Привет, {user}!\nДобро пожаловать в <b>{chat}</b>, ты у нас {count}-й участник.\n"
                "Осмотрись и чувствуй себя как дома 🙂",
                {"user": ("имя ссылкой", "<a href='tg://user?id=1'>Олег</a>"), "name": ("имя без ссылки", "Олег"),
                 "username": ("юзернейм или имя", "@oleg"), "chat": ("название чата", "Круиз"),
                 "count": ("какой по счёту участник", "47")}),
    "captcha": ("Приветствие", "🛡 Капча для новичка",
                "🛡 {user}, подтверди, что ты не бот.\n{task}\n⏳ На это {minutes} мин, потом {fail}.",
                {"user": ("имя ссылкой", "<a href='tg://user?id=1'>Олег</a>"),
                 "task": ("задание", "Сколько будет 4 + 3?"), "minutes": ("сколько минут", "3"),
                 "fail": ("что будет, если не пройти", "удалю из чата")}),
    "captcha_fail": ("Приветствие", "🚪 Не прошёл капчу",
                     "🚪 {user} не прошёл проверку: {fail}.",
                     {"user": ("имя ссылкой", "<a href='tg://user?id=1'>Олег</a>"),
                      "fail": ("что сделано", "удалён из чата")}),
}
GROUP_ICONS = {"Модерация": "🛡", "Роли и доверие": "⭐", "Профиль": "👤", "Задолженность": "💳", "Игры": "🎰",
               "Активность": "📊", "Приветствие": "👋"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_texts (
    chat_id  INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    html     TEXT    NOT NULL DEFAULT '',
    photo    TEXT    NOT NULL DEFAULT '',
    buttons  TEXT    NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT    NOT NULL DEFAULT '',     -- ID того, кто поменял в боте, или "web"
    PRIMARY KEY (chat_id, key)
);
"""
GLOBAL = 0   # тексты бота для всех чатов
MAX_HTML = 3000
MAX_CAPTION = 1024
NO_REASON = "не указана"
AUTO_ADMIN = "автомодерация"


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    async with db.conn().execute("PRAGMA table_info(chat_texts)") as cur:
        cols = {r[1] for r in await cur.fetchall()}
    for col, ddl in (("updated_at", "INTEGER NOT NULL DEFAULT 0"), ("updated_by", "TEXT NOT NULL DEFAULT ''")):
        if col not in cols:
            await db.conn().execute(f"ALTER TABLE chat_texts ADD COLUMN {col} {ddl}")
    await db.conn().commit()


def groups() -> list[str]:
    seen: list[str] = []
    for group, *_ in TEXTS.values():
        if group not in seen:
            seen.append(group)
    return seen


def keys_of(group: str) -> list[str]:
    return [k for k, v in TEXTS.items() if v[0] == group]


def fill(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def sample(key: str, template: str) -> str:
    return fill(template, {k: ex for k, (_, ex) in TEXTS[key][3].items()})


@dataclass
class Msg:
    html: str
    photo: str = ""
    buttons: list[list[Button]] = field(default_factory=list)


def parse_buttons(raw: str, *, design: bool = False) -> tuple[list[list[Button]], list[str]]:
    """«Текст - ссылка», несколько в строке через |. design: можно цвет и премиум-значок (только владелец)."""
    return richtext.parse_buttons(raw, design=design)


def buttons_text(rows: list[list[Button]]) -> str:
    return richtext.buttons_text(rows)


async def override(chat_id: int, key: str) -> dict | None:
    async with db.conn().execute("SELECT html, photo, buttons, updated_at, updated_by FROM chat_texts "
                                 "WHERE chat_id = ? AND key = ?", (chat_id, key)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def set_text(chat_id: int, key: str, *, by: str = "", **fields) -> None:
    """Сохранить поля текста (html, photo, buttons). by: кто поменял (ID в боте или "web")."""
    if key not in TEXTS or set(fields) - {"html", "photo", "buttons"}:
        raise ValueError(key)
    await db.conn().execute("INSERT OR IGNORE INTO chat_texts (chat_id, key) VALUES (?, ?)", (chat_id, key))
    fields = {**fields, "updated_at": db.now(), "updated_by": str(by)}
    sets = ", ".join(f"{k} = ?" for k in fields)
    await db.conn().execute(f"UPDATE chat_texts SET {sets} WHERE chat_id = ? AND key = ?",
                            (*fields.values(), chat_id, key))
    await db.conn().execute("DELETE FROM chat_texts WHERE html = '' AND photo = '' AND buttons = ''")
    await db.conn().commit()


async def reset(chat_id: int, key: str) -> None:
    await db.conn().execute("DELETE FROM chat_texts WHERE chat_id = ? AND key = ?", (chat_id, key))
    await db.conn().commit()


async def custom_keys(chat_id: int) -> set[str]:
    async with db.conn().execute("SELECT key FROM chat_texts WHERE chat_id = ?", (chat_id,)) as cur:
        return {r[0] for r in await cur.fetchall()}


async def template(chat_id: int, key: str) -> Msg:
    """Текст чата, если нет: текст бота для всех чатов, если нет: стандартный. Каждое поле по отдельности."""
    own = await override(chat_id, key) or {}
    common = (await override(GLOBAL, key) or {}) if chat_id != GLOBAL else {}
    rows, _ = parse_buttons(own.get("buttons") or common.get("buttons", ""), design=True)
    return Msg(html=own.get("html") or common.get("html") or TEXTS[key][2],
               photo=own.get("photo") or common.get("photo", ""), buttons=rows)


async def render(chat_id: int, key: str, **values) -> str:
    return fill((await template(chat_id, key)).html, values)


def markup(msg: Msg, values: dict | None = None, extra: InlineKeyboardMarkup | None = None):
    rows = [[InlineKeyboardButton(text=fill(b.text, values or {})[:64], url=b.url, style=b.style or None,
                                  icon_custom_emoji_id=b.icon or None) for b in row] for row in msg.buttons]
    if extra is not None:
        rows += list(extra.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


MEDIA_KINDS = ("video", "animation")


def media_of(value: str) -> tuple[str, str]:
    """Поле photo: просто file_id (картинка) или «video:file_id», «animation:file_id» (гифка)."""
    kind, _, fid = (value or "").partition(":")
    return (kind, fid) if kind in MEDIA_KINDS and fid else ("photo", value)


def media_value(message: Message) -> str:
    """Что сохранить в поле photo из присланного сообщения. Пусто: там нет картинки, видео или гифки."""
    if message.photo:
        return message.photo[-1].file_id
    if message.animation:
        return f"animation:{message.animation.file_id}"
    if message.video:
        return f"video:{message.video.file_id}"
    return ""


async def send_media(bot: Bot, chat_id: int, value: str, *, caption: str | None = None,
                     markup: InlineKeyboardMarkup | None = None, reply_to: int | None = None,
                     thread: int | None = None) -> Message:
    kind, fid = media_of(value)
    method = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation}[kind]
    return await method(chat_id, fid, caption=caption, reply_markup=markup, parse_mode="HTML",
                        reply_to_message_id=reply_to, message_thread_id=thread if reply_to is None else None)


async def send(bot: Bot, chat: dict, key: str, /, *, reply_to: Message | None = None, thread: int | None = None,
               ttl: int | None = None, extra: InlineKeyboardMarkup | None = None, **values) -> Message | None:
    """Сообщение по шаблону чата: картинка (если есть), текст, кнопки. ttl: через сколько удалить
    (по умолчанию как остальные ответы бота в этом чате; 0 не удалять)."""
    if "reason" in TEXTS[key][3] and not values.get("reason"):
        values["reason"] = NO_REASON
    t = await template(chat["chat_id"], key)
    html = fill(t.html, values)
    kb = markup(t, values, extra)
    ttl = chat.get("reply_ttl", 0) if ttl is None else ttl
    if t.photo:
        fits = len(html) <= MAX_CAPTION
        try:
            sent = await send_media(bot, chat["chat_id"], t.photo, caption=html if fits else None,
                                    markup=kb if fits else None,
                                    reply_to=reply_to.message_id if reply_to else None, thread=thread)
            out.delete_later(bot, chat["chat_id"], sent.message_id, ttl)
            if fits:
                return sent
        except Exception as exc:
            log.warning("Картинка текста %s не отправилась: %s", key, exc)
    return await out.say(bot, chat["chat_id"], html, reply_to=reply_to, ttl=ttl, markup=kb, thread=thread)


async def announce_warn(bot: Bot, chat: dict, res, user: str, reason: str, admin: str, *,
                        reply_to: Message | None = None, thread: int | None = None) -> None:
    """Предупреждение, а на лимите ещё и наказание (мут или бан) своим текстом."""
    from guard.parse import human_duration
    await send(bot, chat, "mod_warn", reply_to=reply_to, thread=thread, user=user, reason=reason, admin=admin,
               count=f"{res.count}/{res.limit}")
    why = f"{res.limit}/{res.limit} предупреждений"
    th = out.thread_of(reply_to) if reply_to else thread
    if res.escalated == "mute":
        await send(bot, chat, "mod_mute", thread=th, user=user, reason=why, admin=admin,
                   duration=human_duration(chat["warn_mute_min"] * 60))
    elif res.escalated == "ban":
        await send(bot, chat, "mod_ban", thread=th, user=user, reason=why, admin=admin, duration="навсегда")
