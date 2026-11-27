"""💬 Информация о чате: /info или «инфо» в чате и сама при добавлении бота.

Название и ID чата, права бота (полный доступ или чего не хватает), версия, сколько раз записывались в игры,
подписка (своя от владельца или Бакси), подтверждение, что это настоящий бот. Текст меняется в ✉️ Тексты."""

from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from guard import VERSION, access, baxi, config, db, out, perms, texts


def days_left(until: int) -> str:
    left = max(0, (until - db.now()) // 86400)
    return f"осталось {left} дн."


async def subscription(chat: dict) -> str:
    own = chat.get("own_until") or 0
    if own == -1:
        return "🟢 активна · бессрочно"
    if own > db.now():
        return f"🟢 активна до {datetime.fromtimestamp(own, config.TZ):%d.%m.%Y} · {days_left(own)}"
    ok, mode = await access.status(chat)
    if config.GUARD_NEEDS_BAXI_PAID and baxi.configured():
        info = await baxi.chat_info(chat["chat_id"])
        if info and baxi.paid(info):
            until = info.get("until") or 0
            kind = "пробный период" if info.get("trial") and not info.get("paid") else "активна"
            if until:
                return f"🟢 {kind} до {datetime.fromtimestamp(until, config.TZ):%d.%m.%Y} · {days_left(until)}"
            return f"🟢 {kind}"
    return ("🟢 " if ok else "🔴 ") + mode


async def access_line(bot: Bot, chat_id: int) -> str:
    miss = await perms.missing_rights(bot, chat_id)
    if miss is None:
        return "❌ бот не администратор"
    if miss:
        return "⚠️ частичный, нет: " + ", ".join(miss)
    return "✅ полный"


async def games_count(chat_id: int) -> int:
    async with db.conn().execute("SELECT COUNT(*) FROM game_entries e JOIN games g ON g.id = e.game_id "
                                 "WHERE g.chat_id = ?", (chat_id,)) as cur:
        return (await cur.fetchone())[0]


async def send(bot: Bot, chat: dict, reply_to=None):
    me = await bot.me()
    values = {"chat": out.esc(chat.get("title") or ""), "chat_id": str(chat["chat_id"]),
              "access": await access_line(bot, chat["chat_id"]), "version": VERSION,
              "games": f"{await games_count(chat['chat_id']):,}".replace(",", " "),
              "subscription": await subscription(chat), "bot": f"@{me.username}"}
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Команды", url=f"https://t.me/{me.username}?start=help")],
        [InlineKeyboardButton(text="⚙️ Настройки для админов", url=f"https://t.me/{me.username}?start=s_{chat['chat_id']}")],
    ])
    return await texts.send(bot, chat, "chat_info", reply_to=reply_to, ttl=0, extra=markup, **values)
