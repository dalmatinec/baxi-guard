"""/v, /chat и что делать, когда бота добавили в чат или убрали."""

import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

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
    from guard import owners
    if now_in:
        await db.ensure_chat(chat.id, chat.title or "", chat.username or "")
        await owners.mark_back(chat.id)
        out.spawn(owners.refresh(bot, chat.id))   # запомнить создателя
        from guard import commands
        out.spawn(commands.refresh_chat(bot, chat.id))  # меню команд создателю и ролям
    if now_in and not was_in:
        log.info("Добавили в чат %s %s", chat.id, chat.title)
        from guard import chatinfo
        row = await db.ensure_chat(chat.id, chat.title or "", chat.username or "")
        try:
            await chatinfo.send(bot, row)   # карточка: ID, доступ, версия, подписка
        except Exception:
            log.exception("Не отправил информацию о чате %s", chat.id)
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
        await owners.mark_gone(chat.id)   # настройки остаются: их можно перенести в новый чат


# ---------- активность чата ----------

ACTIVITY_WORDS = re.compile(r"^\s*(стата|статистика|актив|активность)\s*[!.]?\s*$", re.I)
ACTIVITY_COOLDOWN = 10       # участники без роли: не чаще раза в 10 секунд на чат
_activity_last: dict[int, float] = {}
_activity_edit: dict[tuple[int, int], float] = {}


@router.message(GROUP, Command("stat"))
@router.message(GROUP, F.text.regexp(ACTIVITY_WORDS))
async def activity_cmd(message: Message, chat_row: dict, allowed: bool) -> None:
    """Активность чата для всех: сообщений, сколько писали, топ-10. Период кнопками."""
    from guard import activity, texts
    if not allowed or not message.from_user:
        return
    now = time.monotonic()
    staff = await perms.has_any(message.bot, message.chat.id, message.from_user.id)
    if not staff and now - _activity_last.get(message.chat.id, 0) < ACTIVITY_COOLDOWN:
        return
    _activity_last[message.chat.id] = now
    _text, values = await activity.card(chat_row, "day")
    await texts.send(message.bot, chat_row, "activity", reply_to=message, ttl=0, extra=activity.markup("day"),
                     **values)


@router.callback_query(F.data.startswith("ac:"))
async def activity_period(cb: CallbackQuery) -> None:
    from guard import activity, texts
    period = cb.data.split(":", 1)[1]
    msg = cb.message
    if period not in activity.PERIODS or msg is None or msg.chat.type not in ("group", "supergroup"):
        await cb.answer()
        return
    key = (msg.chat.id, msg.message_id)
    now = time.monotonic()
    if now - _activity_edit.get(key, 0) < 1.5:   # не даём долбить кнопки
        await cb.answer("Секунду…")
        return
    _activity_edit[key] = now
    if len(_activity_edit) > 500:
        _activity_edit.clear()
    chat = await db.ensure_chat(msg.chat.id)
    html, values = await activity.card(chat, period)
    markup = texts.markup(await texts.template(chat["chat_id"], "activity"), values, activity.markup(period))
    try:
        if msg.photo:
            await msg.edit_caption(caption=html[:texts.MAX_CAPTION], reply_markup=markup)
        else:
            await msg.edit_text(html, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            log.warning("Не обновил активность: %s", exc)
    await cb.answer()


# ---------- сброс чужого ника (только владелец бота) ----------

@router.message(GROUP, F.reply_to_message, F.text.regexp(r"^\s*(-ник|/resetnick(@\w+)?)\s*$"))
async def reset_nick(message: Message, chat_row: dict) -> None:
    """Ник оскорбительный: владелец бота убирает его ответом. Монеты не возвращаются."""
    from guard import nicks
    if not message.from_user or not perms.is_owner(message.from_user.id):
        return
    target = message.reply_to_message.from_user
    if target is None:
        return
    removed = await nicks.remove(target.id)
    await done(message, chat_row, f"ник {out.esc(out.full_name(target))} убран." if removed else "у него нет своего ника.")



# ---------- информация о чате ----------

INFO_WORDS = re.compile(r"^\s*(инфо|информация|/info(@\w+)?)\s*$", re.I)
_info_last: dict[int, float] = {}


@router.message(GROUP, F.text.regexp(INFO_WORDS))
async def info_cmd(message: Message, chat_row: dict) -> None:
    """Карточка чата для всех; участникам без роли не чаще раза в минуту на чат."""
    from guard import chatinfo
    if not message.from_user:
        return
    now = time.monotonic()
    staff = await perms.has_any(message.bot, message.chat.id, message.from_user.id)
    if not staff and now - _info_last.get(message.chat.id, 0) < 60:
        return
    _info_last[message.chat.id] = now
    await chatinfo.send(message.bot, chat_row, reply_to=message)
