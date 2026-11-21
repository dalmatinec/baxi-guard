"""Тексты сервисных сообщений модерации: бан, разбан, мут, снятие мута, предупреждение, снятие.

Тексты живут в Бакси: админ чата меняет их в личке Бакси (⚙️ Мои чаты → чат → ✉️ Тексты → Модерация),
там же картинка и кнопки. Модератор берёт шаблон по API (кеш минута) и сам подставляет значения.
Бакси не отвечает или не настроен: стандартные тексты ниже, они такие же, как стандартные в Бакси."""

import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from guard import out

log = logging.getLogger(__name__)

DEFAULTS = {
    "mod_ban": "🚫 Пользователь {user} заблокирован.\n\nПричина: {reason}\nСрок: {duration}\nМодератор: {admin}",
    "mod_unban": "🔓 Пользователь {user} разблокирован.\n\nМодератор: {admin}",
    "mod_mute": "🔇 Пользователю {user} выдано ограничение сообщений.\n\nСрок: {duration}\nПричина: {reason}\n"
                "Модератор: {admin}",
    "mod_unmute": "🔊 Ограничение пользователя {user} снято.\n\nМодератор: {admin}",
    "mod_warn": "⚠️ Пользователь {user} получил предупреждение.\n\nПричина: {reason}\n"
                "Всего предупреждений: {count}\nМодератор: {admin}",
    "mod_unwarn": "✅ Предупреждение пользователя {user} снято.\n\nОсталось предупреждений: {count}",
}
CACHE_TTL = 60
MAX_CAPTION = 1024
NO_REASON = "не указана"
AUTO_ADMIN = "автомодерация"
_cache: dict[tuple[int, str], tuple[float, dict]] = {}


def fill(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


async def template(chat_id: int, key: str) -> dict:
    """{"html", "photo", "buttons"}: из Бакси или стандартный."""
    hit = _cache.get((chat_id, key))
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    data = None
    from guard import baxi
    if baxi.configured():
        data = await baxi._get(f"/v1/chat/{chat_id}/text/{key}")
    if not data or not data.get("html"):
        data = {"html": DEFAULTS[key], "photo": "", "buttons": []}
        if hit:  # Бакси прилёг: лучше старый свой текст, чем стандартный
            data = hit[1]
    _cache[(chat_id, key)] = (time.monotonic(), data)
    return data


def markup(buttons: list, values: dict) -> InlineKeyboardMarkup | None:
    rows = []
    for row in buttons or []:
        btns = [InlineKeyboardButton(text=fill(b.get("text", ""), values)[:64], url=b["url"])
                for b in row if b.get("url") and b.get("text")]
        if btns:
            rows.append(btns)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send(bot: Bot, chat: dict, key: str, *, reply_to: Message | None = None, thread: int | None = None,
               **values) -> Message | None:
    """Сервисное сообщение по шаблону чата. Живёт reply_ttl секунд, как и другие ответы модератора."""
    values.setdefault("reason", NO_REASON)
    if not values["reason"]:
        values["reason"] = NO_REASON
    t = await template(chat["chat_id"], key)
    html = fill(t["html"], values)
    kb = markup(t.get("buttons"), values)
    ttl = chat.get("reply_ttl", 0)
    if t.get("photo"):
        try:
            sent = await bot.send_photo(
                chat["chat_id"], t["photo"], caption=html if len(html) <= MAX_CAPTION else None,
                reply_markup=kb if len(html) <= MAX_CAPTION else None, parse_mode="HTML",
                reply_to_message_id=reply_to.message_id if reply_to else None,
                message_thread_id=thread if reply_to is None else None)
            out.delete_later(bot, chat["chat_id"], sent.message_id, ttl)
            if len(html) <= MAX_CAPTION:
                return sent
        except Exception as exc:
            log.warning("Картинка шаблона %s не отправилась: %s", key, exc)
    return await out.say(bot, chat["chat_id"], html, reply_to=reply_to, ttl=ttl, markup=kb, thread=thread)


async def announce_warn(bot: Bot, chat: dict, res, user: str, reason: str, admin: str, *,
                        reply_to: Message | None = None, thread: int | None = None) -> None:
    """Предупреждение, а если упёрлись в лимит, ещё и наказание (мут или бан) по своему шаблону."""
    from guard.parse import human_duration
    await send(bot, chat, "mod_warn", reply_to=reply_to, thread=thread, user=user, reason=reason, admin=admin,
               count=f"{res.count}/{res.limit}")
    why = f"{res.limit}/{res.limit} предупреждений"
    if res.escalated == "mute":
        await send(bot, chat, "mod_mute", thread=out.thread_of(reply_to) if reply_to else thread, user=user,
                   reason=why, admin=admin, duration=human_duration(chat["warn_mute_min"] * 60))
    elif res.escalated == "ban":
        await send(bot, chat, "mod_ban", thread=out.thread_of(reply_to) if reply_to else thread, user=user,
                   reason=why, admin=admin, duration="навсегда")
