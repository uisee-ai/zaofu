from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.channel_sidecar import (
    channel_context_pack_event_payload,
    channel_message_event_payload,
    hydrate_channel_context_pack_payload,
    hydrate_channel_message_text,
)
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    doctor_sidecar_refs,
    hydrate_sidecar_ref,
    iter_sidecar_ref_descriptors,
    write_sidecar_json,
    write_sidecar_text,
)


def test_write_and_hydrate_text_sidecar_descriptor(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"

    descriptor = write_sidecar_text(
        state_dir,
        "diagnostics/run-1/report.txt",
        "diagnosis ok",
        kind="diagnostic_trace",
        schema_version="diagnostic.v1",
        created_by="test",
        required=True,
        preview="diagnosis ok",
    )

    assert descriptor["ref_schema_version"] == "sidecar-ref.v1"
    assert descriptor["kind"] == "diagnostic_trace"
    assert descriptor["ref"] == "diagnostics/run-1/report.txt"
    assert descriptor["byte_count"] == len("diagnosis ok".encode("utf-8"))

    hydrated = hydrate_sidecar_ref(state_dir, descriptor)
    assert hydrated.payload == "diagnosis ok"
    assert hydrated.sha256 == descriptor["sha256"]


def test_write_and_hydrate_json_sidecar_descriptor(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    descriptor = write_sidecar_json(
        state_dir,
        "channels/ch-1/messages/msg-1.json",
        {"text": "hello"},
        kind="channel_message_body",
        schema_version="channel.message.body.v1",
        created_by="test",
    )

    hydrated = hydrate_sidecar_ref(state_dir, descriptor)

    assert hydrated.payload == {"text": "hello"}
    assert descriptor["content_type"] == "application/json"


def test_channel_message_and_context_pack_helpers_write_sidecars(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    full_text = "x" * 2600 + " @dev"
    message = channel_message_event_payload(
        state_dir,
        {
            "channel_id": "ch-1",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "test",
            "text": full_text,
            "mentions": ["dev"],
        },
        created_by="test",
    )

    assert message["schema_version"] == "channel.message.posted.v2"
    assert message["text"] != full_text
    assert "body_ref" in message
    assert message["refs"]["message_body"]["kind"] == "channel_message_body"
    assert hydrate_channel_message_text(state_dir, message, strict=True) == full_text

    context = channel_context_pack_event_payload(
        state_dir,
        {
            "channel_id": "ch-1",
            "thread_id": "main",
            "context_pack_id": "ctx-1",
            "target_member_id": "dev",
            "trigger_message_id": "msg-1",
            "message_refs": [{"message_id": "msg-1", "text_excerpt": "hello"}],
            "artifact_refs": [{"kind": "task", "id": "T1"}],
            "summary": "context summary",
            "source": "test",
        },
        created_by="test",
    )

    assert context["schema_version"] == "channel.context_pack.built.v2"
    assert context["message_ref_count"] == 1
    assert context["refs"]["context_pack"]["kind"] == "channel_context_pack"
    hydrated = hydrate_channel_context_pack_payload(state_dir, context, strict=True)
    assert hydrated["message_refs"][0]["message_id"] == "msg-1"
    assert hydrated["artifact_refs"][0]["id"] == "T1"

    refs = list(iter_sidecar_ref_descriptors({"refs": {
        "message_body": message["refs"]["message_body"],
        "context_pack": context["refs"]["context_pack"],
    }}))
    assert {ref["kind"] for ref in refs} == {"channel_message_body", "channel_context_pack"}


def test_large_feedback_sidecar_round_trips_without_inline_bloat(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    feedback = {
        "schema_version": "rework.feedback.v1",
        "task_id": "TASK-LARGE",
        "findings": [
            {
                "id": f"F-{index}",
                "summary": "preserve full feedback across agent handoff",
                "detail": "expected context " * 80,
            }
            for index in range(80)
        ],
    }
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/rework/TASK-LARGE/feedback.json",
        feedback,
        kind="rework_feedback",
        schema_version="rework.feedback.v1",
        created_by="test",
        required=True,
    )
    event = ZfEvent(
        type="review.rejected",
        payload={
            "task_id": "TASK-LARGE",
            "summary": "see feedback_ref",
            "refs": {"feedback": descriptor},
        },
    )

    event_text = json.dumps(event.payload, ensure_ascii=False)
    assert "expected context expected context" not in event_text
    assert descriptor["byte_count"] > len(event_text)
    hydrated = hydrate_sidecar_ref(state_dir, descriptor)
    assert hydrated.payload["findings"][79]["id"] == "F-79"
    assert "expected context" in hydrated.payload["findings"][79]["detail"]


def test_sidecar_ref_rejects_escape_and_reports_hash_mismatch(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    descriptor = write_sidecar_text(
        state_dir,
        "diagnostics/run-1/report.txt",
        "before",
        kind="diagnostic_trace",
        schema_version="diagnostic.v1",
        created_by="test",
    )
    (state_dir / "diagnostics" / "run-1" / "report.txt").write_text("after", encoding="utf-8")

    with pytest.raises(SidecarRefError) as mismatch:
        hydrate_sidecar_ref(state_dir, descriptor)
    assert mismatch.value.code in {"byte_count_mismatch", "hash_mismatch"}

    with pytest.raises(SidecarRefError) as escape:
        hydrate_sidecar_ref(state_dir, {**descriptor, "ref": "../secret.txt"})
    assert escape.value.code == "state_dir_mismatch"


def test_iter_sidecar_refs_accepts_legacy_raw_output_ref() -> None:
    payload = {
        "refs": {
            "raw_output": {
                "schema_version": "agent-output-raw.v1",
                "raw_ref": "artifacts/agent-session-output/run/out.txt",
                "sha256": "abc",
                "byte_count": 3,
                "mime": "text/plain",
            }
        }
    }

    refs = list(iter_sidecar_ref_descriptors(payload))

    assert refs[0]["kind"] == "agent_session_output"
    assert refs[0]["ref"] == "artifacts/agent-session-output/run/out.txt"
    assert refs[0]["content_type"] == "text/plain"


def test_doctor_sidecar_refs_reports_missing_and_orphan(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    descriptor = write_sidecar_text(
        state_dir,
        "diagnostics/run-1/ok.txt",
        "ok",
        kind="diagnostic_trace",
        schema_version="diagnostic.v1",
        created_by="test",
    )
    orphan = state_dir / "diagnostics" / "run-1" / "orphan.txt"
    orphan.write_text("orphan", encoding="utf-8")
    missing = dict(descriptor)
    missing["ref"] = "diagnostics/run-1/missing.txt"
    event = ZfEvent(type="diagnostic.ready", payload={"refs": {"diagnostic": descriptor, "missing": missing}})

    report = doctor_sidecar_refs(state_dir, [event])

    assert report["ok"] is False
    assert {issue["code"] for issue in report["issues"]} == {"ref_missing"}
    assert any(item["ref"] == "diagnostics/run-1/orphan.txt" for item in report["orphans"])
