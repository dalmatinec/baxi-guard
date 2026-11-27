"""Клиент внутреннего API Бакси (127.0.0.1): оплачен ли чат, кто доверенный, и события о действиях.

Если Бакси недоступен, событие ложится в outbox и досылается раз в минуту (до 24 часов).
Таймаут каждого запроса 3 секунды. Пока у Бакси нет API, модератор работает сам по себе."""

import logging
import time

import httpx

from guard import config, db, out

log = logging.getLogger(__name__)

EVENT_TYPES = {"warn", "unwarn", "mute", "unmute", "ban", "unban", "kick", "spam"}
INFO_TTL = 600        # оплату чата спрашиваем не чаще раза в 10 минут
TRUSTED_TTL = 300     # доверенных раз в 5 минут
FAIL_PAUSE = 60       # Бакси не ответил: минуту не дёргаем его на каждое сообщение
STALE_OK = 86400      # старый ответ про оплату годится сутки, пока Бакси лежит

_client: httpx.AsyncClient | None = None
_info: dict[int, tuple[float, dict]] = {}
_trusted: dict[int, tuple[float, set[int]]] = {}
_down_until = 0.0
state = {"last_ok": 0.0, "last_error": ""}


def configured() -> bool:
    return bool(config.BAXI_API_URL and config.BAXI_API_KEY)


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=config.BAXI_API_URL, timeout=config.API_TIMEOUT,
                                    headers={"X-Key": config.BAXI_API_KEY})
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _ok() -> None:
    state["last_ok"] = time.time()
    state["last_error"] = ""


def _fail(exc: Exception | str) -> None:
    global _down_until
    _down_until = time.monotonic() + FAIL_PAUSE
    state["last_error"] = str(exc)[:200] or exc.__class__.__name__


def describe() -> str:
    """Для /admin: как дела со связью."""
    if not configured():
        return "не настроен"
    if state["last_ok"] and not state["last_error"]:
        ago = int((time.time() - state["last_ok"]) // 60)
        return f"на связи (ответ {ago} мин назад)" if ago else "на связи"
    if state["last_error"]:
        return "нет связи"
    return "ещё не спрашивал"


async def _get(path: str) -> dict | None:
    if not configured() or time.monotonic() < _down_until:
        return None
    try:
        resp = await client().get(path)
    except httpx.HTTPError as exc:
        _fail(exc)
        return None
    if resp.status_code != 200:
        _fail(f"HTTP {resp.status_code}")
        return None
    _ok()
    try:
        return resp.json()
    except ValueError:
        return None


async def chat_info(chat_id: int) -> dict | None:
    """Ответ Бакси про чат. None: Бакси не настроен или не отвечает и старого ответа нет."""
    hit = _info.get(chat_id)
    if hit and time.monotonic() - hit[0] < INFO_TTL:
        return hit[1]
    data = await _get(f"/v1/chat/{chat_id}")
    if data is not None:
        _info[chat_id] = (time.monotonic(), data)
        return data
    if hit and time.monotonic() - hit[0] < STALE_OK:
        return hit[1]
    return None


async def trusted(chat_id: int) -> set[int]:
    """Админы и VIP у Бакси: их автоматика не трогает."""
    hit = _trusted.get(chat_id)
    if hit and time.monotonic() - hit[0] < TRUSTED_TTL:
        return hit[1]
    data = await _get(f"/v1/chat/{chat_id}/trusted")
    if data is None:
        return hit[1] if hit else set()
    ids = set()
    for key in ("admins", "vip"):
        for x in data.get(key) or []:
            try:
                ids.add(int(x))
            except (TypeError, ValueError):
                pass
    _trusted[chat_id] = (time.monotonic(), ids)
    return ids


async def wallet(user_id: int) -> int | None:
    """Монеты человека у Бакси или None, если Бакси не настроен или не отвечает."""
    data = await wallet_info(user_id)
    return int(data["coins"]) if data and "coins" in data else None


async def wallet_info(user_id: int) -> dict | None:
    """{"coins", "level"} у Бакси или None."""
    return await _get(f"/v1/wallet/{user_id}")


async def spend(user_id: int, amount: int, ref: str, reason: str) -> dict | None:
    """Списать монеты у Бакси. {"ok": True, "coins"} / {"ok": False, "error": "not_enough", "coins"} /
    None: Бакси не отвечает (ничего не списано). Тот же ref Бакси второй раз не спишет."""
    if not configured():
        return None
    try:
        resp = await client().post("/v1/coins/spend", json={"user_id": user_id, "amount": amount, "ref": ref,
                                                            "reason": reason})
    except httpx.HTTPError as exc:
        _fail(exc)
        return None
    if resp.status_code != 200:
        _fail(f"HTTP {resp.status_code}")
        return None
    _ok()
    try:
        return resp.json()
    except ValueError:
        return None


def paid(info: dict) -> bool:
    return bool(info.get("known") and info.get("active") and (info.get("paid") or info.get("trial")))


# ---------- события ----------

async def _post(payload: dict) -> str:
    """'ok', 'retry' (Бакси недоступен, повторить позже) или 'drop' (повторять бессмысленно)."""
    try:
        resp = await client().post("/v1/events", json=payload)
    except httpx.HTTPError as exc:
        _fail(exc)
        return "retry"
    if resp.status_code == 200:
        _ok()
        return "ok"
    if resp.status_code == 429 or resp.status_code >= 500:
        _fail(f"HTTP {resp.status_code}")
        return "retry"
    _fail(f"HTTP {resp.status_code}")
    log.warning("Бакси не принял событие %s: HTTP %s", payload.get("type"), resp.status_code)
    return "drop"


async def send_event(payload: dict) -> None:
    if not configured():
        return
    if time.monotonic() < _down_until or await _post(payload) == "retry":
        await db.outbox_add(payload)


def emit(kind: str, chat: dict, user_id: int, by_id: int, until: int, reason: str, warns: int | None) -> None:
    """Событие для Бакси в фоне, чтобы не задерживать ответ в чате."""
    if not configured() or kind not in EVENT_TYPES:
        return

    async def go():
        try:
            count = warns if warns is not None else await db.count_warns(chat["chat_id"], user_id,
                                                                          chat["warn_ttl_days"])
            await send_event({"type": kind, "chat_id": chat["chat_id"], "user_id": user_id, "by_id": by_id,
                              "until": until, "reason": reason, "warns": count, "warn_limit": chat["warn_limit"],
                              "ts": db.now()})
        except Exception:
            log.exception("Не отправил событие Бакси")
    out.spawn(go())


async def flush_outbox() -> None:
    """Раз в минуту: дослать накопленное, выбросить старше суток."""
    dropped = await db.outbox_purge(86400)
    if dropped:
        log.info("Outbox: выброшено %s событий старше суток", dropped)
    if not configured():
        return
    for item in await db.outbox_due(100):
        result = await _post(item["payload"])
        if result == "retry":
            await db.outbox_retry(item["id"], 60)
            break  # Бакси лежит: остальное тоже не пройдёт, ждём минуту
        await db.outbox_done(item["id"])
