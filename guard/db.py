"""Своя база модератора: SQLite в режиме WAL. Чаты и их настройки, предупреждения, журнал действий,
стоп-слова, белые списки, очередь событий для Бакси."""

import json
import time
from pathlib import Path

import aiosqlite

from guard import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id        INTEGER PRIMARY KEY,
    title          TEXT    NOT NULL DEFAULT '',
    username       TEXT    NOT NULL DEFAULT '',
    own_until      INTEGER NOT NULL DEFAULT 0,      -- своя подписка от владельца: 0 нет, -1 навсегда, иначе до
    cmd_delete     INTEGER NOT NULL DEFAULT 1,      -- удалять команды админов через несколько секунд
    reply_ttl      INTEGER NOT NULL DEFAULT 60,     -- через сколько секунд удалять ответ бота, 0 не удалять
    stop_on        INTEGER NOT NULL DEFAULT 0,
    stop_action    TEXT    NOT NULL DEFAULT 'delete',
    spam_on        INTEGER NOT NULL DEFAULT 0,
    spam_action    TEXT    NOT NULL DEFAULT 'delete',
    spam_links     INTEGER NOT NULL DEFAULT 1,
    spam_forwards  INTEGER NOT NULL DEFAULT 1,
    spam_buttons   INTEGER NOT NULL DEFAULT 1,
    spam_scam      INTEGER NOT NULL DEFAULT 1,
    spam_newbie    INTEGER NOT NULL DEFAULT 1,
    spam_ai        INTEGER NOT NULL DEFAULT 0,
    svc_join       INTEGER NOT NULL DEFAULT 0,
    svc_leave      INTEGER NOT NULL DEFAULT 0,
    svc_pin        INTEGER NOT NULL DEFAULT 0,
    svc_photo      INTEGER NOT NULL DEFAULT 0,
    svc_title      INTEGER NOT NULL DEFAULT 0,
    attack_until   INTEGER NOT NULL DEFAULT 0,
    attack_auto    INTEGER NOT NULL DEFAULT 0,
    attack_minutes INTEGER NOT NULL DEFAULT 30,
    warn_limit     INTEGER NOT NULL DEFAULT 3,
    warn_action    TEXT    NOT NULL DEFAULT 'mute',  -- mute | ban
    warn_mute_min  INTEGER NOT NULL DEFAULT 1440,
    warn_ttl_days  INTEGER NOT NULL DEFAULT 30,      -- 0: не сгорают
    log_chat_id    INTEGER NOT NULL DEFAULT 0,
    welcome_on     INTEGER NOT NULL DEFAULT 0,  -- приветствие новичков
    welcome_baxi   INTEGER NOT NULL DEFAULT 1,  -- после приветствия фраза Бакси
    welcome_ttl    INTEGER NOT NULL DEFAULT 0,  -- удалить приветствие через N минут, 0 не удалять
    welcome_clean  INTEGER NOT NULL DEFAULT 1,  -- новое приветствие убирает прошлое
    captcha        TEXT    NOT NULL DEFAULT '', -- '' выкл, button, math, emoji
    captcha_min    INTEGER NOT NULL DEFAULT 3,  -- минут на капчу
    captcha_fail   TEXT    NOT NULL DEFAULT 'kick', -- не прошёл: kick, mute, ban
    owner_id       INTEGER NOT NULL DEFAULT 0,  -- создатель чата в Telegram, каким его видели последний раз
    owner_seen     INTEGER NOT NULL DEFAULT 0,  -- когда последний раз видели создателя среди админов
    owner_lost     INTEGER NOT NULL DEFAULT 0,  -- с какого времени создателя нет (удалён или заблокирован), 0 есть
    heir_id        INTEGER NOT NULL DEFAULT 0,  -- заместитель: может забрать права владельца в боте
    owner_override INTEGER NOT NULL DEFAULT 0,  -- владелец в боте вместо создателя (заместитель или разработчик)
    gone_at        INTEGER NOT NULL DEFAULT 0,  -- с какого времени бот не в чате (убрали, чат удалён), 0 в чате
    moved_to       INTEGER NOT NULL DEFAULT 0,  -- куда перенесли чат
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS warns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    by_id    INTEGER NOT NULL DEFAULT 0,
    reason   TEXT    NOT NULL DEFAULT '',
    ts       INTEGER NOT NULL,
    active   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS warns_user ON warns (chat_id, user_id, active);

CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    by_id    INTEGER NOT NULL DEFAULT 0,
    kind     TEXT    NOT NULL,          -- mute unmute warn unwarn ban unban kick delete spam
    until    INTEGER NOT NULL DEFAULT 0,
    reason   TEXT    NOT NULL DEFAULT '',
    ts       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_chat ON actions (chat_id, ts);
CREATE INDEX IF NOT EXISTS actions_user ON actions (chat_id, user_id, ts);
CREATE INDEX IF NOT EXISTS actions_ts ON actions (ts);

CREATE TABLE IF NOT EXISTS stopwords (
    chat_id  INTEGER NOT NULL,
    word     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, word)
);

CREATE TABLE IF NOT EXISTS whitelist (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    added_by  INTEGER NOT NULL DEFAULT 0,
    ts        INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS domains (
    chat_id  INTEGER NOT NULL,
    domain   TEXT    NOT NULL,
    PRIMARY KEY (chat_id, domain)
);

-- модераторы бота: их назначает создатель чата, они могут пользоваться командами без прав в Telegram
CREATE TABLE IF NOT EXISTS mods (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    added_by  INTEGER NOT NULL DEFAULT 0,
    ts        INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

-- права в боте: что человеку можно (битовая маска, см. perms.RIGHT_*). Админка в Telegram сама прав не даёт,
-- всё может только создатель чата и владелец бота, остальным права выдаются здесь
CREATE TABLE IF NOT EXISTS rights (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    flags     INTEGER NOT NULL DEFAULT 0,
    added_by  INTEGER NOT NULL DEFAULT 0,
    ts        INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

-- кого видели в чате: для поиска по @username и строгости к новичкам
CREATE TABLE IF NOT EXISTS members (
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    username   TEXT    NOT NULL DEFAULT '',
    name       TEXT    NOT NULL DEFAULT '',
    joined_at  INTEGER NOT NULL DEFAULT 0,       -- 0: вход не видели (был в чате до бота)
    msg_count  INTEGER NOT NULL DEFAULT 0,
    last_seen  INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL DEFAULT 0,       -- когда бот впервые увидел человека в чате
    PRIMARY KEY (chat_id, user_id)
);

-- сообщения по часам: «сообщений за 24 часа» в профиле (храним двое суток)
CREATE TABLE IF NOT EXISTS member_hours (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    hour     INTEGER NOT NULL,
    n        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, hour)
);
CREATE INDEX IF NOT EXISTS member_hours_chat ON member_hours (chat_id, hour);

-- сообщения по дням (день по часовому поясу бота, 20261123): активность чата за сегодня и неделю, храним 40 дней
CREATE TABLE IF NOT EXISTS member_days (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    day      INTEGER NOT NULL,
    n        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, day)
);
CREATE INDEX IF NOT EXISTS member_days_chat ON member_days (chat_id, day);
CREATE INDEX IF NOT EXISTS members_username ON members (username);
CREATE INDEX IF NOT EXISTS members_chat_username ON members (chat_id, username);

-- события для Бакси, которые не удалось отправить
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payload     TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    tries       INTEGER NOT NULL DEFAULT 0,
    next_at     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS outbox_next ON outbox (next_at);

CREATE TABLE IF NOT EXISTS kv (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# какие настройки чата можно менять из кода (защита от подстановки имени столбца)
CHAT_FIELDS = {
    "title", "username", "own_until", "cmd_delete", "reply_ttl",
    "stop_on", "stop_action",
    "spam_on", "spam_action", "spam_links", "spam_forwards", "spam_buttons", "spam_scam", "spam_newbie", "spam_ai",
    "svc_join", "svc_leave", "svc_pin", "svc_photo", "svc_title",
    "welcome_on", "welcome_baxi", "welcome_ttl", "welcome_clean", "captcha", "captcha_min", "captcha_fail",
    "attack_until", "attack_auto", "attack_minutes",
    "warn_limit", "warn_action", "warn_mute_min", "warn_ttl_days", "log_chat_id",
    "owner_id", "owner_seen", "owner_lost", "heir_id", "owner_override", "gone_at", "moved_to",
}
ACTION_KINDS = ("mute", "unmute", "warn", "unwarn", "ban", "unban", "kick", "delete", "spam")
ACTIONS_KEEP_DAYS = 90

_db: aiosqlite.Connection | None = None


def now() -> int:
    return int(time.time())


async def connect(path=None) -> None:
    global _db
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.execute("PRAGMA temp_store=MEMORY")
    await _db.execute("PRAGMA cache_size=-8000")          # 8 МБ кеша страниц: быстрые чтения, память под контролем
    await _db.execute("PRAGMA journal_size_limit=16777216")  # WAL после чекпойнта не больше 16 МБ на диске
    await _migrate_before()
    await _db.executescript(SCHEMA)
    await _db.commit()


# столбцы, которых нет в старой базе: добавляются при запуске
NEW_CHAT_COLUMNS = (
    ("welcome_on", "INTEGER NOT NULL DEFAULT 0"),
    ("welcome_baxi", "INTEGER NOT NULL DEFAULT 1"),
    ("welcome_ttl", "INTEGER NOT NULL DEFAULT 0"),
    ("welcome_clean", "INTEGER NOT NULL DEFAULT 1"),
    ("captcha", "TEXT NOT NULL DEFAULT ''"),
    ("captcha_min", "INTEGER NOT NULL DEFAULT 3"),
    ("captcha_fail", "TEXT NOT NULL DEFAULT 'kick'"),
    ("owner_id", "INTEGER NOT NULL DEFAULT 0"),
    ("owner_seen", "INTEGER NOT NULL DEFAULT 0"),
    ("owner_lost", "INTEGER NOT NULL DEFAULT 0"),
    ("heir_id", "INTEGER NOT NULL DEFAULT 0"),
    ("owner_override", "INTEGER NOT NULL DEFAULT 0"),
    ("gone_at", "INTEGER NOT NULL DEFAULT 0"),
    ("moved_to", "INTEGER NOT NULL DEFAULT 0"),
)


async def _migrate_before() -> None:
    """Новые столбцы в старой базе (до создания индексов из SCHEMA)."""
    async with _db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'members'") as cur:
        if await cur.fetchone() is None:
            return
    async with _db.execute("PRAGMA table_info(chats)") as cur:
        chat_cols = {r[1] for r in await cur.fetchall()}
    for col, ddl in NEW_CHAT_COLUMNS:
        if chat_cols and col not in chat_cols:
            await _db.execute(f"ALTER TABLE chats ADD COLUMN {col} {ddl}")
    async with _db.execute("PRAGMA table_info(members)") as cur:
        cols = {r[1] for r in await cur.fetchall()}
    if "first_seen" not in cols:
        await _db.execute("ALTER TABLE members ADD COLUMN first_seen INTEGER NOT NULL DEFAULT 0")
        await _db.execute("UPDATE members SET first_seen = CASE WHEN joined_at > 0 THEN joined_at ELSE last_seen END")


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("База не подключена")
    return _db


async def _one(sql: str, args=()) -> dict | None:
    async with conn().execute(sql, args) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _all(sql: str, args=()) -> list[dict]:
    async with conn().execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _exec(sql: str, args=()) -> int:
    cur = await conn().execute(sql, args)
    await conn().commit()
    return cur.rowcount if cur.rowcount is not None else 0


# ---------- чаты ----------

async def ensure_chat(chat_id: int, title: str = "", username: str = "") -> dict:
    row = await get_chat(chat_id)
    if row is None:
        await _exec("INSERT OR IGNORE INTO chats (chat_id, title, username, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, title, username or "", now()))
        return await get_chat(chat_id)
    if (title and title != row["title"]) or (username or "") != row["username"]:
        await _exec("UPDATE chats SET title = ?, username = ? WHERE chat_id = ?",
                    (title or row["title"], username or "", chat_id))
        row["title"], row["username"] = title or row["title"], username or ""
    return row


async def get_chat(chat_id: int) -> dict | None:
    return await _one("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))


async def set_chat(chat_id: int, **fields) -> None:
    bad = set(fields) - CHAT_FIELDS
    if bad:
        raise ValueError(f"Нет таких настроек: {bad}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    await _exec(f"UPDATE chats SET {cols} WHERE chat_id = ?", (*fields.values(), chat_id))


async def all_chats() -> list[dict]:
    return await _all("SELECT * FROM chats ORDER BY title COLLATE NOCASE")


async def forget_chat(chat_id: int) -> None:
    for table in ("chats", "warns", "stopwords", "whitelist", "domains", "mods", "members", "rights"):
        await conn().execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
    await conn().commit()


async def migrate_chat(old_id: int, new_id: int) -> None:
    """Группа стала супергруппой: у неё новый ID, переносим всё."""
    if old_id == new_id:
        return
    if await get_chat(new_id) is None:
        await conn().execute("UPDATE chats SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))
    for table in ("warns", "actions", "stopwords", "whitelist", "domains", "mods", "members", "rights"):
        await conn().execute(f"UPDATE OR IGNORE {table} SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))
    await conn().commit()


# ---------- участники ----------

async def touch_member(chat_id: int, user_id: int, username: str, name: str, *, message: bool = True) -> dict:
    ts = now()
    await conn().execute(
        "INSERT INTO members (chat_id, user_id, username, name, msg_count, last_seen, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (chat_id, user_id) DO UPDATE SET username = excluded.username, name = excluded.name, "
        "msg_count = msg_count + excluded.msg_count, last_seen = excluded.last_seen",
        (chat_id, user_id, (username or "").lower(), name, 1 if message else 0, ts, ts))
    if message:
        await conn().execute("INSERT INTO member_hours (chat_id, user_id, hour, n) VALUES (?, ?, ?, 1) "
                             "ON CONFLICT (chat_id, user_id, hour) DO UPDATE SET n = n + 1",
                             (chat_id, user_id, ts // 3600))
        await conn().execute("INSERT INTO member_days (chat_id, user_id, day, n) VALUES (?, ?, ?, 1) "
                             "ON CONFLICT (chat_id, user_id, day) DO UPDATE SET n = n + 1",
                             (chat_id, user_id, day_of(ts)))
    await conn().commit()
    return await get_member(chat_id, user_id)


async def member_joined(chat_id: int, user_id: int, username: str, name: str) -> None:
    """Видели вход: человек новичок, счётчик сообщений с нуля."""
    await _exec(
        "INSERT INTO members (chat_id, user_id, username, name, joined_at, msg_count, last_seen, first_seen) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?) ON CONFLICT (chat_id, user_id) DO UPDATE SET username = excluded.username, "
        "name = excluded.name, joined_at = excluded.joined_at, msg_count = 0",
        (chat_id, user_id, (username or "").lower(), name, now(), now(), now()))


async def get_member(chat_id: int, user_id: int) -> dict | None:
    return await _one("SELECT * FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))


async def find_by_username(chat_id: int, username: str) -> dict | None:
    """Ищем по юзернейму сначала в этом чате, потом в любом (Telegram не даёт боту искать людей по нику)."""
    username = username.lstrip("@").lower()
    if not username:
        return None
    row = await _one("SELECT * FROM members WHERE chat_id = ? AND username = ?", (chat_id, username))
    return row or await _one("SELECT * FROM members WHERE username = ? ORDER BY last_seen DESC LIMIT 1", (username,))


async def messages_24h(chat_id: int, user_id: int) -> int:
    row = await _one("SELECT COALESCE(SUM(n), 0) AS n FROM member_hours WHERE chat_id = ? AND user_id = ? AND hour > ?",
                     (chat_id, user_id, now() // 3600 - 24))
    return row["n"] if row else 0


def day_of(ts: int) -> int:
    """День по часовому поясу бота числом: 20261123."""
    from datetime import datetime
    return int(datetime.fromtimestamp(ts, config.TZ).strftime("%Y%m%d"))


async def activity(chat_id: int, period: str, limit: int = 10) -> tuple[int, int, list[dict]]:
    """Активность чата: (сообщений, сколько человек писали, топ [{user_id, name, username, n}]).
    period: hour (этот час), day (сегодня), week (7 дней с сегодня), all (всё время)."""
    if period == "all":
        base, args = "SELECT user_id, msg_count AS n FROM members WHERE chat_id = ? AND msg_count > 0", (chat_id,)
    elif period == "hour":
        base, args = ("SELECT user_id, n FROM member_hours WHERE chat_id = ? AND hour = ?", (chat_id, now() // 3600))
    else:
        days = 1 if period == "day" else 7
        since = day_of(now() - (days - 1) * 86400)
        base, args = ("SELECT user_id, SUM(n) AS n FROM member_days WHERE chat_id = ? AND day >= ? GROUP BY user_id",
                      (chat_id, since))
    total = await _one(f"SELECT COALESCE(SUM(n), 0) AS n, COUNT(*) AS c FROM ({base})", args)
    async with conn().execute(
            f"SELECT t.user_id, t.n, COALESCE(m.name, '') AS name, COALESCE(m.username, '') AS username "
            f"FROM ({base}) t LEFT JOIN members m ON m.chat_id = ? AND m.user_id = t.user_id "
            f"ORDER BY t.n DESC LIMIT ?", (*args, chat_id, limit)) as cur:
        top = [dict(r) for r in await cur.fetchall()]
    return total["n"], total["c"], top


async def member_name(chat_id: int, user_id: int) -> str:
    row = await get_member(chat_id, user_id)
    return (row or {}).get("name") or ""


# ---------- предупреждения ----------

def _warn_since(ttl_days: int) -> int:
    return now() - ttl_days * 86400 if ttl_days > 0 else 0


async def add_warn(chat_id: int, user_id: int, by_id: int, reason: str) -> int:
    cur = await conn().execute("INSERT INTO warns (chat_id, user_id, by_id, reason, ts) VALUES (?, ?, ?, ?, ?)",
                               (chat_id, user_id, by_id, reason, now()))
    await conn().commit()
    return cur.lastrowid


async def active_warns(chat_id: int, user_id: int, ttl_days: int) -> list[dict]:
    return await _all("SELECT * FROM warns WHERE chat_id = ? AND user_id = ? AND active = 1 AND ts >= ? ORDER BY ts",
                      (chat_id, user_id, _warn_since(ttl_days)))


async def count_warns(chat_id: int, user_id: int, ttl_days: int) -> int:
    row = await _one("SELECT COUNT(*) AS n FROM warns WHERE chat_id = ? AND user_id = ? AND active = 1 AND ts >= ?",
                     (chat_id, user_id, _warn_since(ttl_days)))
    return row["n"] if row else 0


async def unwarn_last(chat_id: int, user_id: int, ttl_days: int) -> bool:
    row = await _one("SELECT id FROM warns WHERE chat_id = ? AND user_id = ? AND active = 1 AND ts >= ? "
                     "ORDER BY ts DESC, id DESC LIMIT 1", (chat_id, user_id, _warn_since(ttl_days)))
    if not row:
        return False
    await _exec("UPDATE warns SET active = 0 WHERE id = ?", (row["id"],))
    return True


async def clear_warns(chat_id: int, user_id: int) -> None:
    await _exec("UPDATE warns SET active = 0 WHERE chat_id = ? AND user_id = ? AND active = 1", (chat_id, user_id))


# ---------- журнал ----------

async def log_action(chat_id: int, user_id: int, by_id: int, kind: str, until: int = 0, reason: str = "") -> int:
    if kind not in ACTION_KINDS:
        raise ValueError(kind)
    cur = await conn().execute(
        "INSERT INTO actions (chat_id, user_id, by_id, kind, until, reason, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, user_id, by_id, kind, until, reason, now()))
    await conn().commit()
    return cur.lastrowid


async def recent_actions(chat_id: int, limit: int = 10) -> list[dict]:
    return await _all("SELECT * FROM actions WHERE chat_id = ? ORDER BY ts DESC, id DESC LIMIT ?", (chat_id, limit))


async def stats(chat_id: int | None, since: int) -> dict[str, int]:
    """Сколько каких действий с момента since. chat_id=None: по всем чатам."""
    if chat_id is None:
        rows = await _all("SELECT kind, COUNT(*) AS n FROM actions WHERE ts >= ? GROUP BY kind", (since,))
    else:
        rows = await _all("SELECT kind, COUNT(*) AS n FROM actions WHERE chat_id = ? AND ts >= ? GROUP BY kind",
                          (chat_id, since))
    return {r["kind"]: r["n"] for r in rows}


async def active_chats_since(since: int) -> list[dict]:
    return await _all("SELECT chat_id, COUNT(*) AS n FROM actions WHERE ts >= ? GROUP BY chat_id ORDER BY n DESC",
                      (since,))


async def user_state(chat_id: int, user_id: int) -> dict:
    """Замучен ли и забанен ли человек, по журналу (последнее действие каждого вида)."""
    ts = now()
    mute = await _one("SELECT kind, until FROM actions WHERE chat_id = ? AND user_id = ? AND kind IN ('mute', 'unmute') "
                      "ORDER BY ts DESC, id DESC LIMIT 1", (chat_id, user_id))
    muted_until = 0
    if mute and mute["kind"] == "mute" and (mute["until"] == 0 or mute["until"] > ts):
        muted_until = mute["until"] or -1  # -1: навсегда
    ban = await _one("SELECT kind, until FROM actions WHERE chat_id = ? AND user_id = ? "
                     "AND kind IN ('ban', 'unban', 'kick') ORDER BY ts DESC, id DESC LIMIT 1", (chat_id, user_id))
    banned = bool(ban and ban["kind"] == "ban" and (ban["until"] == 0 or ban["until"] > ts))
    return {"muted_until": muted_until, "banned": banned}


# ---------- стоп-слова, белые списки, модераторы ----------

async def stopwords(chat_id: int) -> list[str]:
    return [r["word"] for r in await _all("SELECT word FROM stopwords WHERE chat_id = ? ORDER BY word", (chat_id,))]


async def add_stopwords(chat_id: int, words: list[str]) -> int:
    added = 0
    for w in words:
        cur = await conn().execute("INSERT OR IGNORE INTO stopwords (chat_id, word) VALUES (?, ?)", (chat_id, w))
        added += cur.rowcount or 0
    await conn().commit()
    return added


async def remove_stopword(chat_id: int, word: str) -> bool:
    return await _exec("DELETE FROM stopwords WHERE chat_id = ? AND word = ?", (chat_id, word)) > 0


async def clear_stopwords(chat_id: int) -> None:
    await _exec("DELETE FROM stopwords WHERE chat_id = ?", (chat_id,))


async def whitelist(chat_id: int) -> list[dict]:
    return await _all("SELECT w.user_id, w.added_by, w.ts, COALESCE(m.name, '') AS name, "
                      "COALESCE(m.username, '') AS username FROM whitelist w LEFT JOIN members m "
                      "ON m.chat_id = w.chat_id AND m.user_id = w.user_id WHERE w.chat_id = ? ORDER BY w.ts",
                      (chat_id,))


async def in_whitelist(chat_id: int, user_id: int) -> bool:
    return await _one("SELECT 1 FROM whitelist WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) is not None


async def toggle_whitelist(chat_id: int, user_id: int, by_id: int) -> bool:
    """True: добавили, False: убрали."""
    if await in_whitelist(chat_id, user_id):
        await _exec("DELETE FROM whitelist WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        return False
    await _exec("INSERT INTO whitelist (chat_id, user_id, added_by, ts) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, by_id, now()))
    return True


async def remove_whitelist(chat_id: int, user_id: int) -> None:
    await _exec("DELETE FROM whitelist WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))


async def domains(chat_id: int) -> list[str]:
    return [r["domain"] for r in await _all("SELECT domain FROM domains WHERE chat_id = ? ORDER BY domain", (chat_id,))]


async def add_domains(chat_id: int, items: list[str]) -> int:
    added = 0
    for d in items:
        cur = await conn().execute("INSERT OR IGNORE INTO domains (chat_id, domain) VALUES (?, ?)", (chat_id, d))
        added += cur.rowcount or 0
    await conn().commit()
    return added


async def remove_domain(chat_id: int, domain: str) -> bool:
    return await _exec("DELETE FROM domains WHERE chat_id = ? AND domain = ?", (chat_id, domain)) > 0


async def mods(chat_id: int) -> list[dict]:
    return await _all("SELECT d.user_id, d.added_by, d.ts, COALESCE(m.name, '') AS name, "
                      "COALESCE(m.username, '') AS username FROM mods d LEFT JOIN members m "
                      "ON m.chat_id = d.chat_id AND m.user_id = d.user_id WHERE d.chat_id = ? ORDER BY d.ts",
                      (chat_id,))


async def is_mod(chat_id: int, user_id: int) -> bool:
    return await _one("SELECT 1 FROM mods WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) is not None


async def toggle_mod(chat_id: int, user_id: int, by_id: int) -> bool:
    if await is_mod(chat_id, user_id):
        await _exec("DELETE FROM mods WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        return False
    await _exec("INSERT INTO mods (chat_id, user_id, added_by, ts) VALUES (?, ?, ?, ?)", (chat_id, user_id, by_id, now()))
    return True


async def remove_mod(chat_id: int, user_id: int) -> None:
    await _exec("DELETE FROM mods WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))


# ---------- права в боте ----------

async def rights_of(chat_id: int, user_id: int) -> int:
    row = await _one("SELECT flags FROM rights WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    return row["flags"] if row else 0


async def set_rights(chat_id: int, user_id: int, flags: int, by_id: int) -> None:
    if flags:
        await _exec("INSERT INTO rights (chat_id, user_id, flags, added_by, ts) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (chat_id, user_id) DO UPDATE SET flags = excluded.flags, added_by = excluded.added_by",
                    (chat_id, user_id, flags, by_id, now()))
    else:
        await _exec("DELETE FROM rights WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))


async def rights_list(chat_id: int) -> list[dict]:
    return await _all("SELECT r.user_id, r.flags, r.added_by, r.ts, COALESCE(m.name, '') AS name, "
                      "COALESCE(m.username, '') AS username FROM rights r LEFT JOIN members m "
                      "ON m.chat_id = r.chat_id AND m.user_id = r.user_id WHERE r.chat_id = ? ORDER BY r.ts",
                      (chat_id,))


async def migrate_rights_to_roles(admin_flag: int, mod_flags: int) -> None:
    """Один раз: старые галочки превращаются в роли (админ бота: Админ, любые другие права: Модератор)."""
    if await kv_get("roles_migrated") == "1":
        return
    await _exec("UPDATE rights SET flags = ? WHERE flags & ? != 0", (admin_flag, admin_flag))
    await _exec("UPDATE rights SET flags = ? WHERE flags & ? = 0 AND flags != 0", (mod_flags, admin_flag))
    await kv_set("roles_migrated", "1")


async def migrate_mods_to_rights(flags: int) -> int:
    """Один раз: старые «модераторы бота» получают права на наказания."""
    if await kv_get("mods_migrated") == "1":
        return 0
    rows = await _all("SELECT chat_id, user_id, added_by FROM mods")
    for r in rows:
        if not await rights_of(r["chat_id"], r["user_id"]):
            await set_rights(r["chat_id"], r["user_id"], flags, r["added_by"])
    await kv_set("mods_migrated", "1")
    return len(rows)


# ---------- очередь событий для Бакси ----------

async def outbox_add(payload: dict) -> None:
    await _exec("INSERT INTO outbox (payload, created_at, next_at) VALUES (?, ?, ?)",
                (json.dumps(payload, ensure_ascii=False), now(), now()))


async def outbox_due(limit: int = 50) -> list[dict]:
    rows = await _all("SELECT * FROM outbox WHERE next_at <= ? ORDER BY id LIMIT ?", (now(), limit))
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


async def outbox_done(item_id: int) -> None:
    await _exec("DELETE FROM outbox WHERE id = ?", (item_id,))


async def outbox_retry(item_id: int, delay: int = 60) -> None:
    await _exec("UPDATE outbox SET tries = tries + 1, next_at = ? WHERE id = ?", (now() + delay, item_id))


async def outbox_purge(max_age: int = 86400) -> int:
    return await _exec("DELETE FROM outbox WHERE created_at < ?", (now() - max_age,))


async def outbox_size() -> int:
    row = await _one("SELECT COUNT(*) AS n FROM outbox")
    return row["n"] if row else 0


# ---------- служебное ----------

async def kv_get(key: str, default: str = "") -> str:
    row = await _one("SELECT value FROM kv WHERE key = ?", (key,))
    return row["value"] if row else default


async def kv_set(key: str, value: str) -> None:
    await _exec("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, str(value)))


async def cleanup() -> int:
    """Журнал храним 90 дней, старые снятые предупреждения тоже не нужны."""
    cutoff = now() - ACTIONS_KEEP_DAYS * 86400
    n = await _exec("DELETE FROM actions WHERE ts < ?", (cutoff,))
    await _exec("DELETE FROM warns WHERE active = 0 AND ts < ?", (cutoff,))
    await _exec("DELETE FROM member_hours WHERE hour < ?", (now() // 3600 - 48,))
    await _exec("DELETE FROM member_days WHERE day < ?", (day_of(now() - 40 * 86400),))
    await conn().execute("PRAGMA optimize")                # обновить статистику индексов
    await conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")  # WAL не растёт на диске
    return n
