"""Все обработчики. Порядок важен: сначала команды, потом автоматика, которая смотрит на всё подряд."""

from aiogram import Router

from guard.handlers import admin, auto, common, games, moderation, settings


def setup() -> Router:
    root = Router(name="root")
    root.include_router(auto.router)  # первой: проверяет всё и отдаёт чистое дальше
    root.include_router(games.router)  # игры со списком: после автоматики, до команд
    root.include_router(common.router)
    root.include_router(moderation.router)
    root.include_router(admin.router)
    root.include_router(settings.router)
    return root
