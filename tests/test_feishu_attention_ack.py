"""L3 card-quality: the attention-ack button verb (2026-07-17).

The button is only honest if its whole chain exists: gateway auth entry,
command dispatch, and an emitted ``runtime.attention.acknowledged`` that
matches the Web attention-ack controlled action's event contract.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.integrations.feishu.gateway import AuthLevel, _COMMAND_AUTH, FeishuCommandEnvelope
from zf.cli.feishu import ATTENTION_ACK_COMMANDS, SIGNED_ACTION_COMMANDS, _handle_attention_ack_result


def test_verb_registered_in_gateway_and_signed_set() -> None:
    assert _COMMAND_AUTH["attention-ack"] is AuthLevel.OPERATOR
    assert ATTENTION_ACK_COMMANDS <= SIGNED_ACTION_COMMANDS


def test_handler_emits_acknowledged_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    envelope = FeishuCommandEnvelope(
        command="attention-ack", args=["attn-42"], user_id="ou-op", source="button",
    )

    result = _handle_attention_ack_result(envelope, state_dir, config=None)

    assert result["ok"] is True and result["status"] == "acknowledged"
    events = EventLog(state_dir / "events.jsonl").read_all()
    acked = [e for e in events if e.type == "runtime.attention.acknowledged"]
    assert len(acked) == 1
    assert acked[0].payload["attention_id"] == "attn-42"
    assert acked[0].payload["source"] == "feishu_card"
    assert acked[0].actor == "feishu:ou-op"


def test_handler_rejects_missing_attention_id(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    result = _handle_attention_ack_result(
        FeishuCommandEnvelope(command="attention-ack", args=[]), state_dir, config=None,
    )
    assert result["ok"] is False and result["status"] == "invalid_payload"
