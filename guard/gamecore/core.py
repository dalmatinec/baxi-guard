"""Ядро игр со списком: одна активная игра на чат, живое закреплённое сообщение со списком.

Запись: обычный участник один раз, топ два (второй раз в любой момент, получает ещё один номер).
ФК: сколько сообщений нужно победителю с начала игры. Засчитывается сообщение от 4 букв;
«го», «ок», «+», стикеры и команды не идут. В списке у каждого номера видно ✅ 34/30 или ❌ 12/30.
Рандом: «рандом 36» показывает только выпавшее число, кто это, админ смотрит в списке сам."""

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass

from guard import db
from guard.gamecore import GAMES, tops

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    hashtag     TEXT    NOT NULL DEFAULT '',
    fk          INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'setup',   -- setup (ждём хэштег) | open | closed | done
    list_msg_id INTEGER NOT NULL DEFAULT 0,
    thread_id   INTEGER NOT NULL DEFAULT 0,
    started_by  INTEGER NOT NULL DEFAULT 0,
    started_at  REAL    NOT NULL,
    closed_at   REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS games_chat ON games (chat_id, status);
CREATE TABLE IF NOT EXISTS game_entries (
    game_id   INTEGER NOT NULL,
    pos       INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '',
    username  TEXT    NOT NULL DEFAULT '',
    top       INTEGER NOT NULL DEFAULT 0,
    ts        REAL    NOT NULL,
    PRIMARY KEY (game_id, pos)
);
CREATE INDEX IF NOT EXISTS game_entries_user ON game_entries (game_id, user_id);
CREATE TABLE IF NOT EXISTS game_fk (
    game_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    n        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, user_id)
);
"""

LIVE = ("setup", "open", "closed")
MAX_TEXT = 3900
REFRESH_GAP = 2.0          # живой список правим не чаще раза в 2 секунды
FK_REFRESH_GAP = 15.0      # из-за ФК не чаще раза в 15 секунд
# chat_id -> (game_id, fk): все идущие игры. Сообщения считаем с начала игры, даже пока ФК не задан:
# ФК ставят по желанию позже (кнопка 🎯 ФК или /fk), и он должен учесть всё, что написали с запуска
_fk_games: dict[int, tuple[int, int]] = {}
_last_edit: dict[int, float] = {}
_pending: set[int] = set()
_HASHTAG = re.compile(r"(?<![\w#])#([\wёЁ]{2,64})", re.U)
_LETTERS = re.compile(r"[^\W\d_]", re.U)
NOT_COUNTED = {"го", "ок", "окей", "+", "++", "да", "нет", "ага"}


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()
    await tops.init()
    _fk_games.clear()
    async with db.conn().execute("SELECT id, chat_id, fk FROM games WHERE status IN ('setup', 'open', 'closed')") as cur:
        for r in await cur.fetchall():
            _fk_games[r["chat_id"]] = (r["id"], r["fk"])


@dataclass
class Entry:
    pos: int
    user_id: int
    name: str
    username: str
    top: bool


# ---------- игры ----------

async def active(chat_id: int) -> dict | None:
    async with db.conn().execute("SELECT * FROM games WHERE chat_id = ? AND status IN ('setup', 'open', 'closed') "
                                 "ORDER BY id DESC LIMIT 1", (chat_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get(game_id: int) -> dict | None:
    async with db.conn().execute("SELECT * FROM games WHERE id = ?", (game_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def create(chat_id: int, kind: str, mode: str, fk: int, by_id: int, thread_id: int = 0) -> dict:
    """Новая игра. Хэштег-режим ждёт хэштег (setup), обычный сразу открыт."""
    await finish(chat_id)
    status = "setup" if mode == "hashtag" else "open"
    cur = await db.conn().execute(
        "INSERT INTO games (chat_id, kind, mode, fk, status, thread_id, started_by, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, kind, mode, max(0, fk), status, thread_id or 0, by_id, time.time()))
    await db.conn().commit()
    game = await get(cur.lastrowid)
    _fk_games[chat_id] = (game["id"], game["fk"])
    return game


async def update(game_id: int, **fields) -> None:
    allowed = {"hashtag", "status", "list_msg_id", "closed_at", "fk"}
    if set(fields) - allowed:
        raise ValueError(fields)
    sets = ", ".join(f"{k} = ?" for k in fields)
    await db.conn().execute(f"UPDATE games SET {sets} WHERE id = ?", (*fields.values(), game_id))
    await db.conn().commit()


async def set_fk(game: dict, fk: int) -> None:
    """ФК по желанию, в любой момент игры (кнопка 🎯 ФК или /fk 30). 0 убирает."""
    fk = max(0, min(10000, fk))
    await update(game["id"], fk=fk)
    _fk_games[game["chat_id"]] = (game["id"], fk)   # счёт идёт и без ФК, с начала игры


async def finish(chat_id: int) -> dict | None:
    """Закончить активную игру (сброс или новая игра поверх)."""
    game = await active(chat_id)
    if game:
        await update(game["id"], status="done", closed_at=game["closed_at"] or time.time())
        await tops.drop_game(game["id"])
    _fk_games.pop(chat_id, None)
    return game


def normalize_hashtag(text: str) -> str:
    """Первый хэштег из текста, в нижнем регистре без решётки. Пусто: хэштега нет."""
    m = _HASHTAG.search(text or "")
    return m.group(1).lower().replace("ё", "е") if m else ""


# ---------- список ----------

async def entries(game_id: int) -> list[Entry]:
    async with db.conn().execute("SELECT * FROM game_entries WHERE game_id = ? ORDER BY pos", (game_id,)) as cur:
        return [Entry(r["pos"], r["user_id"], r["name"], r["username"], bool(r["top"])) for r in await cur.fetchall()]


async def count(game_id: int) -> int:
    async with db.conn().execute("SELECT COUNT(*) FROM game_entries WHERE game_id = ?", (game_id,)) as cur:
        return (await cur.fetchone())[0]


async def join(game: dict, user_id: int, name: str, username: str) -> tuple[bool, str]:
    """Записать. (получилось ли, пояснение для всплывашки)."""
    if game["status"] != "open":
        return False, "Список закрыт"
    async with db.conn().execute("SELECT COUNT(*) FROM game_entries WHERE game_id = ? AND user_id = ?",
                                 (game["id"], user_id)) as cur:
        have = (await cur.fetchone())[0]
    is_top = await tops.is_top(game["chat_id"], user_id, game["id"])
    limit = 2 if is_top else 1
    if have >= limit:
        return False, "Ты уже в списке" + (" два раза" if limit == 2 else "")
    pos = await count(game["id"]) + 1
    await db.conn().execute("INSERT INTO game_entries (game_id, pos, user_id, name, username, top, ts) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (game["id"], pos, user_id, name[:64], (username or "")[:32], int(is_top), time.time()))
    await db.conn().commit()
    return True, f"Ты в списке под номером {pos}"


async def leave(game: dict, user_id: int) -> bool:
    """Выписаться (последняя запись человека), номера после него сдвигаются."""
    if game["status"] != "open":
        return False
    async with db.conn().execute("SELECT MAX(pos) FROM game_entries WHERE game_id = ? AND user_id = ?",
                                 (game["id"], user_id)) as cur:
        pos = (await cur.fetchone())[0]
    if not pos:
        return False
    await remove_pos(game["id"], pos)
    return True


async def remove_pos(game_id: int, pos: int) -> bool:
    cur = await db.conn().execute("DELETE FROM game_entries WHERE game_id = ? AND pos = ?", (game_id, pos))
    if not cur.rowcount:
        return False
    await db.conn().execute("UPDATE game_entries SET pos = pos - 1 WHERE game_id = ? AND pos > ?", (game_id, pos))
    await db.conn().commit()
    return True


# ---------- ФК ----------

def counts_for_fk(text: str | None, is_command: bool = False) -> bool:
    """Сообщение идёт в ФК: от 4 букв (одного слова хватит), не команда, не «го», «ок», «+»."""
    t = (text or "").strip()
    if not t or is_command or t.startswith("/"):
        return False
    if t.lower().strip("!?.,)") in NOT_COUNTED:
        return False
    return len(_LETTERS.findall(t)) >= 4


async def on_message(chat_id: int, user_id: int, text: str | None, is_command: bool = False) -> None:
    """Из трекера сообщений: засчитать в ФК идущей игры (с начала игры, задан ФК или ещё нет)."""
    hit = _fk_games.get(chat_id)
    if not hit or not counts_for_fk(text, is_command):
        return
    await db.conn().execute("INSERT INTO game_fk (game_id, user_id, n) VALUES (?, ?, 1) "
                            "ON CONFLICT (game_id, user_id) DO UPDATE SET n = n + 1", (hit[0], user_id))
    await db.conn().commit()


async def fk_counts(game_id: int) -> dict[int, int]:
    async with db.conn().execute("SELECT user_id, n FROM game_fk WHERE game_id = ?", (game_id,)) as cur:
        return {r[0]: r[1] for r in await cur.fetchall()}


def fk_mark(n: int, need: int) -> str:
    return f"✅ {n}/{need}" if n >= need else f"❌ {n}/{need}"


# ---------- живое сообщение ----------

def entry_line(e: Entry, fk_need: int, fk_have: int, hashtag: str = "") -> str:
    from guard.out import esc, mention
    line = f"{e.pos}. {mention(e.user_id, e.name or str(e.user_id))}"
    if e.username:
        line += f" @{esc(e.username)}"
    if hashtag:   # запись по хэштегу: у каждого его хэштег
        line += f" #{esc(hashtag)}"
    if e.top:
        line += " ⭐"
    if fk_need:
        line += f" · {fk_mark(fk_have, fk_need)}"
    return line


async def render(game: dict) -> tuple[str, bool]:
    """(текст живого списка, обрезан ли). Шаблон «game_list» можно поменять в ✉️ Тексты."""
    from guard import texts
    from guard.out import esc
    items = await entries(game["id"])
    fk = await fk_counts(game["id"]) if game["fk"] else {}
    tag = game["hashtag"] if game["mode"] == "hashtag" else ""
    lines = [entry_line(e, game["fk"], fk.get(e.user_id, 0), tag) for e in items]
    title = GAMES[game["kind"]].title
    if game["status"] == "closed":
        title += " · список закрыт"
    if game["mode"] == "hashtag":
        how = f"#{esc(game['hashtag'])}"   # хэштег внизу списка
    else:
        how = "Записаться: кнопка ниже, @ или свой @юзернейм" if game["status"] == "open" else ""
    values = dict(game=title, fk=f" · ФК {game['fk']}" if game["fk"] else "", count=len(items), how=how)
    text = _no_empty_list(await texts.render(game["chat_id"], "game_list", list="\n".join(lines), **values))
    cut = False
    while len(text) > MAX_TEXT and len(lines) > 10:
        lines = lines[:-10]
        cut = True
        text = await texts.render(game["chat_id"], "game_list",
                                  list="\n".join(lines) + f"\n… и ещё {len(items) - len(lines)}", **values)
    return text, cut


def _no_empty_list(text: str) -> str:
    """Пока никого нет, между разделителями пусто: оставляем один разделитель."""
    return re.sub(r"(➖+)\n\s*\n(➖+)", r"\1", text)


def list_markup(game: dict, cut: bool):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    if game["status"] == "open":
        if game["mode"] == "button":   # выписаться: /leave или «выписаться», без кнопки
            rows.append([InlineKeyboardButton(text="✅ Записаться", callback_data=f"lg:join:{game['id']}",
                                              style="success")])
        # кнопки ведущего: работают у тех, у кого есть роль (право 🎮 игры)
        rows.append([InlineKeyboardButton(text="🔒 Закрыть список", callback_data=f"lg:close:{game['id']}"),
                     InlineKeyboardButton(text="🎯 ФК", callback_data=f"lg:setfk:{game['id']}")])
    elif game["status"] == "closed":
        rows.append([InlineKeyboardButton(text="🔓 Открыть снова", callback_data=f"lg:reopen:{game['id']}"),
                     InlineKeyboardButton(text="🎯 ФК", callback_data=f"lg:setfk:{game['id']}")])
    if cut:
        rows.append([InlineKeyboardButton(text="📋 Весь список", callback_data=f"lg:full:{game['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def post_list(bot, game: dict) -> None:
    """Отправить живой список и закрепить его."""
    from guard.out import say
    text, cut = await render(game)
    sent = await say(bot, game["chat_id"], text, thread=game["thread_id"] or None, markup=list_markup(game, cut))
    if sent is None:
        return
    await update(game["id"], list_msg_id=sent.message_id)
    game = dict(game, list_msg_id=sent.message_id)
    await pin(bot, game)
    _last_edit[game["id"]] = time.monotonic()


async def pin(bot, game: dict) -> None:
    """Закрепить живой список (при запуске и когда список снова открыли)."""
    if not game.get("list_msg_id"):
        return
    try:
        await bot.pin_chat_message(game["chat_id"], game["list_msg_id"], disable_notification=True)
    except Exception as exc:
        log.info("Не закрепил список в %s: %s", game["chat_id"], exc)


async def refresh(bot, game_id: int, *, gap: float = REFRESH_GAP) -> None:
    """Перерисовать живой список, не чаще раза в gap секунд (остальное склеится в одну правку)."""
    if game_id in _pending:
        return
    wait = gap - (time.monotonic() - _last_edit.get(game_id, 0))
    if wait > 0:
        _pending.add(game_id)
        try:
            await asyncio.sleep(wait)
        finally:
            _pending.discard(game_id)
    game = await get(game_id)
    if not game or not game["list_msg_id"]:
        return
    from guard.out import NO_PREVIEW
    text, cut = await render(game)
    _last_edit[game_id] = time.monotonic()
    try:   # премиум-эмодзи подставит общий фильтр запросов (out.sanitize_requests)
        await bot.edit_message_text(text, chat_id=game["chat_id"], message_id=game["list_msg_id"],
                                    parse_mode="HTML", reply_markup=list_markup(game, cut),
                                    link_preview_options=NO_PREVIEW)
    except Exception as exc:
        if "not modified" not in str(exc):
            log.info("Не обновил список %s: %s", game_id, exc)


_tasks: set[asyncio.Task] = set()


def refresh_later(bot, game_id: int, gap: float = REFRESH_GAP) -> None:
    task = asyncio.create_task(refresh(bot, game_id, gap=gap))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---------- рандом ----------

_RANDOM = re.compile(r"^\s*(?:/?random|/?рандом)(?:@\w+)?\s+(\d{1,6})(?:\s+(\d{1,6}))?\s*$", re.I)


def parse_random(text: str) -> tuple[int, int] | None:
    """«рандом 36» -> (1, 36), «рандом 5 20» -> (5, 20). Не команда: None."""
    m = _RANDOM.match(text or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2)) if m.group(2) else None
    lo, hi = (1, a) if b is None else (min(a, b), max(a, b))
    return (lo, hi) if hi >= 1 and hi >= lo else None


def roll(lo: int, hi: int, rnd: random.Random | None = None) -> int:
    return (rnd or random.SystemRandom()).randint(lo, hi)
