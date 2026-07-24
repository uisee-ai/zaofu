"""cj-min-shaped refactor pipeline smoke — full chain + self-healing loops.

R20/R21/R22 each burned hours of live LLM run before hitting a KERNEL handoff
bug (candidate->review no-retry; prose contract scope rejecting every writer
ref; escalated rework re-armed into a livelock). Every one of those bugs lives
in the deterministic kernel, not in the workers — so this smoke drives the real
``Orchestrator`` through the full cj-min stage topology with mock roles and
scripted worker outputs, in seconds, no tmux, no LLM:

  refactor.scan.requested -> scan fanout (readers, artifact projection)
    -> zaofu.refactor.review.ready -> plan fanout (synth reader)
    -> zaofu.refactor.plan.ready (kernel-projected task_map.json)
    -> [driver emits task_map.ready = the orchestrator-LLM hop]
    -> impl fanout (affinity writers, real git workdirs + commits)
    -> ref handoff (allowed_paths scope mint — the R21 fix)
    -> candidate.ready -> review fanout (affinity readers) -> review.approved
    -> verify fanout -> test.passed -> judge fanout -> judge.passed

plus the self-healing loops the runs depend on:
  integration.failed xN -> bounded rework -> Orchestrator triage -> human.escalate
    -> spurious fresh task_map.ready is QUARANTINED (R22 no-livelock fix)
    -> operator-authorized task_map.ready re-arms
  autoresearch repair.dispatch_requested -> dispatch consumer -> dispatched
    (isolated worktree + briefing; the R20-B2 closed loop, spawn=False)

Run before every live R round: pytest tests/e2e/test_refactor_pipeline_smoke.py
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    GitIsolationConfig,
    GoalConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    WorkflowStrictTriggersConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

PDD = "CJMIN-SMOKE-001"

TASK_MAP_TASKS = [
    {
        "task_id": "T-CORE",
        "scope": "pi-core: 核心契约(prose label,不能用作 scope)",
        "affinity_tag": "pi-core",
        "allowed_paths": ["core.txt"],
        "payload": {"instruction": "Create core.txt."},
    },
    {
        "task_id": "T-GW",
        "scope": "gateway: 网关适配(prose label)",
        "affinity_tag": "gateway",
        "allowed_paths": ["gw.txt"],
        "payload": {"instruction": "Create gw.txt."},
    },
]


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


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "smoke@example.com")
    _git(root, "config", "user.name", "Smoke")
    (root / "README.md").write_text("ref\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _reader(name: str) -> RoleConfig:
    return RoleConfig(name=name, instance_id=name, backend="mock", role_kind="reader")


def _writer(name: str) -> RoleConfig:
    return RoleConfig(
        name=name, instance_id=name, backend="mock", role_kind="writer",
        publishes=["dev.build.done", "dev.blocked"],
    )


def _config(state_dir: Path) -> ZfConfig:
    """Mirror the cj-min zf.yaml topology at 2-lane scale."""
    lanes = WorkflowAffinityLaneProfileConfig(
        affinity_key="affinity_tag",
        lanes=[
            WorkflowAffinityLaneConfig(
                id="lane0", impl="dev-1", review="review-1", verify="verify-1",
            ),
            WorkflowAffinityLaneConfig(
                id="lane1", impl="dev-2", review="review-2", verify="verify-2",
            ),
        ],
    )

    def affinity(slot: str) -> FanoutAssignmentConfig:
        return FanoutAssignmentConfig(
            strategy="affinity_stage_slots", lane_profile="cj-2", stage_slot=slot,
        )

    stages = [
        WorkflowStageConfig(
            id="scan", trigger="refactor.scan.requested", topology="fanout_reader",
            roles=["scan-1", "scan-2"], target_ref="${target_ref}",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="refactor.scan.completed",
                child_failure_event="refactor.scan.failed",
                success_event="zaofu.refactor.review.ready",
                failure_event="zaofu.refactor.plan.blocked",
            ),
        ),
        WorkflowStageConfig(
            id="plan", trigger="zaofu.refactor.review.ready", topology="fanout_reader",
            roles=["plan-worker"], target_ref="${target_ref}",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="refactor.plan.child.completed",
                child_failure_event="refactor.plan.child.failed",
                success_event="zaofu.refactor.plan.ready",
                failure_event="zaofu.refactor.plan.blocked",
                synth_role="plan-critic",
            ),
        ),
        WorkflowStageConfig(
            id="impl", trigger="task_map.ready", topology="fanout_writer_scoped",
            synthesize_canonical_tasks=True,
            task_map="${task_map_ref}",
            assignment=affinity("impl"),
            aggregate=FanoutAggregateConfig(
                mode="candidate_integration",
                success_event="candidate.ready",
                failure_event="integration.failed",
            ),
        ),
        WorkflowStageConfig(
            id="review", trigger="candidate.ready", topology="fanout_reader",
            assignment=affinity("review"), target_ref="${candidate_ref}",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                review_strategy="all_approve_or_one_rejects",
                child_success_event="review.child.completed",
                child_failure_event="review.child.failed",
                success_event="review.approved",
                failure_event="review.rejected",
            ),
        ),
        WorkflowStageConfig(
            id="verify", trigger="review.approved", topology="fanout_reader",
            assignment=affinity("verify"), target_ref="${target_ref}",
            aggregate=FanoutAggregateConfig(
                mode="any_failed_fail",
                child_success_event="verify.child.completed",
                child_failure_event="verify.child.failed",
                success_event="test.passed",
                failure_event="test.failed",
            ),
        ),
        WorkflowStageConfig(
            id="judge", trigger="test.passed", topology="fanout_reader",
            roles=["judge-1"], target_ref="${target_ref}",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="judge.child.completed",
                child_failure_event="judge.child.failed",
                success_event="judge.passed",
                failure_event="judge.failed",
            ),
        ),
    ]
    return ZfConfig(
        project=ProjectConfig(name="cj-smoke", state_dir=str(state_dir)),
        roles=[
            _reader("scan-1"), _reader("scan-2"), _reader("plan-worker"),
            _reader("plan-critic"),
            _writer("dev-1"), _writer("dev-2"),
            _reader("review-1"), _reader("review-2"),
            _reader("verify-1"), _reader("verify-2"),
            _reader("judge-1"),
            RoleConfig(
                name="orchestrator",
                instance_id="orchestrator",
                backend="mock",
                role_kind="reader",
                triggers=["orchestrator.rework.triage.requested"],
                publishes=["orchestrator.rework.triage.recorded"],
            ),
        ],
        workflow=WorkflowConfig(
            stages=stages,
            affinity_lanes={"cj-2": lanes},
            strict_triggers=WorkflowStrictTriggersConfig(rework_attempts_gte=2),
        ),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
        goal=GoalConfig(enabled=True),
    )


def _state(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _events(log: EventLog) -> list[ZfEvent]:
    return log.read_all()


def _last_of(log: EventLog, etype: str) -> ZfEvent:
    matches = [e for e in _events(log) if e.type == etype]
    assert matches, f"expected {etype} in event log"
    return matches[-1]


def _dispatched_children(log: EventLog, fanout_id: str) -> list[ZfEvent]:
    return [
        e for e in _events(log)
        if e.type == "fanout.child.dispatched"
        and e.payload.get("fanout_id") == fanout_id
    ]


def _stage_fanout_id(log: EventLog, stage_id: str) -> str:
    started = [
        e for e in _events(log)
        if e.type == "fanout.started" and e.payload.get("stage_id") == stage_id
    ]
    assert started, f"stage {stage_id} fanout never started"
    return started[-1].payload["fanout_id"]


def _complete_reader_children(
    log: EventLog, orch: Orchestrator, *, fanout_id: str, child_event: str,
    extra: dict | None = None,
) -> None:
    completions = []
    for child in _dispatched_children(log, fanout_id):
        completions.append(ZfEvent(
            type=child_event,
            actor=str(child.payload.get("role_instance") or "mock"),
            correlation_id=child.correlation_id,
            payload={
                "fanout_id": fanout_id,
                "child_id": child.payload["child_id"],
                "run_id": child.payload.get("run_id", ""),
                "status": "completed",
                "summary": "smoke ok",
                "evidence_refs": ["smoke://evidence"],
                "git_refs": {"source_branch": "main"},
                **(extra or {}),
            },
        ))
    assert completions, f"no dispatched children for fanout {fanout_id}"
    orch.run_once(events=completions)


def _consume_synth_required_reads(state_dir: Path, dispatched: ZfEvent) -> None:
    descriptor = dispatched.payload["attempt_source_manifest"]
    manifest = hydrate_sidecar_ref(state_dir, descriptor).payload
    assert isinstance(manifest, dict)
    for requirement in dispatched.payload["required_reads"]:
        read_attempt_artifact(
            state_dir,
            manifest=manifest,
            source_id=requirement["source_id"],
            artifact_id=requirement["artifact_id"],
            json_path=requirement.get("json_path", "$"),
            max_items=int(requirement.get("max_items") or 0),
            max_chars=int(requirement.get("max_chars") or 0),
        )


def _complete_plan_synth(
    state_dir: Path,
    log: EventLog,
    orch: Orchestrator,
    *,
    fanout_id: str,
    report: dict,
) -> ZfEvent:
    dispatched = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.synth.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    )
    assert dispatched.payload["output_profile_id"] == "plan-synth"
    assert dispatched.payload["result_protocol_mode"] == "blocking"
    assert dispatched.payload["plan_revision"].startswith("plan-r")
    assert {
        item["source_id"] for item in dispatched.payload["required_reads"]
    } >= {"plan-synth-contract", "child-result-plan-worker"}
    briefing = Path(dispatched.payload["briefing_path"]).read_text(encoding="utf-8")
    assert "Child reports:\n```json" not in briefing
    assert "canonical required inputs" in briefing
    command = briefing.split(
        "When finished, emit exactly one fanout.synth.completed event with "
        "the runtime state dir explicitly:\n```bash\n",
        1,
    )[1].split("\n```", 1)[0]
    command_lines = command.splitlines()
    command_args = shlex.split(command_lines[0])
    if "--result-file" in command_args:
        payload_file_index = command_args.index("--result-file") + 1
        payload_file = command_args[payload_file_index]
    elif "--payload-file" in command_args:
        payload_file_index = command_args.index("--payload-file") + 1
        payload_file = command_args[payload_file_index]
    else:
        payload_file = next(
            arg.split("=", 1)[1]
            for arg in command_args
            if arg.startswith("--payload-file=")
            or arg.startswith("--result-file=")
        )
    if payload_file == "-":
        delimiter = command_lines[0].rsplit("<<'", 1)[1].removesuffix("'")
        assert command_lines[-1] == delimiter
        completion_payload = json.loads("\n".join(command_lines[1:-1]))
    else:
        completion_payload = json.loads(Path(payload_file).read_text(encoding="utf-8"))
    assert completion_payload["child_id"] == "synth"
    _consume_synth_required_reads(state_dir, dispatched)
    completion_payload.update({
        "status": "completed",
        "recommendation": "approve",
        "summary": "plan synthesis passed",
    })
    completion_payload["report"].update({
        "child_id": "synth",
        "status": "passed",
        "recommendation": "approve",
        "summary": "plan synthesis passed",
        **report,
    })
    completed = ZfEvent(
        type="fanout.synth.completed",
        actor="plan-critic",
        correlation_id=dispatched.correlation_id,
        payload=completion_payload,
    )
    log.append(completed)
    orch.run_once(events=[completed])
    return completed


def test_full_refactor_chain_reaches_judge_passed(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    goal_started = ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id="trace-smoke",
        payload={
            "run_id": "trace-smoke",
            "goal_id": PDD,
            "objective": "deliver the full refactor smoke product",
        },
    )
    log.append(goal_started)

    # 1. scan: refactor.scan.requested -> reader fanout -> review.ready
    scan_trigger = ZfEvent(
        type="refactor.scan.requested", actor="orchestrator",
        correlation_id="trace-smoke",
        payload={"pdd_id": PDD, "target_ref": "main", "objective": "smoke"},
    )
    log.append(scan_trigger)
    orch.run_once(events=[scan_trigger])
    scan_fanout = _stage_fanout_id(log, "scan")
    assert len(_dispatched_children(log, scan_fanout)) == 2
    _complete_reader_children(
        log, orch, fanout_id=scan_fanout, child_event="refactor.scan.completed",
        extra={
            "report": {
                "findings": [{
                    "severity": "info",
                    "category": "contract",
                    "path": "README.md",
                    "message": "契约面冻结完成",
                }],
                "recommendation": "approve",
            },
            "coverage_matrix": [{"area": "cli", "status": "covered"}],
        },
    )
    review_ready = _last_of(log, "zaofu.refactor.review.ready")
    assert review_ready.payload.get("artifact_gate") == "passed"

    # 2. plan: review.ready -> synth fanout -> plan.ready (projected task_map)
    orch.run_once(events=[review_ready])
    plan_fanout = _stage_fanout_id(log, "plan")
    plan_result = {
        "review_artifact_ref": review_ready.payload["review_artifact_ref"],
        "refactor_plan_md": "# cj-min smoke 重构计划\n\n两条 lane。",
        "plan_intent": "smoke",
        "task_map": {"tasks": TASK_MAP_TASKS},
        "gates": ["python3 -m compileall -q ."],
        "risk_register": [],
        "backlog_candidates": [],
    }
    _complete_reader_children(
        log, orch, fanout_id=plan_fanout, child_event="refactor.plan.child.completed",
        extra=plan_result,
    )
    _complete_plan_synth(
        state_dir,
        log,
        orch,
        fanout_id=plan_fanout,
        report=plan_result,
    )
    plan_ready = _last_of(log, "zaofu.refactor.plan.ready")
    assert plan_ready.payload.get("artifact_gate") == "passed"
    task_map_ref = plan_ready.payload["task_map_ref"]
    assert Path(task_map_ref).exists()

    # 3. the LLM hop (orchestrator agent turns plan.ready into task_map.ready)
    task_map_ready = ZfEvent(
        type="task_map.ready", actor="orchestrator",
        correlation_id="trace-smoke",
        payload={"pdd_id": PDD, "task_map_ref": task_map_ref},
    )
    log.append(task_map_ready)
    orch.run_once(events=[task_map_ready])

    # 4. impl: affinity writer fanout dispatched with real git workdirs
    impl_fanout = _stage_fanout_id(log, "impl")
    impl_children = _dispatched_children(log, impl_fanout)
    assert len(impl_children) == 2
    manifest = json.loads(
        (state_dir / "fanouts" / impl_fanout / "manifest.json").read_text(
            encoding="utf-8",
        )
    )
    # R21 fix live: contract scope minted from allowed_paths globs, not prose
    from zf.core.task.store import TaskStore

    store = TaskStore(state_dir / "kanban.json")
    assert store.get("T-CORE").contract.scope == ["core.txt"]
    assert store.get("T-GW").contract.scope == ["gw.txt"]

    # 5. writers do scoped work: real commit inside allowed_paths + build.done
    build_events = []
    for task_id, file_name in (("T-CORE", "core.txt"), ("T-GW", "gw.txt")):
        child = next(c for c in manifest["children"] if c["task_id"] == task_id)
        workdir = Path(child["workdir"])
        (workdir / file_name).write_text(f"{task_id}\n", encoding="utf-8")
        _git(workdir, "add", file_name)
        _git(workdir, "commit", "-q", "-m", f"feat: {task_id}")
        build_events.append(ZfEvent(
            type="dev.build.done",
            actor=child["role_instance"],
            task_id=task_id,
            correlation_id="trace-smoke",
            payload={
                "fanout_id": impl_fanout,
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "pdd_id": PDD,
                "source_commit": _git(workdir, "rev-parse", "HEAD"),
                "source_branch": child["source_branch"],
                "workdir": str(workdir),
            },
        ))
    for be in build_events:
        log.append(be)
    orch.run_once(events=build_events)
    orch.run_once(events=[])  # let reconcile/aggregate sweeps settle

    events = _events(log)
    assert len([
        event for event in events
        if event.type == "workflow.call.result.reported"
        and event.payload.get("adapter_id") == "plan-synthesis-result-v1"
    ]) == 1
    # R21 regression gate: zero scope rejections on the live kernel path
    assert not [e for e in events if e.type == "task.ref.rejected"], (
        "writer ref handoff rejected — scope/contract regression"
    )
    candidate_ready = _last_of(log, "candidate.ready")

    # 6. review: affinity reader fanout over the candidate -> approved
    orch.run_once(events=[candidate_ready])
    review_fanout = _stage_fanout_id(log, "review")
    assert len(_dispatched_children(log, review_fanout)) == 2
    _complete_reader_children(
        log, orch, fanout_id=review_fanout, child_event="review.child.completed",
    )
    review_approved = _last_of(log, "review.approved")

    # 7. verify -> test.passed
    orch.run_once(events=[review_approved])
    verify_fanout = _stage_fanout_id(log, "verify")
    _complete_reader_children(
        log, orch, fanout_id=verify_fanout, child_event="verify.child.completed",
    )
    test_passed = _last_of(log, "test.passed")

    # 8. judge -> judge.passed: full chain green
    orch.run_once(events=[test_passed])
    judge_fanout = _stage_fanout_id(log, "judge")
    _complete_reader_children(
        log, orch, fanout_id=judge_fanout, child_event="judge.child.completed",
    )
    judge_passed = _last_of(log, "judge.passed")
    orch.run_once(events=[judge_passed])
    _last_of(log, "run.goal.completed")

    events = _events(log)
    assert not [e for e in events if e.type == "fanout.cancelled"]
    assert not [e for e in events if e.type == "integration.failed"]
    assert not [e for e in events if e.type == "task.contract.invalid"]
    assert len([e for e in events if e.type == "run.goal.completed"]) == 1


def _write_task_map(state_dir: Path) -> str:
    task_map = state_dir / "artifacts" / PDD / "task_map.json"
    task_map.parent.mkdir(parents=True, exist_ok=True)
    task_map.write_text(
        json.dumps({"tasks": TASK_MAP_TASKS}), encoding="utf-8",
    )
    return str(task_map)


def test_rework_cap_escalates_then_quarantines_spurious_taskmap(tmp_path: Path):
    """R22 loop: capped integration recovery is triaged before escalation.

    A spurious fresh task_map.ready must then be quarantined (no impl re-arm)
    until an operator-authorized task_map.ready lifts it.
    """
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_task_map(state_dir)
    writer = orch.event_writer

    def impl_fanouts_triggered_by(event_id: str) -> list[ZfEvent]:
        return [
            e for e in _events(log)
            if e.type == "fanout.started"
            and e.payload.get("stage_id") == "impl"
            and e.payload.get("trigger_event_id") == event_id
        ]

    def fail_integration() -> None:
        writer.append(ZfEvent(
            type="integration.failed", actor="zf-cli",
            correlation_id="trace-smoke",
            payload={"pdd_id": PDD, "reason": "timeout", "status": "failed"},
        ))

    # attempt 1 + 2: bounded recoveries carrying canonical rework lineage.
    # Once an implementation fanout exists, integration-only recovery avoids
    # reopening completed writers and records workflow.resume.applied instead.
    for expected_attempt in (1, 2):
        fail_integration()
        orch._run_candidate_rework_sweep()
        recoveries = [
            event for event in _events(log)
            if event.type in {"task_map.ready", "workflow.resume.applied"}
            and event.payload.get("rework_of")
        ]
        retrig = recoveries[-1]
        assert retrig.payload.get("rework_attempt") == expected_attempt
        assert retrig.payload.get("rework_of")

    # attempt 3: the no-progress breaker receives the first bounded operation.
    fail_integration()
    orch._run_candidate_rework_sweep()
    _last_of(log, "run.manager.autoresearch.requested")

    # Candidate exhaustion is not a direct human decision. Wait for the
    # explicit Orchestrator advisory request, then return a human recommendation.
    requests: list[ZfEvent] = []
    for _ in range(5):
        orch._run_candidate_rework_sweep()
        requests = [
            event for event in _events(log)
            if event.type == "orchestrator.rework.triage.requested"
        ]
        if requests:
            break
    assert requests, "candidate cap must request semantic triage"
    request = requests[-1]
    writer.append(ZfEvent(
        type="orchestrator.rework.triage.recorded",
        actor="orchestrator",
        correlation_id=request.correlation_id,
        payload={
            "request_id": request.payload["request_id"],
            "recommended_action": "human",
            "guidance": "integration recovery remained unsatisfied",
        },
    ))
    for _ in range(5):
        orch._run_candidate_rework_sweep()
        if any(event.type == "human.escalate" for event in _events(log)):
            break
    _last_of(log, "human.escalate")
    _last_of(log, "owner.visible_message.requested")

    # the R22 livelock trigger: a fresh non-rework task_map.ready re-emitted
    spurious = ZfEvent(
        type="task_map.ready", actor=None,
        payload={"pdd_id": PDD, "task_map_ref": task_map_ref},
    )
    log.append(spurious)
    orch.run_once(events=[spurious])

    assert [e for e in _events(log) if e.type == "candidate.rework.quarantined"]
    assert not impl_fanouts_triggered_by(spurious.id), (
        "quarantined pdd must not re-arm the impl fanout"
    )

    # operator authorization lifts the quarantine and impl re-arms
    authorized = ZfEvent(
        type="task_map.ready", actor="operator",
        payload={
            "pdd_id": PDD,
            "operator_authorized": True,
            "task_map_ref": task_map_ref,
        },
    )
    log.append(authorized)
    orch.run_once(events=[authorized])
    assert impl_fanouts_triggered_by(authorized.id), (
        "operator-authorized task_map.ready must re-arm impl"
    )


def test_self_repair_dispatch_consumer_closes_loop(tmp_path: Path):
    """R20-B2 closed loop, deterministically: repair.dispatch_requested ->
    tick consumer -> isolated worktree + briefing + dispatched event."""
    from zf.core.events.writer import EventWriter
    from zf.runtime.self_repair_runner import dispatch_pending_self_repairs

    # a tmp stand-in for the zaofu harness repo (worktrees branch off it)
    harness = tmp_path / "harness"
    harness.mkdir()
    _init_repo(harness)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)

    requested = ZfEvent(
        type="autoresearch.repair.dispatch_requested", actor="zf-autoresearch",
        payload={
            "fingerprint": "failure:smoke:CJMIN-SMOKE-001",
            "attempt": 1,
            "candidate_id": "HIC-SMOKE",
            "hypothesis": "smoke loop",
            "scope": ["src/zf/**", "tests/**"],
            "verification": "pytest -q",
            "candidate_path": str(state_dir / "candidate.json"),
        },
    )
    log.append(requested)

    count = dispatch_pending_self_repairs(
        log.read_all(), writer,
        root=harness, spawn=False, tmp_root=tmp_path / "worktrees",
    )
    assert count == 1
    dispatched = _last_of(log, "autoresearch.repair.dispatched")
    worktree = Path(dispatched.payload["worktree"])
    assert worktree.exists()
    assert Path(dispatched.payload["briefing_path"]).exists()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").startswith(
        "self-repair/"
    )

    # idempotent: a second sweep over the same window dispatches nothing
    assert dispatch_pending_self_repairs(
        log.read_all(), writer,
        root=harness, spawn=False, tmp_root=tmp_path / "worktrees",
    ) == 0
