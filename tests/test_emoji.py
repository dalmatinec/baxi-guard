"""Премиум-эмодзи: замена в тексте и значок на кнопке."""

from aiogram.types import InlineKeyboardButton

from guard import out


def setup_function():
    out.set_pack([("111", "🛡"), ("222", "⚙️")])


def teardown_function():
    out.set_pack([])


def test_decorate_text_not_tags():
    html = '🛡 <b>Модерация</b> <a href="tg://user?id=1">🛡 Вася</a> <code>🛡</code>'
    res = out.decorate(html)
    assert res.startswith('<tg-emoji emoji-id="111">🛡</tg-emoji> <b>Модерация</b>')
    assert '<a href="tg://user?id=1">🛡 Вася</a>' in res      # внутри ссылки не трогаем
    assert "<code>🛡</code>" in res                          # и внутри кода


def test_decorate_empty_pack():
    out.set_pack([])
    assert out.decorate("🛡 текст") == "🛡 текст"


def test_button_icon():
    b = out.button_icon(InlineKeyboardButton(text="⚙️ Мои чаты", callback_data="x"))
    assert b.text == "Мои чаты" and b.icon_custom_emoji_id == "222"
    plain = out.button_icon(InlineKeyboardButton(text="Мои чаты", callback_data="x"))
    assert plain.icon_custom_emoji_id is None and plain.text == "Мои чаты"
