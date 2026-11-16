"""Фоновые дела раз в минуту: чистка журнала старше 90 дней (раз в сутки)."""

import asyncio
import logging

from aiogram import Bot

from guard import db

log = logging.getLogger(__name__)
TICK = 30


async def tick(bot: Bot) -> None:
    last = int(await db.kv_get("cleanup_at", "0") or 0)
    if db.now() - last > 86400:
        removed = await db.cleanup()
        await db.kv_set("cleanup_at", str(db.now()))
        if removed:
            log.info("Журнал: удалено %s записей старше %s дней", removed, db.ACTIONS_KEEP_DAYS)


async def run(bot: Bot) -> None:
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в фоновых делах")
        await asyncio.sleep(TICK)
