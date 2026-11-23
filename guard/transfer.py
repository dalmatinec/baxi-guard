"""Перенос чата: всё, что модератор знает о чате, копируется в другой чат (например, старый чат удалили
или сделали новый). Настройки, стоп-слова, домены, белый список, права в боте, тексты, триггеры, топы,
активные предупреждения. В новом чате это заменяет то же самое; журнал действий остаётся у старого.
Репутация, ранги и монеты живут у Бакси, у него свой перенос."""

from guard import db

SETTINGS_SKIP = {"chat_id", "title", "username", "created_at", "attack_until", "own_until", "log_chat_id"}
# (таблица, столбцы кроме chat_id, ключ для замены)
TABLES = [
    ("stopwords", ["word"]),
    ("domains", ["domain"]),
    ("whitelist", ["user_id", "added_by", "ts"]),
    ("rights", ["user_id", "flags", "added_by", "ts"]),
    ("chat_texts", ["key", "html", "photo", "buttons"]),
    ("triggers", ["word", "kind", "file_id", "html", "created_by", "created"]),
    ("game_tops", ["user_id", "game_id", "until", "by_id", "ts"]),
]


async def copy_chat(src: int, dst: int) -> dict[str, int]:
    """Скопировать данные модератора из src в dst. Возвращает, сколько чего перенесено."""
    c = db.conn()
    counts: dict[str, int] = {}
    old = await db.get_chat(src)
    if old is None:
        raise ValueError("нет такого чата")
    await db.ensure_chat(dst)
    fields = {k: v for k, v in old.items() if k not in SETTINGS_SKIP and k in db.CHAT_FIELDS}
    await db.set_chat(dst, **fields)
    counts["настройки"] = len(fields)
    for table, cols in TABLES:
        names = ", ".join(cols)
        if table == "game_tops":   # топы на конкретную игру в другой чат не переносятся
            where = "chat_id = ? AND game_id = 0"
        else:
            where = "chat_id = ?"
        await c.execute(f"DELETE FROM {table} WHERE chat_id = ?", (dst,))
        cur = await c.execute(f"INSERT OR IGNORE INTO {table} (chat_id, {names}) SELECT ?, {names} FROM {table} "
                              f"WHERE {where}", (dst, src))
        counts[table] = cur.rowcount or 0
    cur = await c.execute("INSERT INTO warns (chat_id, user_id, by_id, reason, ts, active) "
                          "SELECT ?, user_id, by_id, reason, ts, active FROM warns WHERE chat_id = ? AND active = 1",
                          (dst, src))
    counts["warns"] = cur.rowcount or 0
    await c.commit()
    from guard import triggers
    triggers.forget(dst)
    return counts


NAMES = {"настройки": "настроек", "stopwords": "стоп-слов", "domains": "доменов", "whitelist": "в белом списке",
         "rights": "с правами", "chat_texts": "своих текстов", "triggers": "триггеров", "game_tops": "топов",
         "warns": "предупреждений"}


def summary(counts: dict[str, int]) -> str:
    return "\n".join(f"• {NAMES.get(k, k)}: {v}" for k, v in counts.items())
