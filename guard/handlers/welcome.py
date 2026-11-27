"""Приветствие и капча в чате: команды админа и кнопка капчи.

+приветствие (или /setwelcome) ответом на сообщение: сохранить его копией (не пересылкой) как приветствие:
    текст с премиум-эмодзи и форматированием, картинка, видео или гифка. Кнопки: строки в конце сообщения
    «Текст - ссылка» (в ряд через |) бот уберёт из текста и сделает кнопками. Можно и без ответа:
    «+приветствие Привет, {user}!». Сохранение включает приветствие.
-приветствие (/welcomeoff): выключить.  приветствие (/welcome): показать, как выглядит.
капча кнопка|пример|эмодзи|выкл (/captcha button|math|emoji|off): капча для новичков.
Права: ⚙️ настройки (админ чата и владелец). То же самое кнопками в личке: ⚙️ чат → 👋 Приветствие."""

import html as html_lib
import logging
import re

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from guard import db, out, perms, richtext, texts, welcome
from guard.handlers.moderation import settings_gate
from guard.handlers.util import done

log = logging.getLogger(__name__)
router = Router(name="welcome")
GROUP = F.chat.type.in_({"group", "supergroup"})

SET = re.compile(r"^\s*(\+приветствие|/setwelcome(?:@\w+)?)(?:\s+|$)", re.I)
OFF = re.compile(r"^\s*(-приветствие|/welcomeoff(?:@\w+)?)\s*$", re.I)
SHOW = re.compile(r"^\s*(приветствие|/welcome(?:@\w+)?)\s*$", re.I)
CAPTCHA = re.compile(r"^\s*(капча|/captcha(?:@\w+)?)(?:\s+(\S+))?\s*$", re.I)
CAPTCHA_WORDS = {"кнопка": "button", "button": "button", "пример": "math", "math": "math", "эмодзи": "emoji",
                 "emoji": "emoji", "выкл": "", "off": "", "нет": ""}


def split_buttons(html: str, owner: bool) -> tuple[str, str]:
    """Строки-кнопки в конце сообщения («Текст - ссылка») отдельно от текста. (текст, кнопки)."""
    lines = html.split("\n")
    found: list[str] = []
    while lines:
        plain = html_lib.unescape(re.sub(r"<[^>]+>", "", richtext.to_web(lines[-1]))).strip()
        if not plain:
            if found:
                lines.pop()
                continue
            break
        rows, bad = texts.parse_buttons(plain, design=owner)
        if not rows or bad:
            break
        found.insert(0, texts.buttons_text(rows))
        lines.pop()
    return "\n".join(lines).strip(), "\n".join(found)


@router.message(GROUP, F.text.regexp(SET) | F.caption.regexp(SET))
async def set_welcome(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await settings_gate(message, chat_row, allowed, perms.R_SETTINGS):
        return
    owner = perms.is_owner(message.from_user.id)
    src = message.reply_to_message
    if src is not None and not src.forum_topic_created:
        html, media = (src.html_text or ""), texts.media_value(src)
    else:   # текст прямо после команды
        html = SET.sub("", message.html_text or "", count=1)
        media = texts.media_value(message)
    body, buttons = split_buttons(html, owner)
    if not body and not media:
        await done(message, chat_row, "ответьте «+приветствие» на сообщение, которое станет приветствием, или "
                                      "напишите текст сразу после команды. Подстановки: {user}, {name}, {chat}, {count}.")
        return
    fields = {"html": body[:texts.MAX_HTML], "photo": media}
    if buttons:
        fields["buttons"] = buttons
    await texts.set_text(chat_row["chat_id"], "welcome", by=message.from_user.id, **fields)
    await db.set_chat(chat_row["chat_id"], welcome_on=1)
    chat = await db.get_chat(chat_row["chat_id"])
    await welcome.sync_baxi(chat)
    await done(message, chat, "👋 приветствие сохранено и включено. Вот как его увидит новичок:")
    await preview(message, chat)


async def preview(message: Message, chat: dict) -> None:
    u = message.from_user
    await texts.send(message.bot, chat, "welcome", user=out.mention(u.id, out.full_name(u)),
                     name=out.esc(out.full_name(u)), username=f"@{u.username}" if u.username else out.esc(u.first_name),
                     chat=out.esc(chat.get("title") or ""), count="47")


@router.message(GROUP, F.text.regexp(OFF))
async def welcome_off(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await settings_gate(message, chat_row, allowed, perms.R_SETTINGS):
        return
    await db.set_chat(chat_row["chat_id"], welcome_on=0)
    chat = await db.get_chat(chat_row["chat_id"])
    await welcome.sync_baxi(chat)
    await done(message, chat, "👋 приветствие выключено. Текст сохранён, включить: «+приветствие» или в личке.")


@router.message(GROUP, F.text.regexp(SHOW))
async def welcome_show(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await settings_gate(message, chat_row, allowed, perms.R_SETTINGS):
        return
    state = "включено" if chat_row["welcome_on"] else "выключено"
    await done(message, chat_row, f"👋 приветствие {state}, капча: {welcome.MODES.get(chat_row['captcha'], 'выкл')}. "
                                  "Так оно выглядит:")
    await preview(message, chat_row)


@router.message(GROUP, F.text.regexp(CAPTCHA))
async def captcha_cmd(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await settings_gate(message, chat_row, allowed, perms.R_SETTINGS):
        return
    arg = (CAPTCHA.match(message.text).group(2) or "").lower()
    if arg not in CAPTCHA_WORDS:
        await done(message, chat_row, f"капча сейчас: {welcome.MODES.get(chat_row['captcha'], 'выкл')}. "
                                      "Включить: «капча кнопка», «капча пример» или «капча эмодзи», выключить: «капча выкл». "
                                      "Время и что делать с не прошедшими: в личке, ⚙️ чат → 👋 Приветствие.")
        return
    mode = CAPTCHA_WORDS[arg]
    if mode:
        problem = await perms.rights_problem(message.bot, message.chat.id, "can_restrict_members", "can_delete_messages")
        if problem:
            await done(message, chat_row, problem)
            return
    await db.set_chat(chat_row["chat_id"], captcha=mode)
    chat = await db.get_chat(chat_row["chat_id"])
    await welcome.sync_baxi(chat)
    if mode:
        fail = welcome.FAILS.get(chat["captcha_fail"], welcome.FAILS["kick"])[0]
        await done(message, chat, f"🛡 капча включена: {welcome.MODES[mode]}. На ответ {chat['captcha_min']} мин, "
                                  f"не прошёл: {fail}.")
    else:
        await done(message, chat, "🛡 капча выключена.")


@router.callback_query(F.data.startswith("cp:"))
async def captcha_press(cb: CallbackQuery, bot: Bot) -> None:
    try:
        _, uid, value = cb.data.split(":", 2)
        uid = int(uid)
    except ValueError:
        await cb.answer()
        return
    if cb.message is None:
        await cb.answer()
        return
    if cb.from_user.id != uid:
        await cb.answer("Это проверка для новичка, не для тебя 🙂", show_alert=True)
        return
    result = await welcome.answer(bot, cb.message.chat.id, cb.from_user, value)
    texts_ = {"pass": "✅ Готово, добро пожаловать!", "fail": "❌ Неверно", "none": "Проверка уже закончилась"}
    await cb.answer(texts_[result], show_alert=result == "fail")
