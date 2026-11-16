"""Общее для команд в группе: ответ с автоудалением и удаление самой команды."""

from aiogram.types import InlineKeyboardMarkup, Message

from guard import out

CMD_DELETE_DELAY = 5  # через сколько секунд удаляется команда админа


async def done(message: Message, chat: dict, text: str, *, markup: InlineKeyboardMarkup | None = None,
               ttl: int | None = None) -> Message | None:
    """Ответ от имени модерации. Ответ живёт reply_ttl секунд (0: навсегда), команда удаляется через 5 секунд."""
    sent = await out.answer(message, out.head(text), ttl=chat["reply_ttl"] if ttl is None else ttl, markup=markup)
    drop_command(message, chat)
    return sent


def drop_command(message: Message, chat: dict) -> None:
    if chat.get("cmd_delete"):
        out.delete_later(message.bot, message.chat.id, message.message_id, CMD_DELETE_DELAY)
