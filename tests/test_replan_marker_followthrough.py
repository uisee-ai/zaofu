from __future__ import annotations

from pathlib import Path

from zf.autoresearch.failure_signals import collect_failure_signals
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.supervisor_inspection import build_supervisor_snapshot


def _init_state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _append_stale_task_map_with_replan_marker(
    state_dir: Path,
    *,
    with_downstream: bool = False,
) -> None:
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-stale-task-map",
        type="fanout.child.failed",
        actor="zf-runtime",
        task_id="CJMIN-PI-CORE-001",
        payload={
            "pdd_id": "CJMIN-1",
            "trace_id": "trace-r35",
            "fanout_id": "cj-min-impl",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "reason": "stale_task_map",
            "stale_task_ids": ["CJMIN-PI-CORE-001"],
        },
    ))
    log.append(ZfEvent(
        id="evt-replan-marker",
        type="orchestrator.replan_requested",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-1",
            "trace_id": "trace-r35",
            "rework_of": "evt-stale-task-map",
            "rework_source": "fanout.child.failed",
            "classification": "stale_task_map",
        },
        correlation_id="trace-r35",
    ))
    if with_downstream:
        log.append(ZfEvent(
            id="evt-plan-synth-trigger",
            type="zaofu.refactor.review.ready",
            actor="zf-cli",
            payload={
                "pdd_id": "CJMIN-1",
                "trace_id": "trace-r35",
                "rework_of": "evt-stale-task-map",
                "rework_source": "fanout.child.failed",
                "replan_classification": "stale_task_map",
            },
            correlation_id="trace-r35",
        ))


def test_stale_task_map_replan_marker_without_downstream_remains_unresolved(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    _append_stale_task_map_with_replan_marker(state_dir)

    signals = collect_failure_signals(state_dir)
    fingerprints = {signal.fingerprint for signal in signals}

    assert any(
        fingerprint.startswith("stale_task_map_writer_fanout:CJMIN-1")
        for fingerprint in fingerprints
    )
    assert (
        "replan_followthrough_missing:CJMIN-1:evt-stale-task-map"
        in fingerprints
    )


def test_replan_marker_with_synth_trigger_clears_stale_followthrough_signal(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    _append_stale_task_map_with_replan_marker(state_dir, with_downstream=True)

    signals = collect_failure_signals(state_dir)
    fingerprints = {signal.fingerprint for signal in signals}

    assert not any(
        fingerprint.startswith("stale_task_map_writer_fanout:CJMIN-1")
        for fingerprint in fingerprints
    )
    assert (
        "replan_followthrough_missing:CJMIN-1:evt-stale-task-map"
        not in fingerprints
    )


def test_supervisor_attention_reports_verify_replan_marker_without_downstream(
    tmp_path: Path,
) -> None:
    state_dir = _init_state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-verify-failed",
        type="verify.failed",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-1",
            "target_ref": "cand/CJMIN-1",
            "trace_id": "trace-r35",
        },
        correlation_id="trace-r35",
    ))
    log.append(ZfEvent(
        id="evt-verify-replan",
        type="orchestrator.replan_requested",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-1",
            "trace_id": "trace-r35",
            "rework_of": "evt-verify-failed",
            "rework_source": "verify.failed",
            "classification": "contract_freeze_gap",
        },
        correlation_id="trace-r35",
    ))

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
    )
    items = [
        item for item in snapshot["attention_items"]
        if item["source"] == "autoresearch"
    ]

    assert any(
        "replan_followthrough_missing:CJMIN-1:evt-verify-failed"
        in item["fingerprint"]
        for item in items
    )
