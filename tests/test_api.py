"""Внутренний API модератора и досылка событий Бакси через outbox."""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from guard import api, baxi, config, db

KEY = "test-key"
CHAT = -1001234567890


def run(tmp_path, coro_fn, monkeypatch):
    monkeypatch.setattr(config, "BAXI_API_KEY", KEY)

    async def go():
        await db.connect(tmp_path / "t.db")
        try:
            return await coro_fn()
        finally:
            await baxi.close()
            await db.close()
    return asyncio.run(go())


async def client() -> TestClient:
    c = TestClient(TestServer(api.make_app()))
    await c.start_server()
    return c


def test_auth_required(tmp_path, monkeypatch):
    async def go():
        c = await client()
        try:
            assert (await c.get("/v1/ping")).status == 401
            assert (await c.get("/v1/ping", headers={"X-Key": "wrong"})).status == 401
            resp = await c.get("/v1/ping", headers={"X-Key": KEY})
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True and data["version"]
        finally:
            await c.close()
    run(tmp_path, go, monkeypatch)


def test_user_and_stats(tmp_path, monkeypatch):
    async def go():
        await db.ensure_chat(CHAT, "Тест")
        await db.add_warn(CHAT, 5, 1, "флуд")
        await db.log_action(CHAT, 5, 1, "warn")
        until = db.now() + 3600
        await db.log_action(CHAT, 5, 1, "mute", until)
        await db.log_action(CHAT, 6, 1, "ban", 0)
        await db.log_action(CHAT, 7, 0, "spam")
        c = await client()
        h = {"X-Key": KEY}
        try:
            data = await (await c.get(f"/v1/user/{CHAT}/5", headers=h)).json()
            assert data == {"warns": 1, "warn_limit": 3, "muted_until": until, "banned": False, "muted": True}
            data = await (await c.get(f"/v1/user/{CHAT}/6", headers=h)).json()
            assert data["banned"] is True and data["muted_until"] == 0
            data = await (await c.get(f"/v1/chat/{CHAT}/stats?days=1", headers=h)).json()
            assert data == {"mute": 1, "warn": 1, "ban": 1, "kick": 0, "spam": 1}
            assert (await c.get(f"/v1/chat/{CHAT}/stats?days=abc", headers=h)).status == 400
            assert (await c.get("/v1/user/abc/5", headers=h)).status == 404
        finally:
            await c.close()
    run(tmp_path, go, monkeypatch)


def test_settings(tmp_path, monkeypatch):
    async def go():
        await db.ensure_chat(CHAT, "Тест")
        await db.set_chat(CHAT, spam_on=1, svc_join=1)
        c = await client()
        h = {"X-Key": KEY}
        try:
            data = await (await c.get(f"/v1/chat/{CHAT}/settings", headers=h)).json()
            assert data["known"] and data["antispam"] and data["service"] and not data["stopwords"]
            assert data["active"] is True and data["warn_limit"] == 3
            data = await (await c.get("/v1/chat/-100777/settings", headers=h)).json()
            assert data == {"known": False}
        finally:
            await c.close()
    run(tmp_path, go, monkeypatch)


def test_outbox_when_baxi_down_then_flush(tmp_path, monkeypatch):
    got = []

    async def events(request):
        assert request.headers.get("X-Key") == KEY
        got.append(await request.json())
        return web.json_response({"ok": True})

    async def go():
        fake = web.Application()
        fake.router.add_post("/v1/events", events)
        server = TestServer(fake)
        await server.start_server()
        port = server.port
        await server.close()  # Бакси лежит
        monkeypatch.setattr(config, "BAXI_API_URL", f"http://127.0.0.1:{port}")
        await baxi.close()
        payload = {"type": "warn", "chat_id": CHAT, "user_id": 5, "by_id": 1, "until": 0, "reason": "",
                   "warns": 1, "warn_limit": 3, "ts": db.now()}
        await baxi.send_event(payload)
        assert await db.outbox_size() == 1
        # Бакси поднялся на том же порту
        server = TestServer(fake, port=port)
        await server.start_server()
        try:
            monkeypatch.setattr(baxi, "_down_until", 0.0)
            await baxi.flush_outbox()
            assert got == [payload]
            assert await db.outbox_size() == 0
        finally:
            await server.close()
    run(tmp_path, go, monkeypatch)


def test_outbox_drops_old(tmp_path, monkeypatch):
    async def go():
        await db.outbox_add({"type": "warn"})
        await db.conn().execute("UPDATE outbox SET created_at = created_at - 90000")
        await db.conn().commit()
        await baxi.flush_outbox()
        assert await db.outbox_size() == 0
    run(tmp_path, go, monkeypatch)


def test_paid_rule():
    assert baxi.paid({"known": True, "active": True, "paid": True})
    assert baxi.paid({"known": True, "active": True, "trial": True})
    assert not baxi.paid({"known": True, "active": True, "free": True})
    assert not baxi.paid({"known": False})
    assert not baxi.paid({"known": True, "active": False, "paid": True})
