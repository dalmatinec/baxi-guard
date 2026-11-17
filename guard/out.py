"""Всё, что бот пишет: подпись «🛡 Модерация», чистка от длинных тире и ёлочек, автоудаление."""

import asyncio
import html
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions, Message, ReplyParameters

log = logging.getLogger(__name__)

HEAD = "🛡 Модерация"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_HUMANIZE = str.maketrans({"—": "-", "–": "-", "«": '"', "»": '"', "„": '"', "“": '"', "”": '"'})
_TEXT_FIELDS = ("text", "caption", "title", "description", "error_message")
_tasks: set[asyncio.Task] = set()


def humanize(text: str) -> str:
    """Никаких длинных тире и кавычек-ёлочек: дефис и обычные кавычки."""
    return (text or "").translate(_HUMANIZE).replace("…", "...")


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def head(text: str) -> str:
    """Текст от имени модерации."""
    return f"{HEAD}: {text}"


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or str(user_id))}</a>'


def full_name(user) -> str:
    if user is None:
        return ""
    return " ".join(p for p in (user.first_name, user.last_name) if p) or (user.username or str(user.id))


async def sanitize_requests(make_request, bot, method):
    """Фильтр на все запросы к Telegram: тексты, подписи, всплывашки и кнопки без длинных тире и ёлочек."""
    updates = {}
    for field in _TEXT_FIELDS:
        value = getattr(method, field, None)
        if isinstance(value, str):
            clean = humanize(value)
            if clean != value:
                updates[field] = clean
    markup = getattr(method, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None)
    if rows:
        new_rows = [[b.model_copy(update={"text": humanize(b.text)}) if humanize(b.text) != b.text else b
                     for b in row] for row in rows]
        if new_rows != rows:
            updates["reply_markup"] = InlineKeyboardMarkup(inline_keyboard=new_rows)
    if updates:
        method = method.model_copy(update=updates)
    from aiogram.exceptions import TelegramRetryAfter
    try:
        return await make_request(bot, method)
    except TelegramRetryAfter as exc:
        if exc.retry_after > 30:
            raise
        await asyncio.sleep(exc.retry_after + 0.5)
        return await make_request(bot, method)


def spawn(coro) -> asyncio.Task:
    """Фоновая задача, на которую держим ссылку, чтобы её не съел сборщик мусора."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass  # уже удалили руками или нет прав


def delete_later(bot: Bot, chat_id: int, message_id: int, delay: float) -> None:
    if delay and delay > 0:
        spawn(_delete_after(bot, chat_id, message_id, delay))


def thread_of(message: Message | None) -> int | None:
    if message is not None and message.is_topic_message:
        return message.message_thread_id
    return None


async def say(bot: Bot, chat_id: int, text: str, *, reply_to: Message | None = None, ttl: int = 0,
              markup: InlineKeyboardMarkup | None = None, thread: int | None = None) -> Message | None:
    """Сообщение в чат (HTML). ttl > 0: удалить через столько секунд."""
    try:
        sent = await bot.send_message(
            chat_id, text, parse_mode="HTML", link_preview_options=NO_PREVIEW, reply_markup=markup,
            reply_parameters=ReplyParameters(message_id=reply_to.message_id, allow_sending_without_reply=True)
            if reply_to else None,
            message_thread_id=thread if reply_to is None else None)
    except Exception:
        log.exception("Не отправил сообщение в %s", chat_id)
        return None
    delete_later(bot, chat_id, sent.message_id, ttl)
    return sent


async def answer(message: Message, text: str, *, ttl: int = 0, markup: InlineKeyboardMarkup | None = None,
                 reply: bool = True) -> Message | None:
    """Ответ на команду в группе или в личке."""
    return await say(message.bot, message.chat.id, text, reply_to=message if reply else None, ttl=ttl,
                     markup=markup, thread=thread_of(message))


def message_link(chat_id: int, message_id: int, username: str = "") -> str:
    """Ссылка на сообщение: публичный чат по юзернейму, приватная супергруппа через t.me/c/."""
    if not message_id:
        return ""
    if username:
        return f"https://t.me/{username}/{message_id}"
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return ""
