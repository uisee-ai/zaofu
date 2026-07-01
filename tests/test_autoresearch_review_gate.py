from __future__ import annotations

import json
from pathlib import Path

from zf.autoresearch.review_gate import (
    REVIEW_COUNCIL_SCHEMA,
    classify_review_gate_policy,
    closeout_review_gate,
    prepare_review_gate_summary,
    validate_review_council_artifact,
)
from zf.autoresearch.review_gate_context import prepare_review_gate_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _source_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "zf" / "autoresearch").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: ".zf"\n',
        encoding="utf-8",
    )
    (root / "src" / "zf" / "autoresearch" / "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    return root


def _fatal_state(state_dir: Path) -> None:
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-fatal",
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"reason": "missing review worker"},
    ))


def _valid_artifact(**overrides) -> dict:
    payload = {
        "schema_version": REVIEW_COUNCIL_SCHEMA,
        "review_id": "rvw-1",
        "run_dir": "run",
        "state_dir": "state",
        "source_commit": "abc",
        "failure_fingerprint": "fatal:orchestrator.dispatch_failed:missing review worker",
        "decision": "approve",
        "severity": "critical",
        "root_cause": "workflow pattern referenced a missing review worker",
        "minimal_patch_scope": ["src/zf/runtime/orchestrator_reactor.py"],
        "owner_files": ["src/zf/runtime/orchestrator_reactor.py"],
        "regression_commands": ["uv run pytest tests/test_workflow_invoke_pattern_bridge.py -q"],
        "risk": "low after regression",
        "critic_findings": [],
        "evidence_refs": ["evt-fatal", "tests/test_workflow_invoke_pattern_bridge.py"],
        "repair_authorization_recommendation": "manual",
        "context_pack_ref": "context/codebase_context_pack.json",
        "failure_evidence_pack_ref": "context/failure_evidence_pack.json",
        "fanout_refs": {"fanout_id": "F1", "child_reports": [], "synth_event_id": "evt-synth"},
    }
    payload.update(overrides)
    return payload


def test_prepare_review_gate_context_writes_packs_and_reuses_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    state_dir = tmp_path / ".zf"
    source_root = _source_root(tmp_path)
    _fatal_state(state_dir)

    first = prepare_review_gate_context(
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )
    second = prepare_review_gate_context(
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )

    assert Path(first.codebase_context_pack).exists()
    assert Path(first.failure_evidence_pack).exists()
    assert first.codebase_pack_reused is False
    assert second.codebase_pack_reused is True
    evidence = json.loads(Path(first.failure_evidence_pack).read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "failure_evidence_pack.v1"
    assert evidence["severity"] == "critical"
    assert evidence["failure_fingerprint"].startswith("fatal:orchestrator.dispatch_failed:")
    assert evidence["event_refs"] == ["evt-fatal"]
    assert evidence["task_refs"] == ["TASK-1"]


def test_prepare_review_gate_context_invalidates_cache_when_owner_file_changes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    state_dir = tmp_path / ".zf"
    source_root = _source_root(tmp_path)
    _fatal_state(state_dir)

    prepare_review_gate_context(run_dir=run_dir, state_dir=state_dir, source_root=source_root)
    (source_root / "src" / "zf" / "autoresearch" / "example.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    result = prepare_review_gate_context(run_dir=run_dir, state_dir=state_dir, source_root=source_root)

    assert result.codebase_pack_reused is False


def test_review_gate_policy_routes_low_direct_and_high_runtime_to_fanout() -> None:
    low = classify_review_gate_policy({
        "severity": "low",
        "failure_fingerprint": "failure:small-ui-copy",
        "initial_hypotheses": [],
    })
    high = classify_review_gate_policy({
        "severity": "high",
        "failure_fingerprint": "fanout_timed_out:F1:review-a",
        "fatal_event": {"id": "evt-1", "type": "fanout.timed_out"},
        "initial_hypotheses": [{"category": "fanout_runtime_failure"}],
    })

    assert low.route == "direct_repair"
    assert high.route == "fanout_gate"
    assert "ar-critic-verifier" in high.required_roles


def test_prepare_review_gate_summary_auto_skips_passed_and_triggers_fatal(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    state_dir = tmp_path / ".zf"
    source_root = _source_root(tmp_path)

    skipped = prepare_review_gate_summary(
        mode="auto",
        run_status="passed",
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )
    assert skipped["status"] == "skipped"
    assert not (run_dir / "review-gate" / "summary.json").exists()

    forced = prepare_review_gate_summary(
        mode="always",
        run_status="passed",
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )
    assert forced["status"] == "triggered"
    assert forced["triggered"] is True
    assert Path(forced["artifact_refs"]["summary"]).exists()

    _fatal_state(state_dir)
    summary = prepare_review_gate_summary(
        mode="auto",
        run_status="fatal",
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )

    assert summary["status"] == "triggered"
    assert summary["route"] == "fanout_gate"
    assert summary["attempt_cap"] == 2
    assert Path(summary["artifact_refs"]["summary"]).exists()


def test_review_gate_summary_classifies_layer2_handoff_incomplete(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    state_dir = tmp_path / ".zf"
    source_root = _source_root(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-assigned-critic",
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"assignee": "critic", "role": "critic"},
    ))
    log.append(ZfEvent(
        id="evt-contract-dev",
        type="task.contract.update",
        actor="orchestrator",
        task_id="TASK-1",
        payload={"contract": {"phase": "implementation", "owner_role": "dev"}},
    ))

    summary = prepare_review_gate_summary(
        mode="auto",
        run_status="failed",
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )

    assert summary["status"] == "triggered"
    assert summary["route"] == "fanout_gate"
    assert summary["run_terminal_status"] == "incomplete"
    assert summary["primary_failure_class"] == "layer2_handoff_incomplete"
    evidence = json.loads(Path(summary["artifact_refs"]["failure_evidence_pack"]).read_text(encoding="utf-8"))
    assert evidence["handoff_invariants"][0]["expected_assignee"] == "dev"
    assert evidence["handoff_invariants"][0]["assigned_to"] == "critic"
    assert "evt-contract-dev" in evidence["event_refs"]


def test_review_gate_summary_classifies_late_runtime_respawn_failure(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    state_dir = tmp_path / ".zf"
    source_root = _source_root(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-respawn",
        type="worker.respawn.failed",
        actor="orchestrator",
        task_id="TASK-1",
        payload={"error": "tmux split-window -t @22 can't find window: @22"},
    ))
    log.append(ZfEvent(
        id="evt-paused",
        type="dispatch.paused",
        actor="orchestrator",
        payload={"reason": "worker respawn failed"},
    ))
    log.append(ZfEvent(
        id="evt-safehalt",
        type="runtime.safe_halted",
        actor="orchestrator",
        payload={"reason": "infra retry exhausted"},
    ))
    log.append(ZfEvent(
        id="evt-bug",
        type="zaofu.bug.detected",
        actor="supervisor",
        payload={
            "signature": "respawn_failure_cascade",
            "suggested_fix_area": "src/zf/runtime/tmux_layout.py",
        },
    ))

    summary = prepare_review_gate_summary(
        mode="auto",
        run_status="fatal",
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )

    assert summary["status"] == "triggered"
    assert summary["route"] == "fanout_gate"
    assert summary["run_terminal_status"] == "fatal"
    assert summary["primary_failure_class"] == "pane_grid_respawn_failure"
    evidence = json.loads(Path(summary["artifact_refs"]["failure_evidence_pack"]).read_text(encoding="utf-8"))
    assert evidence["fatal_event"]["id"] == "evt-safehalt"
    assert evidence["primary_failure_class"] == "pane_grid_respawn_failure"
    assert {"evt-respawn", "evt-paused", "evt-safehalt", "evt-bug"} <= set(evidence["event_refs"])
    assert evidence["high_signal_events"][-1]["payload"]["suggested_fix_area"] == "src/zf/runtime/tmux_layout.py"


def test_review_council_artifact_rejects_missing_evidence_and_regression() -> None:
    missing_evidence = _valid_artifact(evidence_refs=[])
    missing_regression = _valid_artifact(regression_commands=[])

    assert "decision=approve requires non-empty evidence_refs" in validate_review_council_artifact(missing_evidence)
    assert "decision=approve requires non-empty regression_commands" in validate_review_council_artifact(missing_regression)


def test_closeout_block_outputs_blocker_without_repair_dispatch(tmp_path: Path) -> None:
    artifact = tmp_path / "synth.json"
    artifact.write_text(json.dumps(_valid_artifact(
        decision="block",
        repair_authorization_recommendation="blocked",
        blocker="missing provider credentials",
        manual_next_step="configure provider credentials and rerun prepare",
        root_cause="",
        minimal_patch_scope=[],
        regression_commands=[],
        critic_findings=[{"severity": "high", "status": "open", "message": "provider missing"}],
    )), encoding="utf-8")

    result = closeout_review_gate(run_dir=tmp_path / "run", synth_artifact=artifact)

    assert result.accepted is True
    assert result.decision == "block"
    assert result.blocker == "missing provider credentials"
    assert result.repair_dispatch_requested is False
    assert Path(result.closeout_artifact).exists()


def test_closeout_rejects_unresolved_high_critic_finding(tmp_path: Path) -> None:
    artifact = tmp_path / "synth.json"
    artifact.write_text(json.dumps(_valid_artifact(
        critic_findings=[{"severity": "high", "status": "open", "message": "false fix"}],
    )), encoding="utf-8")

    result = closeout_review_gate(run_dir=tmp_path / "run", synth_artifact=artifact)

    assert result.accepted is False
    assert any("unresolved high" in error for error in result.errors)
