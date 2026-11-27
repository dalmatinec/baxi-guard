"""Премиум-эмодзи для веб-админки, проверка HTML, кнопки с цветом, общие тексты бота, оформление кнопок,
активность чата и API веб-админки."""

import asyncio

from aiogram.types import InlineKeyboardButton
from aiohttp.test_utils import TestClient, TestServer

from guard import activity, api, buttons, config, db, out, richtext, texts

CHAT = -1001234567890
KEY = "web-key"
H = {"X-Key": KEY}


def run(tmp_path, monkeypatch, fn):
    monkeypatch.setattr(config, "WEB_API_KEY", KEY)
    monkeypatch.setattr(config, "BAXI_API_KEY", "baxi-key")

    async def go():
        await db.connect(tmp_path / "t.db")
        try:
            await texts.init()
            await buttons.init()
            await db.ensure_chat(CHAT, "Тестовый чат", "")
            return await fn()
        finally:
            out.BUTTON_HOOK = None
            await db.close()
    return asyncio.run(go())


def test_emoji_round_trip():
    html = 'Привет <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji> <b>мир</b>'
    web = richtext.to_web(html)
    assert web == "Привет {emoji:5368324170671202286:🔥} <b>мир</b>"
    assert richtext.from_web(web) == html
    assert richtext.emoji_of(html) == [{"id": "5368324170671202286", "alt": "🔥"}]
    assert richtext.from_web("{emoji:5368324170671202286}") == '<tg-emoji emoji-id="5368324170671202286">⭐</tg-emoji>'
    assert richtext.from_web("{user} и {emoji:12}") == "{user} и {emoji:12}"   # подстановки и мусор не трогает


def test_check_html():
    assert richtext.check_html("<b>ok</b> <a href='https://x.y'>link</a> <tg-spoiler>s</tg-spoiler>") == ""
    assert "div" in richtext.check_html("<div>x</div>")
    assert richtext.check_html("<b>x") != ""
    assert richtext.check_html("<b><i>x</b></i>") != ""
    assert richtext.check_html('<a onclick="x">y</a>') != ""
    assert richtext.normalize_html("A & B &amp; C &#39;") == "A &amp; B &amp; C &#39;"


def test_buttons_design_only_for_owner():
    raw = "{emoji:5368324170671202286:🔥} Канал - https://t.me/x #зелёная | Сайт - https://e.com #red"
    rows, bad = richtext.parse_buttons(raw, design=True)
    assert not bad
    a, b = rows[0]
    assert (a.text, a.url, a.style, a.icon) == ("Канал", "https://t.me/x", "success", "5368324170671202286")
    assert b.style == "danger" and not b.icon
    assert richtext.parse_buttons(richtext.buttons_text(rows), design=True)[0] == rows   # туда и обратно
    plain, _ = richtext.parse_buttons(raw, design=False)
    assert (plain[0][0].style, plain[0][0].icon, plain[0][0].text) == ("", "", "Канал")


def test_global_texts(tmp_path, monkeypatch):
    async def fn():
        assert "Список закрыт" in await texts.render(CHAT, "game_closed", count=1)
        await texts.set_text(texts.GLOBAL, "game_closed", html="Общий: {count}", buttons="A - https://a.com #синяя",
                             by="web")
        assert await texts.render(CHAT, "game_closed", count=2) == "Общий: 2"          # общий для всех
        t = await texts.template(CHAT, "game_closed")
        kb = texts.markup(t)
        assert kb.inline_keyboard[0][0].style == "primary"
        await texts.set_text(CHAT, "game_closed", html="Свой: {count}", by=1)
        assert await texts.render(CHAT, "game_closed", count=3) == "Свой: 3"           # свой важнее общего
        assert (await texts.template(CHAT, "game_closed")).buttons[0][0].text == "A"  # кнопки от общего
        own = await texts.override(CHAT, "game_closed")
        assert own["updated_by"] == "1" and own["updated_at"] > 0
    run(tmp_path, monkeypatch, fn)


def test_button_design(tmp_path, monkeypatch):
    async def fn():
        assert buttons.pattern_of("g:tz:-100123:del:7") == "g:tz:*:del:*"
        b = InlineKeyboardButton(text="🗑 Удалить", callback_data="g:tz:-100123:del:7")
        assert buttons.apply(b) is b              # новая кнопка: запомнили, не меняли
        await asyncio.sleep(0.05)                 # запись в базу в фоне
        row = buttons._cache["g:tz:*:del:*"]
        assert row["id"] and row["sample"] == "🗑 Удалить"
        await buttons.set_design(row["id"], style="danger", icon="5368324170671202286")
        other = buttons.apply(InlineKeyboardButton(text="🗑 Удалить", callback_data="g:tz:-100999:del:8"))
        assert other.style == "danger" and other.icon_custom_emoji_id == "5368324170671202286"
        assert other.text == "Удалить"            # свой эмодзи ушёл, вместо него значок
        await buttons.set_design(row["id"], style="plain", icon="")
        plain = buttons.apply(InlineKeyboardButton(text="X", callback_data="g:tz:1:del:2", style="primary"))
        assert plain.style is None
        assert buttons.strip_lead("+30 дней") == "+30 дней"
        await buttons.set_design(row["id"], style="")
        assert len(buttons.changed_rows()) == 0
    run(tmp_path, monkeypatch, fn)


def test_activity(tmp_path, monkeypatch):
    async def fn():
        for i in range(12):
            for _ in range(i + 1):
                await db.touch_member(CHAT, 100 + i, f"user{i}", f"Игрок {i}")
        chat = await db.get_chat(CHAT)
        for period in activity.PERIODS:
            messages, members, top = await db.activity(CHAT, period)
            assert messages == sum(range(1, 13)) and members == 12, period
            assert top[0]["name"] == "Игрок 11" and top[0]["n"] == 12 and len(top) == 10
        html, values = await activity.card(chat, "week")
        assert "🥇 <b>Игрок 11</b> · 12" in html and "4. Игрок 8 · 9" in html and "Тестовый чат" in html
        assert values["messages"] == "78"
        kb = activity.markup("week")
        assert [b.style for r in kb.inline_keyboard for b in r] == [None, None, "primary", None]
        assert activity.number(1239) == "1 239"
    run(tmp_path, monkeypatch, fn)


def test_web_api(tmp_path, monkeypatch):
    async def fn():
        c = TestClient(TestServer(api.make_app()))
        await c.start_server()
        try:
            assert (await c.get("/v1/admin/texts")).status == 401
            assert (await c.get("/v1/admin/texts", headers={"X-Key": "baxi-key"})).status == 401   # чужой ключ
            assert (await c.get("/v1/ping", headers=H)).status == 401                              # и наоборот
            r = await (await c.get("/v1/admin/meta", headers=H)).json()
            assert r["ok"] and r["emoji_format"] == "{emoji:ID:🔥}"
            # в вебе поставили простой текст
            r = await c.put("/v1/admin/texts/game_closed?chat=0", headers=H, json={"text": "Привет, {count}"})
            assert r.status == 200
            # в боте добавили премиум-эмодзи (так сохраняет бот: message.html_text)
            await texts.set_text(0, "game_closed", html='<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji> '
                                                        'Привет, {count}', by=42)
            item = (await (await c.get("/v1/admin/texts/game_closed?chat=0", headers=H)).json())["item"]
            assert item["own"]["text"] == "{emoji:5368324170671202286:🔥} Привет, {count}"   # веб видит ID
            assert item["own"]["emoji"][0]["id"] == "5368324170671202286"
            # чат видит общий текст как действующий
            item = (await (await c.get(f"/v1/admin/texts/game_closed?chat={CHAT}", headers=H)).json())["item"]
            assert item["own"] is None and item["common"]["updated_by"] == "42"
            assert "Привет" in item["effective"]["html"]
            # кривой HTML и кнопки не сохраняются
            r = await c.put("/v1/admin/texts/game_closed?chat=0", headers=H, json={"html": "<div>x</div>"})
            assert r.status == 400 and "div" in (await r.json())["error"]
            r = await c.put("/v1/admin/texts/game_closed?chat=0", headers=H, json={"buttons": "кривая"})
            assert r.status == 400
            r = await c.put("/v1/admin/texts/game_closed?chat=0", headers=H,
                            json={"buttons": "Канал - https://t.me/x #зелёная"})
            assert (await r.json())["item"]["own"]["buttons_parsed"][0][0]["style"] == "success"
            assert (await c.put("/v1/admin/texts/nope?chat=0", headers=H, json={"text": "x"})).status == 404
            assert (await c.get("/v1/admin/texts?chat=-1", headers=H)).status == 404
            r = await (await c.delete("/v1/admin/texts/game_closed?chat=0", headers=H)).json()
            assert r["item"]["own"] is None
            # кнопки
            buttons.apply(InlineKeyboardButton(text="✉️ Тексты", callback_data="g:te:-1001"))
            await asyncio.sleep(0.05)
            lst = (await (await c.get("/v1/admin/buttons", headers=H)).json())["buttons"]
            bid = next(b["id"] for b in lst if b["pattern"] == "g:te:*")
            r = await c.put(f"/v1/admin/buttons/{bid}", headers=H, json={"style": "success"})
            assert (await r.json())["button"]["style"] == "success"
            assert (await c.put(f"/v1/admin/buttons/{bid}", headers=H, json={"style": "pink"})).status == 400
            chats = (await (await c.get("/v1/admin/chats", headers=H)).json())["chats"]
            assert chats[0]["chat_id"] == CHAT
        finally:
            await c.close()
    run(tmp_path, monkeypatch, fn)
