"""Личка: /start, мои чаты и настройки каждого чата по категориям.

Карточка чата → категории: 🛡 Защита (антиспам, стоп-слова, режим атаки), 🧹 Чистота, ⚠️ Наказания,
👥 Доверие, 📜 Журнал, ℹ️ О чате. Строгий текстовый стиль из guard/ui.py.

Кнопки: g:<экран>:<chat_id>[:<действие>[:<значение>]]. Права проверяются на каждое нажатие."""

import asyncio
import logging
import re
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import access, config, db, out, perms
from guard.links import normalize_domain
from guard.parse import human_duration, human_for
from guard.ui import DIV, back, btn, chat_title, check, cycle, grid, kb, row, screen
from guard.ui import state as word

log = logging.getLogger(__name__)
router = Router(name="settings")
router.message.filter(F.chat.type == "private")

STOP_ACTIONS = {"delete": "удалить", "delete_warn": "удалить и предупредить", "delete_mute": "удалить и мут на час"}
SPAM_ACTIONS = {"delete": "удалить", "delete_warn": "удалить и предупредить",
                "delete_mute": "удалить и мут на час", "ban": "удалить и бан"}
WARN_MODES = [("mute", 60), ("mute", 1440), ("mute", 10080), ("ban", 0)]
WARN_TTLS = [0, 7, 14, 30, 60, 90]
ATTACK_MINUTES = [15, 30, 60, 120]
REPLY_TTLS = [0, 15, 30, 60, 300]
SERVICE = [("svc_join", "входы"), ("svc_leave", "выходы"), ("svc_pin", "закрепы"),
           ("svc_photo", "фото чата"), ("svc_title", "название чата")]
SPAM_PARTS = [("spam_links", "ссылки"), ("spam_forwards", "пересылки"), ("spam_buttons", "кнопки"),
              ("spam_scam", "скам"), ("spam_newbie", "новички")]
MAX_STOPWORDS = 500
MAX_DOMAINS = 200

P_PROTECT, P_CHATS = "🛡 Защита", "⚙️ Мои чаты"


class Input(StatesGroup):
    waiting = State()  # в data: kind, chat_id


# ---------- главное меню ----------

def start_text() -> str:
    return screen(out.HEAD, [
        "Порядок в чатах без лишнего шума.",
        "",
        "🔗 антиспам и стоп-слова",
        "🚨 защита от набегов",
        "⚠️ предупреждения, муты, баны",
        "🧹 чистка служебных сообщений",
        "📜 журнал всех действий",
    ], note="Добавьте бота в чат администратором и включите нужное в настройках. По умолчанию всё "
            "автоматическое выключено.")


async def add_url(bot: Bot) -> str:
    me = await bot.me()
    return (f"https://t.me/{me.username}?startgroup=guard"
            "&admin=delete_messages+restrict_members+pin_messages+invite_users")


async def start_markup(bot: Bot, user_id: int) -> InlineKeyboardMarkup:
    rows = [btn("➕ Добавить в чат", url=await add_url(bot), accent=True),
            [btn("⚙️ Мои чаты", "g:chats:0"), btn("📖 Команды", "g:help:0")]]
    if perms.is_owner(user_id):
        rows.append(btn("🛠 Админка", "a:home:0"))
    return kb(*rows)


HELP = screen("📖 Команды в чате", [
    "Для админов с правом ограничивать участников и модераторов бота.",
    "Кого: ответом, @username или ID. Время: 30m, 2h, 3d, 1w.",
    "",
    "<b>Наказания</b>",
    "/mute [время] [причина] · мут, по умолчанию час",
    "/warn [причина] · предупреждение",
    "/ban [время] [причина] · бан, по умолчанию навсегда",
    "/kick · выгнать без бана",
    "/unmute · /unwarn · /unban · снять",
    "/warns · предупреждения человека или свои",
    "",
    "<b>Чат</b>",
    "/del · удалить сообщение",
    "/pin · /unpin · закрепить, открепить",
    "/link · ссылка-приглашение",
    "/attack · режим атаки",
    "",
    "<b>Управление</b>",
    "/whitelist · /mod · белый список и модераторы (создатель)",
    "/settings · настройки · /chat · права бота · /v · пинг",
], note="Команда и ответ бота сами исчезают через несколько секунд, это меняется в разделе Чистота.")


# ---------- вход ----------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    arg = command.args or ""
    if arg.startswith("s_"):
        try:
            chat_id = int(arg[2:])
        except ValueError:
            chat_id = 0
        if chat_id and await perms.can_configure(message.bot, chat_id, message.from_user.id):
            await db.ensure_chat(chat_id)
            text, markup = await card(message.bot, chat_id, message.from_user.id)
            await message.answer(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
            return
        await message.answer(out.head("настраивать этот чат могут только его админы с правом ограничивать "
                                      "участников."))
        return
    await message.answer(start_text(), reply_markup=await start_markup(message.bot, message.from_user.id))


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await chats_screen(message.bot, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(out.head("отменено."))


# ---------- сводки ----------

def attack_on(chat: dict) -> bool:
    return chat["attack_until"] > db.now()


def protect_count(chat: dict) -> int:
    return sum(1 for v in (chat["spam_on"], chat["stop_on"], attack_on(chat) or chat["attack_auto"]) if v)


def clean_count(chat: dict) -> tuple[int, int]:
    on = sum(1 for k, _ in SERVICE if chat[k]) + (1 if chat["cmd_delete"] else 0) + (1 if chat["reply_ttl"] else 0)
    return on, len(SERVICE) + 2


def warn_mode_label(chat: dict) -> str:
    if chat["warn_action"] == "ban":
        return "бан"
    return f"мут {human_for(chat['warn_mute_min'] * 60)}"


def warn_summary(chat: dict) -> str:
    return f"{chat['warn_limit']} → {warn_mode_label(chat)}"


async def rights_line(bot: Bot, chat_id: int) -> str:
    miss = await perms.missing_rights(bot, chat_id)
    if miss is None:
        return "⚠️ бот не администратор в чате"
    if miss:
        return "⚠️ не хватает прав: " + ", ".join(miss)
    return "✅ права бота в порядке"


# ---------- экраны ----------

async def my_chats(bot: Bot, user_id: int) -> list[dict]:
    chats = await db.all_chats()
    if perms.is_owner(user_id):
        return chats
    sem = asyncio.Semaphore(10)

    async def check_one(c):
        async with sem:
            return c if await perms.can_configure(bot, c["chat_id"], user_id) else None
    return [c for c in await asyncio.gather(*(check_one(c) for c in chats[:200])) if c]


async def chats_screen(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chats = await my_chats(bot, user_id)
    if not chats:
        return (screen(P_CHATS, ["Пока пусто."], note="Добавьте бота в чат администратором, и чат появится здесь."),
                kb(btn("➕ Добавить в чат", url=await add_url(bot), accent=True), back("g:home:0")))
    lines, buttons = [], []
    for c in chats[:50]:
        mark = "🟢" if protect_count(c) else "⚪"
        lines.append(f"{mark} {chat_title(c)} · защита {protect_count(c)}/3")
        buttons.append(btn(f"{mark} {(c['title'] or str(c['chat_id']))[:30]}", f"g:c:{c['chat_id']}"))
    return (screen(P_CHATS, lines, note="🟢 защита включена · ⚪ всё выключено"),
            kb(*grid(buttons, 1), back("g:home:0")))


async def card(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.ensure_chat(chat_id)
    ok, mode = await access.status(chat)
    wl, mods = len(await db.whitelist(chat_id)), len(await db.mods(chat_id))
    on, total = clean_count(chat)
    lines = [
        row("🛡", "Защита", f"{protect_count(chat)} из 3"),
        row("🧹", "Чистота", f"{on} из {total}"),
        row("⚠️", "Наказания", warn_summary(chat)),
        row("👥", "Доверие", f"белый список {wl}", f"модераторы {mods}"),
        row("📜", "Журнал", "подключён" if chat["log_chat_id"] else "только в базе"),
    ]
    footer = [await rights_line(bot, chat_id)]
    if not ok:
        footer.append(f"⏸ {out.esc(mode)}")
    text = screen(f"⚙️ {chat_title(chat)}", lines, footer=footer)
    rows = [[btn("🛡 Защита", f"g:pr:{chat_id}", accent=True), btn("🧹 Чистота", f"g:cl:{chat_id}")],
            [btn("⚠️ Наказания", f"g:wr:{chat_id}"), btn("👥 Доверие", f"g:wl:{chat_id}")],
            [btn("📜 Журнал", f"g:lg:{chat_id}"), btn("ℹ️ О чате", f"g:in:{chat_id}")],
            back("g:chats:0", "⬅️ Мои чаты")]
    return text, kb(*rows)


async def protect_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    words = len(await db.stopwords(chat_id))
    attack = "идёт" if attack_on(chat) else word(False)
    lines = [
        row("🔗", "Антиспам", word(chat["spam_on"]), SPAM_ACTIONS[chat["spam_action"]] if chat["spam_on"] else ""),
        row("🚫", "Стоп-слова", word(chat["stop_on"]), f"слов {words}"),
        row("🚨", "Режим атаки", attack, "авто" if chat["attack_auto"] else ""),
    ]
    text = screen(P_PROTECT, lines, path=f"⚙️ {chat_title(chat)}",
                  note="Админов, модераторов бота, белый список и доверенных у Бакси автоматика не трогает.")
    return text, kb([btn("🔗 Антиспам", f"g:sp:{chat_id}"), btn("🚫 Стоп-слова", f"g:sw:{chat_id}")],
                    btn("🚨 Режим атаки", f"g:at:{chat_id}"), back(f"g:c:{chat_id}"))


async def spam_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    doms = len(await db.domains(chat_id))
    caught = [name for k, name in SPAM_PARTS if chat[k]]
    lines = [row("⚡", "Состояние", word(chat["spam_on"])),
             row("🎯", "Действие", SPAM_ACTIONS[chat["spam_action"]]),
             row("🔍", "Ловит", ", ".join(caught) or "ничего")]
    lines.append(row("🌐", "Разрешённые домены", str(doms)))
    if config.AI_API_KEY:
        lines.append(row("🧠", "Нейросеть для спорных", word(chat["spam_ai"])))
    text = screen("🔗 Антиспам", lines, path=P_PROTECT,
                  note="Ссылки, приглашения в чужие чаты, @каналы и боты, пересылки, кнопки-ссылки, скам: "
                       "заработок, 'пиши в лс', кошельки, карты. Новичкам первые сутки ссылки нельзя.")
    toggles = [btn(check(chat[k], name), f"g:sp:{chat_id}:t:{k}") for k, name in SPAM_PARTS]
    if config.AI_API_KEY:
        toggles.append(btn(check(chat["spam_ai"], "нейросеть"), f"g:sp:{chat_id}:t:spam_ai"))
    return text, kb(btn("Выключить" if chat["spam_on"] else "Включить", f"g:sp:{chat_id}:on", accent=True),
                    btn(f"🎯 {SPAM_ACTIONS[chat['spam_action']]}", f"g:sp:{chat_id}:act"),
                    *grid(toggles, 2), btn("🌐 Разрешённые домены", f"g:dm:{chat_id}"), back(f"g:pr:{chat_id}"))


async def domains_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    doms = await db.domains(chat_id)
    lines = [f"• {out.esc(d)}" for d in doms[:60]] or ["пусто"]
    if len(doms) > 60:
        lines.append(f"и ещё {len(doms) - 60}")
    text = screen("🌐 Домены", lines, path="🔗 Антиспам",
                  note="Ссылки на эти сайты и их поддомены антиспам пропускает. Новичкам ссылки нельзя всё равно.")
    return text, kb([btn("➕ Добавить", f"g:dm:{chat_id}:add", accent=True), btn("➖ Убрать", f"g:dm:{chat_id}:del")],
                    back(f"g:sp:{chat_id}"))


async def stop_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    words = await db.stopwords(chat_id)
    shown = ", ".join(out.esc(w) for w in words[:80]) or "пусто"
    if len(words) > 80:
        shown += f" и ещё {len(words) - 80}"
    lines = [row("⚡", "Состояние", word(chat["stop_on"])),
             row("🎯", "Действие", STOP_ACTIONS[chat["stop_action"]]),
             row("📝", "Слов", str(len(words))),
             "", f"<tg-spoiler>{shown}</tg-spoiler>"]
    text = screen("🚫 Стоп-слова", lines, path=P_PROTECT,
                  note="Ловлю с окончаниями и обходы: регистр, ё/е, латинские буквы вместо русских, "
                       "повторы и точки между буквами. Проверяю и правки сообщений.")
    return text, kb(btn("Выключить" if chat["stop_on"] else "Включить", f"g:sw:{chat_id}:on", accent=True),
                    btn(f"🎯 {STOP_ACTIONS[chat['stop_action']]}", f"g:sw:{chat_id}:act"),
                    [btn("➕ Добавить", f"g:sw:{chat_id}:add"), btn("➖ Убрать", f"g:sw:{chat_id}:del")],
                    btn("🗑 Очистить список", f"g:sw:{chat_id}:clr"), back(f"g:pr:{chat_id}"))


async def attack_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    on = attack_on(chat)
    now_line = "идёт, выключится через " + human_duration(max(60, (chat["attack_until"] - db.now()) // 60 * 60)) \
        if on else "выключен"
    lines = [row("⚡", "Сейчас", now_line),
             row("🤖", "Автовключение", word(chat["attack_auto"]), "больше 10 входов в минуту"),
             row("⏱", "Длительность", f"{chat['attack_minutes']} мин")]
    text = screen("🚨 Режим атаки", lines, path=P_PROTECT,
                  note="Пока режим идёт, каждого нового участника сразу выгоняю без бана. В чате: /attack.")
    return text, kb(btn("Выключить сейчас" if on else "Включить сейчас", f"g:at:{chat_id}:on", accent=True),
                    [btn(check(chat["attack_auto"], "авто"), f"g:at:{chat_id}:auto"),
                     btn(f"⏱ {chat['attack_minutes']} мин", f"g:at:{chat_id}:len")],
                    back(f"g:pr:{chat_id}"))


async def clean_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    svc = [name for k, name in SERVICE if chat[k]]
    ttl = f"через {chat['reply_ttl']} сек" if chat["reply_ttl"] else "не удаляю"
    lines = [row("🧹", "Служебные", ", ".join(svc) if svc else "не трогаю"),
             row("⌨️", "Команды админов", "через 5 сек" if chat["cmd_delete"] else "не удаляю"),
             row("💬", "Ответы бота", ttl)]
    text = screen("🧹 Чистота", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Отмеченные служебные сообщения Telegram удаляю сразу.")
    toggles = [btn(check(chat[k], name), f"g:cl:{chat_id}:t:{k}") for k, name in SERVICE]
    return text, kb(*grid(toggles, 2),
                    [btn(check(chat["cmd_delete"], "команды"), f"g:cl:{chat_id}:cmd"),
                     btn(f"💬 {ttl}", f"g:cl:{chat_id}:ttl")],
                    back(f"g:c:{chat_id}"))


async def warns_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    ttl = f"через {chat['warn_ttl_days']} дн." if chat["warn_ttl_days"] else "никогда"
    lines = [row("🔢", "Лимит", f"{chat['warn_limit']} предупреждения" if chat["warn_limit"] < 5
                 else f"{chat['warn_limit']} предупреждений"),
             row("🎯", "На лимите", warn_mode_label(chat)),
             row("⏳", "Сгорают", ttl)]
    text = screen("⚠️ Наказания", lines, path=f"⚙️ {chat_title(chat)}",
                  note="На лимите наказание срабатывает само, счёт начинается заново. Предупреждения выдают "
                       "админы (/warn) и автоматика, если так настроено в защите.")
    return text, kb([btn("➖", f"g:wr:{chat_id}:lim:-1"), btn(f"🔢 {chat['warn_limit']}", f"g:wr:{chat_id}"),
                     btn("➕", f"g:wr:{chat_id}:lim:1")],
                    btn(f"🎯 {warn_mode_label(chat)}", f"g:wr:{chat_id}:mode", accent=True),
                    btn(f"⏳ сгорают: {ttl}", f"g:wr:{chat_id}:ttl"),
                    back(f"g:c:{chat_id}"))


def person(r: dict) -> str:
    return r["name"] or (("@" + r["username"]) if r["username"] else str(r["user_id"]))


async def trust_screen(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    wl, mods = await db.whitelist(chat_id), await db.mods(chat_id)
    creator = await perms.is_creator(bot, chat_id, user_id)
    lines = [f"<b>Белый список</b> · {len(wl)}"]
    lines += [f"• {out.esc(person(r))}" for r in wl[:30]] or ["пусто"]
    lines += ["", f"<b>Модераторы бота</b> · {len(mods)}"]
    lines += [f"• {out.esc(person(r))}" for r in mods[:20]] or ["нет"]
    text = screen("👥 Доверие", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Белому списку можно ссылки и пересылки. Модераторы бота пользуются командами без прав "
                       "в Telegram. Добавляет создатель чата в чате: ответом /whitelist или /mod.")
    rows = []
    if creator:
        rows += grid([btn(f"❌ {person(r)[:22]}", f"g:wl:{chat_id}:rw:{r['user_id']}") for r in wl[:20]], 2)
        rows += grid([btn(f"❌ мод {person(r)[:20]}", f"g:wl:{chat_id}:rm:{r['user_id']}") for r in mods[:10]], 2)
    return text, kb(*rows, btn("🌐 Разрешённые домены", f"g:dm:{chat_id}"), back(f"g:c:{chat_id}"))


KIND_NAMES = {"mute": "🔇 мут", "unmute": "🔊 снят мут", "warn": "⚠️ предупреждение", "unwarn": "↩️ снято предупр.",
              "ban": "⛔ бан", "unban": "✅ разбан", "kick": "👢 кик", "delete": "🗑 удалено", "spam": "🧹 спам"}


async def log_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    items = await db.recent_actions(chat_id, 10)
    where = f"<code>{chat['log_chat_id']}</code>" if chat["log_chat_id"] else "только в базе"
    lines = [row("📍", "Куда дублировать", where), "", "<b>Последние действия</b>"]
    if not items:
        lines.append("пока нет")
    for a in items:
        when = datetime.fromtimestamp(a["ts"], config.TZ).strftime("%d.%m %H:%M")
        name = await db.member_name(chat_id, a["user_id"]) or str(a["user_id"])
        line = f"{when} {KIND_NAMES.get(a['kind'], a['kind'])} · {out.esc(name)}"
        if a["reason"]:
            line += f" · {out.esc(a['reason'][:30])}"
        lines.append(line)
    text = screen("📜 Журнал", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Всё хранится 90 дней. В чат или канал журнала идёт строка на каждое действие.")
    rows = [btn("📍 Указать чат или канал", f"g:lg:{chat_id}:set", accent=True)]
    if chat["log_chat_id"]:
        rows.append(btn("Отключить дубль", f"g:lg:{chat_id}:off"))
    return text, kb(*rows, back(f"g:c:{chat_id}"))


async def info_screen(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    ok, mode = await access.status(chat)
    miss = await perms.missing_rights(bot, chat_id)
    lines = [row("🆔", "ID", f"<code>{chat_id}</code>"),
             row("⚡", "Режим", out.esc(mode) + ("" if ok else ", модерация не активна")),
             "", "<b>Права бота</b>"]
    if miss is None:
        lines.append("⚠️ бот не администратор")
    else:
        lines += [f"{'❌' if name in miss else '✅'} {name}" for name in perms.RIGHTS.values()]
    text = screen("ℹ️ О чате", lines, path=f"⚙️ {chat_title(chat)}")
    rows = []
    if perms.is_owner(user_id):
        rows.append(btn("🔑 Своя подписка", f"a:sub:{chat_id}"))
    return text, kb(*rows, back(f"g:c:{chat_id}"))


# ---------- нажатия ----------

async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


async def ask(cb: CallbackQuery, fsm: FSMContext, kind: str, chat_id: int, prompt: str) -> None:
    await fsm.set_state(Input.waiting)
    await fsm.update_data(kind=kind, chat_id=chat_id)
    await cb.message.answer(f"✏️ {prompt}\n{DIV}\n<i>Отменить: /cancel</i>")
    await cb.answer()


def flip(chat: dict, key: str) -> dict:
    return {key: 0 if chat[key] else 1}


@router.callback_query(F.data.startswith("g:"))
async def on_button(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:  # noqa: C901
    fsm = state
    parts = cb.data.split(":")
    scr = parts[1]
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    op = parts[3] if len(parts) > 3 else ""
    arg = parts[4] if len(parts) > 4 else ""
    user_id = cb.from_user.id

    if scr == "home":
        await fsm.clear()
        await show(cb, start_text(), await start_markup(bot, user_id))
        return
    if scr == "help":
        await show(cb, HELP, kb(back("g:home:0")))
        return
    if scr == "chats":
        await fsm.clear()
        await show(cb, *await chats_screen(bot, user_id))
        return

    if not await perms.can_configure(bot, chat_id, user_id):
        await cb.answer("Настраивать могут только админы этого чата с правом ограничивать участников.",
                        show_alert=True)
        return
    chat = await db.ensure_chat(chat_id)

    if scr == "c":
        await show(cb, *await card(bot, chat_id, user_id))
    elif scr == "pr":
        await show(cb, *await protect_screen(chat_id))

    elif scr == "sw":
        if op == "on":
            await db.set_chat(chat_id, **flip(chat, "stop_on"))
        elif op == "act":
            await db.set_chat(chat_id, stop_action=cycle(list(STOP_ACTIONS), chat["stop_action"]))
        elif op == "add":
            await ask(cb, fsm, "stop_add", chat_id, "Пришлите слова или фразы через запятую или с новой строки.")
            return
        elif op == "del":
            await ask(cb, fsm, "stop_del", chat_id, "Пришлите слова, которые убрать, через запятую.")
            return
        elif op == "clr":
            if arg != "yes":
                await show(cb, screen("🗑 Очистить стоп-слова?", ["Весь список удалится."], path=P_PROTECT),
                           kb([btn("Да, очистить", f"g:sw:{chat_id}:clr:yes"), btn("Нет", f"g:sw:{chat_id}")]))
                return
            await db.clear_stopwords(chat_id)
        await show(cb, *await stop_screen(chat_id))

    elif scr == "sp":
        if op == "on":
            await db.set_chat(chat_id, **flip(chat, "spam_on"))
        elif op == "act":
            await db.set_chat(chat_id, spam_action=cycle(list(SPAM_ACTIONS), chat["spam_action"]))
        elif op == "t" and arg in {k for k, _ in SPAM_PARTS} | {"spam_ai"}:
            await db.set_chat(chat_id, **flip(chat, arg))
        await show(cb, *await spam_screen(chat_id))

    elif scr == "dm":
        if op == "add":
            await ask(cb, fsm, "dom_add", chat_id, "Пришлите домены через запятую или с новой строки, "
                                                   "например: youtube.com, example.org")
            return
        if op == "del":
            await ask(cb, fsm, "dom_del", chat_id, "Пришлите домены, которые убрать.")
            return
        await show(cb, *await domains_screen(chat_id))

    elif scr in ("cl", "sv", "rp"):  # sv и rp: старые кнопки, теперь всё в Чистоте
        if op == "t" and arg in {k for k, _ in SERVICE}:
            await db.set_chat(chat_id, **flip(chat, arg))
        elif op == "cmd":
            await db.set_chat(chat_id, **flip(chat, "cmd_delete"))
        elif op == "ttl":
            await db.set_chat(chat_id, reply_ttl=cycle(REPLY_TTLS, chat["reply_ttl"]))
        await show(cb, *await clean_screen(chat_id))

    elif scr == "at":
        if op == "on":
            from guard import protect
            if attack_on(chat):
                await protect.attack_off(bot, chat_id, by_id=user_id)
            else:
                await protect.attack_on(bot, chat_id, by_id=user_id)
        elif op == "auto":
            await db.set_chat(chat_id, **flip(chat, "attack_auto"))
        elif op == "len":
            await db.set_chat(chat_id, attack_minutes=cycle(ATTACK_MINUTES, chat["attack_minutes"]))
        await show(cb, *await attack_screen(chat_id))

    elif scr == "wr":
        if op == "lim":
            step = 1 if arg == "1" else -1
            await db.set_chat(chat_id, warn_limit=min(10, max(1, chat["warn_limit"] + step)))
        elif op == "mode":
            current = (chat["warn_action"], chat["warn_mute_min"] if chat["warn_action"] == "mute" else 0)
            action, minutes = cycle(WARN_MODES, current)
            await db.set_chat(chat_id, warn_action=action, warn_mute_min=minutes or chat["warn_mute_min"])
        elif op == "ttl":
            await db.set_chat(chat_id, warn_ttl_days=cycle(WARN_TTLS, chat["warn_ttl_days"]))
        await show(cb, *await warns_screen(chat_id))

    elif scr == "wl":
        if op in ("rw", "rm") and arg.lstrip("-").isdigit():
            if not await perms.is_creator(bot, chat_id, user_id):
                await cb.answer("Это может только создатель чата.", show_alert=True)
                return
            if op == "rw":
                await db.remove_whitelist(chat_id, int(arg))
            else:
                await db.remove_mod(chat_id, int(arg))
                perms.forget(chat_id, int(arg))
        await show(cb, *await trust_screen(bot, chat_id, user_id))

    elif scr == "lg":
        if op == "set":
            await ask(cb, fsm, "log", chat_id, "Пришлите ID чата или канала для журнала (вида -100...) или "
                                               "перешлите сюда любое сообщение из канала. Бот должен быть там "
                                               "админом с правом писать, вы тоже.")
            return
        if op == "off":
            await db.set_chat(chat_id, log_chat_id=0)
        await show(cb, *await log_screen(chat_id))

    elif scr == "in":
        await show(cb, *await info_screen(bot, chat_id, user_id))
    else:
        await cb.answer()


# ---------- ввод текста ----------

def split_items(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n;]+", text or "") if p.strip()]


async def set_log_chat(bot: Bot, message: Message, chat_id: int) -> str:
    target = 0
    origin = message.forward_origin
    if origin is not None and getattr(origin, "chat", None) is not None:
        target = origin.chat.id
    else:
        raw = (message.text or "").strip()
        if re.fullmatch(r"-?\d{5,20}", raw):
            target = int(raw)
    if not target:
        return "не понял. Пришлите ID вида -1001234567890 или перешлите сообщение из канала."
    m = await perms.member(bot, target, message.from_user.id)
    if not perms.is_staff_member(m) and not perms.is_owner(message.from_user.id):
        return "вы должны быть админом в том чате или канале."
    chat = await db.get_chat(chat_id)
    try:
        await bot.send_message(target, out.head(f"сюда будет идти журнал чата {out.esc(chat['title'] or str(chat_id))}."))
    except TelegramAPIError as exc:
        return f"не могу туда писать: {out.esc(exc.message)}. Добавьте меня админом с правом публиковать."
    await db.set_chat(chat_id, log_chat_id=target)
    return ""


@router.message(Input.waiting)
async def on_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    kind, chat_id = data.get("kind"), data.get("chat_id")
    if kind == "emoji":  # премиум-эмодзи владельца, см. admin.py
        from guard.handlers import admin
        await admin.on_emoji_input(message, state)
        return
    if not chat_id or not await perms.can_configure(bot, chat_id, message.from_user.id):
        await state.clear()
        return
    nxt = None
    if kind == "stop_add":
        items = [w.lower()[:64] for w in split_items(message.text or "")]
        have = len(await db.stopwords(chat_id))
        items = items[:max(0, MAX_STOPWORDS - have)]
        n = await db.add_stopwords(chat_id, items)
        await message.answer(f"✅ Добавлено слов: {n}")
        nxt = await stop_screen(chat_id)
    elif kind == "stop_del":
        n = 0
        for w in split_items(message.text or ""):
            n += await db.remove_stopword(chat_id, w.lower())
        await message.answer(f"✅ Убрано слов: {n}")
        nxt = await stop_screen(chat_id)
    elif kind in ("dom_add", "dom_del"):
        items = [normalize_domain(d) for d in split_items(message.text or "")]
        good = [d for d in items if d]
        if kind == "dom_add":
            have = len(await db.domains(chat_id))
            n = await db.add_domains(chat_id, good[:max(0, MAX_DOMAINS - have)])
            note = f"✅ Добавлено доменов: {n}"
        else:
            n = 0
            for d in good:
                n += await db.remove_domain(chat_id, d)
            note = f"✅ Убрано доменов: {n}"
        if len(good) < len(items):
            note += "\nЧасть строк не похожа на домены, пропустил."
        await message.answer(note)
        nxt = await domains_screen(chat_id)
    elif kind == "log":
        error = await set_log_chat(bot, message, chat_id)
        if error:
            await message.answer(out.head(error + "\nОтменить: /cancel"))
            return
        await message.answer("✅ Журнал подключён")
        nxt = await log_screen(chat_id)
    await state.clear()
    if nxt:
        await message.answer(nxt[0], reply_markup=nxt[1], link_preview_options=out.NO_PREVIEW)
