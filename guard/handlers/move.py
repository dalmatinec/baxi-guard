"""В личке: 🧰 перенос чата (мастер по шагам) и 👑 владелец чата (заместитель, запрос прав, ответ владельца).

Перенос работает и из чата, где бота уже нет: старый чат удалили, заблокировали или бота убрали. Тогда права
проверяются по тому, что бот запомнил: последний известный создатель, владелец в боте, роли из базы."""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import baxi, db, owners, out, perms, transfer
from guard.ui import DIV_SHORT, back, btn, chat_title, grid, kb, row, screen

log = logging.getLogger(__name__)
router = Router(name="move")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

P_MOVE, P_OWNER = "🧰 Перенос чата", "👑 Владелец чата"
DEFAULT = transfer.ROLES | transfer.HEIR | transfer.WARNS | transfer.BANS | transfer.ACTIVITY | transfer.TARIFF \
    | transfer.BAXI


# откуда пришли в мастер переноса: «Назад» с первого шага возвращает туда (О чате или главное меню)
_move_from: dict[int, str] = {}


class OwnerInput(StatesGroup):
    heir = State()   # в data: chat_id


async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup, link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()


def status_mark(chat: dict) -> str:
    if chat["gone_at"]:
        return "🚫"
    if chat["moved_to"]:
        return "📦"
    return "🟢"


# ---------- права на чат, в том числе недоступный ----------

async def src_flags(bot: Bot, chat: dict, user_id: int) -> int:
    """Права человека в чате-источнике. Чат недоступен: по памяти бота."""
    if not chat["gone_at"]:
        live = await perms.flags(bot, chat["chat_id"], user_id)
        if live:
            return live
        if await owners.refresh(bot, chat["chat_id"]) != "gone":
            return live
        chat = await db.get_chat(chat["chat_id"])
    return perms.offline_flags(chat, user_id, await db.rights_of(chat["chat_id"], user_id))


async def sources(bot: Bot, user_id: int) -> list[dict]:
    """Откуда можно переносить: чаты, где есть ⚙️ (живые и недоступные)."""
    sem = asyncio.Semaphore(10)

    async def one(c: dict):
        async with sem:
            return c if await src_flags(bot, c, user_id) & perms.R_SETTINGS else None
    chats = await db.all_chats()
    if not perms.is_owner(user_id):
        # недоступные чаты проверяются по памяти и без запросов, живые через Telegram
        chats = chats[:300]
    return [c for c in await asyncio.gather(*(one(c) for c in chats)) if c]


async def targets(bot: Bot, user_id: int, src: int) -> list[dict]:
    """Куда: живые чаты, где у человека ⚙️, кроме источника."""
    sem = asyncio.Semaphore(10)

    async def one(c: dict):
        async with sem:
            return c if await perms.can_configure(bot, c["chat_id"], user_id) else None
    chats = [c for c in await db.all_chats() if c["chat_id"] != src and not c["gone_at"]]
    return [c for c in await asyncio.gather(*(one(c) for c in chats[:300])) if c]


# ---------- 🧰 мастер переноса ----------

async def home_screen(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chats = await sources(bot, user_id)
    lines = ["Шаг 1 из 3. <b>Откуда</b> переносим?", "",
             "🟢 бот в чате · 🚫 бота убрали или чат удалён · 📦 уже переносили"]
    if not chats:
        lines = ["Нет чатов, где вы владелец или админ бота."]
    buttons = [btn(f"{status_mark(c)} {(c['title'] or str(c['chat_id']))[:34]}", f"tr:s:{c['chat_id']}")
               for c in chats[:40]]
    return (screen(P_MOVE, lines, note="Если старый чат удалили или заблокировали, он тоже здесь: бот помнит его "
                                       "настройки, роли и тариф."),
            kb(*grid(buttons, 1), back("g:home:0")))


async def dst_screen(bot: Bot, user_id: int, src: dict) -> tuple[str, InlineKeyboardMarkup]:
    back_to = _move_from.get(user_id) or "tr:home"
    from guard.handlers.settings import add_url
    found = await targets(bot, user_id, src["chat_id"])
    lines = [f"Из: {chat_title(src)}", "", "Шаг 2 из 3. <b>Куда</b> переносим?"]
    if not found:
        lines += ["", "Сначала добавьте бота в новый чат администратором, потом нажмите «Проверить снова»."]
    buttons = [btn(f"➡️ {(c['title'] or str(c['chat_id']))[:34]}", f"tr:d:{src['chat_id']}:{c['chat_id']}")
               for c in found[:40]]
    return (screen(P_MOVE, lines),
            kb(*grid(buttons, 1), [btn("➕ Добавить в новый чат", url=await add_url(bot)),
                                   btn("🔄 Проверить снова", f"tr:s:{src['chat_id']}")], back(back_to)))


async def available(bot: Bot, src: dict, user_id: int) -> dict[int, str]:
    """Какие пункты можно выбрать. Значение: пояснение, почему нельзя (пусто: можно)."""
    why: dict[int, str] = {bit: "" for bit, _ in transfer.OPTIONS}
    if not src["heir_id"]:
        why[transfer.HEIR] = "не назначен"
    if not src["own_until"]:
        why[transfer.TARIFF] = "своей подписки нет"
    elif not await owners.is_bot_owner_of(bot, src, user_id):
        why[transfer.TARIFF] = "переносит только владелец"
    else:
        locked = await transfer.tariff_locked(src["chat_id"])
        if locked:
            why[transfer.TARIFF] = f"пришёл переносом, дальше с {owners.date(locked)[:10]}"
    if not baxi.configured():
        why[transfer.BAXI] = "связи с Бакси нет"
    return why


async def options_screen(bot: Bot, user_id: int, src: dict, dst: dict, mask: int) -> tuple[str, InlineKeyboardMarkup]:
    why = await available(bot, src, user_id)
    lines = [f"Из: {chat_title(src)}", f"В: {chat_title(dst)}", "", "Шаг 3 из 3. <b>Что переносим</b>",
             "✅ Настройки, тексты, триггеры, стоп-слова, домены, топы: всегда"]
    buttons = []
    s, d = src["chat_id"], dst["chat_id"]
    for bit, name in transfer.OPTIONS:
        if why[bit]:
            lines.append(f"▫️ {name}: {why[bit]}")
            continue
        on = bool(mask & bit)
        lines.append(f"{'✅' if on else '▫️'} {name}")
        buttons.append(btn(f"{'✅' if on else '▫️'} {name}", f"tr:o:{s}:{d}:{mask ^ bit}"))
    if mask & transfer.BANS and not why[transfer.BANS]:
        n = len(await transfer.banned_ids(s))
        lines.append(f"<i>Бан-лист: {n} чел., бот забанит их в новом чате.</i>")
    return (screen(P_MOVE, lines, note="В новом чате настройки и тексты заменятся. Тариф уходит из старого чата. "
                                       "Журнал действий остаётся у старого."),
            kb(*grid(buttons, 1), btn("➡️ Перенести", f"tr:c:{s}:{d}:{mask}", accent=True),
               back(f"tr:s:{s}")))


async def run_bans(bot: Bot, user_id: int, src: int, dst: int, ids: list[int]) -> None:
    done = 0
    for uid in ids:
        try:
            await bot.ban_chat_member(dst, uid)
            await db.log_action(dst, uid, bot.id, "ban", 0, "бан-лист перенесён из старого чата")
            done += 1
        except Exception:
            pass   # человек уже удалил аккаунт или бот без права банить
        await asyncio.sleep(0.15)
    try:
        await bot.send_message(user_id, f"⛔ Бан-лист перенесён: забанено {done} из {len(ids)}.")
    except Exception:
        pass


async def execute(cb: CallbackQuery, bot: Bot, src: dict, dst: dict, mask: int) -> None:
    why = await available(bot, src, cb.from_user.id)
    mask &= ~sum(bit for bit, reason in why.items() if reason)
    s, d = src["chat_id"], dst["chat_id"]
    counts = await transfer.copy_chat(s, d, mask & ~transfer.BANS & ~transfer.BAXI)
    extra = []
    if mask & transfer.BAXI:
        res = await baxi.move_chat(s, d, cb.from_user.id)
        if res.get("ok"):
            extra.append("🤝 Бакси: аренда, репутация и память о людях перенесены")
        else:
            reasons = {"no_dst": "Бакси ещё нет в новом чате, добавьте его и повторите перенос",
                       "no_src": "у Бакси нет данных старого чата", "down": "Бакси не ответил, повторите позже"}
            extra.append(f"🤝 Бакси: {reasons.get(res.get('error'), 'не перенёс')}")
    if mask & transfer.BANS:
        ids = await transfer.banned_ids(s)
        if ids:
            out.spawn(run_bans(bot, cb.from_user.id, s, d, ids))
            extra.append(f"⛔ Бан-лист: {len(ids)} чел., баню в фоне, напишу, когда закончу")
    from guard import support
    await support.notify_devs(bot, f"📦 Перенос чата: {chat_title(src)} <code>{s}</code> → {chat_title(dst)} "
                                   f"<code>{d}</code>\nПеренёс: {out.mention(cb.from_user.id, out.full_name(cb.from_user))} "
                                   f"<code>{cb.from_user.id}</code>")
    from guard import commands
    out.spawn(commands.refresh_chat(bot, d))
    lines = [f"Из: {chat_title(src)}", f"В: {chat_title(dst)}", "", transfer.summary(counts)]
    if extra:
        lines += ["", *extra]
    await show(cb, screen("✅ Перенесено", lines),
               kb(btn("⚙️ Открыть новый чат", f"g:c:{d}", accent=True), back("g:home:0", "⬅️ В начало")))


@router.callback_query(F.data.startswith("tr:"))
async def on_move(cb: CallbackQuery, bot: Bot) -> None:
    parts = cb.data.split(":")
    op = parts[1]
    uid = cb.from_user.id
    if op == "home":
        _move_from.pop(uid, None)
        await show(cb, *await home_screen(bot, uid))
        return
    try:
        s = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    src = await db.get_chat(s)
    if src is None or not await src_flags(bot, src, uid) & perms.R_SETTINGS:
        await cb.answer("В этом чате у вас нет права ⚙️ настройки.", show_alert=True)
        return
    if op == "s":
        if len(parts) > 3 and parts[3] == "in":   # пришли из «О чате»
            _move_from[uid] = f"g:in:{s}"
        await show(cb, *await dst_screen(bot, uid, src))
        return
    try:
        d = int(parts[3])
    except (IndexError, ValueError):
        await cb.answer()
        return
    dst = await db.get_chat(d)
    if dst is None or d == s or dst["gone_at"] or not await perms.can_configure(bot, d, uid):
        await cb.answer("В новом чате нужно право ⚙️ настройки, и бот должен быть там.", show_alert=True)
        return
    mask = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else DEFAULT
    mask &= transfer.ALL
    if op in ("d", "o"):
        await show(cb, *await options_screen(bot, uid, src, dst, mask))
    elif op == "c":
        names = [name for bit, name in transfer.OPTIONS if mask & bit and not (await available(bot, src, uid))[bit]]
        await show(cb, screen("🧰 Перенести?", [f"Из: {chat_title(src)}", f"В: {chat_title(dst)}", "",
                                               "Настройки, тексты, триггеры", *names]),
                   kb([btn("✅ Перенести", f"tr:go:{s}:{d}:{mask}", accent=True), btn("Отмена", f"tr:d:{s}:{d}:{mask}")]))
    elif op == "go":
        await cb.answer("Переношу…")
        await execute(cb, bot, src, dst, mask)
    else:
        await cb.answer()


# ---------- 👑 владелец чата ----------

async def owner_screen(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await db.get_chat(chat_id)
    if not chat["gone_at"]:
        await owners.refresh(bot, chat_id)
        chat = await db.get_chat(chat_id)

    async def name(uid: int) -> str:
        return out.mention(uid, await db.member_name(chat_id, uid) or str(uid)) if uid else "нет"
    creator = await name(chat["owner_id"]) if chat["owner_id"] else "не известен"
    if chat["owner_lost"]:
        creator += f" · <i>нет в чате с {owners.date(chat['owner_lost'])[:10]}</i>"
    lines = [row("👤", "Создатель в Telegram", creator),
             row("👑", "Владелец в боте", await name(chat["owner_override"]) if chat["owner_override"]
                 else "создатель"),
             row("🛟", "Заместитель", await name(chat["heir_id"]))]
    claim = await owners.get_claim(chat_id)
    if claim:
        lines += ["", f"⏳ Заместитель запросил права, перейдут {owners.date(claim['deadline'])}"]
    is_owner = await owners.is_bot_owner_of(bot, chat, user_id)
    rows = []
    if is_owner:
        rows.append(btn("🛟 Назначить заместителя" if not chat["heir_id"] else "🛟 Сменить заместителя",
                        f"ow:h:{chat_id}", accent=True))
        if chat["heir_id"]:
            rows.append(btn("🗑 Убрать заместителя", f"ow:hx:{chat_id}"))
        if claim:
            rows.append(btn("✋ Отклонить запрос", f"ow:no:{chat_id}"))
        if chat["owner_override"] and await perms.is_creator(bot, chat_id, user_id) \
                and user_id != chat["owner_override"]:
            rows.append(btn("↩️ Вернуть права создателю", f"ow:ox:{chat_id}"))
    text = screen(P_OWNER, lines, path=f"⚙️ {chat_title(chat)}",
                  note="Заместитель на случай, если аккаунт владельца удалят, заблокируют или он потеряется. "
                       "Заместитель запрашивает права в личке бота, владельцу и чату приходит уведомление; "
                       "если за 72 часа никто не отклонит, он становится владельцем в боте (если владельца в чате "
                       "уже нет, через сутки). Нет заместителя: напишите разработчику через «🆘 Поддержка».")
    return text, kb(*rows, back(f"g:c:{chat_id}"))


async def heir_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chats = await owners.heir_chats(user_id)
    lines = ["Вы заместитель владельца в этих чатах. Если владелец потерял аккаунт, запросите права: "
             "владельцу и чату придёт уведомление, и если никто не отклонит, права перейдут к вам."]
    buttons = [btn(f"👑 {(c['title'] or str(c['chat_id']))[:34]}", f"ow:cl:{c['chat_id']}") for c in chats[:30]]
    return screen("🔑 Права владельца", lines), kb(*grid(buttons, 1), back("g:home:0"))


@router.callback_query(F.data.startswith("ow:"))
async def on_owner(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    parts = cb.data.split(":")
    op = parts[1]
    uid = cb.from_user.id
    if op == "me":
        await show(cb, *await heir_screen(uid))
        return
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    chat = await db.get_chat(chat_id)
    if chat is None:
        await cb.answer()
        return
    if op in ("cl", "CL"):   # заместитель
        if chat["heir_id"] != uid:
            await cb.answer("Вы не заместитель в этом чате.", show_alert=True)
            return
        if op == "cl":
            await show(cb, screen("👑 Запросить права владельца?", [
                f"Чат: {chat_title(chat)}", "",
                "Владельцу придёт уведомление в личку бота, в чате появится сообщение. Если никто не отклонит, "
                "вы станете владельцем в боте: 72 часа, если вы админ чата; неделя, если просто участник; "
                "сутки, если создателя в чате уже нет."]),
                kb([btn("✅ Запросить", f"ow:CL:{chat_id}", accent=True), btn("Отмена", "ow:me")]))
            return
        ok, text = await owners.claim(bot, chat_id, uid)
        await show(cb, screen("👑 Права владельца", [("✅ " if ok else "⚠️ ") + text]), kb(back("ow:me")))
        return
    owner_level = await owners.is_bot_owner_of(bot, chat, uid) or uid == chat["owner_id"]
    if op in ("no", "yes"):   # ответ владельца на запрос
        if not owner_level:
            await cb.answer("Отвечает только владелец чата.", show_alert=True)
            return
        claim = await owners.get_claim(chat_id)
        if claim is None:
            await cb.answer("Запроса уже нет.", show_alert=True)
            return
        if op == "no":
            await owners.decline(bot, chat_id)
            await show(cb, screen("✋ Запрос отклонён", [f"Чат: {chat_title(chat)}",
                                                        "Права остаются у вас. Повторить запрос можно через неделю."]),
                       kb(back("g:home:0", "⬅️ В начало")))
        else:
            if not await owners.is_bot_owner_of(bot, chat, uid):
                await cb.answer("Передать сейчас может только создатель чата.", show_alert=True)
                return
            await owners.grant(bot, chat_id, claim["user_id"], "владелец передал права сам")
            await show(cb, screen("✅ Права переданы", [f"Чат: {chat_title(chat)}"]), kb(back("g:home:0", "⬅️ В начало")))
        return
    if op == "c":
        if not await perms.flags(bot, chat_id, uid) & perms.R_SETTINGS and not owner_level:
            await cb.answer("Это для владельца и админов бота.", show_alert=True)
            return
        await state.clear()
        await show(cb, *await owner_screen(bot, chat_id, uid))
        return
    if not await owners.is_bot_owner_of(bot, chat, uid):
        await cb.answer("Это может только владелец чата.", show_alert=True)
        return
    if op == "h":
        await state.set_state(OwnerInput.heir)
        await state.update_data(chat_id=chat_id)
        await cb.message.answer(f"🛟 Кого назначить заместителем? Пришлите @username или ID человека из этого чата "
                                f"или перешлите его сообщение.\n{DIV_SHORT}\n<i>Отменить: /cancel</i>")
        await cb.answer()
    elif op == "hx":
        await owners.set_heir(chat_id, 0)
        await show(cb, *await owner_screen(bot, chat_id, uid))
    elif op == "ox":
        await owners.set_override(chat_id, 0)
        await show(cb, *await owner_screen(bot, chat_id, uid))
    else:
        await cb.answer()


@router.message(StateFilter(OwnerInput.heir), ~F.text.startswith("/"))
async def on_heir(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or 0)
    chat = await db.get_chat(chat_id)
    if chat is None or not await owners.is_bot_owner_of(bot, chat, message.from_user.id):
        await state.clear()
        return
    target = 0
    origin = message.forward_origin
    if origin is not None and getattr(origin, "sender_user", None):
        target = origin.sender_user.id
    elif message.text:
        word = message.text.strip().split()[0]
        if word.lstrip("-").isdigit():
            target = int(word)
        else:
            m = await db.find_by_username(chat_id, word)
            target = m["user_id"] if m else 0
    if not target:
        await message.answer("Не нашёл этого человека. Пришлите ID или @username того, кто писал в чате, или /cancel")
        return
    if target == message.from_user.id or target == bot.id:
        await message.answer("Заместителем должен быть другой человек. Пришлите ещё раз или /cancel")
        return
    if not chat["gone_at"]:
        m = await perms.member(bot, chat_id, target)
        if m is None or m.status in ("left", "kicked") or m.user.is_bot:
            await message.answer("Этот человек не участник чата. Пришлите ещё раз или /cancel")
            return
    await owners.set_heir(chat_id, target)
    await state.clear()
    try:
        await bot.send_message(target, f"🛟 Вас назначили заместителем владельца в чате {chat_title(chat)}. Если "
                                       "владелец потеряет аккаунт, запросите права в «🔑 Права владельца».")
    except Exception:
        pass
    text, markup = await owner_screen(bot, chat_id, message.from_user.id)
    await message.answer("✅ Заместитель назначен\n\n" + text, reply_markup=markup)
