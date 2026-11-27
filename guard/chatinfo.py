"""🛡 Паспорт чата: /info или «инфо» в чате и сам при добавлении бота.

Не «карточка подписки», а состояние чата: кто в команде, что включено из защиты, как встречают новичков,
есть ли Бакси, что было за неделю (сообщения, новички, спам, наказания), тариф. Текст меняется в ✉️ Тексты."""

from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from guard import access, baxi, config, db, out, perms, texts

WEEK = 7 * 86400


def number(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


async def plan(chat: dict) -> str:
    own = chat.get("own_until") or 0
    if own == -1:
        return "Тариф: бессрочно"
    if own > db.now():
        left = (own - db.now()) // 86400
        return f"Тариф до {datetime.fromtimestamp(own, config.TZ):%d.%m.%Y} · {left} дн."
    if config.GUARD_NEEDS_BAXI_PAID and baxi.configured():
        info = await baxi.chat_info(chat["chat_id"])
        if info and baxi.paid(info):
            until = info.get("until") or 0
            kind = "Пробный период" if info.get("trial") and not info.get("paid") else "Тариф с Бакси"
            if until:
                return f"{kind} до {datetime.fromtimestamp(until, config.TZ):%d.%m.%Y} · {(until - db.now()) // 86400} дн."
            return kind
    if not config.GUARD_NEEDS_BAXI_PAID:
        return "Тариф: без ограничений"
    ok, mode = await access.status(chat)
    return ("Тариф: " if ok else "Защита остановлена: ") + mode


def team(rows: list[dict]) -> str:
    counts = {}
    for r in rows:
        counts[perms.role_from_flags(r["flags"])] = counts.get(perms.role_from_flags(r["flags"]), 0) + 1
    words = {perms.ADMIN: ("админ", "админа", "админов"), perms.MOD: ("модератор", "модератора", "модераторов"),
             perms.JUNIOR: ("мл. модератор", "мл. модератора", "мл. модераторов"),
             perms.HOST: ("ведущий", "ведущих", "ведущих")}
    parts = [f"{n} {plural(n, *words[k])}" for k, n in counts.items() if k in words]
    return ", ".join(parts) or "только владелец"


def protection(chat: dict) -> str:
    on = [name for flag, name in ((chat["spam_on"], "антиспам"), (chat["stop_on"], "стоп-слова"),
                                  (chat["captcha"], "капча"), (chat["attack_auto"], "автозащита от набегов"))
          if flag]
    if chat["attack_until"] > db.now():
        on.insert(0, "режим атаки идёт")
    return ", ".join(on) or "выключена, включается в настройках"


def newcomers(chat: dict) -> str:
    parts = [p for flag, p in ((chat["welcome_on"], "приветствие"), (chat["captcha"], "капча")) if flag]
    return " и ".join(parts) or "без приветствия"


async def baxi_line(bot: Bot, chat_id: int) -> str:
    here = False
    if config.BAXI_BOT_ID:
        m = await perms.member(bot, chat_id, config.BAXI_BOT_ID)
        here = m is not None and m.status in ("member", "administrator", "creator")
    if not here:
        return "нет в чате"
    return "в чате, связь есть" if baxi.configured() and baxi.state.get("last_ok") else "в чате"


async def week(chat_id: int, bot_id: int) -> dict:
    since = db.now() - WEEK
    msgs = (await db.activity(chat_id, "week"))[0]
    async with db.conn().execute("SELECT COUNT(*) FROM members WHERE chat_id = ? AND joined_at >= ?",
                                 (chat_id, since)) as cur:
        joined = (await cur.fetchone())[0]
    async with db.conn().execute(
            "SELECT SUM(kind = 'spam' OR (kind = 'delete' AND by_id = ?)), "
            "SUM(kind IN ('warn', 'mute', 'ban', 'kick')) FROM actions WHERE chat_id = ? AND ts >= ?",
            (bot_id, chat_id, since)) as cur:
        spam, punished = await cur.fetchone()
    return {"messages": number(msgs), "joined": f"+{joined}" if joined else "0", "spam": number(spam or 0),
            "punished": number(punished or 0)}


async def send(bot: Bot, chat: dict, reply_to=None):
    me = await bot.me()
    try:
        members = await bot.get_chat_member_count(chat["chat_id"])
    except Exception:
        members = 0
    values = {"chat": out.esc(chat.get("title") or ""), "chat_id": str(chat["chat_id"]), "members": number(members),
              "team": team(await db.rights_list(chat["chat_id"])), "protection": protection(chat),
              "newcomers": newcomers(chat), "baxi": await baxi_line(bot, chat["chat_id"]),
              "plan": await plan(chat), **await week(chat["chat_id"], bot.id)}
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Команды", url=f"https://t.me/{me.username}?start=help"),
         InlineKeyboardButton(text="⚙️ Настройки", url=f"https://t.me/{me.username}?start=s_{chat['chat_id']}")],
    ])
    return await texts.send(bot, chat, "chat_info", reply_to=reply_to, ttl=0, extra=markup, **values)
