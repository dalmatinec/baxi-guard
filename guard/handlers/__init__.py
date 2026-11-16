"""Все обработчики. Порядок важен: сначала команды, потом автоматика, которая смотрит на всё подряд."""

from aiogram import Router

from guard.handlers import admin, common, moderation, settings


def setup() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(moderation.router)
    root.include_router(admin.router)
    root.include_router(settings.router)
    return root
