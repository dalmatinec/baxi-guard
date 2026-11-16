"""Автоматика в группе. Стоит первой: проверяет каждое сообщение и, если всё чисто, отдаёт его дальше
(SkipHandler), чтобы спам нельзя было протащить, начав сообщение с команды."""

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import ChatMemberUpdated, Message

from guard import access, db, perms, protect

log = logging.getLogger(__name__)
router = Router(name="auto")
GROUP = F.chat.type.in_({"group", "supergroup"})

SERVICE_FIELDS = {
    "new_chat_members": "svc_join", "left_chat_member": "svc_leave", "pinned_message": "svc_pin",
    "new_chat_photo": "svc_photo", "delete_chat_photo": "svc_photo", "new_chat_title": "svc_title",
}
SERVICE_FILTER = (F.new_chat_members | F.left_chat_member | F.pinned_message | F.new_chat_photo
                  | F.delete_chat_photo | F.new_chat_title)


@router.message(GROUP, SERVICE_FILTER)
async def on_service(message: Message, bot: Bot, chat_row: dict, allowed: bool) -> None:
    if not allowed:
        return
    kicked_all = False
    if message.new_chat_members:
        results = [await protect.on_join(bot, chat_row, u) for u in message.new_chat_members]
        kicked_all = bool(results) and all(results)
        chat_row = await db.get_chat(message.chat.id) or chat_row
    field = next((f for attr, f in SERVICE_FIELDS.items() if getattr(message, attr, None)), "")
    if (field and chat_row.get(field)) or kicked_all:
        try:
            await message.delete()
        except Exception:
            pass


@router.message(GROUP)
async def on_message(message: Message, bot: Bot, chat_row: dict, allowed: bool, member_row: dict | None = None):
    if allowed and await protect.check_message(bot, chat_row, message, member_row):
        return
    raise SkipHandler()


@router.edited_message(GROUP)
async def on_edit(message: Message, bot: Bot, chat_row: dict, allowed: bool) -> None:
    if not allowed:
        return
    member = None
    if message.from_user and not message.sender_chat:
        member = await db.get_member(message.chat.id, message.from_user.id)
    await protect.check_message(bot, chat_row, message, member, edited=True)


@router.chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_member(event: ChatMemberUpdated, bot: Bot) -> None:
    """Входы в больших чатах, где служебные сообщения о входе скрыты. И сброс кеша прав."""
    user = event.new_chat_member.user
    perms.forget(event.chat.id, user.id)
    old, new = event.old_chat_member, event.new_chat_member
    was_in = old.status in ("member", "administrator", "creator") or (old.status == "restricted"
                                                                     and getattr(old, "is_member", False))
    now_in = new.status in ("member",) or (new.status == "restricted" and getattr(new, "is_member", False))
    if was_in or not now_in:
        return
    chat = await db.ensure_chat(event.chat.id, event.chat.title or "", event.chat.username or "")
    if not await access.allowed(chat):
        return
    await protect.on_join(bot, chat, user)
