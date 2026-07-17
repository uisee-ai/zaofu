"""OBS-A5-1 regression: web action endpoints must not block the event loop.

A sync:true action runs the whole headless turn inside `_web_action` (observed
525s). Before the fix the route handler called it directly on the asyncio event
loop, freezing every other GET/SSE for the turn's duration. The handler now
offloads `_web_action` to a worker thread, so the loop stays responsive.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("httpx")

import httpx

from zf.web.server import create_app


async def test_action_endpoint_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    # Stand in for a slow sync:true turn: a blocking call inside _web_action.
    entered = threading.Event()

    def slow_action(*_args, **_kwargs):  # noqa: ANN001 - test stub
        entered.set()
        time.sleep(1.5)
        return {"ok": True, "status": "done", "_status_code": 200}

    monkeypatch.setattr("zf.web.server._web_action", slow_action)

    app = create_app(state_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = time.monotonic()
        slow = asyncio.ensure_future(client.post("/api/actions/noop", json={}))
        # Give the POST handler time to reach its thread-offload point.
        await asyncio.sleep(0.05)

        # If _web_action ran on the loop, this GET could not even begin until the
        # 1.5s action finished. Offloaded, the loop serves it right away.
        health = await client.get("/healthz")
        total = time.monotonic() - start

        assert entered.is_set()
        assert health.status_code == 200
        assert total < 0.7, f"event loop blocked by sync action: GET landed at {total:.2f}s"

        resp = await slow
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
