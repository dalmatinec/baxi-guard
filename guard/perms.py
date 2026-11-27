"""Роли в боте и права самого бота в чате.

Админка в Telegram сама ничего в боте не даёт. Роли:
  👑 Владелец    создатель чата и владелец бота (OWNER_IDS): всё, назначает и снимает админов
  ⭐ Админ        всё в чате: модерация, игры, триггеры, настройки, тексты, белый список, назначает модераторов
  🛡 Модератор    мут, предупреждения, бан и кик, удаление и закреп, игры, триггеры
Назначение в чате ответом: /mod (модератор), /adm (админ), /unmod (снять роль).
Внутри роли хранятся битовой маской прав (R_*), команды проверяют нужный бит.

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
ROLE_MOD_FLAGS = R_MUTE | R_WARN | R_BAN | R_CLEAN | R_GAMES | R_TRIGGERS
OLD_MODS_FLAGS = ROLE_MOD_FLAGS
OWNER, ADMIN, MOD, JUNIOR, HOST, NONE = "owner", "admin", "mod", "junior", "host", ""
# Роли в боте. Назначаются одной командой /role (ответом) с выбором роли. У каждой свои права:
ROLES = {
    ADMIN: ("⭐ Админ", R_ADMIN, "все права, назначает модераторов и ведущих"),
    MOD: ("🛡 Модератор", ROLE_MOD_FLAGS, "мут, предупреждения, бан и кик, удаление и закреп, игры, триггеры"),
    JUNIOR: ("🔰 Мл. модератор", R_MUTE | R_WARN | R_CLEAN, "мут, предупреждения, удаление и закреп"),
    HOST: ("🎮 Ведущий игр", R_GAMES, "запуск игр, ФК, топы, список, рандом"),
}
ROLE_TITLES = {OWNER: "👑 Владелец", **{k: v[0] for k, v in ROLES.items()}, NONE: "участник"}
ROLE_RANK = {NONE: 0, HOST: 1, JUNIOR: 1, MOD: 2, ADMIN: 3, OWNER: 4}
ROLE_WORDS = {"admin": ADMIN, "админ": ADMIN, "mod": MOD, "мод": MOD, "модератор": MOD, "junior": JUNIOR,
              "младший": JUNIOR, "host": HOST, "ведущий": HOST, "none": NONE, "снять": NONE}


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


def role_from_flags(f: int) -> str:
    if f & R_ADMIN:
        return ADMIN
    for key, (_title, flags, _about) in ROLES.items():
        if f == flags:
            return key
    return MOD if f else NONE   # старые наборы галочек считаем модератором


async def role(bot: Bot, chat_id: int, user_id: int) -> str:
    if await is_creator(bot, chat_id, user_id):
        return OWNER
    return role_from_flags(await db.rights_of(chat_id, user_id))


def role_flags(r: str) -> int:
    return ROLES[r][1] if r in ROLES else 0


async def can_set_role(bot: Bot, chat_id: int, actor: int, target: int, new: str) -> str:
    """Можно ли actor поставить target роль new ('' значит снять). Пусто: можно, иначе почему нельзя."""
    me, them = await role(bot, chat_id, actor), await role(bot, chat_id, target)
    if them == OWNER:
        return "у владельца и так все права"
    if me == OWNER:
        return ""
    if me != ADMIN:
        return "роли назначают только владелец и админ"
    if them == ADMIN or new == ADMIN:
        return "админов назначает и снимает только владелец"
    return ""


async def roles_for(bot: Bot, chat_id: int, actor: int) -> list[str]:
    """Какие роли actor может выдавать: владелец все, админ только ограниченные."""
    me = await role(bot, chat_id, actor)
    if me == OWNER:
        return [ADMIN, MOD, JUNIOR, HOST]
    if me == ADMIN:
        return [MOD, JUNIOR, HOST]
    return []


async def set_role(chat_id: int, user_id: int, new: str, by_id: int) -> None:
    await db.set_rights(chat_id, user_id, role_flags(new), by_id)
    forget(chat_id, user_id)


def rights_text(f: int) -> str:
    if f & R_ADMIN:
        return ROLES[ADMIN][0]
    for _key, (title, flags, _about) in ROLES.items():
        if f == flags:
            return title
    if f and f & ROLE_MOD_FLAGS == f:
        return ROLES[MOD][0]
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
