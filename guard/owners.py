"""Владелец чата в боте. Бот запоминает создателя чата и следит, на месте ли он.

Если создателя удалили, заблокировали или он потерял аккаунт, Telegram сам права не передаст. В боте их
можно вернуть двумя путями:
  • заместитель: владелец назначает его заранее. Заместитель запрашивает права, владельцу и чату приходит
    уведомление, и если за срок никто не отклонил, заместитель становится владельцем в боте;
  • разработчик: по обращению в поддержку назначает владельца сам (/setowner).
Владелец в боте получает все права бота в этом чате (как создатель). Права администратора Telegram он так
не получает: их выдают админы чата."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from guard import db, out, perms

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_claims (
    chat_id   INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    created   INTEGER NOT NULL,
    deadline  INTEGER NOT NULL
);
"""
REFRESH_EVERY = 6 * 3600       # раз в 6 часов сверяем создателей всех чатов
DAY = 86400
WAIT_ADMIN = 3 * DAY           # заместитель админ в чате: 72 часа на возражение
WAIT_MEMBER = 7 * DAY          # просто участник: неделя
WAIT_LOST = DAY                # создателя в чате уже нет: сутки
DECLINE_COOLDOWN = 7 * DAY     # после отказа повторно не раньше чем через неделю
_lock = asyncio.Lock()


async def init() -> None:
    await db.conn().executescript(SCHEMA)
    await db.conn().commit()
    perms.OVERRIDES.clear()
    for row in await db._all("SELECT chat_id, owner_override FROM chats WHERE owner_override != 0"):
        perms.OVERRIDES[row["chat_id"]] = row["owner_override"]


# ---------- создатель и доступ к чату ----------

async def refresh(bot: Bot, chat_id: int) -> str:
    """Сверить создателя и доступ к чату. Возвращает ok, lost (создателя нет) или gone (чат недоступен)."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        text = str(exc).lower()
        if isinstance(exc, TelegramForbiddenError) or "not found" in text or "kicked" in text:
            await mark_gone(chat_id)
            return "gone"
        log.warning("Не проверил админов %s: %s", chat_id, exc)
        return "error"
    chat = await db.ensure_chat(chat_id)
    creator = next((a.user for a in admins if a.status == "creator"), None)
    fields: dict = {"gone_at": 0}
    if creator is not None and not creator.is_bot:
        fields.update(owner_id=creator.id, owner_seen=db.now(), owner_lost=0)
    elif not chat["owner_lost"]:
        fields["owner_lost"] = db.now()
    await db.set_chat(chat_id, **fields)
    return "ok" if creator else "lost"


async def refresh_all(bot: Bot) -> None:
    """Все чаты, где бот есть, без спешки: Telegram не любит сотни запросов разом."""
    if _lock.locked():
        return
    async with _lock:
        for chat in await db.all_chats():
            if chat["gone_at"]:
                continue
            try:
                await refresh(bot, chat["chat_id"])
            except Exception:
                log.exception("Сверка владельца %s", chat["chat_id"])
            await asyncio.sleep(0.2)
        await db.kv_set("owners_at", str(db.now()))


async def maybe_refresh_all(bot: Bot) -> None:
    last = int(await db.kv_get("owners_at", "0") or 0)
    if db.now() - last >= REFRESH_EVERY and not _lock.locked():
        await db.kv_set("owners_at", str(db.now()))
        out.spawn(refresh_all(bot))


async def mark_gone(chat_id: int) -> None:
    chat = await db.get_chat(chat_id)
    if chat and not chat["gone_at"]:
        await db.set_chat(chat_id, gone_at=db.now())


async def mark_back(chat_id: int) -> None:
    chat = await db.get_chat(chat_id)
    if chat and chat["gone_at"]:
        await db.set_chat(chat_id, gone_at=0)


def owner_of(chat: dict) -> int:
    """Кто владелец в боте: назначенный вместо создателя, иначе создатель."""
    return chat.get("owner_override") or chat.get("owner_id") or 0


async def is_bot_owner_of(bot: Bot, chat: dict, user_id: int) -> bool:
    """Владелец чата в смысле бота: создатель сейчас, владелец в боте, или (чат недоступен) последний создатель."""
    if perms.is_owner(user_id) or user_id == chat.get("owner_override"):
        return True
    if chat.get("gone_at"):
        return user_id == chat.get("owner_id")
    return await perms.is_creator(bot, chat["chat_id"], user_id)


# ---------- заместитель ----------

async def set_heir(chat_id: int, user_id: int) -> None:
    await db.set_chat(chat_id, heir_id=user_id)
    await db._exec("DELETE FROM owner_claims WHERE chat_id = ?", (chat_id,))


async def heir_chats(user_id: int) -> list[dict]:
    return await db._all("SELECT * FROM chats WHERE heir_id = ? ORDER BY title COLLATE NOCASE", (user_id,))


async def set_override(chat_id: int, user_id: int) -> None:
    """Назначить владельца в боте (0: снять). Права в кеше забываются."""
    await db.set_chat(chat_id, owner_override=user_id)
    if user_id:
        perms.OVERRIDES[chat_id] = user_id
    else:
        perms.OVERRIDES.pop(chat_id, None)
    await db._exec("DELETE FROM owner_claims WHERE chat_id = ?", (chat_id,))
    perms.forget(chat_id)


async def get_claim(chat_id: int) -> dict | None:
    return await db._one("SELECT * FROM owner_claims WHERE chat_id = ?", (chat_id,))


async def claim(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str]:
    """Заместитель просит права владельца. Возвращает (получилось, текст для него)."""
    chat = await db.get_chat(chat_id)
    if chat is None or chat["heir_id"] != user_id:
        return False, "Вы не заместитель владельца в этом чате."
    if chat["owner_override"] == user_id:
        return False, "Вы уже владелец этого чата в боте."
    if await get_claim(chat_id):
        return False, "Запрос уже отправлен, ждём срок."
    cooldown = int(await db.kv_get(f"claim_cd:{chat_id}", "0") or 0)
    if db.now() < cooldown:
        return False, f"Владелец отклонил прошлый запрос. Повторить можно с {date(cooldown)}."
    if chat["gone_at"]:
        wait = WAIT_ADMIN
    else:
        status = await refresh(bot, chat_id)
        chat = await db.get_chat(chat_id)
        m = await perms.member(bot, chat_id, user_id)
        if m is None or m.status in ("left", "kicked"):
            return False, "Вы должны быть участником этого чата."
        if status == "lost":
            wait = WAIT_LOST
        elif m.status in ("administrator", "creator"):
            wait = WAIT_ADMIN
        else:
            wait = WAIT_MEMBER
    deadline = db.now() + wait
    await db._exec("INSERT INTO owner_claims (chat_id, user_id, created, deadline) VALUES (?, ?, ?, ?)",
                   (chat_id, user_id, db.now(), deadline))
    title = out.esc(chat["title"] or str(chat_id))
    who = out.mention(user_id, await db.member_name(chat_id, user_id) or str(user_id))
    owner = chat["owner_id"]
    if owner:
        from guard.ui import btn, kb
        markup = kb([btn("✋ Отклонить", f"ow:no:{chat_id}"), btn("✅ Передать сейчас", f"ow:yes:{chat_id}")])
        try:
            await bot.send_message(owner, f"👑 <b>Запрос прав владельца</b>\n➖➖➖➖➖\nЧат: {title}\n"
                                          f"Заместитель {who} просит права владельца в боте.\n"
                                          f"Если не отклонить, права перейдут {date(deadline)}.", reply_markup=markup)
        except Exception:
            pass   # владелец не запускал бота или аккаунт недоступен: на это и расчёт
    if not chat["gone_at"]:
        await out.say(bot, chat_id, f"👑 Заместитель владельца {who} запросил права владельца в боте.\n"
                                    f"Если владелец не отклонит запрос в личке бота, права перейдут {date(deadline)}.")
    from guard import support
    await support.notify_devs(bot, f"👑 Запрос прав владельца\nЧат: {title} · <code>{chat_id}</code>\n"
                                   f"Заместитель: {who} · <code>{user_id}</code>\nСоздатель: <code>{owner}</code>\n"
                                   f"Срок: {date(deadline)}")
    return True, f"Запрос отправлен. Если владелец не отклонит его, права перейдут {date(deadline)}."


async def decline(bot: Bot, chat_id: int) -> dict | None:
    c = await get_claim(chat_id)
    if c is None:
        return None
    await db._exec("DELETE FROM owner_claims WHERE chat_id = ?", (chat_id,))
    await db.kv_set(f"claim_cd:{chat_id}", str(db.now() + DECLINE_COOLDOWN))
    try:
        await bot.send_message(c["user_id"], "👑 Владелец отклонил ваш запрос прав владельца в боте.")
    except Exception:
        pass
    return c


async def grant(bot: Bot, chat_id: int, user_id: int, reason: str) -> None:
    """Права владельца в боте переходят к user_id. Сообщаем ему, чату и разработчику."""
    await set_override(chat_id, user_id)
    chat = await db.get_chat(chat_id)
    title = out.esc(chat["title"] or str(chat_id)) if chat else str(chat_id)
    who = out.mention(user_id, await db.member_name(chat_id, user_id) or str(user_id))
    try:
        await bot.send_message(user_id, f"👑 Теперь вы владелец чата {title} в боте: {reason}.\n"
                                        "Все права бота в этом чате ваши, настройки в «Мои чаты».")
    except Exception:
        pass
    if chat and not chat["gone_at"]:
        await out.say(bot, chat_id, f"👑 {who} теперь владелец этого чата в боте: {reason}.")
    from guard import commands, support
    out.spawn(commands.refresh_chat(bot, chat_id))
    await support.notify_devs(bot, f"👑 Владелец в боте сменился\nЧат: {title} · <code>{chat_id}</code>\n"
                                   f"Новый: {who} · <code>{user_id}</code>\nПричина: {reason}")


async def sweep(bot: Bot) -> None:
    """Запросы, срок которых вышел и никто не отклонил: права переходят заместителю."""
    for c in await db._all("SELECT * FROM owner_claims WHERE deadline <= ?", (db.now(),)):
        chat = await db.get_chat(c["chat_id"])
        if chat is None or chat["heir_id"] != c["user_id"]:
            await db._exec("DELETE FROM owner_claims WHERE chat_id = ?", (c["chat_id"],))
            continue
        try:
            await grant(bot, c["chat_id"], c["user_id"], "запрос заместителя никто не отклонил")
        except Exception:
            log.exception("Не передал права владельца в %s", c["chat_id"])


def date(ts: int) -> str:
    from datetime import datetime

    from guard import config
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d.%m.%Y %H:%M")
