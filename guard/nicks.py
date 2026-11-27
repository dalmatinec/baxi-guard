"""Свой ник за монеты Бакси: со своим шрифтом (жирный, курсив, зачёркнутый, спойлер) и премиум-эмодзи.

Покупается кнопками в личке модератора (👤 Профиль → ✨ Ник), монеты списывает Бакси (POST /v1/coins/spend).
Ник виден везде, где модератор упоминает человека (out.mention): списки игр, профиль, сервисные сообщения,
топ активности. Бакси получает его событием nick и показывает в своих упоминаниях.

Ник хранится тремя частями: премиум-эмодзи до, сам ник в HTML, премиум-эмодзи после. Так нужно, потому что
Telegram не пускает премиум-эмодзи внутрь ссылки на человека: эмодзи стоят снаружи, ник внутри ссылки."""

import logging
from dataclasses import dataclass

from aiogram.types import Message, MessageEntity
from aiogram.utils.text_decorations import html_decoration

from guard import config, db, out

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nicks (
    user_id  INTEGER PRIMARY KEY,
    head     TEXT    NOT NULL DEFAULT '',
    body     TEXT    NOT NULL DEFAULT '',
    tail     TEXT    NOT NULL DEFAULT '',
    plain    TEXT    NOT NULL DEFAULT '',
    paid     INTEGER NOT NULL DEFAULT 0,
    ts       INTEGER NOT NULL DEFAULT 0
);
-- стикер профиля: приходит в чате перед карточкой профиля
CREATE TABLE IF NOT EXISTS profile_stickers (
    user_id  INTEGER PRIMARY KEY,
    file_id  TEXT    NOT NULL,
    ts       INTEGER NOT NULL DEFAULT 0
);
"""
MAX_LEN = 24
MAX_EMOJI = 3
FORMATS = {"bold", "italic", "underline", "strikethrough", "spoiler"}
FORBIDDEN = {"url", "text_link", "mention", "text_mention", "code", "pre", "email", "phone_number", "bot_command",
             "blockquote", "expandable_blockquote"}
_cache: dict[int, tuple[str, str, str]] = {}


class NickError(ValueError):
    pass


@dataclass
class Nick:
    head: str
    body: str
    tail: str
    plain: str


def price() -> int:
    return config.NICK_PRICE


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()
    async with db.conn().execute("SELECT user_id, head, body, tail FROM nicks WHERE body != ''") as cur:
        _cache.update({r[0]: (r[1], r[2], r[3]) for r in await cur.fetchall()})
    out.NICK_HOOK = render


def render(user_id: int, link: bool = True) -> str | None:
    """Ник человека в HTML (со ссылкой на него или без) или None, если ника нет."""
    nick = _cache.get(user_id)
    if nick is None:
        return None
    head, body, tail = nick
    return f'{head}<a href="tg://user?id={user_id}">{body}</a>{tail}' if link else f"{head}{body}{tail}"


def preview(nick: Nick, user_id: int) -> str:
    return f'{nick.head}<a href="tg://user?id={user_id}">{nick.body}</a>{nick.tail}'


def has(user_id: int) -> bool:
    return user_id in _cache


def _emoji_html(e: MessageEntity, alt: str) -> str:
    return f'<tg-emoji emoji-id="{e.custom_emoji_id}">{out.esc(alt or "⭐")}</tg-emoji>'


def parse(message: Message) -> Nick:
    """Ник из присланного сообщения. NickError с понятным текстом, если так нельзя."""
    text = message.text or ""
    if not text.strip():
        raise NickError("Пришли ник текстом.")
    ents = list(message.entities or [])
    raw = text.encode("utf-16-le")
    total = len(raw) // 2

    def sub(a: int, b: int) -> str:
        return raw[a * 2:b * 2].decode("utf-16-le", errors="ignore")   # полэмодзи при проверке пробела

    customs = sorted((e for e in ents if e.type == "custom_emoji" and e.custom_emoji_id), key=lambda e: e.offset)
    start, end = 0, total
    head, tail = [], []
    while True:   # премиум-эмодзи в начале (пробелы между ними можно)
        while start < end and sub(start, start + 1).isspace():
            start += 1
        hit = next((e for e in customs if e.offset == start), None)
        if hit is None:
            break
        head.append(_emoji_html(hit, sub(hit.offset, hit.offset + hit.length)))
        start = hit.offset + hit.length
    while True:   # и в конце
        while end > start and sub(end - 1, end).isspace():
            end -= 1
        hit = next((e for e in customs if e.offset + e.length == end and e.offset >= start), None)
        if hit is None:
            break
        tail.insert(0, _emoji_html(hit, sub(hit.offset, hit.offset + hit.length)))
        end = hit.offset
    if start >= end:
        raise NickError("Нужен сам ник, а не только эмодзи.")
    if any(e.offset < end and e.offset + e.length > start for e in customs):
        raise NickError("Премиум-эмодзи ставь в начале или в конце ника, не в середине.")
    if len(head) + len(tail) > MAX_EMOJI:
        raise NickError(f"Не больше {MAX_EMOJI} премиум-эмодзи.")
    plain = sub(start, end)
    low = plain.lower()
    if len(plain) > MAX_LEN:
        raise NickError(f"Длинновато: {len(plain)} символов, можно до {MAX_LEN}.")
    if "@" in plain or "http" in low or "t.me" in low or "\n" in plain:
        raise NickError("В нике нельзя ссылки, @упоминания и переносы строк.")
    body_ents = []
    for e in ents:
        a, b = max(e.offset, start), min(e.offset + e.length, end)
        if a >= b:
            continue
        if e.type in FORBIDDEN:
            raise NickError("В нике нельзя ссылки, упоминания, код и цитаты. Можно жирный, курсив, "
                            "подчёркнутый, зачёркнутый и спойлер.")
        if e.type in FORMATS:
            body_ents.append(MessageEntity(type=e.type, offset=a - start, length=b - a))
    body = html_decoration.unparse(plain, body_ents)
    return Nick(head=" ".join(head) + (" " if head else ""), body=body,
                tail=(" " if tail else "") + " ".join(tail), plain=plain)


async def save(user_id: int, nick: Nick, paid: int) -> None:
    await db.conn().execute(
        "INSERT INTO nicks (user_id, head, body, tail, plain, paid, ts) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET head = excluded.head, body = excluded.body, tail = excluded.tail, "
        "plain = excluded.plain, paid = nicks.paid + excluded.paid, ts = excluded.ts",
        (user_id, nick.head, nick.body, nick.tail, nick.plain, paid, db.now()))
    await db.conn().commit()
    _cache[user_id] = (nick.head, nick.body, nick.tail)
    await _tell_baxi(user_id, nick)


async def remove(user_id: int) -> bool:
    cur = await db.conn().execute("DELETE FROM nicks WHERE user_id = ?", (user_id,))
    await db.conn().commit()
    _cache.pop(user_id, None)
    await _tell_baxi(user_id, Nick("", "", "", ""))
    return cur.rowcount > 0


async def _tell_baxi(user_id: int, nick: Nick) -> None:
    """Бакси показывает тот же ник у себя. Недоступен: событие подождёт в outbox."""
    from guard import baxi
    try:
        await baxi.send_event({"type": "nick", "user_id": user_id, "chat_id": 0, "head": nick.head,
                               "body": nick.body, "tail": nick.tail, "plain": nick.plain, "ts": db.now()})
    except Exception:
        log.exception("Не передал ник Бакси")


# ---------- стикер профиля ----------

async def sticker_of(user_id: int) -> str:
    async with db.conn().execute("SELECT file_id FROM profile_stickers WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return row[0] if row else ""


async def set_sticker(user_id: int, file_id: str) -> None:
    if file_id:
        await db.conn().execute("INSERT INTO profile_stickers (user_id, file_id, ts) VALUES (?, ?, ?) ON CONFLICT "
                                "(user_id) DO UPDATE SET file_id = excluded.file_id, ts = excluded.ts",
                                (user_id, file_id, db.now()))
    else:
        await db.conn().execute("DELETE FROM profile_stickers WHERE user_id = ?", (user_id,))
    await db.conn().commit()
