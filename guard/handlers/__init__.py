"""Все обработчики. Порядок важен: сначала команды, потом автоматика, которая смотрит на всё подряд."""

from aiogram import Router

from guard.handlers import admin, auto, common, games, moderation, nick, settings, triggers, welcome


def setup() -> Router:
    root = Router(name="root")
    root.include_router(auto.router)  # первой: проверяет всё и отдаёт чистое дальше
    root.include_router(settings.group_router)  # ввод для настроек, открытых в чате: раньше игр и триггеров
    root.include_router(games.router)  # игры со списком: после автоматики, до команд
    root.include_router(common.router)
    root.include_router(welcome.router)  # приветствие и капча: до триггеров, чтобы слово не перехватили
    root.include_router(triggers.router)  # после команд: на команды триггеры не отвечают
    root.include_router(moderation.router)
    root.include_router(admin.router)
    root.include_router(nick.router)  # ✨ мой ник в личке: свой ввод, до общего ввода настроек
    root.include_router(settings.router)
    return root
