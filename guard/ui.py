"""Оформление лички в одном строгом стиле.

Экран: жирный заголовок (с путём, если экран вложенный), разделитель ➖, строки «значок название · значение»,
разделитель, короткая подсказка курсивом. Кнопки в две колонки, один цвет акцента на экран (style только у главной).
Обычные эмодзи заменяются премиум-версиями, если владелец добавил их в /admin (см. out.decorate)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from guard import out

DIV = "➖" * 7          # разделитель экранов и длинных сообщений
DIV_SHORT = "➖" * 5    # в коротких сообщениях


def btn(text: str, data: str = "", *, url: str = "", accent: bool = False) -> InlineKeyboardButton:
    style = "primary" if accent else None
    if url:
        return InlineKeyboardButton(text=text, url=url, style=style)
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def kb(*rows: list[InlineKeyboardButton] | InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r if isinstance(r, list) else [r] for r in rows if r])


def grid(buttons: list[InlineKeyboardButton], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def back(data: str, text: str = "⬅️ Назад") -> InlineKeyboardButton:
    return btn(text, data)


def screen(title: str, lines: list[str], *, path: str = "", note: str = "", footer: list[str] | None = None) -> str:
    """Собрать экран. path: откуда пришли («🛡 Защита»), title: где сейчас."""
    head = f"<b>{path} › {title}</b>" if path else f"<b>{title}</b>"
    parts = [head, DIV, *lines]
    if footer:
        parts += [DIV, *footer]
    if note:
        parts += ["", f"<i>{note}</i>"]
    return "\n".join(parts)


def row(icon: str, name: str, *values: str) -> str:
    vals = [v for v in values if v]
    return f"{icon} {name}" + (" · " + " · ".join(vals) if vals else "")


def state(value) -> str:
    return "вкл" if value else "выкл"


def onoff(value) -> str:
    return "✅ вкл" if value else "▫️ выкл"


def check(value, name: str) -> str:
    return f"{'✅' if value else '▫️'} {name}"


toggle_text = check


def chat_title(chat: dict) -> str:
    return out.esc(chat.get("title") or str(chat["chat_id"]))


def cycle(options: list, current):
    """Следующее значение по кругу."""
    try:
        return options[(options.index(current) + 1) % len(options)]
    except ValueError:
        return options[0]
