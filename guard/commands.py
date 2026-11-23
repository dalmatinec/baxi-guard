"""Меню команд по кнопке /. Команды лички видны только в личке, команды чата только в чате.
Команды модерации видит только тот, у кого есть роль в этом чате (меню на человека в чате):
участникам и админам Telegram без роли в чате виден только /profile."""

import asyncio
import logging

from aiogram import Bot
from aiogram.types import (BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
                           BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeChatMember)

from guard import config, db, perms

log = logging.getLogger(__name__)

PRIVATE = [("start", "Главное меню"), ("settings", "Мои чаты и их настройки"), ("cancel", "Отменить ввод")]
OWNER_DM = [("admin", "Все чаты и здоровье бота"), *PRIVATE]
GROUP = [("profile", "Мой профиль"), ("stat", "Активность чата")]
MOD = [("mute", "Мут: ответом, время, причина"), ("unmute", "Снять мут"), ("warn", "Предупреждение"),
       ("unwarn", "Снять предупреждение"), ("warns", "Предупреждения человека"),
       ("ban", "Бан: ответом, время, причина"), ("unban", "Разбан"), ("kick", "Выгнать без бана"),
       ("del", "Удалить сообщение"), ("pin", "Закрепить"), ("unpin", "Открепить"), ("link", "Ссылка-приглашение"),
       ("attack", "Режим атаки"), ("game", "Запустить игру"), ("list", "Показать список игры"),
       ("close", "Закрыть список"), ("open", "Открыть список"), ("fk", "ФК для игры: /fk 30"),
       ("random", "Рандом: /random 36"), ("endgame", "Закончить игру"), ("profile", "Профиль"),
       ("stat", "Активность"), ("v", "Пинг")]
ADMIN = [*MOD[:-3], ("trust", "В белый список (ответом)"), ("untrust", "Из белого списка"),
         ("mod", "Назначить модератора (ответом)"), ("unmod", "Снять роль (ответом)"),
         ("settings", "Настройки в личке"), ("chat", "Чат и права бота"), ("profile", "Профиль"), ("stat", "Активность"),
         ("v", "Пинг")]
OWNER = [*ADMIN[:-3], ("adm", "Назначить админа (ответом)"), ("profile", "Профиль"), ("stat", "Активность"),
         ("v", "Пинг")]
BY_ROLE = {perms.MOD: MOD, perms.ADMIN: ADMIN, perms.OWNER: OWNER}


def _cmds(items: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in items]


async def set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(_cmds(PRIVATE), scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(_cmds(GROUP), scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(_cmds(GROUP), scope=BotCommandScopeAllChatAdministrators())
        for owner in config.OWNER_IDS:
            try:
                await bot.set_my_commands(_cmds(OWNER_DM), scope=BotCommandScopeChat(chat_id=owner))
            except Exception:
                pass  # владелец ещё не писал боту
    except Exception as exc:
        log.warning("Не задал меню команд: %s", exc)


async def refresh_member(bot: Bot, chat_id: int, user_id: int) -> None:
    """Меню человека в чате по его роли: модератор, админ, владелец или обычное (только /profile)."""
    scope = BotCommandScopeChatMember(chat_id=chat_id, user_id=user_id)
    try:
        r = await perms.role(bot, chat_id, user_id)
        if r in BY_ROLE:
            await bot.set_my_commands(_cmds(BY_ROLE[r]), scope=scope)
        else:
            await bot.delete_my_commands(scope=scope)
    except Exception as exc:
        log.info("Не обновил меню %s в %s: %s", user_id, chat_id, exc)


async def refresh_chat(bot: Bot, chat_id: int) -> None:
    """Меню создателя и всех, у кого есть роль в чате."""
    users = {r["user_id"] for r in await db.rights_list(chat_id)}
    try:
        for m in await bot.get_chat_administrators(chat_id):
            if m.status == "creator" and not m.user.is_bot:
                users.add(m.user.id)
    except Exception:
        pass
    for uid in users:
        await refresh_member(bot, chat_id, uid)
        await asyncio.sleep(0.2)


async def refresh_all_once(bot: Bot) -> None:
    """Один раз после обновления: разложить меню по всем чатам (в фоне, с паузами)."""
    if await db.kv_get("menus_v3") == "1":
        return
    for chat in await db.all_chats():
        await refresh_chat(bot, chat["chat_id"])
    await db.kv_set("menus_v3", "1")
