"""Связь с разработчиком. Человек пишет из лички бота, выбирает чат и тему; разработчику приходит обращение
с карточкой: кто пишет, его роль, что за чат, права бота, тариф, Бакси, владелец, что было за сутки.
Разработчик отвечает кнопкой или ответом на сообщение, ответ приходит человеку в личку."""

import logging
from datetime import datetime

from aiogram import Bot

from guard import VERSION, config, db, out, perms

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    chat_id  INTEGER NOT NULL DEFAULT 0,
    topic    TEXT    NOT NULL DEFAULT 'other',
    status   TEXT    NOT NULL DEFAULT 'open',   -- open | closed
    created  INTEGER NOT NULL,
    updated  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS tickets_user ON tickets (user_id, status);

-- какие сообщения у разработчика к какому обращению: чтобы ответ на любое из них дошёл до человека
CREATE TABLE IF NOT EXISTS ticket_links (
    dev_chat   INTEGER NOT NULL,
    dev_msg    INTEGER NOT NULL,
    ticket_id  INTEGER NOT NULL,
    PRIMARY KEY (dev_chat, dev_msg)
);
"""
TOPICS = {"bug": "🐞 Ошибка", "pay": "💳 Оплата и тариф", "move": "🧰 Перенос чата", "owner": "👑 Владелец чата",
          "idea": "💡 Идея", "other": "❓ Другое"}
MAX_OPEN = 3          # открытых обращений у одного человека
MAX_PER_DAY = 5       # новых в сутки
MAX_TEXT = 3000
FOLLOW_GAP = 20       # дополнения не чаще раза в 20 секунд


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()


def dev_chats() -> list[int]:
    return [config.SUPPORT_CHAT_ID] if config.SUPPORT_CHAT_ID else sorted(config.OWNER_IDS)


async def notify_devs(bot: Bot, text: str) -> None:
    """Служебное уведомление разработчику (смена владельца и т.п.)."""
    for chat_id in dev_chats():
        try:
            await bot.send_message(chat_id, text, link_preview_options=out.NO_PREVIEW)
        except Exception:
            log.warning("Не отправил уведомление разработчику в %s", chat_id)


# ---------- обращения ----------

async def can_open(user_id: int) -> str:
    """Пусто: можно создать. Иначе причина."""
    async with db.conn().execute("SELECT COUNT(*) FROM tickets WHERE user_id = ? AND status = 'open'",
                                 (user_id,)) as cur:
        if (await cur.fetchone())[0] >= MAX_OPEN:
            return f"У вас уже {MAX_OPEN} открытых обращения. Дождитесь ответа или закройте одно из них."
    async with db.conn().execute("SELECT COUNT(*) FROM tickets WHERE user_id = ? AND created >= ?",
                                 (user_id, db.now() - 86400)) as cur:
        if (await cur.fetchone())[0] >= MAX_PER_DAY:
            return "На сегодня лимит обращений исчерпан. Попробуйте завтра или дополните открытое."
    return ""


async def create(user_id: int, chat_id: int, topic: str) -> int:
    cur = await db.conn().execute("INSERT INTO tickets (user_id, chat_id, topic, created, updated) VALUES (?, ?, ?, ?, ?)",
                                  (user_id, chat_id, topic if topic in TOPICS else "other", db.now(), db.now()))
    await db.conn().commit()
    return cur.lastrowid


async def get(ticket_id: int) -> dict | None:
    return await db._one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))


async def open_of(user_id: int) -> list[dict]:
    return await db._all("SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC", (user_id,))


async def touch(ticket_id: int) -> None:
    await db._exec("UPDATE tickets SET updated = ? WHERE id = ?", (db.now(), ticket_id))


async def close(ticket_id: int) -> None:
    await db._exec("UPDATE tickets SET status = 'closed', updated = ? WHERE id = ?", (db.now(), ticket_id))


async def link(dev_chat: int, dev_msg: int, ticket_id: int) -> None:
    await db._exec("INSERT OR REPLACE INTO ticket_links (dev_chat, dev_msg, ticket_id) VALUES (?, ?, ?)",
                   (dev_chat, dev_msg, ticket_id))


async def ticket_by_message(dev_chat: int, dev_msg: int) -> int:
    row = await db._one("SELECT ticket_id FROM ticket_links WHERE dev_chat = ? AND dev_msg = ?", (dev_chat, dev_msg))
    return row["ticket_id"] if row else 0


# ---------- карточка для разработчика ----------

def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d.%m.%Y %H:%M")


async def chat_lines(bot: Bot, chat_id: int, user_id: int) -> list[str]:
    """Всё, что нужно разработчику о чате, без лишних вопросов человеку."""
    from guard import chatinfo
    chat = await db.get_chat(chat_id)
    if chat is None:
        return [f"💬 Чат <code>{chat_id}</code>: бот о нём ничего не знает"]
    title = out.esc(chat["title"] or str(chat_id))
    link = f"@{chat['username']}" if chat["username"] else "приватный"
    lines = [f"💬 <b>{title}</b> · <code>{chat_id}</code> · {link}"]
    if chat["moved_to"]:
        lines.append(f"📦 Перенесён в <code>{chat['moved_to']}</code>")
    if chat["gone_at"]:
        lines.append(f"🚫 Бота нет в чате с {_date(chat['gone_at'])} (убрали или чат удалён)")
        role = "владелец (последний известный)" if user_id == chat["owner_id"] else "нет доступа к чату"
    else:
        try:
            members = await bot.get_chat_member_count(chat_id)
        except Exception:
            members = 0
        miss = await perms.missing_rights(bot, chat_id)
        rights = "все" if miss == [] else ("бот не админ" if miss is None else "не хватает: " + ", ".join(miss))
        lines += [f"👥 Участников: {chatinfo.number(members) if members else 'не узнал'}",
                  f"🤖 Права бота: {rights}",
                  f"🤝 Бакси: {await chatinfo.baxi_line(bot, chat_id)}"]
        role = perms.ROLE_TITLES.get(await perms.role(bot, chat_id, user_id), "участник")
    lines.append(f"🏷 Роль автора: {role}")
    lines.append(f"💳 {await chatinfo.plan(chat)}")
    owner = chat["owner_override"] or chat["owner_id"]
    owner_line = f"👑 Владелец: <code>{owner}</code>" if owner else "👑 Владелец: не известен"
    if chat["owner_override"]:
        owner_line += " (назначен в боте)"
    if chat["owner_lost"]:
        owner_line += f" · создателя нет в чате с {_date(chat['owner_lost'])}"
    if chat["heir_id"]:
        owner_line += f" · заместитель <code>{chat['heir_id']}</code>"
    lines.append(owner_line)
    async with db.conn().execute("SELECT kind, COUNT(*) FROM actions WHERE chat_id = ? AND ts >= ? GROUP BY kind",
                                 (chat_id, db.now() - 86400)) as cur:
        day = {k: n for k, n in await cur.fetchall()}
    if day:
        lines.append("🧾 За сутки: " + ", ".join(f"{k} {n}" for k, n in sorted(day.items())))
    protect = [name for key, name in (("spam_on", "антиспам"), ("stop_on", "стоп-слова")) if chat[key]]
    if chat["captcha"]:
        protect.append(f"капча {chat['captcha']}")
    lines.append("🛡 Защита: " + (", ".join(protect) or "выключена"))
    return lines


async def card(bot: Bot, ticket: dict, user) -> str:
    uname = f" · @{user.username}" if getattr(user, "username", None) else ""
    head = [f"🆘 <b>Обращение #{ticket['id']}</b> · {TOPICS.get(ticket['topic'], '❓ Другое')}",
            "➖➖➖➖➖➖➖",
            f"👤 {out.mention(user.id, out.full_name(user))}{uname} · <code>{user.id}</code>"]
    if getattr(user, "language_code", None):
        head[-1] += f" · {user.language_code}"
    body = await chat_lines(bot, ticket["chat_id"], user.id) if ticket["chat_id"] else ["💬 Не про конкретный чат"]
    async with db.conn().execute("SELECT COUNT(*) FROM tickets WHERE user_id = ?", (user.id,)) as cur:
        total = (await cur.fetchone())[0]
    tail = ["➖➖➖➖➖➖➖", f"🛡 Гард {VERSION} · обращений от него всего: {total} · {_date(ticket['created'])}",
            "Текст обращения ниже. Ответить: кнопкой или ответом на любое сообщение обращения."]
    return "\n".join(head + ["➖➖➖➖➖➖➖"] + body + tail)
