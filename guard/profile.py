"""Профиль участника: /profile, «профиль», «мой профиль». Ответом на сообщение: профиль того человека.

Текст из шаблона «profile» (✉️ Тексты): админ чата может переписать его, добавить картинку и кнопки.
Активность и предупреждения модератор знает сам, репутацию, ранг и монеты берёт у Бакси по API."""

import datetime
import logging
import time

from aiogram import Bot
from aiogram.types import User

from guard import config, db, out, perms

log = logging.getLogger(__name__)

NO_DATA = "—"


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def date_text(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, config.TZ).strftime("%d.%m.%Y") if ts else "неизвестно"


def last_active_text(ts: int) -> str:
    if not ts:
        return "давно"
    if time.time() - ts < 120:
        return "только что"
    dt = datetime.datetime.fromtimestamp(ts, config.TZ)
    today = datetime.datetime.now(config.TZ).date()
    if dt.date() == today:
        return f"сегодня в {dt:%H:%M}"
    if dt.date() == today - datetime.timedelta(days=1):
        return f"вчера в {dt:%H:%M}"
    return dt.strftime("%d.%m.%Y %H:%M")


async def status(bot: Bot, chat_id: int, user_id: int) -> str:
    """Роль в чате: Создатель, Администратор, Модератор (права в боте) или Участник; плюс Топ."""
    m = await perms.member(bot, chat_id, user_id)
    if m is not None and m.status == "creator":
        text = "Создатель"
    elif m is not None and m.status == "administrator":
        text = "Администратор"
    elif await db.rights_of(chat_id, user_id):
        text = "Модератор"
    else:
        text = "Участник"
    try:
        from guard.gamecore import tops
        if await tops.is_top(chat_id, user_id):
            text += " · Топ"
    except Exception:
        pass
    return text


async def values(bot: Bot, chat: dict, user: User) -> dict:
    chat_id = chat["chat_id"]
    member = await db.get_member(chat_id, user.id) or {}
    warns = await db.count_warns(chat_id, user.id, chat["warn_ttl_days"])
    baxi_data = None
    from guard import baxi
    if baxi.configured():
        baxi_data = await baxi._get(f"/v1/chat/{chat_id}/user/{user.id}")
    debt = "нет"
    try:
        from guard.gamecore import debts
        debt = await debts.summary(chat_id, user.id) or "нет"
    except Exception:
        pass
    name = out.full_name(user)
    return {
        "mention": out.mention(user.id, name),
        "name": out.esc(name),
        "username": f"@{out.esc(user.username)}" if user.username else "нет",
        "user_id": f"<code>{user.id}</code>",
        "status": await status(bot, chat_id, user.id),
        "join_date": date_text(member.get("joined_at") or member.get("first_seen") or 0),
        "messages": num(member.get("msg_count", 0)),
        "messages_24h": num(await db.messages_24h(chat_id, user.id)),
        "last_active": last_active_text(member.get("last_seen", 0)),
        "reputation": str(baxi_data["reputation"]) if baxi_data else NO_DATA,
        "rank": baxi_data["rank"] if baxi_data else NO_DATA,
        "achievements": "пока нет",
        "warnings": f"{warns}/{chat['warn_limit']}" if warns else "нет",
        "balance": f"{num(baxi_data['coins'])} 🪙" if baxi_data else NO_DATA,
        "debt": debt,
    }


async def send(bot: Bot, chat: dict, user: User, reply_to=None):
    """Стикер человека (если задал в личке), следом карточка профиля."""
    from guard import nicks, texts
    thread = out.thread_of(reply_to) if reply_to else None
    sticker = await nicks.sticker_of(user.id)
    if sticker:
        try:
            await bot.send_sticker(chat["chat_id"], sticker, reply_to_message_id=reply_to.message_id if reply_to else None)
            reply_to = None
        except Exception as exc:
            log.info("Стикер профиля %s не отправился: %s", user.id, exc)
    return await texts.send(bot, chat, "profile", reply_to=reply_to, ttl=0, thread=thread,
                            **await values(bot, chat, user))
