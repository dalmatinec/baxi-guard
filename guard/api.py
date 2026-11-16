"""Внутренний API модератора для Бакси. Слушает только 127.0.0.1, снаружи его нет.
Каждый запрос с заголовком X-Key: <BAXI_API_KEY>, без ключа 401. Всё в JSON, время в unix-секундах.

GET /v1/ping                           {"ok": true, "version": "..."}
GET /v1/user/{chat_id}/{user_id}       {"warns": 1, "warn_limit": 3, "muted_until": 0, "banned": false, "muted": false}
GET /v1/chat/{chat_id}/stats?days=1    {"mute": 2, "warn": 3, "ban": 0, "kick": 0, "spam": 5}
GET /v1/chat/{chat_id}/settings        какие защиты включены

muted_until: 0 не замучен, иначе до какого времени. Мут навсегда: FOREVER_TS (и "muted": true)."""

import functools
import hmac
import json
import logging

from aiohttp import web

from guard import VERSION, access, config, db, health

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
FOREVER_TS = 4102444800  # 01.01.2100: «навсегда» для тех, кто сравнивает muted_until с текущим временем
STAT_KINDS = ("mute", "warn", "ban", "kick", "spam")
DEFAULT_WARN_LIMIT = 3


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=functools.partial(json.dumps, ensure_ascii=False))


@web.middleware
async def auth(request: web.Request, handler):
    key = request.headers.get("X-Key", "")
    if not config.BAXI_API_KEY or not hmac.compare_digest(key.encode(), config.BAXI_API_KEY.encode()):
        return _json({"ok": False, "error": "unauthorized"}, 401)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ValueError:
        return _json({"ok": False, "error": "bad request"}, 400)
    except Exception:
        log.exception("Ошибка API на %s", request.path)
        return _json({"ok": False, "error": "internal"}, 500)


def _id(request: web.Request, name: str) -> int:
    return int(request.match_info[name])


async def ping(_request: web.Request) -> web.Response:
    return _json({"ok": True, "version": VERSION})


async def user(request: web.Request) -> web.Response:
    chat_id, user_id = _id(request, "chat_id"), _id(request, "user_id")
    chat = await db.get_chat(chat_id)
    limit = chat["warn_limit"] if chat else DEFAULT_WARN_LIMIT
    ttl = chat["warn_ttl_days"] if chat else 30
    warns = await db.count_warns(chat_id, user_id, ttl)
    st = await db.user_state(chat_id, user_id)
    muted_until = FOREVER_TS if st["muted_until"] == -1 else st["muted_until"]
    return _json({"warns": warns, "warn_limit": limit, "muted_until": muted_until, "banned": st["banned"],
                  "muted": bool(muted_until)})


async def stats(request: web.Request) -> web.Response:
    chat_id = _id(request, "chat_id")
    days = int(request.query.get("days", "1"))
    days = min(90, max(1, days))
    counts = await db.stats(chat_id, db.now() - days * 86400)
    return _json({k: counts.get(k, 0) for k in STAT_KINDS})


async def settings(request: web.Request) -> web.Response:
    chat_id = _id(request, "chat_id")
    chat = await db.get_chat(chat_id)
    if chat is None:
        return _json({"known": False})
    service = {k[4:]: bool(chat[k]) for k in ("svc_join", "svc_leave", "svc_pin", "svc_photo", "svc_title")}
    attack = chat["attack_until"] > db.now()
    return _json({
        "known": True,
        "active": await access.allowed(chat),
        "stopwords": bool(chat["stop_on"]),
        "antispam": bool(chat["spam_on"]),
        "service": any(service.values()),
        "service_detail": service,
        "attack": attack,
        "attack_until": chat["attack_until"] if attack else 0,
        "attack_auto": bool(chat["attack_auto"]),
        "warn_limit": chat["warn_limit"],
        "warn_action": chat["warn_action"],
        "warn_ttl_days": chat["warn_ttl_days"],
        "log": bool(chat["log_chat_id"]),
    })


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth])
    app.router.add_get("/v1/ping", ping)
    app.router.add_get(r"/v1/user/{chat_id:-?\d+}/{user_id:-?\d+}", user)
    app.router.add_get(r"/v1/chat/{chat_id:-?\d+}/stats", stats)
    app.router.add_get(r"/v1/chat/{chat_id:-?\d+}/settings", settings)
    return app


async def start() -> web.AppRunner | None:
    """Поднять сервер. Без ключа в .env не поднимаем: открытый API без ключа не нужен."""
    if not config.BAXI_API_KEY:
        log.info("BAXI_API_KEY пуст: внутренний API не запущен")
        health.api_state = "выключен (нет BAXI_API_KEY)"
        return None
    runner = web.AppRunner(make_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, HOST, config.GUARD_API_PORT)
    try:
        await site.start()
    except OSError as exc:
        log.error("Внутренний API не запустился на %s:%s: %s", HOST, config.GUARD_API_PORT, exc)
        health.api_state = f"ошибка запуска: {exc}"
        await runner.cleanup()
        return None
    log.info("Внутренний API слушает %s:%s", HOST, config.GUARD_API_PORT)
    health.api_state = f"{HOST}:{config.GUARD_API_PORT}"
    return runner
