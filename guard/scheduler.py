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


def heartbeat() -> None:
    """Пульс для сторожа (deploy/guard-health.sh): бот жив и фоновые дела идут."""
    from guard import config
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "heartbeat").write_text(str(int(time.time())))
    except OSError:
        pass


async def tick(bot: Bot) -> None:
    global _last_flush
    heartbeat()
    for chat in await db.all_chats():  # режим атаки сам выключается по времени
        if 0 < chat["attack_until"] <= db.now():
            await protect.attack_off(bot, chat["chat_id"], auto=True)
    from guard import owners, welcome
    await welcome.sweep(bot)  # капчи, которые просрочились, пока бот был выключен
    await owners.sweep(bot)   # запросы заместителей, срок которых вышел
    await owners.maybe_refresh_all(bot)  # раз в 6 часов: на месте ли создатели, есть ли бот в чатах
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
            await asyncio.wait_for(tick(bot), timeout=120)   # фоновые дела не могут зависнуть навсегда
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.warning("Фоновые дела не уложились в 2 минуты, пропускаю круг")
        except Exception:
            log.exception("Ошибка в фоновых делах")
        await asyncio.sleep(TICK)
