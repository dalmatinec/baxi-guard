"""Приветствие: кнопки из строк в конце сообщения; капча: задания и ответы."""

from guard import welcome
from guard.handlers.welcome import split_buttons


def test_split_buttons():
    body, buttons = split_buttons("Привет, {user}!\nЗаходи\n\nПравила - https://t.me/r | Канал - https://t.me/c", False)
    assert body == "Привет, {user}!\nЗаходи"
    assert buttons == "Правила - https://t.me/r | Канал - https://t.me/c"
    body, buttons = split_buttons("Просто текст - без ссылки", False)
    assert buttons == "" and body == "Просто текст - без ссылки"
    _, buttons = split_buttons("Текст\nКанал - https://t.me/c #зелёная", True)
    assert buttons == "Канал - https://t.me/c #зелёная"            # цвет только у владельца
    _, buttons = split_buttons("Текст\nКанал - https://t.me/c #зелёная", False)
    assert buttons == "Канал - https://t.me/c"


def test_captcha_tasks():
    for mode in ("button", "math", "emoji"):
        text, markup, answer = welcome.make_task(mode, 42)
        datas = [b.callback_data for r in markup.inline_keyboard for b in r]
        assert all(d.startswith("cp:42:") for d in datas)
        assert f"cp:42:{answer}" in datas                      # правильный ответ среди кнопок
        assert len(set(datas)) == len(datas)                   # без повторов
    text, _, answer = welcome.make_task("math", 1)
    x, y = [int(n) for n in text.replace("?", "").split() if n.isdigit()]
    assert int(answer) == x + y
    assert welcome.greets({"welcome_on": 0, "captcha": "math"}) and not welcome.greets({"welcome_on": 0, "captcha": ""})
