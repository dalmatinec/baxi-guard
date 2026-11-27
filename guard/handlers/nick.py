"""✨ Мой ник в личке: всё кнопками. Задать, посмотреть как будет, купить за монеты Бакси, убрать."""

import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import baxi, nicks, out
from guard.ui import DIV_SHORT, back, btn, kb, row, screen

log = logging.getLogger(__name__)
router = Router(name="nick")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class NickInput(StatesGroup):
    waiting = State()


def coins_text(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " 🪙"


NOTE = ("Ник со своим шрифтом и премиум-эмодзи видно везде, где тебя упоминают: списки игр, профиль, "
        f"сервисные сообщения, Бакси. До {nicks.MAX_LEN} символов, премиум-эмодзи (до {nicks.MAX_EMOJI}) в начале "
        "или в конце. Ссылки и @ нельзя. Смена ника стоит столько же.")


async def home(user_id: int, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    now = nicks.render(user_id)
    coins = await baxi.wallet(user_id)
    lines = [row("👤", "Сейчас", now or "не задан, видно имя из Telegram"),
             row("💰", "Цена", coins_text(nicks.price())),
             row("👛", "У тебя", coins_text(coins) if coins is not None else "Бакси сейчас не отвечает")]
    if note:
        lines = [note, "", *lines]
    rows = [btn("✏️ Сменить ник" if now else "✏️ Задать ник", "nk:set", accent=True)]
    if now:
        rows.append(btn("🗑 Убрать ник", "nk:del"))
    rows.append(back("g:home:0"))
    return screen("✨ Мой ник", lines, note=NOTE), kb(*rows)


async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


@router.callback_query(F.data.startswith("nk:"))
async def on_nick(cb: CallbackQuery, state: FSMContext) -> None:
    op = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    if op == "home":
        await state.clear()
        await show(cb, *await home(uid))
    elif op == "set":
        await state.set_state(NickInput.waiting)
        await cb.message.answer("✏️ Пришли ник одним сообщением, как он должен выглядеть: жирный, курсив, "
                                "подчёркнутый, зачёркнутый или спойлер, премиум-эмодзи в начале или в конце."
                                f"\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
        await cb.answer()
    elif op == "del":
        await show(cb, screen("🗑 Убрать ник?", [f"Сейчас: {nicks.render(uid) or 'не задан'}"],
                              note="Снова будет видно имя из Telegram. Монеты не вернутся."),
                   kb([btn("🗑 Да, убрать", "nk:delyes").model_copy(update={"style": "danger"}),
                       btn("⬅️ Нет", "nk:home")]))
    elif op == "delyes":
        await nicks.remove(uid)
        await show(cb, *await home(uid, "✅ Ник убран"))
    elif op == "buy":
        await buy(cb, state)
    else:
        await cb.answer()


@router.message(StateFilter(NickInput.waiting), F.text, ~F.text.startswith("/"))
async def on_nick_text(message: Message, state: FSMContext) -> None:
    try:
        nick = nicks.parse(message)
    except nicks.NickError as exc:
        await message.answer(f"⚠️ {exc}\nПришли ещё раз или /cancel")
        return
    uid = message.from_user.id
    await state.update_data(head=nick.head, body=nick.body, tail=nick.tail, plain=nick.plain,
                            ref=f"nick:{uid}:{int(time.time() * 1000)}")
    coins = await baxi.wallet(uid)
    lines = [row("👀", "Так будет", nicks.preview(nick, uid)), row("💰", "Цена", coins_text(nicks.price())),
             row("👛", "У тебя", coins_text(coins) if coins is not None else "Бакси сейчас не отвечает")]
    await message.answer(screen("✨ Новый ник", lines, path="✨ Мой ник"),
                         reply_markup=kb([btn(f"✅ Купить за {coins_text(nicks.price())}", "nk:buy")
                                         .model_copy(update={"style": "success"})],
                                         [btn("✏️ Другой", "nk:set"), btn("✖️ Отмена", "nk:home")]),
                         link_preview_options=out.NO_PREVIEW)


@router.message(StateFilter(NickInput.waiting), ~F.text)
async def on_nick_other(message: Message) -> None:
    await message.answer("Ник нужен текстом. Пришли ещё раз или /cancel")


async def buy(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    uid = cb.from_user.id
    if not data.get("body"):
        await show(cb, *await home(uid))
        return
    if not baxi.configured():
        await cb.answer("Покупка ника работает вместе с Бакси, а связь с ним не настроена.", show_alert=True)
        return
    price = nicks.price()
    result = await baxi.spend(uid, price, data["ref"], "ник в модераторе")
    if result is None:
        await cb.answer("Бакси сейчас не отвечает, монеты не списаны. Попробуй через минуту.", show_alert=True)
        return
    if not result.get("ok"):
        have = int(result.get("coins") or 0)
        await show(cb, screen("✨ Не хватает монет", [row("💰", "Нужно", coins_text(price)),
                                                      row("👛", "У тебя", coins_text(have))],
                              note="Монеты Бакси дают за общение в чатах, ежедневный бонус и игры."),
                   kb(btn("⬅️ Назад", "nk:home")))
        return
    nick = nicks.Nick(data["head"], data["body"], data["tail"], data["plain"])
    await nicks.save(uid, nick, 0 if result.get("repeat") else price)
    await state.clear()
    await show(cb, *await home(uid, "✅ Ник сохранён"))
