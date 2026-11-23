"""Топ-участники: могут записаться в список два раза (два номера, двойной шанс).

  постоянный  в этом чате во всех играх, пока не снимут
  временный   на одну игру (game_id) или до срока (until)

Выдают и снимают админы ответом на сообщение: «+топ», «+топ игра», «+топ 3д», «-топ».
Команды нарочно не похожи на «топы» (рейтинги)."""

import time

from guard import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS game_tops (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    game_id  INTEGER NOT NULL DEFAULT 0,   -- 0: не привязан к игре
    until    REAL    NOT NULL DEFAULT 0,   -- 0: без срока
    by_id    INTEGER NOT NULL DEFAULT 0,
    ts       REAL    NOT NULL,
    PRIMARY KEY (chat_id, user_id, game_id)
);
"""


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()


async def give(chat_id: int, user_id: int, by_id: int, *, game_id: int = 0, seconds: int = 0) -> None:
    until = time.time() + seconds if seconds else 0
    await db.conn().execute(
        "INSERT INTO game_tops (chat_id, user_id, game_id, until, by_id, ts) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (chat_id, user_id, game_id) DO UPDATE SET until = excluded.until, by_id = excluded.by_id, "
        "ts = excluded.ts", (chat_id, user_id, game_id, until, by_id, time.time()))
    await db.conn().commit()


async def take(chat_id: int, user_id: int) -> int:
    cur = await db.conn().execute("DELETE FROM game_tops WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    await db.conn().commit()
    return cur.rowcount or 0


async def is_top(chat_id: int, user_id: int, game_id: int = 0) -> bool:
    """Топ сейчас: постоянный, временный по сроку или на эту игру."""
    async with db.conn().execute(
            "SELECT 1 FROM game_tops WHERE chat_id = ? AND user_id = ? AND (game_id = 0 OR game_id = ?) "
            "AND (until = 0 OR until > ?) LIMIT 1", (chat_id, user_id, game_id or -1, time.time())) as cur:
        return await cur.fetchone() is not None


async def list_tops(chat_id: int) -> list[dict]:
    async with db.conn().execute(
            "SELECT t.*, COALESCE(u.name, '') AS name FROM game_tops t LEFT JOIN members u "
            "ON u.chat_id = t.chat_id AND u.user_id = t.user_id WHERE t.chat_id = ? AND (t.until = 0 OR t.until > ?) "
            "ORDER BY t.ts", (chat_id, time.time())) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def drop_game(game_id: int) -> None:
    """Игра кончилась: временные топы на неё больше не нужны."""
    await db.conn().execute("DELETE FROM game_tops WHERE game_id = ?", (game_id,))
    await db.conn().commit()
