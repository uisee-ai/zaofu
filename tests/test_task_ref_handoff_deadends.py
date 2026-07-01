from __future__ import annotations

from pathlib import Path

from zf.autoresearch.failure_signals import collect_failure_signals
from zf.autoresearch.triggers import TriggerPolicy, scan_trigger_decisions
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.supervisor_inspection import build_supervisor_snapshot


def _init_state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _append_missing_task_ref_deadend(state_dir: Path) -> None:
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-ref-rejected",
        type="task.ref.rejected",
        actor="zf-runtime",
        task_id="CJMIN-STATE-001",
        payload={
            "trigger_event_id": "evt-dev-build",
            "reason": "worktree_dirty handoff is not allowed in worktree mode",
        },
    ))
    log.append(ZfEvent(
        id="evt-fanout-child-failed",
        type="fanout.child.failed",
        actor="zf-runtime",
        task_id="CJMIN-STATE-001",
        payload={
            "fanout_id": "cj-min-impl",
            "child_id": "dev-state-config",
            "reason": "missing task ref after dev.build.done",
        },
    ))


def test_collect_failure_signals_detects_task_ref_handoff_deadend(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    _append_missing_task_ref_deadend(state_dir)

    signals = collect_failure_signals(state_dir)
    fingerprints = {signal.fingerprint for signal in signals}

    assert any(
        fingerprint.startswith("task_ref_rejected:CJMIN-STATE-001:evt-dev-build")
        for fingerprint in fingerprints
    )
    assert (
        "missing_task_ref_after_dev_build_done:"
        "CJMIN-STATE-001:cj-min-impl:dev-state-config"
    ) in fingerprints
    assert {signal.category for signal in signals} == {"task_ref_handoff_deadend"}


def test_collect_failure_signals_ignores_recovered_task_ref_rejection(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-ref-rejected",
        type="task.ref.rejected",
        actor="zf-runtime",
        task_id="CJMIN-STATE-001",
        payload={
            "trigger_event_id": "evt-dev-build",
            "reason": "temporary ref rejection",
        },
    ))
    log.append(ZfEvent(
        id="evt-ref-updated",
        type="task.ref.updated",
        actor="zf-runtime",
        task_id="CJMIN-STATE-001",
        payload={"trigger_event_id": "evt-dev-build"},
    ))

    signals = collect_failure_signals(state_dir)

    assert not [
        signal for signal in signals
        if signal.category == "task_ref_handoff_deadend"
    ]


def test_autoresearch_triggers_missing_task_ref_deadend(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    _append_missing_task_ref_deadend(state_dir)

    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(
            cooldown_minutes=0,
            max_triggers_per_hour=10,
            max_daily_runs=10,
        ),
    )

    accepted = [
        decision for decision in decisions
        if decision.decision == "accepted"
    ]
    assert accepted
    assert any(
        decision.fingerprint.startswith("missing_task_ref_after_dev_build_done")
        for decision in accepted
    )


def test_supervisor_snapshot_projects_task_ref_deadend_attention(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    _append_missing_task_ref_deadend(state_dir)

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
    )

    items = [
        item for item in snapshot["attention_items"]
        if item["source"] == "autoresearch"
    ]
    assert items
    assert any(
        "missing_task_ref_after_dev_build_done" in item["fingerprint"]
        for item in items
    )
