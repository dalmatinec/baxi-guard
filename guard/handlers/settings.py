"""Личка: /start, мои чаты, карточка чата со всеми настройками модерации.

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
from guard.ui import btn, cycle, kb, onoff, toggle_text

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
SERVICE = [("svc_join", "вошёл в чат"), ("svc_leave", "вышел из чата"), ("svc_pin", "закрепил сообщение"),
           ("svc_photo", "сменил фото чата"), ("svc_title", "сменил название чата")]
SPAM_PARTS = [("spam_links", "ссылки и @каналы"), ("spam_forwards", "пересылки"), ("spam_buttons", "кнопки-ссылки"),
              ("spam_scam", "скам-признаки"), ("spam_newbie", "строже к новичкам")]
MAX_STOPWORDS = 500
MAX_DOMAINS = 200


class Input(StatesGroup):
    waiting = State()  # в data: kind, chat_id


def start_text() -> str:
    return (f"<b>{out.HEAD}</b>\n\n"
            "Навожу порядок в чатах: мут, бан, предупреждения, стоп-слова, антиспам, чистка служебных "
            "сообщений и режим атаки. Участники меня не замечают, пока не нарушат.\n\n"
            "Добавьте меня в чат администратором, потом включите нужные защиты в настройках. "
            "Всё автоматическое по умолчанию выключено.")


async def add_url(bot: Bot) -> str:
    me = await bot.me()
    return (f"https://t.me/{me.username}?startgroup=guard"
            "&admin=delete_messages+restrict_members+pin_messages+invite_users")


async def start_markup(bot: Bot, user_id: int) -> InlineKeyboardMarkup:
    rows = [btn("➕ Добавить в чат", url=await add_url(bot), accent=True),
            [btn("⚙️ Мои чаты", "g:chats:0"), btn("📜 Команды", "g:help:0")]]
    if perms.is_owner(user_id):
        rows.append(btn("🛠 Админка", "a:home:0"))
    return kb(*rows)


HELP = (
    "<b>Команды в чате</b> (для админов с правом ограничивать участников)\n\n"
    "Цель: ответом на сообщение, или @username, или ID после команды.\n"
    "Время: 30m, 2h, 3d, 1w. Причина: всё после времени.\n\n"
    "/mute [время] [причина] - запретить писать, без времени на 1 час\n"
    "/unmute - снять мут\n"
    "/warn [причина] - предупреждение, на лимите мут или бан\n"
    "/warns - предупреждения человека (ответом) или свои\n"
    "/unwarn - снять последнее предупреждение\n"
    "/ban [время] [причина] - бан, без времени навсегда\n"
    "/unban - разбан\n"
    "/kick - выгнать без бана\n"
    "/del - удалить сообщение и команду\n"
    "/pin, /unpin - закрепить, открепить\n"
    "/link - ссылка-приглашение\n"
    "/attack - режим атаки: выгонять всех новых\n"
    "/whitelist - белый список (создатель чата)\n"
    "/mod - модератор бота (создатель чата)\n"
    "/settings - настройки в личке\n"
    "/chat - ID чата и права бота\n"
    "/v - пинг\n\n"
    "Команда и ответ бота удаляются сами через несколько секунд, это настраивается."
)


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
            await message.answer(text, reply_markup=markup, parse_mode="HTML", link_preview_options=out.NO_PREVIEW)
            return
        await message.answer(out.head("настраивать этот чат могут только его админы с правом ограничивать "
                                      "участников."))
        return
    await message.answer(start_text(), reply_markup=await start_markup(message.bot, message.from_user.id),
                         parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await chats_screen(message.bot, message.from_user.id)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(out.head("отменено."))


# ---------- экраны ----------

async def my_chats(bot: Bot, user_id: int) -> list[dict]:
    chats = await db.all_chats()
    if perms.is_owner(user_id):
        return chats
    sem = asyncio.Semaphore(10)

    async def check(c):
        async with sem:
            return c if await perms.can_configure(bot, c["chat_id"], user_id) else None
    return [c for c in await asyncio.gather(*(check(c) for c in chats[:200])) if c]


async def chats_screen(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chats = await my_chats(bot, user_id)
    if not chats:
        return (out.head("чатов нет. Добавьте меня в чат администратором, и он появится здесь."),
                kb(btn("➕ Добавить в чат", url=await add_url(bot), accent=True), btn("⬅️ Назад", "g:home:0")))
    rows = [btn((c["title"] or str(c["chat_id"]))[:40], f"g:c:{c['chat_id']}") for c in chats[:50]]
    rows.append(btn("⬅️ Назад", "g:home:0"))
    return "⚙️ <b>Мои чаты</b>\nВыберите чат:", kb(*rows)


def warn_mode_label(chat: dict) -> str:
    if chat["warn_action"] == "ban":
        return "бан"
    return f"мут {human_for(chat['warn_mute_min'] * 60)}"


def warn_summary(chat: dict) -> str:
    ttl = f"сгорают через {chat['warn_ttl_days']} дн." if chat["warn_ttl_days"] else "не сгорают"
    return f"лимит {chat['warn_limit']}, потом {warn_mode_label(chat)}, {ttl}"


def attack_on(chat: dict) -> bool:
    return chat["attack_until"] > db.now()


async def card(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.ensure_chat(chat_id)
    _ok, mode = await access.status(chat)
    miss = await perms.missing_rights(bot, chat_id)
    rights = ("я не администратор" if miss is None else ("не хватает: " + ", ".join(miss) if miss else "все есть"))
    words = len(await db.stopwords(chat_id))
    wl, doms, mods = len(await db.whitelist(chat_id)), len(await db.domains(chat_id)), len(await db.mods(chat_id))
    svc = sum(1 for k, _ in SERVICE if chat[k])
    log_to = f"<code>{chat['log_chat_id']}</code>" if chat["log_chat_id"] else "не задан"
    text = (f"⚙️ <b>{out.esc(chat['title'] or str(chat_id))}</b>\n"
            f"Режим: {out.esc(mode)}\nПрава бота: {rights}\n\n"
            f"🛡 <b>Защита</b>\n"
            f"Стоп-слова: {onoff(chat['stop_on'])}, слов {words}\n"
            f"Антиспам: {onoff(chat['spam_on'])}\n"
            f"Служебные: {svc} из {len(SERVICE)}\n"
            f"Режим атаки: {onoff(attack_on(chat))}\n\n"
            f"⚠️ Предупреждения: {warn_summary(chat)}\n"
            f"👥 Белый список: {wl}, доменов {doms}, модераторов бота {mods}\n"
            f"📜 Журнал: {log_to}")
    rows = [[btn("🚫 Стоп-слова", f"g:sw:{chat_id}"), btn("🔗 Антиспам", f"g:sp:{chat_id}")],
            [btn("🧹 Служебные", f"g:sv:{chat_id}"), btn("🚨 Режим атаки", f"g:at:{chat_id}")],
            [btn("⚠️ Предупреждения", f"g:wr:{chat_id}"), btn("👥 Белый список", f"g:wl:{chat_id}")],
            [btn("📜 Журнал", f"g:lg:{chat_id}"), btn("💬 Ответы бота", f"g:rp:{chat_id}")]]
    if perms.is_owner(user_id):
        rows.append(btn("🔑 Своя подписка (владелец)", f"a:sub:{chat_id}"))
    rows.append(btn("⬅️ Мои чаты", "g:chats:0"))
    return text, kb(*rows)


async def stop_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    words = await db.stopwords(chat_id)
    shown = ", ".join(out.esc(w) for w in words[:80]) or "пусто"
    if len(words) > 80:
        shown += f" и ещё {len(words) - 80}"
    text = (f"🚫 <b>Стоп-слова</b>: {onoff(chat['stop_on'])}\n"
            f"Действие: {STOP_ACTIONS.get(chat['stop_action'], chat['stop_action'])}\n\n"
            "Сравниваю без учёта регистра, ё и е, латинских двойников букв, повторов («плохоооой») и знаков "
            "между буквами («п.л.о.х.о»). Слово ловится и с окончаниями. Проверяю и отредактированные сообщения.\n\n"
            f"Слова ({len(words)}): <tg-spoiler>{shown}</tg-spoiler>")
    return text, kb(
        btn("Выключить" if chat["stop_on"] else "Включить", f"g:sw:{chat_id}:on", accent=True),
        btn(f"Действие: {STOP_ACTIONS.get(chat['stop_action'], '?')}", f"g:sw:{chat_id}:act"),
        [btn("➕ Добавить", f"g:sw:{chat_id}:add"), btn("➖ Убрать", f"g:sw:{chat_id}:del")],
        btn("🗑 Очистить список", f"g:sw:{chat_id}:clr"),
        btn("⬅️ Назад", f"g:c:{chat_id}"))


async def spam_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    doms = await db.domains(chat_id)
    ai_line = "нет ключа в .env" if not config.AI_API_KEY else onoff(chat["spam_ai"])
    text = (f"🔗 <b>Антиспам</b>: {onoff(chat['spam_on'])}\n"
            f"Действие: {SPAM_ACTIONS.get(chat['spam_action'], chat['spam_action'])}\n\n"
            "Не трогаю админов, модераторов бота, белый список и доверенных у Бакси.\n"
            "Ловлю: ссылки на сайты (кроме разрешённых доменов), приглашения в чужие чаты, ссылки на каналы "
            "и ботов, упоминания @каналов, пересылки, кнопки-ссылки, типовой скам (заработок, «пиши в лс», "
            "раздачи, кошельки, номера карт). Новичкам (первые 24 часа или первые 5 сообщений) "
            "нельзя никаких ссылок.\n\n"
            f"Нейросеть для спорных случаев: {ai_line}\n"
            f"Разрешённые домены: {len(doms)}")
    rows = [btn("Выключить" if chat["spam_on"] else "Включить", f"g:sp:{chat_id}:on", accent=True),
            btn(f"Действие: {SPAM_ACTIONS.get(chat['spam_action'], '?')}", f"g:sp:{chat_id}:act")]
    parts = [btn(toggle_text(chat[k], name), f"g:sp:{chat_id}:t:{k}") for k, name in SPAM_PARTS]
    rows += [parts[i:i + 2] for i in range(0, len(parts), 2)]
    if config.AI_API_KEY:
        rows.append(btn(toggle_text(chat["spam_ai"], "нейросеть для спорных"), f"g:sp:{chat_id}:t:spam_ai"))
    rows += [btn("🌐 Разрешённые домены", f"g:dm:{chat_id}"), btn("⬅️ Назад", f"g:c:{chat_id}")]
    return text, kb(*rows)


async def domains_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    doms = await db.domains(chat_id)
    shown = "\n".join(out.esc(d) for d in doms[:100]) or "пусто"
    text = ("🌐 <b>Разрешённые домены</b>\nСсылки на них антиспам пропускает (с поддоменами). "
            "Новичкам ссылки нельзя всё равно.\n\n" + shown)
    return text, kb([btn("➕ Добавить", f"g:dm:{chat_id}:add"), btn("➖ Убрать", f"g:dm:{chat_id}:del")],
                    btn("⬅️ Назад", f"g:sp:{chat_id}"))


async def service_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    text = "🧹 <b>Чистка служебных сообщений</b>\nУдаляю отмеченные сообщения Telegram о событиях в чате."
    rows = [btn(toggle_text(chat[k], name), f"g:sv:{chat_id}:t:{k}") for k, name in SERVICE]
    rows.append(btn("⬅️ Назад", f"g:c:{chat_id}"))
    return text, kb(*rows)


async def attack_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    on = attack_on(chat)
    left = ""
    if on:
        left = f", выключится через {human_duration(max(60, (chat['attack_until'] - db.now()) // 60 * 60))}"
    text = (f"🚨 <b>Режим атаки</b>: {onoff(on)}{left}\n\n"
            "Пока включён, каждого нового участника сразу выгоняю (без бана). Включается здесь или командой "
            "/attack в чате, сам выключается через заданное время.\n\n"
            f"Автовключение (больше 10 входов за минуту): {onoff(chat['attack_auto'])}\n"
            f"Длительность: {chat['attack_minutes']} мин")
    return text, kb(
        btn("Выключить сейчас" if on else "Включить сейчас", f"g:at:{chat_id}:on", accent=True),
        btn(toggle_text(chat["attack_auto"], "автовключение при набеге"), f"g:at:{chat_id}:auto"),
        btn(f"Длительность: {chat['attack_minutes']} мин", f"g:at:{chat_id}:len"),
        btn("⬅️ Назад", f"g:c:{chat_id}"))


async def warns_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    ttl = f"{chat['warn_ttl_days']} дн." if chat["warn_ttl_days"] else "не сгорают"
    text = (f"⚠️ <b>Предупреждения</b>\nНа {chat['warn_limit']}-м предупреждении: {warn_mode_label(chat)}, "
            "после этого счёт начинается заново.\n"
            f"Срок жизни предупреждения: {ttl}")
    return text, kb(
        [btn("➖", f"g:wr:{chat_id}:lim:-1"), btn(f"Лимит: {chat['warn_limit']}", f"g:wr:{chat_id}"),
         btn("➕", f"g:wr:{chat_id}:lim:1")],
        btn(f"На лимите: {warn_mode_label(chat)}", f"g:wr:{chat_id}:mode", accent=True),
        btn(f"Сгорают: {ttl}", f"g:wr:{chat_id}:ttl"),
        btn("⬅️ Назад", f"g:c:{chat_id}"))


def person(r: dict) -> str:
    return r["name"] or (("@" + r["username"]) if r["username"] else str(r["user_id"]))


async def whitelist_screen(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    wl, mods = await db.whitelist(chat_id), await db.mods(chat_id)
    creator = await perms.is_creator(bot, chat_id, user_id)
    text = ("👥 <b>Белый список</b>\nИм можно ссылки и пересылки. Добавляет создатель чата: ответом /whitelist "
            "на сообщение человека.\n" + ("\n".join(f"• {out.esc(person(r))}" for r in wl) or "пусто") +
            "\n\n<b>Модераторы бота</b>\nМогут пользоваться командами модерации без прав в Telegram. "
            "Назначает создатель чата: ответом /mod.\n" +
            ("\n".join(f"• {out.esc(person(r))}" for r in mods) or "нет"))
    rows = []
    if creator:
        rows += [btn(f"❌ {person(r)[:30]}", f"g:wl:{chat_id}:rw:{r['user_id']}") for r in wl[:30]]
        rows += [btn(f"❌ мод. {person(r)[:28]}", f"g:wl:{chat_id}:rm:{r['user_id']}") for r in mods[:20]]
    rows.append(btn("⬅️ Назад", f"g:c:{chat_id}"))
    return text, kb(*rows)


KIND_NAMES = {"mute": "мут", "unmute": "снят мут", "warn": "предупреждение", "unwarn": "снято предупреждение",
              "ban": "бан", "unban": "разбан", "kick": "кик", "delete": "удаление", "spam": "спам"}


async def log_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    items = await db.recent_actions(chat_id, 10)
    lines = []
    for a in items:
        when = datetime.fromtimestamp(a["ts"], config.TZ).strftime("%d.%m %H:%M")
        name = await db.member_name(chat_id, a["user_id"]) or str(a["user_id"])
        line = f"{when} {KIND_NAMES.get(a['kind'], a['kind'])}: {out.esc(name)}"
        if a["reason"]:
            line += f" ({out.esc(a['reason'][:40])})"
        lines.append(line)
    where = f"<code>{chat['log_chat_id']}</code>" if chat["log_chat_id"] else "не задан, всё пишется только в базу"
    text = (f"📜 <b>Журнал</b>\nКуда дублировать: {where}\n\n"
            "Каждое действие пишется в базу (храню 90 дней). Если указать чат или канал, туда пойдёт короткая "
            "строка: кто, кого, что, причина, ссылка.\n\n<b>Последние действия</b>\n" + ("\n".join(lines) or "пусто"))
    rows = [btn("📍 Указать чат или канал", f"g:lg:{chat_id}:set", accent=True)]
    if chat["log_chat_id"]:
        rows.append(btn("Отключить", f"g:lg:{chat_id}:off"))
    rows.append(btn("⬅️ Назад", f"g:c:{chat_id}"))
    return text, kb(*rows)


async def replies_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    ttl = f"через {chat['reply_ttl']} сек" if chat["reply_ttl"] else "не удалять"
    text = ("💬 <b>Ответы бота</b>\nЧтобы не засорять ленту, команда админа удаляется через 5 секунд, "
            "а ответ бота через заданное время.\n\n"
            f"Удалять команды: {onoff(chat['cmd_delete'])}\nУдалять ответы: {ttl}")
    return text, kb(btn(toggle_text(chat["cmd_delete"], "удалять команды"), f"g:rp:{chat_id}:cmd"),
                    btn(f"Ответы: {ttl}", f"g:rp:{chat_id}:ttl", accent=True),
                    btn("⬅️ Назад", f"g:c:{chat_id}"))


# ---------- нажатия ----------

async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML", link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


async def ask(cb: CallbackQuery, state: FSMContext, kind: str, chat_id: int, prompt: str) -> None:
    await state.set_state(Input.waiting)
    await state.update_data(kind=kind, chat_id=chat_id)
    await cb.message.answer(out.head(prompt + "\nОтменить: /cancel"))
    await cb.answer()


@router.callback_query(F.data.startswith("g:"))
async def on_button(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    parts = cb.data.split(":")
    screen = parts[1]
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    op = parts[3] if len(parts) > 3 else ""
    arg = parts[4] if len(parts) > 4 else ""
    user_id = cb.from_user.id

    if screen == "home":
        await state.clear()
        await show(cb, start_text(), await start_markup(bot, user_id))
        return
    if screen == "help":
        await show(cb, HELP, kb(btn("⬅️ Назад", "g:home:0")))
        return
    if screen == "chats":
        await state.clear()
        await show(cb, *await chats_screen(bot, user_id))
        return

    if not await perms.can_configure(bot, chat_id, user_id):
        await cb.answer("Настраивать могут только админы этого чата с правом ограничивать участников.",
                        show_alert=True)
        return
    chat = await db.ensure_chat(chat_id)

    if screen == "c":
        await show(cb, *await card(bot, chat_id, user_id))

    elif screen == "sw":
        if op == "on":
            await db.set_chat(chat_id, stop_on=0 if chat["stop_on"] else 1)
        elif op == "act":
            await db.set_chat(chat_id, stop_action=cycle(list(STOP_ACTIONS), chat["stop_action"]))
        elif op == "add":
            await ask(cb, state, "stop_add", chat_id, "пришлите слова или фразы через запятую или с новой строки.")
            return
        elif op == "del":
            await ask(cb, state, "stop_del", chat_id, "пришлите слова, которые убрать, через запятую.")
            return
        elif op == "clr":
            if arg != "yes":
                await show(cb, "Очистить весь список стоп-слов?",
                           kb([btn("Да, очистить", f"g:sw:{chat_id}:clr:yes"), btn("Нет", f"g:sw:{chat_id}")]))
                return
            await db.clear_stopwords(chat_id)
        await show(cb, *await stop_screen(chat_id))

    elif screen == "sp":
        if op == "on":
            await db.set_chat(chat_id, spam_on=0 if chat["spam_on"] else 1)
        elif op == "act":
            await db.set_chat(chat_id, spam_action=cycle(list(SPAM_ACTIONS), chat["spam_action"]))
        elif op == "t" and arg in {k for k, _ in SPAM_PARTS} | {"spam_ai"}:
            await db.set_chat(chat_id, **{arg: 0 if chat[arg] else 1})
        await show(cb, *await spam_screen(chat_id))

    elif screen == "dm":
        if op == "add":
            await ask(cb, state, "dom_add", chat_id, "пришлите домены через запятую или с новой строки, "
                                                     "например: youtube.com, example.org")
            return
        if op == "del":
            await ask(cb, state, "dom_del", chat_id, "пришлите домены, которые убрать.")
            return
        await show(cb, *await domains_screen(chat_id))

    elif screen == "sv":
        if op == "t" and arg in {k for k, _ in SERVICE}:
            await db.set_chat(chat_id, **{arg: 0 if chat[arg] else 1})
        await show(cb, *await service_screen(chat_id))

    elif screen == "at":
        if op == "on":
            from guard import protect
            if attack_on(chat):
                await protect.attack_off(bot, chat_id, by_id=user_id)
            else:
                await protect.attack_on(bot, chat_id, by_id=user_id)
        elif op == "auto":
            await db.set_chat(chat_id, attack_auto=0 if chat["attack_auto"] else 1)
        elif op == "len":
            await db.set_chat(chat_id, attack_minutes=cycle(ATTACK_MINUTES, chat["attack_minutes"]))
        await show(cb, *await attack_screen(chat_id))

    elif screen == "wr":
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

    elif screen == "wl":
        if op in ("rw", "rm") and arg.lstrip("-").isdigit():
            if not await perms.is_creator(bot, chat_id, user_id):
                await cb.answer("Это может только создатель чата.", show_alert=True)
                return
            if op == "rw":
                await db.remove_whitelist(chat_id, int(arg))
            else:
                await db.remove_mod(chat_id, int(arg))
                perms.forget(chat_id, int(arg))
        await show(cb, *await whitelist_screen(bot, chat_id, user_id))

    elif screen == "lg":
        if op == "set":
            await ask(cb, state, "log", chat_id, "пришлите ID чата или канала для журнала (вида -100...) или "
                                                 "перешлите сюда любое сообщение из канала. Я должен быть там "
                                                 "админом с правом писать, вы тоже.")
            return
        if op == "off":
            await db.set_chat(chat_id, log_chat_id=0)
        await show(cb, *await log_screen(chat_id))

    elif screen == "rp":
        if op == "cmd":
            await db.set_chat(chat_id, cmd_delete=0 if chat["cmd_delete"] else 1)
        elif op == "ttl":
            await db.set_chat(chat_id, reply_ttl=cycle(REPLY_TTLS, chat["reply_ttl"]))
        await show(cb, *await replies_screen(chat_id))
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
        await bot.send_message(target, out.head(f"сюда будет идти журнал чата «{chat['title'] or chat_id}»."))
    except TelegramAPIError as exc:
        return f"не могу туда писать: {out.esc(exc.message)}. Добавьте меня админом с правом публиковать."
    await db.set_chat(chat_id, log_chat_id=target)
    return ""


@router.message(Input.waiting)
async def on_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    kind, chat_id = data.get("kind"), data.get("chat_id")
    if not chat_id or not await perms.can_configure(bot, chat_id, message.from_user.id):
        await state.clear()
        return
    back = None
    if kind == "stop_add":
        items = [w.lower()[:64] for w in split_items(message.text or "")]
        have = len(await db.stopwords(chat_id))
        items = items[:max(0, MAX_STOPWORDS - have)]
        n = await db.add_stopwords(chat_id, items)
        await message.answer(out.head(f"добавлено слов: {n}."))
        back = await stop_screen(chat_id)
    elif kind == "stop_del":
        n = 0
        for w in split_items(message.text or ""):
            n += await db.remove_stopword(chat_id, w.lower())
        await message.answer(out.head(f"убрано слов: {n}."))
        back = await stop_screen(chat_id)
    elif kind in ("dom_add", "dom_del"):
        items = [normalize_domain(d) for d in split_items(message.text or "")]
        good = [d for d in items if d]
        if kind == "dom_add":
            have = len(await db.domains(chat_id))
            n = await db.add_domains(chat_id, good[:max(0, MAX_DOMAINS - have)])
            note = f"добавлено доменов: {n}."
        else:
            n = 0
            for d in good:
                n += await db.remove_domain(chat_id, d)
            note = f"убрано доменов: {n}."
        if len(good) < len(items):
            note += " Часть строк не похожа на домены, пропустил."
        await message.answer(out.head(note))
        back = await domains_screen(chat_id)
    elif kind == "log":
        error = await set_log_chat(bot, message, chat_id)
        if error:
            await message.answer(out.head(error + "\nОтменить: /cancel"))
            return
        await message.answer(out.head("журнал подключён."))
        back = await log_screen(chat_id)
    await state.clear()
    if back:
        await message.answer(back[0], reply_markup=back[1], parse_mode="HTML", link_preview_options=out.NO_PREVIEW)
