"""Кого касается команда: ответ, синее имя без юзернейма, @username из базы, ID."""

import asyncio
import datetime

from aiogram.types import Chat, Message, MessageEntity, User

from guard import db
from guard.handlers.moderation import resolve

CHAT = Chat(id=-1001234567890, type="supergroup", title="Тест")
ADMIN = User(id=111111, is_bot=False, first_name="Админ")
VASYA = User(id=222222, is_bot=False, first_name="Вася", username="vasya")
NOW = datetime.datetime(2026, 11, 17, tzinfo=datetime.timezone.utc)


def msg(text: str, *, reply_to: Message | None = None, entities=None, sender_chat=None, user=ADMIN,
        message_id=10) -> Message:
    return Message(message_id=message_id, date=NOW, chat=CHAT, from_user=user, text=text,
                   reply_to_message=reply_to, entities=entities, sender_chat=sender_chat)


def run(coro_fn, tmp_path):
    async def go():
        await db.connect(tmp_path / "t.db")
        try:
            return await coro_fn()
        finally:
            await db.close()
    return asyncio.run(go())


def test_reply_target(tmp_path):
    async def go():
        original = msg("привет", user=VASYA, message_id=5)
        args, target, error = await resolve(msg("/mute 2h флуд", reply_to=original), with_time=True)
        assert error == ""
        assert target.user_id == VASYA.id and target.name == "Вася"
        assert args.seconds == 7200 and args.reason == "флуд"
    run(go, tmp_path)


def test_reply_to_channel_post_is_refused(tmp_path):
    async def go():
        channel = Chat(id=-100999, type="channel", title="Канал")
        original = msg("пост", user=None, sender_chat=channel, message_id=5)
        _args, target, error = await resolve(msg("/ban", reply_to=original), with_time=True)
        assert target is None and "/del" in error
    run(go, tmp_path)


def test_username_from_db(tmp_path):
    async def go():
        await db.touch_member(CHAT.id, VASYA.id, "Vasya", "Вася")
        args, target, error = await resolve(msg("/ban @vasya 1d реклама"), with_time=True)
        assert error == "" and target.user_id == VASYA.id
        assert args.seconds == 86400 and args.reason == "реклама"
    run(go, tmp_path)


def test_unknown_username(tmp_path):
    async def go():
        _args, target, error = await resolve(msg("/ban @nobody_here"), with_time=True)
        assert target is None and "@nobody_here" in error
    run(go, tmp_path)


def test_id_target(tmp_path):
    async def go():
        await db.touch_member(CHAT.id, VASYA.id, "vasya", "Вася")
        args, target, error = await resolve(msg(f"/mute {VASYA.id} 30m"), with_time=True)
        assert error == "" and target.user_id == VASYA.id and target.name == "Вася" and args.seconds == 1800
    run(go, tmp_path)


def test_text_mention(tmp_path):
    async def go():
        text = "/mute Петя 1h спам"
        petya = User(id=333333, is_bot=False, first_name="Петя")
        ent = MessageEntity(type="text_mention", offset=6, length=4, user=petya)
        args, target, error = await resolve(msg(text, entities=[ent]), with_time=True)
        assert error == "" and target.user_id == petya.id
        assert args.seconds == 3600 and args.reason == "спам"
    run(go, tmp_path)


def test_no_target(tmp_path):
    async def go():
        _args, target, error = await resolve(msg("/mute 1h"), with_time=True)
        assert target is None and "ответьте" in error
    run(go, tmp_path)
