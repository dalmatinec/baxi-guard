"""Все обработчики. Порядок важен: сначала команды, потом автоматика, которая смотрит на всё подряд."""

from aiogram import Router

from guard.handlers import common, moderation


def setup() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(moderation.router)
    return root
