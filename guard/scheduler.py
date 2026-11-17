"""Фоновые дела каждые 30 секунд: выключить режим атаки по времени, раз в минуту дослать события Бакси
из outbox, раз в сутки чистить журнал старше 90 дней."""

import asyncio
import logging
import time

from aiogram import Bot

from guard import baxi, db, protect

log = logging.getLogger(__name__)
TICK = 30
_last_flush = 0.0


async def tick(bot: Bot) -> None:
    global _last_flush
    for chat in await db.all_chats():  # режим атаки сам выключается по времени
        if 0 < chat["attack_until"] <= db.now():
            await protect.attack_off(bot, chat["chat_id"], auto=True)
    if time.monotonic() - _last_flush >= 60:  # outbox для Бакси раз в минуту
        _last_flush = time.monotonic()
        await baxi.flush_outbox()
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
