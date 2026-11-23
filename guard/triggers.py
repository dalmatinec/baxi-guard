"""Триггеры: на слово в чате бот отвечает сохранённым сообщением (текст с премиум-эмодзи, фото, видео,
гифка, стикер, голосовое, файл). Слово сравнивается целиком, без учёта регистра, ё/е и знаков по краям.
Управляет тот, у кого право 🧩 триггеры: ⚙️ Мои чаты → чат → 🧩 Триггеры."""

import re
import time

from aiogram import Bot
from aiogram.types import Message

from guard import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    word        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'text',   -- text photo video animation sticker voice audio document video_note
    file_id     TEXT    NOT NULL DEFAULT '',
    html        TEXT    NOT NULL DEFAULT '',
    created_by  INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL,
    UNIQUE (chat_id, word)
);
"""
MAX_PER_CHAT = 300
COOLDOWN = 10          # один и тот же триггер в чате не чаще раза в 10 секунд
KINDS = ("photo", "video", "animation", "sticker", "voice", "audio", "document", "video_note")
_EDGE = re.compile(r"^[\W_]+|[\W_]+$", re.U)
_cache: dict[int, dict[str, dict]] = {}
_last: dict[tuple[int, str], float] = {}


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()


def norm(word: str) -> str:
    """'Привет!!' -> 'привет', 'Ёлка' -> 'елка'."""
    w = " ".join((word or "").lower().replace("ё", "е").split())
    return _EDGE.sub("", w)[:64]


def content_of(message: Message) -> tuple[str, str, str] | None:
    """(вид, file_id, html) из присланного сообщения. Подпись и текст с премиум-эмодзи сохраняются HTML-ом."""
    for kind in KINDS:
        obj = getattr(message, kind, None)
        if obj:
            file_id = obj[-1].file_id if kind == "photo" else obj.file_id
            html = message.html_text if message.caption else ""
            return kind, file_id, html[:1024]
    if message.text:
        return "text", "", message.html_text[:4000]
    return None


async def all_of(chat_id: int) -> dict[str, dict]:
    if chat_id not in _cache:
        async with db.conn().execute("SELECT * FROM triggers WHERE chat_id = ? ORDER BY word", (chat_id,)) as cur:
            _cache[chat_id] = {r["word"]: dict(r) for r in await cur.fetchall()}
    return _cache[chat_id]


async def save(chat_id: int, word: str, kind: str, file_id: str, html: str, by_id: int) -> str:
    """Сохранить (замена, если слово уже есть). Возвращает ошибку или пусто."""
    word = norm(word)
    if not word:
        return "пустое слово"
    have = await all_of(chat_id)
    if word not in have and len(have) >= MAX_PER_CHAT:
        return f"в чате уже {MAX_PER_CHAT} триггеров, удалите лишние"
    await db.conn().execute(
        "INSERT INTO triggers (chat_id, word, kind, file_id, html, created_by, created) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (chat_id, word) DO UPDATE SET kind = excluded.kind, file_id = excluded.file_id, "
        "html = excluded.html, created_by = excluded.created_by, created = excluded.created",
        (chat_id, word, kind, file_id, html, by_id, int(time.time())))
    await db.conn().commit()
    _cache.pop(chat_id, None)
    return ""


async def delete(chat_id: int, word: str) -> bool:
    cur = await db.conn().execute("DELETE FROM triggers WHERE chat_id = ? AND word = ?", (chat_id, norm(word)))
    await db.conn().commit()
    _cache.pop(chat_id, None)
    return bool(cur.rowcount)


def forget(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def match(chat_id: int, text: str | None) -> dict | None:
    if not text or len(text) > 70:
        return None
    word = norm(text)
    if not word:
        return None
    return (await all_of(chat_id)).get(word)


def cooled(chat_id: int, word: str) -> bool:
    """True: можно отвечать (с прошлого раза прошло 10 секунд)."""
    key, now = (chat_id, word), time.monotonic()
    if now - _last.get(key, 0) < COOLDOWN:
        return False
    _last[key] = now
    return True


async def send(bot: Bot, chat_id: int, trig: dict, reply_to: int | None = None, thread: int | None = None):
    """Ответить триггером. Премиум-эмодзи в обычных эмодзи подставит общий фильтр запросов."""
    common = {"reply_to_message_id": reply_to, "message_thread_id": thread if reply_to is None else None}
    kind, file_id, html = trig["kind"], trig["file_id"], trig["html"]
    if kind == "text":
        return await bot.send_message(chat_id, html, parse_mode="HTML", **common)
    caption = {"caption": html or None, "parse_mode": "HTML"} if kind not in ("sticker", "video_note") else {}
    method = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
              "sticker": bot.send_sticker, "voice": bot.send_voice, "audio": bot.send_audio,
              "document": bot.send_document, "video_note": bot.send_video_note}[kind]
    return await method(chat_id, file_id, **caption, **common)
