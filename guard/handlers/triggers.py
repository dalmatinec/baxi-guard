"""Ответы триггерами в чате: сообщение целиком совпало со словом триггера."""

from aiogram import Bot, F, Router
from aiogram.types import Message

from guard import triggers
from guard.out import thread_of

router = Router(name="triggers")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def is_trigger(message: Message, allowed: bool = True) -> dict | bool:
    text = message.text or ""
    if not allowed or not text or text.startswith("/") or (message.from_user and message.from_user.is_bot):
        return False
    trig = await triggers.match(message.chat.id, text)
    return {"trig": trig} if trig else False


@router.message(is_trigger)
async def on_trigger(message: Message, bot: Bot, trig: dict) -> None:
    if not triggers.cooled(message.chat.id, trig["word"]):
        return
    try:
        await triggers.send(bot, message.chat.id, trig, reply_to=message.message_id, thread=thread_of(message))
    except Exception:
        pass
