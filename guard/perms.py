"""Права людей в боте и права самого бота в чате.

Админка в Telegram сама ничего в боте не даёт. Всё может только создатель чата и владелец бота (OWNER_IDS),
остальным создатель (или тот, у кого есть право выдавать права) раздаёт права галочками:
  👑 админ бота   все права сразу
  🔇 мут · ⚠️ предупреждения · ⛔ бан и кик · 🗑 удаление, закреп, ссылки
  🎮 игры · 🧩 триггеры · ⚙️ настройки бота · 🔑 выдача прав

Ответ Telegram (get_chat_member) кешируем на минуту."""

import time

from aiogram import Bot
from aiogram.types import ChatMember, Message

from guard import config, db

TTL = 60
_cache: dict[tuple[int, int], tuple[float, ChatMember | None]] = {}

# права бота и как они называются в Telegram
RIGHTS = {
    "can_delete_messages": "удаление сообщений",
    "can_restrict_members": "блокировка участников",
    "can_pin_messages": "закрепление сообщений",
    "can_invite_users": "пригласительные ссылки",
}
ALL_RIGHTS = tuple(RIGHTS)

# права людей в боте (битовая маска в таблице rights)
R_ADMIN = 1
R_MUTE = 2
R_WARN = 4
R_BAN = 8
R_CLEAN = 16
R_GAMES = 32
R_TRIGGERS = 64
R_SETTINGS = 128
R_GRANT = 256
ALL_FLAGS = R_MUTE | R_WARN | R_BAN | R_CLEAN | R_GAMES | R_TRIGGERS | R_SETTINGS | R_GRANT
RIGHT_NAMES = [(R_ADMIN, "👑", "админ бота"), (R_MUTE, "🔇", "мут"), (R_WARN, "⚠️", "предупреждения"),
               (R_BAN, "⛔", "бан и кик"), (R_CLEAN, "🗑", "удаление и закреп"), (R_GAMES, "🎮", "игры"),
               (R_TRIGGERS, "🧩", "триггеры"), (R_SETTINGS, "⚙️", "настройки"), (R_GRANT, "🔑", "выдача прав")]
OLD_MODS_FLAGS = R_MUTE | R_WARN | R_BAN | R_CLEAN


def is_owner(user_id: int | None) -> bool:
    return bool(user_id) and user_id in config.OWNER_IDS


def is_anon_admin(message: Message) -> bool:
    """Анонимный админ пишет от имени самой группы. Кто это, бот не знает, поэтому прав у него нет."""
    return bool(message.sender_chat and message.sender_chat.id == message.chat.id)


async def member(bot: Bot, chat_id: int, user_id: int) -> ChatMember | None:
    key = (chat_id, user_id)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        m = None
    _cache[key] = (time.monotonic(), m)
    if len(_cache) > 5000:
        cutoff = time.monotonic() - TTL
        for k in [k for k, v in _cache.items() if v[0] < cutoff]:
            _cache.pop(k, None)
    return m


def forget(chat_id: int, user_id: int | None = None) -> None:
    """Права поменялись (пришло обновление chat_member): забываем кеш."""
    if user_id is not None:
        _cache.pop((chat_id, user_id), None)
        return
    for k in [k for k in _cache if k[0] == chat_id]:
        _cache.pop(k, None)


def is_staff_member(m: ChatMember | None) -> bool:
    """Админ или создатель в Telegram (любые права)."""
    return m is not None and m.status in ("administrator", "creator")


async def is_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Создатель чата или владелец бота: у них все права."""
    if is_owner(user_id):
        return True
    m = await member(bot, chat_id, user_id)
    return m is not None and m.status == "creator"


async def flags(bot: Bot, chat_id: int, user_id: int) -> int:
    """Все права человека в боте. Создатель и владелец: всё."""
    if not user_id:
        return 0
    if await is_creator(bot, chat_id, user_id):
        return R_ADMIN | ALL_FLAGS
    f = await db.rights_of(chat_id, user_id)
    return (R_ADMIN | ALL_FLAGS) if f & R_ADMIN else f


async def has(bot: Bot, chat_id: int, user_id: int, right: int) -> bool:
    return bool(await flags(bot, chat_id, user_id) & right)


async def has_any(bot: Bot, chat_id: int, user_id: int) -> bool:
    return bool(await flags(bot, chat_id, user_id))


async def message_has(message: Message, right: int) -> bool:
    if is_anon_admin(message) or not message.from_user:
        return False
    return await has(message.bot, message.chat.id, message.from_user.id, right)


async def can_configure(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Настройки чата в личке: право ⚙️ (или выше)."""
    return await has(bot, chat_id, user_id, R_SETTINGS)


async def can_grant(bot: Bot, chat_id: int, user_id: int, right: int) -> bool:
    """Выдавать можно только те права, что есть у самого, и только с правом 🔑.
    Админа бота и право выдачи даёт только создатель (или админ бота)."""
    f = await flags(bot, chat_id, user_id)
    if not f & R_GRANT:
        return False
    if right in (R_ADMIN, R_GRANT):
        return bool(f & R_ADMIN)
    return bool(f & right)


async def is_staff(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Кого не трогает автоматика: админы Telegram, все, у кого есть права в боте, владелец."""
    if is_owner(user_id):
        return True
    if is_staff_member(await member(bot, chat_id, user_id)):
        return True
    return bool(await db.rights_of(chat_id, user_id))


def rights_text(f: int) -> str:
    if f & R_ADMIN:
        return "👑 админ бота"
    names = [f"{icon} {name}" for bit, icon, name in RIGHT_NAMES if bit != R_ADMIN and f & bit]
    return ", ".join(names) or "нет прав"


async def missing_rights(bot: Bot, chat_id: int, needed: tuple[str, ...] = ALL_RIGHTS) -> list[str] | None:
    """Каких прав не хватает боту. None: бот вообще не админ."""
    me = await member(bot, chat_id, bot.id)
    if me is None or me.status != "administrator":
        return None
    return [RIGHTS[r] for r in needed if not getattr(me, r, False)]


async def rights_problem(bot: Bot, chat_id: int, *needed: str) -> str:
    """Текст для чата, если боту не хватает прав на действие. Пусто: всё есть."""
    miss = await missing_rights(bot, chat_id, needed)
    if miss is None:
        return "я не администратор в этом чате. Дайте права: " + ", ".join(RIGHTS[r] for r in needed) + "."
    if miss:
        return "не хватает права: " + ", ".join(miss) + "."
    return ""
