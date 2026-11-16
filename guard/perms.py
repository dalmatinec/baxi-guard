"""Права людей и самого бота. Ответ Telegram (get_chat_member) кешируем на минуту.

Кто что может:
  участник           ничего
  админ с правом ограничивать участников, модератор бота   команды модерации, настройки в ЛС
  создатель чата     то же, плюс /whitelist и модераторы бота
  владелец бота      всё во всех чатах (только по ID из OWNER_IDS)
"""

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

OWNER, CREATOR, ADMIN, MOD, MEMBER = "owner", "creator", "admin", "mod", "member"
_RANK = {MEMBER: 0, MOD: 1, ADMIN: 1, CREATOR: 2, OWNER: 3}


def is_owner(user_id: int | None) -> bool:
    return bool(user_id) and user_id in config.OWNER_IDS


def is_anon_admin(message: Message) -> bool:
    """Анонимный админ пишет от имени самой группы."""
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
    """Админ или создатель в Telegram (любые права). Их автоматика не трогает."""
    return m is not None and m.status in ("administrator", "creator")


async def role(bot: Bot, chat_id: int, user_id: int) -> str:
    if is_owner(user_id):
        return OWNER
    m = await member(bot, chat_id, user_id)
    if m is not None and m.status == "creator":
        return CREATOR
    if m is not None and m.status == "administrator" and getattr(m, "can_restrict_members", False):
        return ADMIN
    if m is not None and m.status in ("administrator", "member", "restricted") and await db.is_mod(chat_id, user_id):
        return MOD
    return MEMBER


async def can_moderate(bot: Bot, chat_id: int, user_id: int) -> bool:
    return _RANK[await role(bot, chat_id, user_id)] >= 1


async def is_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Создатель чата или владелец бота: белый список и модераторы бота."""
    return _RANK[await role(bot, chat_id, user_id)] >= 2


async def message_can_moderate(message: Message) -> bool:
    if is_anon_admin(message):
        return True  # анонимные админы есть только у тех, кому их дали админы
    if not message.from_user:
        return False
    return await can_moderate(message.bot, message.chat.id, message.from_user.id)


async def is_staff(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Кого не трогает автоматика: админы любого уровня, создатель, модераторы бота, владелец."""
    if is_owner(user_id):
        return True
    if is_staff_member(await member(bot, chat_id, user_id)):
        return True
    return await db.is_mod(chat_id, user_id)


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
