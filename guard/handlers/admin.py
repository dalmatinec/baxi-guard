"""/admin владельца бота: сводка, все чаты, здоровье бота, своя подписка чатов, премиум-эмодзи,
🎨 цвет и значок любой кнопки бота, ✉️ тексты бота для всех чатов."""

import json
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import access, buttons, config, db, health, out, perms
from guard.ui import DIV_SHORT, back, btn, chat_title, grid, kb, row, screen

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

STAT_NAMES = [("mute", "🔇"), ("warn", "⚠️"), ("ban", "⛔"), ("kick", "👢"), ("spam", "🧹"), ("delete", "🗑")]
EMOJI_KEY = "emoji_pack"
P_ADMIN = "🛠 Админка"


def stats_line(st: dict) -> str:
    return " · ".join(f"{icon} {st.get(k, 0)}" for k, icon in STAT_NAMES)


async def home() -> tuple[str, InlineKeyboardMarkup]:
    chats = await db.all_chats()
    allowed = 0
    for c in chats:
        allowed += await access.allowed(c)
    st = await db.stats(None, db.now() - 86400)
    h = await health.snapshot()
    if config.AI_API_KEY:
        used = await db.kv_get("ai_used:" + datetime.now(config.TZ).strftime("%Y-%m-%d"), "0")
        ai = f"{used} из {config.AI_DAILY_LIMIT} сегодня"
    else:
        ai = "нет ключа"
    rule = "нужен оплаченный Бакси или своя подписка" if config.GUARD_NEEDS_BAXI_PAID else "работает везде"
    lines = [row("💬", "Чатов", str(len(chats)), f"работает в {allowed}"),
             row("📊", "За сутки", stats_line(st)),
             "",
             "<b>Здоровье</b>",
             row("⏱", "Работает", h["uptime"]),
             row("🧠", "Память", f"{h['rss']:.0f} МБ", f"база {h['db']:.1f} МБ"),
             row("🔌", "API Бакси", h.get("baxi", "не настроен"), f"очередь {h['outbox']}"),
             row("📡", "Свой API", h["api"]),
             row("🤖", "Нейросеть", ai),
             row("💳", "Правило", rule),
             row("✨", "Премиум-эмодзи", str(len(out.PACK))),
             row("🎨", "Кнопки", f"своё оформление у {len(buttons.changed_rows())}")]
    return (screen(P_ADMIN, lines),
            kb([btn("💬 Все чаты", "a:chats:0", accent=True), btn("✨ Эмодзи", "a:emoji")],
               [btn("🎨 Кнопки", "a:btn"), btn("✉️ Тексты бота", "g:te:0")],
               btn("🔄 Обновить", "a:home:0")))


async def chats_list() -> tuple[str, InlineKeyboardMarkup]:
    chats = await db.all_chats()
    counts = {r["chat_id"]: r["n"] for r in await db.active_chats_since(db.now() - 86400)}
    lines, buttons = [], []
    for c in chats[:60]:
        mark = "🟢" if await access.allowed(c) else "⚪"
        lines.append(f"{mark} {chat_title(c)} · действий за сутки {counts.get(c['chat_id'], 0)}")
        buttons.append(btn(f"{mark} {(c['title'] or str(c['chat_id']))[:30]}", f"g:c:{c['chat_id']}"))
    return (screen("💬 Все чаты", lines or ["пока ни одного"], path=P_ADMIN, note="🟢 работает · ⚪ не активен"),
            kb(*grid(buttons, 1), back("a:home:0")))


def own_label(chat: dict) -> str:
    until = chat["own_until"]
    if until == -1:
        return "навсегда"
    if until > db.now():
        return "до " + datetime.fromtimestamp(until, config.TZ).strftime("%d.%m.%Y")
    return "нет"


async def sub_screen(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.ensure_chat(chat_id)
    _ok, mode = await access.status(chat)
    lines = [row("🔑", "Своя подписка", own_label(chat)), row("⚡", "Режим", out.esc(mode))]
    text = screen("🔑 Своя подписка", lines, path=f"⚙️ {chat_title(chat)}",
                  note="Нужна, только если в .env стоит GUARD_NEEDS_BAXI_PAID=1: тогда модератор работает там, "
                       "где Бакси оплачен или на пробном, и в чатах со своей подпиской.")
    return text, kb([btn("+30 дней", f"a:sub:{chat_id}:30", accent=True), btn("Навсегда", f"a:sub:{chat_id}:inf")],
                    btn("Снять", f"a:sub:{chat_id}:off"), back(f"g:in:{chat_id}"))


# ---------- премиум-эмодзи ----------

async def load_pack() -> None:
    try:
        pairs = json.loads(await db.kv_get(EMOJI_KEY, "[]") or "[]")
    except ValueError:
        pairs = []
    out.set_pack([(str(i), str(a)) for i, a in pairs])


async def save_pack(pairs: list[tuple[str, str]]) -> None:
    await db.kv_set(EMOJI_KEY, json.dumps(pairs, ensure_ascii=False))
    out.set_pack(pairs)


def emoji_screen() -> tuple[str, InlineKeyboardMarkup]:
    shown = " ".join(out.PACK) or "пока нет"
    text = screen("✨ Премиум-эмодзи", [row("✨", "В наборе", str(len(out.PACK))), "", shown], path=P_ADMIN,
                  note="Пришлите сообщение с премиум-эмодзи: каждый заменит свой обычный двойник во всех "
                       "сообщениях и на кнопках бота (например, 🛡 в заголовках). Нужен Telegram Premium "
                       "у владельца бота.")
    return text, kb([btn("➕ Добавить", "a:emoji:add", accent=True), btn("🗑 Очистить", "a:emoji:clr")],
                    back("a:home:0"))


def extract_custom(message: Message) -> list[tuple[str, str]]:
    """Премиум-эмодзи из сообщения: [(id, обычный эмодзи)]. Смещения Telegram считает в UTF-16."""
    text = message.text or message.caption or ""
    raw = text.encode("utf-16-le")
    found = []
    for ent in message.entities or message.caption_entities or []:
        if ent.type == "custom_emoji" and ent.custom_emoji_id:
            alt = raw[ent.offset * 2:(ent.offset + ent.length) * 2].decode("utf-16-le", "ignore")
            if alt:
                found.append((ent.custom_emoji_id, alt))
    return found


async def on_emoji_input(message: Message, state: FSMContext) -> None:
    if not perms.is_owner(message.from_user.id):
        await state.clear()
        return
    found = extract_custom(message)
    if not found:
        await message.answer("В сообщении нет премиум-эмодзи. Пришлите их ещё раз или /cancel")
        return
    pack = dict(out.PACK)
    for emoji_id, alt in found:
        pack[alt] = emoji_id
    await save_pack([(i, a) for a, i in pack.items()])
    await state.clear()
    await message.answer(f"✅ Добавлено: {len(found)}")
    text, markup = emoji_screen()
    await message.answer(text, reply_markup=markup)


# ---------- 🎨 кнопки ----------

PAGE = 20
COLOR_ICONS = {"primary": "🔵", "success": "🟢", "danger": "🔴", "plain": "⚪", "": "▫️"}
COLOR_NAMES = {"primary": "синяя", "success": "зелёная", "danger": "красная", "plain": "без цвета", "": "как в коде"}


def icon_html(icon: str) -> str:
    return f'<tg-emoji emoji-id="{icon}">⭐</tg-emoji>' if icon else ""


def button_label(r: dict) -> str:
    return f"{COLOR_ICONS.get(r['style'], '▫️')}{' ✨' if r['icon'] else ''} {r['sample'] or r['pattern']}"[:60]


def buttons_home() -> tuple[str, InlineKeyboardMarkup]:
    rows = buttons.changed_rows()
    lines = [row("🔘", "Видов кнопок", str(len(buttons.all_rows()))), row("🎨", "Своё оформление", str(len(rows)))]
    if rows:
        lines += ["", *[f"{icon_html(r['icon'])} {out.esc(button_label(r))}" for r in rows[:15]]]
    text = screen("🎨 Кнопки", lines, path=P_ADMIN,
                  note="Цвет и премиум-значок любой кнопки бота, во всех чатах и в личке. Менять можете только вы. "
                       "Проще всего: «🎯 Выбрать нажатием» и нажмите нужную кнопку где угодно в боте.")
    picks = [btn(button_label(r), f"bb:{r['id']}") for r in rows[:15]]
    return text, kb([btn("🎯 Выбрать нажатием", "a:btn:pick", accent=True), btn("📋 Все кнопки", "a:btn:all:0")],
                    *grid(picks, 1), back("a:home:0"))


def buttons_all(page: int) -> tuple[str, InlineKeyboardMarkup]:
    rows = buttons.all_rows()
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * PAGE:(page + 1) * PAGE]
    text = screen("📋 Все кнопки", [f"Страница {page + 1} из {pages}"], path="🎨 Кнопки",
                  note="Здесь кнопки, которые бот уже показывал. Новые появятся сами, когда бот их отправит.")
    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"a:btn:all:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("➡️", f"a:btn:all:{page + 1}"))
    return text, kb(*grid([btn(button_label(r), f"bb:{r['id']}") for r in chunk], 1), nav, back("a:btn"))


def button_screen(r: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [row("🏷", "Подпись", out.esc(r["sample"])),
             row("📍", "Где", f"<code>{out.esc(r['pattern'])}</code>"),
             row("🎨", "Цвет", f"{COLOR_ICONS.get(r['style'], '▫️')} {COLOR_NAMES.get(r['style'], r['style'])}"),
             row("✨", "Значок", icon_html(r["icon"]) or "нет")]
    text = screen("🔘 Кнопка", lines, path="🎨 Кнопки",
                  note="Настройка действует на эту кнопку везде: во всех чатах и экранах, где она есть. "
                       "* в «Где» значит любой чат или номер.")
    i = r["id"]
    return text, kb(
        [btn("Синяя", f"bb:{i}:s:primary").model_copy(update={"style": "primary"}),
         btn("Зелёная", f"bb:{i}:s:success").model_copy(update={"style": "success"}),
         btn("Красная", f"bb:{i}:s:danger").model_copy(update={"style": "danger"})],
        [btn("⚪ Без цвета", f"bb:{i}:s:plain"), btn("↩️ Как в коде", f"bb:{i}:s:-")],
        [btn("✨ Значок", f"bb:{i}:i"), btn("🗑 Без значка", f"bb:{i}:i:-")],
        [btn("👁 Пример", f"bb:{i}:demo"), btn("🎯 Выбрать другую", "a:btn:pick")],
        back("a:btn"))


async def on_button_icon(message: Message, state: FSMContext, button_id: int) -> None:
    if not perms.is_owner(message.from_user.id):
        await state.clear()
        return
    found = extract_custom(message)
    if not found:
        await message.answer("Нужен премиум-эмодзи. Пришлите его ещё раз или /cancel")
        return
    r = await buttons.set_design(button_id, icon=found[0][0])
    await state.clear()
    if r:
        text, markup = button_screen(r)
        await message.answer("✅ Значок поставлен")
        await message.answer(text, reply_markup=markup)


async def on_bb(cb: CallbackQuery, state: FSMContext) -> tuple[str, InlineKeyboardMarkup] | None:
    """bb:<id>[:s:<цвет>|:i[:-]|:demo]"""
    parts = cb.data.split(":")
    if parts[1] == "none":
        await cb.answer()
        return None
    r = await buttons.get(int(parts[1])) if parts[1].isdigit() else None
    if r is None:
        await cb.answer("Кнопка не найдена", show_alert=True)
        return None
    op = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    if op == "s":
        r = await buttons.set_design(r["id"], style="" if arg == "-" else arg)
    elif op == "i" and arg == "-":
        r = await buttons.set_design(r["id"], icon="")
    elif op == "i":
        from guard.handlers.settings import Input
        await state.set_state(Input.waiting)
        await state.update_data(kind=f"btn_icon:{r['id']}", chat_id=0)
        await cb.message.answer(f"✏️ Пришлите премиум-эмодзи для значка кнопки\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
        await cb.answer()
        return None
    elif op == "demo":
        sample = buttons.apply(btn(r["sample"] or "Кнопка", r["pattern"].replace("*", "0")))
        await cb.message.answer(f"Так кнопка выглядит сейчас:\n{DIV_SHORT}",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                    sample.model_copy(update={"callback_data": "bb:none"})]]))
        await cb.answer()
        return None
    return button_screen(r)


# ---------- обработчики ----------

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not perms.is_owner(message.from_user.id):
        return
    text, markup = await home()
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith(("a:", "bb:")))
async def on_admin(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if not perms.is_owner(cb.from_user.id):
        await cb.answer()
        return
    parts = cb.data.split(":")
    scr = parts[1]
    if parts[0] == "bb":
        res = await on_bb(cb, state)
        if res is None:
            return
        text, markup = res
    elif scr == "btn":
        op = parts[2] if len(parts) > 2 else ""
        if op == "pick":
            if cb.from_user.id in buttons.PICKING:
                buttons.PICKING.discard(cb.from_user.id)
                await cb.answer("Выбор отменён")
                return
            buttons.PICKING.add(cb.from_user.id)
            await cb.message.answer("🎯 Нажмите любую кнопку бота: в личке или в чате. Вместо действия откроется "
                                    "её настройка. Передумали: нажмите «🎯 Выбрать нажатием» ещё раз.")
            await cb.answer("Жду нажатия")
            return
        text, markup = buttons_all(int(parts[3]) if op == "all" and len(parts) > 3 else 0) if op == "all" \
            else buttons_home()
    elif scr == "home":
        text, markup = await home()
    elif scr == "chats":
        text, markup = await chats_list()
    elif scr == "emoji":
        op = parts[2] if len(parts) > 2 else ""
        if op == "add":
            from guard.handlers.settings import Input
            await state.set_state(Input.waiting)
            await state.update_data(kind="emoji", chat_id=0)
            await cb.message.answer(f"✏️ Пришлите сообщение с премиум-эмодзи\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
            await cb.answer()
            return
        if op == "clr":
            await save_pack([])
        text, markup = emoji_screen()
    elif scr == "sub":
        chat_id = int(parts[2])
        op = parts[3] if len(parts) > 3 else ""
        chat = await db.ensure_chat(chat_id)
        if op == "30":
            base = max(db.now(), chat["own_until"]) if chat["own_until"] != -1 else db.now()
            await db.set_chat(chat_id, own_until=base + 30 * 86400)
        elif op == "inf":
            await db.set_chat(chat_id, own_until=-1)
        elif op == "off":
            await db.set_chat(chat_id, own_until=0)
        text, markup = await sub_screen(chat_id)
    else:
        await cb.answer()
        return
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()
