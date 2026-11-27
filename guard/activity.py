"""Активность чата: сколько написали за час, сегодня, неделю и всё время, и топ-10 самых активных.
Текст карточки меняется в ✉️ Тексты (📊 Активность чата), период переключается кнопками под ней."""

from datetime import datetime

from aiogram.types import InlineKeyboardMarkup

from guard import config, db, out, texts
from guard.ui import btn, kb

PERIODS = {"hour": "🕐 Час", "day": "☀️ Сегодня", "week": "🗓 Неделя", "all": "♾ Всё время"}
MEDALS = ("🥇", "🥈", "🥉")
TOP = 10


def number(n: int) -> str:
    """1239 -> 1 239"""
    return f"{n:,}".replace(",", " ")


def period_label(period: str, chat: dict) -> str:
    now = datetime.now(config.TZ)
    if period == "hour":
        return f"Этот час, с {now:%H}:00"
    if period == "day":
        return "Сегодня с 00:00"
    if period == "week":
        return "7 дней, с " + datetime.fromtimestamp(db.now() - 6 * 86400, config.TZ).strftime("%d.%m")
    since = chat.get("created_at") or 0
    return "Всё время" + (", с " + datetime.fromtimestamp(since, config.TZ).strftime("%d.%m.%Y") if since else "")


def top_lines(top: list[dict]) -> str:
    """Имена без ссылок: топ никого не будит уведомлением."""
    lines = []
    for i, r in enumerate(top):
        name = out.display(r["user_id"], (r["name"] or r["username"] or str(r["user_id"]))[:32])
        lines.append(f"{MEDALS[i]} <b>{name}</b> · {number(r['n'])}" if i < 3 else f"{i + 1}. {name} · {number(r['n'])}")
    return "\n".join(lines) or "пока никто не писал"


def markup(period: str) -> InlineKeyboardMarkup:
    buttons = [btn(title, f"ac:{key}", accent=key == period) for key, title in PERIODS.items()]
    return kb(buttons[:2], buttons[2:])


async def card(chat: dict, period: str) -> tuple[str, dict]:
    """(готовый текст, подстановки). Текст по шаблону чата."""
    messages, members, top = await db.activity(chat["chat_id"], period, TOP)
    values = {"chat_name": out.esc(chat.get("title") or "чат"), "period": period_label(period, chat),
              "messages": number(messages), "members": number(members), "top": top_lines(top)}
    return await texts.render(chat["chat_id"], "activity", **values), values
