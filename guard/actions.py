"""Сами действия модерации: мут, бан, кик, предупреждения. Ими пользуются и команды, и автоматика.
Каждое действие пишется в журнал (база), а дальше в чат журнала и Бакси (см. record)."""

import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions

from guard import db
from guard.parse import FOREVER, human_for

log = logging.getLogger(__name__)

MUTED = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False, can_invite_users=False,
    can_change_info=False, can_pin_messages=False, can_manage_topics=False)
FREE = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True)

WARN_ACTIONS = {"mute": "мут", "ban": "бан"}


class ActionError(Exception):
    """Telegram не дал сделать действие. Текст понятен людям."""


def _until(seconds: int) -> int:
    return 0 if seconds == FOREVER else db.now() + seconds


def _tg_error(exc: TelegramAPIError) -> ActionError:
    msg = getattr(exc, "message", "") or str(exc)
    low = msg.lower()
    if "not enough rights" in low or "chat_admin_required" in low or "need administrator rights" in low:
        return ActionError("не хватает прав у бота.")
    if "user is an administrator" in low or "chat owner" in low or "can't restrict self" in low:
        return ActionError("нельзя: это администратор.")
    if "user not found" in low or "participant_id_invalid" in low:
        return ActionError("не нашёл этого человека в чате.")
    return ActionError(f"Telegram не дал: {msg}")


async def record(bot: Bot, chat: dict, user_id: int, by_id: int, kind: str, *, until: int = 0, reason: str = "",
                 link: str = "", warns: int = 0) -> None:
    """Записать действие в журнал."""
    try:
        await db.log_action(chat["chat_id"], user_id, by_id, kind, until, reason)
    except Exception:
        log.exception("Не записал действие в журнал")


# ---------- мут ----------

async def mute(bot: Bot, chat: dict, user_id: int, by_id: int, seconds: int, reason: str = "",
               link: str = "") -> int:
    until = _until(seconds)
    try:
        await bot.restrict_chat_member(chat["chat_id"], user_id, MUTED, use_independent_chat_permissions=True,
                                       until_date=until or None)
    except TelegramAPIError as exc:
        raise _tg_error(exc) from exc
    await record(bot, chat, user_id, by_id, "mute", until=until, reason=reason, link=link)
    return until


async def unmute(bot: Bot, chat: dict, user_id: int, by_id: int, reason: str = "") -> None:
    """Вернуть права по умолчанию для этого чата."""
    try:
        full = await bot.get_chat(chat["chat_id"])
        allowed = full.permissions or FREE
    except TelegramAPIError:
        allowed = FREE
    try:
        await bot.restrict_chat_member(chat["chat_id"], user_id, allowed, use_independent_chat_permissions=True)
    except TelegramAPIError as exc:
        raise _tg_error(exc) from exc
    await record(bot, chat, user_id, by_id, "unmute", reason=reason)


# ---------- бан и кик ----------

async def ban(bot: Bot, chat: dict, user_id: int, by_id: int, seconds: int = FOREVER, reason: str = "",
              link: str = "") -> int:
    until = _until(seconds)
    try:
        await bot.ban_chat_member(chat["chat_id"], user_id, until_date=until or None)
    except TelegramAPIError as exc:
        raise _tg_error(exc) from exc
    await record(bot, chat, user_id, by_id, "ban", until=until, reason=reason, link=link)
    return until


async def unban(bot: Bot, chat: dict, user_id: int, by_id: int, reason: str = "") -> None:
    try:
        await bot.unban_chat_member(chat["chat_id"], user_id, only_if_banned=True)
    except TelegramAPIError as exc:
        raise _tg_error(exc) from exc
    await record(bot, chat, user_id, by_id, "unban", reason=reason)


async def kick(bot: Bot, chat: dict, user_id: int, by_id: int, reason: str = "", link: str = "",
               log_it: bool = True) -> None:
    """Выгнать без бана: бан и сразу разбан, человек может вернуться по ссылке."""
    try:
        await bot.ban_chat_member(chat["chat_id"], user_id)
        await bot.unban_chat_member(chat["chat_id"], user_id, only_if_banned=True)
    except TelegramAPIError as exc:
        raise _tg_error(exc) from exc
    if log_it:
        await record(bot, chat, user_id, by_id, "kick", reason=reason, link=link)


# ---------- предупреждения ----------

@dataclass
class WarnResult:
    count: int              # сколько активных стало (до сброса)
    limit: int
    escalated: str = ""     # '' | 'mute' | 'ban'
    until: int = 0
    error: str = ""         # не получилось наказать на лимите

    def describe(self, chat: dict) -> str:
        text = f"предупреждение {self.count}/{self.limit}"
        if self.escalated == "ban":
            text += " и бан"
        elif self.escalated == "mute":
            text += f" и мут {human_for(chat['warn_mute_min'] * 60)}"
        if self.error:
            text += f". Наказать не вышло: {self.error}"
        return text


async def warn(bot: Bot, chat: dict, user_id: int, by_id: int, reason: str = "", link: str = "") -> WarnResult:
    """Предупреждение. На лимите: бан или мут, как настроено, и предупреждения обнуляются."""
    await db.add_warn(chat["chat_id"], user_id, by_id, reason)
    count = await db.count_warns(chat["chat_id"], user_id, chat["warn_ttl_days"])
    limit = max(1, chat["warn_limit"])
    res = WarnResult(count=count, limit=limit)
    await record(bot, chat, user_id, by_id, "warn", reason=reason, link=link, warns=count)
    if count < limit:
        return res
    why = f"{limit}/{limit} предупреждений" + (f": {reason}" if reason else "")
    try:
        if chat["warn_action"] == "ban":
            res.until = await ban(bot, chat, user_id, by_id, FOREVER, why, link)
            res.escalated = "ban"
        else:
            res.until = await mute(bot, chat, user_id, by_id, max(60, chat["warn_mute_min"] * 60), why, link)
            res.escalated = "mute"
        await db.clear_warns(chat["chat_id"], user_id)
    except ActionError as exc:
        res.error = str(exc)
    return res


async def unwarn(bot: Bot, chat: dict, user_id: int, by_id: int) -> int | None:
    """Снять последнее предупреждение. None: снимать нечего. Иначе сколько осталось."""
    if not await db.unwarn_last(chat["chat_id"], user_id, chat["warn_ttl_days"]):
        return None
    left = await db.count_warns(chat["chat_id"], user_id, chat["warn_ttl_days"])
    await record(bot, chat, user_id, by_id, "unwarn", warns=left)
    return left
