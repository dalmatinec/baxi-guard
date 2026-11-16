"""Здоровье бота для /admin: сколько работает, память, база, очередь для Бакси."""

import time

from guard import config, db

STARTED = time.monotonic()


def uptime() -> str:
    s = int(time.monotonic() - STARTED)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return f"{d} дн {h} ч" if d else (f"{h} ч {m} мин" if h else f"{m} мин")


def rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def db_mb() -> float:
    total = 0
    for suffix in ("", "-wal"):
        p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
        if p.exists():
            total += p.stat().st_size
    return total / 1024 / 1024


async def snapshot() -> dict:
    return {"uptime": uptime(), "rss": rss_mb(), "db": db_mb(), "outbox": await db.outbox_size()}
