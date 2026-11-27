"""Свой ник: разбор присланного сообщения, хранение, подстановка во все упоминания."""

import asyncio
from datetime import datetime

import pytest
from aiogram.types import Chat, Message, MessageEntity

from guard import db, nicks, out


def msg(text: str, *ents: tuple) -> Message:
    entities = [MessageEntity(type=t, offset=o, length=n, **({"custom_emoji_id": e[0]} if e else {}))
                for t, o, n, *e in ents]
    return Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type="private"), text=text,
                   entities=entities or None)


def test_parse_head_tail_and_formatting():
    # 🔥 Вася 💎, жирный на всём сообщении (вместе с эмодзи): жирным остаётся только ник
    m = msg("🔥 Вася 💎", ("custom_emoji", 0, 2, "111"), ("custom_emoji", 8, 2, "222"), ("bold", 0, 10))
    n = nicks.parse(m)
    assert n.head == '<tg-emoji emoji-id="111">🔥</tg-emoji> '
    assert n.body == "<b>Вася</b>"
    assert n.tail == ' <tg-emoji emoji-id="222">💎</tg-emoji>'
    assert n.plain == "Вася"
    n = nicks.parse(msg("Вася Пупкин", ("italic", 5, 6), ("spoiler", 0, 4)))
    assert n.head == n.tail == "" and n.body == "<tg-spoiler>Вася</tg-spoiler> <i>Пупкин</i>"
    assert nicks.preview(n, 7).startswith('<a href="tg://user?id=7">')


@pytest.mark.parametrize("m, error", [
    (msg("Ва🔥ся", ("custom_emoji", 2, 2, "1")), "в середине"),
    (msg("🔥", ("custom_emoji", 0, 2, "1")), "сам ник"),
    (msg("x" * 25), "до 24"),
    (msg("@admin"), "@"),
    (msg("сайт", ("text_link", 0, 4)), "ссылки"),
    (msg("🔥🔥🔥 a 🔥", ("custom_emoji", 0, 2, "1"), ("custom_emoji", 2, 2, "1"), ("custom_emoji", 4, 2, "1"),
         ("custom_emoji", 9, 2, "1")), "Не больше 3"),
])
def test_parse_errors(m, error):
    with pytest.raises(nicks.NickError) as exc:
        nicks.parse(m)
    assert error.lower() in str(exc.value).lower()


def test_save_render_remove(tmp_path):
    async def go():
        await db.connect(tmp_path / "t.db")
        try:
            await nicks.init()
            assert out.mention(5, "Вася") == '<a href="tg://user?id=5">Вася</a>'
            n = nicks.parse(msg("🔥 Вася", ("custom_emoji", 0, 2, "111"), ("bold", 3, 4)))
            await nicks.save(5, n, 5000)
            assert out.mention(5, "Василий") == ('<tg-emoji emoji-id="111">🔥</tg-emoji> '
                                                 '<a href="tg://user?id=5"><b>Вася</b></a>')
            assert out.display(5, "Василий") == '<tg-emoji emoji-id="111">🔥</tg-emoji> <b>Вася</b>'
            assert nicks.has(5) and not nicks.has(6)
            assert await nicks.remove(5)
            assert out.mention(5, "Вася") == '<a href="tg://user?id=5">Вася</a>'
            assert not await nicks.remove(5)
        finally:
            out.NICK_HOOK = None
            nicks._cache.clear()
            await db.close()
    asyncio.run(go())
