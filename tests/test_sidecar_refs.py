from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
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
