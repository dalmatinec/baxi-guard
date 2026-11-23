"""Запуск: python -m guard"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from guard import VERSION, api, baxi, config, db, handlers, scheduler
from guard.commands import set_commands
from guard.middlewares import ChatTracker
from guard.out import sanitize_requests

log = logging.getLogger("guard")


async def main() -> None:
    config.check()
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    await db.connect()
    from guard import texts
    await texts.init()  # свои тексты чатов: премиум-эмодзи, картинки, кнопки
    from guard.gamecore import core as gamecore
    await gamecore.init()  # игры со списком: рулетка, топы, ФК
    from guard import triggers
    await triggers.init()  # триггеры чатов
    from guard.handlers.admin import load_pack
    await load_pack()  # премиум-эмодзи владельца
    from guard import buttons
    await buttons.init()  # цвет и значки кнопок от владельца
    from guard import perms
    await db.migrate_mods_to_rights(perms.OLD_MODS_FLAGS)  # старые модераторы бота получают права
    await db.migrate_rights_to_roles(perms.R_ADMIN, perms.ROLE_MOD_FLAGS)  # галочки в роли
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    bot.session.middleware(sanitize_requests)  # никаких длинных тире и ёлочек ни в одном сообщении
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(ChatTracker(count_messages=True))
    dp.edited_message.outer_middleware(ChatTracker(count_messages=False))
    dp.callback_query.outer_middleware(buttons.PickMiddleware())  # 🎯 выбор кнопки нажатием (владелец)
    dp.include_router(handlers.setup())

    me = await bot.me()
    log.info("Модератор @%s запущен, версия %s", me.username, VERSION)
    api.BOT_USERNAME = me.username or ""
    await set_commands(bot)

    runner = await api.start()  # внутренний API для Бакси, только 127.0.0.1
    from guard.commands import refresh_all_once
    tasks = [asyncio.create_task(scheduler.run(bot)), asyncio.create_task(refresh_all_once(bot))]
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        # не больше 50 обновлений обрабатываются одновременно: при наплыве бот не съедает память
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), tasks_concurrency_limit=50)
    finally:
        for t in tasks:
            t.cancel()
        from guard import out as _out
        from guard.gamecore import core as _core
        for t in list(_out._tasks) + list(_core._tasks):   # отложенные удаления и правки списков
            t.cancel()
        if runner is not None:
            await runner.cleanup()
        await baxi.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code not in (None, 0):
            raise
