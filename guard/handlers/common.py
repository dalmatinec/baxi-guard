"""/v, /chat и что делать, когда бота добавили в чат или убрали."""

import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from guard import VERSION, access, db, out, perms
from guard.handlers.util import done

log = logging.getLogger(__name__)
router = Router(name="common")
GROUP = F.chat.type.in_({"group", "supergroup"})


@router.message(Command("v"), F.chat.type == "private")
async def ping_private(message: Message) -> None:
    started = time.monotonic()
    sent = await message.answer(out.head(f"на месте, версия {VERSION}"))
    ms = int((time.monotonic() - started) * 1000)
    await sent.edit_text(out.head(f"на месте, версия {VERSION}, ответ за {ms} мс"))


@router.message(Command("v"), GROUP)
async def ping_group(message: Message, chat_row: dict) -> None:
    if perms.is_anon_admin(message) or not message.from_user or \
            not await perms.has_any(message.bot, message.chat.id, message.from_user.id):
        return
    started = time.monotonic()
    sent = await done(message, chat_row, f"на месте, версия {VERSION}")
    if sent:
        ms = int((time.monotonic() - started) * 1000)
        try:
            await sent.edit_text(out.head(f"на месте, версия {VERSION}, ответ за {ms} мс"))
        except Exception:
            pass


def protections_line(chat: dict) -> str:
    def on(v) -> str:
        return "вкл" if v else "выкл"
    svc = any(chat[k] for k in ("svc_join", "svc_leave", "svc_pin", "svc_photo", "svc_title"))
    attack = chat["attack_until"] > db.now()
    return (f"стоп-слова {on(chat['stop_on'])}, антиспам {on(chat['spam_on'])}, служебные {on(svc)}, "
            f"режим атаки {on(attack)}")


@router.message(Command("chat"), GROUP)
async def chat_info(message: Message, chat_row: dict) -> None:
    if perms.is_anon_admin(message) or not message.from_user or \
            not await perms.has_any(message.bot, message.chat.id, message.from_user.id):
        return
    ok, mode = await access.status(chat_row)
    miss = await perms.missing_rights(message.bot, message.chat.id)
    if miss is None:
        rights = "я не администратор, дайте права: " + ", ".join(perms.RIGHTS.values())
    elif miss:
        rights = "не хватает: " + ", ".join(miss)
    else:
        rights = "все нужные есть"
    text = (f"ID чата: <code>{message.chat.id}</code>\n"
            f"Режим: {out.esc(mode)}{'' if ok else ', модерация не активна'}\n"
            f"Права бота: {rights}\n"
            f"Защита: {protections_line(chat_row)}")
    await done(message, chat_row, text)


# ---------- профиль ----------

PROFILE_WORDS = re.compile(r"^\s*(профиль|мой профиль)\s*[!.]?\s*$", re.I)


@router.message(GROUP, Command("profile"))
@router.message(GROUP, F.text.regexp(PROFILE_WORDS))
async def profile_cmd(message: Message, chat_row: dict, allowed: bool) -> None:
    """Профиль для всех участников. Ответом на сообщение: профиль того человека."""
    from guard import profile
    from guard.handlers.moderation import real_reply
    if not allowed or not message.from_user or perms.is_anon_admin(message):
        return
    rt = real_reply(message)
    who = rt.from_user if rt and rt.from_user and not rt.from_user.is_bot and not rt.sender_chat else message.from_user
    await profile.send(message.bot, chat_row, who, reply_to=message)


# ---------- бота добавили, повысили, убрали ----------

@router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def my_status(event: ChatMemberUpdated, bot: Bot) -> None:
    chat = event.chat
    perms.forget(chat.id, bot.id)
    old, new = event.old_chat_member.status, event.new_chat_member.status
    was_in = old in ("member", "administrator", "restricted", "creator")
    now_in = new in ("member", "administrator", "restricted")
    if now_in:
        await db.ensure_chat(chat.id, chat.title or "", chat.username or "")
    if now_in and not was_in:
        log.info("Добавили в чат %s %s", chat.id, chat.title)
        text = "подключена. Все автоматические защиты выключены, админы включают их в /settings."
        if new != "administrator":
            text += "\nЧтобы работать, мне нужны права администратора: " + ", ".join(perms.RIGHTS.values()) + "."
        await out.say(bot, chat.id, out.head(text))
    elif new == "administrator" and old != "administrator":
        miss = await perms.missing_rights(bot, chat.id)
        if miss:
            await out.say(bot, chat.id, out.head("права получены, но не хватает: " + ", ".join(miss) + "."))
    elif not now_in and was_in:
        log.info("Убрали из чата %s %s", chat.id, chat.title)
