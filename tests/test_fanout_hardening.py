from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.fanout import FanoutContext
from zf.runtime.fanout_replay import record_fanout_fixture, replay_fanout_fixture
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision


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


class _FlakyTransport(_RecordingTransport):
    def __init__(self, *, alive: bool) -> None:
        super().__init__()
        self.alive = alive

    def is_alive(self, role_name):  # noqa: ANN001
        return self.alive


def _config(
    *, timeout_seconds: int = 0, max_retries: int = 0, idle_threshold: float = 300.0
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="review-a",
                backend="mock",
                role_kind="reader",
                skills=["declared-review"],
                stuck_threshold_seconds=idle_threshold,
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-candidate",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                    max_retries=max_retries,
                ),
                timeout_seconds=timeout_seconds,
            ),
        ]),
    )


def _scan_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(
                name="scan-a",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="refactor-scan",
                trigger="refactor.scan.requested",
                topology="fanout_reader",
                roles=["scan-a"],
                target_ref="${target_ref}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="refactor.scan.completed",
                    failure_event="refactor.scan.failed",
                ),
            ),
        ]),
    )


def _state(
    tmp_path: Path,
    *,
    timeout_seconds: int = 0,
    max_retries: int = 0,
    idle_threshold: float = 300.0,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _config(
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            idle_threshold=idle_threshold,
        ),
        transport,  # type: ignore[arg-type]
    )
    return state_dir, log, transport, orch


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _init_git_project(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("test\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    _git(root, "branch", "-M", "main")


def _scan_state(tmp_path: Path):
    _init_git_project(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _scan_config(),
        transport,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return state_dir, log, transport, orch


def _state_with_transport(tmp_path: Path, transport):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _config(), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _start(orch: Orchestrator) -> None:
    orch.run_once(events=[ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", "skills": ["undeclared"]},
    )])


def _fanout_id(log: EventLog) -> str:
    return next(event.payload["fanout_id"] for event in log.read_all()
                if event.type == "fanout.started")


def test_refactor_scan_rejects_file_path_target_ref_before_dispatch(tmp_path: Path):
    _state_dir, log, transport, orch = _scan_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="refactor.scan.requested",
        actor="operator",
        correlation_id="trace-scan",
        payload={
            "target_ref": "src/game-core.js",
            "pdd_id": "CJMIN-R37",
        },
    )])

    events = log.read_all()
    failed = [event for event in events if event.type == "refactor.scan.failed"]
    assert failed
    assert failed[-1].payload["reason"] == "target_ref_not_a_git_ref"
    assert failed[-1].payload["failure_classification"] == "operator_config"
    assert any(
        event.type == "fanout.cancelled"
        and event.payload.get("reason") == "target_ref_not_a_git_ref"
        for event in events
    )
    assert not [event for event in events if event.type == "fanout.started"]
    assert transport.sent == []


def test_refactor_scan_accepts_valid_git_target_ref(tmp_path: Path):
    _state_dir, log, transport, orch = _scan_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="refactor.scan.requested",
        actor="operator",
        correlation_id="trace-scan",
        payload={
            "target_ref": "main",
            "pdd_id": "CJMIN-R37",
        },
    )])

    events = log.read_all()
    assert any(event.type == "fanout.started" for event in events)
    assert not [event for event in events if event.type == "refactor.scan.failed"]
    assert len(transport.sent) == 1


def test_reader_fanout_defers_dead_worker_dispatch(tmp_path: Path):
    transport = _FlakyTransport(alive=False)
    _state_dir, log, _transport, orch = _state_with_transport(tmp_path, transport)
    respawns: list[str] = []

    def _respawn(role):  # noqa: ANN001
        respawns.append(role.instance_id)
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="test respawn",
        )

    orch._respawn_instance = _respawn  # type: ignore[method-assign]

    _start(orch)

    events = log.read_all()
    assert respawns == ["review-a"]
    assert any(event.type == "fanout.child.dispatch_deferred" for event in events)
    assert not any(event.type == "fanout.child.dispatched" for event in events)
    assert not any(event.type == "fanout.child.failed" for event in events)


def test_pending_reader_fanout_dispatches_after_worker_recovers(tmp_path: Path):
    transport = _FlakyTransport(alive=False)
    _state_dir, log, _transport, orch = _state_with_transport(tmp_path, transport)
    orch._respawn_instance = lambda role: OrchestratorDecision(  # type: ignore[method-assign]
        action="respawn",
        role=role.instance_id,
        reason="test respawn",
    )
    _start(orch)
    fanout_id = _fanout_id(log)
    transport.alive = True

    orch.run_once(events=[])

    events = log.read_all()
    dispatched = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(dispatched) == 1
    assert dispatched[0].payload["role_instance"] == "review-a"
    assert not [
        event for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == fanout_id
    ]


def test_timeout_emits_timed_out_and_child_failure(tmp_path: Path):
    _state_dir, log, _transport, orch = _state(tmp_path, timeout_seconds=1)
    _start(orch)
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    assert any(event.type == "fanout.child.failed" for event in events)
    timed_out = [event for event in events if event.type == "fanout.timed_out"]
    assert len(timed_out) == 1
    assert timed_out[0].payload["pending_children"] == ["review-a"]
    aggregate = [
        event for event in events if event.type == "fanout.aggregate.completed"
    ]
    assert aggregate
    assert aggregate[-1].payload["status"] == "failed"
    assert aggregate[-1].payload["reason"] == "timeout"
    rejected = [event for event in events if event.type == "review.rejected"]
    assert rejected
    assert rejected[-1].payload["reason"] == "timeout"
    assert rejected[-1].payload["failed_children"] == ["review-a"]


def test_timeout_skips_superseded_fanout_instance(tmp_path: Path):
    _state_dir, log, _transport, orch = _state(tmp_path, timeout_seconds=1)
    _start(orch)
    started = next(event for event in log.read_all()
                   if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    replacement_payload = dict(started.payload)
    replacement_payload["fanout_id"] = "fanout-review-current"
    replacement_payload["trigger_event_id"] = "candidate-ready-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload=replacement_payload,
        correlation_id="trace-2",
    ))
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert not [
        event for event in events
        if event.type == "fanout.timed_out"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert not [
        event for event in events
        if event.type in {"fanout.aggregate.completed", "review.rejected"}
        and event.payload.get("fanout_id") == fanout_id
    ]


def test_legacy_timed_out_manifest_backfills_aggregate_failure(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, timeout_seconds=1)
    _start(orch)
    fanout_id = _fanout_id(log)
    EventWriter(log).append(ZfEvent(
        type="fanout.timed_out",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "review-candidate",
            "pending_children": ["review-a"],
            "timeout_seconds": 1,
        },
        correlation_id="trace-1",
    ))

    orch.run_once(events=[])

    events = log.read_all()
    aggregate = [
        event for event in events if event.type == "fanout.aggregate.completed"
    ]
    assert aggregate
    assert aggregate[-1].payload["status"] == "failed"
    rejected = [event for event in events if event.type == "review.rejected"]
    assert rejected
    assert rejected[-1].payload["reason"] == "timeout"
    assert rejected[-1].causation_id == aggregate[-1].id


def test_timeout_times_out_child_without_dispatch_event(tmp_path: Path):
    # An assigned/queued child that never got a dispatch event (affinity overflow
    # whose lane never freed) must still time out by fanout age, or a wait_for_all
    # aggregate strands forever waiting on it.
    state_dir, log, _transport, orch = _state(tmp_path, timeout_seconds=1)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest_path = state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["children"].append({
        "child_id": "review-overflow",
        "role_instance": "review-a",
        "status": "dispatched",
        "run_id": "",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    failed_ids = [
        event.payload["child_id"] for event in events
        if event.type == "fanout.child.failed"
    ]
    assert "review-overflow" in failed_ids
    assert any(
        "review-overflow" in event.payload.get("pending_children", [])
        for event in events if event.type == "fanout.timed_out"
    )


def test_retry_keeps_fanout_and_child_id_but_changes_run_id(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    _state_dir, log, transport, orch = _state(
        tmp_path,
        timeout_seconds=1,
        max_retries=1,
    )
    _start(orch)
    fanout_id = _fanout_id(log)
    first_dispatch = next(event for event in log.read_all()
                          if event.type == "fanout.child.dispatched")
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    dispatches = [event for event in log.read_all()
                  if event.type == "fanout.child.dispatched"]
    assert len(dispatches) == 2
    retry = dispatches[-1]
    assert retry.payload["fanout_id"] == fanout_id
    assert retry.payload["child_id"] == first_dispatch.payload["child_id"]
    assert retry.payload["run_id"] != first_dispatch.payload["run_id"]
    assert retry.payload["retry_of_run_id"] == first_dispatch.payload["run_id"]
    retry_briefing = transport.sent[-1][1].read_text(encoding="utf-8")
    assert "Aggregate contract:" in retry_briefing
    assert "uv --project /repo run zf emit workflow.child.completed" in retry_briefing
    assert "zf emit workflow.child.completed" in retry_briefing
    assert "Do not emit the aggregate success/failure event directly" in retry_briefing
    assert not any(event.type == "fanout.timed_out" for event in log.read_all())


def test_idle_child_caught_before_stage_timeout(tmp_path: Path):
    # R15: a reader engaged, hit a transient backend API error, and froze. The
    # stage budget was 1800s, so it stranded the fanout for ~26min before the
    # budget would have fired. A child idle past its role stuck_threshold must be
    # timed out on the liveness signal alone, long before the stage budget.
    _state_dir, log, _transport, orch = _state(
        tmp_path,
        timeout_seconds=10_000,   # stage budget NOT breached
        max_retries=0,
        idle_threshold=1,         # but no sign of life for >1s
    )
    _start(orch)
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    failed = [event for event in events if event.type == "fanout.child.failed"]
    assert failed, "idle child should have been failed before the stage budget"
    assert failed[-1].payload["reason"] == "idle"
    # the stage budget was nowhere near elapsed — this fired on liveness only
    assert any(event.type == "fanout.timed_out" for event in events)


def test_idle_child_retried_when_retries_available(tmp_path: Path):
    # An idle child with retries left is re-dispatched (re-sending the briefing
    # recovers a transient API blip), not failed.
    _state_dir, log, transport, orch = _state(
        tmp_path,
        timeout_seconds=10_000,
        max_retries=1,
        idle_threshold=1,
    )
    _start(orch)
    first_dispatch = next(event for event in log.read_all()
                          if event.type == "fanout.child.dispatched")
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orch.run_once(events=[])

    dispatches = [event for event in log.read_all()
                  if event.type == "fanout.child.dispatched"]
    assert len(dispatches) == 2
    assert dispatches[-1].payload["child_id"] == first_dispatch.payload["child_id"]
    assert dispatches[-1].payload["retry_of_run_id"] == first_dispatch.payload["run_id"]
    assert not any(event.type == "fanout.timed_out" for event in log.read_all())


def test_active_child_within_idle_window_not_killed(tmp_path: Path):
    # The inverse safety property: a child still emitting progress (agent.usage)
    # within its idle window must NOT be killed, even well past where a frozen
    # child would have been — otherwise a legitimately slow reader gets reaped.
    base = time.time()
    _state_dir, log, transport, orch = _state(
        tmp_path,
        timeout_seconds=10_000,
        max_retries=0,
        idle_threshold=30,
    )
    _start(orch)
    dispatch_count = len([e for e in log.read_all()
                          if e.type == "fanout.child.dispatched"])
    # a recent sign of life: the child (actor) emitted usage 10s before "now"
    EventWriter(log).append(ZfEvent(
        type="agent.usage",
        actor="review-a",
        ts=datetime.fromtimestamp(base + 40, timezone.utc).isoformat(),
        payload={"task_id": "t", "usage": {}},
        correlation_id="trace-1",
    ))
    orch._now = lambda: base + 50  # type: ignore[method-assign]

    orch.run_once(events=[])

    events = log.read_all()
    assert not any(event.type == "fanout.child.failed" for event in events)
    assert not any(event.type == "fanout.timed_out" for event in events)
    # no retry either — the child is alive, just slow
    assert len([e for e in events
                if e.type == "fanout.child.dispatched"]) == dispatch_count


def test_reader_output_outside_fanout_root_is_rejected(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _start(orch)
    fanout_id = _fanout_id(log)

    orch.run_once(events=[ZfEvent(
        type="review.approved",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "approved",
            "output_path": str(state_dir.parent / "outside.json"),
        },
    )])

    failed = next(event for event in log.read_all()
                  if event.type == "fanout.child.failed")
    assert "outside allowed root" in failed.payload["reason"]


def test_fanout_child_skills_come_from_role_config_only(tmp_path: Path):
    _state_dir, log, _transport, orch = _state(tmp_path)

    _start(orch)

    dispatched = next(event for event in log.read_all()
                      if event.type == "fanout.child.dispatched")
    assert dispatched.payload["skills"] == ["declared-review"]


def test_fanout_replay_fixture_matches_and_reports_structured_diff(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    context = FanoutContext.create(
        stage_id="review-candidate",
        topology="fanout_reader",
        trace_id="trace-1",
        trigger_event_id="evt-1",
        target_ref="candidate/F-1",
        role_instances=["review-a"],
    )
    writer.append(context.started_event())
    writer.append(context.child_dispatched_event(
        context.expected_children[0],
        run_id="run-1",
    ))
    writer.append(ZfEvent(
        type="fanout.child.completed",
        payload={
            "fanout_id": context.fanout_id,
            "child_id": "review-a",
            "run_id": "run-1",
            "status": "completed",
        },
    ))

    fixture_path = tmp_path / "fanout-fixture.json"
    fixture = record_fanout_fixture(
        event_log=log,
        state_dir=state_dir,
        fanout_id=context.fanout_id,
        output_path=fixture_path,
    )
    assert fixture["events"]
    assert replay_fanout_fixture(fixture_path)["status"] == "matched"

    fixture["expected_manifest"]["children"][0]["status"] = "failed"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    mismatch = replay_fanout_fixture(fixture_path)
    assert mismatch["status"] == "mismatch"
    assert mismatch["diff"][0]["path"].startswith("$.children")


def _synth_config(*, timeout_seconds: int = 1) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-synth", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-candidate",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    synth_role="review-synth",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
                timeout_seconds=timeout_seconds,
            ),
        ]),
    )


def test_synth_timeout_fails_aggregate_instead_of_hanging(tmp_path: Path):
    # R23: children all terminal + synth dispatched but never completing
    # (pane stuck on an interactive prompt) hung the fanout 6h+ past its
    # stage budget — the timeout sweep only walked manifest children. The
    # synth phase must consume the same stage budget and converge the
    # aggregate to its failure event.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _synth_config(), transport)  # type: ignore[arg-type]
    _start(orch)
    fanout_id = _fanout_id(log)

    # child completes → synth is dispatched, manifest synth.status=dispatched
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
        },
    )])
    events = log.read_all()
    assert any(event.type == "fanout.synth.dispatched" for event in events)
    assert not [e for e in events if e.type == "review.rejected"]

    # synth never completes; stage budget elapses
    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]
    orch.run_once(events=[])

    events = log.read_all()
    synth_completed = [
        event for event in events
        if event.type == "fanout.synth.completed"
        and event.payload.get("reason") == "synth_timeout"
    ]
    assert synth_completed, "synth timeout must produce a terminal synth event"
    aggregate = [
        event for event in events if event.type == "fanout.aggregate.completed"
    ]
    assert aggregate and aggregate[-1].payload["status"] == "failed"
    assert [e for e in events if e.type == "review.rejected"], (
        "aggregate must converge to its failure event instead of hanging"
    )


def _synth_budget_config(
    *,
    timeout_seconds: int = 3600,
    review_strategy: str = "",
    synth_timeout_seconds: int = 0,
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-b", backend="mock", role_kind="reader"),
            RoleConfig(name="review-synth", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-candidate",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a", "review-b"],
                target_ref="candidate/${pdd_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    synth_role="review-synth",
                    success_event="review.approved",
                    failure_event="review.rejected",
                    review_strategy=review_strategy,
                    synth_timeout_seconds=synth_timeout_seconds,
                ),
                timeout_seconds=timeout_seconds,
            ),
        ]),
    )


def _drive_children_terminal(orch, log, *, b_status: str) -> str:
    fanout_id = _fanout_id(log)
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
        },
    )])
    orch.run_once(events=[ZfEvent(
        type=(
            "workflow.child.completed"
            if b_status == "completed" else "workflow.child.failed"
        ),
        actor="review-b",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-b",
            "run_id": f"run-{fanout_id}-review-b",
            "status": b_status,
        },
    )])
    return fanout_id


def test_synth_decided_verdict_short_circuits_wait(tmp_path: Path):
    # B3 (R25 ISSUE-005): 5/6-failed round under one-rejects strategy sat
    # the full 40min synth budget although the verdict could not flip.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        _synth_budget_config(
            timeout_seconds=3600,
            review_strategy="all_approve_or_one_rejects",
        ),
        _RecordingTransport(),  # type: ignore[arg-type]
    )
    _start(orch)
    _drive_children_terminal(orch, log, b_status="failed")
    assert any(
        e.type == "fanout.synth.dispatched" for e in log.read_all()
    )

    # decided verdict → 600s budget, NOT the 3600s stage budget
    orch._now = lambda: time.time() + 700  # type: ignore[method-assign]
    orch.run_once(events=[])
    events = log.read_all()
    assert [
        e for e in events
        if e.type == "fanout.synth.completed"
        and e.payload.get("reason") == "synth_timeout"
    ], "decided verdict must time the synth out at the short budget"


def test_synth_timeout_seconds_config_overrides_stage_budget(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        _synth_budget_config(timeout_seconds=3600, synth_timeout_seconds=5),
        _RecordingTransport(),  # type: ignore[arg-type]
    )
    _start(orch)
    _drive_children_terminal(orch, log, b_status="completed")

    orch._now = lambda: time.time() + 10  # type: ignore[method-assign]
    orch.run_once(events=[])
    events = log.read_all()
    assert [
        e for e in events
        if e.type == "fanout.synth.completed"
        and e.payload.get("reason") == "synth_timeout"
    ], "configured synth_timeout_seconds must override the stage budget"
