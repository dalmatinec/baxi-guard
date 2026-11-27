"""Журнал в чат или канал, если он указан в настройках: короткая строка на каждое действие."""

import logging

from aiogram import Bot

from guard import db, out
from guard.parse import human_for

log = logging.getLogger(__name__)

KINDS = {"mute": "мут", "unmute": "снят мут", "warn": "предупреждение", "unwarn": "снято предупреждение",
         "ban": "бан", "unban": "разбан", "kick": "кик", "delete": "удалено сообщение", "spam": "спам удалён"}


async def who(bot: Bot, chat_id: int, user_id: int) -> str:
    if user_id == bot.id:
        return "автоматика"
    if not user_id:
        return "анонимный админ"
    name = await db.member_name(chat_id, user_id)
    return out.mention(user_id, name or str(user_id))


def until_text(kind: str, until: int) -> str:
    if kind not in ("mute", "ban"):
        return ""
    if not until:
        return " навсегда"
    return " " + human_for(max(60, (until - db.now() + 30) // 60 * 60))


async def line(bot: Bot, chat: dict, user_id: int, by_id: int, kind: str, until: int, reason: str, link: str,
               warns: int = 0) -> str:
    what = KINDS.get(kind, kind) + until_text(kind, until)
    if kind in ("warn", "unwarn"):
        what += f" ({warns}/{chat['warn_limit']})"
    target = await who(bot, chat["chat_id"], user_id) if user_id else "?"
    text = (f"🛡 <b>{out.esc(chat['title'] or str(chat['chat_id']))}</b>: {await who(bot, chat['chat_id'], by_id)}"
            f" → {target}: {what}")
    if reason:
        text += f". Причина: {out.esc(reason[:200])}"
    if link:
        text += f'. <a href="{link}">Сообщение</a>'
    return text


async def post(bot: Bot, chat: dict, user_id: int, by_id: int, kind: str, *, until: int = 0, reason: str = "",
               link: str = "", warns: int = 0) -> None:
    target = chat.get("log_chat_id") or 0
    if not target:
        return
    try:
        text = await line(bot, chat, user_id, by_id, kind, until, reason, link, warns)
        await bot.send_message(target, text, parse_mode="HTML", link_preview_options=out.NO_PREVIEW,
                               disable_notification=True)
    except Exception as exc:
        log.warning("Не отправил строку журнала в %s: %s", target, exc)


async def note(bot: Bot, chat: dict, text: str) -> None:
    """Свободная строка в журнал: включили режим атаки и т.п."""
    target = chat.get("log_chat_id") or 0
    if not target:
        return
    try:
        await bot.send_message(target, f"🛡 <b>{out.esc(chat['title'] or str(chat['chat_id']))}</b>: {text}",
                               parse_mode="HTML", disable_notification=True)
    except Exception as exc:
        log.warning("Не отправил заметку журнала в %s: %s", target, exc)
