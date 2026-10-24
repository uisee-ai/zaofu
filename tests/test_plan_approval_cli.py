from __future__ import annotations

import argparse
import json

import pytest

from zf.cli.plan_approval import _run
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


@pytest.mark.parametrize("ref", ["artifacts/plan/task_map.json", ".zf/artifacts/plan/task_map.json"])
def test_plan_review_reads_configured_state_artifact_without_mutation(tmp_path, monkeypatch, capsys, ref):
    project = tmp_path / "project"
    state = project / ".zf-custom"
    artifact = state / "artifacts/plan/task_map.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"tasks": [{
        "task_id": "SLICE-1", "owner_role": "dev", "allowed_paths": ["src/feature.py"],
        "verification": "python -m pytest", "wave": 1,
    }]}))
    ledger = state / "events.jsonl"
    EventWriter(EventLog(ledger)).append(ZfEvent(
        type="plan.approval.requested", actor="zf-cli",
        payload={"plan_id": "evt-plan", "task_map_ref": ref, "task_count": 1},
    ))
    before = ledger.read_bytes()
    monkeypatch.chdir(project)
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-custom")
    assert _run(argparse.Namespace(plan_cmd="review", state_dir=None)) == 0
    output = capsys.readouterr().out
    assert "[OK] checklist passed" in output
    assert "not readable" not in output
    assert ledger.read_bytes() == before


def test_plan_cli_uses_zf_state_dir_env(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf-custom"
    state_dir.mkdir()
    EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="plan.approval.requested",
        actor="zf-cli",
        payload={
            "plan_id": "evt-plan",
            "stage_id": "issue-impl",
            "task_count": 1,
            "pdd_id": "F-11111111",
        },
    ))
    monkeypatch.chdir(project)
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-custom")

    rc = _run(argparse.Namespace(plan_cmd="review", state_dir=None))
    assert rc == 0
    assert "plan evt-plan" in capsys.readouterr().out

    rc = _run(argparse.Namespace(
        plan_cmd="approve",
        state_dir=None,
        plan_id="evt-plan",
    ))
    assert rc == 0
    assert any(
        event.type == "plan.approved"
        and event.payload.get("plan_id") == "evt-plan"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )


def test_plan_reject_inherits_approval_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf-custom"
    state_dir.mkdir()
    EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="plan.approval.requested",
        actor="zf-cli",
        payload={
            "plan_id": "evt-plan",
            "stage_id": "flow-lanes-impl",
            "trace_id": "trace-1",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": "/tmp/task_map.json",
            "task_count": 4,
            "digest_ref": "artifacts/plan-digest/evt-plan.md",
        },
    ))
    monkeypatch.chdir(project)
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-custom")

    rc = _run(argparse.Namespace(
        plan_cmd="reject",
        state_dir=None,
        plan_id="evt-plan",
        reason="owner_role mismatch",
    ))

    assert rc == 0
    rejected = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "plan.rejected"
    ][0]
    assert rejected.payload["pdd_id"] == "F-11111111"
    assert rejected.payload["trace_id"] == "trace-1"
    assert rejected.payload["task_map_ref"] == "/tmp/task_map.json"
    assert rejected.payload["digest_ref"] == "artifacts/plan-digest/evt-plan.md"
    assert rejected.payload["reason"] == "owner_role mismatch"


def test_gap_review_uses_kernel_digest_for_selected_dispatch_scope(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    state = project / ".zf-custom"
    artifact = state / "artifacts/plan/task_map.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"tasks": [
        {"task_id": task_id, "owner_role": "dev", "allowed_paths": ["cli.py"],
         "verification": "python -m unittest", "root_owner_class": "assembly"}
        for task_id in ("OLD-CLI", "GAP-CLI")
    ]}))
    digest = state / "artifacts/plan/digest.md"
    digest.write_text("# Plan Digest — gap-plan\n\nSelected: GAP-CLI\nNo overlapping active paths\n")
    ledger = state / "events.jsonl"
    EventWriter(EventLog(ledger)).append(ZfEvent(
        type="plan.approval.requested", actor="zf-cli",
        payload={"plan_id": "gap-plan", "task_map_ref": "artifacts/plan/task_map.json",
                 "task_count": 1, "digest_ref": "artifacts/plan/digest.md"},
    ))
    before = ledger.read_bytes()
    monkeypatch.chdir(project)
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-custom")
    assert _run(argparse.Namespace(plan_cmd="review", state_dir=None)) == 0
    output = capsys.readouterr().out
    assert "Selected: GAP-CLI" in output
    assert "[X]" not in output
    assert "OLD-CLI" not in output
    assert ledger.read_bytes() == before
