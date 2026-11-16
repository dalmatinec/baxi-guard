"""Работать ли модератору в чате.

GUARD_NEEDS_BAXI_PAID=0: работает во всех чатах.
GUARD_NEEDS_BAXI_PAID=1: только если есть своя подписка от владельца (own_until) или Бакси в этом чате
оплачен или на пробном. Пока у Бакси нет API или он не отвечает, чат считается разрешённым,
чтобы защита не пропадала из-за сбоя."""

from datetime import datetime

from guard import baxi, config, db


def own_active(chat: dict) -> bool:
    until = chat.get("own_until") or 0
    return until == -1 or until > db.now()


async def status(chat: dict) -> tuple[bool, str]:
    """(разрешён ли, понятная строка для /chat и карточки)."""
    if not config.GUARD_NEEDS_BAXI_PAID:
        return True, "работает во всех чатах"
    if own_active(chat):
        return True, "своя подписка"
    if not baxi.configured():
        return True, "API Бакси не настроен, чат разрешён"
    info = await baxi.chat_info(chat["chat_id"])
    if info is None:
        return True, "Бакси не отвечает, чат пока разрешён"
    if baxi.paid(info):
        kind = "пробный" if info.get("trial") and not info.get("paid") else "оплачен"
        until = info.get("until") or 0
        if until:
            kind += " до " + datetime.fromtimestamp(until, config.TZ).strftime("%d.%m.%Y")
        return True, f"Бакси {kind}"
    if not info.get("known"):
        return False, "Бакси нет в этом чате, подписки нет"
    return False, "Бакси в этом чате не оплачен, подписки нет"


async def allowed(chat: dict) -> bool:
    return (await status(chat))[0]
