"""/admin владельца бота: все чаты, сводка действий за сутки, здоровье бота, своя подписка чатов."""

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from guard import access, config, db, health, out, perms
from guard.ui import btn, kb

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

STAT_NAMES = [("mute", "мутов"), ("warn", "предупреждений"), ("ban", "банов"), ("kick", "киков"),
              ("spam", "спама удалено"), ("delete", "удалено руками")]


def stats_line(st: dict) -> str:
    parts = [f"{name} {st.get(k, 0)}" for k, name in STAT_NAMES]
    return ", ".join(parts)


async def home() -> tuple[str, InlineKeyboardMarkup]:
    chats = await db.all_chats()
    allowed = 0
    for c in chats:
        allowed += await access.allowed(c)
    st = await db.stats(None, db.now() - 86400)
    h = await health.snapshot()
    ai = "нет ключа" if not config.AI_API_KEY else \
        f"сегодня {await db.kv_get('ai_used:' + datetime.now(config.TZ).strftime('%Y-%m-%d'), '0')} из " \
        f"{config.AI_DAILY_LIMIT}"
    baxi_state = h.get("baxi", "не настроен")
    text = (f"🛠 <b>Админка модератора</b>\n\n"
            f"Чатов: {len(chats)}, работает в {allowed}\n"
            f"За сутки: {stats_line(st)}\n\n"
            f"<b>Здоровье</b>\nработает {h['uptime']}, память {h['rss']:.0f} МБ, база {h['db']:.1f} МБ\n"
            f"API Бакси: {baxi_state}, в очереди событий: {h['outbox']}\n"
            f"Нейросеть: {ai}\n"
            f"Правило оплаты: {'нужен оплаченный Бакси или своя подписка' if config.GUARD_NEEDS_BAXI_PAID else 'работает везде'}")
    return text, kb([btn("💬 Все чаты", "a:chats:0", accent=True), btn("🔄 Обновить", "a:home:0")])


async def chats_list() -> tuple[str, InlineKeyboardMarkup]:
    chats = await db.all_chats()
    since = db.now() - 86400
    counts = {r["chat_id"]: r["n"] for r in await db.active_chats_since(since)}
    lines, rows = [], []
    for c in chats[:60]:
        ok = await access.allowed(c)
        mark = "🟢" if ok else "⚪"
        title = c["title"] or str(c["chat_id"])
        lines.append(f"{mark} {out.esc(title)}: действий за сутки {counts.get(c['chat_id'], 0)}")
        rows.append(btn(f"{mark} {title[:38]}", f"g:c:{c['chat_id']}"))
    rows.append(btn("⬅️ Админка", "a:home:0"))
    text = "💬 <b>Все чаты</b>\n" + ("\n".join(lines) if lines else "пока ни одного")
    return text, kb(*rows)


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
    text = (f"🔑 <b>Своя подписка</b>: {out.esc(chat['title'] or str(chat_id))}\n"
            f"Сейчас: {own_label(chat)}\nРежим: {out.esc(mode)}\n\n"
            "Нужна, только если в .env стоит GUARD_NEEDS_BAXI_PAID=1: тогда модератор работает в чатах, где Бакси "
            "оплачен или на пробном, и в чатах со своей подпиской.")
    return text, kb([btn("+30 дней", f"a:sub:{chat_id}:30", accent=True), btn("Навсегда", f"a:sub:{chat_id}:inf")],
                    btn("Снять", f"a:sub:{chat_id}:off"), btn("⬅️ К чату", f"g:c:{chat_id}"))


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not perms.is_owner(message.from_user.id):
        return
    text, markup = await home()
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("a:"))
async def on_admin(cb: CallbackQuery, bot: Bot) -> None:
    if not perms.is_owner(cb.from_user.id):
        await cb.answer()
        return
    parts = cb.data.split(":")
    screen = parts[1]
    if screen == "home":
        text, markup = await home()
    elif screen == "chats":
        text, markup = await chats_list()
    elif screen == "sub":
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
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML", link_preview_options=out.NO_PREVIEW)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    await cb.answer()
