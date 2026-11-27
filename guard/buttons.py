"""Цвет и премиум-значок любой кнопки бота. Меняет только владелец бота: /admin → 🎨 Кнопки.

Кнопка узнаётся по своему callback_data, в котором числа заменены на *: «g:tz:-100123:del:7» и
«g:tz:-100999:del:8» это один вид кнопки «g:tz:*:del:*» (удалить триггер в любом чате). Поэтому одна
настройка красит кнопку везде, где она встречается. Новые виды кнопок бот запоминает сам, когда
впервые их отправляет. Проще всего выбрать кнопку нажатием: «🎯 Выбрать нажатием», потом нажать нужную
кнопку где угодно в боте, и откроется её настройка.

Порядок: настройка владельца → цвет из кода (одна главная кнопка на экран)."""

import logging
import re

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from guard import db, out

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_buttons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern    TEXT    NOT NULL UNIQUE,
    sample     TEXT    NOT NULL DEFAULT '',     -- как кнопка подписана (пример)
    style      TEXT    NOT NULL DEFAULT '',     -- primary, success, danger, plain (убрать цвет) или пусто: как в коде
    icon       TEXT    NOT NULL DEFAULT '',     -- ID премиум-эмодзи значка
    seen_at    INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
);
"""
STYLES = ("primary", "success", "danger", "plain")
MAX_KINDS = 1000
_NUM = re.compile(r"^-?\d+$")
_LEAD = re.compile(r"^[^\w\s\x00-\x7f«]+\s*")   # эмодзи и значки в начале подписи (не буквы и не ASCII)

_cache: dict[str, dict] = {}   # pattern -> строка таблицы
PICKING: set[int] = set()      # владельцы, которые выбирают кнопку нажатием


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()
    await reload()
    out.BUTTON_HOOK = apply


async def reload() -> None:
    async with db.conn().execute("SELECT * FROM ui_buttons") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    _cache.clear()
    _cache.update({r["pattern"]: r for r in rows})


def pattern_of(data: str) -> str:
    return ":".join("*" if _NUM.match(p) else p for p in (data or "").split(":"))


def strip_lead(text: str) -> str:
    """«🗑 Удалить» -> «Удалить»: когда у кнопки премиум-значок, свой эмодзи в тексте уже лишний."""
    rest = _LEAD.sub("", text or "", count=1)
    return rest or text


def apply(button):
    """Настройка владельца на кнопку (вызывается на каждой отправке, см. out.sanitize_requests)."""
    data = getattr(button, "callback_data", None)
    if not data or data.startswith("bb:"):   # сам редактор кнопок не перекрашиваем, чтобы не сломать
        return button
    p = pattern_of(data)
    row = _cache.get(p)
    if row is None:
        remember(p, button.text)
        return button
    update = {}
    if row["style"] in ("primary", "success", "danger"):
        update["style"] = row["style"]
    elif row["style"] == "plain":
        update["style"] = None
    if row["icon"]:
        update["icon_custom_emoji_id"] = row["icon"]
        update["text"] = strip_lead(button.text)
    return button.model_copy(update=update) if update else button


def remember(p: str, sample: str) -> None:
    """Новый вид кнопки: в кеш сразу, в базу в фоне."""
    if len(_cache) >= MAX_KINDS:
        return
    _cache[p] = {"id": 0, "pattern": p, "sample": sample[:64], "style": "", "icon": "", "seen_at": db.now(),
                 "updated_at": 0}
    out.spawn(_insert(p, sample[:64]))


async def _insert(p: str, sample: str) -> None:
    try:
        await db.conn().execute("INSERT OR IGNORE INTO ui_buttons (pattern, sample, seen_at) VALUES (?, ?, ?)",
                                (p, sample, db.now()))
        await db.conn().commit()
        async with db.conn().execute("SELECT * FROM ui_buttons WHERE pattern = ?", (p,)) as cur:
            row = await cur.fetchone()
        if row:
            _cache[p] = dict(row)
    except Exception:
        log.exception("Не запомнил кнопку %s", p)


async def ensure(p: str, sample: str) -> dict:
    if not (_cache.get(p) or {}).get("id"):
        await _insert(p, sample[:64])
    return _cache[p]


async def get(button_id: int) -> dict | None:
    for row in _cache.values():
        if row["id"] == button_id:
            return row
    return None


async def set_design(button_id: int, *, style: str | None = None, icon: str | None = None) -> dict | None:
    """style: primary/success/danger/plain или "" (как в коде); icon: ID премиум-эмодзи или "" (без значка)."""
    fields = {}
    if style is not None:
        if style and style not in STYLES:
            raise ValueError(style)
        fields["style"] = style
    if icon is not None:
        if icon and not icon.isdigit():
            raise ValueError(icon)
        fields["icon"] = icon
    if not fields:
        return await get(button_id)
    fields["updated_at"] = db.now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    await db.conn().execute(f"UPDATE ui_buttons SET {sets} WHERE id = ?", (*fields.values(), button_id))
    await db.conn().commit()
    await reload()
    return await get(button_id)


def all_rows() -> list[dict]:
    return sorted((r for r in _cache.values() if r["id"]), key=lambda r: r["id"])


def changed_rows() -> list[dict]:
    return [r for r in all_rows() if r["style"] or r["icon"]]


class PickMiddleware(BaseMiddleware):
    """Владелец включил «🎯 Выбрать нажатием»: следующее нажатие любой кнопки не выполняет её, а открывает
    её настройку. Один раз, потом режим выключается сам."""

    async def __call__(self, handler, event: CallbackQuery, data: dict):
        user = event.from_user
        if not PICKING or user is None or user.id not in PICKING or (event.data or "").startswith(("bb:", "a:btn")):
            return await handler(event, data)
        PICKING.discard(user.id)
        sample = ""
        for r in getattr(event.message, "reply_markup", None) and event.message.reply_markup.inline_keyboard or []:
            for b in r:
                if b.callback_data == event.data:
                    sample = b.text
        row = await ensure(pattern_of(event.data or ""), sample or event.data or "")
        from guard.handlers.admin import button_screen
        text, markup = button_screen(row)
        try:
            await event.bot.send_message(user.id, text, reply_markup=markup)
            await event.answer("🎯 Кнопка выбрана, настройка в личке")
        except Exception:
            await event.answer("Напишите боту в личку /start, и выберите ещё раз", show_alert=True)
        return None
