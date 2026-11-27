"""Перенос чата: всё, что модератор знает о чате, копируется в другой чат. Например, старый чат удалили или
заблокировали, бота из него убрали, или просто сделали новый.

Всегда переносятся настройки, стоп-слова, домены, тексты, триггеры, топы. По выбору: роли и белый список,
предупреждения, бан-лист (бот банит тех же людей в новом чате), активность участников, тариф и данные Бакси.
Журнал действий остаётся у старого чата. Старый чат помечается как перенесённый."""

from guard import db

SETTINGS_SKIP = {"chat_id", "title", "username", "created_at", "attack_until", "own_until", "log_chat_id",
                 "owner_id", "owner_seen", "owner_lost", "heir_id", "owner_override", "gone_at", "moved_to"}

# что переносить (битовая маска)
ROLES = 1       # роли и белый список
WARNS = 2       # активные предупреждения
BANS = 4        # бан-лист: забанить тех же в новом чате
ACTIVITY = 8    # активность: сообщения участников, дни, кто когда пришёл
TARIFF = 16     # своя подписка (уходит из старого чата)
BAXI = 32       # Бакси переносит своё: аренду, репутацию, память о людях
HEIR = 64       # заместитель владельца
ALL = ROLES | WARNS | BANS | ACTIVITY | TARIFF | BAXI | HEIR
OPTIONS = [(ROLES, "👥 Роли и белый список"), (HEIR, "👑 Заместитель владельца"), (WARNS, "⚠️ Предупреждения"),
           (BANS, "⛔ Бан-лист"), (ACTIVITY, "📊 Активность и топы"), (TARIFF, "💳 Тариф"), (BAXI, "🤝 Бакси")]
TARIFF_LOCK = 30 * 86400   # тариф, который пришёл переносом, дальше можно передать не раньше чем через 30 дней
MAX_BANS = 2000

BASE_TABLES = [
    ("stopwords", ["word"]),
    ("domains", ["domain"]),
    ("chat_texts", ["key", "html", "photo", "buttons"]),
    ("triggers", ["word", "kind", "file_id", "html", "created_by", "created"]),
    ("game_tops", ["user_id", "game_id", "until", "by_id", "ts"]),
]
ROLE_TABLES = [
    ("whitelist", ["user_id", "added_by", "ts"]),
    ("rights", ["user_id", "flags", "added_by", "ts"]),
]


async def _table_exists(name: str) -> bool:
    async with db.conn().execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)) as cur:
        return await cur.fetchone() is not None


async def tariff_locked(src: int) -> int:
    """До какого времени тариф этого чата нельзя передавать дальше (0: можно)."""
    got = int(await db.kv_get(f"tariff_in:{src}", "0") or 0)
    return got + TARIFF_LOCK if got and db.now() < got + TARIFF_LOCK else 0


async def banned_ids(chat_id: int) -> list[int]:
    """Кто сейчас в бане: последнее действие с человеком ban (не unban), бессрочный или ещё не истёк."""
    rows = await db._all(
        "SELECT a.user_id, a.until FROM actions a JOIN (SELECT user_id, MAX(id) AS id FROM actions "
        "WHERE chat_id = ? AND kind IN ('ban', 'unban') GROUP BY user_id) last ON a.id = last.id "
        "WHERE a.kind = 'ban'", (chat_id,))
    return [r["user_id"] for r in rows if not r["until"] or r["until"] > db.now()][:MAX_BANS]


async def copy_chat(src: int, dst: int, mask: int = ALL & ~BANS & ~BAXI) -> dict[str, int]:
    """Скопировать данные модератора из src в dst. Возвращает, сколько чего перенесено.
    Баны и Бакси делает обработчик (нужны запросы в Telegram и к Бакси), здесь только данные."""
    c = db.conn()
    counts: dict[str, int] = {}
    old = await db.get_chat(src)
    if old is None:
        raise ValueError("нет такого чата")
    await db.ensure_chat(dst)
    fields = {k: v for k, v in old.items() if k not in SETTINGS_SKIP and k in db.CHAT_FIELDS}
    await db.set_chat(dst, **fields)
    counts["настройки"] = len(fields)
    tables = BASE_TABLES + (ROLE_TABLES if mask & ROLES else [])
    for table, cols in tables:
        if not await _table_exists(table):
            continue
        names = ", ".join(cols)
        where = "chat_id = ? AND game_id = 0" if table == "game_tops" else "chat_id = ?"   # топ на игру не уходит
        await c.execute(f"DELETE FROM {table} WHERE chat_id = ?", (dst,))
        cur = await c.execute(f"INSERT OR IGNORE INTO {table} (chat_id, {names}) SELECT ?, {names} FROM {table} "
                              f"WHERE {where}", (dst, src))
        counts[table] = cur.rowcount or 0
    if mask & WARNS:
        cur = await c.execute("INSERT INTO warns (chat_id, user_id, by_id, reason, ts, active) "
                              "SELECT ?, user_id, by_id, reason, ts, active FROM warns WHERE chat_id = ? AND active = 1",
                              (dst, src))
        counts["warns"] = cur.rowcount or 0
    if mask & ACTIVITY:
        # кого бот уже видел в новом чате, тем сообщения складываются, остальные приходят как были
        await c.execute(
            "UPDATE members SET msg_count = msg_count + COALESCE((SELECT o.msg_count FROM members o WHERE "
            "o.chat_id = ? AND o.user_id = members.user_id), 0), first_seen = MIN(first_seen, COALESCE((SELECT "
            "o.first_seen FROM members o WHERE o.chat_id = ? AND o.user_id = members.user_id), first_seen)), "
            "joined_at = 0 WHERE chat_id = ? AND user_id IN (SELECT user_id FROM members WHERE chat_id = ?)",
            (src, src, dst, src))
        cur = await c.execute(
            "INSERT OR IGNORE INTO members (chat_id, user_id, username, name, joined_at, msg_count, last_seen, first_seen) "
            "SELECT ?, user_id, username, name, joined_at, msg_count, last_seen, first_seen FROM members WHERE chat_id = ?",
            (dst, src))
        counts["members"] = cur.rowcount or 0
        await c.execute("INSERT INTO member_days (chat_id, user_id, day, n) SELECT ?, user_id, day, n FROM member_days "
                        "WHERE chat_id = ? ON CONFLICT (chat_id, user_id, day) DO UPDATE SET n = n + excluded.n",
                        (dst, src))
    if mask & HEIR and old["heir_id"]:
        await db.set_chat(dst, heir_id=old["heir_id"])
        counts["heir"] = 1
    if mask & TARIFF and old["own_until"] and not await tariff_locked(src):
        new = await db.get_chat(dst)
        until = -1 if -1 in (old["own_until"], new["own_until"]) else max(old["own_until"], new["own_until"])
        await db.set_chat(dst, own_until=until)
        await db.set_chat(src, own_until=0)
        await db.kv_set(f"tariff_in:{dst}", str(db.now()))
        counts["tariff"] = 1
    await db.set_chat(src, moved_to=dst)
    await c.commit()
    from guard import triggers
    triggers.forget(dst)
    return counts


NAMES = {"настройки": "настроек", "stopwords": "стоп-слов", "domains": "доменов", "whitelist": "в белом списке",
         "rights": "с ролями", "chat_texts": "своих текстов", "triggers": "триггеров", "game_tops": "топов",
         "warns": "предупреждений", "members": "участников с активностью", "heir": "заместитель",
         "tariff": "тариф", "bans": "забанено в новом чате", "baxi": "Бакси"}


def summary(counts: dict[str, int]) -> str:
    lines = []
    for k, v in counts.items():
        if k in ("tariff", "heir"):
            lines.append(f"• {NAMES[k]}: перенесён")
        elif k == "baxi":
            continue
        else:
            lines.append(f"• {NAMES.get(k, k)}: {v}")
    return "\n".join(lines)
