"""API для веб-админки (будет позже). Те же тексты и кнопки, что в боте, ничего отдельного.

Слушает тот же 127.0.0.1:GUARD_API_PORT, что и API для Бакси, но свой ключ: заголовок X-Key: <WEB_API_KEY>.
Наружу его не открываем: веб-админка будет стоять на этом же сервере и ходить сюда сама.
Пока весь доступ как у владельца бота; вход админов чатов через Telegram добавится вместе с веб-админкой.

Премиум-эмодзи в поле "text" видны как {emoji:5368324170671202286:🔥} (см. guard/richtext.py), в поле "html"
как их понимает Telegram. Сохранять можно любым из двух. chat=0: тексты бота для всех чатов.

GET    /v1/admin/meta                      разделы текстов, цвета кнопок, формат премиум-эмодзи
GET    /v1/admin/chats                     чаты бота
GET    /v1/admin/texts?chat=0              все тексты: стандартный, общий, свой, какой действует
GET    /v1/admin/texts/{key}?chat=0        один текст
PUT    /v1/admin/texts/{key}?chat=0        {"text": "...", или "html": "...", "photo": "file_id", "buttons": "..."}
DELETE /v1/admin/texts/{key}?chat=0        вернуть стандартный (или общий)
GET    /v1/admin/buttons                   виды кнопок бота с цветом и значком
PUT    /v1/admin/buttons/{id}              {"style": "primary|success|danger|plain|", "icon": "ID|"}
GET    /v1/admin/emoji                     набор премиум-эмодзи владельца"""

from aiohttp import web

from guard import access, buttons, db, out, richtext, texts

PREFIX = "/v1/admin/"


def _err(text: str, status: int = 400) -> web.Response:
    from guard.api import _json
    return _json({"ok": False, "error": text}, status)


def _ok(data: dict) -> web.Response:
    from guard.api import _json
    return _json({"ok": True, **data})


def _value(row: dict | None) -> dict | None:
    if not row:
        return None
    rows, _ = texts.parse_buttons(row.get("buttons", ""), design=True)
    return {"html": row.get("html", ""), "text": richtext.to_web(row.get("html", "")),
            "emoji": richtext.emoji_of(row.get("html", "")), "photo": row.get("photo", ""),
            "buttons": row.get("buttons", ""),
            "buttons_parsed": [[{"text": b.text, "url": b.url, "style": b.style, "icon": b.icon} for b in r]
                               for r in rows],
            "updated_at": row.get("updated_at", 0), "updated_by": row.get("updated_by", "")}


async def _item(chat_id: int, key: str) -> dict:
    group, title, default, subs = texts.TEXTS[key]
    own = await texts.override(chat_id, key)
    common = await texts.override(texts.GLOBAL, key) if chat_id != texts.GLOBAL else None
    t = await texts.template(chat_id, key)
    return {"key": key, "group": group, "title": title,
            "placeholders": {k: {"about": a, "example": e} for k, (a, e) in subs.items()},
            "default": {"html": default, "text": richtext.to_web(default)},
            "own": _value(own), "common": _value(common),
            "effective": {"html": t.html, "text": richtext.to_web(t.html), "photo": t.photo,
                          "buttons": texts.buttons_text(t.buttons)}}


async def _chat_of(request: web.Request) -> int | None:
    """chat=0 или известный боту чат; иначе None."""
    chat_id = int(request.query.get("chat", "0"))
    if chat_id == texts.GLOBAL or await db.get_chat(chat_id):
        return chat_id
    return None


async def meta(_request: web.Request) -> web.Response:
    return _ok({"groups": [{"name": g, "icon": texts.GROUP_ICONS.get(g, "📝"), "keys": texts.keys_of(g)}
                           for g in texts.groups()],
                "styles": {"primary": "синяя", "success": "зелёная", "danger": "красная", "plain": "без цвета",
                           "": "как в коде"},
                "emoji_format": "{emoji:ID:🔥}", "max_html": texts.MAX_HTML, "max_caption": texts.MAX_CAPTION,
                "buttons_format": "Текст - ссылка, в ряд через |, цвет в конце #синяя #зелёная #красная, "
                                  "значок в начале {emoji:ID:🔥}"})


async def chats(_request: web.Request) -> web.Response:
    result = []
    for c in await db.all_chats():
        result.append({"chat_id": c["chat_id"], "title": c["title"], "username": c["username"],
                       "active": await access.allowed(c), "custom_texts": len(await texts.custom_keys(c["chat_id"]))})
    return _ok({"chats": result})


async def texts_list(request: web.Request) -> web.Response:
    chat_id = await _chat_of(request)
    if chat_id is None:
        return _err("unknown chat", 404)
    return _ok({"chat": chat_id, "items": [await _item(chat_id, k) for k in texts.TEXTS]})


async def text_get(request: web.Request) -> web.Response:
    chat_id, key = await _chat_of(request), request.match_info["key"]
    if chat_id is None or key not in texts.TEXTS:
        return _err("not found", 404)
    return _ok({"chat": chat_id, "item": await _item(chat_id, key)})


async def text_put(request: web.Request) -> web.Response:
    chat_id, key = await _chat_of(request), request.match_info["key"]
    if chat_id is None or key not in texts.TEXTS:
        return _err("not found", 404)
    try:
        body = await request.json()
    except ValueError:
        return _err("нужен JSON")
    if not isinstance(body, dict):
        return _err("нужен JSON-объект")
    fields = {}
    if "text" in body or "html" in body:
        raw = body.get("html") if "html" in body else richtext.from_web(str(body.get("text") or ""))
        html = richtext.normalize_html(str(raw or ""))
        if len(html) > texts.MAX_HTML:
            return _err(f"длиннее {texts.MAX_HTML} символов")
        problem = richtext.check_html(html)
        if problem:
            return _err(problem)
        fields["html"] = html
    if "photo" in body:
        fields["photo"] = str(body["photo"] or "")[:256]
    if "buttons" in body:
        rows, bad = texts.parse_buttons(str(body["buttons"] or ""), design=True)
        if bad:
            return _err("не понял кнопки: " + ", ".join(bad)[:300])
        fields["buttons"] = texts.buttons_text(rows)
    if not fields:
        return _err("нечего сохранять: text, html, photo или buttons")
    await texts.set_text(chat_id, key, by="web", **fields)
    return _ok({"chat": chat_id, "item": await _item(chat_id, key)})


async def text_delete(request: web.Request) -> web.Response:
    chat_id, key = await _chat_of(request), request.match_info["key"]
    if chat_id is None or key not in texts.TEXTS:
        return _err("not found", 404)
    await texts.reset(chat_id, key)
    return _ok({"chat": chat_id, "item": await _item(chat_id, key)})


def _button(r: dict) -> dict:
    return {k: r[k] for k in ("id", "pattern", "sample", "style", "icon", "seen_at", "updated_at")}


async def buttons_list(_request: web.Request) -> web.Response:
    return _ok({"buttons": [_button(r) for r in buttons.all_rows()]})


async def button_put(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return _err("нужен JSON")
    style = body.get("style") if isinstance(body, dict) else None
    icon = body.get("icon") if isinstance(body, dict) else None
    try:
        r = await buttons.set_design(int(request.match_info["id"]),
                                     style=None if style is None else str(style),
                                     icon=None if icon is None else str(icon))
    except ValueError:
        return _err("style: primary, success, danger, plain или пусто; icon: ID премиум-эмодзи или пусто")
    if r is None:
        return _err("not found", 404)
    return _ok({"button": _button(r)})


async def emoji(_request: web.Request) -> web.Response:
    return _ok({"emoji": [{"alt": a, "id": i} for a, i in out.PACK.items()]})


def add_routes(app: web.Application) -> None:
    app.router.add_get(PREFIX + "meta", meta)
    app.router.add_get(PREFIX + "chats", chats)
    app.router.add_get(PREFIX + "texts", texts_list)
    app.router.add_get(PREFIX + "texts/{key}", text_get)
    app.router.add_put(PREFIX + "texts/{key}", text_put)
    app.router.add_delete(PREFIX + "texts/{key}", text_delete)
    app.router.add_get(PREFIX + "buttons", buttons_list)
    app.router.add_put(PREFIX + r"buttons/{id:\d+}", button_put)
    app.router.add_get(PREFIX + "emoji", emoji)
