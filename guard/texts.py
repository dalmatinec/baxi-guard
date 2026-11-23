"""Тексты бота в чате: сервисные сообщения модерации, профиль, задолженность, списки игр.

Админ чата (право ⚙️ настройки) меняет их в личке: ⚙️ Мои чаты → чат → ✉️ Тексты. У каждого текста:
свой текст (HTML как прислали: премиум-эмодзи, жирный, ссылки), картинка над ним и кнопки-ссылки.
Порядок: текст чата → стандартный. Подстановки в фигурных скобках бот заменяет сам."""

import logging
import re
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from guard import db, out

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
                "👤 <b>ПРОФИЛЬ УЧАСТНИКА</b>\n\nИмя: {mention}\n{name}\nUsername:\n{username}\nID:{user_id}\n"
                "Статус: {status}\n━━━━━━━━━━━━━━\n📊 <b>АКТИВНОСТЬ</b>\n\nДата вступления:\n{join_date}\n"
                "Всего сообщений:\n{messages}\nСообщений за 24 часа:\n{messages_24h}\nПоследняя активность:\n"
                "{last_active}\n━━━━━━━━━━━━━━\n⭐ <b>ПОКАЗАТЕЛИ</b>\n\nРепутация:\n{reputation}\nРанг:\n{rank}\n"
                "Достижения:\n{achievements}\nПредупреждения:\n{warnings}\n━━━━━━━━━━━━━━\n💰 <b>ФИНАНСЫ</b>\n\n"
                "Баланс:\n{balance}\nЗадолженность:\n{debt}",
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
                  "🎰 <b>{game}</b>{fk}\n━━━━━━━━━━━━━━\n{list}\n━━━━━━━━━━━━━━\nИгроков: {count}\n{how}",
                  {"game": ("название игры", "Рулетка"), "fk": ("строка ФК", " · ФК 30"),
                   "list": ("сам список", "1. Олег @oleg\n2. Аня @anya"), "count": ("сколько записалось", "2"),
                   "how": ("как записаться или хэштег", "#рулетка")}),
    "game_closed": ("Игры", "🔒 Список закрыт",
                    "🔒 Список закрыт.\n\nКоличество игроков: {count}\nВсем удачи! 🍀",
                    {"count": ("сколько игроков", "36")}),
    "game_random": ("Игры", "🎲 Рандом",
                    "🎲 Рандом от {from} до {to}\n\nВыпало число: <b>{number}</b>",
                    {"from": ("от", "1"), "to": ("до", "36"), "number": ("выпавшее число", "17")}),
}
GROUP_ICONS = {"Модерация": "🛡", "Роли и доверие": "⭐", "Профиль": "👤", "Задолженность": "💳", "Игры": "🎰"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_texts (
    chat_id  INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    html     TEXT    NOT NULL DEFAULT '',
    photo    TEXT    NOT NULL DEFAULT '',
    buttons  TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, key)
);
"""
MAX_HTML = 3000
MAX_CAPTION = 1024
NO_REASON = "не указана"
AUTO_ADMIN = "автомодерация"
_BUTTON = re.compile(r"^\s*(.+?)\s+[-–—]\s+((?:https?://|tg://)\S+)\s*$")


async def init() -> None:
    await db.conn().executescript(SCHEMA)
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
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)


def parse_buttons(raw: str) -> tuple[list[list[tuple[str, str]]], list[str]]:
    """«Текст - ссылка», несколько в строке через |. (кнопки, непонятые строки)."""
    rows, bad = [], []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split("|"):
            m = _BUTTON.match(part)
            if m:
                row.append((m.group(1)[:64], m.group(2)[:512]))
            elif part.strip():
                bad.append(part.strip())
        if row:
            rows.append(row[:4])
    return rows[:8], bad


def buttons_text(rows: list[list[tuple[str, str]]]) -> str:
    return "\n".join(" | ".join(f"{t} - {u}" for t, u in row) for row in rows)


async def override(chat_id: int, key: str) -> dict | None:
    async with db.conn().execute("SELECT html, photo, buttons FROM chat_texts WHERE chat_id = ? AND key = ?",
                                 (chat_id, key)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def set_text(chat_id: int, key: str, **fields) -> None:
    if key not in TEXTS or set(fields) - {"html", "photo", "buttons"}:
        raise ValueError(key)
    await db.conn().execute("INSERT OR IGNORE INTO chat_texts (chat_id, key) VALUES (?, ?)", (chat_id, key))
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
    own = await override(chat_id, key) or {}
    rows, _ = parse_buttons(own.get("buttons", ""))
    return Msg(html=own.get("html") or TEXTS[key][2], photo=own.get("photo", ""), buttons=rows)


async def render(chat_id: int, key: str, **values) -> str:
    return fill((await template(chat_id, key)).html, values)


def markup(msg: Msg, values: dict | None = None, extra: InlineKeyboardMarkup | None = None):
    rows = [[InlineKeyboardButton(text=fill(t, values or {})[:64], url=u) for t, u in row] for row in msg.buttons]
    if extra is not None:
        rows += list(extra.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send(bot: Bot, chat: dict, key: str, *, reply_to: Message | None = None, thread: int | None = None,
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
            sent = await bot.send_photo(
                chat["chat_id"], t.photo, caption=html if fits else None, reply_markup=kb if fits else None,
                parse_mode="HTML", reply_to_message_id=reply_to.message_id if reply_to else None,
                message_thread_id=thread if reply_to is None else None)
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
