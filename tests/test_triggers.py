"""Триггеры, перенос чата и права в боте."""

import asyncio
import datetime
from types import SimpleNamespace

from aiogram.types import Chat, Message, MessageEntity, PhotoSize, User

from guard import config, db, perms, texts, transfer, triggers
from guard.gamecore import core, tops

CHAT, NEW = -1001, -1002
NOW = datetime.datetime(2026, 11, 23, tzinfo=datetime.timezone.utc)


def run(tmp_path, fn):
    async def go():
        await db.connect(tmp_path / "g.db")
        await texts.init()
        await core.init()
        await triggers.init()
        try:
            return await fn()
        finally:
            await db.close()
    return asyncio.run(go())


def msg(**kw):
    return Message(message_id=1, date=NOW, chat=Chat(id=1, type="private"),
                   from_user=User(id=1, is_bot=False, first_name="a"), **kw)


def test_norm():
    assert triggers.norm("  Привет!! ") == "привет"
    assert triggers.norm("Ёлка") == "елка"
    assert triggers.norm("доброе   утро") == "доброе утро"
    assert triggers.norm("!!!") == ""


def test_content_of():
    m = msg(text="🔥 огонь", entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="77")])
    assert triggers.content_of(m) == ("text", "", '<tg-emoji emoji-id="77">🔥</tg-emoji> огонь')
    p = msg(photo=[PhotoSize(file_id="small", file_unique_id="1", width=1, height=1),
                   PhotoSize(file_id="big", file_unique_id="2", width=9, height=9)], caption="<b>x</b>")
    kind, file_id, html = triggers.content_of(p)
    assert (kind, file_id) == ("photo", "big") and "x" in html


def test_save_match_delete(tmp_path):
    async def fn():
        assert await triggers.save(CHAT, "Привет!", "text", "", "Здарова 👋", 5) == ""
        assert (await triggers.match(CHAT, "привет"))["html"] == "Здарова 👋"
        assert (await triggers.match(CHAT, "ПРИВЕТ!!!"))["word"] == "привет"
        assert await triggers.match(CHAT, "привет всем") is None       # только целиком
        assert await triggers.match(CHAT + 5, "привет") is None        # другой чат
        await triggers.save(CHAT, "привет", "sticker", "STK", "", 5)    # замена
        assert (await triggers.match(CHAT, "привет"))["kind"] == "sticker"
        assert triggers.cooled(CHAT, "привет") and not triggers.cooled(CHAT, "привет")
        assert await triggers.delete(CHAT, "Привет")
        assert await triggers.match(CHAT, "привет") is None
    run(tmp_path, fn)


def test_transfer(tmp_path):
    async def fn():
        await db.ensure_chat(CHAT, "Старый")
        await db.set_chat(CHAT, spam_on=1, warn_limit=5, log_chat_id=-999)
        await db.add_stopwords(CHAT, ["плохо"])
        await db.add_domains(CHAT, ["youtube.com"])
        await db.set_rights(CHAT, 7, perms.R_GAMES, 1)
        await texts.set_text(CHAT, "game_closed", html="свой {count}")
        await triggers.save(CHAT, "правила", "text", "", "Не спамить", 1)
        await tops.give(CHAT, 8, 1)
        await tops.give(CHAT, 9, 1, game_id=42)
        await db.add_warn(CHAT, 10, 1, "флуд")
        await db.ensure_chat(NEW, "Новый")
        counts = await transfer.copy_chat(CHAT, NEW)
        new = await db.get_chat(NEW)
        assert new["spam_on"] == 1 and new["warn_limit"] == 5 and new["title"] == "Новый" and new["log_chat_id"] == 0
        assert await db.stopwords(NEW) == ["плохо"] and await db.domains(NEW) == ["youtube.com"]
        assert await db.rights_of(NEW, 7) == perms.R_GAMES
        assert "свой" in await texts.render(NEW, "game_closed", count=1)
        assert (await triggers.match(NEW, "правила"))["html"] == "Не спамить"
        assert await tops.is_top(NEW, 8) and not await tops.is_top(NEW, 9, 42)
        assert await db.count_warns(NEW, 10, 30) == 1
        assert counts["triggers"] == 1 and "триггеров: 1" in transfer.summary(counts)
    run(tmp_path, fn)


class FakeBot:
    def __init__(self, statuses):
        self.statuses = statuses

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.statuses.get(user_id, "member"))


def test_rights(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", {900})

    async def fn():
        perms._cache.clear()
        bot = FakeBot({1: "creator", 2: "administrator"})
        assert await perms.has(bot, CHAT, 1, perms.R_BAN)                # создатель: всё
        assert await perms.has(bot, CHAT, 900, perms.R_SETTINGS)         # владелец бота: всё
        assert not await perms.has(bot, CHAT, 2, perms.R_MUTE)           # админ Telegram: ничего сам по себе
        assert not await perms.has_any(bot, CHAT, 3)
        await db.set_rights(CHAT, 2, perms.R_MUTE | perms.R_GAMES, 1)
        assert await perms.has(bot, CHAT, 2, perms.R_MUTE) and not await perms.has(bot, CHAT, 2, perms.R_BAN)
        assert not await perms.can_grant(bot, CHAT, 2, perms.R_MUTE)    # нет права выдачи
        await db.set_rights(CHAT, 2, perms.R_MUTE | perms.R_GRANT, 1)
        assert await perms.can_grant(bot, CHAT, 2, perms.R_MUTE)
        assert not await perms.can_grant(bot, CHAT, 2, perms.R_BAN)     # чего нет у самого, не выдать
        assert not await perms.can_grant(bot, CHAT, 2, perms.R_ADMIN)   # админа бота даёт только создатель
        await db.set_rights(CHAT, 3, perms.R_ADMIN, 1)
        assert await perms.has(bot, CHAT, 3, perms.R_TRIGGERS)           # админ бота: все права
        assert await perms.can_grant(bot, CHAT, 3, perms.R_ADMIN)
        assert perms.rights_text(perms.R_ADMIN) == "⭐ Админ"
        assert perms.rights_text(perms.ROLE_MOD_FLAGS) == "🛡 Модератор"
        # роли: владелец назначает всех, админ только модераторов
        assert await perms.role(bot, CHAT, 1) == perms.OWNER and await perms.role(bot, CHAT, 3) == perms.ADMIN
        assert await perms.can_set_role(bot, CHAT, 1, 4, perms.ADMIN) == ""
        assert await perms.can_set_role(bot, CHAT, 3, 4, perms.MOD) == ""
        assert "владелец" in await perms.can_set_role(bot, CHAT, 3, 4, perms.ADMIN)
        assert "владелец" in await perms.can_set_role(bot, CHAT, 3, 3, perms.NONE) or True
        assert "все права" in await perms.can_set_role(bot, CHAT, 3, 1, perms.NONE)
        await perms.set_role(CHAT, 4, perms.MOD, 1)
        assert await perms.role(bot, CHAT, 4) == perms.MOD
        assert await perms.has(bot, CHAT, 4, perms.R_GAMES) and not await perms.has(bot, CHAT, 4, perms.R_SETTINGS)
        assert "назначают" in await perms.can_set_role(bot, CHAT, 4, 5, perms.MOD)   # модератор не назначает
    run(tmp_path, fn)
