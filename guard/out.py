"""Всё, что бот пишет: подпись «🛡 Модерация», чистка от длинных тире и ёлочек, автоудаление."""

import asyncio
import html
import logging
import re

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions, Message, ReplyParameters

log = logging.getLogger(__name__)

HEAD = "🛡 Модерация"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_HUMANIZE = str.maketrans({"—": "-", "–": "-", "«": '"', "»": '"', "„": '"', "“": '"', "”": '"'})
_TEXT_FIELDS = ("text", "caption", "title", "description", "error_message")
_tasks: set[asyncio.Task] = set()


# ---------- премиум-эмодзи ----------
# Владелец присылает их в /admin. Обычный эмодзи из набора в тексте меняется на премиум-версию,
# а на кнопке, если текст начинается с такого эмодзи, он становится значком кнопки.
# Работает, если у владельца бота есть Telegram Premium.
PACK: dict[str, str] = {}   # обычный эмодзи -> id премиум-версии
BUTTON_HOOK = None          # цвет и значок кнопок от владельца (buttons.apply), ставится при запуске
_SPLIT = re.compile(r"(<a\b[^>]*>.*?</a>|<tg-emoji\b[^>]*>.*?</tg-emoji>|<code>.*?</code>|<[^>]+>)", re.S)


def set_pack(pairs: list[tuple[str, str]]) -> None:
    PACK.clear()
    for emoji_id, alt in pairs:
        if alt and emoji_id:
            PACK[alt] = emoji_id


def decorate(html: str) -> str:
    """Обычные эмодзи из набора меняются на премиум. Внутри тегов, ссылок и кода ничего не трогает."""
    if not PACK or not html:
        return html
    parts = _SPLIT.split(html)
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        for alt, emoji_id in PACK.items():
            if alt in chunk:
                chunk = chunk.replace(alt, f'<tg-emoji emoji-id="{emoji_id}">{alt}</tg-emoji>')
        parts[i] = chunk
    return "".join(parts)


def button_icon(button):
    """Кнопка, текст которой начинается с эмодзи из набора: эмодзи уходит в значок кнопки."""
    if not PACK or getattr(button, "icon_custom_emoji_id", None):
        return button
    for alt, emoji_id in PACK.items():
        if button.text.startswith(alt):
            rest = button.text[len(alt):].lstrip()
            return button.model_copy(update={"text": rest or alt, "icon_custom_emoji_id": emoji_id})
    return button


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
    html_mode = _is_html(getattr(method, "parse_mode", None))
    for field in _TEXT_FIELDS:
        value = getattr(method, field, None)
        if isinstance(value, str):
            clean = humanize(value)
            if html_mode and field in ("text", "caption"):
                clean = decorate(clean)
            if clean != value:
                updates[field] = clean
    markup = getattr(method, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None)
    if rows:
        new_rows = [[_button(b) for b in row] for row in rows]
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


def _button(b):
    """Подпись без длинных тире, потом цвет и значок от владельца, потом премиум-эмодзи из набора."""
    if humanize(b.text) != b.text:
        b = b.model_copy(update={"text": humanize(b.text)})
    if BUTTON_HOOK is not None:
        try:
            b = BUTTON_HOOK(b)
        except Exception:
            log.exception("Не применил оформление кнопки")
    return button_icon(b)


def _is_html(parse_mode) -> bool:
    """HTML прямо указан или стоит по умолчанию у бота (DefaultBotProperties(parse_mode='HTML'))."""
    if isinstance(parse_mode, str):
        return parse_mode.upper() == "HTML"
    return parse_mode is not None and type(parse_mode).__name__ == "Default"


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
