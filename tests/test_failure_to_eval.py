from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.model import ZfEvent
from zf.runtime.failure_to_eval import (
    failure_candidate_from_event,
    materialize_failure_closeout,
    materialize_failure_candidates_from_events,
    materialize_failure_candidate,
    promote_failure_closeout_backlogs,
    write_failure_candidate,
)


def test_failure_candidate_materializes_to_backlog(tmp_path):
    event = ZfEvent(
        type="flow.goal.blocked",
        task_id="TASK-1",
        payload={
            "reason": "open P0 gap",
            "trace_ref": "reports/gap.json",
            "checkpoint_id": "ck-gap",
        },
    )
    candidate = failure_candidate_from_event(event)
    candidate_ref = write_failure_candidate(tmp_path / ".zf", candidate)

    output = materialize_failure_candidate(
        candidate_ref,
        output_dir=tmp_path / "backlogs",
        kind="backlog",
    )

    text = output.read_text(encoding="utf-8")
    assert "> 状态: proposed" in text
    assert "flow.goal.blocked" in text
    assert "reports/gap.json" in text


def test_failure_materialize_cli(tmp_path, capsys):
    candidate = {
        "schema_version": "failure-candidate.v1",
        "failure_id": "fail-test",
        "summary": "flow.goal.blocked: open gap",
        "classification": {"problem_class": "product_gap"},
        "event": {"type": "flow.goal.blocked"},
        "evidence_refs": [],
    }
    candidate_ref = tmp_path / "failure.json"
    candidate_ref.write_text(json.dumps(candidate), encoding="utf-8")

    rc = main([
        "failure",
        "materialize",
        str(candidate_ref),
        "--output-dir",
        str(tmp_path / "out"),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "failure.materialize.result.v1"
    assert result["output_ref"].endswith("fail-test.md")


def test_failure_closeout_materializes_backlog_eval_and_skill(tmp_path):
    state_dir = tmp_path / ".zf"
    event = ZfEvent(
        type="run.manager.action.failed",
        task_id="TASK-RM",
        payload={"reason": "resume command failed"},
    )
    candidate_ref = write_failure_candidate(
        state_dir,
        failure_candidate_from_event(event),
    )

    result = materialize_failure_closeout(
        state_dir,
        output_root=tmp_path / "closeout",
        kinds=("backlog", "eval", "skill"),
    )

    assert result["schema_version"] == "failure-closeout.v1"
    assert result["materialized_count"] == 1
    item = result["items"][0]
    assert item["candidate_ref"] == str(candidate_ref)
    assert Path(item["outputs"]["backlog"]).exists()
    assert Path(item["outputs"]["eval"]).exists()
    assert Path(item["outputs"]["skill"]).exists()
    manifest = json.loads(Path(result["manifest_ref"]).read_text(encoding="utf-8"))
    assert manifest["items"][0]["outputs"]["backlog"] == item["outputs"]["backlog"]


def test_failure_closeout_cli(tmp_path, capsys, monkeypatch):
    state_dir = tmp_path / ".zf"
    write_failure_candidate(
        state_dir,
        {
            "schema_version": "failure-candidate.v1",
            "failure_id": "fail-cli",
            "summary": "run.manager.action.failed: resume failed",
            "classification": {"problem_class": "runtime_recovery"},
            "event": {"type": "run.manager.action.failed"},
            "evidence_refs": [],
        },
    )
    monkeypatch.chdir(tmp_path)

    rc = main([
        "failure",
        "closeout",
        "--state-dir",
        str(state_dir),
        "--output-root",
        str(tmp_path / "out"),
        "--kinds",
        "backlog,eval",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "failure-closeout.v1"
    assert result["materialized_count"] == 1
    assert set(result["items"][0]["outputs"]) == {"backlog", "eval"}


def test_failure_closeout_promote_requires_approval_and_creates_active_task(tmp_path):
    state_dir = tmp_path / ".zf"
    write_failure_candidate(
        state_dir,
        {
            "schema_version": "failure-candidate.v1",
            "failure_id": "fail-promote",
            "summary": "run.manager.action.failed: resume failed",
            "classification": {"problem_class": "runtime_recovery"},
            "event": {"type": "run.manager.action.failed"},
            "evidence_refs": [],
        },
    )
    closeout = materialize_failure_closeout(
        state_dir,
        output_root=tmp_path / "artifacts" / "failure-closeout",
        kinds=("backlog",),
    )

    result = promote_failure_closeout_backlogs(
        Path(closeout["manifest_ref"]),
        project_root=tmp_path,
        approval_ref="owner-approved-1",
    )

    assert result["schema_version"] == "failure-closeout-promotion.v1"
    assert result["promoted_count"] == 1
    task_ref = Path(result["promoted"][0]["task_ref"])
    assert task_ref.parent == tmp_path / "tasks" / "active"
    text = task_ref.read_text(encoding="utf-8")
    assert "> 状态: active" in text
    assert "owner-approved-1" in text
    assert Path(result["report_ref"]).exists()


def test_failure_promote_cli(tmp_path, capsys, monkeypatch):
    state_dir = tmp_path / ".zf"
    write_failure_candidate(
        state_dir,
        {
            "schema_version": "failure-candidate.v1",
            "failure_id": "fail-cli-promote",
            "summary": "gate.failed: missing evidence",
            "classification": {"problem_class": "evidence_gap"},
            "event": {"type": "gate.failed"},
            "evidence_refs": [],
        },
    )
    closeout = materialize_failure_closeout(
        state_dir,
        output_root=tmp_path / "artifacts" / "failure-closeout",
        kinds=("backlog",),
    )
    monkeypatch.chdir(tmp_path)

    rc = main([
        "failure",
        "promote",
        str(closeout["manifest_ref"]),
        "--approval-ref",
        "owner-approved-cli",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["promoted_count"] == 1
    assert Path(result["promoted"][0]["task_ref"]).exists()


def test_failure_candidates_materialize_from_recent_events_idempotently(tmp_path):
    state_dir = tmp_path / ".zf"
    events = [
        ZfEvent(type="loop.started", actor="zf-cli"),
        ZfEvent(
            type="run.manager.action.failed",
            actor="run-manager",
            payload={"reason": "resume command failed", "task_id": "TASK-RM"},
        ),
    ]

    first = materialize_failure_candidates_from_events(state_dir, events)
    second = materialize_failure_candidates_from_events(state_dir, events)

    assert len(first) == 1
    assert second == []
    candidate = json.loads(first[0].read_text(encoding="utf-8"))
    assert candidate["event"]["type"] == "run.manager.action.failed"
    assert candidate["event"]["task_id"] == "TASK-RM"


def test_unknown_actionable_event_forces_failure_candidate(tmp_path):
    """131-P1-5:actionable 形状但 registry 无 spec 的事件不许静默消失。"""
    state_dir = tmp_path / ".zf"
    events = [
        ZfEvent(type="scene.custom.verify.rejected", actor="dev-scene",
                payload={"reason": "custom gate rejected", "task_id": "T-U"}),
        ZfEvent(type="scene.custom.info", actor="dev-scene", payload={}),
    ]
    written = materialize_failure_candidates_from_events(state_dir, events)
    assert len(written) == 1
    candidate = json.loads(written[0].read_text(encoding="utf-8"))
    assert candidate["event"]["type"] == "scene.custom.verify.rejected"
    assert candidate["classification"]["problem_class"] == "unknown"


def test_waive_kind_and_closeout_status(tmp_path):
    """131 §16.3-5:四选一含 waive;status 列出未 closeout 的 open 候选。"""
    from zf.runtime.failure_to_eval import failure_closeout_status

    state_dir = tmp_path / ".zf"
    for idx in (1, 2):
        event = ZfEvent(
            type="run.manager.action.failed",
            payload={"reason": f"failure {idx}", "task_id": f"T-{idx}"},
        )
        write_failure_candidate(state_dir, failure_candidate_from_event(event))

    status = failure_closeout_status(state_dir)
    assert status["open"] == 2 and status["closed"] == 0

    # 对全部候选做 waive closeout → open 归零
    manifest = materialize_failure_closeout(state_dir, kinds=("waive",))
    assert manifest["materialized_count"] == 2
    waive_ref = Path(manifest["items"][0]["outputs"]["waive"])
    waive_doc = json.loads(waive_ref.read_text(encoding="utf-8"))
    assert waive_doc["schema_version"] == "failure-waive.v1"

    status = failure_closeout_status(state_dir)
    assert status["open"] == 0 and status["closed"] == 2
