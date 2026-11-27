"""🆘 Связь с разработчиком. Человеку: выбрать чат и тему, написать (можно со скриншотом), получить ответ
в личку. Разработчику: обращение с карточкой чата, ответ кнопкой или ответом на сообщение, закрыть,
назначить владельца в боте. Плюс /setowner для владельцев бота."""

import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import db, owners, out, perms, support
from guard.ui import DIV_SHORT, back, btn, chat_title, grid, kb, screen

log = logging.getLogger(__name__)
router = Router(name="support")
P = "🆘 Поддержка"
_last: dict[int, float] = {}


class SupportInput(StatesGroup):
    text = State()    # новое обращение: chat_id, topic
    more = State()    # дополнение от человека: ticket
    reply = State()   # ответ разработчика: ticket


def is_dev(user_id: int) -> bool:
    return perms.is_owner(user_id)


async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


def user_buttons(ticket_id: int) -> InlineKeyboardMarkup:
    return kb([btn("↩️ Ответить", f"sp:more:{ticket_id}"), btn("✅ Вопрос решён", f"sp:cls:{ticket_id}")])


def dev_buttons(ticket: dict) -> InlineKeyboardMarkup:
    rows = [[btn("↩️ Ответить", f"sp:rep:{ticket['id']}", accent=True), btn("✅ Закрыть", f"sp:cls:{ticket['id']}")]]
    if ticket["chat_id"]:
        rows.append(btn("👑 Сделать владельцем в боте", f"sp:own:{ticket['id']}"))
    return kb(*rows)


# ---------- экраны человека ----------

async def home_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    tickets = await support.open_of(user_id)
    lines = ["Напишите разработчику: ошибка, оплата, перенос чата, потерянный владелец, идея. "
             "Бот сам приложит данные о чате, ответ придёт сюда."]
    rows = [btn("✍️ Новое обращение", "sp:new", accent=True)]
    if tickets:
        lines += ["", "<b>Открытые обращения</b>"]
        for t in tickets:
            lines.append(f"#{t['id']} · {support.TOPICS.get(t['topic'], '❓ Другое')} · {owners.date(t['created'])[:10]}")
            rows.append(btn(f"➕ Дополнить #{t['id']}", f"sp:more:{t['id']}"))
    return screen(P, lines), kb(*rows, back("g:home:0"))


async def chats_screen(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard.handlers.move import sources, status_mark
    chats = await sources(bot, user_id)
    buttons = [btn(f"{status_mark(c)} {(c['title'] or str(c['chat_id']))[:34]}", f"sp:t:{c['chat_id']}")
               for c in chats[:30]]
    return (screen(P, ["О каком чате вопрос?"], note="Чата нет в списке (вы в нём не админ): выберите «Не про чат» и "
                                                     "напишите ссылку на чат в тексте."),
            kb(*grid(buttons, 1), btn("💬 Не про чат", "sp:t:0"), back("sp:home")))


def topics_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [btn(name, f"sp:w:{chat_id}:{key}") for key, name in support.TOPICS.items()]
    return screen(P, ["Тема обращения?"]), kb(*grid(buttons, 2), back("sp:new"))


@router.callback_query(F.data.startswith("sp:"))
async def on_button(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:  # noqa: C901
    parts = cb.data.split(":")
    op = parts[1]
    uid = cb.from_user.id
    private = cb.message.chat.type == "private"
    if op == "home" and private:
        await state.clear()
        await show(cb, *await home_screen(uid))
    elif op == "new" and private:
        problem = await support.can_open(uid)
        if problem:
            await cb.answer(problem, show_alert=True)
            return
        await show(cb, *await chats_screen(bot, uid))
    elif op == "t" and private:
        await show(cb, *topics_screen(int(parts[2])))
    elif op == "w" and private:
        chat_id, topic = int(parts[2]), parts[3]
        if chat_id:
            from guard.handlers.move import src_flags
            chat = await db.get_chat(chat_id)
            if chat is None or not await src_flags(bot, chat, uid):
                await cb.answer("Нет доступа к этому чату.", show_alert=True)
                return
        await state.set_state(SupportInput.text)
        await state.update_data(chat_id=chat_id, topic=topic)
        await cb.message.answer(f"✍️ Опишите вопрос одним сообщением: что делали, что ожидали, что случилось. "
                                f"Можно приложить скриншот или файл с подписью.\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
        await cb.answer()
    elif op in ("more", "rep", "cls", "own", "OWN"):
        ticket = await support.get(int(parts[2]))
        if ticket is None:
            await cb.answer()
            return
        if op == "more":
            if ticket["user_id"] != uid or ticket["status"] != "open":
                await cb.answer("Обращение закрыто, создайте новое.", show_alert=True)
                return
            await state.set_state(SupportInput.more)
            await state.update_data(ticket=ticket["id"])
            await cb.message.answer(f"✍️ Дополнение к обращению #{ticket['id']}: пришлите одно сообщение.\n"
                                    f"{DIV_SHORT}\n<i>Отменить: /cancel</i>")
            await cb.answer()
        elif op == "rep":
            if not is_dev(uid):
                await cb.answer()
                return
            await state.set_state(SupportInput.reply)
            await state.update_data(ticket=ticket["id"])
            await cb.message.answer(f"↩️ Ответ на обращение #{ticket['id']}: пришлите одно сообщение, оно уйдёт как "
                                    "есть (текст, фото, файл).\n<i>Отменить: /cancel</i>")
            await cb.answer()
        elif op == "cls":
            if not (is_dev(uid) or ticket["user_id"] == uid):
                await cb.answer()
                return
            if ticket["status"] == "closed":
                await cb.answer("Уже закрыто")
                return
            await support.close(ticket["id"])
            await cb.answer("Закрыто")
            if is_dev(uid) and ticket["user_id"] != uid:
                try:
                    await bot.send_message(ticket["user_id"], f"✅ Обращение #{ticket['id']} закрыто. Если вопрос "
                                                              "остался, создайте новое в «🆘 Поддержка».")
                except Exception:
                    pass
            else:
                for chat_id in support.dev_chats():
                    try:
                        await bot.send_message(chat_id, f"✅ Обращение #{ticket['id']}: автор отметил, что вопрос решён.")
                    except Exception:
                        pass
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        elif op in ("own", "OWN"):
            if not is_dev(uid) or not ticket["chat_id"]:
                await cb.answer()
                return
            chat = await db.get_chat(ticket["chat_id"])
            if op == "own":
                await cb.message.answer(
                    f"👑 Сделать автора обращения #{ticket['id']} (<code>{ticket['user_id']}</code>) владельцем чата "
                    f"{chat_title(chat) if chat else ticket['chat_id']} в боте?",
                    reply_markup=kb([btn("✅ Да", f"sp:OWN:{ticket['id']}", accent=True)]))
                await cb.answer()
                return
            await owners.grant(bot, ticket["chat_id"], ticket["user_id"],
                               f"назначил разработчик по обращению #{ticket['id']}")
            await cb.answer("Готово", show_alert=True)
    else:
        await cb.answer()


# ---------- ввод ----------

def too_fast(user_id: int) -> bool:
    now = time.monotonic()
    if now - _last.get(user_id, 0) < support.FOLLOW_GAP:
        return True
    _last[user_id] = now
    if len(_last) > 5000:
        _last.clear()
    return False


def text_len(message: Message) -> int:
    return len(message.text or message.caption or "")


async def deliver_to_devs(bot: Bot, ticket: dict, message: Message, header: str) -> None:
    for chat_id in support.dev_chats():
        try:
            head = await bot.send_message(chat_id, header, reply_markup=dev_buttons(ticket),
                                          link_preview_options=out.NO_PREVIEW)
            await support.link(chat_id, head.message_id, ticket["id"])
            copy = await bot.copy_message(chat_id, message.chat.id, message.message_id)
            await support.link(chat_id, copy.message_id, ticket["id"])
        except Exception:
            log.exception("Не доставил обращение #%s в %s", ticket["id"], chat_id)


@router.message(StateFilter(SupportInput.text), F.chat.type == "private", ~F.text.startswith("/"))
async def on_new(message: Message, state: FSMContext, bot: Bot) -> None:
    if text_len(message) > support.MAX_TEXT:
        await message.answer(f"Слишком длинно, до {support.MAX_TEXT} символов. Сократите или /cancel")
        return
    if not (message.text or message.caption or message.photo or message.document or message.video):
        await message.answer("Нужен текст, скриншот или файл. Пришлите ещё раз или /cancel")
        return
    problem = await support.can_open(message.from_user.id)
    data = await state.get_data()
    await state.clear()
    if problem:
        await message.answer(f"⚠️ {problem}")
        return
    ticket_id = await support.create(message.from_user.id, int(data.get("chat_id") or 0), data.get("topic") or "other")
    ticket = await support.get(ticket_id)
    await deliver_to_devs(bot, ticket, message, await support.card(bot, ticket, message.from_user))
    await message.answer(f"✅ Обращение #{ticket_id} отправлено разработчику. Ответ придёт сюда.",
                         reply_markup=kb(btn("🆘 Мои обращения", "sp:home")))


@router.message(StateFilter(SupportInput.more), F.chat.type == "private", ~F.text.startswith("/"))
async def on_more(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ticket = await support.get(int(data.get("ticket") or 0))
    await state.clear()
    if ticket is None or ticket["user_id"] != message.from_user.id or ticket["status"] != "open":
        await message.answer("Обращение закрыто, создайте новое в «🆘 Поддержка».")
        return
    if too_fast(message.from_user.id) or text_len(message) > support.MAX_TEXT:
        await message.answer("Не так быстро и не длиннее 3000 символов. Попробуйте ещё раз через полминуты.")
        return
    await support.touch(ticket["id"])
    who = out.mention(message.from_user.id, out.full_name(message.from_user))
    await deliver_to_devs(bot, ticket, message, f"➕ Обращение #{ticket['id']} · дополнение от {who}")
    await message.answer(f"✅ Дополнение к #{ticket['id']} отправлено.")


async def send_reply(bot: Bot, ticket: dict, message: Message) -> bool:
    try:
        await bot.send_message(ticket["user_id"], f"💬 <b>Ответ разработчика</b> · обращение #{ticket['id']}")
        await bot.copy_message(ticket["user_id"], message.chat.id, message.message_id,
                               reply_markup=user_buttons(ticket["id"]))
    except Exception as exc:
        await message.reply(f"⚠️ Не доставил: {out.esc(str(exc))[:200]}")
        return False
    await support.touch(ticket["id"])
    await support.link(message.chat.id, message.message_id, ticket["id"])
    await message.reply(f"✅ Ответ на #{ticket['id']} доставлен.")
    return True


@router.message(StateFilter(SupportInput.reply), ~F.text.startswith("/"))
async def on_reply(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not is_dev(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    ticket = await support.get(int(data.get("ticket") or 0))
    if ticket:
        await send_reply(bot, ticket, message)


async def reply_ticket(message: Message) -> dict | bool:
    """Ответ разработчика на сообщение обращения (в группе поддержки или в личке)."""
    rt = message.reply_to_message
    if not rt or not message.from_user or not is_dev(message.from_user.id):
        return False
    if message.chat.id not in support.dev_chats() and message.chat.type != "private":
        return False
    if (message.text or "").startswith("/"):
        return False
    ticket_id = await support.ticket_by_message(message.chat.id, rt.message_id)
    ticket = await support.get(ticket_id) if ticket_id else None
    return {"ticket": ticket} if ticket else False


@router.message(reply_ticket)
async def on_reply_to(message: Message, bot: Bot, ticket: dict) -> None:
    await send_reply(bot, ticket, message)


# ---------- владельцам бота ----------

@router.message(Command("setowner"), F.chat.type == "private")
async def setowner(message: Message, command: CommandObject, bot: Bot) -> None:
    """/setowner ID_чата ID_человека: владелец в боте вместо пропавшего создателя. 0 вместо человека: снять."""
    if not message.from_user or not is_dev(message.from_user.id):
        return
    args = (command.args or "").split()
    if len(args) != 2 or not all(a.lstrip("-").isdigit() for a in args):
        await message.answer("Формат: <code>/setowner ID_чата ID_человека</code> (0 вместо человека: снять)")
        return
    chat_id, user_id = int(args[0]), int(args[1])
    if await db.get_chat(chat_id) is None:
        await message.answer("Такого чата бот не знает.")
        return
    if user_id:
        await owners.grant(bot, chat_id, user_id, "назначил разработчик")
        await message.answer(f"✅ <code>{user_id}</code> теперь владелец чата <code>{chat_id}</code> в боте.")
    else:
        await owners.set_override(chat_id, 0)
        await message.answer("✅ Владелец в боте снят, права снова у создателя чата.")
