"""feishu B4: async dispatch — WS handler never blocks on the agent run."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from zf.cli.feishu_consume import dispatch_inbound_async
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.transport import MockFeishuTransport


def _project(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "channel_id": "ch-a", "default_member": "dev-1"}}}}))
    main(["init"])


def _event():
    return MockFeishuTransport().parse_webhook({
        "type": "message", "payload": {"text": "@dev-1 hi", "message_id": "m1"},
        "user_id": "ou_u", "chat_id": "oc_x"})


def test_caller_returns_immediately_thread_does_the_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    ctx = resolve_project_context()
    t = MockFeishuTransport()

    t0 = time.monotonic()
    fut = dispatch_inbound_async(_event(), context=ctx, transport=t)
    # the caller returned without waiting for the (background) reply
    assert (time.monotonic() - t0) < 0.5
    result = fut.result(timeout=10)  # now drain the background work
    assert result.get("status") == "replied"

    # the background thread produced the real reply (fake backend → no deltas,
    # so no stream card; a real backend's part.delta drives the card, tested in
    # test_feishu_stream_delivery).
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert [e for e in events if e.type == "channel.message.posted"
            and e.payload.get("member_id") == "dev-1"]


def test_dispatch_uses_injected_executor(tmp_path, monkeypatch):
    import concurrent.futures
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    ctx = resolve_project_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = dispatch_inbound_async(_event(), context=ctx, transport=None,
                                     executor=ex)
        assert fut.result(timeout=10).get("status") == "replied"
