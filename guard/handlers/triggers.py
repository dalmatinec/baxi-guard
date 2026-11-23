"""Триггеры в чате.

Управление прямо в чате (право 🧩 триггеры, создатель всегда):
  +триггер слово    ответом на сообщение: сохранить его (текст с премиум-эмодзи, пересланное, фото, видео,
                    гифка, стикер, голосовое, файл) как ответ на «слово»; можно несколько слов
  -триггер слово    удалить (без ответа)
  триггеры          список триггеров чата
То же самое есть в личке: ⚙️ чат → 🧩 Триггеры.

Срабатывание: сообщение целиком совпало со словом триггера (без регистра, ё/е и знаков по краям)."""

import re

from aiogram import Bot, F, Router
from aiogram.types import Message

from guard import out, perms, triggers
from guard.handlers.util import done
from guard.out import thread_of

router = Router(name="triggers")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

_ADD = re.compile(r"^\s*\+\s*тр[иі]ггер\s+(.+)$", re.I | re.S)
_DEL = re.compile(r"^\s*-\s*тр[иі]ггер\s+(.+)$", re.I | re.S)
_LIST = re.compile(r"^\s*тр[иі]ггеры\s*$", re.I)


def real_reply(message: Message) -> Message | None:
    from guard.handlers.moderation import real_reply as rr
    return rr(message)


async def trigger_command(message: Message, allowed: bool = True) -> dict | bool:
    text = message.text or ""
    name, m = "", None
    for cmd, rx in (("add", _ADD), ("del", _DEL), ("list", _LIST)):
        m = rx.match(text)
        if m:
            name = cmd
            break
    if not name:
        return False
    if not allowed or not await perms.message_has(message, perms.R_TRIGGERS):
        return False
    return {"cmd": name, "word": (m.group(1) if m.groups() else "").strip()}


@router.message(trigger_command)
async def on_trigger_command(message: Message, chat_row: dict, cmd: str, word: str) -> None:
    chat_id = message.chat.id
    if cmd == "list":
        items = list((await triggers.all_of(chat_id)).values())
        if not items:
            await done(message, chat_row, "триггеров пока нет. Добавить: ответьте «+триггер слово» на сообщение.")
            return
        lines = [f"{i}. {out.esc(t['word'])}" for i, t in enumerate(items[:150], 1)]
        if len(items) > 150:
            lines.append(f"и ещё {len(items) - 150}")
        await done(message, chat_row, f"триггеры ({len(items)}):\n" + "\n".join(lines))
        return
    if cmd == "del":
        ok = await triggers.delete(chat_id, word)
        await done(message, chat_row, f"триггер \"{out.esc(triggers.norm(word))}\" удалён." if ok
                   else f"триггера \"{out.esc(triggers.norm(word))}\" нет.")
        return
    rt = real_reply(message)
    if rt is None:
        await done(message, chat_row, "ответьте «+триггер слово» на сообщение, которым бот будет отвечать: текст, "
                                      "пересланное, фото, видео, гифка, стикер, голосовое или файл.")
        return
    body = triggers.content_of(rt)
    if body is None:
        await done(message, chat_row, "такое сообщение сохранить не могу.")
        return
    error = await triggers.save(chat_id, word, *body, message.from_user.id)
    if error:
        await done(message, chat_row, error + ".")
        return
    await done(message, chat_row, f"триггер \"{out.esc(triggers.norm(word))}\" сохранён. Напишут это слово, отвечу.")


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
