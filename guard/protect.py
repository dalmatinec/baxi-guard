"""Автоматическая защита: стоп-слова, антиспам, режим атаки. Каждая включается в чате отдельно."""

import logging
import time
from collections import deque

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, User

from guard import actions, ai, db, journal, out, perms
from guard.actions import ActionError
from guard.links import extract_links, link_violation, looks_suspicious, scam_reason
from guard.normalize import find_stopword

log = logging.getLogger(__name__)

NEWBIE_SECONDS = 24 * 3600
NEWBIE_MESSAGES = 5
AUTO_MUTE = 3600                  # «удалить и замутить на час»
ESCALATE_COOLDOWN = 60            # одного человека наказываем не чаще раза в минуту, удаляем всё
RAID_JOINS, RAID_WINDOW = 10, 60  # больше 10 входов за минуту: автовключение режима атаки

_escalated: dict[tuple[int, int], float] = {}
_joins: dict[int, deque] = {}
_seen_join: dict[tuple[int, int], float] = {}
_mention_cache: dict[str, tuple[float, bool]] = {}
_no_rights_logged: dict[int, float] = {}


# ---------- кто под защитой ----------

def is_newbie(member: dict | None) -> bool:
    """Новичок: вход видели, и с него не прошло 24 часа или он написал не больше 5 сообщений."""
    if not member or not member.get("joined_at"):
        return False
    return db.now() - member["joined_at"] < NEWBIE_SECONDS or member.get("msg_count", 0) <= NEWBIE_MESSAGES


def sender_id(message: Message) -> int:
    if message.sender_chat:
        return message.sender_chat.id
    return message.from_user.id if message.from_user else 0


def is_system(message: Message) -> bool:
    """Свои и служебные: боты-участники, анонимный админ, автопересылка из привязанного канала."""
    if message.is_automatic_forward or perms.is_anon_admin(message):
        return True
    return bool(message.from_user and message.from_user.is_bot and not message.sender_chat)


async def trusted(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Доверенные у Бакси (админы и VIP): их автоматика не трогает."""
    from guard import baxi
    return baxi.configured() and user_id in await baxi.trusted(chat_id)


async def staff(bot: Bot, message: Message) -> bool:
    if is_system(message):
        return True
    if message.sender_chat:
        return False  # пишет от имени чужого канала
    uid = message.from_user.id if message.from_user else 0
    return bool(uid) and await perms.is_staff(bot, message.chat.id, uid)


async def exempt_from_spam(bot: Bot, message: Message) -> bool:
    if await staff(bot, message):
        return True
    if message.sender_chat:
        return False
    uid = message.from_user.id
    return await db.in_whitelist(message.chat.id, uid) or await trusted(bot, message.chat.id, uid)


# ---------- проверки ----------

def message_text(message: Message) -> str:
    return message.text or message.caption or ""


def entity_urls(message: Message) -> list[str]:
    text = message_text(message)
    urls = []
    for ent in (message.entities or message.caption_entities or []):
        if ent.type == "url":
            urls.append(ent.extract_from(text))
        elif ent.type == "text_link" and ent.url:
            urls.append(ent.url)
    return urls


def mentions(message: Message) -> list[str]:
    text = message_text(message)
    return [ent.extract_from(text).lstrip("@").lower()
            for ent in (message.entities or message.caption_entities or []) if ent.type == "mention"]


async def is_channel_or_bot(bot: Bot, username: str) -> bool:
    """@имя: канал, группа или бот. Людей Telegram боту не показывает, поэтому ошибка значит «человек»."""
    if username.endswith("bot"):
        return True
    hit = _mention_cache.get(username)
    if hit and time.monotonic() - hit[0] < 86400:
        return hit[1]
    try:
        chat = await bot.get_chat("@" + username)
        result = chat.type in ("channel", "group", "supergroup")
    except TelegramAPIError:
        result = False
    _mention_cache[username] = (time.monotonic(), result)
    return result


def has_link_buttons(message: Message) -> bool:
    rows = getattr(message.reply_markup, "inline_keyboard", None) or []
    return any(b.url or b.web_app or b.login_url for row in rows for b in row)


def foreign_forward(message: Message) -> bool:
    origin = message.forward_origin
    if origin is None:
        return False
    if origin.type == "user" and message.from_user and origin.sender_user.id == message.from_user.id:
        return False  # переслал сам себя
    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    return not (chat is not None and chat.id == message.chat.id)  # из этого же чата можно


async def spam_reason(bot: Bot, chat: dict, message: Message, member: dict | None, *, edited: bool = False) -> str:
    """Причина, если сообщение похоже на спам. Пусто: всё в порядке."""
    text = message_text(message)
    newbie = bool(chat["spam_newbie"]) and is_newbie(member)
    if chat["spam_links"] or newbie:
        links = extract_links(text, entity_urls(message))
        why = link_violation(links, allowed_domains=await db.domains(chat["chat_id"]),
                             own_username=chat.get("username") or "", newbie=newbie)
        if why:
            return why
    if chat["spam_links"]:
        own = (chat.get("username") or "").lower()
        for name in [m for m in mentions(message) if m and m != own][:3]:
            if await is_channel_or_bot(bot, name):
                return "упоминание чужого канала или бота"
    if chat["spam_forwards"] and not edited and foreign_forward(message):
        return "пересылка"
    if chat["spam_buttons"] and has_link_buttons(message):
        return "кнопки-ссылки"
    if chat["spam_scam"]:
        why = scam_reason(text)
        if why:
            return why
    if chat["spam_ai"] and ai.enabled() and len(text) >= 15 and looks_suspicious(text):
        if await ai.is_spam(text):
            return "похоже на спам (проверка нейросетью)"
    return ""


async def stop_reason(chat: dict, message: Message) -> str:
    if not chat["stop_on"]:
        return ""
    words = await db.stopwords(chat["chat_id"])
    hit = find_stopword(message_text(message), words)
    return f"стоп-слово \"{hit}\"" if hit else ""


# ---------- наказание ----------

def _can_escalate(chat_id: int, user_id: int) -> bool:
    key, now = (chat_id, user_id), time.monotonic()
    if now - _escalated.get(key, 0) < ESCALATE_COOLDOWN:
        return False
    _escalated[key] = now
    if len(_escalated) > 5000:
        for k in [k for k, v in _escalated.items() if now - v > ESCALATE_COOLDOWN]:
            _escalated.pop(k, None)
    return True


async def punish(bot: Bot, chat: dict, message: Message, action: str, reason: str, kind: str) -> bool:
    """Удалить сообщение и, если так настроено, предупредить, замутить или забанить.
    kind: 'delete' для стоп-слов, 'spam' для антиспама. False: удалить не вышло (нет прав)."""
    chat_id = message.chat.id
    try:
        await message.delete()
    except TelegramAPIError as exc:
        if time.monotonic() - _no_rights_logged.get(chat_id, 0) > 3600:
            _no_rights_logged[chat_id] = time.monotonic()
            log.warning("Не удалил сообщение в %s (%s): %s", chat_id, chat.get("title"), exc)
        return False
    uid = sender_id(message)
    await actions.record(bot, chat, uid, bot.id, kind, reason=reason)
    if action == "delete" or not uid:
        return True
    if message.sender_chat:  # пишет от имени канала: такой канал можно только забанить в чате
        if action == "ban":
            try:
                await bot.ban_chat_sender_chat(chat_id, message.sender_chat.id)
            except TelegramAPIError:
                pass
        return True
    if not _can_escalate(chat_id, uid):
        return True
    from guard import texts
    name = out.mention(uid, out.full_name(message.from_user))
    thread = out.thread_of(message)
    try:
        if action == "delete_warn":
            res = await actions.warn(bot, chat, uid, bot.id, reason)
            await texts.announce_warn(bot, chat, res, name, out.esc(reason), texts.AUTO_ADMIN, thread=thread)
        elif action == "delete_mute":
            await actions.mute(bot, chat, uid, bot.id, AUTO_MUTE, reason)
            await texts.send(bot, chat, "mod_mute", thread=thread, user=name, duration="1 час",
                             reason=out.esc(reason), admin=texts.AUTO_ADMIN)
        elif action == "ban":
            await actions.ban(bot, chat, uid, bot.id, reason=reason)
            await texts.send(bot, chat, "mod_ban", thread=thread, user=name, duration="навсегда",
                             reason=out.esc(reason), admin=texts.AUTO_ADMIN)
    except ActionError as exc:
        log.info("Не наказал %s в %s: %s", uid, chat_id, exc)
    return True


async def check_message(bot: Bot, chat: dict, message: Message, member: dict | None, *, edited: bool = False) -> bool:
    """Проверить сообщение всеми включёнными защитами. True: нарушение обработано."""
    if not (chat["stop_on"] or chat["spam_on"]):
        return False
    if await staff(bot, message):
        return False
    why = await stop_reason(chat, message)
    if why:
        return await punish(bot, chat, message, chat["stop_action"], why, "delete")
    if chat["spam_on"] and not await exempt_from_spam(bot, message):
        why = await spam_reason(bot, chat, message, member, edited=edited)
        if why:
            return await punish(bot, chat, message, chat["spam_action"], why, "spam")
    return False


# ---------- режим атаки ----------

async def attack_on(bot: Bot, chat_id: int, *, by_id: int, auto: bool = False, minutes: int = 0) -> None:
    chat = await db.ensure_chat(chat_id)
    minutes = minutes or chat["attack_minutes"]
    await db.set_chat(chat_id, attack_until=db.now() + minutes * 60)
    await db.kv_set(f"attack_kicked:{chat_id}", "0")
    why = "много входов за минуту, " if auto else ""
    await out.say(bot, chat_id, out.head(f"{why}включён режим атаки на {minutes} мин: новых участников сразу "
                                         "выгоняю."), ttl=chat["reply_ttl"])
    who = "автоматически" if auto else ("владелец или админ" if by_id else "анонимный админ")
    await journal.note(bot, chat, f"режим атаки включён на {minutes} мин ({who})")


async def attack_off(bot: Bot, chat_id: int, *, by_id: int = 0, auto: bool = False) -> None:
    chat = await db.ensure_chat(chat_id)
    await db.set_chat(chat_id, attack_until=0)
    kicked = await db.kv_get(f"attack_kicked:{chat_id}", "0")
    text = f"режим атаки выключен. Выгнано новых: {kicked}."
    await out.say(bot, chat_id, out.head(text), ttl=chat["reply_ttl"])
    await journal.note(bot, chat, text + (" Выключился сам по времени." if auto else ""))


def _count_join(chat_id: int) -> int:
    now = time.monotonic()
    q = _joins.setdefault(chat_id, deque())
    q.append(now)
    while q and now - q[0] > RAID_WINDOW:
        q.popleft()
    return len(q)


async def on_join(bot: Bot, chat: dict, user: User) -> bool:
    """Новый участник. True: выгнали (режим атаки). Один и тот же вход может прийти дважды
    (служебное сообщение и обновление chat_member), второй раз не считаем."""
    if user.is_bot or user.id == bot.id:
        return False
    chat_id = chat["chat_id"]
    key, now = (chat_id, user.id), time.monotonic()
    if now - _seen_join.get(key, 0) < 30:
        return False
    _seen_join[key] = now
    if len(_seen_join) > 5000:
        for k in [k for k, v in _seen_join.items() if now - v > 60]:
            _seen_join.pop(k, None)
    await db.member_joined(chat_id, user.id, user.username or "", out.full_name(user))
    joins = _count_join(chat_id)
    if chat["attack_until"] <= db.now() and chat["attack_auto"] and joins > RAID_JOINS:
        await attack_on(bot, chat_id, by_id=bot.id, auto=True)
        chat = await db.get_chat(chat_id)
    if chat["attack_until"] > db.now():
        try:
            await actions.kick(bot, chat, user.id, bot.id, "режим атаки", quiet=True)
        except ActionError as exc:
            log.info("Режим атаки: не выгнал %s в %s: %s", user.id, chat_id, exc)
            return False
        n = int(await db.kv_get(f"attack_kicked:{chat_id}", "0") or 0) + 1
        await db.kv_set(f"attack_kicked:{chat_id}", str(n))
        return True
    from guard import welcome
    out.spawn(welcome.on_join(bot, chat, user))   # капча и приветствие в фоне, вход не задерживаем
    return False
