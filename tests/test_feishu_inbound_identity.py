"""2026-07-03 audit B2 (safe half): feishu inbound identity + idempotency.

- message-id-less WS frames used to bypass the IdempotencyStore ("fall
  through and are processed") — a re-delivered frame double-fired.
- The message path had no per-sender gate: any chat member addressing the
  bot drove the agent. `runtime.feishu_inbound.allowed_senders` (opt-in,
  empty = unchanged behavior) closes that.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.feishu_consume import ingest_feishu_event
from zf.cli.main import main
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog


def _config_dict(allowed_senders: list[str] | None = None) -> dict:
    config = {
        "version": "1.0",
        "project": {"name": "inbound-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_routing": {
                "oc_team": {"target": "channel", "channel_id": "ch-dev",
                            "default_member": "dev"},
            },
        },
    }
    if allowed_senders is not None:
        config["runtime"] = {
            "feishu_inbound": {"allowed_senders": allowed_senders},
        }
    return config


@pytest.fixture
def make_project(tmp_path: Path, monkeypatch):
    def _make(allowed_senders: list[str] | None = None) -> Path:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "sek")
        (tmp_path / "zf.yaml").write_text(yaml.dump(_config_dict(allowed_senders)))
        main(["init"])
        return tmp_path

    return _make


def _msg(chat_id: str, text: str, message_id: str = "",
         user_id: str = "ou_member", create_time: str = "1751500000000") -> dict:
    payload = {"text": text, "create_time": create_time}
    if message_id:
        payload["message_id"] = message_id
    return {"type": "message", "payload": payload,
            "user_id": user_id, "chat_id": chat_id}


def _events(root: Path):
    return EventLog(root / ".zf" / "events.jsonl").read_all()


def test_message_id_less_frame_dedups_on_content(make_project):
    root = make_project()
    ctx = resolve_project_context()
    frame = _msg("oc_team", "hello @dev")
    first = ingest_feishu_event(frame, context=ctx)
    assert first.get("status") != "duplicate"
    second = ingest_feishu_event(frame, context=ctx)
    assert second.get("status") == "duplicate", (
        "re-delivered message-id-less frame must not double-fire"
    )


def test_message_id_less_distinct_messages_both_process(make_project):
    root = make_project()
    ctx = resolve_project_context()
    first = ingest_feishu_event(_msg("oc_team", "first"), context=ctx)
    second = ingest_feishu_event(
        _msg("oc_team", "second", create_time="1751500009999"), context=ctx)
    assert first.get("status") != "duplicate"
    assert second.get("status") != "duplicate"


def test_allowed_senders_blocks_stranger_and_emits_audit(make_project):
    root = make_project(allowed_senders=["ou_boss"])
    ctx = resolve_project_context()
    result = ingest_feishu_event(
        _msg("oc_team", "do something", "m-stranger"), context=ctx)
    assert result == {"status": "dropped", "reason": "sender_not_allowed",
                      "chat_id": "oc_team"}
    blocked = [e for e in _events(root)
               if e.type == "feishu.inbound.sender_blocked"]
    assert len(blocked) == 1
    assert blocked[0].payload["user_id"] == "ou_member"


def test_allowed_senders_admits_listed_sender(make_project):
    root = make_project(allowed_senders=["ou_boss"])
    ctx = resolve_project_context()
    result = ingest_feishu_event(
        _msg("oc_team", "ship it", "m-boss", user_id="ou_boss"), context=ctx)
    assert result.get("status") not in {"dropped", "duplicate"}
    assert not [e for e in _events(root)
                if e.type == "feishu.inbound.sender_blocked"]


def test_empty_allowlist_keeps_existing_behavior(make_project):
    root = make_project()
    ctx = resolve_project_context()
    result = ingest_feishu_event(_msg("oc_team", "hi", "m-any"), context=ctx)
    assert result.get("status") not in {"dropped", "duplicate"}


def test_allowed_senders_must_be_a_list(tmp_path: Path):
    config = _config_dict()
    config["runtime"] = {"feishu_inbound": {"allowed_senders": "ou_boss"}}
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.dump(config))
    with pytest.raises(ConfigError, match="allowed_senders"):
        load_config(path)
