"""Перед обработчиками в группе: регистрируем чат и человека, решаем, работает ли модератор в этом чате."""

import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

from guard import access, db
from guard.out import full_name

log = logging.getLogger(__name__)

GROUPS = {"group", "supergroup"}


class ChatTracker(BaseMiddleware):
    """Вешается на message и edited_message. Кладёт в data: chat_row, member_row, allowed."""

    def __init__(self, count_messages: bool = True):
        self.count_messages = count_messages

    async def __call__(self, handler, event: Message, data: dict):
        if event.chat.type not in GROUPS:
            return await handler(event, data)
        try:
            if event.migrate_to_chat_id:  # старая группа стала супергруппой: всё переедет с её сообщением
                await db.migrate_chat(event.chat.id, event.migrate_to_chat_id)
                return None
            if event.migrate_from_chat_id:
                await db.migrate_chat(event.migrate_from_chat_id, event.chat.id)
            chat = await db.ensure_chat(event.chat.id, event.chat.title or "", event.chat.username or "")
            data["chat_row"] = chat
            data["allowed"] = await access.allowed(chat)
            user = event.from_user
            if user and not user.is_bot and not event.sender_chat:
                counted = self.count_messages and not event.new_chat_members and not event.left_chat_member
                data["member_row"] = await db.touch_member(
                    event.chat.id, user.id, user.username or "", full_name(user), message=counted)
                if counted:   # ФК активной игры со списком
                    from guard.gamecore import core as gamecore
                    text = event.text or event.caption
                    await gamecore.on_message(event.chat.id, user.id, text, bool(text and text.startswith("/")))
        except Exception:
            log.exception("Не записал чат или участника")
            data.setdefault("chat_row", None)
            data.setdefault("allowed", True)
        if data.get("chat_row") is None:
            return None
        return await handler(event, data)
