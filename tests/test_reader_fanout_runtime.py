from __future__ import annotations

import hashlib
import json
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ExecutionConfig,
    ExecutionProfileConfig,
    ExecutionProfileLimitsConfig,
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    FanoutChildConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageCriteriaConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.skills import SkillLockEntry
from zf.runtime.fanout import FanoutChild, FanoutContext, validate_fanout_report
from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_admission import CallResultAdmissionOutcome
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.call_result_runtime import admit_runtime_call_result
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.operator_inbox import build_operator_inbox
from zf.runtime.reader_fanout_recovery_snapshot import (
    build_fanout_currentness,
    build_reader_fanout_recovery_snapshot,
)
from zf.runtime.run_manager import (
    RUN_MANAGER_AUTORESEARCH_REQUESTED,
    run_manager_tick,
)
from zf.runtime.run_admission import RunDispatchBlocked
from zf.runtime.skill_briefing import render_skill_briefing_section
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot
from zf.core.workflow.lane_pipeline import parse_lane_pipeline


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


class _FanoutReportProbe(FanoutEvidenceQueriesMixin):
    pass


def _config(
    *,
    mode: str = "wait_for_all",
    synth: bool = False,
    review_skills: bool = False,
    synth_skills: bool = False,
    child_success_event: str = "",
    child_failure_event: str = "",
) -> ZfConfig:
    reader_skills = ["verify-review"] if review_skills else []
    roles = [
        RoleConfig(
            name="review-a",
            backend="mock",
            role_kind="reader",
            skills=reader_skills,
        ),
        RoleConfig(
            name="review-b",
            backend="mock",
            role_kind="reader",
            skills=reader_skills,
        ),
    ]
    if synth:
        roles.append(RoleConfig(
            name="review-synth",
            backend="mock",
            role_kind="reader",
            publishes=["fanout.synth.completed"],
            skills=["zf-harness-gate-evaluator"] if synth_skills else [],
        ))
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=roles,
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-candidate",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a", "review-b"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode=mode,
                    child_success_event=child_success_event,
                    child_failure_event=child_failure_event,
                    success_event="review.approved",
                    failure_event="review.rejected",
                    synth_role="review-synth" if synth else "",
                ),
            ),
        ]),
    )


def _state(
    tmp_path: Path,
    *,
    mode: str = "wait_for_all",
    synth: bool = False,
    review_skills: bool = False,
    synth_skills: bool = False,
    child_success_event: str = "",
    child_failure_event: str = "",
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _config(
            mode=mode,
            synth=synth,
            review_skills=review_skills,
            synth_skills=synth_skills,
            child_success_event=child_success_event,
            child_failure_event=child_failure_event,
        ),
        transport,
    )  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _start_fanout(
    orch: Orchestrator,
    *,
    payload: dict[str, object] | None = None,
) -> ZfEvent:
    event = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", **(payload or {})},
    )
    orch.run_once(events=[event])
    return event


def test_reader_recovery_skips_historical_result_replay_without_active_fanout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, _log, _transport, orch = _state(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        orch,
        "_recover_durable_fanout_aggregate_results",
        lambda _events: calls.append("durable"),
    )
    monkeypatch.setattr(
        orch,
        "_resume_unstarted_reader_fanouts",
        lambda _events: calls.append("unstarted") or False,
    )
    monkeypatch.setattr(
        orch,
        "_resume_unrecorded_reader_synth_results",
        lambda _events: calls.append("synth") or False,
    )
    monkeypatch.setattr(
        orch,
        "_recover_pending_reader_fanout_dispatches",
        lambda _events: pytest.fail("terminal reader history must not be replayed"),
    )

    orch._recover_unrecorded_reader_fanout_results()  # type: ignore[attr-defined]

    assert calls == ["durable", "unstarted", "synth"]


def test_reader_recovery_refreshes_events_after_durable_aggregate_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    appended: list[ZfEvent] = []

    def append_durable_result(_events: list[ZfEvent]) -> None:
        appended.append(EventWriter(log).append(ZfEvent(
            type="workflow.operation.settled",
            actor="zf-cli",
            payload={"operation_id": "op-reader-refresh"},
        )))

    from zf.runtime import reader_fanout_recovery_snapshot

    snapshot_event_ids: list[list[str]] = []
    original_builder = (
        reader_fanout_recovery_snapshot.build_reader_fanout_recovery_snapshot
    )

    def capture_snapshot(runtime, events):  # noqa: ANN001
        snapshot_event_ids.append([event.id for event in events])
        return original_builder(runtime, events)

    monkeypatch.setattr(
        orch,
        "_recover_durable_fanout_aggregate_results",
        append_durable_result,
    )
    monkeypatch.setattr(
        reader_fanout_recovery_snapshot,
        "build_reader_fanout_recovery_snapshot",
        capture_snapshot,
    )

    orch._recover_unrecorded_reader_fanout_results()  # type: ignore[attr-defined]

    assert snapshot_event_ids == [[appended[0].id]]


def test_nonterminal_reader_fanout_is_visible_to_recovery(tmp_path: Path) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    trigger = _start_fanout(orch)
    started = next(
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == trigger.id
    )

    assert started.payload["fanout_id"]
    assert orch._has_nonterminal_reader_fanouts() is True  # type: ignore[attr-defined]


def test_reader_recovery_sweep_loads_each_manifest_and_identity_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    active_id = str(started.payload["fanout_id"])
    active = _manifest(state_dir, active_id)
    for index in range(5):
        historical_id = f"fanout-terminal-{index}"
        historical = json.loads(json.dumps(active))
        historical["fanout_id"] = historical_id
        historical["status"] = "completed"
        historical["aggregate"] = {"status": "completed"}
        path = state_dir / "fanouts" / historical_id / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(historical), encoding="utf-8")
    for index in range(40):
        EventWriter(log).append(ZfEvent(
            type="workflow.child.completed",
            actor=f"unknown-reader-{index}",
            correlation_id="trace-1",
            payload={"status": "completed"},
        ))

    manifest_calls: list[str] = []
    original_manifest = orch._fanout_manifest  # type: ignore[attr-defined]

    def counted_manifest(fanout_id: str) -> dict:
        manifest_calls.append(fanout_id)
        return original_manifest(fanout_id)

    projection_calls: list[int] = []
    from zf.runtime import fanout_identity

    original_projection = fanout_identity.build_fanout_identity_projection

    def counted_projection(events):  # noqa: ANN001
        rows = list(events)
        projection_calls.append(len(rows))
        return original_projection(rows)

    monkeypatch.setattr(orch, "_fanout_manifest", counted_manifest)
    monkeypatch.setattr(
        fanout_identity,
        "build_fanout_identity_projection",
        counted_projection,
    )

    orch._recover_unrecorded_reader_fanout_results()  # type: ignore[attr-defined]

    manifest_count = len(list((state_dir / "fanouts").glob("*/manifest.json")))
    assert len(manifest_calls) == manifest_count
    assert projection_calls == [len(log.read_all())]


def test_reader_recovery_mutation_keeps_one_manifest_scan_per_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    active_id = str(started.payload["fanout_id"])
    active = _manifest(state_dir, active_id)
    for index in range(4):
        historical_id = f"fanout-terminal-mutation-{index}"
        historical = json.loads(json.dumps(active))
        historical["fanout_id"] = historical_id
        historical["status"] = "completed"
        historical["aggregate"] = {"status": "completed"}
        path = state_dir / "fanouts" / historical_id / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(historical), encoding="utf-8")
    orphan = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={"status": "completed"},
    ))
    calls: list[str] = []
    original = orch._fanout_manifest  # type: ignore[attr-defined]

    def counted(fanout_id: str) -> dict:
        calls.append(fanout_id)
        return original(fanout_id)

    monkeypatch.setattr(orch, "_fanout_manifest", counted)

    orch._recover_unrecorded_reader_fanout_results()  # type: ignore[attr-defined]

    manifest_count = len(list((state_dir / "fanouts").glob("*/manifest.json")))
    # One direct load is allowed for the single accepted result's aggregate
    # evaluation; historical manifests are still scanned exactly once.
    assert len(calls) == manifest_count + 1
    assert [
        event
        for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.causation_id == orphan.id
    ]


def test_reader_recovery_caches_goal_claim_lookup_per_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    orch.event_writer.append(ZfEvent(
        type="run.goal.completion.claimed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "run_id": "trace-1",
            "workflow_run_id": "trace-1",
            "goal_id": "F-11111111",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "claim_id": "goal-claim-reader-snapshot",
            "claim_type": "admitted_goal_closure_result",
        },
    ))
    from zf.runtime import durable_call_fanout

    monkeypatch.setattr(
        durable_call_fanout,
        "reader_fanout_superseding_goal_claim",
        lambda *_args, **_kwargs: pytest.fail(
            "snapshot recovery must not rescan goal claims per manifest"
        ),
    )

    orch._recover_unrecorded_reader_fanout_results()  # type: ignore[attr-defined]

    cancelled = [
        event
        for event in log.read_all()
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["superseded_by"] == (
        "goal-claim-reader-snapshot"
    )


def test_reader_recovery_currentness_matches_existing_queries(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    writer = EventWriter(log)
    superseded_id = "fanout-currentness-old"
    current_id = "fanout-currentness-new"
    shared = {
        "stage_id": "review-currentness",
        "topology": "fanout_reader",
        "target_ref": "candidate/currentness",
        "pdd_id": "F-CURRENTNESS",
    }
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload={**shared, "fanout_id": superseded_id},
    ))
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload={**shared, "fanout_id": current_id},
    ))

    terminal_run = "run-currentness-terminal"
    terminal_id = "fanout-currentness-terminal"
    writer.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        correlation_id=terminal_run,
        payload={"run_id": terminal_run},
    ))
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id=terminal_run,
        payload={
            "fanout_id": terminal_id,
            "stage_id": "review-terminal",
            "target_ref": "candidate/terminal",
        },
    ))
    writer.append(ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id=terminal_run,
        payload={"run_id": terminal_run},
    ))

    generation_run = "run-currentness-generation"
    generation_id = "fanout-currentness-generation"
    writer.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        correlation_id=generation_run,
        payload={"run_id": generation_run},
    ))
    writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        correlation_id=generation_run,
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "workflow_run_id": generation_run,
            "generation_id": "generation-1",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id=generation_run,
        payload={
            "fanout_id": generation_id,
            "stage_id": "verify-generation",
            "target_ref": "candidate/generation",
            "trigger_payload": {
                "schema_version": "candidate-freeze-receipt.v1",
                "workflow_run_id": generation_run,
                "generation_id": "generation-1",
            },
        },
    ))
    writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        correlation_id=generation_run,
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "workflow_run_id": generation_run,
            "generation_id": "generation-2",
        },
    ))
    events = log.read_all()
    currentness = build_fanout_currentness(events)

    for fanout_id in (
        superseded_id,
        current_id,
        terminal_id,
        generation_id,
    ):
        assert currentness[fanout_id] == (
            orch._fanout_identity_stale_reason(fanout_id)  # type: ignore[attr-defined]
        )


def test_reader_recovery_snapshot_prefilters_nonresult_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    for index in range(50):
        EventWriter(log).append(ZfEvent(
            type="codex.hook.post_tool_use",
            actor="review-a",
            payload={"sequence": index},
        ))
    orphan = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={"status": "completed"},
    ))
    events = log.read_all()
    snapshot = build_reader_fanout_recovery_snapshot(orch, events)
    inspected: list[str] = []
    original = orch._resolve_orphan_reader_fanout_child  # type: ignore[attr-defined]

    def counted(event, payload, **kwargs):  # noqa: ANN001
        inspected.append(event.id)
        return original(event, payload, **kwargs)

    monkeypatch.setattr(orch, "_resolve_orphan_reader_fanout_child", counted)

    assert orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        events,
        recovery_snapshot=snapshot,
    )
    assert inspected == [orphan.id]


def test_reader_recovery_snapshot_rebuilds_after_event_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    events = log.read_all()
    started = next(event for event in events if event.type == "fanout.started")
    fanout_id = str(started.payload["fanout_id"])

    from zf.runtime import fanout_identity

    calls = 0
    original_projection = fanout_identity.build_fanout_identity_projection

    def counted_projection(rows):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return original_projection(rows)

    monkeypatch.setattr(
        fanout_identity,
        "build_fanout_identity_projection",
        counted_projection,
    )
    first = build_reader_fanout_recovery_snapshot(orch, events)
    replacement_payload = dict(started.payload)
    replacement_payload["fanout_id"] = "fanout-current-replacement"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id=started.correlation_id,
        payload=replacement_payload,
    ))
    second = build_reader_fanout_recovery_snapshot(orch, log.read_all())

    assert calls == 2
    assert first.currentness[fanout_id] == ("", "")
    assert second.currentness[fanout_id] == (
        "superseded_by_latest_fanout",
        "fanout-current-replacement",
    )


def test_reader_recovery_currentness_uses_full_authority_log(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    all_events = log.read_all()
    started = next(event for event in all_events if event.type == "fanout.started")
    fanout_id = str(started.payload["fanout_id"])
    replacement_payload = dict(started.payload)
    replacement_payload["fanout_id"] = "fanout-authority-replacement"
    replacement = EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id=started.correlation_id,
        payload=replacement_payload,
    ))

    snapshot = build_reader_fanout_recovery_snapshot(
        orch,
        [replacement],
    )

    assert snapshot.currentness[fanout_id] == (
        "superseded_by_latest_fanout",
        "fanout-authority-replacement",
    )


def test_reader_recovery_rejects_stale_snapshot_after_concurrent_append(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    events = log.read_all()
    started = next(event for event in events if event.type == "fanout.started")
    fanout_id = str(started.payload["fanout_id"])
    child = _manifest(state_dir, fanout_id)["children"][0]
    snapshot = build_reader_fanout_recovery_snapshot(orch, events)
    late = ZfEvent(
        type="workflow.child.completed",
        actor=str(child["role_instance"]),
        correlation_id="trace-1",
        payload={
            "status": "completed",
            "fanout_id": fanout_id,
            "child_id": str(child["child_id"]),
        },
    )
    replacement_payload = dict(started.payload)
    replacement_payload["fanout_id"] = "fanout-concurrent-replacement"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id=started.correlation_id,
        payload=replacement_payload,
    ))

    assert orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        [*events, late],
        recovery_snapshot=snapshot,
    ) is False
    assert not [
        event
        for event in log.read_all()
        if event.type in {"fanout.child.completed", "fanout.child.failed"}
        and event.causation_id == late.id
    ]


def test_reader_recovery_binds_orphan_once_and_replays_bound_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    orphan = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={"status": "completed"},
    ))
    events = log.read_all()
    snapshot = build_reader_fanout_recovery_snapshot(orch, events)
    calls: dict[str, int] = {}
    original_resolver = orch._resolve_orphan_reader_fanout_child  # type: ignore[attr-defined]

    def counted_resolver(event, payload, **kwargs):  # noqa: ANN001
        calls[event.id] = calls.get(event.id, 0) + 1
        return original_resolver(event, payload, **kwargs)

    monkeypatch.setattr(
        orch,
        "_resolve_orphan_reader_fanout_child",
        counted_resolver,
    )

    recovered = orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        events,
        recovery_snapshot=snapshot,
    )

    assert recovered is True
    assert calls[orphan.id] == 1
    assert max(calls.values(), default=0) == 1
    completed = [
        event
        for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.causation_id == orphan.id
    ]
    assert len(completed) == 1
    assert completed[0].payload["fanout_id"]
    assert completed[0].payload["child_id"] == "review-a"


def test_reader_recovery_snapshot_keeps_ambiguous_role_unbound(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch, payload={"pdd_id": "F-FIRST"})
    _start_fanout(orch, payload={"pdd_id": "F-SECOND"})
    orphan = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={"status": "completed"},
    ))
    events = log.read_all()
    snapshot = build_reader_fanout_recovery_snapshot(orch, events)

    recovered = orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        events,
        recovery_snapshot=snapshot,
    )

    assert recovered is False
    assert not [
        event
        for event in log.read_all()
        if event.type in {"fanout.child.completed", "fanout.child.failed"}
        and event.causation_id == orphan.id
    ]


def test_reader_recovery_does_not_accept_terminal_manifest_result(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = str(started.payload["fanout_id"])
    manifest = _manifest(state_dir, fanout_id)
    child = manifest["children"][0]
    manifest["status"] = "cancelled"
    manifest["aggregate"] = {"status": "cancelled"}
    (state_dir / "fanouts" / fanout_id / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    late = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor=str(child["role_instance"]),
        correlation_id="trace-1",
        payload={
            "status": "completed",
            "fanout_id": fanout_id,
            "child_id": str(child["child_id"]),
        },
    ))
    events = log.read_all()
    snapshot = build_reader_fanout_recovery_snapshot(orch, events)

    assert orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        events,
        recovery_snapshot=snapshot,
    ) is False
    assert not [
        event
        for event in log.read_all()
        if event.type in {"fanout.child.completed", "fanout.child.failed"}
        and event.causation_id == late.id
    ]


def test_reader_result_recovery_starts_after_active_dispatch_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    historical = [
        EventWriter(log).append(ZfEvent(
            type="agent.text",
            actor="historical-reader",
            payload={"status": "completed", "sequence": index},
        ))
        for index in range(8)
    ]
    _start_fanout(orch)
    current = EventWriter(log).append(ZfEvent(
        type="agent.text",
        actor="current-reader",
        payload={"status": "completed"},
    ))
    inspected: list[str] = []
    monkeypatch.setattr(
        orch,
        "_resolve_orphan_reader_fanout_child",
        lambda event, _payload, **_kwargs: inspected.append(event.id) or None,
    )

    recovered = orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert current.id in inspected
    assert not {event.id for event in historical}.intersection(inspected)


def test_reader_result_recovery_falls_back_when_dispatch_boundary_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    historical = EventWriter(log).append(ZfEvent(
        type="agent.text",
        actor="historical-reader",
        payload={"status": "completed"},
    ))
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    manifest = _manifest(state_dir, started.payload["fanout_id"])
    manifest["children"][0]["last_event_id"] = "evt-missing-boundary"
    manifest_path = (
        state_dir / "fanouts" / started.payload["fanout_id"] / "manifest.json"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    inspected: list[str] = []
    monkeypatch.setattr(
        orch,
        "_resolve_orphan_reader_fanout_child",
        lambda event, _payload, **_kwargs: inspected.append(event.id) or None,
    )

    recovered = orch._resume_unrecorded_reader_fanout_results(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert historical.id in inspected


def test_lost_reader_recovery_skips_stale_fold_for_terminal_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    active_id = str(started.payload["fanout_id"])
    terminal_id = "fanout-terminal-history"
    terminal = _manifest(state_dir, active_id)
    terminal["fanout_id"] = terminal_id
    terminal["status"] = "completed"
    terminal["aggregate"] = {"status": "completed"}
    terminal_path = state_dir / "fanouts" / terminal_id / "manifest.json"
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    inspected: list[str] = []
    monkeypatch.setattr(
        orch,
        "_fanout_identity_stale_reason",
        lambda fanout_id: inspected.append(fanout_id) or ("", ""),
    )

    recovered = orch._recover_lost_reader_fanout_dispatches(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert inspected == [active_id]


def test_timeout_sweep_skips_stale_fold_for_terminal_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "fanout.started"
    )
    active_id = str(started.payload["fanout_id"])
    terminal_id = "fanout-terminal-timeout-history"
    terminal = _manifest(state_dir, active_id)
    terminal["fanout_id"] = terminal_id
    terminal["status"] = "completed"
    terminal["aggregate"] = {"status": "completed"}
    terminal_path = state_dir / "fanouts" / terminal_id / "manifest.json"
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    inspected: list[str] = []
    monkeypatch.setattr(
        orch,
        "_fanout_identity_stale_reason",
        lambda fanout_id: inspected.append(fanout_id) or ("", ""),
    )

    orch._check_fanout_timeouts()  # type: ignore[attr-defined]

    assert inspected == [active_id]


def test_lost_synth_recovery_skips_stale_fold_without_dispatched_synth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    inspected: list[str] = []
    monkeypatch.setattr(
        orch,
        "_fanout_identity_stale_reason",
        lambda fanout_id: inspected.append(fanout_id) or ("", ""),
    )

    recovered = orch._recover_lost_fanout_synth_dispatches(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert inspected == []


def test_kernel_provider_failure_projects_reader_child_failure(
    tmp_path: Path,
) -> None:
    state_dir, log, _, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(
        event for event in log.read_all() if event.type == "fanout.started"
    )
    manifest = _manifest(state_dir, started.payload["fanout_id"])
    child = manifest["children"][0]
    failure = ZfEvent(
        type="workflow.operation.failed",
        actor="zf-cli",
        origin="kernel",
        correlation_id="trace-1",
        payload={
            "operation_id": "op-reader-provider-failure",
            "request_hash": "request-reader-provider-failure",
            "operation_type": "fanout_reader_child",
            "fanout_id": manifest["fanout_id"],
            "stage_id": manifest["stage_id"],
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "role_instance": child["role_instance"],
            "status": "failed",
            "reason": "provider_stop:transport_error",
            "failure_class": "provider_stop:transport_error",
            "provider_stop_reason": "transport_error",
            "provider_failure_projection": True,
        },
    )
    log.append(failure)

    orch.run_once(events=[failure])

    child_failures = [
        event for event in log.read_all()
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == manifest["fanout_id"]
        and event.payload.get("child_id") == child["child_id"]
    ]
    assert len(child_failures) == 1
    assert child_failures[0].payload["failure_class"] == (
        "provider_stop:transport_error"
    )


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


def _durable_reader_state(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    source = tmp_path / "inputs" / "context.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"facts": ["one"]}), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="verify-1",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            flow_metadata={"result_protocol": {"mode": "blocking"}},
            stages=[
                WorkflowStageConfig(
                    id="verify-selected",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=[],
                    children=[
                        FanoutChildConfig(
                            role_instance="verify-1",
                            payload={
                                "task_id": "T-VERIFY",
                                "artifact_refs": [{
                                    "source_id": "context",
                                    "artifact_id": "context",
                                    "kind": "context",
                                    "ref": "inputs/context.json",
                                    "sha256": digest,
                                    "allowed_paths": ["$.facts"],
                                }],
                                "required_reads": [{
                                    "source_id": "context",
                                    "artifact_id": "context",
                                    "artifact_sha256": digest,
                                    "json_path": "$.facts",
                                }],
                            },
                        ),
                    ],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="verify.child.completed",
                        child_failure_event="verify.child.failed",
                        success_event="review.approved",
                        failure_event="review.rejected",
                    ),
                ),
            ],
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="run-durable",
        payload={
            "pdd_id": "F-DURABLE",
            "workflow_run_id": "run-durable",
        },
    )
    orch.run_once(events=[trigger])
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    manifest = _manifest(state_dir, started.payload["fanout_id"])
    child = manifest["children"][0]
    child["fanout_id"] = started.payload["fanout_id"]
    return state_dir, log, transport, orch, config, child


def _durable_verification_payload(child: dict, *, verdict: str) -> dict:
    child_payload = dict(child.get("payload") or {})
    failed = verdict == "rejected"
    identity = {
        "workflow_run_id": "run-durable",
        "task_id": "T-VERIFY",
        "contract_revision": "contract-1",
        "task_map_generation": "generation-1",
        "base_commit": "base-1",
        "task_ref": "artifacts/task-ref.json",
        "contract_snapshot_ref": "artifacts/contract.json",
        "contract_snapshot_digest": "a" * 64,
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "b" * 64,
        "target_commit": "target-1",
    }
    verification_result = {
        "schema_version": "verification-result.v1",
        "execution_status": "completed",
        "verdict": verdict,
        "failure_class": "product_rejection" if failed else "none",
        **identity,
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
        "requirement_results": [{
            "acceptance_id": "AC-1",
            "status": "failed" if failed else "passed",
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "evidence_refs": ["test:durable"],
            "findings": [{"message": "gap"}] if failed else [],
            "reproduction_commands": ["pytest"],
        }],
    }
    return {
        **child_payload,
        **identity,
        "fanout_id": child["fanout_id"],
        "child_id": child["child_id"],
        "run_id": child["run_id"],
        "role_instance": child["role_instance"],
        "stage_id": "verify-selected",
        "status": "completed",
        "verification_result": verification_result,
        "report": {
            "child_id": child["child_id"],
            "status": "failed" if failed else "passed",
            "summary": "durable verification result",
            "findings": [{"message": "gap"}] if failed else [],
            "recommendation": "reject" if failed else "approve",
            "evidence_refs": ["test:durable"],
            "requirement_coverage_matrix": [{
                "acceptance_id": "AC-1",
                "status": "failed" if failed else "passed",
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
                "evidence_refs": ["test:durable"],
                "findings": [{"message": "gap"}] if failed else [],
            }],
        },
    }


def test_selected_reader_repair_is_restart_idempotent_and_semantic_rejects(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch, config, child = _durable_reader_state(tmp_path)
    initial_payload = _durable_verification_payload(child, verdict="rejected")
    initial_payload["verification_result"].pop("target_commit")
    malformed = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=initial_payload,
    )
    log.append(malformed)
    orch.run_once(events=[malformed])

    repairs = [
        event for event in log.read_all()
        if event.type == "workflow.call.result.repair.requested"
    ]
    assert len(repairs) == 1
    assert repairs[0].payload["semantic_attempt_incremented"] is False
    assert len(transport.sent) == 2  # initial call plus one correction turn
    assert not any(
        event.type in {"fanout.child.completed", "fanout.child.failed"}
        for event in log.read_all()
    )

    restarted_transport = _RecordingTransport()
    restarted = Orchestrator(
        state_dir,
        config,
        restarted_transport,
    )  # type: ignore[arg-type]
    restarted.run_once(events=[])
    assert restarted_transport.sent == []
    assert sum(
        event.type == "workflow.call.result.repair.requested"
        for event in log.read_all()
    ) == 1

    child_payload = dict(child.get("payload") or {})
    source_manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        state_dir,
        manifest=source_manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    corrected = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(child, verdict="rejected"),
    )
    log.append(corrected)
    restarted.run_once(events=[corrected])

    events = log.read_all()
    assert any(event.type == "workflow.operation.settled" for event in events)
    child_failure = next(
        event for event in events if event.type == "fanout.child.failed"
    )
    assert child_failure.payload["semantic_verdict"] == "rejected"
    assert child_failure.payload["admitted_call_result_ref"]["ref"]
    attempt_starts = [
        event
        for event in events
        if event.type == "task.attempt.started"
        and event.task_id == "T-VERIFY"
    ]
    assert len(attempt_starts) == 1
    assert attempt_starts[0].payload["attempt_id"]
    assert attempt_starts[0].payload["lease_id"]
    assert not any(
        event.type in {
            "task.attempt.retry_scheduled",
            "task.attempt.deadlettered",
        }
        for event in events
    )


def test_selected_reader_restart_projects_settled_result_without_provider_call(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch, config, child = _durable_reader_state(tmp_path)
    child_payload = dict(child.get("payload") or {})
    source_manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        state_dir,
        manifest=source_manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    terminal = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(child, verdict="passed"),
    )
    log.append(terminal)
    admitted = admit_runtime_call_result(
        orch,
        terminal,
        mode="blocking",
        dispatch_correction=False,
    )
    assert admitted.admitted is True
    assert not any(
        event.type == "fanout.child.completed" for event in log.read_all()
    )

    restarted_transport = _RecordingTransport()
    restarted = Orchestrator(
        state_dir,
        config,
        restarted_transport,
    )  # type: ignore[arg-type]
    restarted.run_once(events=[])

    events = log.read_all()
    projected = next(
        event for event in events if event.type == "fanout.child.completed"
    )
    assert projected.payload["admitted_call_result_ref"]["ref"] == (
        admitted.envelope_ref["ref"]
    )
    assert any(event.type == "review.approved" for event in events)
    assert restarted_transport.sent == []


def test_reader_turn_close_after_admitted_result_is_not_dispatch_lost(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch, _config, child = _durable_reader_state(
        tmp_path
    )
    child_payload = dict(child.get("payload") or {})
    source_manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        state_dir,
        manifest=source_manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    result = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(child, verdict="passed"),
    )
    log.append(result)
    admitted = admit_runtime_call_result(
        orch,
        result,
        mode="blocking",
        dispatch_correction=False,
    )
    assert admitted.admitted is True
    EventWriter(log).append(ZfEvent(
        type="provider.turn.closed",
        actor="verify-1",
        payload={
            "backend": "codex",
            "instance_id": "verify-1",
            "turn_id": "turn-after-admission",
        },
    ))

    recovered = orch._recover_lost_reader_fanout_dispatches(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert not any(
        event.type == "fanout.child.dispatch_lost"
        for event in log.read_all()
    )


def test_synth_turn_close_after_settled_result_is_not_dispatch_lost(
    tmp_path: Path,
) -> None:
    _state_dir, log, orch, _fanout_id = _task_map_synth_orchestrator(tmp_path)
    dispatch = next(
        event
        for event in log.read_all()
        if event.type == "fanout.synth.dispatched"
    )
    operation_id = str(dispatch.payload["operation_id"])
    request_hash = str(dispatch.payload["request_hash"])
    log.append(ZfEvent(
        type="workflow.call.result.admitted",
        actor="zf-cli",
        origin="kernel",
        payload={
            "operation_id": operation_id,
            "request_hash": request_hash,
            "workflow_run_id": "trace-product",
            "admission_status": "admitted",
            "envelope_ref": {"ref": "artifacts/result.json"},
            "control_result_ref": {"ref": "artifacts/control.json"},
        },
    ))
    log.append(ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        origin="kernel",
        payload={
            "operation_id": operation_id,
            "request_hash": request_hash,
            "workflow_run_id": "trace-product",
            "admitted_call_result_ref": {"ref": "artifacts/result.json"},
        },
    ))
    log.append(ZfEvent(
        type="provider.turn.closed",
        actor="task-map-synth",
        payload={
            "backend": "codex",
            "instance_id": "task-map-synth",
            "turn_id": "turn-synth-after-admission",
        },
    ))

    recovered = orch._recover_lost_fanout_synth_dispatches(  # type: ignore[attr-defined]
        log.read_all()
    )

    assert recovered is False
    assert not any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("child_id") == "synth"
        for event in log.read_all()
    )


def test_paused_synth_dispatch_defers_without_business_failure_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(
        tmp_path,
        complete_child=False,
    )
    original_send = orch._send_transport_task  # type: ignore[attr-defined]

    def blocked_send(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RunDispatchBlocked(
            role_name="review-synth",
            reason="run_paused",
            run_id="trace-product",
        )

    monkeypatch.setattr(orch, "_send_transport_task", blocked_send)
    orch.run_once(events=[ZfEvent(
        type="task_map.child.completed",
        actor="source-reader",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "child_id": "source-reader",
            "run_id": f"run-{fanout_id}-source-reader",
            "status": "completed",
            "summary": "source read",
            "artifact_refs": ["docs/product-prd.md"],
        },
    )])

    paused_events = log.read_all()
    requested = next(
        event
        for event in paused_events
        if event.type == "workflow.operation.requested"
        and event.payload.get("operation_type") == "fanout_synth"
    )
    assert not any(
        event.type == "fanout.synth.completed" for event in paused_events
    )
    assert not any(
        event.type == "fanout.aggregate.completed"
        and event.payload.get("status") == "failed"
        for event in paused_events
    )

    monkeypatch.setattr(orch, "_send_transport_task", original_send)
    orch._evaluate_reader_fanout(fanout_id)  # type: ignore[attr-defined]

    synth_dispatch = next(
        event
        for event in log.read_all()
        if event.type == "fanout.synth.dispatched"
    )
    assert synth_dispatch.payload["operation_id"] == requested.payload[
        "operation_id"
    ]


def test_superseded_blocking_reader_result_closes_child_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch, _config, child = _durable_reader_state(
        tmp_path
    )
    terminal = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(child, verdict="passed"),
    )
    terminal.payload.update({
        "flow_kind": "workflow",
        "completion_profile": "artifact_delivery",
    })
    outcome = CallResultAdmissionOutcome(
        status="superseded",
        mode="blocking",
        operation_id="operation-stale",
        request_hash="request-stale",
        issues=({
            "field": "control_result.workflow_generation",
            "code": "stale_workflow_generation",
            "message": "a newer generation is current",
        },),
    )
    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.admit_runtime_call_result",
        lambda *_args, **_kwargs: outcome,
    )

    orch.run_once(events=[terminal])
    orch.run_once(events=[terminal])

    events = log.read_all()
    failed = [
        event
        for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("reason") == "stale_call_result_superseded"
    ]
    assert len(failed) == 1
    assert failed[0].payload["failure_class"] == "call_result_currentness"
    assert failed[0].payload["call_result_issues"][0]["code"] == (
        "stale_workflow_generation"
    )
    aggregate = next(
        event
        for event in events
        if event.type == "fanout.aggregate.completed"
    )
    assert aggregate.payload["status"] == "failed"
    assert any(event.type == "review.rejected" for event in events)
    manifest = _manifest(state_dir, child["fanout_id"])
    assert manifest["children"][0]["status"] == "failed"


def test_blocking_reader_evidence_gate_uses_admitted_control_result(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch, _config, child = _durable_reader_state(tmp_path)
    orch.config.verification.report_evidence_gate = "fail_closed"
    child_payload = dict(child.get("payload") or {})
    source_manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        state_dir,
        manifest=source_manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    payload = _durable_verification_payload(child, verdict="passed")
    payload["report"].pop("evidence_refs")
    terminal = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=payload,
    )

    orch.run_once(events=[terminal])

    types = [event.type for event in log.read_all()]
    assert "workflow.call.result.admitted" in types
    assert "fanout.child.completed" in types
    assert "stage.report.evidence_missing" not in types
    assert "fanout.child.failed" not in types


def test_trigger_creates_one_fanout_and_dispatches_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    state_dir, log, transport, orch = _state(tmp_path)
    trigger = _start_fanout(orch)
    orch.run_once(events=[trigger])

    events = log.read_all()
    started = [event for event in events if event.type == "fanout.started"]
    dispatched = [event for event in events if event.type == "fanout.child.dispatched"]
    assert len(started) == 1
    assert len(dispatched) == 2
    assert {event.payload["target_ref"] for event in dispatched} == {
        "candidate/F-11111111",
    }
    assert len({event.payload["run_id"] for event in dispatched}) == 2
    assert [sent[0] for sent in transport.sent] == ["review-a", "review-b"]
    assert all(sent[3].trace_id == "trace-1" for sent in transport.sent)
    assert started[0].payload["pdd_id"] == "F-11111111"
    assert started[0].payload["feature_id"] == "F-11111111"
    assert started[0].payload["aggregate"]["child_success_event"] == "workflow.child.completed"
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "uv --project /repo run zf emit workflow.child.completed" in briefing
    assert "zf emit workflow.child.completed" in briefing
    assert "Do not emit the aggregate success/failure event directly" in briefing
    assert f"--state-dir {state_dir}" in briefing
    assert '"fanout_id":' in briefing


def test_reader_children_inherit_workflow_anchor_task_id(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        task_id="FLOW-ANCHOR",
        correlation_id="trace-anchor",
        payload={
            "pdd_id": "F-ANCHOR",
            "task_id": "FLOW-ANCHOR",
        },
    )

    orch.run_once(events=[trigger])

    dispatched = [
        event
        for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    ]
    assert dispatched
    assert all(
        event.payload["payload"]["task_id"] == "FLOW-ANCHOR"
        for event in dispatched
    )


def test_reader_fanout_briefing_includes_stage_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    _state_dir, _log, transport, orch = _state(tmp_path)
    orch.config.workflow.stages[0].criteria = WorkflowStageCriteriaConfig(
        instructions=[
            "Initial scan is not implementation verification.",
            "Missing code belongs in planning input, not scan failure.",
        ],
    )

    _start_fanout(orch)

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Stage Intent" in briefing
    assert "Initial scan is not implementation verification." in briefing
    assert "Missing code belongs in planning input, not scan failure." in briefing
    assert "`report.recommendation` is an enum" in briefing
    assert "never append rationale to the enum value" in briefing
    assert "rationale in `report.summary` or `report.findings`" in briefing


def test_flow_discovery_briefing_teaches_canonical_gap_task_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    _state_dir, _log, transport, orch = _state(tmp_path)
    aggregate = orch.config.workflow.stages[0].aggregate
    aggregate.success_event = "flow.discovery.completed"
    aggregate.failure_event = "flow.discovery.failed"

    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "candidate_policy": {"rolling_smoke": "required"},
        },
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "completed semantic discovery" in briefing
    assert "non-empty `task_id`" in briefing
    assert "`claim_paths` or `allowed_paths`" in briefing
    assert "`acceptance_refs` and `verification_commands` do not replace" in briefing
    assert "MUST NOT claim overlapping paths" in briefing
    assert "requires rolling smoke" in briefing
    assert "`validation.commands`" in briefing
    assert "do not mark a source-delta-only assertion" in briefing


def test_flow_discovery_aggregate_strips_invalid_gap_tasks(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    stage = orch.config.workflow.stages[0]
    stage.roles = ["review-a"]
    stage.aggregate.child_success_event = "flow.discovery.child.completed"
    stage.aggregate.child_failure_event = "flow.discovery.child.failed"
    stage.aggregate.success_event = "flow.discovery.completed"
    stage.aggregate.failure_event = "flow.discovery.failed"
    _start_fanout(orch)
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )

    orch.run_once(events=[ZfEvent(
        type="flow.discovery.child.failed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": dispatched.payload["fanout_id"],
            "stage_id": dispatched.payload["stage_id"],
            "child_id": dispatched.payload["child_id"],
            "run_id": dispatched.payload["run_id"],
            "role_instance": "review-a",
            "status": "failed",
            "report": {
                "status": "failed",
                "summary": "blocking gap with malformed task shape",
                "recommendation": "reject",
                "findings": [{
                    "severity": "high",
                    "path": "app/src/render.ts",
                    "message": "render placement is missing",
                }],
                "gap_tasks": [{
                    "id": "gap-render",
                    "acceptance_refs": ["accept-render"],
                    "verification_commands": ["npm test"],
                    "source_refs": ["app/src/render.ts:10"],
                }],
            },
        },
    )])

    failed = next(
        event for event in log.read_all()
        if event.type == "flow.discovery.failed"
    )
    assert "gap_tasks" not in failed.payload
    assert failed.payload["gap_task_contract_errors"] == [
        "gap-render.claim_paths is required",
        "gap-render.acceptance is required",
        "gap-render.verify_commands is required",
    ]


def test_flow_discovery_aggregate_preserves_parent_goal_identity(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    stage = orch.config.workflow.stages[0]
    stage.roles = ["review-a"]
    stage.aggregate.child_success_event = "flow.discovery.child.completed"
    stage.aggregate.child_failure_event = "flow.discovery.child.failed"
    stage.aggregate.success_event = "flow.discovery.completed"
    stage.aggregate.failure_event = "flow.discovery.failed"
    _start_fanout(orch)
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )

    orch.run_once(events=[ZfEvent(
        type="flow.discovery.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": dispatched.payload["fanout_id"],
            "stage_id": dispatched.payload["stage_id"],
            "child_id": dispatched.payload["child_id"],
            "run_id": dispatched.payload["run_id"],
            "role_instance": "review-a",
            "status": "completed",
            "report": {
                "status": "passed",
                "summary": "no remaining gaps",
                "recommendation": "approve",
                "findings": [],
                "goal_id": "trace-1",
                "pdd_id": "child-must-not-replace-parent",
                "feature_id": "child-must-not-replace-parent",
                "open_p0_p1_gap_count": 0,
                "evidence_refs": ["git:abc"],
                "test_refs": ["pytest"],
            },
        },
    )])

    completed = next(
        event for event in log.read_all()
        if event.type == "flow.discovery.completed"
    )
    assert completed.payload["workflow_run_id"] == "trace-1"
    assert completed.payload["pdd_id"] == "F-11111111"
    assert completed.payload["feature_id"] == "F-11111111"
    assert completed.payload["goal_id"] == "F-11111111"
    assert {
        item["key"] for item in completed.payload["identity_conflicts"]
    } == {"pdd_id", "feature_id", "goal_id"}

    orch.run_once(events=[ZfEvent(
        type="fanout.aggregate.rebuild.requested",
        actor="run-manager",
        correlation_id="trace-1",
        payload={
            "fanout_id": dispatched.payload["fanout_id"],
            "source_event_id": completed.id,
            "reason": "regression drill",
        },
    )])

    rebuilt = [
        event for event in log.read_all()
        if event.type == "flow.discovery.completed"
    ]
    assert len(rebuilt) == 2
    assert rebuilt[-1].payload["rebuild_of"] == completed.id
    assert rebuilt[-1].payload["recovery_generation"] == 1
    assert rebuilt[-1].payload["goal_id"] == "F-11111111"


def test_goal_identity_recovery_drill_rebuilds_legacy_aggregate_without_agent(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    stage = orch.config.workflow.stages[0]
    stage.roles = ["review-a"]
    stage.aggregate.child_success_event = "flow.discovery.child.completed"
    stage.aggregate.child_failure_event = "flow.discovery.child.failed"
    stage.aggregate.success_event = "flow.discovery.completed"
    stage.aggregate.failure_event = "flow.discovery.failed"
    _start_fanout(orch)
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )
    orch.run_once(events=[ZfEvent(
        type="flow.discovery.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": dispatched.payload["fanout_id"],
            "stage_id": dispatched.payload["stage_id"],
            "child_id": dispatched.payload["child_id"],
            "run_id": dispatched.payload["run_id"],
            "role_instance": "review-a",
            "status": "completed",
            "report": {
                "status": "passed",
                "summary": "complete",
                "recommendation": "approve",
                "findings": [],
                "goal_id": "child-alias",
                "open_p0_p1_gap_count": 0,
            },
        },
    )])
    legacy = ZfEvent(
        id="evt-legacy-malformed-aggregate",
        type="flow.discovery.completed",
        actor="legacy-reducer",
        correlation_id="trace-1",
        payload={
            "fanout_id": dispatched.payload["fanout_id"],
            "stage_id": dispatched.payload["stage_id"],
            "workflow_run_id": "trace-1",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "goal_id": "child-alias",
        },
    )
    log.append(legacy)
    log.append(ZfEvent(
        id="evt-legacy-identity-invalid",
        type="goal.closure.identity.invalid",
        actor="zf-cli",
        payload={
            "fingerprint": "goal-identity:trace-1",
            "source_event_id": legacy.id,
            "source_event_type": legacy.type,
            "fanout_id": dispatched.payload["fanout_id"],
            "reason": "legacy child alias replaced parent goal",
        },
    ))

    tick = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=orch.config,
        project_root=tmp_path,
        spawn_repairs=False,
        action_filter={"fanout-aggregate-rebuild"},
    )
    rebuild_request = next(
        event for event in log.read_all()
        if event.type == "fanout.aggregate.rebuild.requested"
    )
    orch.run_once(events=[rebuild_request])

    rebuilt = [
        event for event in log.read_all()
        if event.type == "flow.discovery.completed"
        and event.payload.get("rebuild_of") == legacy.id
    ]
    assert tick.actions_applied == 1
    assert len(rebuilt) == 1
    assert rebuilt[0].payload["goal_id"] == "F-11111111"
    assert rebuilt[0].payload["workflow_run_id"] == "trace-1"
    assert not any(
        event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
        for event in log.read_all()
    )


def test_goal_closure_schema_failure_can_rebuild_from_discriminator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    fanout_id = "fanout-goal-closure"
    manifest_path = state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "fanout_id": fanout_id,
        "aggregate_config": {
            "success_event": "goal.closure.synthesized",
        },
        "children": [{"child_id": "judge", "status": "completed"}],
    }), encoding="utf-8")
    blocked = ZfEvent(
        type="discriminator.failed",
        actor="zf-cli",
        correlation_id="run-goal",
        payload={
            "fanout_id": fanout_id,
            "blocked_event_type": "goal.closure.synthesized",
            "blocked_event_payload": {"fanout_id": fanout_id},
            "failed_d": ["EventSchemaD"],
        },
    )
    log.append(blocked)
    rebuild = ZfEvent(
        type="fanout.aggregate.rebuild.requested",
        actor="run-manager",
        correlation_id="run-goal",
        payload={
            "fanout_id": fanout_id,
            "source_event_id": blocked.id,
        },
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.event_log = log
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orch,
        "_fanout_identity_stale_reason",
        lambda _fanout_id: ("", ""),
    )
    monkeypatch.setattr(
        orch,
        "_evaluate_reader_fanout",
        lambda target, rebuild_request=None: calls.append((
            target,
            rebuild_request.id if rebuild_request is not None else "",
        )),
    )

    orch._rebuild_reader_fanout_aggregate(rebuild)

    assert calls == [(fanout_id, rebuild.id)]


def test_reader_fanout_stage_success_criteria_can_fail_aggregate(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    candidate_root = state_dir / "candidates" / "F-11111111" / "worktree"
    candidate_root.mkdir(parents=True)
    (candidate_root / "matrix.json").write_text(
        json.dumps({"rows": [
            {"id": "CAP-1", "priority": "P0", "status": "partial"},
        ]}),
        encoding="utf-8",
    )
    orch.config.workflow.stages[0].criteria = WorkflowStageCriteriaConfig(
        success_criteria=[{
            "kind": "artifact_matrix_gate",
            "matrix_paths": ["matrix.json"],
            "blocking_priority": "P0",
            "allowed_statuses": ["done"],
        }],
    )
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    manifest = _manifest(state_dir, started.payload["fanout_id"])

    for child in manifest["children"]:
        orch.run_once(events=[ZfEvent(
            type="workflow.child.completed",
            actor=child["role_instance"],
            correlation_id="trace-1",
            payload={
                "fanout_id": started.payload["fanout_id"],
                "trace_id": "trace-1",
                "stage_id": "review-candidate",
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "role_instance": child["role_instance"],
                "status": "completed",
            },
        )])

    events = log.read_all()
    rejected = [event for event in events if event.type == "review.rejected"]
    approved = [event for event in events if event.type == "review.approved"]
    aggregate = [
        event for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ][-1]

    assert rejected
    assert not approved
    assert aggregate.payload["status"] == "failed"
    assert aggregate.payload["stage_success_criteria"]["passed"] is False
    assert "artifact matrix gate failed" in rejected[-1].payload["findings"][0]["message"]


def test_failed_aggregate_publishes_typed_recovery_routing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    manifest = _manifest(state_dir, started.payload["fanout_id"])
    recovery = {
        "failure_class": "dependency_blocked",
        "recovery_action": "replan",
        "rework_scope": "plan_ports",
        "recovery_owner": "planner",
        "failed_task_ids": ["TASK-1"],
        "rework_item_ids": ["RW-1"],
        "semantic_result_refs": ["artifacts/call-results/control/result.json"],
    }
    monkeypatch.setattr(
        "zf.runtime.orchestrator_fanout.fanout_failure_context",
        lambda _owner, _manifest, *, failed: ([], recovery) if failed else ([], {}),
    )

    for index, child in enumerate(manifest["children"]):
        orch.run_once(events=[ZfEvent(
            type="workflow.child.completed",
            actor=child["role_instance"],
            correlation_id="trace-1",
            payload={
                "fanout_id": started.payload["fanout_id"],
                "trace_id": "trace-1",
                "stage_id": "review-candidate",
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "role_instance": child["role_instance"],
                "status": "failed" if index == 0 else "completed",
                "reason": "upstream plan port is incomplete" if index == 0 else "",
            },
        )])

    aggregate = [
        event
        for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ][-1]
    published = [
        event
        for event in log.read_all()
        if event.type == "review.rejected"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ][-1]
    for event in (aggregate, published):
        assert event.payload["failure_class"] == "dependency_blocked"
        assert event.payload["recovery_action"] == "replan"
        assert event.payload["rework_scope"] == "plan_ports"
        assert event.payload["recovery_owner"] == "planner"
        assert event.payload["semantic_result_refs"] == recovery[
            "semantic_result_refs"
        ]


def test_reader_fanout_stage_gate_config_can_live_in_project_root(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    candidate_root = state_dir / "candidates" / "F-11111111" / "worktree"
    candidate_root.mkdir(parents=True)
    (candidate_root / "package.json").write_text('{"name":"candidate"}\n', encoding="utf-8")
    gate_path = tmp_path / "docs" / "plans" / "gate.json"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text(
        json.dumps({"required_artifacts": ["package.json"]}),
        encoding="utf-8",
    )
    orch.config.workflow.stages[0].criteria = WorkflowStageCriteriaConfig(
        success_criteria=[{
            "kind": "artifact_matrix_gate",
            "config_ref": "docs/plans/gate.json",
        }],
    )
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    manifest = _manifest(state_dir, started.payload["fanout_id"])

    for child in manifest["children"]:
        orch.run_once(events=[ZfEvent(
            type="workflow.child.completed",
            actor=child["role_instance"],
            correlation_id="trace-1",
            payload={
                "fanout_id": started.payload["fanout_id"],
                "trace_id": "trace-1",
                "stage_id": "review-candidate",
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "role_instance": child["role_instance"],
                "status": "completed",
            },
        )])

    approved = [event for event in log.read_all() if event.type == "review.approved"]
    rejected = [event for event in log.read_all() if event.type == "review.rejected"]
    aggregate = [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ][-1]

    assert approved
    assert not rejected
    assert aggregate.payload["status"] == "completed"


def test_candidate_ready_default_head_target_uses_candidate_ref(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="verify-code", backend="mock", role_kind="reader"),
            RoleConfig(name="verify-regression", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="issue-verify",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["verify-code", "verify-regression"],
                target_ref="HEAD",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="verify.child.completed",
                    child_failure_event="verify.child.failed",
                    success_event="test.passed",
                    failure_event="test.failed",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    candidate_ref = "zf/prod-issue/candidate/issue-calc-add-regression"
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-ADD",
            "candidate_ref": candidate_ref,
            "candidate_head_commit": "abc123",
        },
    )

    orch.run_once(events=[trigger])

    events = log.read_all()
    started = next(event for event in events if event.type == "fanout.started")
    dispatched = [
        event for event in events if event.type == "fanout.child.dispatched"
    ]
    assert started.payload["target_ref"] == candidate_ref
    assert {event.payload["target_ref"] for event in dispatched} == {candidate_ref}
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert f"- target_ref: `{candidate_ref}`" in briefing
    assert "- target_ref: `HEAD`" not in briefing


def test_idle_tick_replays_unstarted_reader_fanout_trigger(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    log.append(trigger)

    orch.run_once(events=[])

    started = [
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == trigger.id
    ]
    assert len(started) == 1
    assert started[0].payload["stage_id"] == "review-candidate"
    assert [sent[0] for sent in transport.sent] == ["review-a", "review-b"]

    orch.run_once(events=[])

    assert len([
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == trigger.id
    ]) == 1


def test_idle_tick_replays_only_latest_equivalent_reader_trigger(tmp_path: Path):
    _state_dir, log, transport, orch = _state(tmp_path)
    first = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_head": "abc123",
            "pdd_id": "F-11111111",
        },
    )
    latest = ZfEvent(
        type="candidate.ready",
        actor="zf-stall-redispatch",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_head": "abc123",
            "pdd_id": "F-11111111",
        },
    )
    log.append(first)
    log.append(latest)

    orch.run_once(events=[])

    started = [event for event in log.read_all() if event.type == "fanout.started"]
    assert len(started) == 1
    assert started[0].payload["trigger_event_id"] == latest.id
    assert [sent[0] for sent in transport.sent] == ["review-a", "review-b"]


def test_idle_tick_skips_equivalent_reader_trigger_after_terminal(tmp_path: Path):
    _state_dir, log, transport, orch = _state(tmp_path)
    first = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_head": "abc123",
            "pdd_id": "F-11111111",
        },
    )
    log.append(first)
    orch.run_once(events=[])
    assert len([event for event in log.read_all() if event.type == "fanout.started"]) == 1

    duplicate = ZfEvent(
        type="candidate.ready",
        actor="zf-stall-redispatch",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_head": "abc123",
            "pdd_id": "F-11111111",
        },
    )
    log.append(duplicate)
    transport.sent.clear()

    orch.run_once(events=[])

    started = [event for event in log.read_all() if event.type == "fanout.started"]
    assert len(started) == 1
    assert started[0].payload["trigger_event_id"] == first.id
    assert transport.sent == []


def test_idle_tick_replays_reader_trigger_when_candidate_head_commit_changes(
    tmp_path: Path,
):
    _state_dir, log, transport, orch = _state(tmp_path)
    first = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_ref": "candidate/F-11111111",
            "candidate_head_commit": "old-head",
            "pdd_id": "F-11111111",
        },
    )
    log.append(first)
    orch.run_once(events=[])
    assert len([event for event in log.read_all() if event.type == "fanout.started"]) == 1

    repaired = ZfEvent(
        type="candidate.ready",
        actor="zf-operator",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-impl-1",
            "candidate_ref": "candidate/F-11111111",
            "candidate_head_commit": "new-head",
            "pdd_id": "F-11111111",
        },
    )
    log.append(repaired)
    transport.sent.clear()

    orch.run_once(events=[])

    started = [event for event in log.read_all() if event.type == "fanout.started"]
    assert len(started) == 2
    assert started[-1].payload["trigger_event_id"] == repaired.id
    current_fanout_id = started[-1].payload["fanout_id"]
    assert len(transport.sent) == 2
    assert all(
        current_fanout_id in briefing_path.name
        for _role, briefing_path, _prompt, _context in transport.sent
    )
    assert any(
        event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == started[0].payload["fanout_id"]
        and event.payload.get("superseded_by") == current_fanout_id
        for event in log.read_all()
    )
    deferred = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("fanout_id") == started[-1].payload["fanout_id"]
    ]
    assert {event.payload["role_instance"] for event in deferred} == {
        "review-a", "review-b",
    }
    assert {
        event.payload["reason"] for event in deferred
    } == {"worker_state_not_dispatchable:busy"}


def test_reader_fanout_child_payload_reaches_dispatch_and_briefing(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-b", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-candidate",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=[],
                target_ref="candidate/${pdd_id}",
                children=[
                    FanoutChildConfig(
                        role_instance="review-a",
                        payload={
                            "child_id": "review-a-docs",
                            "instruction": "Run only docs smoke checks.",
                            "expected_output": "approve docs-only candidate",
                        },
                    ),
                    FanoutChildConfig(
                        role_instance="review-b",
                        payload={
                            "instruction": "Run only API smoke checks.",
                        },
                    ),
                ],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    _start_fanout(orch)

    events = log.read_all()
    started = next(event for event in events if event.type == "fanout.started")
    assert started.payload["expected_children"][0]["child_id"] == "review-a-docs"
    assert started.payload["expected_children"][0]["payload"]["expected_output"] == (
        "approve docs-only candidate"
    )
    dispatched = [
        event for event in events if event.type == "fanout.child.dispatched"
    ]
    assert dispatched[0].payload["child_id"] == "review-a-docs"
    assert dispatched[0].payload["payload"]["instruction"] == (
        "Run only docs smoke checks."
    )
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Child-Specific Context" in briefing
    assert "Run only docs smoke checks." in briefing
    assert '"expected_output": "approve docs-only candidate"' in briefing


def test_reader_semantic_replan_trigger_is_required_immutable_input(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="discovery", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(
            flow_metadata={"result_protocol": {"mode": "blocking"}},
            stages=[WorkflowStageConfig(
                id="post-verify-discovery",
                trigger="flow.discovery.requested",
                topology="fanout_reader",
                roles=["discovery"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="flow.discovery.completed",
                    failure_event="flow.discovery.failed",
                ),
            )],
        ),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    writer = EventWriter(log)
    writer.emit(
        "workflow.invoke.accepted",
        actor="zf-cli",
        task_id="TASK-PRD-ROOT",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "task_id": "TASK-PRD-ROOT",
        },
    )
    contract_snapshot = write_task_contract_snapshot(
        state_dir,
        {
            "schema_version": "task-contract-snapshot.v1",
            "workflow_run_id": "workflow-1",
            "task_id": "GAP-CURRENT",
            "contract_revision": "contract-revision-1",
            "task_map_generation": "generation-1",
            "base_commit": "base123",
            "task_ref": "task/GAP-CURRENT",
            "acceptance_criteria": [{
                "acceptance_id": "GAP-CURRENT-AC1",
                "statement": "materialize the missing predecessor slice",
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
            }],
        },
    )
    current_candidate = writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "pdd_id": "PRD-1",
            "candidate_ref": "candidate/PRD-1",
            "candidate_head_commit": "candidate123",
            "candidate_base_commit": "base123",
            "completed_task_ids": ["GAP-CURRENT"],
            "task_map_generation": "generation-1",
        },
    )
    trigger = ZfEvent(
        type="flow.discovery.requested",
        actor="run-manager",
        task_id=None,
        correlation_id="workflow-1",
        payload={
            "schema_version": "semantic-replan-request.v1",
            "workflow_run_id": "workflow-1",
            "pdd_id": "PRD-1",
            "task_id": "GAP-CURRENT",
            "task_map_ref": "artifacts/plan/task-map.json",
            "task_map_generation": "generation-1",
            "contract_revision": "contract-revision-1",
            "task_ref": "task/GAP-CURRENT",
            "contract_snapshot_ref": contract_snapshot["ref"],
            "contract_snapshot_digest": contract_snapshot["sha256"],
            "rework_of": "blocked-current",
            "recommended_action": "replan",
            "guidance": "Create a new task id and supersede only GAP-CURRENT.",
            "supersedes_task_ids": ["GAP-CURRENT"],
            "handoff_kind": "partial_task_continuation",
            "continuation_commit": "partial456",
            "continuation_ref": "worker/dev-1",
            "candidate_ref": "worker/dev-1",
            "candidate_head_commit": "partial456",
            "target_ref": "partial456",
            "candidate_event_id": current_candidate.id,
            "output_profile_id": "global-rescan",
            "output_profile_revision": "1",
        },
    )

    orch.run_once(events=[trigger])

    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )
    child_payload = dict(dispatched.payload["payload"])
    assert dispatched.task_id == "GAP-CURRENT"
    assert child_payload["task_id"] == "GAP-CURRENT"
    assert child_payload["parent_task_id"] == "TASK-PRD-ROOT"
    assert child_payload["output_profile_id"] == "global-rescan"
    assert child_payload["output_profile_revision"] == "1"
    assert not any(
        event.type == "fanout.child.failed"
        and "preregistration failed" in str(event.payload.get("reason") or "")
        for event in log.read_all()
    )
    manifest = hydrate_sidecar_ref(
        state_dir,
        child_payload["attempt_source_manifest"],
    ).payload
    semantic_source = next(
        source for source in manifest["sources"]
        if source["source_id"] == "semantic-replan-request"
    )
    recovered = read_attempt_artifact(
        state_dir,
        manifest=manifest,
        source_id="semantic-replan-request",
        artifact_id="semantic-replan-request",
        json_path="$",
    )
    recovered_payload = json.loads(recovered["content"])
    assert recovered_payload["guidance"] == trigger.payload["guidance"]
    assert recovered_payload["continuation_commit"] == "partial456"
    assert recovered_payload["target_ref"] == "partial456"
    assert semantic_source["sha256"]
    assert any(
        item["source_id"] == "semantic-replan-request"
        for item in child_payload["required_reads"]
    )
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    started = next(
        event for event in log.read_all() if event.type == "fanout.started"
    )
    assert started.payload["target_ref"] == "partial456"
    assert dispatched.payload["target_ref"] == "partial456"
    assert "- target_ref: `partial456`" in briefing
    assert "--source semantic-replan-request" in briefing
    assert "--json-path '$'" in briefing


def test_reader_affinity_stage_slot_handoff_uses_upstream_lane(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    upstream = FanoutContext(
        fanout_id="fanout-dev-evt-1",
        stage_id="dev-fanout",
        topology="fanout_writer_scoped",
        trace_id="trace-1",
        trigger_event_id="evt-dev",
        target_ref="main",
        expected_children=[
            FanoutChild(
                child_id="dev-1-TASK-1",
                role_instance="dev-1",
                target_ref="main",
                payload={
                    "task_id": "TASK-1",
                    "assignment_strategy": "affinity_stage_slots",
                    "lane_profile": "refactor-2",
                    "lane_id": "lane0",
                    "stage_slot": "impl",
                    "affinity_tag": "pi-core",
                },
            ),
        ],
    )
    writer.append(upstream.started_event())
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": upstream.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": "dev-1-TASK-1",
            "run_id": "run-dev-1-TASK-1",
            "role_instance": "dev-1",
            "task_id": "TASK-1",
            "task_ref": "task/TASK-1",
            "source_commit": "abc123",
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": "refactor-2",
            "lane_id": "lane0",
            "stage_slot": "impl",
            "affinity_tag": "pi-core",
            "status": "completed",
        },
    ))
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="review",
                instance_id="review-1",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="review",
                instance_id="review-2",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="test",
                instance_id="test-1",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="test",
                instance_id="test-2",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            affinity_lanes={
                "refactor-2": WorkflowAffinityLaneProfileConfig(
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id="lane0",
                            impl="dev-1",
                            review="review-1",
                            verify="test-1",
                        ),
                        WorkflowAffinityLaneConfig(
                            id="lane1",
                            impl="dev-2",
                            review="review-2",
                            verify="test-2",
                        ),
                    ],
                ),
            },
            stages=[
                WorkflowStageConfig(
                    id="review-candidate",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=[],
                    target_ref="candidate/${pdd_id}",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="refactor-2",
                        stage_slot="review",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="review.approved",
                        failure_event="review.rejected",
                    ),
                ),
                WorkflowStageConfig(
                    id="verify-candidate",
                    trigger="review.approved",
                    topology="fanout_reader",
                    roles=[],
                    target_ref="candidate/${pdd_id}",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="refactor-2",
                        stage_slot="verify",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="test.passed",
                        failure_event="test.failed",
                    ),
                ),
            ],
        ),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": upstream.fanout_id,
            "pdd_id": "F-11111111",
        },
    )])

    events = log.read_all()
    started = [
        event for event in events
        if event.type == "fanout.started" and event.payload["stage_id"] == "review-candidate"
    ][0]
    dispatched = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload["fanout_id"] == started.payload["fanout_id"]
    ]
    assert [sent[0] for sent in transport.sent] == ["review-1"]
    assert dispatched[0].payload["lane_id"] == "lane0"
    assert dispatched[0].payload["stage_slot"] == "review"
    assert dispatched[0].payload["upstream_child_id"] == "dev-1-TASK-1"

    child_id = dispatched[0].payload["child_id"]
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="review-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": started.payload["fanout_id"],
            "child_id": child_id,
            "run_id": dispatched[0].payload["run_id"],
            "status": "completed",
        },
    )])

    manifest = _manifest(state_dir, started.payload["fanout_id"])
    child = manifest["children"][0]
    assert child["status"] == "completed"
    assert child["lane_id"] == "lane0"
    assert child["upstream_task_id"] == "TASK-1"

    review_approved = [
        event for event in log.read_all()
        if event.type == "review.approved" and not event.payload.get("child_id")
    ][-1]
    orch.run_once(events=[review_approved])

    verify_started = [
        event for event in log.read_all()
        if event.type == "fanout.started" and event.payload["stage_id"] == "verify-candidate"
    ][0]
    verify_dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload["fanout_id"] == verify_started.payload["fanout_id"]
    ]
    assert transport.sent[-1][0] == "test-1"
    assert verify_dispatched[0].payload["lane_id"] == "lane0"
    assert verify_dispatched[0].payload["stage_slot"] == "verify"
    assert verify_dispatched[0].payload["upstream_fanout_id"] == started.payload["fanout_id"]


def test_reader_affinity_stage_slot_accepts_operator_recovery_upstream(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    upstream = FanoutContext(
        fanout_id="fanout-dev-evt-1",
        stage_id="dev-fanout",
        topology="fanout_writer_scoped",
        trace_id="trace-1",
        trigger_event_id="evt-dev",
        target_ref="main",
        expected_children=[
            FanoutChild(
                child_id="dev-1-TASK-1",
                role_instance="dev-1",
                target_ref="main",
                payload={
                    "task_id": "TASK-1",
                    "assignment_strategy": "affinity_stage_slots",
                    "lane_profile": "refactor-2",
                    "lane_id": "lane0",
                    "stage_slot": "impl",
                    "affinity_tag": "pi-core",
                },
            ),
        ],
    )
    writer.append(upstream.started_event())
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": upstream.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": "dev-1-TASK-1",
            "run_id": "run-dev-1-TASK-1",
            "role_instance": "dev-1",
            "task_id": "TASK-1",
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": "refactor-2",
            "lane_id": "lane0",
            "stage_slot": "impl",
            "affinity_tag": "pi-core",
            "status": "completed",
        },
    ))
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="verify",
                instance_id="verify-1",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            affinity_lanes={
                "refactor-2": WorkflowAffinityLaneProfileConfig(
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id="lane0",
                            impl="dev-1",
                            verify="verify-1",
                        ),
                    ],
                ),
            },
            stages=[
                WorkflowStageConfig(
                    id="verify-candidate",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=[],
                    target_ref="candidate/${pdd_id}",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="refactor-2",
                        stage_slot="verify",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="verify.passed",
                        failure_event="verify.failed",
                    ),
                ),
            ],
        ),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "operator-manual-fanout",
            "pdd_id": "F-11111111",
            "operator_recovery": {
                "upstream_fanout_id": upstream.fanout_id,
            },
        },
    )])

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("reason") == "missing_upstream_affinity_fanout"
    ]
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload["stage_id"] == "verify-candidate"
    ][0]
    dispatched = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload["fanout_id"] == started.payload["fanout_id"]
    ]
    assert [sent[0] for sent in transport.sent] == ["verify-1"]
    assert dispatched[0].payload["upstream_fanout_id"] == upstream.fanout_id
    assert dispatched[0].payload["lane_id"] == "lane0"


def test_reader_affinity_stage_slot_missing_lane_fails_closed(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    upstream = FanoutContext(
        fanout_id="fanout-dev-evt-1",
        stage_id="dev-fanout",
        topology="fanout_writer_scoped",
        trace_id="trace-1",
        trigger_event_id="evt-dev",
        target_ref="main",
        expected_children=[
            FanoutChild(
                child_id="dev-1-TASK-1",
                role_instance="dev-1",
                target_ref="main",
                payload={
                    "task_id": "TASK-1",
                    "assignment_strategy": "affinity_stage_slots",
                    "lane_profile": "refactor-2",
                    "stage_slot": "impl",
                    "affinity_tag": "pi-core",
                },
            ),
        ],
    )
    writer.append(upstream.started_event())
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": upstream.fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": "dev-1-TASK-1",
            "run_id": "run-dev-1-TASK-1",
            "role_instance": "dev-1",
            "task_id": "TASK-1",
            "status": "completed",
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": "refactor-2",
            "stage_slot": "impl",
            "affinity_tag": "pi-core",
        },
    ))
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="review",
                instance_id="review-1",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            affinity_lanes={
                "refactor-2": WorkflowAffinityLaneProfileConfig(
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id="lane0",
                            impl="dev-1",
                            review="review-1",
                        ),
                    ],
                ),
            },
            stages=[
                WorkflowStageConfig(
                    id="review-candidate",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=[],
                    target_ref="candidate/${pdd_id}",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="refactor-2",
                        stage_slot="review",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="review.approved",
                        failure_event="review.rejected",
                    ),
                ),
            ],
        ),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": upstream.fanout_id,
            "pdd_id": "F-11111111",
        },
    )])

    events = log.read_all()
    cancelled = [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("stage_id") == "review-candidate"
    ]
    assert transport.sent == []
    assert cancelled[-1].payload["reason"] == "missing_affinity_child_identity"
    assert cancelled[-1].payload["diagnostics"][0]["errors"] == ["missing_lane_id"]
    assert not [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "review-candidate"
    ]


def test_affinity_reader_briefing_rejects_missing_lane_identity(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    role = RoleConfig(
        name="review",
        instance_id="review-1",
        backend="mock",
        role_kind="reader",
    )
    context = FanoutContext(
        fanout_id="fanout-review",
        stage_id="review-candidate",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="candidate/F-1",
    )

    with pytest.raises(RuntimeError, match="missing_lane_id"):
        orch._write_fanout_briefing(  # type: ignore[attr-defined]
            role=role,
            context=context,
            child_id="review-1-TASK-1",
            run_id="run-1",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                success_event="review.approved",
                failure_event="review.rejected",
            ),
            child_payload={
                "assignment_strategy": "affinity_stage_slots",
                "stage_slot": "review",
                "affinity_tag": "pi-core",
            },
        )


def test_reader_fanout_briefing_includes_workflow_input_manifest(tmp_path: Path):
    _state_dir, _log, transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-workflow",
        payload={
            "pdd_id": "F-11111111",
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "source_refs": {"channel_id": "ch-zaofu"},
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
        },
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Workflow Input Manifest" in briefing
    assert "workflow-inputs/wf-review/manifest.json" in briefing
    assert "channels/ch-zaofu/spec.md" in briefing


def test_verification_briefing_nests_binding_identity_in_typed_result(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    snapshot = {
        "schema_version": "task-contract-snapshot.v1",
        "workflow_run_id": "run-verify",
        "task_id": "T-VERIFY",
        "contract_revision": "contract-1",
        "task_map_generation": "generation-1",
        "base_commit": "base-1",
        "task_ref": "task/T-VERIFY",
        "acceptance_criteria": [{
            "acceptance_id": "AC-1",
            "statement": "verify the requested behavior",
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
        }],
    }
    descriptor = write_task_contract_snapshot(state_dir, snapshot)
    context = FanoutContext(
        fanout_id="fanout-verify",
        stage_id="verify-selected",
        topology="fanout_reader",
        trace_id="run-verify",
        trigger_event_id="evt-verify",
        target_ref="candidate/T-VERIFY",
    )
    child_payload = {
        **snapshot,
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "b" * 64,
        "target_commit": "target-1",
    }

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="verify",
            instance_id="verify-1",
            backend="mock",
            role_kind="reader",
        ),
        context=context,
        child_id="verify-1-T-VERIFY",
        run_id="attempt-verify-1",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="verify.child.completed",
            child_failure_event="verify.child.failed",
            success_event="verify.approved",
            failure_event="verify.rejected",
        ),
        child_payload=child_payload,
    )

    briefing = path.read_text(encoding="utf-8")
    command = briefing.split("Success command:\n```bash\n", 1)[1].split("\n```", 1)[0]
    argv = shlex.split(command)
    payload = json.loads(Path(argv[argv.index("--payload-file") + 1]).read_text())
    result = payload["verification_result"]
    for key in (
        "workflow_run_id",
        "task_id",
        "contract_revision",
        "task_map_generation",
        "base_commit",
        "task_ref",
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "target_snapshot_ref",
        "target_commit",
        "target_snapshot_digest",
    ):
        assert result[key] == payload[key]
    failure_command = briefing.split(
        "Failure command:\n```bash\n", 1,
    )[1].split("\n```", 1)[0]
    failure_argv = shlex.split(failure_command)
    failure_payload = json.loads(
        Path(failure_argv[failure_argv.index("--payload-file") + 1]).read_text()
    )
    failure_result = failure_payload["verification_result"]
    assert failure_result["verdict"] == "rejected"
    assert {
        item["status"] for item in failure_result["requirement_results"]
    } == {"failed"}
    assert all(item["evidence_refs"] for item in failure_result["requirement_results"])
    assert (
        "use only `passed`, `failed`, `blocked`, `waived`, or "
        "`not_applicable`"
    ) in briefing


def test_semantic_submit_briefing_exposes_profile_not_capability(
    tmp_path: Path,
) -> None:
    _state_dir, _log, _transport, orch = _state(tmp_path)
    context = FanoutContext(
        fanout_id="fanout-semantic",
        stage_id="verify-selected",
        topology="fanout_reader",
        trace_id="run-semantic",
        trigger_event_id="evt-semantic",
        target_ref="candidate/T1",
    )
    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="review-a",
            backend="codex",
            role_kind="reader",
        ),
        context=context,
        child_id="review-a-T1",
        run_id="attempt-1",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="verify.child.completed",
            child_failure_event="verify.child.failed",
            success_event="verify.approved",
            failure_event="verify.rejected",
        ),
        child_payload={
            "operation_id": "wop-verify-1",
            "output_profile_id": "task-verify",
            "output_profile_revision": "1",
            "semantic_result_submit_mode": "blocking",
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert "result submit" in briefing
    assert "--operation wop-verify-1" in briefing
    assert "profile: `task-verify` revision `1`" in briefing
    assert "ZF_RESULT_SUBMIT_TOKEN" not in briefing
    assert "When finished, emit exactly one result event with this payload" not in briefing
    assert briefing.count("result submit") == 1
    assert "Failure command:" not in briefing
    assert path.with_suffix(".md.metrics.json").exists()


def test_artifact_production_briefing_separates_findings_from_execution(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    context = FanoutContext(
        fanout_id="fanout-audit",
        stage_id="scope",
        topology="fanout_reader",
        trace_id="run-audit",
        trigger_event_id="evt-audit",
        target_ref="",
    )
    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="audit-scoper",
            backend="codex",
            role_kind="reader",
        ),
        context=context,
        child_id="audit-scoper",
        run_id="attempt-audit",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="workflow.child.completed",
            child_failure_event="workflow.child.failed",
            success_event="scope.completed",
            failure_event="scope.failed",
        ),
        child_payload={
            "operation_id": "wop-audit",
            "attempt_id": "attempt-audit",
            "output_profile_id": "workflow-read",
            "output_profile_revision": "1",
            "semantic_result_submit_mode": "blocking",
            "result_semantics": "artifact_production",
            "workflow_output_ports": [
                {"name": "scope", "kind": "audit/scope"},
            ],
            "required_delivery_artifacts": [
                {
                    "name": "report",
                    "kind": "report/markdown",
                    "source_ref": "synthesize.report",
                },
            ],
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert "not whether the inspected subject is healthy" in briefing
    assert "high/critical findings" in briefing
    assert "Run-level `required_delivery_artifacts`" in briefing
    assert "must not make it fail" in briefing
    assert "`outputs.scope`" in briefing
    scratch = (
        state_dir
        / "tmp/result-submit/wop-audit/attempt-audit/result.json"
    )
    assert json.loads(scratch.read_text(encoding="utf-8"))["outputs"] == {
        "scope": "",
    }


def test_plan_candidate_briefing_validates_before_submit(
    tmp_path: Path,
) -> None:
    _state_dir, _log, _transport, orch = _state(tmp_path)
    context = FanoutContext(
        fanout_id="fanout-plan-validate",
        stage_id="issue-triage",
        topology="fanout_reader",
        trace_id="run-plan-validate",
        trigger_event_id="evt-plan-validate",
        target_ref="docs/issues/one.md",
    )
    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="planner",
            backend="codex",
            role_kind="reader",
        ),
        context=context,
        child_id="planner",
        run_id="attempt-plan",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="issue.triage.child.completed",
            child_failure_event="issue.triage.child.failed",
            success_event="task_map.ready",
            failure_event="issue.triage.failed",
        ),
        child_payload={
            "operation_id": "wop-plan-1",
            "output_profile_id": "workflow-read",
            "output_profile_revision": "1",
            "semantic_result_submit_mode": "blocking",
            "plan_candidate_validation": {
                "schema_version": "plan-candidate-validation-context.v1",
                "manifest": {"stage_id": "issue-triage"},
                "metadata": {
                    "flow_kind": "issue",
                    "artifact_package": {"required_ports": ["issue_spec", "task_map", "product_acceptance_spec"]},
                    "task_pipeline": {
                        "candidate": {"rolling_smoke": "required"},
                    },
                },
                "writer_policy": {
                    "candidate_quality_source": "task_contract_required",
                    "work_units": {
                        "enabled": True,
                        "split_quality": {
                            "mode": "blocking",
                            "max_scope_files": 12,
                            "max_acceptance_criteria": 8,
                            "require_validation_surface": True,
                        },
                    },
                },
            },
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert briefing.count("result validate") == 1
    assert briefing.count("result submit") == 1
    assert briefing.index("result validate") < briefing.index("result submit")
    assert "does not consume the submit capability" in briefing
    assert "Effective Plan Admission Policy (kernel-pinned)" in briefing
    assert "maximum scope paths per task: `12`" in briefing
    assert "maximum acceptance criteria per task: `8`" in briefing
    assert "rolling smoke policy: `required`" in briefing
    assert "tier `static` or `runtime`" in briefing
    assert "required Plan inputs/outputs: `issue_spec`, `task_map`, `product_acceptance_spec`, `source_index`" in briefing
    assert "do not rewrite intake" in briefing
    # Validate the nested authoring example from the actual dispatch, after
    # adding only the identities that the Kernel binds at admission.
    example_line = next(line for line in briefing.splitlines()
                        if "Product Acceptance shape example" in line)
    example = json.loads(example_line.split("`", 1)[1].rsplit("`", 1)[0])
    from zf.runtime.product_acceptance import validate_product_acceptance_spec
    validate_product_acceptance_spec({
        **example["body"], "schema_version": example["schema_version"],
        "workflow_run_id": "run-test", "flow_kind": "issue",
        "plan_revision": "r1", "task_map_generation": "g1",
    })


def test_artifact_delivery_briefing_pins_identity_and_admitted_inputs(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    context = FanoutContext(
        fanout_id="fanout-artifact-delivery",
        stage_id="verify",
        topology="fanout_reader",
        trace_id="run-generic",
        trigger_event_id="evt-verify",
        target_ref="",
    )
    report_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "report.v1", "body": "Grounded report."},
        root="generic-workflow/outputs/run-generic/synthesize/report",
        kind="report/markdown",
        schema_version="generic-workflow-output.v1",
        created_by="test",
    )
    artifact = {
        **report_ref,
        "name": "report",
        "kind": "report/markdown",
        "source_ref": "synthesize.report",
        "producer_stage_id": "synthesize",
    }
    control_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "workflow-read-result.v1",
            "status": "passed",
            "output_artifacts": [artifact],
        },
        root="call-results/control/workflow-read-result.v1",
        kind="call_control_result",
        schema_version="workflow-read-result.v1",
        created_by="test",
    )
    envelope = normalize_call_result_envelope(
        source_payload={
            "workflow_run_id": "run-generic",
            "attempt_id": "attempt-synthesize",
            "stage_id": "synthesize",
            "role_instance": "workflow-synthesizer",
        },
        control_result={
            "schema_version": "workflow-read-result.v1",
            **control_ref,
        },
        workflow_run_id="run-generic",
        operation_id="operation-synthesize",
        request_hash="request-synthesize",
        source_event_id="evt-synthesize",
        source_event_type="workflow.child.completed",
        actor="workflow-synthesizer",
        correlation_id="run-generic",
    )
    input_result = write_immutable_json_sidecar(
        state_dir,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    input_result_ref = input_result["ref"]
    scratch_ref = "tmp/result-submit/operation-verify/attempt-verify/result.json"
    child_payload = {
        "workflow_run_id": "run-generic",
        "goal_id": "goal-generic",
        "flow_kind": "workflow",
        "workflow_generation": "a" * 64,
        "request_revision": 2,
        "generic_workflow_contract_digest": "b" * 64,
        "workflow_intent": "research",
        "workflow_template": "evidence-synthesis-v1",
        "completion_profile": "artifact_delivery",
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "c" * 64,
        "goal_claim_set_ref": (
            "artifacts/goal-closure/claim-sets/current.json"
        ),
        "goal_claim_set_digest": "d" * 64,
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
        }],
        "input_result_refs": [input_result_ref],
        "operation_id": "operation-verify",
        "attempt_id": "attempt-verify",
        "result_scratch_ref": scratch_ref,
        "output_profile_id": "artifact-delivery",
        "output_profile_revision": "1",
        "semantic_result_submit_mode": "blocking",
    }

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="workflow-verifier",
            instance_id="workflow-verifier",
            backend="codex",
            role_kind="reader",
        ),
        context=context,
        child_id="workflow-verifier",
        run_id="attempt-verify",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="workflow.child.completed",
            child_failure_event="workflow.child.failed",
            success_event="workflow.verify.completed",
            failure_event="workflow.verify.failed",
        ),
        child_payload=child_payload,
    )

    briefing = path.read_text(encoding="utf-8")
    assert "result submit" in briefing
    assert "--operation operation-verify" in briefing
    assert "When finished, emit exactly one result event" not in briefing
    assert '"goal_id": "goal-generic"' in briefing
    assert input_result_ref in briefing
    assert "this artifact-delivery profile has no candidate branch" in briefing
    assert "Do not enumerate `candidate/*`" in briefing
    assert "evidence from another `workflow_run_id`" in briefing
    assert "If a deliverable branch exists" not in briefing
    scratch = json.loads(
        (state_dir / scratch_ref).read_text(encoding="utf-8")
    )
    for key in (
        "workflow_run_id",
        "goal_id",
        "workflow_generation",
        "request_revision",
        "generic_workflow_contract_digest",
        "workflow_intent",
        "workflow_template",
        "completion_profile",
        "run_contract_ref",
        "run_contract_digest",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "input_result_refs",
    ):
        assert key not in scratch
    assert scratch["artifacts"][0]["source_ref"] == "synthesize.report"
    assert scratch["artifacts"][0]["ref"] == artifact["ref"]
    assert "Kernel. Do not add raw child result files" in briefing


def test_goal_closure_success_uses_editable_payload_draft(
    tmp_path: Path,
    monkeypatch,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    orch.config = ZfConfig(project=ProjectConfig(name="goal-closure"))
    monkeypatch.setattr(
        "zf.runtime.goal_closure_identity.validate_goal_closure_dispatch_snapshots",
        lambda state_dir, payload: None,
    )

    class _ClaimSet:
        payload = {"claims": [{"goal_claim_id": "CLAIM-1", "mandatory": True}]}

    monkeypatch.setattr(
        "zf.runtime.sidecar_refs.hydrate_sidecar_ref",
        lambda state_dir, descriptor: _ClaimSet(),
    )
    context = FanoutContext(
        fanout_id="fanout-goal",
        stage_id="goal-closure",
        topology="fanout_reader",
        trace_id="workflow-1",
        trigger_event_id="evt-goal",
        target_ref="candidate/goal-1",
    )
    child_payload = {
        "closure_identity": "closure-1",
        "goal_claim_set_ref": "artifacts/claim-set.json",
        "goal_claim_set_digest": "a" * 64,
        "workflow_run_id": "workflow-1",
        "task_map_generation": "generation-1",
        "target_commit": "commit-1",
        "goal_id": "goal-1",
        "flow_kind": "prd",
        "closure_fact_ref": "artifacts/closure-fact.json",
        "closure_fact_digest": "b" * 64,
        "objective_ref": "artifacts/objective.json",
        "planning_result_ref": "artifacts/task-map.json",
        "candidate_ref": "candidate/goal-1",
        "input_result_refs": ["artifacts/result-1.json"],
    }

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=RoleConfig(
            name="judge-prd",
            instance_id="judge-prd",
            backend="mock",
            role_kind="reader",
        ),
        context=context,
        child_id="judge-prd",
        run_id="attempt-goal-1",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="judge.child.completed",
            child_failure_event="judge.child.failed",
            success_event="goal.closure.synthesized",
            failure_event="goal.closure.synthesis.failed",
        ),
        child_payload=child_payload,
    )

    briefing = path.read_text(encoding="utf-8")
    command = briefing.split("Success command:\n```bash\n", 1)[1].split("\n```", 1)[0]
    argv = shlex.split(command)
    assert "--payload-file" in argv
    assert "--payload" not in argv
    payload_path = Path(argv[argv.index("--payload-file") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["goal_closure_result"]["verdict"] == "passed"
    assert payload["goal_closure_result"]["goal_coverage"] == [{
        "goal_claim_id": "CLAIM-1",
        "status": "closed",
        "supporting_result_refs": ["artifacts/result-1.json"],
    }]
    assert payload_path.is_relative_to(
        state_dir / "artifacts" / "attempts" / "attempt-goal-1"
    )


def test_reader_fanout_accepts_result_metadata_nested_under_report(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[ZfEvent(
        type="review.approved",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "report": {
                "fanout_id": fanout_id,
                "child_id": "review-a",
                "run_id": f"run-{fanout_id}-review-a",
                "role_instance": "review-a",
                "status": "passed",
                "summary": "Reviewed with nested fanout metadata.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    )])

    completed = next(event for event in log.read_all()
                     if event.type == "fanout.child.completed")
    assert completed.payload["fanout_id"] == fanout_id
    assert completed.payload["child_id"] == "review-a"
    assert completed.payload["run_id"] == f"run-{fanout_id}-review-a"
    assert completed.payload["report"]["summary"] == (
        "Reviewed with nested fanout metadata."
    )


def test_reader_fanout_accepts_configured_child_result_event(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
            "report": {
                "child_id": "review-a",
                "status": "passed",
                "summary": "Reviewed through child result event.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    )])

    completed = next(event for event in log.read_all()
                     if event.type == "fanout.child.completed")
    assert completed.payload["result_event_id"]
    assert completed.payload["report"]["summary"] == (
        "Reviewed through child result event."
    )


def test_reader_fanout_briefing_includes_enabled_role_skills(tmp_path: Path):
    _state_dir, log, transport, orch = _state(tmp_path, review_skills=True)

    _start_fanout(orch)

    dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    ]
    assert dispatched[0].payload["skills"] == ["verify-review"]
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Enabled Skills" in briefing
    assert "`/verify-review`" in briefing
    skill_section = briefing.split("## Enabled Skills", 1)[1].split("##", 1)[0]
    assert "source:" not in skill_section
    assert "runtime:" not in skill_section
    metrics = json.loads(
        transport.sent[0][1].with_suffix(".md.metrics.json").read_text(
            encoding="utf-8",
        )
    )
    assert metrics["auto_injected_skill_count"] == 0


def test_fanout_skill_briefing_requires_auto_injected_skill_activation():
    role = RoleConfig(
        name="planner",
        backend="claude-code",
        role_kind="reader",
        skills=["planner-context"],
    )
    entries = [
        SkillLockEntry(
            role="planner",
            instance_id="planner",
            backend="claude-code",
            task_id=None,
            run_id=None,
            name="planner-context",
            source="skills/planner-context/SKILL.md",
            sha256="context",
            description="Planner role boundary.",
            auto_inject=True,
            load_on_demand=False,
            dependencies=("matrix-enrichment",),
        ),
        SkillLockEntry(
            role="planner",
            instance_id="planner",
            backend="claude-code",
            task_id=None,
            run_id=None,
            name="matrix-enrichment",
            source="skills/matrix-enrichment/SKILL.md",
            sha256="matrix",
            description="Close matrix ids.",
            dependency_of=("planner-context",),
        ),
    ]

    briefing = "\n".join(render_skill_briefing_section(role, entries))

    assert "### Auto-Injected Skills (Required)" in briefing
    assert "Invoke or read every skill" in briefing
    assert "`/planner-context`" in briefing
    assert "### Load-On-Demand Skills" in briefing
    assert "`/matrix-enrichment`" in briefing
    assert "dependency of: planner-context" in briefing


def test_busy_reader_defers_before_repeated_skill_materialization(
    tmp_path: Path,
) -> None:
    _state_dir, log, transport, orch = _state(
        tmp_path,
        review_skills=True,
    )
    orch._set_worker_state(  # type: ignore[attr-defined]
        "review-a",
        "busy",
        reason="previous provider turn active",
        force=True,
    )
    orch._set_worker_state(  # type: ignore[attr-defined]
        "review-b",
        "busy",
        reason="previous provider turn active",
        force=True,
    )

    _start_fanout(orch)
    orch.run_once(events=[])

    events = log.read_all()
    assert transport.sent == []
    assert not [
        event for event in events
        if event.type == "skills.materialized"
    ]
    assert {
        event.payload["role_instance"]
        for event in events
        if event.type == "fanout.child.dispatch_deferred"
    } == {"review-a", "review-b"}


def test_wait_for_all_waits_for_all_terminal_children(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[ZfEvent(
        type="review.approved",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "approved",
        },
    )])
    assert not any(
        event.type == "fanout.aggregate.completed" for event in log.read_all()
    )

    orch.run_once(events=[ZfEvent(
        type="review.approved",
        actor="review-b",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-b",
            "run_id": f"run-{fanout_id}-review-b",
            "status": "approved",
        },
    )])

    manifest = _manifest(state_dir, fanout_id)
    assert manifest["aggregate"]["status"] == "completed"
    assert (state_dir / "fanouts" / fanout_id / "children" / "review-a" / "result.json").exists()
    aggregate_event = next(event for event in log.read_all()
                           if event.type == "review.approved"
                           and not event.payload.get("child_id"))
    orch.run_once(events=[aggregate_event])
    assert not any(event.type == "event.malformed" for event in log.read_all())


def test_product_reader_aggregate_projects_generic_schema_payload(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="task-map-synth", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="product-task-map",
                trigger="product.design.ready",
                topology="fanout_reader",
                roles=["task-map-synth"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="product.task_map.child.completed",
                    child_failure_event="product.task_map.child.failed",
                    success_event="task_map.ready",
                    failure_event="product.design.blocked",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="product.design.ready",
        actor="zf-cli",
        correlation_id="trace-product",
        payload={
            "pdd_id": "PDD-1",
            "feature_id": "PDD-1",
            "rework_of": "evt-upstream-blocker",
            "rework_attempt": 2,
            "rework_source": "dev.blocked",
            "rework_feedback": ["core must model off-lane siding"],
            "rework_categories": ["task_contract_unsatisfiable"],
            "replan_classification": "design_issue",
            "failed_task_ids": ["PDD-1-CORE", "PDD-1-SCHED"],
            "task_ids": ["PDD-1-CORE", "PDD-1-SCHED", "PDD-1-UI"],
            "downstream_task_ids": ["PDD-1-UI"],
            "resume_scope": "failed_children_and_downstream",
        },
    )])
    fanout_id = next(
        event.payload["fanout_id"] for event in log.read_all()
        if event.type == "fanout.started"
    )
    orch.run_once(events=[ZfEvent(
        type="product.task_map.child.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "child_id": "task-map-synth",
            "run_id": f"run-{fanout_id}-task-map-synth",
            "status": "completed",
            "summary": "task map ready",
            "pdd_id": "PDD-1",
            "feature_id": "PDD-1",
            "task_map_ref": ".zf/task-map.json",
            "source_commit": "source-commit",
            "candidate_base_commit": "base-commit",
            "artifact_refs": ["docs/product-plan.md"],
            "evidence_refs": ["reports/task-map.json"],
        },
    )])

    task_map_ready = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
    ][-1]
    payload = task_map_ready.payload
    assert payload["pdd_id"] == "PDD-1"
    assert payload["feature_id"] == "PDD-1"
    assert payload["task_map_ref"] == ".zf/task-map.json"
    assert payload["source_commit"] == "source-commit"
    assert payload["candidate_base_commit"] == "base-commit"
    assert payload["rework_of"] == "evt-upstream-blocker"
    assert payload["rework_attempt"] == 2
    assert payload["rework_source"] == "dev.blocked"
    assert payload["rework_feedback"] == ["core must model off-lane siding"]
    assert payload["replan_classification"] == "design_issue"
    assert payload["failed_task_ids"] == ["PDD-1-CORE", "PDD-1-SCHED"]
    assert payload["task_ids"] == [
        "PDD-1-CORE",
        "PDD-1-SCHED",
        "PDD-1-UI",
    ]
    assert payload["downstream_task_ids"] == ["PDD-1-UI"]
    assert payload["resume_scope"] == "failed_children_and_downstream"
    assert "docs/product-plan.md" in payload["artifact_refs"]
    assert any(ref.endswith("/report.json") for ref in payload["artifact_refs"])
    assert payload["evidence_refs"] == [
        "reports/task-map.json",
        ".zf/task-map.json",
    ]


def test_candidate_ready_cancels_pending_candidate_failure_replan(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="task-map-synth", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="product-task-map",
            trigger="product.design.ready",
            topology="fanout_reader",
            roles=["task-map-synth"],
            aggregate=FanoutAggregateConfig(
                success_event="task_map.ready",
                failure_event="product.design.blocked",
            ),
        )]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    failure = writer.append(ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1"},
    ))
    trigger = writer.append(ZfEvent(
        type="product.design.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "PDD-1",
            "rework_of": failure.id,
            "rework_source": "integration.failed",
        },
    ))
    orch.run_once(events=[trigger])
    fanout_id = next(
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
    )
    ready = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1", "candidate_head_commit": "abc"},
    ))

    orch.run_once(events=[ready])

    cancelled = [
        event for event in log.read_all()
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == (
        "candidate_replan_superseded_by_candidate_ready"
    )
    assert cancelled[0].payload["superseded_by"] == ready.id
    assert not any(event.type == "task_map.ready" for event in log.read_all())


def test_candidate_ready_triggered_fanout_does_not_cancel_itself(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="verifier", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="candidate-verify",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["verifier"],
            aggregate=FanoutAggregateConfig(
                success_event="verify.passed",
                failure_event="verify.failed",
            ),
        )]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    failure = writer.append(ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "PDD-1"},
    ))
    ready = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "PDD-1",
            "candidate_head_commit": "abc",
            "rework_of": failure.id,
            "rework_source": "integration.failed",
        },
    ))

    orch.run_once(events=[ready])

    fanout = next(
        event for event in log.read_all()
        if event.type == "fanout.started"
    )
    assert fanout.payload["trigger_event_id"] == ready.id
    assert not any(
        event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
        for event in log.read_all()
    )


def test_verified_generation_suppresses_candidate_reader_redrive(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="verifier", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="candidate-verify",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["verifier"],
            aggregate=FanoutAggregateConfig(
                success_event="test.passed",
                failure_event="test.failed",
            ),
        )]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    generation = "a" * 64
    original = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "pdd_id": "PDD-1",
            "candidate_ref": "candidate/PDD-1",
            "candidate_head_commit": "candidate-head-1",
            "completed_task_ids": ["TASK-1"],
            "task_map_generation": generation,
            "quality_status": "passed",
        },
    ))
    writer.append(ZfEvent(
        type="test.passed",
        actor="zf-cli",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "target_ref": "candidate/PDD-1",
            "task_ids": ["TASK-1"],
            "task_map_generation": generation,
        },
    ))
    redrive = writer.append(ZfEvent(
        type="candidate.ready",
        actor="operator",
        correlation_id="run-1",
        payload={
            **original.payload,
            "redrive_of": original.id,
        },
    ))

    orch.run_once(events=[redrive])

    assert not [
        event for event in log.read_all()
        if event.type == "fanout.started"
    ]
    suppressed = next(
        event for event in log.read_all()
        if event.type == "fanout.retrigger.suppressed"
    )
    assert suppressed.payload["stage_id"] == "candidate-verify"
    assert suppressed.payload["reason"] == "generation_already_verified"
    assert suppressed.payload["candidate_event_id"] == original.id


def test_stale_task_pipeline_candidate_does_not_start_reader_fanout(
    tmp_path: Path,
) -> None:
    _state_dir, log, transport, orch = _state(tmp_path)
    writer = EventWriter(log)
    for generation_id, task_map_generation in (
        ("generation-old", "map-old"),
        ("generation-current", "map-current"),
    ):
        writer.append(ZfEvent(
            type="task.pipeline.generation.admitted",
            actor="zf-cli",
            origin="kernel",
            correlation_id="run-1",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "generation_id": generation_id,
                "workflow_run_id": "run-1",
                "task_map_generation": task_map_generation,
                "task_ids": ["TASK-1"],
            },
        ))
    stale = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-1",
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": "run-1",
            "generation_id": "generation-old",
            "task_map_generation": "map-old",
            "pdd_id": "F-11111111",
            "candidate_ref": "refs/heads/candidate/F-11111111",
            "candidate_head_commit": "candidate-head-old",
        },
    ))

    orch.run_once(events=[stale])
    orch.run_once(events=[stale])

    assert transport.sent == []
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.started"
    ]
    suppressed = [
        event for event in log.read_all()
        if event.type == "fanout.retrigger.suppressed"
        and event.payload.get("reason")
        == "candidate_ready_stale_task_pipeline_generation"
    ]
    assert len(suppressed) == 1
    assert suppressed[0].payload["trigger_event_id"] == stale.id
    assert suppressed[0].payload["stage_ids"] == ["review-candidate"]
    assert suppressed[0].payload["claimed_generation_id"] == "generation-old"
    assert suppressed[0].payload["current_generation_id"] == "generation-current"


def test_stale_task_pipeline_candidate_manifest_is_cancelled_before_retry(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-1",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-old",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-old",
            "task_ids": ["TASK-1"],
        },
    ))
    candidate = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-1",
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": "run-1",
            "generation_id": "generation-old",
            "task_map_generation": "map-old",
            "pdd_id": "F-11111111",
            "candidate_ref": "refs/heads/candidate/F-11111111",
            "candidate_head_commit": "candidate-head-old",
        },
    ))
    orch.run_once(events=[candidate])
    started = next(
        event for event in log.read_all()
        if event.type == "fanout.started"
    )
    from zf.runtime.workflow_operation import WorkflowOperationService

    operation_id = "wop-stale-candidate-review-a"
    ensured = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    ).ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="fanout_reader_child",
        request={"attempt_domain": "task"},
        parent_stage_id="review-candidate",
        task_id="F-11111111",
        role_instance="review-a",
        causation_id=started.id,
        correlation_id="run-1",
    )
    manifest = _manifest(state_dir, started.payload["fanout_id"])
    child = next(
        item for item in manifest["children"]
        if item["child_id"] == "review-a"
    )
    child["operation_id"] = operation_id
    child["request_hash"] = ensured.request_hash
    manifest_path = (
        state_dir
        / "fanouts"
        / started.payload["fanout_id"]
        / "manifest.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    initial_dispatch_count = len(transport.sent)

    current = writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-1",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-current",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-current",
            "task_ids": ["TASK-2"],
        },
    ))
    writer.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "review-a",
            "role": "review-a",
            "backend": "mock",
            "launch_attempt": 2,
            "is_resume": False,
        },
    ))

    orch.run_once(events=[])

    events = log.read_all()
    assert len(transport.sent) == initial_dispatch_count
    assert not [
        event for event in events
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    ]
    cancelled = next(
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == started.payload["fanout_id"]
    )
    assert cancelled.payload["reason"] == (
        "candidate_ready_stale_task_pipeline_generation"
    )
    assert cancelled.payload["superseded_by"] == current.id
    cancelled_operations = {
        str(event.payload.get("operation_id") or "")
        for event in events
        if event.type == "workflow.operation.cancelled"
    }
    assert cancelled_operations == {operation_id}
    assert _manifest(state_dir, started.payload["fanout_id"])["status"] == (
        "cancelled"
    )


def _task_map_synth_orchestrator(
    tmp_path: Path,
    *,
    complete_child: bool = True,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="source-reader", backend="mock", role_kind="reader"),
            RoleConfig(
                name="task-map-synth",
                backend="mock",
                role_kind="reader",
                publishes=["fanout.synth.completed"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="product-task-map",
                trigger="product.design.ready",
                topology="fanout_reader",
                roles=["source-reader"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="task_map.child.completed",
                    child_failure_event="task_map.child.failed",
                    success_event="task_map.ready",
                    failure_event="task_map.blocked",
                    synth_role="task-map-synth",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    orch.run_once(events=[ZfEvent(
        type="product.design.ready",
        actor="zf-cli",
        correlation_id="trace-product",
        payload={"pdd_id": "PDD-1", "feature_id": "PDD-1"},
    )])
    fanout_id = next(
        event.payload["fanout_id"] for event in log.read_all()
        if event.type == "fanout.started"
    )
    if complete_child:
        orch.run_once(events=[ZfEvent(
            type="task_map.child.completed",
            actor="source-reader",
            correlation_id="trace-product",
            payload={
                "fanout_id": fanout_id,
                "child_id": "source-reader",
                "run_id": f"run-{fanout_id}-source-reader",
                "status": "completed",
                "summary": "source read",
                "artifact_refs": ["docs/product-prd.md"],
            },
        )])
    return state_dir, log, orch, fanout_id


def test_task_map_synth_payload_projects_task_map_ref(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "task map ready",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "task map ready",
                "findings": [],
                "recommendation": "approve",
                "pdd_id": "PDD-1",
                "feature_id": "PDD-1",
                "task_map_ref": ".zf/artifacts/PDD-1/task_map.json",
                "source_index_ref": ".zf/artifacts/PDD-1/source_index.json",
                "artifact_refs": ["docs/product-prd.md"],
                "evidence_refs": [".zf/artifacts/PDD-1/source_index.json"],
            },
        },
    ))

    orch.run_once(events=[synth_event])

    ready = next(event for event in log.read_all()
                 if event.type == "task_map.ready")
    assert ready.payload["task_map_ref"] == ".zf/artifacts/PDD-1/task_map.json"
    assert ready.payload["source_index_ref"] == ".zf/artifacts/PDD-1/source_index.json"


def test_synth_schema_failure_projects_actionable_diagnostics(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "task map ready",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "task map ready",
                "findings": [{
                    "severity": "info",
                    "category": "residual_risk",
                    "path": "docs/plans/plan.md",
                    "line": -1,
                    "message": "Evidence remains required.",
                }],
                "recommendation": "approve",
                "task_map_ref": ".zf/artifacts/PDD-1/task_map.json",
            },
        },
    ))

    orch.run_once(events=[synth_event])

    aggregate = next(
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    )
    assert aggregate.payload["status"] == "failed"
    assert aggregate.payload["report_diagnostics"] == [
        "findings[0].line must be a positive integer"
    ]
    schema_finding = next(
        finding for finding in aggregate.payload["findings"]
        if finding.get("category") == "report-schema"
    )
    assert "positive integer" in schema_finding["message"]
    blocked = next(
        event for event in log.read_all()
        if event.type == "task_map.blocked"
    )
    assert blocked.payload["report_diagnostics"] == (
        aggregate.payload["report_diagnostics"]
    )


def test_synth_failure_preserves_previous_plan_candidate_refs(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    previous_refs = [{
        "ref": "artifacts/attempt-inputs/candidate-task-map.json",
        "sha256": "a" * 64,
        "kind": "fanout_child_artifact",
    }]
    preflight_ref = {
        "ref": "plan-candidate-preflight/report.json",
        "sha256": "b" * 64,
        "kind": "plan_candidate_preflight",
    }
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="zf-cli",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "failed",
            "recommendation": "reject",
            "summary": "plan candidate failed mechanical preflight",
            "failure_class": "plan_candidate_preflight",
            "plan_candidate_preflight_ref": preflight_ref,
            "previous_plan_candidate_refs": previous_refs,
            "report": {
                "child_id": "synth",
                "status": "failed",
                "summary": "plan candidate failed mechanical preflight",
                "findings": [],
                "recommendation": "reject",
            },
        },
    ))

    orch.run_once(events=[synth_event])

    blocked = next(
        event for event in log.read_all()
        if event.type == "task_map.blocked"
    )
    assert blocked.payload["failure_class"] == "plan_candidate_preflight"
    assert blocked.payload["plan_candidate_preflight_ref"] == preflight_ref
    assert blocked.payload["previous_plan_candidate_refs"] == previous_refs


def test_plan_synth_owner_decision_holds_then_resumes_same_run(
    tmp_path: Path,
) -> None:
    state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    decision_items = [{
        "decision_id": "ODI-AC3-SCOPE",
        "question": "May the Plan add one compatibility fixture?",
        "options": [
            {"option_id": "approve", "label": "Add fixture"},
            {"option_id": "reject", "label": "Keep current scope"},
        ],
        "blocking": True,
        "evidence_refs": ["requirements:AC-3"],
    }]
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "failed",
            "recommendation": "reject",
            "summary": "Owner must decide fixture scope.",
            "owner_decision_items": decision_items,
            "report": {
                "child_id": "synth",
                "status": "failed",
                "summary": "Owner must decide fixture scope.",
                "findings": [],
                "fix_items": [],
                "owner_decision_items": decision_items,
                "recommendation": "reject",
            },
        },
    ))

    orch.run_once(events=[synth_event])

    events = log.read_all()
    checkpoint = next(
        event for event in events
        if event.type == "plan.synth.owner_decision.requested"
    )
    approval = next(event for event in events if event.type == "approval.requested")
    assert checkpoint.payload["workflow_run_id"] == "trace-product"
    assert approval.payload["approve_action"] == "replan-approve"
    assert approval.payload["plan_preview_bundle_ref"].startswith("artifacts/plan-preview/bundles/")
    assert len(approval.payload["plan_preview_bundle_digest"]) == 64
    assert (state_dir / approval.payload["plan_preview_bundle_ref"]).is_file()
    assert not [event for event in events if event.type == "fanout.aggregate.completed"]
    assert not [event for event in events if event.type == "task_map.blocked"]

    inbox = build_operator_inbox(state_dir, events, project_root=tmp_path)
    item = next(row for row in inbox["items"] if row["kind"] == "approval")
    assert item["checkpoint_id"] == checkpoint.payload["checkpoint_id"]
    assert item["proposal_ref"] == checkpoint.payload["proposal_ref"]

    requested = orch.event_writer.append(ZfEvent(
        type="web.action.requested",
        actor="web",
        correlation_id="trace-product",
        payload={"action": "replan-approve"},
    ))
    service = ControlledActionService(
        state_dir,
        orch.event_writer,
        config=orch.config,
        project_root=tmp_path,
        actor="web",
        source="operator-inbox",
        surface="web",
    )
    result = service.execute(
        action="replan-approve",
        requested_action="replan.approve",
        requested=requested,
        payload={
            "approval_ref": item["approval_ref"],
            "checkpoint_id": item["checkpoint_id"],
            "workflow_run_id": item["workflow_run_id"],
            "fanout_id": item["fanout_id"],
            "source_event_id": item["source_event_id"],
            "proposal_ref": item["proposal_ref"],
            "eval_ref": item["eval_ref"],
            "candidate_task_map_ref": item.get("candidate_task_map_ref", ""),
            "reason": "Owner approves the bounded fixture.",
            "owner": "owner:min",
        },
    )
    assert result["status"] == "approved"

    orch.run_once(events=[])

    events = log.read_all()
    assert any(event.type == "approval.resolved" for event in events)
    resolved = next(
        event for event in events
        if event.type == "plan.synth.owner_decision.resolved"
    )
    assert resolved.payload["decision"] == "approved"
    aggregate = next(
        event for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    )
    assert aggregate.payload["status"] == "failed"
    blocked = next(event for event in events if event.type == "task_map.blocked")
    assert blocked.payload["owner_decision_resolution"] == "approved"
    confirmation_ref = blocked.payload["owner_confirmation_ref"]
    assert confirmation_ref
    assert (state_dir / confirmation_ref).exists()

    orch.run_once(events=[blocked])

    replan = [
        event for event in log.read_all()
        if event.type == "product.design.ready"
        and event.causation_id == blocked.id
    ]
    assert len(replan) == 1
    assert replan[0].correlation_id == "trace-product"
    assert replan[0].payload["owner_confirmation_ref"] == confirmation_ref


def test_task_map_synth_relocates_workdir_relative_artifacts(tmp_path: Path):
    state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    workdir_docs = state_dir / "workdirs" / "task-map-synth" / "project" / "docs" / "plans"
    workdir_docs.mkdir(parents=True)
    (workdir_docs / "task_map.json").write_text(
        json.dumps({
            "feature_id": "PDD-1",
            "tasks": [{
                "task_id": "PDD-1-T1",
                "title": "Do the work",
                "owner_role": "dev",
                "allowed_paths": ["src/app.js"],
            }],
        }) + "\n",
        encoding="utf-8",
    )
    (workdir_docs / "source_index.json").write_text(
        json.dumps({
            "tasks": [{
                "task_id": "PDD-1-T1",
                "source_refs": ["src/app.js:1"],
            }],
        }) + "\n",
        encoding="utf-8",
    )
    (workdir_docs / "plan.md").write_text("# Plan\n", encoding="utf-8")

    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "task map ready",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "task map ready",
                "findings": [],
                "recommendation": "approve",
                "pdd_id": "PDD-1",
                "feature_id": "PDD-1",
                "plan_artifact_ref": "docs/plans/plan.md",
                "task_map_ref": "docs/plans/task_map.json",
                "source_index_ref": "docs/plans/source_index.json",
                "artifact_refs": [
                    "docs/plans/plan.md",
                    "docs/plans/task_map.json",
                    "events:evt-source",
                ],
                "evidence_refs": [
                    "docs/plans/source_index.json",
                    "command:npm test#exit=0",
                ],
            },
        },
    ))

    orch.run_once(events=[synth_event])

    ready = next(event for event in log.read_all()
                 if event.type == "task_map.ready")
    assert ready.payload["task_map_ref"].startswith(
        f"artifacts/fanouts/{fanout_id}/task-map-synth/"
    )
    assert ready.payload["source_index_ref"].startswith(
        f"artifacts/fanouts/{fanout_id}/task-map-synth/"
    )
    assert ready.payload["plan_artifact_ref"].startswith(
        f"artifacts/fanouts/{fanout_id}/task-map-synth/"
    )
    assert (state_dir / ready.payload["task_map_ref"]).exists()
    assert (state_dir / ready.payload["source_index_ref"]).exists()
    assert (state_dir / ready.payload["plan_artifact_ref"]).exists()
    assert "events:evt-source" in ready.payload["artifact_refs"]
    assert "command:npm test#exit=0" in ready.payload["evidence_refs"]
    assert "docs/plans/task_map.json" not in ready.payload["artifact_refs"]


def test_task_map_synth_relocates_workdir_state_artifacts_without_zf_duplication(
    tmp_path: Path,
):
    state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    workdir_artifacts = (
        state_dir
        / "workdirs"
        / "task-map-synth"
        / "project"
        / ".zf"
        / "artifacts"
        / fanout_id
        / "task-map-synth"
    )
    workdir_artifacts.mkdir(parents=True)
    (workdir_artifacts / "task_map.json").write_text(
        json.dumps({
            "feature_id": "PDD-1",
            "tasks": [{
                "task_id": "PDD-1-T1",
                "title": "Do the work",
                "owner_role": "dev",
                "allowed_paths": ["src/app.js"],
            }],
        }) + "\n",
        encoding="utf-8",
    )

    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "task map ready",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "task map ready",
                "findings": [],
                "recommendation": "approve",
                "pdd_id": "PDD-1",
                "feature_id": "PDD-1",
                "task_map_ref": (
                    f".zf/artifacts/{fanout_id}/task-map-synth/task_map.json"
                ),
            },
        },
    ))

    orch.run_once(events=[synth_event])

    ready = next(event for event in log.read_all()
                 if event.type == "task_map.ready")
    assert ready.payload["task_map_ref"] == (
        f"artifacts/fanouts/{fanout_id}/task-map-synth/"
        f"artifacts/{fanout_id}/task-map-synth/task_map.json"
    )
    assert "/zf/artifacts/" not in ready.payload["task_map_ref"]
    assert (state_dir / ready.payload["task_map_ref"]).exists()


def test_task_map_ready_missing_ref_fails_contract_gate(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="task-map-synth",
        correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "product-task-map",
            "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "markdown-only plan",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "markdown-only plan",
                "findings": [],
                "recommendation": "approve",
            },
        },
    ))

    orch.run_once(events=[synth_event])

    events = log.read_all()
    assert not any(event.type == "task_map.ready" for event in events)
    blocked = next(event for event in events
                   if event.type == "task_map.blocked")
    assert blocked.payload["contract_gate"] == "failed"
    assert blocked.payload["reason"] == "task_map.ready requires task_map_ref"
    aggregate = next(event for event in events
                     if event.type == "fanout.aggregate.completed"
                     and event.payload.get("fanout_id") == fanout_id)
    assert aggregate.payload["status"] == "failed"


def _prd_author_orchestrator(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="prd-author", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-authoring",
                trigger="user.message",
                topology="fanout_reader",
                roles=["prd-author"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="prd.author.completed",
                    child_failure_event="prd.author.failed",
                    success_event="prd.ready",
                    failure_event="prd.blocked",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    orch.run_once(events=[ZfEvent(
        type="user.message",
        actor="zf-cli",
        correlation_id="trace-prd",
        payload={"pdd_id": "PDD-1", "feature_id": "PDD-1", "text": "build prd"},
    )])
    fanout_id = next(
        event.payload["fanout_id"] for event in log.read_all()
        if event.type == "fanout.started"
    )
    return state_dir, log, orch, fanout_id


def test_prd_ready_missing_structured_refs_fails_contract_gate(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _prd_author_orchestrator(tmp_path)
    orch.run_once(events=[ZfEvent(
        type="prd.author.completed",
        actor="prd-author",
        correlation_id="trace-prd",
        payload={
            "fanout_id": fanout_id,
            "child_id": "prd-author",
            "run_id": f"run-{fanout_id}-prd-author",
            "status": "completed",
            "summary": "transcript-only prd",
        },
    )])

    events = log.read_all()
    assert not any(event.type == "prd.ready" for event in events)
    blocked = next(event for event in events if event.type == "prd.blocked")
    assert blocked.payload["contract_gate"] == "failed"
    assert blocked.payload["reason"] == "prd.ready requires prd_ref"
    aggregate = next(event for event in events
                     if event.type == "fanout.aggregate.completed"
                     and event.payload.get("fanout_id") == fanout_id)
    assert aggregate.payload["status"] == "failed"


def test_prd_ready_requires_evidence_refs(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _prd_author_orchestrator(tmp_path)
    orch.run_once(events=[ZfEvent(
        type="prd.author.completed",
        actor="prd-author",
        correlation_id="trace-prd",
        payload={
            "fanout_id": fanout_id,
            "child_id": "prd-author",
            "run_id": f"run-{fanout_id}-prd-author",
            "status": "completed",
            "summary": "prd without evidence refs",
            "report": {
                "prd_ref": "docs/prds/PDD-1.md",
                "artifact_refs": ["docs/prds/PDD-1.md"],
            },
        },
    )])

    events = log.read_all()
    assert not any(event.type == "prd.ready" for event in events)
    blocked = next(event for event in events if event.type == "prd.blocked")
    assert blocked.payload["contract_gate"] == "failed"
    assert blocked.payload["reason"] == "prd.ready requires evidence_refs"


def test_prd_ready_preserves_structured_refs(tmp_path: Path):
    _state_dir, log, orch, fanout_id = _prd_author_orchestrator(tmp_path)
    orch.run_once(events=[ZfEvent(
        type="prd.author.completed",
        actor="prd-author",
        correlation_id="trace-prd",
        payload={
            "fanout_id": fanout_id,
            "child_id": "prd-author",
            "run_id": f"run-{fanout_id}-prd-author",
            "status": "completed",
            "summary": "prd ready",
            "report": {
                "prd_ref": "docs/prds/PDD-1.md",
                "artifact_refs": ["docs/prds/PDD-1.md"],
                "evidence_refs": ["channels/ch-zaofu/messages/1"],
            },
        },
    )])

    ready = next(event for event in log.read_all() if event.type == "prd.ready")
    assert ready.payload["prd_ref"] == "docs/prds/PDD-1.md"
    assert "docs/prds/PDD-1.md" in ready.payload["artifact_refs"]
    assert ready.payload["evidence_refs"] == ["channels/ch-zaofu/messages/1"]


def test_plan_fanout_child_briefing_requires_durable_plan_artifact(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    role = RoleConfig(
        name="product-arch",
        backend="mock",
        role_kind="reader",
        stages=["plan"],
    )
    context = FanoutContext(
        fanout_id="fanout-product-plan",
        stage_id="product-plan-authoring",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="HEAD",
    )

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=role,
        context=context,
        child_id="product-arch",
        run_id="run-1",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="arch.proposal.done",
            child_failure_event="clarification.needed",
            success_event="product.plan.ready",
            failure_event="product.plan.blocked",
        ),
    )

    briefing = path.read_text(encoding="utf-8")
    assert "Plan stages must produce a durable markdown plan artifact" in briefing
    assert "plan_artifact_ref" in briefing
    assert "top-level `task_map.target_commit`" in briefing
    assert "`static`, `runtime`, `e2e`, or `manual_evidence`" in briefing
    assert "rather than inventing a `setup` tier" in briefing
    assert "`rolling_smoke: true`" in briefing
    assert "never a tier" in briefing
    assert "docs/plans/product-plan-authoring-product-arch-plan.md" in briefing


def test_task_map_briefing_uses_workdir_relative_relocatable_ref(tmp_path: Path):
    state_dir = tmp_path / ".zf-custom"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    role = RoleConfig(name="planner", backend="mock", role_kind="reader")
    context = FanoutContext(
        fanout_id="fanout-prd-plan",
        stage_id="prd-plan",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="docs/prd.md",
    )

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=role,
        context=context,
        child_id="planner",
        run_id="run-1",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="prd.plan.child.completed",
            child_failure_event="prd.plan.child.failed",
            success_event="task_map.ready",
            failure_event="prd.plan.failed",
        ),
    )

    briefing = path.read_text(encoding="utf-8")
    assert "workdir-relative path `artifacts/plan/task_map.json`" in briefing
    assert '"task_map_ref": "artifacts/plan/task_map.json"' in briefing
    assert "the kernel relocates it into runtime artifact storage" in briefing
    assert "EXACT absolute path" not in briefing
    assert ".zf/artifacts/default/task_map.json" not in briefing


def test_plan_reader_operation_pins_artifact_scope_and_result_semantics(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf-plan-operation"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    role = RoleConfig(
        name="issue-triage",
        backend="mock",
        role_kind="reader",
    )
    config = ZfConfig(
        project=ProjectConfig(name="issue-test", state_dir=str(state_dir)),
        roles=[role],
        workflow=WorkflowConfig(
            flow_metadata={"result_protocol": {"mode": "blocking"}},
            stages=[WorkflowStageConfig(
                id="issue-triage",
                attempt_domain="plan",
                trigger="issue.requested",
                topology="fanout_reader",
                result_semantics="artifact_production",
                roles=["issue-triage"],
                target_ref="docs/issues/bug.md",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="issue.triage.child.completed",
                    child_failure_event="issue.triage.child.failed",
                    success_event="task_map.ready",
                    failure_event="issue.triage.failed",
                ),
            )],
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),
    )  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="issue.requested",
        actor="zf-cli",
        correlation_id="run-issue",
        payload={"workflow_run_id": "run-issue"},
    )])

    requested = next(
        event
        for event in log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request_body = hydrate_sidecar_ref(
        state_dir,
        requested.payload["request_ref"],
    ).payload
    operation_request = request_body["request"]
    assert operation_request["workdir_write_scopes"] == [
        "docs/plans/**",
        "artifacts/plan/**",
    ]
    assert operation_request["result_identity"]["result_semantics"] == (
        "artifact_production"
    )
    validation = operation_request["plan_candidate_validation"]
    assert validation["schema_version"] == (
        "plan-candidate-validation-context.v1"
    )
    assert validation["manifest"]["stage_id"] == "issue-triage"
    assert validation["manifest"]["fanout_id"]


def test_scan_reader_operation_does_not_receive_plan_candidate_validation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf-scan-operation"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    role = RoleConfig(name="product-scan", backend="mock", role_kind="reader")
    config = ZfConfig(
        project=ProjectConfig(name="prd-scan-test", state_dir=str(state_dir)),
        roles=[role],
        workflow=WorkflowConfig(
            flow_metadata={"result_protocol": {"mode": "blocking"}},
            stages=[WorkflowStageConfig(
                id="prd-scan",
                trigger="prd.requested",
                topology="fanout_reader",
                result_semantics="artifact_production",
                roles=["product-scan"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="prd.scan.child.completed",
                    child_failure_event="prd.scan.child.failed",
                    success_event="prd.scan.completed",
                    failure_event="prd.scan.failed",
                ),
            )],
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),
    )  # type: ignore[arg-type]

    orch.run_once(events=[ZfEvent(
        type="prd.requested",
        actor="zf-cli",
        correlation_id="run-prd-scan",
        payload={"workflow_run_id": "run-prd-scan"},
    )])

    requested = next(
        event for event in log.read_all()
        if event.type == "workflow.operation.requested"
    )
    operation_request = hydrate_sidecar_ref(
        state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    assert "plan_candidate_validation" not in operation_request
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    )
    briefing = Path(dispatched.payload["briefing_path"]).read_text(
        encoding="utf-8"
    )
    assert "result validate" not in briefing


def test_issue_triage_briefing_is_a_plan_port_producer(tmp_path: Path):
    state_dir = tmp_path / ".zf-issue"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    role = RoleConfig(
        name="issue-triage",
        backend="mock",
        role_kind="reader",
        execution=ExecutionConfig(
            default_profile="bounded-direct-v1",
            profile_allowlist=["bounded-direct-v1"],
        ),
    )
    orch.config = ZfConfig(
        project=ProjectConfig(name="issue-test"),
        roles=[role],
        workflow=WorkflowConfig(execution_profiles={
            "bounded-direct-v1": ExecutionProfileConfig(
                limits=ExecutionProfileLimitsConfig(
                    max_children=0,
                    max_depth=0,
                    timeout_seconds=900,
                    max_usage_samples=30,
                    token_budget=1_500_000,
                    cost_budget_usd=8,
                ),
            ),
        }),
    )
    context = FanoutContext(
        fanout_id="fanout-issue-triage",
        stage_id="issue-triage",
        topology="fanout_reader",
        trace_id="issue-run",
        trigger_event_id="evt-issue",
        target_ref="docs/issues/docker.md",
    )

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=role,
        context=context,
        child_id="issue-triage",
        run_id="run-issue-triage",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="issue.triage.child.completed",
            child_failure_event="issue.triage.child.failed",
            success_event="task_map.ready",
            failure_event="issue.triage.failed",
        ),
        child_payload={
            "task_id": "TASK-ISSUE",
            "execution_profile_id": "bounded-direct-v1",
            "semantic_result_submit_mode": "blocking",
            "output_profile_id": "workflow-read",
            "output_profile_revision": "1",
            "operation_id": "wop-issue-triage",
            "attempt_id": "attempt-issue-triage",
            "flow_kind": "issue",
            "trigger_payload": {"flow_kind": "issue"},
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert briefing.startswith(
        "Active task: TASK-ISSUE\n\n# Fanout Reader Child: issue-triage"
    )
    assert "## Execution Boundary (kernel-pinned)" in briefing
    assert "profile_id: `bounded-direct-v1`" in briefing
    assert "max_children=`0`" in briefing
    assert "max_usage_samples=`30`" in briefing
    assert "Reserve the final two samples" in briefing
    assert "Do not perform external web research" in briefing
    assert "Do not spawn or delegate to any provider-native sub-agent" in briefing
    assert "## Recursion Guard (强制)" in briefing
    assert "## Child Result Authority" in briefing
    assert "Do not run `zf guard ownership` for that Task" in briefing
    assert "Do not inspect ZaoFu runtime source" in briefing
    assert "do not reverse-engineer the harness" in briefing
    assert "With apply_patch use `*** Update File`" in briefing
    assert "never delete, add, move, or recreate the scratch path" in briefing
    assert "the next tool call MUST be the Success command" in briefing
    assert "Do not run a post-authoring audit" in briefing
    result_template = json.loads(
        (
            state_dir / "tmp/result-submit/wop-issue-triage"
            / "attempt-issue-triage/result.json"
        ).read_text(encoding="utf-8")
    )
    assert result_template["task_map_ref"] == "artifacts/plan/task_map.json"
    assert result_template["plan_ports"] == []
    assert "This remains task-map synthesis" not in briefing
    assert "regression_test_matrix` is a delivery alias" in briefing
    assert "Test Matrix has exactly one command registry" in briefing
    assert "test_matrix.commands[]" in briefing
    assert "`exclusive_files` has exactly one owner" in briefing
    assert "Prefer exactly one Task Map producer: `task_map_ref`" in briefing
    assert "Do not duplicate the full Task Map inline" in briefing
    assert "Do not manually diff or print Task Map producers" in briefing
    assert "task_map_producer_mismatch" in briefing
    assert "do not modify product implementation files" in briefing
    role_instructions = (
        state_dir / "instructions" / "issue-triage.md"
    ).read_text(encoding="utf-8")
    assert "## Current Task" not in role_instructions
    assert "guard ownership" not in role_instructions
    assert str(state_dir / "artifacts" / "task_map.json") not in briefing


def test_refactor_plan_briefing_prefills_plan_ports_once(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf-refactor"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    role = RoleConfig(
        name="refactor-plan-synth",
        backend="mock",
        role_kind="reader",
    )
    context = FanoutContext(
        fanout_id="fanout-refactor-plan",
        stage_id="flow-plan",
        topology="fanout_reader",
        trace_id="refactor-run",
        trigger_event_id="evt-refactor",
        target_ref="",
    )

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=role,
        context=context,
        child_id="refactor-plan-synth",
        run_id="run-refactor-plan",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="workflow.child.completed",
            child_failure_event="workflow.child.failed",
            success_event="zaofu.refactor.plan.ready",
            failure_event="zaofu.refactor.plan.blocked",
        ),
        child_payload={
            "flow_kind": "refactor",
            "trigger_payload": {
                "review_artifact_ref": "artifacts/review.md",
            },
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert briefing.count('"plan_ports": []') == 1
    assert "inline plan_ports required by this briefing" in briefing
    assert "`plan_ports` MUST be a JSON array of descriptor objects" in briefing
    assert "do not duplicate the matrix bodies inside `report`" in briefing
    assert '"plan_artifact_ref": "docs/plans/fanout-refactor-plan.md"' in briefing
    assert (
        '"task_map_ref": "artifacts/fanout-refactor-plan/task_map.json"'
        in briefing
    )
    assert "workdir-relative paths" in briefing
    assert "Never write the configured state dir or root project directly" in briefing
    assert "Kernel relocates admitted refs" in briefing
    assert str(state_dir / "artifacts" / "fanout-refactor-plan") not in briefing


def test_refactor_plan_semantic_submit_forbids_post_authoring_audit(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf-refactor"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    role = RoleConfig(
        name="refactor-plan-synth",
        backend="mock",
        role_kind="reader",
    )

    path = orch._write_fanout_briefing(  # type: ignore[attr-defined]
        role=role,
        context=FanoutContext(
            fanout_id="fanout-refactor-plan",
            stage_id="flow-plan",
            topology="fanout_reader",
            trace_id="refactor-run",
            trigger_event_id="evt-refactor",
            target_ref="",
        ),
        child_id="refactor-plan-synth",
        run_id="run-refactor-plan",
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="workflow.child.completed",
            child_failure_event="workflow.child.failed",
            success_event="zaofu.refactor.plan.ready",
            failure_event="zaofu.refactor.plan.blocked",
        ),
        child_payload={
            "task_id": "TASK-REFACTOR",
            "semantic_result_submit_mode": "blocking",
            "output_profile_id": "workflow-read",
            "output_profile_revision": "1",
            "operation_id": "wop-refactor-plan",
            "attempt_id": "attempt-refactor-plan",
            "flow_kind": "refactor",
            "trigger_payload": {
                "review_artifact_ref": "artifacts/review.md",
            },
        },
    )

    briefing = path.read_text(encoding="utf-8")
    assert "the next tool call MUST be the Success command" in briefing
    assert "Do not run a post-authoring audit" in briefing
    assert "`result submission` performs deterministic schema/admission validation" in briefing


def test_generic_fanout_synth_empty_ports_preserve_child_handoff(
    tmp_path: Path,
) -> None:
    _state_dir, _log, _transport, orch = _state(tmp_path)
    child_ports = [{
        "logical_name": "acceptance_matrix",
        "schema_version": "acceptance-matrix.v1",
        "body": {"schema_version": "acceptance-matrix.v1", "status": "ready"},
    }]
    orch._fanout_child_payloads = lambda _manifest: [  # type: ignore[method-assign]
        {"child_id": "planner", "plan_ports": child_ports}
    ]

    projected = orch._generic_fanout_success_payload(
        manifest={
            "fanout_id": "fanout-plan",
            "trace_id": "workflow-1",
            "stage_id": "plan",
            "children": [{"child_id": "planner"}],
        },
        success_event="task_map.ready",
        extra_payloads=[{"child_id": "synth", "plan_ports": []}],
    )

    assert projected["plan_ports"] == child_ports


def test_refactor_plan_critic_submits_only_verdict_delta(tmp_path: Path):
    state_dir = tmp_path / ".zf-refactor-critic"
    state_dir.mkdir()
    planner = RoleConfig(
        name="refactor-plan-synth",
        backend="mock",
        role_kind="reader",
    )
    critic = RoleConfig(
        name="plan-critic",
        backend="mock",
        role_kind="reader",
    )
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[planner, critic],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="flow-plan",
                trigger="zaofu.refactor.review.ready",
                topology="fanout_reader",
                roles=["refactor-plan-synth"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="workflow.child.completed",
                    child_failure_event="workflow.child.failed",
                    success_event="zaofu.refactor.plan.ready",
                    failure_event="zaofu.refactor.plan.blocked",
                    synth_role="plan-critic",
                ),
            ),
        ]),
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    orch.config = config

    scratch_ref = "tmp/result-submit/op-plan/synth/result.json"
    path = orch._write_fanout_synth_briefing(  # type: ignore[attr-defined]
        role=critic,
        manifest={
            "fanout_id": "fanout-refactor-plan",
            "stage_id": "flow-plan",
            "target_ref": "",
            "trigger_payload": {
                "flow_kind": "refactor",
                "review_artifact_ref": "artifacts/review.md",
            },
            "aggregate_config": {
                "success_event": "zaofu.refactor.plan.ready",
                "failure_event": "zaofu.refactor.plan.blocked",
                "synth_role": "plan-critic",
            },
            "children": [],
        },
        run_id="run-synth",
        call_payload={
            "workflow_run_id": "refactor-run",
            "operation_id": "op-plan",
            "request_hash": "hash-plan",
            "attempt_id": "run-synth",
            "result_protocol_mode": "blocking",
            "output_profile_id": "plan-synth",
            "output_profile_revision": "1",
            "result_scratch_ref": scratch_ref,
            "plan_revision": "plan-r1",
            "plan_synth_contract_ref": "artifacts/plan-contract.json",
            "plan_synth_contract_digest": "digest-plan",
            "reviewed_plan_candidate_digest": "digest-plan",
        },
        reports=[{
            "child_id": "refactor-plan-synth",
            "report_path": "artifacts/planner-result.json",
            "report": {
                "plan_artifact_ref": "artifacts/planner/refactor-plan.md",
                "task_map_ref": "artifacts/planner/task_map.json",
                "task_map": {"schema_version": "task-map.v1", "tasks": [{"task_id": "T1"}]},
                "plan_ports": [{"logical_name": "task_map", "body": {"tasks": []}}],
            },
        }],
    )

    briefing = path.read_text(encoding="utf-8")
    result = json.loads((state_dir / scratch_ref).read_text(encoding="utf-8"))
    assert "## Plan Critic Contract" in briefing
    assert "does not author or rewrite plan artifacts" in briefing
    assert "deterministic pre-submit validation" in briefing
    assert "Treat mechanical schema and required-port presence as admitted facts" in briefing
    assert "`goal_claim_set` and `planning_result` are Kernel-derived" in briefing
    assert "issue flows adapt `requirement_spec` to `issue_spec`" in briefing
    assert "Edit only the signed result scratch file" in briefing
    assert f"Edit `{(state_dir / scratch_ref).resolve()}`" in briefing
    assert "Use native Edit/Write/apply_patch" in briefing
    assert "not shell redirection or Python/shell heredocs" in briefing
    for body in (result, result["report"]):
        assert body["status"] == ""
        assert body["recommendation"] == ""
        assert body["summary"] == ""
    assert "non-empty `message`" in briefing
    assert "`info`, `low`, `medium`, `high`, or `critical`" in briefing
    assert "never invent `residual-risk`" in briefing
    assert briefing.count("result validate") == 1
    assert briefing.count("result submit") == 1
    assert briefing.index("result validate") < briefing.index("result submit")
    assert "does not consume the submit binding" in briefing
    assert "## Plan Artifact Contract" not in briefing
    assert result["artifact_refs"] == []
    assert result["fix_items"] == []
    assert "task_map" not in result
    assert str(state_dir / "artifacts" / "fanout-refactor-plan") not in briefing


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("ledger_bound", [False, True])
def test_refactor_plan_critic_large_package_is_ref_digest_only(
    tmp_path: Path, admitted: bool, ledger_bound: bool,
) -> None:
    state_dir = tmp_path / ".zf-refactor-critic-large"
    state_dir.mkdir()
    critic = RoleConfig(
        name="plan-critic",
        backend="mock",
        role_kind="reader",
    )
    planner_body = {
        "schema_version": "fanout-child-result.v1",
        "task_map": {
            "tasks": [{
                "task_id": "T1",
                "description": "LARGE-PLAN-SENTINEL-" + ("x" * 150_000),
            }],
        },
    }
    report_path = state_dir / "artifacts/planner-result.json"
    report_path.parent.mkdir()
    report_path.write_text(json.dumps(planner_body), encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[critic],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="flow-plan",
            trigger="zaofu.refactor.review.ready",
            topology="fanout_reader",
            roles=["plan-critic"],
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="workflow.child.completed",
                child_failure_event="workflow.child.failed",
                success_event="zaofu.refactor.plan.ready",
                failure_event="zaofu.refactor.plan.blocked",
                synth_role="plan-critic",
            ),
        )]),
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.project_root = tmp_path
    orch.config = config
    reports = [{
        "child_id": "refactor-plan-synth",
        "report_path": str(report_path),
        "report": planner_body,
    }]
    if admitted:
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar

        control = {"verdict": "passed", "payload": planner_body}
        control_ref = write_immutable_json_sidecar(
            state_dir, control, root="control-results", kind="control_result",
            schema_version="fanout-child-result.v1", created_by="test",
        )
        envelope_ref = write_immutable_json_sidecar(
            state_dir, {"control_result_ref": control_ref}, root="call-results",
            kind="agent_call_result", schema_version="agent-call-result.v1",
            created_by="test",
        )
        reports[0].update({
            "control_result": control, "control_result_ref": control_ref,
            "admitted_call_result_ref": envelope_ref,
        })
    from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload

    call_payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=tmp_path,
        manifest={
            "fanout_id": "fanout-large-plan",
            "trace_id": "refactor-run-large",
            "stage_id": "flow-plan",
            "trigger_event_id": "evt-large-plan",
            "trigger_payload": {"flow_kind": "refactor"},
            "aggregate_config": {
                "success_event": "zaofu.refactor.plan.ready",
            },
        },
        reports=reports,
        run_id="run-synth-large",
        role_instance="plan-critic",
    )
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload

    manifest, descriptor = source_manifest_from_payload(
        state_dir=state_dir,
        project_root=tmp_path,
        payload=call_payload,
        workflow_run_id="refactor-run-large",
        task_id="",
        attempt_id="run-synth-large",
        dispatch_id="run-synth-large",
        source_event_id="evt-large-plan",
    )
    from zf.runtime.artifact_read_ledger import canonical_required_reads

    call_payload.update({
        "attempt_source_manifest_ref": descriptor["ref"],
        "attempt_source_manifest_digest": descriptor["sha256"],
        "attempt_source_manifest": descriptor,
        "required_reads": canonical_required_reads(
            manifest,
            output_profile_id="plan-synth",
        ),
        "operation_id": "op-large-plan",
        "attempt_id": "run-synth-large",
        "result_scratch_ref": (
            "tmp/result-submit/op-large-plan/run-synth-large/result.json"
        ),
    })

    path = orch._write_fanout_synth_briefing(  # type: ignore[attr-defined]
        role=critic,
        manifest={
            "fanout_id": "fanout-large-plan",
            "stage_id": "flow-plan",
            "trigger_payload": {"flow_kind": "refactor"},
            "aggregate_config": {
                "success_event": "zaofu.refactor.plan.ready",
            },
            "children": [],
        },
        run_id="run-synth-large",
        call_payload=call_payload if ledger_bound else {},
        reports=reports,
    )

    briefing = path.read_text(encoding="utf-8")
    metrics = json.loads(
        path.with_suffix(".md.metrics.json").read_text(encoding="utf-8")
    )
    if not ledger_bound:
        assert "LARGE-PLAN-SENTINEL" in briefing
        return
    assert "LARGE-PLAN-SENTINEL" not in briefing
    assert "Child report bodies are canonical required inputs" in briefing
    assert "artifact read --attempt run-synth-large" in briefing
    assert len(briefing.encode("utf-8")) < 24 * 1024
    assert metrics["briefing_bytes"] < 24 * 1024
    assert metrics["required_read_count"] >= 2
    if admitted:
        assert envelope_ref["sha256"] in briefing
        assert json.loads((state_dir / control_ref["ref"]).read_text()) == control
        assert any(source["source_id"].endswith("-control-result")
                   and source["sha256"] == control_ref["sha256"]
                   for source in manifest["sources"])
        assert any(read["source_id"] == "child-result-refactor-plan-synth"
                   for read in call_payload["required_reads"])


def test_issue_triage_retry_restores_lineage_and_plan_contract(tmp_path: Path):
    state_dir = tmp_path / ".zf-issue"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    rich_trigger = ZfEvent(
        id="evt-rich-trigger",
        type="task.fanout.requested",
        correlation_id="issue-run",
        payload={
            "workflow_run_id": "issue-run",
            "workflow_input_manifest_ref": "artifacts/workflow/input.json",
            "source_refs": {
                "acceptance_matrix_ref": "artifacts/workflow/acceptance.json",
                "test_matrix_ref": "artifacts/workflow/test.json",
            },
            "flow_kind": "issue",
        },
    )
    log.append(rich_trigger)
    log.append(ZfEvent(
        type="fanout.started",
        correlation_id="issue-run",
        payload={
            "fanout_id": "fanout-original",
            "stage_id": "issue-triage",
            "trace_id": "issue-run",
            "trigger_event_id": rich_trigger.id,
        },
    ))
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.event_log = log
    role = RoleConfig(
        name="issue-triage",
        backend="mock",
        role_kind="reader",
    )

    path = orch._write_fanout_retry_briefing(  # type: ignore[attr-defined]
        role=role,
        manifest={
            "fanout_id": "fanout-replan",
            "stage_id": "issue-triage",
            "trace_id": "issue-run",
            "target_ref": "docs/issues/docker.md",
            "topology": "fanout_reader",
            "aggregate_config": {
                "success_event": "task_map.ready",
                "failure_event": "issue.triage.failed",
                "child_success_event": "issue.triage.child.completed",
                "child_failure_event": "issue.triage.child.failed",
            },
        },
        child={
            "child_id": "issue-triage",
            "payload": {
                "flow_kind": "issue",
                "trigger_payload": {"rework_attempt": 2},
            },
        },
        run_id="run-retry",
    )

    briefing = path.read_text(encoding="utf-8")
    assert "artifacts/workflow/input.json" in briefing
    assert "artifacts/workflow/test.json" in briefing
    assert '"task_map_ref": "artifacts/plan/task_map.json"' in briefing
    assert '"plan_ports": []' in briefing
    assert "This remains task-map synthesis, not findings-only triage" in briefing
    assert "## Child Result Authority" in briefing
    assert "Do not run `zf guard ownership` for that Task" in briefing


def test_blocking_reader_retry_preserves_typed_result_submit_contract(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(
        tmp_path,
        child_success_event="verify.child.completed",
        child_failure_event="verify.child.failed",
    )
    role = next(
        item for item in orch.config.roles if item.instance_id == "review-a"
    )
    scratch_ref = "tmp/result-submit/op-review/attempt-1/result.json"

    path = orch._write_fanout_retry_briefing(  # type: ignore[attr-defined]
        role=role,
        manifest={
            "fanout_id": "fanout-review-retry",
            "stage_id": "review-candidate",
            "trace_id": "trace-1",
            "trigger_event_id": "evt-candidate",
            "target_ref": "candidate/F-11111111",
            "topology": "fanout_reader",
            "aggregate_config": {
                "success_event": "review.approved",
                "failure_event": "review.rejected",
                "child_success_event": "verify.child.completed",
                "child_failure_event": "verify.child.failed",
            },
        },
        child={
            "child_id": "review-a",
            "role_instance": "review-a",
            "payload": {
                "fanout_id": "fanout-review-retry",
                "stage_id": "review-candidate",
                "child_id": "review-a",
                "workflow_run_id": "trace-1",
                "operation_id": "op-review",
                "request_hash": "request-review",
                "attempt_id": "attempt-1",
                "result_protocol_mode": "blocking",
                "semantic_result_submit_mode": "blocking",
                "output_profile_id": "candidate-verify",
                "output_profile_revision": "1",
                "result_scratch_ref": scratch_ref,
            },
        },
        run_id="run-fanout-review-retry-review-a-retry-1",
    )

    briefing = path.read_text(encoding="utf-8")
    assert "result submit --operation op-review" in briefing
    assert "emit verify.child.completed" not in briefing
    assert (state_dir / scratch_ref).is_file()


def test_generic_plan_aggregate_preserves_report_plan_artifact_ref(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    result_dir = state_dir / "fanouts" / "fanout-product-plan" / "children" / "product-arch"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({
            "payload": {
                "fanout_id": "fanout-product-plan",
                "child_id": "product-arch",
                "status": "completed",
                "task_map_ref": "artifacts/plans/task-map.json",
                "plan_artifact_package_id": "planpkg-current",
                "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
                "plan_artifact_package_digest": "a" * 64,
                "goal_claim_set_ref": "artifacts/goal-claims/current.json",
                "goal_claim_set_digest": "b" * 64,
                "report": {
                    "plan_artifact_ref": "docs/plans/product-plan.md",
                    "artifact_refs": ["docs/plans/product-plan.md"],
                    "evidence_refs": ["docs/plans/source-index.json"],
                    "source_index_ref": "docs/plans/source-index.json",
                },
            },
        }),
        encoding="utf-8",
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir

    payload = orch._generic_fanout_success_payload(  # type: ignore[attr-defined]
        manifest={
            "fanout_id": "fanout-product-plan",
            "children": [{"child_id": "product-arch"}],
        },
        success_event="product.plan.ready",
    )

    assert payload["plan_artifact_ref"] == "docs/plans/product-plan.md"
    assert payload["source_index_ref"] == "docs/plans/source-index.json"
    assert payload["task_map_ref"] == "artifacts/plans/task-map.json"
    assert payload["plan_artifact_package_id"] == "planpkg-current"
    assert (
        payload["plan_artifact_package_ref"]
        == "artifacts/plan-packages/current.json"
    )
    assert payload["plan_artifact_package_digest"] == "a" * 64
    assert payload["goal_claim_set_ref"] == "artifacts/goal-claims/current.json"
    assert payload["goal_claim_set_digest"] == "b" * 64
    assert payload["artifact_refs"] == [
        "docs/plans/product-plan.md",
        "artifacts/plans/task-map.json",
        "artifacts/plan-packages/current.json",
        "artifacts/goal-claims/current.json",
    ]
    assert payload["evidence_refs"] == [
        "docs/plans/source-index.json",
        "artifacts/plans/task-map.json",
        "artifacts/plan-packages/current.json",
        "artifacts/goal-claims/current.json",
    ]


def test_generic_aggregate_reads_lineage_from_manifest_child_payload(
    tmp_path: Path,
) -> None:
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = tmp_path / ".zf"

    payload = orch._generic_fanout_success_payload(  # type: ignore[attr-defined]
        manifest={
            "fanout_id": "fanout-verify-lineage",
            "trigger_payload": {"flow_kind": "workflow"},
            "children": [
                {
                    "child_id": "verify-1",
                    "admitted_call_result_ref": {
                        "ref": (
                            "artifacts/call-results/envelopes/"
                            + "c" * 64
                            + ".json"
                        ),
                        "sha256": "c" * 64,
                    },
                    "payload": {
                        "task_map_generation": "generation-current",
                        "plan_artifact_package_id": "planpkg-current",
                        "plan_artifact_package_ref": (
                            "artifacts/plan-packages/current.json"
                        ),
                        "plan_artifact_package_digest": "a" * 64,
                        "goal_claim_set_ref": (
                            "artifacts/goal-claims/current.json"
                        ),
                        "goal_claim_set_digest": "b" * 64,
                        "input_result_refs": [
                            "fanouts/fanout-verify-lineage/children/"
                            "verify-1/result.json",
                            "artifacts/attempt-source-manifests/raw.json",
                        ],
                        "trigger_payload": {
                            "task_map_ref": "artifacts/plans/task-map.json",
                        },
                    },
                },
                {
                    "child_id": "verify-2",
                    "admitted_call_result_ref": {
                        "ref": (
                            "artifacts/call-results/envelopes/"
                            + "d" * 64
                            + ".json"
                        ),
                        "sha256": "d" * 64,
                    },
                },
                {
                    "child_id": "verify-duplicate",
                    "admitted_call_result_ref": {
                        "ref": (
                            "artifacts/call-results/envelopes/"
                            + "c" * 64
                            + ".json"
                        ),
                        "sha256": "c" * 64,
                    },
                },
                {
                    "child_id": "verify-raw-result",
                    "admitted_call_result_ref": {
                        "ref": (
                            "fanouts/fanout-verify-lineage/children/"
                            "verify-raw-result/result.json"
                        ),
                        "sha256": "f" * 64,
                    },
                },
                {
                    "child_id": "verify-incomplete-descriptor",
                    "admitted_call_result_ref": {
                        "ref": (
                            "artifacts/call-results/envelopes/"
                            + "f" * 64
                            + ".json"
                        ),
                    },
                },
            ],
        },
        success_event="test.passed",
    )

    assert payload["task_map_ref"] == "artifacts/plans/task-map.json"
    assert payload["plan_artifact_package_id"] == "planpkg-current"
    assert (
        payload["plan_artifact_package_ref"]
        == "artifacts/plan-packages/current.json"
    )
    assert payload["goal_claim_set_ref"] == "artifacts/goal-claims/current.json"
    assert payload["goal_claim_set_digest"] == "b" * 64
    assert payload["input_result_refs"] == [
        "artifacts/call-results/envelopes/" + "c" * 64 + ".json",
        "artifacts/call-results/envelopes/" + "d" * 64 + ".json",
    ]


def test_parity_aggregate_synthesizes_gap_tasks_from_p1_findings(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    result_dir = (
        state_dir
        / "fanouts"
        / "fanout-cangjie-module-parity"
        / "children"
        / "scan-runtime"
    )
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({
            "payload": {
                "fanout_id": "fanout-cangjie-module-parity",
                "child_id": "scan-runtime",
                "status": "completed",
                "findings": [
                    {
                        "severity": "medium",
                        "path": "packages/core/src/agent-loop.ts",
                        "message": (
                            "P1 invalid JSON recovery diverges from "
                            "Python agent/conversation_loop.py:3727-3750. "
                            "Gap task hint: skip sibling tool calls when "
                            "any tool call has invalid JSON."
                        ),
                        "line": 196,
                    },
                ],
                "report": {
                    "status": "passed",
                    "summary": "No P0; one P1 runtime parity finding.",
                    "findings": [
                        {
                            "severity": "medium",
                            "path": "packages/core/src/agent-loop.ts",
                            "message": (
                                "P1 invalid JSON recovery diverges from "
                                "Python agent/conversation_loop.py:3727-3750. "
                                "Gap task hint: skip sibling tool calls when "
                                "any tool call has invalid JSON."
                            ),
                            "line": 196,
                        },
                        {
                            "severity": "medium",
                            "path": "packages/core/src/agent-loop.ts",
                            "message": (
                                "P1 max-iterations summary call omits the "
                                "Python agent/chat_completion_helpers.py:1269-1274 "
                                "summary-request user turn. Gap task hint: "
                                "inject the summary-request user turn before "
                                "the final toolless call."
                            ),
                            "line": 237,
                        },
                        {
                            "severity": "low",
                            "path": "packages/state/src/config-loader.ts",
                            "message": "P2 legacy config normalization drift.",
                        },
                    ],
                },
            },
        }),
        encoding="utf-8",
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir

    payload = orch._generic_fanout_success_payload(  # type: ignore[attr-defined]
        manifest={
            "fanout_id": "fanout-cangjie-module-parity",
            "children": [{"child_id": "scan-runtime"}],
            "trigger_payload": {
                "pdd_id": "CANGJIE-ZERO-TO-ONE-HERMES-REBUILD-R3",
                "feature_id": "CANGJIE-ZERO-TO-ONE-HERMES-REBUILD-R3",
                "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
                "source_index_ref": ".zf/artifacts/CANGJIE/source_index.json",
            },
        },
        success_event="module.parity.scan.completed",
    )

    assert payload["open_p0_p1_gap_count"] == 2
    assert payload["gap_task_count"] == 1
    gap_task = payload["gap_tasks"][0]
    assert gap_task["priority"] == "P1"
    assert gap_task["affinity_tag"] == "pi-core"
    assert gap_task["claim_paths"] == ["packages/core/src/agent-loop.ts"]
    assert "agent/conversation_loop.py:3727-3750" in gap_task["source_refs"]
    assert "agent/chat_completion_helpers.py:1269-1274" in gap_task["source_refs"]
    assert "skip sibling tool calls" in gap_task["acceptance"][0]
    assert len(gap_task["acceptance"]) == 2
    assert len(gap_task["findings"]) == 2


def test_parity_aggregate_ignores_info_findings_that_deny_p0_p1_gaps(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    result_dir = (
        state_dir
        / "fanouts"
        / "fanout-cangjie-module-parity"
        / "children"
        / "scan-verification"
    )
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({
            "payload": {
                "fanout_id": "fanout-cangjie-module-parity",
                "child_id": "scan-verification",
                "status": "completed",
                "report": {
                    "status": "passed",
                    "summary": (
                        "Gap table has 0 open P0/P1 items; three P2 items "
                        "are deferred behind environment flags."
                    ),
                    "findings": [
                        {
                            "severity": "info",
                            "path": "packages/web-server/src",
                            "message": (
                                "Routes are consolidated differently from the "
                                "planning paths. Real auth/ws are present; "
                                "file-layout divergence, NOT a capability gap. "
                                "Do not raise as P0/P1."
                            ),
                        },
                        {
                            "severity": "info",
                            "path": "docs/validation/cangjie-gap-task-map.json",
                            "message": (
                                "GAP-LIVE-LLM remains P2 open; deterministic "
                                "golden tests cover function calling. "
                                "No open P0/P1."
                            ),
                        },
                    ],
                },
            },
        }),
        encoding="utf-8",
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir

    payload = orch._generic_fanout_success_payload(  # type: ignore[attr-defined]
        manifest={
            "fanout_id": "fanout-cangjie-module-parity",
            "children": [{"child_id": "scan-verification"}],
            "trigger_payload": {
                "pdd_id": "CANGJIE-ZERO-TO-ONE-HERMES-REBUILD-R4",
                "feature_id": "CANGJIE-ZERO-TO-ONE-HERMES-REBUILD-R4",
                "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
                "source_index_ref": ".zf/artifacts/CANGJIE/source_index.json",
            },
        },
        success_event="module.parity.scan.completed",
    )

    assert payload["open_p0_p1_gap_count"] == 0
    assert "gap_tasks" not in payload
    assert "open_p0_p1_findings" not in payload


def test_refactor_plan_synth_briefing_supports_legacy_ready_event(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    # db25fd6: _write_fanout_synth_briefing reads self.config for the
    # pure-aggregator policy; None -> applies=False (legacy content path).
    orch.config = None
    role = RoleConfig(
        name="refactor-plan-synth",
        backend="mock",
        role_kind="reader",
        stages=["refactor_plan_synthesis"],
    )

    path = orch._write_fanout_synth_briefing(  # type: ignore[attr-defined]
        role=role,
        manifest={
            "fanout_id": "fanout-refactor-plan",
            "stage_id": "refactor-planning-scan",
            "target_ref": "HEAD",
            "trigger_payload": {
                "review_artifact_ref": ".zf/artifacts/review.md",
                "plan_intent": "Generate refactor plan.",
            },
            "aggregate_config": {
                "success_event": "refactor.plan.ready",
                "failure_event": "refactor.plan.blocked",
                "synth_role": "refactor-plan-synth",
            },
            "children": [],
        },
        run_id="run-synth",
    )

    briefing = path.read_text(encoding="utf-8")
    assert "Plan Artifact Contract" in briefing
    assert "refactor_plan_md" in briefing
    assert "plan_artifact_ref" in briefing
    assert "task_map_ref" in briefing
    assert "scan_quality_audit_ref" in briefing
    assert "task_map" in briefing
    assert "gates" in briefing
    assert "`plan_ports` MUST be a JSON array of descriptor objects" in briefing
    assert "do not use a `{logical_name: body}` object map" in briefing
    assert "Generate refactor plan." in briefing


def test_synth_briefing_lists_with_and_without_output_children(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.config = None
    role = RoleConfig(
        name="review-synth",
        backend="mock",
        role_kind="reader",
        stages=["review"],
    )

    path = orch._write_fanout_synth_briefing(  # type: ignore[attr-defined]
        role=role,
        manifest={
            "fanout_id": "fanout-review",
            "stage_id": "review-candidate",
            "target_ref": "HEAD",
            "aggregate_config": {
                "success_event": "review.approved",
                "failure_event": "review.rejected",
                "synth_role": "review-synth",
            },
            "children": [
                {
                    "child_id": "review-a",
                    "role_instance": "review-a",
                    "status": "completed",
                    "report_path": "fanouts/fanout-review/children/review-a/report.json",
                    "report": {"summary": "ok"},
                },
                {
                    "child_id": "review-b",
                    "role_instance": "review-b",
                    "status": "dispatched",
                },
            ],
        },
        run_id="run-synth",
    )

    briefing = path.read_text(encoding="utf-8")
    assert "## Fanout Scope Summary" in briefing
    assert "with_output: `review-a`" in briefing
    assert "without_output: `review-b`" in briefing
    assert "## Reducer Discipline" in briefing
    assert "Do not read or edit project source files" in briefing


def test_any_failed_fail_aggregates_on_first_failure(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, mode="any_failed_fail")
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[ZfEvent(
        type="review.rejected",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "rejected",
            "reason": "risk",
        },
    )])

    manifest = _manifest(state_dir, fanout_id)
    assert manifest["aggregate"]["status"] == "failed"
    assert any(event.type == "review.rejected" and not event.payload.get("child_id")
               for event in log.read_all())


def test_valid_child_report_is_stored_in_manifest(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
                "report": {
                    "child_id": child_id,
                    "status": "passed",
                    "summary": "No blocking findings.",
                    "findings": [{
                        "severity": "medium",
                        "category": "testing",
                        "path": "src/app.py",
                        "line": 42,
                        "message": "Needs one regression test.",
                    }],
                    "recommendation": "approve",
                },
            },
        )])

    manifest = _manifest(state_dir, fanout_id)
    child = next(item for item in manifest["children"]
                 if item["child_id"] == "review-a")
    assert child["report_status"] == "passed"
    assert child["recommendation"] == "approve"
    assert child["report"]["findings"][0]["line"] == 42
    assert Path(child["report_path"]).exists()


def test_malformed_child_report_marks_child_failed_with_diagnostics(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[ZfEvent(
        type="review.approved",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "approved",
            "report": {
                "child_id": "review-a",
                "status": "ok",
                "findings": "none",
                "recommendation": "ship",
            },
        },
    )])

    manifest = _manifest(state_dir, fanout_id)
    child = next(item for item in manifest["children"]
                 if item["child_id"] == "review-a")
    assert child["status"] == "failed"
    assert child["reason"] == "malformed_report"
    assert child["report_diagnostics"]
    assert (
        state_dir
        / "fanouts"
        / fanout_id
        / "children"
        / "review-a"
        / "report-diagnostics.json"
    ).exists()


def test_stale_reader_completion_from_superseded_fanout_is_audited(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    manifest = _manifest(state_dir, fanout_id)
    child = manifest["children"][0]
    new_fanout_id = "fanout-review-candidate-new"
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = new_fanout_id
    new_payload["trigger_event_id"] = "candidate-ready-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))
    late = ZfEvent(
        type="review.approved",
        actor=child["role_instance"],
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "status": "approved",
            "report": {
                "child_id": child["child_id"],
                "status": "passed",
                "summary": "Late approval from old fanout.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    )

    orch.run_once(events=[late])

    events = log.read_all()
    stale = [
        event for event in events
        if event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == late.id
    ]
    assert len(stale) == 1
    assert stale[0].payload["reason"] == "superseded_by_latest_fanout"
    assert stale[0].payload["superseded_by"] == new_fanout_id
    assert not [
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == late.id
    ]
    current_child = next(
        item for item in _manifest(state_dir, fanout_id)["children"]
        if item["child_id"] == child["child_id"]
    )
    assert current_child["status"] == "dispatched"


def test_stale_reader_aggregate_recovery_skips_superseded_fanout(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    writer = EventWriter(log)
    for child in _manifest(state_dir, fanout_id)["children"]:
        writer.append(ZfEvent(
            type="fanout.child.completed",
            actor="zf-cli",
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "trace_id": "trace-1",
                "stage_id": "review-candidate",
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "role_instance": child["role_instance"],
                "status": "completed",
                "report": {
                    "child_id": child["child_id"],
                    "status": "passed",
                    "summary": "Already terminal before redispatch.",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        ))
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = "fanout-review-candidate-new"
    new_payload["trigger_event_id"] = "candidate-ready-new"
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))

    orch._evaluate_reader_fanout(fanout_id)  # type: ignore[attr-defined]

    assert not [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]


def test_provider_style_finding_fields_are_normalized():
    result = validate_fanout_report(
        {
            "child_id": "review-a",
            "status": "failed",
            "summary": "Rejected.",
            "findings": [
                {
                    "severity": "blocking",
                    "file": "src/zf/core/config/loader.py:42",
                    "summary": "Loader crashes on malformed config section.",
                },
            ],
            "recommendation": "reject",
        },
        child_id="review-a",
    )

    assert result.valid is True
    finding = result.report["findings"][0]
    assert finding["severity"] == "high"
    assert finding["path"] == "src/zf/core/config/loader.py"
    assert finding["line"] == 42
    assert finding["message"] == "Loader crashes on malformed config section."


def test_admitted_rejected_verdict_is_not_a_malformed_fanout_report():
    result = _FanoutReportProbe()._fanout_child_report(
        child_id="verify-a",
        event=ZfEvent(
            type="verify.child.completed",
            payload={
                "report": {
                    "child_id": "verify-a",
                    "status": "rejected",
                    "summary": "Product acceptance remains open.",
                    "findings": [],
                    "recommendation": "needs_rework",
                },
                "verification_result": {"verdict": "rejected"},
            },
        ),
        success=True,
    )

    assert result.valid is True
    assert result.diagnostics == []
    assert result.report["status"] == "failed"
    assert result.report["recommendation"] == "needs_rework"


def test_contract_finding_fields_survive_fanout_normalization():
    result = validate_fanout_report(
        {
            "child_id": "plan-critic",
            "status": "failed",
            "summary": "Plan contract rejected.",
            "findings": [
                {
                    "severity": "blocker",
                    "code": "plan_ports_not_descriptor_array",
                    "field": "plan_ports",
                    "observed": "plan_ports contains logical-name strings",
                    "expected": "plan_ports contains descriptor objects",
                    "evidence_refs": ["plan-synth-contract.json"],
                },
            ],
            "recommendation": "reject",
        },
        child_id="plan-critic",
    )

    assert result.valid is True
    finding = result.report["findings"][0]
    assert finding["severity"] == "high"
    assert finding["category"] == "plan_ports_not_descriptor_array"
    assert finding["field"] == "plan_ports"
    assert finding["evidence_refs"] == ["plan-synth-contract.json"]
    assert "Observed: plan_ports contains logical-name strings" in finding["message"]
    assert "Expected: plan_ports contains descriptor objects" in finding["message"]


def test_fanout_report_preserves_audit_evidence_fields():
    result = validate_fanout_report(
        {
            "child_id": "verify-a",
            "status": "passed",
            "summary": "verified",
            "findings": [],
            "recommendation": "approve",
            "checks": [{"command": "pytest -q", "exit_code": 0}],
            "artifact_refs": ["reports/verify.json"],
            "evidence_refs": ["reports/verify.json", "git:abc123"],
            "test_refs": ["pytest"],
            "e2e_refs": ["smoke"],
            "scores": {"evidence_quality": 1.0},
        },
        child_id="verify-a",
    )

    assert result.valid is True
    assert result.report["checks"][0]["command"] == "pytest -q"
    assert result.report["artifact_refs"] == ["reports/verify.json"]
    assert result.report["evidence_refs"] == ["reports/verify.json", "git:abc123"]
    assert result.report["test_refs"] == ["pytest"]
    assert result.report["e2e_refs"] == ["smoke"]
    assert result.report["scores"]["evidence_quality"] == 1.0


def test_fanout_child_report_merges_top_level_evidence_refs():
    result = _FanoutReportProbe()._fanout_child_report(
        child_id="verify-a",
        event=ZfEvent(
            type="verify.child.completed",
            payload={
                "report": {
                    "status": "passed",
                    "summary": "verified",
                    "findings": [],
                    "recommendation": "approve",
                },
                "artifact_refs": ["reports/verify.json"],
                "evidence_refs": ["git:abc123"],
            },
        ),
        success=True,
    )

    assert result.report["artifact_refs"] == ["reports/verify.json"]
    assert result.report["evidence_refs"] == ["git:abc123"]


def test_fanout_synth_report_paths_become_evidence_refs():
    result = _FanoutReportProbe()._fanout_child_report(
        child_id="judge-synth",
        event=ZfEvent(
            type="fanout.synth.completed",
            payload={
                "status": "completed",
                "summary": "all child reports passed",
                "report_paths": [
                    "fanouts/fanout-final/children/verify-a/report.json",
                    "fanouts/fanout-final/children/verify-b/report.json",
                ],
                "report": {
                    "child_id": "judge-synth",
                    "status": "passed",
                    "summary": "all child reports passed",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        ),
        success=True,
    )

    assert result.report["evidence_refs"] == [
        "fanouts/fanout-final/children/verify-a/report.json",
        "fanouts/fanout-final/children/verify-b/report.json",
    ]


def test_synth_role_receives_child_report_paths_and_defers_final_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    state_dir, log, transport, orch = _state(
        tmp_path,
        synth=True,
        synth_skills=True,
    )
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
                "report": {
                    "child_id": child_id,
                    "status": "passed",
                    "summary": f"{child_id} passed",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        )])

    events = log.read_all()
    synth_events = [event for event in events
                    if event.type == "fanout.synth.dispatched"]
    assert len(synth_events) == 1
    assert synth_events[0].payload["role_instance"] == "review-synth"
    assert (
        synth_events[0].payload["runner_policy"]["policy_id"]
        == "pure_aggregator.v1"
    )
    assert len([path for path in synth_events[0].payload["report_paths"] if path]) == 2
    assert [sent[0] for sent in transport.sent][-1] == "review-synth"
    briefing = Path(synth_events[0].payload["briefing_path"]).read_text(
        encoding="utf-8",
    )
    assert "## Runner Policy" in briefing
    assert "pure_aggregator: true" in briefing
    assert "candidate/F-11111111" in briefing
    assert "review-a passed" in briefing
    assert "`/zf-harness-gate-evaluator`" in briefing
    assert "uv --project /repo run zf emit fanout.synth.completed" in briefing
    assert not any(event.type == "review.approved" for event in events)


def test_synth_briefing_filters_stale_fanout_instance(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, synth=True)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    manifest = _manifest(state_dir, fanout_id)
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = "fanout-review-current"
    new_payload["trigger_event_id"] = "candidate-ready-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))
    role = next(role for role in orch.config.roles if role.name == "review-synth")

    briefing = orch._write_fanout_synth_briefing(  # type: ignore[attr-defined]
        role=role,
        manifest=manifest,
        run_id=f"run-{fanout_id}-synth",
    ).read_text(encoding="utf-8")

    assert "current_instance: false" in briefing
    assert "superseded_by: `fanout-review-current`" in briefing
    assert "`review-a` role=" not in briefing
    assert "Do not synthesize or approve from its child outputs" in briefing


def test_run_once_replays_missed_reader_child_result(tmp_path: Path):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        synth=True,
        child_success_event="review.child.completed",
        child_failure_event="review.child.failed",
    )
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    missed = ZfEvent(
        type="review.child.failed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "review-candidate",
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "role_instance": "review-a",
            "status": "failed",
            "reason": "blocking finding",
            "report": {
                "child_id": "review-a",
                "status": "failed",
                "summary": "Review found a blocker.",
                "findings": [{
                    "severity": "high",
                    "path": "package.json",
                    "line": 1,
                    "message": "Gate command is not executable.",
                }],
                "recommendation": "reject",
            },
        },
    )
    log.append(missed)

    orch.run_once(events=[])

    events = log.read_all()
    failed = [
        event for event in events
        if event.type == "fanout.child.failed"
        and (event.payload.get("evidence") or {}).get("result_event_id") == missed.id
    ]
    assert failed
    assert failed[-1].payload["child_id"] == "review-a"
    assert _manifest(state_dir, fanout_id)["children"][0]["status"] == "failed"


def test_synth_recommendation_maps_to_kernel_owned_final_event(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, synth=True)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
                "report": {
                    "child_id": child_id,
                    "status": "passed",
                    "summary": f"{child_id} passed",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        )])

    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="review-synth",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "review-candidate",
            "role_instance": "review-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "Approve after synthesis.",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "Approve after synthesis.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    ))
    orch.run_once(events=[synth_event])

    events = log.read_all()
    final = [event for event in events
             if event.type == "review.approved" and event.actor == "zf-cli"]
    assert len(final) == 1
    assert not any(event.type == "event.malformed" for event in events)
    assert final[0].payload["recommendation"] == "approve"
    manifest = _manifest(state_dir, fanout_id)
    assert manifest["aggregate"]["status"] == "completed"
    assert manifest["aggregate"]["synth_event_id"] == synth_event.id
    assert manifest["synth"]["recommendation"] == "approve"


def test_run_once_replays_missed_reader_synth_result(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, synth=True)
    package_identity = {
        "plan_artifact_package_id": "planpkg-current",
        "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
        "plan_artifact_package_digest": "package-sha",
    }
    _start_fanout(orch, payload=package_identity)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
                "report": {
                    "child_id": child_id,
                    "status": "passed",
                    "summary": f"{child_id} passed",
                    "findings": [],
                    "recommendation": "approve",
                },
            },
        )])

    synth_event = EventWriter(log).append(ZfEvent(
        type="fanout.synth.completed",
        actor="review-synth",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "review-candidate",
            "role_instance": "review-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "reject",
            "summary": "Reject after synthesis.",
            "report": {
                "child_id": "synth",
                "status": "failed",
                "summary": "Reject after synthesis.",
                "findings": [{
                    "severity": "high",
                    "path": "package.json",
                    "line": 1,
                    "message": "Gate command is not executable.",
                }],
                "recommendation": "reject",
            },
        },
    ))
    assert not any(event.type == "review.rejected" for event in log.read_all())
    assert _manifest(state_dir, fanout_id)["aggregate"]["status"] == "started"

    orch.run_once(events=[])

    events = log.read_all()
    final = [
        event for event in events
        if event.type == "review.rejected"
        and event.actor == "zf-cli"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(final) == 1
    assert final[0].payload["summary"] == "Reject after synthesis."
    assert final[0].payload["findings"][0]["message"] == (
        "Gate command is not executable."
    )
    aggregate_events = [
        event for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(aggregate_events) == 1
    manifest = _manifest(state_dir, fanout_id)
    assert manifest["aggregate"]["status"] == "failed"
    assert manifest["aggregate"]["synth_event_id"] == synth_event.id
    assert manifest["synth"]["recommendation"] == "reject"
    # B-FIX-07 (R32 stall): failure publish_event(review.rejected)必带 manifest
    # 的 pdd_id/feature_id —— 否则 candidate_rework 推不出 pdd → 无法路由 rework。
    assert final[0].payload.get("pdd_id") == str(manifest.get("pdd_id") or "")
    assert "pdd_id" in final[0].payload and "feature_id" in final[0].payload
    assert aggregate_events[0].payload.get("pdd_id") == str(manifest.get("pdd_id") or "")
    for key, value in package_identity.items():
        assert aggregate_events[0].payload[key] == value
        assert final[0].payload[key] == value

    orch.run_once(events=[])

    events_after_replay = log.read_all()
    assert len([
        event for event in events_after_replay
        if event.type == "review.rejected"
        and event.actor == "zf-cli"
        and event.payload.get("fanout_id") == fanout_id
    ]) == 1
    assert len([
        event for event in events_after_replay
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]) == 1


def test_refactor_review_ready_projects_artifacts_with_coverage_gate(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-b", backend="mock", role_kind="reader"),
            RoleConfig(
                name="review-synth",
                backend="mock",
                role_kind="reader",
                publishes=["fanout.synth.completed"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="zaofu-refactor-review-scan",
                trigger="zaofu.refactor.review.requested",
                topology="fanout_reader",
                roles=["review-a", "review-b"],
                target_ref="${target_ref}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.review.ready",
                    failure_event="zaofu.refactor.review.blocked",
                    synth_role="review-synth",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    trigger = ZfEvent(
        type="zaofu.refactor.review.requested",
        actor="human",
        correlation_id="trace-review",
        payload={"pdd_id": "PDD-ZF", "target_ref": "dev"},
    )
    orch.run_once(events=[trigger])
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="zaofu.refactor.review.ready",
            actor=child_id,
            correlation_id="trace-review",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "completed",
                "report": {
                    "child_id": child_id,
                    "status": "passed",
                    "summary": f"{child_id} reviewed.",
                    "findings": [],
                    "recommendation": "approve",
                    "coverage_matrix": [{
                        "subsystem": child_id,
                        "inspected_paths": ["src/zf/runtime/orchestrator.py"],
                        "evidence_refs": ["src/zf/runtime/orchestrator.py:1"],
                        "coverage": "complete",
                    }],
                    "evidence_refs": ["src/zf/runtime/orchestrator.py:1"],
                    "uncovered": [],
                },
            },
        )])

    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="review-synth",
        correlation_id="trace-review",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "zaofu-refactor-review-scan",
            "role_instance": "review-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "Review coverage accepted.",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "Review coverage accepted.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    ))
    orch.run_once(events=[synth_event])

    final = next(event for event in log.read_all()
                 if event.type == "zaofu.refactor.review.ready"
                 and event.actor == "zf-cli")
    review_path = Path(final.payload["review_artifact_ref"])
    coverage_path = Path(final.payload["coverage_matrix_ref"])
    assert review_path.exists()
    assert coverage_path.exists()
    assert final.payload["artifact_digests"][str(review_path)]
    assert final.payload["artifact_digests"][str(coverage_path)]
    assert final.payload["artifact_gate"] == "passed"
    assert len(json.loads(coverage_path.read_text(encoding="utf-8"))) == 2


def test_refactor_review_gate_blocks_missing_coverage(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(
                name="review-synth",
                backend="mock",
                role_kind="reader",
                publishes=["fanout.synth.completed"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="zaofu-refactor-review-scan",
                trigger="zaofu.refactor.review.requested",
                topology="fanout_reader",
                roles=["review-a"],
                target_ref="${target_ref}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.review.ready",
                    failure_event="zaofu.refactor.review.blocked",
                    synth_role="review-synth",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    trigger = ZfEvent(
        type="zaofu.refactor.review.requested",
        actor="human",
        correlation_id="trace-review",
        payload={"pdd_id": "PDD-ZF", "target_ref": "dev"},
    )
    orch.run_once(events=[trigger])
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")
    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.review.ready",
        actor="review-a",
        correlation_id="trace-review",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
            "report": {
                "child_id": "review-a",
                "status": "passed",
                "summary": "Reviewed without structured coverage.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    )])
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="review-synth",
        correlation_id="trace-review",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "zaofu-refactor-review-scan",
            "role_instance": "review-synth",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "Approve.",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "Approve.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    ))
    orch.run_once(events=[synth_event])

    events = log.read_all()
    blocked = next(event for event in events
                   if event.type == "zaofu.refactor.review.blocked")
    assert not any(event.type == "zaofu.refactor.review.ready"
                   and event.actor == "zf-cli" for event in events)
    assert blocked.payload["artifact_gate"] == "failed"
    assert blocked.payload["failed_children"] == ["review-a"]
    assert blocked.payload["reason"].startswith("artifact gate failed:")
    assert "review-a: missing coverage_matrix" in blocked.payload["reason"]
    diagnostics = Path(blocked.payload["diagnostics_ref"])
    assert diagnostics.exists()
    assert blocked.payload["artifact_digests"][str(diagnostics)]
    assert "missing coverage_matrix" in diagnostics.read_text(encoding="utf-8")


def test_refactor_plan_ready_projects_plan_and_task_map(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="refactor-plan-author", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="zaofu-refactor-plan-synthesis",
                trigger="zaofu.refactor.plan.requested",
                topology="fanout_reader",
                flow_kind="refactor",
                roles=[],
                target_ref="${target_ref}",
                children=[
                    FanoutChildConfig(role_instance="refactor-plan-author"),
                ],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.plan.ready",
                    failure_event="zaofu.refactor.plan.blocked",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    trigger = ZfEvent(
        type="zaofu.refactor.plan.requested",
        actor="human",
        correlation_id="trace-plan",
        payload={
            "pdd_id": "PDD-ZF",
            "flow_kind": "refactor",
            "target_ref": "dev",
            "review_artifact_ref": ".zf/artifacts/fanout-review/review.md",
            "plan_intent": "Conservative P0/P1 refactor plan.",
        },
    )
    orch.run_once(events=[trigger])
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "review_artifact_ref" in briefing
    assert "Conservative P0/P1 refactor plan." in briefing
    audit_path = state_dir / "artifacts" / "fanout-plan" / "scan-quality-audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.plan.ready",
        actor="refactor-plan-author",
        correlation_id="trace-plan",
        payload={
            "fanout_id": fanout_id,
            "child_id": "refactor-plan-author",
            "run_id": f"run-{fanout_id}-refactor-plan-author",
            "status": "completed",
            "scan_quality_audit_ref": str(audit_path),
            "artifact_refs": [str(audit_path)],
            "report": {
                "child_id": "refactor-plan-author",
                "status": "passed",
                "summary": "Plan ready.",
                "findings": [],
                "recommendation": "approve",
                "review_artifact_ref": ".zf/artifacts/fanout-review/review.md",
                "plan_intent": "Conservative P0/P1 refactor plan.",
                "scan_quality_audit_ref": str(audit_path),
                "artifact_refs": [str(audit_path)],
                "refactor_plan_md": "## Plan\n\n1. Split runtime projector.",
                "task_map": {
                    "tasks": [{
                        "task_id": "P0-runtime-projector",
                        "scope": "runtime projector",
                        "allowed_paths": ["src/zf/runtime/"],
                        "dependencies": [],
                    }],
                },
                "gates": [{"task_id": "P0-runtime-projector", "command": "pytest"}],
                "risk_register": [],
                "backlog_candidates": [],
            },
        },
    )])

    final = next(event for event in log.read_all()
                 if event.type == "zaofu.refactor.plan.ready"
                 and event.actor == "zf-cli")
    plan_path = Path(final.payload["plan_artifact_ref"])
    task_map_path = Path(final.payload["task_map_ref"])
    assert plan_path.exists()
    assert task_map_path.exists()
    assert final.payload["artifact_digests"][str(plan_path)]
    assert final.payload["artifact_digests"][str(task_map_path)]
    assert final.payload["scan_quality_audit_ref"] == str(audit_path)
    assert str(audit_path) in final.payload["artifact_refs"]
    assert final.payload["artifact_digests"][str(audit_path)]
    assert "Split runtime projector" in plan_path.read_text(encoding="utf-8")
    task_map = json.loads(task_map_path.read_text(encoding="utf-8"))
    assert task_map["tasks"][0]["task_id"] == "P0-runtime-projector"
    assert final.payload["artifact_gate"] == "passed"


def test_refactor_plan_synth_ready_bridges_to_task_map(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="refactor-plan-author",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="plan-critic",
                backend="mock",
                role_kind="reader",
                publishes=["fanout.synth.completed"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="zaofu-refactor-plan-synthesis",
                trigger="zaofu.refactor.plan.requested",
                topology="fanout_reader",
                flow_kind="refactor",
                roles=[],
                children=[
                    FanoutChildConfig(role_instance="refactor-plan-author"),
                ],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.plan.ready",
                    failure_event="zaofu.refactor.plan.blocked",
                    synth_role="plan-critic",
                ),
            ),
        ]),
    )
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    review_path = state_dir / "artifacts" / "review" / "review.md"
    review_path.parent.mkdir(parents=True)
    review_path.write_text("# Review\n\nScope is complete.\n", encoding="utf-8")
    audit_path = state_dir / "artifacts" / "review" / "scan-quality-audit.json"
    audit_path.write_text('{"status":"passed"}\n', encoding="utf-8")
    requirement_path = state_dir / "artifacts" / "review" / "requirement.json"
    requirement_path.write_text('{"objective":"Preserve behavior."}\n', encoding="utf-8")
    adapter_path = state_dir / "artifacts" / "review" / "adapter.json"
    adapter_path.write_text('{"status":"ready"}\n', encoding="utf-8")
    source_index_path = (
        state_dir
        / "workdirs"
        / "refactor-plan-author"
        / "project"
        / "docs"
        / "plans"
        / "source-index.json"
    )
    source_index_path.parent.mkdir(parents=True)
    source_index_path.write_text('{"tasks":[]}\n', encoding="utf-8")
    trigger = ZfEvent(
        type="zaofu.refactor.plan.requested",
        actor="human",
        correlation_id="trace-plan",
        payload={
            "pdd_id": "PDD-ZF",
            "feature_id": "PDD-ZF",
            "flow_kind": "refactor",
            "plan_revision": "revision-1",
            "task_map_generation": "generation-1",
            "requirement_spec_ref": str(requirement_path),
            "review_artifact_ref": str(review_path),
        },
    )
    orch.run_once(events=[trigger])
    fanout_id = next(
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
    )
    report = {
        "child_id": "refactor-plan-author",
        "status": "passed",
        "summary": "Plan ready.",
        "findings": [],
        "recommendation": "approve",
        "review_artifact_ref": str(review_path),
        "scan_quality_audit_ref": str(audit_path),
        "source_index_ref": "docs/plans/source-index.json",
        "artifact_refs": [str(audit_path), "docs/plans/source-index.json"],
        "requirement_spec_ref": str(requirement_path),
        "project_adapter_ref": str(adapter_path),
        "skill_adapter_plan_ref": str(adapter_path),
        "plan_revision": "revision-2",
        "plan_ports": [{
            "logical_name": "source_inventory",
            "schema_version": "source-inventory.v1",
            "body": {
                "schema_version": "source-inventory.v1",
                "status": "ready",
                "items": [],
            },
        }],
        "refactor_plan_md": "## Plan\n\nExtract the configuration module.",
        "task_map": {
            "schema_version": "task-map.v1",
            "feature_id": "PDD-ZF",
            "task_map_generation": "generation-2",
            "metadata": {"plan_revision": "revision-2"},
            "required_plan_ports": ["source_index"],
            "tasks": [{
                "task_id": "P0-config",
                "title": "Extract configuration",
                "allowed_paths": ["src/config.py"],
                "allowed_paths_reason": "Own the cohesive config module.",
                "blocked_by": [],
                "validation": {
                    "commands": [{
                        "id": "test-config",
                        "command": "pytest -q",
                        "acceptance_ids": ["AC-CONFIG"],
                        "owner": "verify",
                        "tier": "static",
                        "deterministic": True,
                        "reusable": True,
                        "timeout_seconds": 60,
                    }],
                },
                "acceptance_criteria": [{
                    "id": "AC-CONFIG",
                    "statement": "Configuration behavior remains stable.",
                    "mandatory": True,
                    "verification_owner": "verify",
                    "verification_tier": "static",
                    "verification_command_ids": ["test-config"],
                }],
            }],
        },
        "gates": [{"id": "test-config", "command": "pytest -q"}],
        "risk_register": [],
        "backlog_candidates": [],
    }
    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.plan.ready",
        actor="refactor-plan-author",
        correlation_id="trace-plan",
        payload={
            "fanout_id": fanout_id,
            "child_id": "refactor-plan-author",
            "run_id": f"run-{fanout_id}-refactor-plan-author",
            "status": "completed",
            "report": report,
        },
    )])
    synth_event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="plan-critic",
        correlation_id="trace-plan",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "zaofu-refactor-plan-synthesis",
            "child_id": "synth",
            "role_instance": "plan-critic",
            "run_id": f"run-{fanout_id}-synth",
            "status": "completed",
            "recommendation": "approve",
            "summary": "Plan approved.",
            "plan_ports": [],
            "review_artifact_ref": "",
            "plan_artifact_ref": "",
            "task_map_ref": "",
            "source_index_ref": "",
            "scan_quality_audit_ref": "",
            "refactor_plan_md": "",
            "task_map": {},
            "gates": [],
            "risk_register": [],
            "backlog_candidates": [],
            "report": {
                "child_id": "synth",
                "status": "passed",
                "findings": [],
                "recommendation": "approve",
                "summary": "Plan approved.",
            },
        },
    ))

    orch.run_once(events=[synth_event])

    ready = [
        event
        for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "refactor_plan_bridge"
    ]
    assert len(ready) == 1
    assert ready[0].payload["feature_id"] == "PDD-ZF"
    assert ready[0].payload["task_map_ref"]
    assert ready[0].payload["requirement_spec_ref"] == str(requirement_path)
    assert ready[0].payload["project_adapter_ref"] == str(adapter_path)
    assert ready[0].payload["plan_revision"] == "revision-2"
    assert ready[0].payload["task_map_generation"] == "generation-2"
    published_plan = next(
        event
        for event in log.read_all()
        if event.type == "zaofu.refactor.plan.ready"
        and event.actor == "zf-cli"
    )
    assert published_plan.payload["plan_revision"] == "revision-2"
    assert published_plan.payload["plan_ports"][0]["logical_name"] == (
        "source_inventory"
    )
    assert published_plan.payload["planning_result_ref"] == (
        published_plan.payload["plan_artifact_ref"]
    )
    assert published_plan.payload["source_index_ref"].startswith(
        f"artifacts/fanouts/{fanout_id}/refactor-plan-author/"
    )
    assert (state_dir / published_plan.payload["source_index_ref"]).exists()
    assert published_plan.payload["artifact_digests"][
        published_plan.payload["source_index_ref"]
    ]


def _lane_pipeline_spec(assembly=None):
    if assembly is None:
        assembly = {"task": "CJMIN-ASSEMBLY-001"}
    return parse_lane_pipeline({
        "id": "cj-min-refactor-lane-pipeline",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 2,
        "assembly": assembly,
        "stages": [{"id": "impl"}],
    })


def _refactor_plan_config_with_pipeline(assembly=None) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="refactor-plan-author", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(
            pipelines=[_lane_pipeline_spec(assembly=assembly)],
            stages=[
                WorkflowStageConfig(
                    id="zaofu-refactor-plan-synthesis",
                    trigger="zaofu.refactor.plan.requested",
                    topology="fanout_reader",
                    roles=[],
                    target_ref="${target_ref}",
                    children=[
                        FanoutChildConfig(role_instance="refactor-plan-author"),
                    ],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="zaofu.refactor.plan.ready",
                        failure_event="zaofu.refactor.plan.blocked",
                    ),
                ),
            ],
        ),
    )


def _run_refactor_plan_with_task_map(
    tmp_path: Path,
    task_map: dict,
    *,
    assembly=None,
) -> list[ZfEvent]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        _refactor_plan_config_with_pipeline(assembly=assembly),
        _RecordingTransport(),
    )  # type: ignore[arg-type]
    trigger = ZfEvent(
        type="zaofu.refactor.plan.requested",
        actor="human",
        correlation_id="trace-plan",
        payload={
            "pdd_id": "PDD-ZF",
            "target_ref": "dev",
            "review_artifact_ref": ".zf/artifacts/fanout-review/review.md",
        },
    )
    orch.run_once(events=[trigger])
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")
    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.plan.ready",
        actor="refactor-plan-author",
        correlation_id="trace-plan",
        payload={
            "fanout_id": fanout_id,
            "child_id": "refactor-plan-author",
            "run_id": f"run-{fanout_id}-refactor-plan-author",
            "status": "completed",
            "report": {
                "child_id": "refactor-plan-author",
                "status": "passed",
                "summary": "Plan ready.",
                "findings": [],
                "recommendation": "approve",
                "review_artifact_ref": ".zf/artifacts/fanout-review/review.md",
                "refactor_plan_md": "## Plan\n\nCreate cj-min slices.",
                "task_map": task_map,
                "gates": [{"task_id": "x", "command": "pytest"}],
            },
        },
    )])
    return log.read_all()


def test_refactor_plan_compile_blocks_missing_lane_pipeline_assembly(
    tmp_path: Path,
):
    events = _run_refactor_plan_with_task_map(
        tmp_path,
        {"tasks": [{
            "task_id": "web-tui",
            "root_owner_class": "slice",
            "allowed_paths": ["package.json"],
            "verification": "pnpm build",
        }]},
    )

    assert not [
        event for event in events
        if event.type == "zaofu.refactor.plan.ready" and event.actor == "zf-cli"
    ]
    blocked = next(event for event in events
                   if event.type == "zaofu.refactor.plan.blocked")
    assert blocked.payload["artifact_gate"] == "failed"
    assert blocked.payload["plan_compile_gate"] == "failed"
    assert "CJMIN-ASSEMBLY-001" in blocked.payload["reason"]
    compile_finding = next(
        finding for finding in blocked.payload["findings"]
        if finding.get("category") == "plan_compile_gate"
    )
    assert compile_finding["severity"] == "high"
    assert "CJMIN-ASSEMBLY-001" in compile_finding["message"]
    diagnostics = Path(blocked.payload["diagnostics_ref"]).read_text(
        encoding="utf-8",
    )
    assert "CJMIN-ASSEMBLY-001" in diagnostics


def test_refactor_plan_compile_blocks_bad_verification_command(tmp_path: Path):
    events = _run_refactor_plan_with_task_map(
        tmp_path,
        {"tasks": [{
            "task_id": "CJMIN-ASSEMBLY-001",
            "root_owner_class": "assembly",
            "allowed_paths": ["package.json"],
            "verification": (
                "bash -lc 'set -euo pipefail; "
                "pnpm --filter @cj-min/contracts exec node -e "
                "\"const fs=require('node:fs');"
                "JSON.parse(fs.readFileSync('package.json','utf8'))\"; "
                "pnpm --filter ./packages/** run typecheck'"
            ),
        }]},
    )

    assert not [
        event for event in events
        if event.type == "zaofu.refactor.plan.ready" and event.actor == "zf-cli"
    ]
    blocked = next(event for event in events
                   if event.type == "zaofu.refactor.plan.blocked")
    assert blocked.payload["plan_compile_gate"] == "failed"
    diagnostics = Path(blocked.payload["diagnostics_ref"]).read_text(
        encoding="utf-8",
    )
    assert "must not wrap bash -c payload in single quotes" in diagnostics


def test_refactor_plan_compile_blocks_unknown_verification_owner(tmp_path: Path):
    events = _run_refactor_plan_with_task_map(
        tmp_path,
        {"tasks": [{
            "task_id": "CJMIN-ASSEMBLY-001",
            "root_owner_class": "assembly",
            "allowed_paths": ["package.json"],
            "verification": "pnpm build",
            "acceptance_criteria": [{
                "id": "AC-1",
                "statement": "The package remains buildable.",
                "verification_owner": "mystery",
                "verification_tier": "runtime",
            }],
        }]},
    )

    assert not [
        event for event in events
        if event.type == "zaofu.refactor.plan.ready" and event.actor == "zf-cli"
    ]
    blocked = next(event for event in events
                   if event.type == "zaofu.refactor.plan.blocked")
    assert blocked.payload["plan_compile_gate"] == "failed"
    diagnostics = Path(blocked.payload["diagnostics_ref"]).read_text(
        encoding="utf-8",
    )
    assert "unsupported verification owner 'mystery'" in diagnostics


def test_refactor_plan_compile_accepts_role_assembly_alias(tmp_path: Path):
    events = _run_refactor_plan_with_task_map(
        tmp_path,
        {"tasks": [{
            "task_id": "web-tui",
            "root_owner_class": "assembly",
            "allowed_paths": ["package.json"],
            "verification": "pnpm build",
        }]},
    )

    ready = next(event for event in events
                 if event.type == "zaofu.refactor.plan.ready"
                 and event.actor == "zf-cli")
    assert ready.payload["artifact_gate"] == "passed"
    assert ready.payload["plan_compile_gate"] == "passed"


def test_refactor_plan_compile_accepts_assembly_none_leaf_refactor(
    tmp_path: Path,
):
    events = _run_refactor_plan_with_task_map(
        tmp_path,
        {
            "schema_version": "task-map.v1",
            "feature_id": "REFACTOR-PRICING-001",
            "refactor_contract": {
                "assembly": "none",
                "assembly_policy": "none",
            },
            "tasks": [
                {
                    "task_id": "REFACTOR-PRICING-CHAR",
                    "allowed_paths": ["tests/test_pricing.py"],
                    "verification": "uv run pytest -q",
                },
                {
                    "task_id": "REFACTOR-PRICING-HELPERS",
                    "allowed_paths": ["src/orders/pricing.py"],
                    "verification": "uv run pytest -q",
                },
            ],
        },
        assembly="none",
    )

    assert not [
        event for event in events
        if event.type == "zaofu.refactor.plan.blocked"
    ]
    ready = next(event for event in events
                 if event.type == "zaofu.refactor.plan.ready"
                 and event.actor == "zf-cli")
    assert ready.payload["artifact_gate"] == "passed"
    assert ready.payload["plan_compile_gate"] == "passed"


def test_orphan_child_result_rebinds_by_role_instance(tmp_path: Path):
    """B-STUCK-1b: a reader child completion that lost fanout_id/child_id
    (restart / regular re-dispatch strips the fanout context) is re-bound to
    its fanout child by the emitting role instance, so the barrier still
    resolves instead of timing out (the ledgerlite prd-refine livelock)."""
    state_dir, log, transport, orch = _state(tmp_path)
    trigger = _start_fanout(orch)
    orch.run_once(events=[trigger])

    events = log.read_all()
    started = next(e for e in events if e.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    dispatched = [
        e for e in events
        if e.type == "fanout.child.dispatched"
        and e.payload["fanout_id"] == fanout_id
    ]
    review_a = next(e for e in dispatched if e.payload["child_id"] == "review-a")
    expected_run_id = review_a.payload["run_id"]

    # Bare completion: only actor + status, NO fanout_id/child_id/run_id.
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={"status": "completed"},
    )])

    completed = [
        e for e in log.read_all()
        if e.type == "fanout.child.completed"
        and e.payload.get("fanout_id") == fanout_id
    ]
    assert len(completed) == 1
    assert completed[0].payload["child_id"] == "review-a"
    assert completed[0].payload["run_id"] == expected_run_id
    manifest = _manifest(state_dir, fanout_id)
    child_a = next(c for c in manifest["children"] if c["child_id"] == "review-a")
    assert child_a["status"] == "completed"
    # review-b must remain untouched (no cross-binding).
    child_b = next(c for c in manifest["children"] if c["child_id"] == "review-b")
    assert child_b["status"] not in {"completed", "failed"}


def test_orphan_child_result_unknown_role_not_bound(tmp_path: Path):
    """B-STUCK-1b is conservative: a bare result from a role that owns no
    non-terminal reader child resolves to nothing -- no spurious aggregation."""
    state_dir, log, transport, orch = _state(tmp_path)
    trigger = _start_fanout(orch)
    orch.run_once(events=[trigger])
    fanout_id = next(
        e.payload["fanout_id"]
        for e in log.read_all()
        if e.type == "fanout.started"
    )

    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="ghost-role",
        correlation_id="trace-1",
        payload={"status": "completed"},
    )])

    completed = [
        e for e in log.read_all()
        if e.type == "fanout.child.completed"
        and e.payload.get("fanout_id") == fanout_id
    ]
    assert completed == []
    manifest = _manifest(state_dir, fanout_id)
    assert all(
        c["status"] not in {"completed", "failed"}
        for c in manifest["children"]
    )


def test_orphan_child_result_before_dispatch_not_rebound(tmp_path: Path):
    """A historical result cannot complete a fanout created later."""
    state_dir, log, _transport, orch = _state(tmp_path)
    stale_result = EventWriter(log).append(ZfEvent(
        type="task.pipeline.verify.completed",
        actor="review-a",
        task_id="TASK-LOCAL-OLD",
        correlation_id="trace-1",
        payload={
            "status": "completed",
            "operation_id": "operation-old",
            "attempt_id": "attempt-old",
        },
    ))

    trigger = _start_fanout(orch)
    orch.run_once(events=[trigger])
    orch.run_once()

    fanout_id = next(
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
    )
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.causation_id == stale_result.id
    ]
    child = next(
        item for item in _manifest(state_dir, fanout_id)["children"]
        if item["child_id"] == "review-a"
    )
    assert child["status"] == "dispatched"


def test_orphan_child_result_with_conflicting_operation_not_rebound(
    tmp_path: Path,
):
    """A reused reader role cannot cross-bind another durable operation."""
    state_dir, log, _transport, orch = _state(tmp_path)
    trigger = _start_fanout(orch)
    orch.run_once(events=[trigger])
    dispatched = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("child_id") == "review-a"
    )
    expected = dict(dispatched.payload)
    expected.update({
        "task_id": "TASK-GLOBAL-VERIFY",
        "operation_id": "operation-global",
        "attempt_id": "attempt-global",
    })
    EventWriter(log).append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-GLOBAL-VERIFY",
        correlation_id="trace-1",
        payload=expected,
    ))
    stale_result = EventWriter(log).append(ZfEvent(
        type="task.pipeline.verify.completed",
        actor="review-a",
        task_id="TASK-LOCAL-OLD",
        correlation_id="trace-1",
        payload={
            "status": "completed",
            "operation_id": "operation-old",
            "attempt_id": "attempt-old",
        },
    ))

    orch.run_once(events=[stale_result])

    fanout_id = expected["fanout_id"]
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.causation_id == stale_result.id
    ]
    child = next(
        item for item in _manifest(state_dir, fanout_id)["children"]
        if item["child_id"] == "review-a"
    )
    assert child["status"] == "dispatched"
    assert child["operation_id"] == "operation-global"

    matching_result = EventWriter(log).append(ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        task_id="TASK-GLOBAL-VERIFY",
        correlation_id="trace-1",
        payload={
            "status": "completed",
            "operation_id": "operation-global",
            "attempt_id": "attempt-global",
        },
    ))
    orch.run_once(events=[matching_result])

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.causation_id == matching_result.id
    ]
    assert len(completed) == 1
    assert completed[0].payload["operation_id"] == "operation-global"


def test_orphan_child_result_does_not_rebind_to_superseded_fanout(tmp_path: Path):
    """Regression (feishu e2e prd-refine stall): the orphan re-bind (B-STUCK-1b)
    must skip SUPERSEDED fanout generations, not only terminal ones. A superseded
    fanout is abandoned mid-flight -- its manifest status is not terminal and its
    children stay 'dispatched' -- so binding a fresh bare completion to it only
    gets that completion dropped as fanout.child.stale_completion, stalling the
    current stage. A resident role re-used across rounds emitted exactly this bare
    completion and bound to a stale, long-superseded prd-refine fanout -> the new
    task's prd stage stalled. The resolver must not pick the superseded generation."""
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    old_fanout_id = started.payload["fanout_id"]
    child = _manifest(state_dir, old_fanout_id)["children"][0]
    role_instance = child["role_instance"]

    # Supersede gen-1 with a fresh generation of the same logical stage.
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = "fanout-review-candidate-new"
    new_payload["trigger_event_id"] = "candidate-ready-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started", actor="zf-cli", correlation_id="trace-2",
        payload=new_payload,
    ))

    # Bare completion from the resident role: only actor, no fanout_id/child_id.
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor=role_instance,
        correlation_id="trace-1",
        payload={"status": "completed"},
    )])

    events = log.read_all()
    # Must NOT bind the fresh completion onto the superseded generation ...
    assert not [
        e for e in events
        if e.type == "fanout.child.completed"
        and e.payload.get("fanout_id") == old_fanout_id
    ]
    # ... and must NOT drop it as a stale completion of that superseded fanout.
    assert not [
        e for e in events
        if e.type == "fanout.child.stale_completion"
        and e.payload.get("fanout_id") == old_fanout_id
    ]
    # the superseded child stays dispatched (untouched), never falsely completed.
    stale_child = next(
        c for c in _manifest(state_dir, old_fanout_id)["children"]
        if c["child_id"] == child["child_id"]
    )
    assert stale_child["status"] not in {"completed", "failed"}


def test_reader_fanout_retries_lost_dispatched_child_after_worker_session_replace(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    first_dispatch = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    )

    log.append(ZfEvent(
        type="cost.usage.capture_miss",
        actor="review-a",
        payload={
            "role": "review-a",
            "reason": "session file not found for review-a",
        },
    ))
    # The hook ledger can still expose the replaced session's open Codex turn.
    # That stale projection must not consume the only infrastructure recovery.
    orch._active_provider_turn = lambda _role: {"turn_id": "stale-turn"}  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    assert any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("lost_signal_type") == "cost.usage.capture_miss"
        for event in events
    )
    retry_run_id = f"run-{fanout_id}-review-a-retry-1"
    retry_dispatches = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("run_id") == retry_run_id
        and event.id != first_dispatch.id
    ]
    assert retry_dispatches
    manifest = _manifest(state_dir, fanout_id)
    child = next(c for c in manifest["children"] if c["child_id"] == "review-a")
    assert child["status"] == "dispatched"
    assert child["run_id"] == retry_run_id
    assert [sent[0] for sent in transport.sent][-1] == "review-a"
    assert not any(
        event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("reason") == "provider_turn_active"
        for event in events
    )


def test_reader_retry_run_dispatch_blocked_keeps_retry_nonterminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
    )
    manifest = _manifest(state_dir, fanout_id)
    child = next(
        item for item in manifest["children"]
        if item["child_id"] == "review-a"
    )
    previous_dispatch = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("child_id") == "review-a"
    )

    monkeypatch.setattr(
        orch,
        "_ensure_fanout_role_dispatchable",
        lambda **_kwargs: True,
    )

    def blocked_send(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RunDispatchBlocked(
            role_name="review-a",
            reason="run_paused",
            run_id="trace-1",
        )

    monkeypatch.setattr(orch, "_send_scoped_transport_task", blocked_send)
    dispatched = orch._retry_fanout_child(  # type: ignore[attr-defined]
        manifest=manifest,
        child=child,
        previous_dispatch=previous_dispatch,
        attempt=1,
        provider_session_replaced=True,
    )

    retry_run_id = f"run-{fanout_id}-review-a-retry-1"
    events = log.read_all()
    assert dispatched is False
    assert any(
        event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("run_id") == retry_run_id
        and event.payload.get("reason") == "run_paused"
        for event in events
    )
    assert not any(
        event.type == "fanout.child.failed"
        and event.payload.get("run_id") == retry_run_id
        for event in events
    )


def test_reader_fanout_retries_child_dormant_after_restart(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = str(started.payload["fanout_id"])
    first_dispatch = next(
        event
        for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    )
    log.append(ZfEvent(
        type="role.lifecycle.dormant",
        actor="zf-cli",
        payload={
            "role": "review-a",
            "instance_id": "review-a",
            "backend": "mock",
            "from": "",
            "to": "dormant",
            "preserve_session": True,
            "preserve_workdir": True,
        },
    ))

    orch.run_once(events=[])

    events = log.read_all()
    loss = next(
        event
        for event in events
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    )
    assert loss.payload["lost_signal_type"] == "role.lifecycle.dormant"
    retry = next(
        event
        for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.id != first_dispatch.id
    )
    assert retry.payload["run_id"].endswith("-retry-1")
    assert [sent[0] for sent in transport.sent][-1] == "review-a"


def test_blocking_reader_dormant_retry_redrives_suspended_operation(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch, _config, child = _durable_reader_state(
        tmp_path
    )
    from zf.runtime.workflow_operation import (
        WorkflowOperationService,
        load_workflow_operation,
    )

    child_payload = dict(child["payload"])
    operation_id = str(child_payload["operation_id"])
    request_hash = str(child_payload["request_hash"])
    initial = load_workflow_operation(log, operation_id)
    assert initial is not None
    initial_attempt_id = str(initial["active_attempt_id"])
    initial_lease_id = str(initial["lease_id"])
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    interrupted = service.interrupt(
        operation_id=operation_id,
        request_hash=request_hash,
        workflow_run_id="run-durable",
        reason="graceful_stop",
        task_id="T-VERIFY",
        source_attempt_id=initial_attempt_id,
    )
    assert interrupted is not None
    EventWriter(log).append(ZfEvent(
        type="role.lifecycle.dormant",
        actor="zf-cli",
        payload={
            "role": "verify-1",
            "instance_id": "verify-1",
            "backend": "mock",
            "from": "",
            "to": "dormant",
            "preserve_session": True,
            "preserve_workdir": True,
        },
    ))

    orch.run_once(events=[])

    events = log.read_all()
    lost = next(
        event
        for event in events
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("child_id") == "verify-1"
    )
    redrives = [
        event
        for event in events
        if event.type == "workflow.operation.redrive_admitted"
        and event.payload.get("operation_id") == operation_id
    ]
    assert len(redrives) == 1
    assert redrives[0].causation_id == lost.id
    assert redrives[0].payload["recovery_decision_event_id"] == lost.id
    assert redrives[0].payload["recovery_decision_owner"] == "kernel_replay"

    current = load_workflow_operation(log, operation_id)
    assert current is not None
    assert current["status"] == "running"
    assert current["request_hash"] == request_hash
    assert current["active_attempt_id"] == initial_attempt_id
    assert current["lease_id"] == initial_lease_id
    assert str(current["dispatch_id"]).endswith("-retry-1")

    retry = next(
        event
        for event in events
        if event.type == "fanout.child.dispatched"
        and str(event.payload.get("run_id") or "").endswith("-retry-1")
    )
    assert retry.payload["payload"]["operation_id"] == operation_id
    assert retry.payload["payload"]["request_hash"] == request_hash
    briefing = transport.sent[-1][1].read_text(encoding="utf-8")
    assert child_payload["result_scratch_ref"] in briefing
    assert operation_id in briefing


def test_reader_fanout_retries_after_worker_relaunch_even_with_prior_activity(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]

    log.append(ZfEvent(
        type="agent.usage",
        actor="review-a",
        payload={"role": "review-a", "context_usage_ratio": 0.12},
    ))
    log.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "review-a",
            "role": "review-a",
            "backend": "mock",
            "launch_attempt": 2,
            "is_resume": False,
        },
    ))

    orch.run_once(events=[])

    events = log.read_all()
    assert any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("lost_signal_type")
        == "worker.launch_artifact.written"
        for event in events
    )
    retry_run_id = f"run-{fanout_id}-review-a-retry-1"
    assert any(
        event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("run_id") == retry_run_id
        for event in events
    )
    manifest = _manifest(state_dir, fanout_id)
    child = next(c for c in manifest["children"] if c["child_id"] == "review-a")
    assert child["status"] == "dispatched"
    assert child["run_id"] == retry_run_id
    assert [sent[0] for sent in transport.sent][-1] == "review-a"


def test_reader_fanout_retries_provider_turn_closed_without_child_result(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]

    log.append(ZfEvent(
        type="agent.text",
        actor="review-a",
        payload={"text": "acknowledged briefing without reporting a result"},
    ))
    closed = ZfEvent(
        type="provider.turn.closed",
        actor="review-a",
        payload={
            "backend": "codex",
            "provider_event": "task_complete",
            "provider_status": "completed",
            "turn_id": "turn-1",
        },
    )
    log.append(closed)

    orch.run_once(events=[closed])

    events = log.read_all()
    lost = [
        event for event in events
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    ]
    assert len(lost) == 1
    assert lost[0].payload["lost_signal_type"] == "provider.turn.closed"
    assert lost[0].payload["reason"] == "provider_turn_closed_without_child_result"
    assert any(
        event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        and event.payload.get("run_id") == f"run-{fanout_id}-review-a-retry-1"
        for event in events
    )
    assert [sent[0] for sent in transport.sent].count("review-a") == 2


def test_reader_fanout_fails_when_closed_turn_recovery_is_exhausted(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]

    for turn_id in ("turn-1", "turn-2"):
        closed = ZfEvent(
            type="provider.turn.closed",
            actor="review-a",
            payload={
                "backend": "codex",
                "provider_event": "task_complete",
                "provider_status": "completed",
                "turn_id": turn_id,
            },
        )
        log.append(closed)
        orch.run_once(events=[closed])

    failures = [
        event for event in log.read_all()
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    ]
    assert len(failures) == 1
    assert failures[0].payload["failure_class"] == (
        "worker_noop_or_terminal_missing"
    )
    assert failures[0].payload["reason"] == (
        "provider_turn_closed_without_child_result_recovery_exhausted"
    )


def test_reader_fanout_uses_latest_loss_signal_after_intervening_activity(
    tmp_path: Path,
) -> None:
    _state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]

    log.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "review-a",
            "role": "review-a",
            "backend": "codex",
            "launch_attempt": 2,
            "is_resume": True,
        },
    ))
    log.append(ZfEvent(
        type="agent.text",
        actor="review-a",
        payload={"text": "the replacement session acknowledged the briefing"},
    ))
    closed = ZfEvent(
        type="provider.turn.closed",
        actor="review-a",
        payload={
            "backend": "codex",
            "provider_event": "task_complete",
            "provider_status": "completed",
            "turn_id": "turn-after-ack",
        },
    )
    log.append(closed)

    orch.run_once(events=[closed])

    losses = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
    ]
    assert len(losses) == 1
    assert losses[0].payload["lost_signal_type"] == "provider.turn.closed"
    assert [sent[0] for sent in transport.sent].count("review-a") == 2


def test_reader_fanout_synth_fails_when_closed_turn_recovery_is_exhausted(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path, synth=True)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
            },
        )])

    for turn_id in ("turn-1", "turn-2"):
        closed = ZfEvent(
            type="provider.turn.closed",
            actor="review-synth",
            payload={
                "backend": "codex",
                "provider_event": "task_complete",
                "provider_status": "completed",
                "turn_id": turn_id,
            },
        )
        log.append(closed)
        orch.run_once(events=[closed])

    failures = [
        event for event in log.read_all()
        if event.type == "fanout.synth.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("status") == "failed"
    ]
    assert len(failures) == 1
    assert failures[0].payload["failure_class"] == (
        "worker_noop_or_terminal_missing"
    )
    assert failures[0].payload["reason"] == (
        "provider_turn_closed_without_synth_result_recovery_exhausted"
    )
    aggregates = [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(aggregates) == 1
    assert aggregates[0].payload["status"] == "failed"


def test_reader_fanout_redispatches_synth_lost_by_worker_relaunch(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, synth=True)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    for child_id in ("review-a", "review-b"):
        orch.run_once(events=[ZfEvent(
            type="review.approved",
            actor=child_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": child_id,
                "run_id": f"run-{fanout_id}-{child_id}",
                "status": "approved",
            },
        )])
    first = next(
        event for event in log.read_all()
        if event.type == "fanout.synth.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    )
    first_send_count = len(transport.sent)
    launch_ts = datetime.now(timezone.utc) - timedelta(minutes=1)
    stale_usage_ts = launch_ts - timedelta(minutes=30)
    log.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        ts=launch_ts.isoformat(),
        payload={
            "instance_id": "review-synth",
            "role": "review-synth",
            "backend": "mock",
            "launch_attempt": 2,
            "is_resume": True,
        },
    ))
    log.append(ZfEvent(
        type="agent.usage",
        actor="review-synth",
        ts=datetime.now(timezone.utc).isoformat(),
        payload={
            "role": "review-synth",
            "source": "disk_reader",
            "usage_timestamp": stale_usage_ts.isoformat(),
        },
    ))
    orch._active_provider_turn = lambda _role: {"turn_id": "stale-turn"}  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    lost = [
        event for event in events
        if event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "synth"
    ]
    assert len(lost) == 1
    assert lost[0].payload["lost_signal_type"] == "worker.launch_artifact.written"
    retries = [
        event for event in events
        if event.type == "fanout.synth.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(retries) == 2
    assert retries[-1].payload["run_id"] == first.payload["run_id"]
    assert retries[-1].payload["retry_of_event_id"] == first.id
    assert retries[-1].payload["semantic_attempt_consumed"] is False
    assert len(transport.sent) == first_send_count + 1
    assert transport.sent[-1][0] == "review-synth"


def test_reader_fanout_ignores_stale_lock_purge_for_active_child(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    first_dispatch_count = len(transport.sent)

    log.append(ZfEvent(
        type="worker.spawn.stale_session_purged",
        actor="zf-cli",
        payload={
            "instance_id": "review-a",
            "role": "review-a",
            "backend": "mock",
            "session_id": "old-session",
            "claude_json_lock": True,
        },
    ))
    log.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "review-a",
            "role": "review-a",
            "backend": "mock",
            "session_id": "old-session",
        },
    ))
    log.append(ZfEvent(
        type="worker.refresh.triggered",
        actor="review-a",
        payload={"role": "review-a", "reason": "drift", "detail": ""},
    ))

    orch.run_once(events=[])

    events = log.read_all()
    assert not any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        for event in events
    )
    assert len(transport.sent) == first_dispatch_count
    manifest = _manifest(state_dir, fanout_id)
    child = next(c for c in manifest["children"] if c["child_id"] == "review-a")
    assert child["status"] == "dispatched"


def test_reader_fanout_ignores_usage_capture_miss_after_worker_activity(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    first_dispatch_count = len(transport.sent)

    log.append(ZfEvent(
        type="agent.usage",
        actor="review-a",
        payload={
            "role": "review-a",
            "context_usage_ratio": 0.12,
            "source": "test",
        },
    ))
    log.append(ZfEvent(
        type="cost.usage.capture_miss",
        actor="review-a",
        payload={
            "role": "review-a",
            "reason": "claude session file not found by derived path nor uuid glob",
        },
    ))

    orch.run_once(events=[])

    events = log.read_all()
    assert not any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == "review-a"
        for event in events
    )
    assert len(transport.sent) == first_dispatch_count
    manifest = _manifest(state_dir, fanout_id)
    child = next(c for c in manifest["children"] if c["child_id"] == "review-a")
    assert child["status"] == "dispatched"


class _FlakyDispatchTransport(_RecordingTransport):
    def __init__(self, *, fail_times: int, reason: str) -> None:
        super().__init__()
        self._fail_times = fail_times
        self._reason = reason
        self.attempts = 0

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise RuntimeError(self._reason)
        super().send_task(role_name, briefing_path, prompt, context=context)


def _state_with_transport(tmp_path: Path, transport: _RecordingTransport):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _config(), transport)  # type: ignore[arg-type]
    return state_dir, log, orch


def test_reader_run_dispatch_blocked_defers_without_child_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    def blocked_send(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RunDispatchBlocked(
            role_name="review-a",
            reason="run_paused",
            run_id="trace-1",
        )

    monkeypatch.setattr(orch, "_send_scoped_transport_task", blocked_send)
    _start_fanout(orch)

    events = log.read_all()
    assert {
        event.payload.get("child_id")
        for event in events
        if event.type == "fanout.child.dispatch_deferred"
    } == {"review-a", "review-b"}
    assert not any(event.type == "fanout.child.failed" for event in events)


def test_reader_child_infra_dispatch_timeout_defers_then_redispatches(
    tmp_path: Path,
) -> None:
    transport = _FlakyDispatchTransport(
        fail_times=2,
        reason="tmux command timed out: tmux send-keys -t review",
    )
    _state_dir, log, orch = _state_with_transport(tmp_path, transport)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    orch.run_once(events=[trigger])
    for _ in range(3):
        orch.run_once(events=[])

    events = log.read_all()
    deferred = [e for e in events if e.type == "fanout.child.dispatch_deferred"]
    failed = [e for e in events if e.type == "fanout.child.failed"]
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]

    assert deferred
    assert failed == []
    assert {e.payload["child_id"] for e in dispatched} == {"review-a", "review-b"}
    assert {sent[0] for sent in transport.sent} == {"review-a", "review-b"}


def test_reader_child_infra_dispatch_failure_eventually_fails_at_cap(
    tmp_path: Path,
) -> None:
    transport = _FlakyDispatchTransport(
        fail_times=10_000,
        reason="tmux command timed out: tmux send-keys -t review",
    )
    _state_dir, log, orch = _state_with_transport(tmp_path, transport)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    orch.run_once(events=[trigger])
    for _ in range(6):
        orch.run_once(events=[])

    events = log.read_all()
    deferred = [e for e in events if e.type == "fanout.child.dispatch_deferred"]
    failed = [e for e in events if e.type == "fanout.child.failed"]

    assert deferred
    assert failed
    assert any(
        "tmux command timed out" in str(e.payload.get("reason") or "")
        for e in failed
    )
    by_child: dict[str, int] = {}
    for event in deferred:
        child_id = str(event.payload.get("child_id") or "")
        by_child[child_id] = by_child.get(child_id, 0) + 1
    assert by_child
    assert all(count <= 3 for count in by_child.values())


def test_reader_child_noninfra_dispatch_failure_fails_immediately(
    tmp_path: Path,
) -> None:
    transport = _FlakyDispatchTransport(
        fail_times=10_000,
        reason="briefing render exploded",
    )
    _state_dir, log, orch = _state_with_transport(tmp_path, transport)
    trigger = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    orch.run_once(events=[trigger])

    events = log.read_all()
    assert [e for e in events if e.type == "fanout.child.dispatch_deferred"] == []
    failed = [e for e in events if e.type == "fanout.child.failed"]
    assert failed
    assert all(
        "briefing render exploded" in str(e.payload.get("reason") or "")
        for e in failed
    )


# ---------------------------------------------------------------------------
# U20 → LB-4 fail-closed(2026-07-08):审角色报告带判决但零证据。
# 默认 signal 只发观测事件;verification.report_evidence_gate=fail_closed
# 时该 child 并入 malformed-report 失败轨道(reason=report_evidence_missing),
# 由既有 rework/上限链兜底。


def _no_evidence_child_result(fanout_id: str) -> ZfEvent:
    return ZfEvent(
        type="review.approved",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "report": {
                "fanout_id": fanout_id,
                "child_id": "review-a",
                "run_id": f"run-{fanout_id}-review-a",
                "role_instance": "review-a",
                "status": "passed",
                "summary": "Looks good.",
                "findings": [],
                "recommendation": "approve",
            },
        },
    )


def test_report_evidence_signal_default_keeps_child_completed(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[_no_evidence_child_result(fanout_id)])

    types = [e.type for e in log.read_all()]
    assert "stage.report.evidence_missing" in types
    assert "fanout.child.completed" in types
    assert "fanout.child.failed" not in types


def test_report_evidence_fail_closed_fails_child_without_evidence(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    orch.config.verification.report_evidence_gate = "fail_closed"
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    orch.run_once(events=[_no_evidence_child_result(fanout_id)])

    events = log.read_all()
    failed = next(e for e in events if e.type == "fanout.child.failed")
    assert failed.payload["reason"] == "report_evidence_missing"
    assert failed.payload["child_id"] == "review-a"
    types = [e.type for e in events]
    assert "stage.report.evidence_missing" in types
    assert "fanout.child.completed" not in types


def test_report_evidence_fail_closed_passes_child_with_evidence(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    orch.config.verification.report_evidence_gate = "fail_closed"
    _start_fanout(orch)
    fanout_id = next(event.payload["fanout_id"] for event in log.read_all()
                     if event.type == "fanout.started")

    result = _no_evidence_child_result(fanout_id)
    result.payload["report"]["evidence_refs"] = ["artifacts/review/report.json"]
    orch.run_once(events=[result])

    types = [e.type for e in log.read_all()]
    assert "fanout.child.completed" in types
    assert "fanout.child.failed" not in types
    assert "stage.report.evidence_missing" not in types


# ---------------------------------------------------------------------------
# A3 → LB-5(2026-07-08):candidate_ref 缺席的验收读者 briefing 仍必须
# 拿到受审对象语义(target_ref 状态不得作拒因);candidate_ref 在场时
# 保持原 EVALUATE THE CANDIDATE 块。


def test_reader_briefing_without_candidate_ref_gets_subject_guard(
    tmp_path: Path,
):
    _state_dir, _log, transport, orch = _state(tmp_path)
    _start_fanout(orch)  # candidate.ready 只带 pdd_id,无 candidate_ref

    assert transport.sent
    briefing = Path(transport.sent[0][1]).read_text(encoding="utf-8")
    assert "SUBJECT OF REVIEW" in briefing
    assert "MUST NOT be a rejection reason" in briefing
    assert "candidate/F-11111111" in briefing
    assert "Evaluate the target ref as a read-only fanout child." not in briefing


def test_reader_briefing_with_candidate_ref_keeps_candidate_block(
    tmp_path: Path,
):
    _state_dir, _log, transport, orch = _state(tmp_path)
    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "candidate_ref": "candidate/F-11111111",
            "candidate_head_commit": "abc1234",
        },
    )])

    assert transport.sent
    briefing = Path(transport.sent[0][1]).read_text(encoding="utf-8")
    assert "EVALUATE THE CANDIDATE" in briefing
    assert "SUBJECT OF REVIEW" not in briefing


def test_report_projection_preserves_v3_contract_fields():
    """2026-07-08 live 轮实锚:verify 事件带 3 行矩阵(schema 机械验证过),
    但 REPORT_AUDIT_FIELD_KEYS 白名单漏收 v3 契约字段 → children/*/report.json
    投影丢矩阵 → judge 读盘按纪律拒绝,事件真相与磁盘投影分叉烧一轮返工。
    钉住:canonical 报告归一必须携带 v3 读者契约的结构化字段。"""
    result = validate_fanout_report(
        {
            "child_id": "verify-lane-0",
            "status": "passed",
            "summary": "3 of 3 covered",
            "findings": [],
            "recommendation": "approve",
            "requirement_understanding": "PRD asks for a TOC CLI.",
            "requirement_coverage_matrix": [
                {"requirement_id": "AC-1", "status": "covered"},
                {"requirement_id": "AC-2", "status": "covered"},
                {"requirement_id": "AC-3", "status": "covered"},
            ],
            "gap_findings": [],
            "replan_recommendation": "continue",
            "evidence_refs": ["cmd:pytest -> 7 passed"],
        },
        child_id="verify-lane-0",
    )
    assert result.valid
    assert len(result.report["requirement_coverage_matrix"]) == 3
    assert result.report["requirement_understanding"]
    assert result.report["gap_findings"] == []
    assert result.report["replan_recommendation"] == "continue"


def test_report_projection_passes_through_unknown_fields():
    """投影根治(2026-07-08 第四批):枚举白名单决定投影字段与 scheme
    打地鼠同构——改为归一化字段优先、其余原始键一律透传。未知键存活,
    归一化字段不被原始值覆盖(坏 status 仍被归一)。"""
    result = validate_fanout_report(
        {
            "child_id": "verify-lane-0",
            "status": "bogus-status",
            "summary": "x",
            "findings": [],
            "recommendation": "approve",
            "future_contract_field": {"rows": [1, 2]},
            "custom_probe": "probe-x",
        },
        child_id="verify-lane-0",
    )
    # 未知键透传
    assert result.report["future_contract_field"] == {"rows": [1, 2]}
    assert result.report["custom_probe"] == "probe-x"
    # 归一化仍优先:非法 status 被归一(并按既有语义判 invalid)
    assert result.report["status"] == "failed"
    assert not result.valid
