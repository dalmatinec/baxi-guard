"""Перенос чата (в том числе из недоступного), владелец в боте и заместитель, поддержка, лимиты."""

import asyncio
from types import SimpleNamespace

from guard import config, db, limits, owners, perms, support, transfer

OLD, NEW = -1001, -1002
OWNER, HEIR, MOD = 10, 20, 30


def run(tmp_path, coro_fn):
    async def go():
        await db.connect(tmp_path / "t.db")
        await owners.init()
        await support.init()
        from guard import texts, triggers
        from guard.gamecore import tops
        await texts.init()
        await triggers.init()
        await db.conn().executescript(tops.SCHEMA)
        try:
            return await coro_fn()
        finally:
            await db.close()
    return asyncio.run(go())


class FakeBot:
    id = 999

    def __init__(self, creator=OWNER, gone=False, members=None):
        self.creator, self.gone, self.sent = creator, gone, []
        self.members = members or {}

    async def get_chat_administrators(self, chat_id):
        if self.gone:
            from aiogram.exceptions import TelegramForbiddenError
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was kicked")
        out = []
        if self.creator:
            out.append(SimpleNamespace(status="creator", user=SimpleNamespace(id=self.creator, is_bot=False)))
        return out

    async def get_chat_member(self, chat_id, user_id):
        status = self.members.get(user_id, "creator" if user_id == self.creator else "member")
        return SimpleNamespace(status=status, user=SimpleNamespace(id=user_id, is_bot=False))

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))


def test_copy_with_options(tmp_path):
    async def go():
        await db.ensure_chat(OLD, "Старый")
        await db.ensure_chat(NEW, "Новый")
        await db.set_chat(OLD, spam_on=1, captcha="math", own_until=db.now() + 86400 * 20, heir_id=HEIR)
        await db.conn().execute("INSERT INTO rights (chat_id, user_id, flags, added_by, ts) VALUES (?, ?, ?, 0, 0)",
                                (OLD, MOD, perms.ROLE_MOD_FLAGS))
        await db.touch_member(OLD, 5, "vasya", "Вася")
        await db.touch_member(OLD, 5, "vasya", "Вася")
        await db.member_joined(NEW, 5, "vasya", "Вася")     # Вася уже зашёл в новый чат: новичок там
        await db.touch_member(NEW, 5, "vasya", "Вася")
        await db.log_action(OLD, 77, OWNER, "ban")
        await db.log_action(OLD, 78, OWNER, "ban")
        await db.log_action(OLD, 78, OWNER, "unban")         # разбанен: в бан-лист не попадает
        await db.conn().commit()

        assert await transfer.banned_ids(OLD) == [77]
        counts = await transfer.copy_chat(OLD, NEW, transfer.ALL & ~transfer.ROLES & ~transfer.BANS & ~transfer.BAXI)
        new = await db.get_chat(NEW)
        assert new["spam_on"] == 1 and new["captcha"] == "math"
        assert new["own_until"] > db.now() and (await db.get_chat(OLD))["own_until"] == 0   # тариф ушёл
        assert new["heir_id"] == HEIR and counts["tariff"] == 1
        assert (await db.get_chat(OLD))["moved_to"] == NEW
        assert await db.rights_of(NEW, MOD) == 0                                         # роли не выбраны
        vasya = await db.get_member(NEW, 5)
        assert vasya["msg_count"] == 3 and vasya["joined_at"] == 0   # сообщения сложились, уже не новичок
        # тариф, пришедший переносом, дальше 30 дней не передаётся
        assert await transfer.tariff_locked(NEW) > db.now()
        await db.ensure_chat(-1003, "Третий")
        await transfer.copy_chat(NEW, -1003, transfer.TARIFF | transfer.ROLES)
        assert (await db.get_chat(-1003))["own_until"] == 0 and (await db.get_chat(NEW))["own_until"] > 0
        await transfer.copy_chat(OLD, -1003, transfer.ROLES)
        assert await db.rights_of(-1003, MOD) == perms.ROLE_MOD_FLAGS
    run(tmp_path, go)


def test_offline_flags_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", {1})

    async def go():
        bot = FakeBot(gone=True)
        await db.ensure_chat(OLD, "Удалённый")
        await db.set_chat(OLD, owner_id=OWNER)
        assert await owners.refresh(bot, OLD) == "gone"
        chat = await db.get_chat(OLD)
        assert chat["gone_at"] > 0
        assert perms.offline_flags(chat, OWNER, 0) & perms.R_SETTINGS                     # последний создатель
        assert not perms.offline_flags(chat, MOD, perms.R_MUTE) & perms.R_SETTINGS       # модератор: только своё
        assert perms.offline_flags(chat, MOD, perms.R_ADMIN) & perms.R_SETTINGS          # админ бота: всё
        await owners.set_override(OLD, HEIR)
        assert perms.OVERRIDES[OLD] == HEIR
        assert await perms.is_creator(FakeBot(), OLD, HEIR)
        await owners.init()                                                               # после перезапуска
        assert perms.OVERRIDES[OLD] == HEIR
        await owners.set_override(OLD, 0)
        assert OLD not in perms.OVERRIDES
    run(tmp_path, go)


def test_heir_claim_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", {1})
    monkeypatch.setattr(config, "SUPPORT_CHAT_ID", 0)

    async def go():
        bot = FakeBot(creator=OWNER, members={HEIR: "administrator"})
        await db.ensure_chat(OLD, "Чат")
        ok, text = await owners.claim(bot, OLD, HEIR)
        assert not ok and "не заместитель" in text
        await owners.set_heir(OLD, HEIR)
        ok, _ = await owners.claim(bot, OLD, HEIR)
        c = await owners.get_claim(OLD)
        assert ok and c["deadline"] - c["created"] == owners.WAIT_ADMIN          # админ чата: 72 часа
        assert any(cid == OWNER for cid, _ in bot.sent)                          # владельцу пришло уведомление
        assert any(cid == OLD for cid, _ in bot.sent)                            # и в чат
        ok, text = await owners.claim(bot, OLD, HEIR)
        assert not ok and "уже" in text
        await owners.decline(bot, OLD)                                           # владелец отклонил
        ok, text = await owners.claim(bot, OLD, HEIR)
        assert not ok and "отклонил" in text
        await db.kv_set(f"claim_cd:{OLD}", "0")
        bot2 = FakeBot(creator=0, members={HEIR: "member"})                      # создателя больше нет
        ok, _ = await owners.claim(bot2, OLD, HEIR)
        c = await owners.get_claim(OLD)
        assert c["deadline"] - c["created"] == owners.WAIT_LOST                  # сутки
        await db._exec("UPDATE owner_claims SET deadline = 0")
        import guard.commands as commands

        async def nothing(*a, **k):
            return None
        monkeypatch.setattr(commands, "refresh_chat", nothing)
        await owners.sweep(bot2)
        assert perms.OVERRIDES.get(OLD) == HEIR and await owners.get_claim(OLD) is None
    run(tmp_path, go)


def test_support_limits_and_links(tmp_path):
    async def go():
        assert await support.can_open(5) == ""
        ids = [await support.create(5, 0, "bug") for _ in range(support.MAX_OPEN)]
        assert "открытых" in await support.can_open(5)
        await support.close(ids[0])
        assert await support.can_open(5) == ""
        for _ in range(support.MAX_PER_DAY - support.MAX_OPEN):
            t = await support.create(5, 0, "idea")
            await support.close(t)
        assert "лимит" in await support.can_open(5)
        await support.link(-500, 42, ids[1])
        assert await support.ticket_by_message(-500, 42) == ids[1]
        assert await support.ticket_by_message(-500, 43) == 0
        t = await support.get(await support.create(6, 0, "nonsense"))
        assert t["topic"] == "other"
    run(tmp_path, go)


def test_flood_window_and_bucket():
    w = limits.Window(3, 10)
    assert all(w.allow("u") for _ in range(3)) and not w.allow("u") and w.allow("other")
    b = limits.Bucket(2, 1.0)
    assert b.wait_time() == 0 and b.wait_time() == 0
    assert 0 < b.wait_time() <= 0.6           # третье ждёт своей очереди
