"""Личка: /start, мои чаты и настройки каждого чата по категориям.

Карточка чата → категории: 🛡 Защита (антиспам, стоп-слова, режим атаки), 🧹 Чистота, ⚠️ Наказания,
👥 Доверие, 📜 Журнал, ℹ️ О чате. Строгий текстовый стиль из guard/ui.py.

Кнопки: g:<экран>:<chat_id>[:<действие>[:<значение>]]. Права проверяются на каждое нажатие."""

import asyncio
import html as html_lib
import logging
import re
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import access, config, db, out, perms
from guard import welcome as welcome_mod
from guard.links import normalize_domain
from guard.parse import human_duration, human_for
from guard.ui import DIV, DIV_SHORT, back, btn, chat_title, check, cycle, grid, kb, row, screen
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


# ввод для настроек, открытых прямо в чате (/settings в группе): стоит рано, сразу после автоматики
group_router = Router(name="settings_group")
group_router.message.filter(F.chat.type.in_({"group", "supergroup"}), StateFilter(Input.waiting))
GROUP_TTL = 600   # настройки, открытые в чате, сами исчезают через 10 минут


def in_group(message) -> bool:
    return message is not None and message.chat.type in ("group", "supergroup")


def for_group(markup: InlineKeyboardMarkup, chat_id: int) -> InlineKeyboardMarkup:
    """В чате нет «Моих чатов» и главного меню: вместо них «✖️ Закрыть»."""
    rows = []
    for r in markup.inline_keyboard:
        new = []
        for b in r:
            data = b.callback_data or ""
            if data.startswith(("g:chats", "g:home")):
                b = btn("✖️ Закрыть", f"g:x:{chat_id}")
            new.append(b)
        rows.append(new)
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    from guard import owners
    rows = [btn("➕ Добавить в чат", url=await add_url(bot), accent=True),
            [btn("⚙️ Мои чаты", "g:chats:0"), btn("📖 Команды", "g:help:0")],
            [btn("👤 Профиль", "pf:home"), btn("🧰 Перенос чата", "tr:home")],
            btn("🆘 Связь с разработчиком", "sp:home")]
    if await owners.heir_chats(user_id):
        rows.append(btn("🔑 Права владельца", "ow:me"))
    if perms.is_owner(user_id):
        rows.append(btn("🛠 Супер-админка", "a:home:0", style="success"))
    return kb(*rows)


HELP = screen("📖 Команды в чате", [
    "Роли: 👑 владелец (создатель чата), ⭐ админ, 🛡 модератор, 🔰 мл. модератор, 🎮 ведущий игр. "
    "Админка в Telegram сама ничего не даёт. Кого: ответом, @username или ID. Время: 30m, 2h, 3d, 1w.",
    "У каждой русской команды есть латинская: в скобках.",
    "",
    "<b>🔰 Мл. модератор</b>",
    "/mute · /unmute · /warn · /unwarn · /warns · /del · /pin · /unpin",
    "",
    "<b>🎮 Ведущий игр</b>",
    "/game · /list · /close · /open · /fk 30 · /random 36 · /endgame",
    "рандом 36 (/random 36) · закрыть список (/close) · фк 30 (/fk 30)",
    "+топ, +топ игра, +топ 3д (/top) · -топ (/untop) ответом",
    "+в список (/add) ответом · убрать 3 (/remove 3)",
    "",
    "<b>🛡 Модератор</b>: всё выше и",
    "/ban · /unban · /kick · /attack · /link",
    "+триггер слово (+trigger) ответом · -триггер слово (-trigger) · триггеры (/triggers)",
    "",
    "<b>⭐ Админ и 👑 владелец</b>",
    "роль (/role) ответом: выбрать роль кнопкой. Админа назначает только владелец",
    "/allow · /unallow: белый список (ответом)",
    "+приветствие (/setwelcome) ответом · -приветствие (/welcomeoff) · приветствие (/welcome)",
    "капча кнопка/пример/эмодзи/выкл (/captcha button/math/emoji/off)",
    "настройки (/settings): кнопка в личку · /chat",
    "",
    "<b>Все</b>",
    "профиль (/profile) · стата (/stat) · инфо (/info) · выписаться (/leave)",
    "👤 профиль, ник и стикер профиля: в личке бота, кнопка «Профиль»",
], note="Команда и ответ бота сами исчезают через несколько секунд, это меняется в разделе Чистота.")


# ---------- вход ----------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    arg = command.args or ""
    if arg == "help":
        await message.answer(HELP, reply_markup=kb(back("g:home:0")))
        return
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
        await message.answer(out.head("настраивать этот чат могут админы бота и владелец (создатель чата)."))
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
        return "⚠️ Бот не администратор в чате"
    if miss:
        return "⚠️ Не хватает прав: " + ", ".join(miss)
    return "✅ Права бота в порядке"


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


def protect_status(chat: dict) -> str:
    on = [name for key, name in (("spam_on", "антиспам"), ("stop_on", "стоп-слова")) if chat[key]]
    if attack_on(chat):
        on.append("идёт режим атаки")
    elif chat["attack_auto"]:
        on.append("атака авто")
    return "🟢 " + ", ".join(on) if on else "🔴 выключена"


def newcomers_status(chat: dict) -> str:
    if chat["captcha"]:
        hi = " · приветствие" if chat["welcome_on"] else ""
        return f"🟢 капча: {welcome_mod.MODES.get(chat['captcha'], chat['captcha'])}{hi}"
    return "🟡 приветствие, без капчи" if chat["welcome_on"] else "🔴 без капчи"


def clean_status(chat: dict) -> str:
    svc = [name for k, name in SERVICE if chat[k]]
    return "🟢 удаляю: " + ", ".join(svc) if svc else "▫️ служебные не трогаю"


def needs_quick(chat: dict) -> bool:
    """Рекомендуемое ещё не включено: показываем «⚡ Быстрая настройка»."""
    return not (chat["spam_on"] and chat["captcha"] and chat["attack_auto"])


async def card(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard import chatinfo
    chat = await db.ensure_chat(chat_id)
    ok, mode = await access.status(chat)
    wl, mods = len(await db.whitelist(chat_id)), len(await db.rights_list(chat_id))
    try:
        members = await bot.get_chat_member_count(chat_id)
    except Exception:
        members = 0
    plan = (await chatinfo.plan(chat)).replace("Тариф: ", "")
    sub = " · ".join(x for x in (f"👥 {chatinfo.number(members)} {chatinfo.plural(members, 'участник', 'участника', 'участников')}"
                                 if members else "", f"💳 {plan}") if x)
    team = f"ролей {mods}" if mods else "ролей нет"
    lines = [f"<i>{sub}</i>", "➖" * 7,
             row("🛡", "Защита", protect_status(chat)),
             row("👋", "Новички", newcomers_status(chat)),
             row("🧹", "Чистота", clean_status(chat)),
             row("⚠️", "Наказания", f"{chat['warn_limit']} "
                 f"{chatinfo.plural(chat['warn_limit'], 'предупреждение', 'предупреждения', 'предупреждений')} "
                 f"→ {warn_mode_label(chat)}"),
             row("👥", "Команда", team, f"🤍 {wl}"),
             row("📜", "Журнал", "в канале" if chat["log_chat_id"] else "в базе бота")]
    rights = await rights_line(bot, chat_id)
    footer = [rights]
    if not ok:
        footer.append(f"⏸ {out.esc(mode)}")
    if "⚠️" in rights:
        footer.append("💡 Дайте боту права администратора, без них защита не работает")
    elif not (chat["spam_on"] or chat["stop_on"]):
        footer.append("💡 Защита выключена: включите её кнопкой «⚡ Быстрая настройка»")
    elif not chat["captcha"]:
        footer.append("💡 Капча не включена: боты-спамеры заходят без проверки")
    head = f"⚙️ {chat_title(chat)}"
    text = "\n".join([f"<b>{head}</b>", *lines, "➖" * 7, *footer])
    protect_on = bool(chat["spam_on"] or chat["stop_on"])
    rows = []
    if needs_quick(chat):
        rows.append(btn("⚡ Быстрая настройка", f"g:qs:{chat_id}", style="success"))
    rows += [[btn("🛡 Защита", f"g:pr:{chat_id}", style="success" if protect_on else "danger"),
              btn("👥 Команда", f"g:tm:{chat_id}")],
             [btn("🎨 Оформление", f"g:ds:{chat_id}"), btn("📜 Журнал", f"g:lg:{chat_id}")],
             btn("ℹ️ О чате и перенос", f"g:in:{chat_id}"),
             back("g:chats:0", "⬅️ Мои чаты")]
    return text, kb(*rows)


QUICK = [("🛡", "Антиспам: ссылки новичков, реклама, мошенники"),
         ("👋", "Капча кнопкой, 3 минуты, не прошёл: удалить из чата"),
         ("🧹", "Удалять «вошёл» и «вышел»"),
         ("🚨", "Режим атаки включается сам при наплыве")]


def quick_screen(chat: dict) -> tuple[str, InlineKeyboardMarkup]:
    c = chat["chat_id"]
    lines = ["Включу рекомендуемое:", *[f"✅ {text}" for _icon, text in QUICK]]
    text = screen("⚡ Быстрая настройка", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Остальное не трогаю. Любой пункт потом меняется в разделах.")
    return text, kb([btn("✅ Включить", f"g:qs:{c}:yes", style="success"), btn("Отмена", f"g:c:{c}")])


async def quick_apply(bot: Bot, chat_id: int) -> str:
    """Включить рекомендуемое. Возвращает предупреждение, если что-то не включилось."""
    fields = dict(spam_on=1, spam_links=1, spam_forwards=1, spam_buttons=1, spam_scam=1, spam_newbie=1,
                  svc_join=1, svc_leave=1, attack_auto=1)
    note = ""
    problem = await perms.rights_problem(bot, chat_id, "can_restrict_members", "can_delete_messages")
    if problem:
        note = "Капчу не включил: " + re.sub(r"<[^>]+>", "", problem)
    else:
        fields.update(captcha="button", captcha_min=3, captcha_fail="kick")
    before = welcome_mod.greets(await db.get_chat(chat_id))
    await db.set_chat(chat_id, **fields)
    fresh = await db.get_chat(chat_id)
    if welcome_mod.greets(fresh) != before:
        await welcome_mod.sync_baxi(fresh)
    return note


async def team_screen(bot: Bot, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    rights = await db.rights_list(chat_id)
    by_role: dict[str, int] = {}
    for r in rights:
        role = perms.role_from_flags(r["flags"])
        by_role[role] = by_role.get(role, 0) + 1
    roles = ", ".join(f"{perms.ROLE_TITLES.get(k, k)} {n}" for k, n in by_role.items()) or "не назначены"
    owner = chat["owner_override"] or chat["owner_id"]
    lines = [row("👥", "Роли", roles),
             row("🤍", "Белый список", str(len(await db.whitelist(chat_id)))),
             row("👑", "Владелец", (await db.member_name(chat_id, owner) or str(owner)) if owner else "не известен"),
             row("🛟", "Заместитель", (await db.member_name(chat_id, chat["heir_id"]) or str(chat["heir_id"]))
                 if chat["heir_id"] else "нет")]
    text = screen("👥 Команда", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Роль в чате: ответом на сообщение «роль» или /role.")
    return text, kb(btn("👥 Роли и белый список", f"g:wl:{chat_id}"), btn("👑 Владелец и заместитель", f"ow:c:{chat_id}"),
                    back(f"g:c:{chat_id}"))


async def design_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard import texts, triggers
    chat = await db.get_chat(chat_id)
    own = 0
    for key in texts.TEXTS:
        if (await texts.override(chat_id, key) or {}).get("html"):
            own += 1
    trig = len(await triggers.all_of(chat_id))
    lines = [row("👋", "Приветствие", "вкл" if chat["welcome_on"] else "выкл",
                 f"капча: {welcome_mod.MODES.get(chat['captcha'], 'выкл')}"),
             row("✉️", "Свои тексты", str(own) if own else "стандартные"),
             row("🧩", "Триггеры", str(trig) if trig else "нет")]
    text = screen("🎨 Оформление", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Любое сообщение бота можно переписать с премиум-эмодзи, картинкой и кнопками.")
    return text, kb([btn("👋 Приветствие", f"g:hi:{chat_id}"), btn("✉️ Тексты", f"g:te:{chat_id}")],
                    btn("🧩 Триггеры", f"g:tz:{chat_id}"), back(f"g:c:{chat_id}"))


WELCOME_TTLS = [0, 1, 5, 15, 60]
CAPTCHA_MODES = ["", "button", "math", "emoji"]
CAPTCHA_MINUTES = [1, 3, 5, 10]
CAPTCHA_FAILS = ["kick", "mute", "ban"]
P_WELCOME = "👋 Приветствие"


async def welcome_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    ttl = f"через {chat['welcome_ttl']} мин" if chat["welcome_ttl"] else "не удаляю"
    fail = welcome_mod.FAILS.get(chat["captcha_fail"], welcome_mod.FAILS["kick"])[1]
    lines = [row("👋", "Приветствие", word(chat["welcome_on"])),
             row("🤖", "Фраза Бакси после", word(chat["welcome_baxi"])),
             row("⏱", "Удалять", ttl),
             row("🧹", "Новое убирает прошлое", word(chat["welcome_clean"])),
             "",
             row("🛡", "Капча", welcome_mod.MODES.get(chat["captcha"], "выкл")),
             row("⏳", "Время на ответ", f"{chat['captcha_min']} мин"),
             row("🚪", "Не прошёл", fail)]
    text = screen(P_WELCOME, lines, path=f"⚙️ {chat_title(chat)}",
                  note="Приветствие задаётся и в чате: ответьте «+приветствие» на любое сообщение, оно сохранится "
                       "копией с премиум-эмодзи, картинкой или видео, а строки «Текст - ссылка» в конце станут "
                       "кнопками. Пока капча не пройдена, новичок не может писать.")
    c = chat_id
    return text, kb(
        [btn(check(chat["welcome_on"], "Приветствие"), f"g:hi:{c}:on", accent=True),
         btn("✏️ Текст и медиа", f"g:ti:{c}:welcome:hi")],
        [btn(check(chat["welcome_baxi"], "Фраза Бакси"), f"g:hi:{c}:baxi"),
         btn(check(chat["welcome_clean"], "Убирать прошлое"), f"g:hi:{c}:clean")],
        [btn(f"⏱ {ttl}", f"g:hi:{c}:ttl"), btn("👁 Как в чате", f"g:hi:{c}:test")],
        [btn(f"🛡 Капча: {welcome_mod.MODES.get(chat['captcha'], 'выкл')}", f"g:hi:{c}:cap")],
        [btn(f"⏳ {chat['captcha_min']} мин", f"g:hi:{c}:capmin"), btn(f"🚪 {fail}", f"g:hi:{c}:capfail")],
        btn("✏️ Текст капчи", f"g:ti:{c}:captcha:hi"),
        back(f"g:ds:{c}"))


async def protect_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    words = len(await db.stopwords(chat_id))
    domains = len(await db.domains(chat_id))
    attack = "🔥 идёт" if attack_on(chat) else ("авто" if chat["attack_auto"] else "выкл")
    on, total = clean_count(chat)
    lines = [
        row("🛡", "Антиспам", "🟢 вкл" if chat["spam_on"] else "🔴 выкл",
            SPAM_ACTIONS[chat["spam_action"]] if chat["spam_on"] else ""),
        row("🚫", "Стоп-слова", "🟢 вкл" if chat["stop_on"] else "🔴 выкл", f"слов {words}"),
        row("🔗", "Разрешённые сайты", str(domains)),
        row("🚨", "Режим атаки", attack),
        row("🧹", "Чистота", f"{on} из {total}"),
        row("⚠️", "Наказания", warn_summary(chat)),
    ]
    text = screen(P_PROTECT, lines, path=f"⚙️ {chat_title(chat)}",
                  note="Админов, роли бота, белый список и доверенных у Бакси автоматика не трогает.")
    c = chat_id
    return text, kb([btn("🛡 Антиспам", f"g:sp:{c}", style="success" if chat["spam_on"] else "danger"),
                     btn("🚫 Стоп-слова", f"g:sw:{c}", style="success" if chat["stop_on"] else "danger")],
                    [btn("🔗 Сайты", f"g:dm:{c}"), btn("🚨 Атака", f"g:at:{c}")],
                    [btn("🧹 Чистота", f"g:cl:{c}"), btn("⚠️ Наказания", f"g:wr:{c}")],
                    back(f"g:c:{c}"))


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
                    back(f"g:pr:{chat_id}"))


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
                    back(f"g:pr:{chat_id}"))


def person(r: dict) -> str:
    return r["name"] or (("@" + r["username"]) if r["username"] else str(r["user_id"]))


async def trust_screen(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    wl, holders = await db.whitelist(chat_id), await db.rights_list(chat_id)
    can_grant = await perms.has(bot, chat_id, user_id, perms.R_GRANT)
    lines = [f"<b>⭐ Роли</b> · {len(holders)}"]
    lines += [f"• {out.esc(person(r))}: {perms.rights_text(r['flags'])}" for r in holders[:30]] or \
        ["ни у кого, кроме владельца"]
    lines += ["", f"<b>🤍 Белый список</b> · {len(wl)}"]
    lines += [f"• {out.esc(person(r))}" for r in wl[:30]] or ["пусто"]
    text = screen("👥 Роли", lines, path=f"⚙️ {chat_title(chat)}",
                  note="👑 Владелец: создатель чата. ⭐ Админ: всё в чате, назначает модераторов. 🛡 Модератор: "
                       "наказания, удаление, игры, триггеры. Админка в Telegram сама ничего не даёт. "
                       "В чате ответом: /mod, /adm, /unmod; белый список: /trust, /untrust.")
    rows = []
    if can_grant:
        rows += grid([btn(f"{person(r)[:24]}", f"g:rk:{chat_id}:{r['user_id']}") for r in holders[:20]], 2)
        rows.append(btn("➕ Назначить", f"g:wl:{chat_id}:add", accent=True))
    if await perms.has(bot, chat_id, user_id, perms.R_SETTINGS):
        rows += grid([btn(f"❌ {person(r)[:22]}", f"g:wl:{chat_id}:rw:{r['user_id']}") for r in wl[:20]], 2)
    return text, kb(*rows, btn("🌐 Разрешённые домены", f"g:dm:{chat_id}"), back(f"g:tm:{chat_id}"))


async def role_card(bot: Bot, chat_id: int, target: int, name: str,
                    actor: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    r = await perms.role(bot, chat_id, target)
    keys = await perms.roles_for(bot, chat_id, actor) if actor else list(perms.ROLES)
    lines = [row("🎭", "Роль", perms.ROLE_TITLES[r]), ""]
    lines += [f"{perms.ROLES[k][0]}: {perms.ROLES[k][2]}" for k in keys]
    text = screen(f"⭐ {out.esc(name)}", lines, path="👥 Роли")
    rows = grid([btn(("✅ " if r == k else "") + perms.ROLES[k][0], f"g:rk:{chat_id}:{target}:{k}") for k in keys], 2)
    rows.append(btn("🚫 Снять роль", f"g:rk:{chat_id}:{target}:none"))
    return text, kb(*rows, back(f"g:wl:{chat_id}"))


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
    rows = [btn("🧰 Перенести в другой чат", f"tr:s:{chat_id}:in")]
    if perms.is_owner(user_id):
        rows.append(btn("🔑 Своя подписка", f"a:sub:{chat_id}"))
    return text, kb(*rows, back(f"g:c:{chat_id}"))


# ---------- ✉️ тексты ----------

BUTTONS_HELP = ("Каждая кнопка с новой строки: <code>Текст - ссылка</code>. Несколько в один ряд через "
                "<code>|</code>.\nПример:\n<code>📢 Канал - https://t.me/channel | 🛒 Магазин - https://example.com</code>")
BUTTONS_OWNER_HELP = ("\n\n<b>Только для владельца бота:</b> цвет в конце кнопки <code>#синяя</code>, "
                      "<code>#зелёная</code> или <code>#красная</code>, а премиум-эмодзи в начале станет значком "
                      "кнопки.\nПример: <code>🔥 Канал - https://t.me/channel #зелёная</code>")
GLOBAL_CHAT = {"chat_id": 0, "title": "Все чаты"}
TEXT_SCREENS = ("te", "tg", "ti", "tw", "tv", "tr")


def texts_back(chat_id: int) -> str:
    return "a:home:0" if chat_id == 0 else f"g:ds:{chat_id}"


async def send_template(message: Message, chat_id: int, key: str, what: str) -> None:
    """Сохранённый текст или кнопки отдельным сообщением как есть, с премиум-эмодзи: скопировать, поправить
    в Telegram и прислать обратно. Так текст, поменянный в веб-админке, получает премиум-эмодзи."""
    from guard import richtext, texts
    t = await texts.template(chat_id, key)
    if what == "text":
        body = t.html
    elif what == "buttons" and t.buttons:
        body = richtext.from_web(out.esc(texts.buttons_text(t.buttons)))
    else:
        return
    try:
        await message.answer(body, link_preview_options=out.NO_PREVIEW)
    except TelegramAPIError:
        await message.answer(out.esc(richtext.to_web(body)), link_preview_options=out.NO_PREVIEW)


async def texts_home(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard import texts
    chat = await db.get_chat(chat_id) if chat_id else GLOBAL_CHAT
    own = await texts.custom_keys(chat_id)
    lines, buttons = [], []
    for n, group in enumerate(texts.groups()):
        keys = texts.keys_of(group)
        mine = len([k for k in keys if k in own])
        icon = texts.GROUP_ICONS.get(group, "📝")
        lines.append(row(icon, group, f"{len(keys)}", f"✏️ своих {mine}" if mine else ""))
        buttons.append(btn(f"{icon} {group}", f"g:tg:{chat_id}:{n}"))
    if chat_id == 0:
        text = screen("✉️ Тексты бота", lines, path="🛠 Админка",
                      note="Тексты сразу для всех чатов: текст с премиум-эмодзи, картинка, кнопки с цветом и значком. "
                           "Где админы чата поставили свой текст, остаётся их.")
    else:
        text = screen("✉️ Тексты", lines, path=f"⚙️ {chat_title(chat)}",
                      note="Любое сообщение бота в этом чате можно переписать под себя: свой текст с премиум-эмодзи, "
                           "картинка, кнопки-ссылки (например, реклама). Другие чаты это не меняет.")
    return text, kb(*grid(buttons, 2), back(texts_back(chat_id)))


async def texts_group(chat_id: int, n: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard import texts
    group = texts.groups()[n]
    own = await texts.custom_keys(chat_id)
    buttons = [btn(("✏️ " if k in own else "") + texts.TEXTS[k][1], f"g:ti:{chat_id}:{k}") for k in texts.keys_of(group)]
    text = screen(f"{texts.GROUP_ICONS.get(group, '📝')} {group}", ["Нажми на текст, чтобы посмотреть и поменять."],
                  path="✉️ Тексты", note="✏️ значит здесь уже свой текст.")
    return text, kb(*grid(buttons, 1), back(f"g:te:{chat_id}"))


# откуда открыли текст: «Назад» возвращает туда же (приветствие → его текст → назад в приветствие)
_text_from: dict[tuple[int, int, str], str] = {}
TEXT_ORIGINS = {"hi": "g:hi:{chat}"}


def text_back(user_id: int, chat_id: int, key: str) -> str:
    from guard import texts
    group = texts.TEXTS[key][0]
    return _text_from.get((user_id, chat_id, key)) or f"g:tg:{chat_id}:{texts.groups().index(group)}"


async def text_item(chat_id: int, key: str, user_id: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    from guard import texts
    group, title, _default, subs = texts.TEXTS[key]
    own = await texts.override(chat_id, key) or {}
    common = (await texts.override(texts.GLOBAL, key) or {}) if chat_id else {}
    t = await texts.template(chat_id, key)
    hint = "\n".join(f"<code>{{{k}}}</code> {v[0]}" for k, v in subs.items())
    source = "свой" if own.get("html") else ("общий текст бота" if common.get("html") else "стандартный")
    lines = [row("📝", "Текст", source),
             row("🖼", "Картинка", {"photo": "картинка", "video": "видео", "animation": "гифка"}[texts.media_of(t.photo)[0]]
                 if t.photo else "нет"),
             row("🔘", "Кнопки", str(sum(len(r) for r in t.buttons)) if t.buttons else "нет"),
             DIV, "<b>Так выглядит:</b>", texts.sample(key, t.html), DIV, "<b>Подстановки</b>", hint]
    text = screen(title, lines, path="✉️ Тексты")
    if len(text) > 4000:
        text = text[:3990] + "…"
    rows = [[btn("✏️ Текст", f"g:tw:{chat_id}:{key}:text", accent=True), btn("🖼 Картинка", f"g:tw:{chat_id}:{key}:photo")],
            [btn("🔘 Кнопки", f"g:tw:{chat_id}:{key}:buttons"), btn("👁 Как в чате", f"g:tv:{chat_id}:{key}")]]
    extra = []
    if t.photo:
        extra.append(btn("🗑 Без картинки", f"g:tw:{chat_id}:{key}:nophoto"))
    if t.buttons:
        extra.append(btn("🗑 Без кнопок", f"g:tw:{chat_id}:{key}:nobuttons"))
    if extra:
        rows.append(extra)
    if own:
        rows.append(btn("↩️ Вернуть стандартный", f"g:tr:{chat_id}:{key}"))
    return text, kb(*rows, back(text_back(user_id, chat_id, key)))


async def preview(cb: CallbackQuery, chat_id: int, key: str) -> None:
    from guard import texts
    t = await texts.template(chat_id, key)
    html = texts.sample(key, t.html)
    markup = texts.markup(t)
    try:
        if t.photo and len(html) <= texts.MAX_CAPTION:
            await texts.send_media(cb.bot, cb.message.chat.id, t.photo, caption=html, markup=markup)
        else:
            if t.photo:
                await texts.send_media(cb.bot, cb.message.chat.id, t.photo)
            await cb.message.answer(html, reply_markup=markup)
    except TelegramAPIError as exc:
        await cb.message.answer(f"⚠️ Telegram не принял этот текст: {out.esc(exc.message)}")


# ---------- 🧩 триггеры ----------

KIND_ICONS = {"text": "💬", "photo": "🖼", "video": "🎬", "animation": "🎞", "sticker": "🩷", "voice": "🎤",
              "audio": "🎵", "document": "📄", "video_note": "⭕"}


async def triggers_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from guard import triggers
    chat = await db.get_chat(chat_id)
    items = list((await triggers.all_of(chat_id)).values())
    lines = [f"{i}. {KIND_ICONS.get(t['kind'], '💬')} {out.esc(t['word'])}"
             for i, t in enumerate(items[:80], 1)] or ["пока нет"]
    if len(items) > 80:
        lines.append(f"и ещё {len(items) - 80}")
    text = screen("🧩 Триггеры", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Напишут в чате слово целиком, бот ответит сохранённым: текстом с премиум-эмодзи, фото, "
                       "видео, гифкой, стикером, голосовым или файлом. В чате: ответом «+триггер слово», "
                       "«-триггер слово», «триггеры».")
    dels = [btn(f"🗑 {t['word'][:24]}", f"g:tz:{chat_id}:del:{t['id']}") for t in items[:30]]
    return text, kb(btn("➕ Добавить", f"g:tz:{chat_id}:add", accent=True), *grid(dels, 2), back(f"g:ds:{chat_id}"))


# ---------- нажатия ----------

async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if in_group(cb.message):
        markup = for_group(markup, cb.message.chat.id)
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


async def ask(cb: CallbackQuery, fsm: FSMContext, kind: str, chat_id: int, prompt: str) -> None:
    await fsm.set_state(Input.waiting)
    await fsm.update_data(kind=kind, chat_id=chat_id)
    await cb.message.answer(f"✏️ {prompt}\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
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

    if scr == "x":   # закрыть настройки, открытые в чате
        if in_group(cb.message) and await perms.flags(bot, chat_id, user_id) & perms.R_SETTINGS:
            try:
                await cb.message.delete()
            except TelegramBadRequest:
                pass
        await cb.answer()
        return
    if in_group(cb.message) and cb.message.chat.id != chat_id:
        await cb.answer()   # в чате настраивается только этот чат
        return
    if chat_id == 0:   # тексты бота для всех чатов: только владелец бота
        if not perms.is_owner(user_id) or scr not in TEXT_SCREENS:
            await cb.answer()
            return
        chat = GLOBAL_CHAT
    else:
        need = {"wl": perms.R_SETTINGS | perms.R_GRANT, "rk": perms.R_SETTINGS | perms.R_GRANT,
                "tz": perms.R_SETTINGS | perms.R_TRIGGERS}.get(scr, perms.R_SETTINGS)
        if not await perms.flags(bot, chat_id, user_id) & need:
            await cb.answer("Настраивать могут админы бота и владелец (создатель чата).", show_alert=True)
            return
        chat = await db.ensure_chat(chat_id)

    if scr == "c":
        await show(cb, *await card(bot, chat_id, user_id))
    elif scr == "qs":   # ⚡ быстрая настройка
        if op != "yes":
            await show(cb, *quick_screen(chat))
            return
        note = await quick_apply(bot, chat_id)
        text, markup = await card(bot, chat_id, user_id)
        await show(cb, ("✅ <b>Рекомендуемое включено</b>\n" + (f"⚠️ {out.esc(note)}\n" if note else "") + "\n" + text),
                   markup)
    elif scr == "tm":
        await show(cb, *await team_screen(bot, chat_id))
    elif scr == "ds":
        await show(cb, *await design_screen(chat_id))
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

    elif scr == "hi":
        before = welcome_mod.greets(chat)
        if op == "on":
            await db.set_chat(chat_id, **flip(chat, "welcome_on"))
        elif op == "baxi":
            await db.set_chat(chat_id, **flip(chat, "welcome_baxi"))
        elif op == "clean":
            await db.set_chat(chat_id, **flip(chat, "welcome_clean"))
        elif op == "ttl":
            await db.set_chat(chat_id, welcome_ttl=cycle(WELCOME_TTLS, chat["welcome_ttl"]))
        elif op == "cap":
            mode = cycle(CAPTCHA_MODES, chat["captcha"])
            if mode and not chat["captcha"]:
                problem = await perms.rights_problem(bot, chat_id, "can_restrict_members", "can_delete_messages")
                if problem:
                    await cb.answer(re.sub(r"<[^>]+>", "", problem)[:190], show_alert=True)
                    return
            await db.set_chat(chat_id, captcha=mode)
        elif op == "capmin":
            await db.set_chat(chat_id, captcha_min=cycle(CAPTCHA_MINUTES, chat["captcha_min"]))
        elif op == "capfail":
            await db.set_chat(chat_id, captcha_fail=cycle(CAPTCHA_FAILS, chat["captcha_fail"]))
        elif op == "test":
            await preview(cb, chat_id, "welcome")
            return
        fresh = await db.get_chat(chat_id)
        if welcome_mod.greets(fresh) != before:
            await welcome_mod.sync_baxi(fresh)
        await show(cb, *await welcome_screen(chat_id))

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
        if op == "add":
            if not await perms.has(bot, chat_id, user_id, perms.R_GRANT):
                await cb.answer("Назначать может админ или владелец.", show_alert=True)
                return
            await ask(cb, fsm, "grant", chat_id, "Кого назначить? Пришлите @username или ID человека из этого чата.")
            return
        if op == "rw" and arg.lstrip("-").isdigit():
            await db.remove_whitelist(chat_id, int(arg))
        await show(cb, *await trust_screen(bot, chat_id, user_id))

    elif scr == "rk" and op.isdigit():   # роль человека
        target = int(op)
        name = await db.member_name(chat_id, target) or str(target)
        if arg in (*perms.ROLES, "none"):
            from guard.handlers.moderation import give_role
            new = perms.NONE if arg == "none" else arg
            error = await give_role(bot, chat, user_id, out.mention(user_id, out.full_name(cb.from_user)), target,
                                    name, new)
            if error and "уже" not in error and "и так" not in error:
                await cb.answer(re.sub(r"<[^>]+>", "", error)[:190], show_alert=True)
                return
        await show(cb, *await role_card(bot, chat_id, target, name, user_id))

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

    elif scr == "tz":
        if not await perms.has(bot, chat_id, user_id, perms.R_TRIGGERS):
            await cb.answer("Это для модераторов и админов.", show_alert=True)
            return
        if op == "add":
            await ask(cb, fsm, "trig_word", chat_id, "Пришлите слово (или пару слов), на которое бот будет отвечать.")
            return
        if op == "del" and arg.isdigit():
            from guard import triggers
            items = {t["id"]: t for t in (await triggers.all_of(chat_id)).values()}
            if int(arg) in items:
                await triggers.delete(chat_id, items[int(arg)]["word"])
                await cb.answer("Удалил")
        await show(cb, *await triggers_screen(chat_id))

    elif scr == "mv":   # старые кнопки: теперь мастер переноса
        target = f"tr:d:{chat_id}:{op}" if op.lstrip("-").isdigit() else f"tr:s:{chat_id}"
        await show(cb, screen("🧰 Перенос", ["Перенос теперь по шагам: откуда, куда и что переносить."]),
                   kb(btn("➡️ Продолжить", target, accent=True), back(f"g:c:{chat_id}")))
    elif scr == "te":
        await show(cb, *await texts_home(chat_id))
    elif scr == "tg" and op.isdigit():
        await show(cb, *await texts_group(chat_id, int(op)))
    elif scr in ("ti", "tw", "tv", "tr"):
        from guard import texts
        if op not in texts.TEXTS:
            await cb.answer()
            return
        if scr == "ti":   # запомнить, откуда пришли
            if arg in TEXT_ORIGINS:
                _text_from[(user_id, chat_id, op)] = TEXT_ORIGINS[arg].format(chat=chat_id)
            else:
                _text_from.pop((user_id, chat_id, op), None)
            if len(_text_from) > 5000:
                _text_from.clear()
        if scr == "tv":
            await cb.answer()
            await preview(cb, chat_id, op)
            return
        if scr == "tr":
            await texts.reset(chat_id, op)
        elif scr == "tw":
            if arg == "nophoto":
                await texts.set_text(chat_id, op, photo="", by=user_id)
            elif arg == "nobuttons":
                await texts.set_text(chat_id, op, buttons="", by=user_id)
            elif arg in ("text", "photo", "buttons"):
                prompts = {"text": "Выше текущий текст: скопируйте, поправьте и пришлите одним сообщением. "
                                   "Премиум-эмодзи, жирный, курсив и ссылки сохранятся как есть. Подстановки в "
                                   "фигурных скобках бот заменит сам.",
                           "photo": "Пришлите картинку, видео или гифку. Встанет над текстом (подпись до 1024 символов, длиннее: "
                                    "картинка и текст отдельно).",
                           "buttons": BUTTONS_HELP + (BUTTONS_OWNER_HELP if perms.is_owner(user_id) else "")}
                await send_template(cb.message, chat_id, op, arg)
                await ask(cb, fsm, f"text:{op}:{arg}", chat_id, prompts[arg])
                return
        await show(cb, *await text_item(chat_id, op, user_id))
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
    if (kind or "").startswith("btn_icon:"):  # значок кнопки, см. admin.py
        from guard.handlers import admin
        await admin.on_button_icon(message, state, int(kind.split(":")[1]))
        return
    need = perms.R_GRANT if kind == "grant" else (perms.R_TRIGGERS if (kind or "").startswith("trig") else perms.R_SETTINGS)
    if chat_id == 0:
        allowed_here = perms.is_owner(message.from_user.id) and (kind or "").startswith("text:")
    else:
        allowed_here = bool(chat_id) and await perms.has(bot, chat_id, message.from_user.id, need)
    if not allowed_here:
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
    elif kind == "trig_word":
        from guard import triggers
        word = triggers.norm(message.text or "")
        if not word:
            await message.answer("Нужно слово. Пришлите ещё раз или /cancel")
            return
        await state.update_data(kind=f"trig_body:{word}")
        await message.answer(f"✏️ Теперь пришлите, чем отвечать на \"{out.esc(word)}\": текст (можно с премиум-эмодзи), "
                             f"фото, видео, гифку, стикер, голосовое или файл.\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
        return
    elif kind and kind.startswith("trig_body:"):
        from guard import triggers
        word = kind.split(":", 1)[1]
        body = triggers.content_of(message)
        if body is None:
            await message.answer("Такое сохранить не могу. Пришлите текст, фото, видео, гифку, стикер, голосовое "
                                 "или файл, или /cancel")
            return
        error = await triggers.save(chat_id, word, *body, message.from_user.id)
        if error:
            await message.answer(f"⚠️ {error}")
        else:
            await message.answer(f"✅ Триггер \"{out.esc(word)}\" сохранён")
        nxt = await triggers_screen(chat_id)
    elif kind and kind.startswith("text:"):
        from guard import texts
        _, key, what = kind.split(":")
        if what == "text":
            if not message.text:
                await message.answer("Нужен текст. Пришлите ещё раз или /cancel")
                return
            await texts.set_text(chat_id, key, html=message.html_text[:texts.MAX_HTML], by=message.from_user.id)
            note = "✅ Текст сохранён"
        elif what == "photo":
            media = texts.media_value(message)
            if not media:
                await message.answer("Нужна картинка, видео или гифка. Пришлите ещё раз или /cancel")
                return
            await texts.set_text(chat_id, key, photo=media, by=message.from_user.id)
            note = "✅ Сохранено"
        else:
            from guard import richtext
            owner = perms.is_owner(message.from_user.id)
            # у владельца премиум-эмодзи в начале кнопки становится значком, поэтому берём HTML с их ID
            raw = (html_lib.unescape(re.sub(r"<[^>]+>", "", richtext.to_web(message.html_text)))
                   if owner and message.text else (message.text or ""))
            rows_, bad = texts.parse_buttons(raw, design=owner)
            if not rows_:
                await message.answer("Не нашёл ни одной кнопки.\n\n" + BUTTONS_HELP)
                return
            await texts.set_text(chat_id, key, buttons=texts.buttons_text(rows_), by=message.from_user.id)
            note = f"✅ Кнопок: {sum(len(r) for r in rows_)}"
            if bad:
                note += f"\nНе понял: {out.esc(', '.join(bad)[:200])}"
        await message.answer(note)
        nxt = await text_item(chat_id, key, message.from_user.id)
    elif kind == "grant":
        raw = (message.text or "").strip()
        row = await db.find_by_username(chat_id, raw) if raw.startswith("@") else \
            (await db.get_member(chat_id, int(raw)) if raw.isdigit() else None)
        if row is None:
            await message.answer("Не нашёл такого человека: я знаю только тех, кто писал в чате при мне. "
                                 "Пришлите @username или ID ещё раз, или /cancel")
            return
        await state.clear()
        text, markup = await role_card(bot, chat_id, row["user_id"], row["name"] or raw, message.from_user.id)
        await message.answer(text, reply_markup=markup)
        return
    elif kind == "log":
        error = await set_log_chat(bot, message, chat_id)
        if error:
            await message.answer(out.head(error + "\nОтменить: /cancel"))
            return
        await message.answer("✅ Журнал подключён")
        nxt = await log_screen(chat_id)
    await state.clear()
    if nxt:
        if in_group(message):   # настройки, открытые в чате: без «Моих чатов», и сами исчезнут
            await out.answer(message, nxt[0], ttl=GROUP_TTL, markup=for_group(nxt[1], message.chat.id))
        else:
            await message.answer(nxt[0], reply_markup=nxt[1], link_preview_options=out.NO_PREVIEW)


# ---------- ввод в чате ----------

@group_router.message(Command("cancel"))
async def group_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    out.delete_later(message.bot, message.chat.id, message.message_id, 5)
    await out.answer(message, out.head("отменено."), ttl=10)


@group_router.message()
async def group_input(message: Message, state: FSMContext, bot: Bot) -> None:
    """Админ открыл настройки в чате и нажал «✏️ ...»: его следующее сообщение в этом чате и есть ввод."""
    data = await state.get_data()
    if data.get("chat_id") != message.chat.id:
        await state.clear()
        raise SkipHandler()
    await on_input(message, state, bot)
