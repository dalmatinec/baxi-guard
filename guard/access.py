"""Работать ли модератору в чате.

GUARD_NEEDS_BAXI_PAID=0: работает во всех чатах.
GUARD_NEEDS_BAXI_PAID=1: только если есть своя подписка от владельца (own_until) или Бакси в этом чате
оплачен или на пробном. Пока Бакси без API, такие чаты считаются разрешёнными, чтобы защита не пропадала.
"""

from guard import config, db


def own_active(chat: dict) -> bool:
    until = chat.get("own_until") or 0
    return until == -1 or until > db.now()


async def status(chat: dict) -> tuple[bool, str]:
    """(разрешён ли, понятная строка для /chat и карточки)."""
    if not config.GUARD_NEEDS_BAXI_PAID:
        return True, "работает во всех чатах"
    if own_active(chat):
        return True, "своя подписка"
    return True, "проверка оплаты Бакси пока не подключена, чат разрешён"


async def allowed(chat: dict) -> bool:
    return (await status(chat))[0]
