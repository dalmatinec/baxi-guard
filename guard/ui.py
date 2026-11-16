"""Кнопки для лички. Один цвет акцента на экран: style ставим только главной кнопке экрана."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def btn(text: str, data: str = "", *, url: str = "", accent: bool = False) -> InlineKeyboardButton:
    style = "primary" if accent else None
    if url:
        return InlineKeyboardButton(text=text, url=url, style=style)
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def kb(*rows: list[InlineKeyboardButton] | InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r if isinstance(r, list) else [r] for r in rows])


def onoff(value) -> str:
    return "✅ вкл" if value else "▫️ выкл"


def toggle_text(value, name: str) -> str:
    return f"{'✅' if value else '▫️'} {name}"


def cycle(options: list, current):
    """Следующее значение по кругу."""
    try:
        return options[(options.index(current) + 1) % len(options)]
    except ValueError:
        return options[0]
