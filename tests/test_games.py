"""Игры со списком: запись, топы, ФК, закрытие, рандом, профиль и тексты чата."""

import asyncio
import random

import pytest

from guard import db, texts
from guard.gamecore import core, tops

CHAT = -1001234567890


def run(tmp_path, monkeypatch, fn):
    async def go():
        await db.connect(tmp_path / "g.db")
        await texts.init()
        await core.init()
        try:
            return await fn()
        finally:
            await db.close()
    return asyncio.run(go())


def test_hashtag_parse():
    assert core.normalize_hashtag("#Рулетка") == "рулетка"
    assert core.normalize_hashtag("го #рулётка!") == "рулетка"
    assert core.normalize_hashtag("без хэштега") == ""
    assert core.normalize_hashtag("a#b") == ""


@pytest.mark.parametrize("text,ok", [("привет", True), ("всем", True), ("го", False), ("ок", False), ("+", False),
                                     ("/start", False), ("ага", False), ("123456", False), ("ну да", True),
                                     ("Окей", False), ("1234 abcd", True), ("", False), (None, False)])
def test_fk_counting_rules(text, ok):
    assert core.counts_for_fk(text) is ok


def test_parse_random():
    assert core.parse_random("рандом 36") == (1, 36)
    assert core.parse_random("Random 36") == (1, 36)
    assert core.parse_random("/random 36") == (1, 36)
    assert core.parse_random("рандом 5 20") == (5, 20)
    assert core.parse_random("рандом 20 5") == (5, 20)
    assert core.parse_random("рандом") is None
    assert core.parse_random("рандом 0") is None
    assert core.parse_random("какой-то рандом 36") is None
    rnd = random.Random(1)
    assert all(1 <= core.roll(1, 36, rnd) <= 36 for _ in range(200))


def test_join_limits_and_tops(tmp_path, monkeypatch):
    async def fn():
        game = await core.create(CHAT, "roulette", "button", 0, 111)
        assert (await core.join(game, 1, "Аня", "anya"))[0]
        ok, note = await core.join(game, 1, "Аня", "anya")
        assert not ok and "уже" in note                       # обычный только раз
        await tops.give(CHAT, 2, 111)                          # постоянный топ
        assert (await core.join(game, 2, "Олег", "oleg"))[0]
        assert (await core.join(game, 3, "Петя", ""))[0]
        assert (await core.join(game, 2, "Олег", "oleg"))[0]   # второй раз позже, номер 4
        assert not (await core.join(game, 2, "Олег", "oleg"))[0]
        items = await core.entries(game["id"])
        assert [(e.pos, e.user_id, e.top) for e in items] == [(1, 1, False), (2, 2, True), (3, 3, False), (4, 2, True)]
        # временный топ на игру
        await tops.give(CHAT, 3, 111, game_id=game["id"])
        assert (await core.join(game, 3, "Петя", ""))[0]
        assert await tops.is_top(CHAT, 3, game["id"]) and not await tops.is_top(CHAT, 3, game["id"] + 1)
        # выписка сдвигает номера
        assert await core.leave(game, 1)
        assert [e.pos for e in await core.entries(game["id"])] == [1, 2, 3, 4]
        # закрыт: не записывает
        await core.update(game["id"], status="closed")
        game = await core.get(game["id"])
        assert not (await core.join(game, 9, "Новый", ""))[0]
        # конец игры: временный топ на неё снят, постоянный остался
        await core.finish(CHAT)
        assert not await tops.is_top(CHAT, 3, game["id"])
        assert await tops.is_top(CHAT, 2)
        assert await core.active(CHAT) is None
    run(tmp_path, monkeypatch, fn)


def test_fk_and_render(tmp_path, monkeypatch):
    async def fn():
        game = await core.create(CHAT, "roulette", "hashtag", 3, 111)
        assert game["status"] == "setup"
        await core.update(game["id"], hashtag="рулетка", status="open")
        game = await core.get(game["id"])
        await core.join(game, 1, "Аня", "anya")
        await core.join(game, 2, "Олег", "")
        for text in ("привет всем", "как дела", "отлично", "го"):
            await core.on_message(CHAT, 1, text)
        await core.on_message(CHAT, 2, "ок")
        await core.on_message(CHAT, 2, "здравствуйте")
        assert await core.fk_counts(game["id"]) == {1: 3, 2: 1}
        text, cut = await core.render(game)
        assert not cut
        assert "Рулетка" in text and "ФК 3" in text and "#рулетка" in text
        assert "1. " in text and "@anya" in text and "✅ 3/3" in text and "❌ 1/3" in text
        assert "Игроков: 2" in text
    run(tmp_path, monkeypatch, fn)


def test_long_list_is_cut(tmp_path, monkeypatch):
    async def fn():
        game = await core.create(CHAT, "roulette", "button", 0, 111)
        for i in range(300):
            await core.join(game, 1000 + i, f"Игрок номер {i}", f"player_{i}")
        text, cut = await core.render(game)
        assert cut and len(text) <= core.MAX_TEXT and "и ещё" in text and "Игроков: 300" in text
    run(tmp_path, monkeypatch, fn)


def test_chat_texts_override(tmp_path, monkeypatch):
    async def fn():
        assert "Список закрыт" in await texts.render(CHAT, "game_closed", count=5)
        await texts.set_text(CHAT, "game_closed", html="<b>Чат</b>: {count} 🍀",
                             buttons="Канал - https://t.me/x | Сайт - https://example.com\nМусор")
        msg = await texts.template(CHAT, "game_closed")
        assert texts.fill(msg.html, {"count": 7}) == "<b>Чат</b>: 7 🍀"
        assert [[(b.text, b.url) for b in r] for r in msg.buttons] == [[("Канал", "https://t.me/x"),
                                                                       ("Сайт", "https://example.com")]]
        assert "Список закрыт" in await texts.render(CHAT + 1, "game_closed", count=1)   # другой чат не задет
        await texts.reset(CHAT, "game_closed")
        assert "Список закрыт" in await texts.render(CHAT, "game_closed", count=2)
        rows, bad = texts.parse_buttons("A - https://a.com\nкривая строка\nB – https://b.com | C - tg://resolve?domain=x")
        assert [[(b.text, b.url) for b in r] for r in rows] == [[("A", "https://a.com")], [
            ("B", "https://b.com"), ("C", "tg://resolve?domain=x")]]
        assert bad == ["кривая строка"]
    run(tmp_path, monkeypatch, fn)
