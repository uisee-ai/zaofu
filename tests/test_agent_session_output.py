from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.agent_session_output import (
    apply_agent_output_contract,
    read_agent_output_artifact,
)


def test_large_agent_output_spills_to_state_dir_artifact(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    raw = "line\n" * 3000

    payload = apply_agent_output_contract(
        state_dir,
        {"content": raw, "refs": {"output": raw}},
        metadata={"run_id": "run-1", "part_id": "tool-result"},
        threshold_bytes=64,
    )

    raw_output = payload["refs"]["raw_output"]
    assert payload["content"] != raw
    assert payload["refs"]["output"] == payload["content"]
    assert raw_output["ref_schema_version"] == "sidecar-ref.v1"
    assert raw_output["kind"] == "agent_session_output"
    assert raw_output["ref"] == raw_output["raw_ref"]
    assert raw_output["raw_ref"].startswith("artifacts/agent-session-output/run-1/")
    assert raw_output["sha256"]
    assert raw not in json.dumps(payload, ensure_ascii=False)

    page = read_agent_output_artifact(state_dir, raw_output["raw_ref"])
    assert page["content"] == raw
    assert page["sha256"] == raw_output["sha256"]
    assert page["line_count"] == 3001


def test_read_agent_output_artifact_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        read_agent_output_artifact(tmp_path / ".zf", "../secret.txt")


def test_read_agent_output_artifact_detects_hash_mismatch(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    raw = "line\n" * 3000
    payload = apply_agent_output_contract(
        state_dir,
        {"content": raw},
        metadata={"run_id": "run-1", "part_id": "tool-result"},
        threshold_bytes=64,
    )
    raw_ref = payload["refs"]["raw_output"]["raw_ref"]
    (state_dir / raw_ref).write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError):
        read_agent_output_artifact(state_dir, raw_ref)
