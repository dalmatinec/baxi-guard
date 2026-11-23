"""Меню команд по кнопке /: админам групп команды модерации, в личке настройки, владельцу /admin."""

import logging

from aiogram import Bot
from aiogram.types import (BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
                           BotCommandScopeAllPrivateChats, BotCommandScopeChat)

from guard import config

log = logging.getLogger(__name__)

PRIVATE = [("start", "Главное меню"), ("settings", "Настройки модерации моих чатов"), ("v", "Пинг")]
ADMIN = [("mute", "Мут: ответом, время, причина"), ("unmute", "Снять мут"), ("warn", "Предупреждение"),
         ("warns", "Предупреждения человека"), ("unwarn", "Снять последнее предупреждение"),
         ("ban", "Бан: ответом, время, причина"), ("unban", "Разбан"), ("kick", "Выгнать без бана"),
         ("del", "Удалить сообщение"), ("pin", "Закрепить"), ("unpin", "Открепить"), ("link", "Ссылка-приглашение"), ("attack", "Режим атаки"),
         ("whitelist", "Белый список"), ("mod", "Права в боте (ответом)"), ("settings", "Настройки"),
         ("chat", "Чат и права бота"), ("profile", "Профиль"), ("v", "Пинг")]
OWNER = [("admin", "Все чаты и здоровье бота"), *PRIVATE]
GROUP = [("profile", "Мой профиль")]


def _cmds(items: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in items]


async def set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(_cmds(PRIVATE), scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(_cmds(GROUP), scope=BotCommandScopeAllGroupChats())  # участникам только профиль
        await bot.set_my_commands(_cmds(ADMIN), scope=BotCommandScopeAllChatAdministrators())
        for owner in config.OWNER_IDS:
            try:
                await bot.set_my_commands(_cmds(OWNER), scope=BotCommandScopeChat(chat_id=owner))
            except Exception:
                pass  # владелец ещё не писал боту
    except Exception as exc:
        log.warning("Не задал меню команд: %s", exc)
