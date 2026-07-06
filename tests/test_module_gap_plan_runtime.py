from __future__ import annotations

import json
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
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


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


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="gap-plan-test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="dev",
                instance_id="dev-lane-0",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done", "dev.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="module-gap-impl",
                    trigger="task_map.ready",
                    topology="fanout_writer_scoped",
                    roles=["dev-lane-0"],
                    task_map="${task_map_ref}",
                    synthesize_canonical_tasks=True,
                    aggregate=FanoutAggregateConfig(
                        mode="candidate_integration",
                        success_event="candidate.ready",
                        failure_event="integration.failed",
                    ),
                ),
            ],
        ),
    )


def _state(tmp_path: Path) -> tuple[Path, EventLog, _RecordingTransport, Orchestrator]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _parity_scan_config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="parity-scan-test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="verify-lane-0", backend="mock", role_kind="reader"),
            RoleConfig(name="scan-contract", backend="mock", role_kind="reader"),
            RoleConfig(name="scan-runtime", backend="mock", role_kind="reader"),
            RoleConfig(name="scan-verification", backend="mock", role_kind="reader"),
            RoleConfig(name="judge-refactor", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="cangjie-candidate-verification",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=["verify-lane-0"],
                    target_ref="${candidate_ref}",
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="verify.child.completed",
                        child_failure_event="verify.child.failed",
                        success_event="verify.passed",
                        failure_event="verify.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="cangjie-module-parity-scan",
                    trigger="verify.parity_scan.requested",
                    topology="fanout_reader",
                    roles=["scan-contract", "scan-runtime", "scan-verification"],
                    target_ref="${candidate_ref}",
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="module.parity.scan.completed",
                        failure_event="module.parity.scan.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="cangjie-final-judge",
                    trigger="module.parity.closed",
                    topology="fanout_reader",
                    roles=["judge-refactor"],
                    target_ref="${candidate_ref}",
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="judge.passed",
                        failure_event="judge.failed",
                    ),
                ),
            ],
        ),
    )


def _flow_discovery_config(
    state_dir: Path,
    *,
    flow_kind: str,
    discovery_profile: str,
    with_discovery_stage: bool = False,
    extra_flow_metadata: dict | None = None,
) -> ZfConfig:
    stages = []
    roles = [RoleConfig(name="flow-discovery", backend="mock", role_kind="reader")]
    if with_discovery_stage:
        stages.append(WorkflowStageConfig(
            id=f"{flow_kind}-post-verify-discovery",
            trigger="flow.discovery.requested",
            topology="fanout_reader",
            roles=["flow-discovery"],
            target_ref="${target_ref}",
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="flow.discovery.child.completed",
                child_failure_event="flow.discovery.child.failed",
                success_event="flow.discovery.completed",
                failure_event="flow.discovery.failed",
            ),
        ))
    flow_metadata = {
        "flow_kind": flow_kind,
        "post_verify_discovery": discovery_profile,
        "quality_floor": (
            "issue-regression" if flow_kind == "issue" else "product-demo"
        ),
        "evidence_policy": "strict_refs",
        "projection_policy": "control_room",
    }
    flow_metadata.update(extra_flow_metadata or {})
    return ZfConfig(
        project=ProjectConfig(name=f"{flow_kind}-flow-test", state_dir=str(state_dir)),
        roles=roles,
        workflow=WorkflowConfig(
            stages=stages,
            flow_metadata=flow_metadata,
        ),
    )


def _parity_scan_state(tmp_path: Path) -> tuple[Path, EventLog, _RecordingTransport, Orchestrator]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _parity_scan_config(state_dir), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _flow_discovery_state(
    tmp_path: Path,
    *,
    flow_kind: str,
    discovery_profile: str,
    with_discovery_stage: bool = False,
    extra_flow_metadata: dict | None = None,
) -> tuple[Path, EventLog, _RecordingTransport, Orchestrator]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _flow_discovery_config(
            state_dir,
            flow_kind=flow_kind,
            discovery_profile=discovery_profile,
            with_discovery_stage=with_discovery_stage,
            extra_flow_metadata=extra_flow_metadata,
        ),
        transport,
    )  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _write_base_task_map(state_dir: Path) -> str:
    path = state_dir / "artifacts" / "CANGJIE" / "task_map.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "CANGJIE",
            "tasks": [{
                "task_id": "CANGJIE-WEB-001",
                "title": "Web baseline",
                "owner_role": "dev",
                "wave": 0,
                "allowed_paths": ["web/**"],
                "allowed_paths_reason": "baseline web slice",
                "acceptance": ["baseline web slice exists"],
            }],
        }),
        encoding="utf-8",
    )
    return ".zf/artifacts/CANGJIE/task_map.json"


def test_issue_verify_passed_requests_report_only_flow_discovery(tmp_path: Path) -> None:
    _state_dir, log, transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="issue",
        discovery_profile="regression_impact",
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="issue-verify-passed-1",
        type="verify.passed",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-123",
            "feature_id": "ISSUE-123",
            "trace_id": "trace-issue",
            "task_map_ref": ".zf/artifacts/ISSUE-123/task_map.json",
            "candidate_ref": "cand/ISSUE-123",
            "artifact_refs": ["reports/ISSUE-123/verify.md"],
        },
    )])

    events = log.read_all()
    requested = [event for event in events if event.type == "flow.discovery.requested"]
    assert any(decision.action == "bridge" for decision in decisions)
    assert len(requested) == 1
    payload = requested[0].payload
    assert payload["flow_kind"] == "issue"
    assert payload["discovery_profile"] == "regression_impact"
    assert payload["task_map_ref"] == ".zf/artifacts/ISSUE-123/task_map.json"
    assert payload["artifact_refs"] == ["reports/ISSUE-123/verify.md"]
    assert payload["source_event_id"] == "issue-verify-passed-1"
    assert transport.sent == []


def test_prd_verify_passed_flow_discovery_can_start_reader_fanout(tmp_path: Path) -> None:
    _state_dir, log, transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="prd",
        discovery_profile="product_completeness",
        with_discovery_stage=True,
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="prd-verify-passed-1",
        type="verify.passed",
        actor="zf-cli",
        correlation_id="trace-prd",
        payload={
            "pdd_id": "PRD-1",
            "feature_id": "PRD-1",
            "trace_id": "trace-prd",
            "task_map_ref": ".zf/artifacts/PRD-1/task_map.json",
            "candidate_ref": "cand/PRD-1",
        },
    )])

    events = log.read_all()
    requested = [event for event in events if event.type == "flow.discovery.requested"]
    started = [event for event in events if event.type == "fanout.started"]
    assert any(decision.action == "bridge" for decision in decisions)
    assert len(requested) == 1
    assert requested[0].payload["flow_kind"] == "prd"
    assert requested[0].payload["discovery_profile"] == "product_completeness"
    assert [event.payload["stage_id"] for event in started] == [
        "prd-post-verify-discovery",
    ]
    assert [sent[0] for sent in transport.sent] == ["flow-discovery"]


def test_issue_judge_passed_without_quality_evidence_blocks_goal(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="issue",
        discovery_profile="regression_impact",
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="issue-judge-passed-missing-evidence",
        type="judge.passed",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-123",
            "feature_id": "ISSUE-123",
            "trace_id": "trace-issue",
            "task_map_ref": ".zf/artifacts/ISSUE-123/task_map.json",
        },
    )])

    events = log.read_all()
    blocked = [event for event in events if event.type == "flow.goal.blocked"]
    assert any(decision.action == "block" for decision in decisions)
    assert len(blocked) == 1
    payload = blocked[0].payload
    assert payload["flow_kind"] == "issue"
    assert payload["quality_floor"] == "issue-regression"
    assert payload["source_event_id"] == "issue-judge-passed-missing-evidence"
    assert payload["expected_downstream_events"] == [
        "flow.gap_plan.ready",
        "goal.gap_plan.ready",
        "flow.goal.closed",
    ]


def test_prd_judge_passed_with_demo_evidence_can_settle(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="prd",
        discovery_profile="product_completeness",
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="prd-judge-passed-with-evidence",
        type="judge.passed",
        actor="zf-cli",
        correlation_id="trace-prd",
        payload={
            "pdd_id": "PRD-1",
            "feature_id": "PRD-1",
            "trace_id": "trace-prd",
            "task_map_ref": ".zf/artifacts/PRD-1/task_map.json",
            "demo_refs": ["reports/PRD-1/demo.md"],
        },
    )])

    assert not [event for event in log.read_all() if event.type == "flow.goal.blocked"]
    assert all(decision.action != "block" for decision in decisions)


def test_judge_passed_with_only_artifact_refs_still_blocks_goal(
    tmp_path: Path,
) -> None:
    """artifact_refs alone must not satisfy a quality floor — aggregated
    judge.passed payloads almost always carry artifact_refs, so accepting it
    would make the evidence gate pass vacuously."""
    _state_dir, log, _transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="issue",
        discovery_profile="regression_impact",
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="issue-judge-passed-artifact-refs-only",
        type="judge.passed",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-124",
            "feature_id": "ISSUE-124",
            "trace_id": "trace-issue",
            "task_map_ref": ".zf/artifacts/ISSUE-124/task_map.json",
            "artifact_refs": [".zf/artifacts/ISSUE-124/report.md"],
        },
    )])

    blocked = [event for event in log.read_all() if event.type == "flow.goal.blocked"]
    assert any(decision.action == "block" for decision in decisions)
    assert len(blocked) == 1
    assert blocked[0].payload["missing_ref_groups"] == [
        ["repro_ref", "regression_refs", "test_refs"],
    ]


def test_flow_declared_quality_floor_ref_groups_override_builtin(
    tmp_path: Path,
) -> None:
    """flow_metadata.quality_floor_ref_groups replaces the builtin vocabulary:
    the declared keys gate, the builtin floor keys stop mattering."""
    extra = {"quality_floor_ref_groups": [["bench_refs", "perf_refs"]]}
    _state_dir, log, _transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="issue",
        discovery_profile="regression_impact",
        extra_flow_metadata=extra,
    )

    blocked_decisions = orch.run_once(events=[ZfEvent(
        id="issue-judge-passed-declared-missing",
        type="judge.passed",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-125",
            "feature_id": "ISSUE-125",
            "trace_id": "trace-issue",
            # Satisfies the builtin issue-regression floor but not the
            # declared groups — declared must win.
            "repro_ref": "reports/ISSUE-125/repro.md",
        },
    )])
    blocked = [event for event in log.read_all() if event.type == "flow.goal.blocked"]
    assert any(decision.action == "block" for decision in blocked_decisions)
    assert len(blocked) == 1
    assert blocked[0].payload["missing_ref_groups"] == [["bench_refs", "perf_refs"]]

    passing_decisions = orch.run_once(events=[ZfEvent(
        id="issue-judge-passed-declared-present",
        type="judge.passed",
        actor="zf-cli",
        correlation_id="trace-issue",
        payload={
            "pdd_id": "ISSUE-125",
            "feature_id": "ISSUE-125",
            "trace_id": "trace-issue",
            "bench_refs": ["reports/ISSUE-125/bench.json"],
        },
    )])
    assert all(decision.action != "block" for decision in passing_decisions)
    assert len([
        event for event in log.read_all() if event.type == "flow.goal.blocked"
    ]) == 1


def test_flow_discovery_completed_with_prd_gaps_amends_task_map(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)

    decisions = orch.run_once(events=[ZfEvent(
        id="flow-discovery-completed-gaps",
        type="flow.discovery.completed",
        actor="flow-discovery",
        correlation_id="trace-prd-gap",
        payload={
            "schema_version": "flow-discovery-result.v1",
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "goal_id": "CANGJIE",
            "flow_kind": "prd",
            "discovery_profile": "product_completeness",
            "gap_category": "acceptance_gap",
            "trace_id": "trace-prd-gap",
            "task_map_ref": task_map_ref,
            "gap_plan_ref": "reports/CANGJIE/prd-gap-plan.json",
            "gap_tasks": [{
                "task_id": "CANGJIE-PRD-GAP-001",
                "parent_task_id": "CANGJIE-WEB-001",
                "affinity_tag": "web-tui",
                "owner_role": "dev",
                "claim_paths": ["web/src/**", "tests/e2e/**"],
                "acceptance": ["product demo covers the missing workflow"],
                "verify_commands": ["npm run test:e2e"],
                "source_refs": ["reports/CANGJIE/prd-gap-plan.json"],
            }],
        },
    )])

    events = log.read_all()
    assert any(decision.action == "bridge" for decision in decisions)
    gap_ready = next(event for event in events if event.type == "flow.gap_plan.ready")
    ready = next(event for event in events if event.type == "task_map.ready")
    assert gap_ready.payload["goal_kind"] == "prd"
    assert gap_ready.payload["gap_category"] == "acceptance_gap"
    assert ready.payload["gap_event_type"] == "flow.gap_plan.ready"
    assert ready.payload["resume_scope"] == "gap_tasks_only"
    assert ready.payload["task_ids"] == ["CANGJIE-PRD-GAP-001"]
    task = TaskStore(state_dir / "kanban.json").get("CANGJIE-PRD-GAP-001")
    assert task is not None
    assert task.contract.evidence_contract["goal_kind"] == "prd"
    assert task.contract.evidence_contract["gap_category"] == "acceptance_gap"
    assert transport.sent and transport.sent[0][0] == "dev-lane-0"


def test_flow_discovery_completed_without_gaps_closes_goal(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="issue",
        discovery_profile="regression_impact",
    )

    decisions = orch.run_once(events=[ZfEvent(
        id="flow-discovery-clean",
        type="flow.discovery.completed",
        actor="flow-discovery",
        correlation_id="trace-issue-clean",
        payload={
            "pdd_id": "ISSUE-123",
            "feature_id": "ISSUE-123",
            "flow_kind": "issue",
            "trace_id": "trace-issue-clean",
            "task_map_ref": ".zf/artifacts/ISSUE-123/task_map.json",
            "open_p0_p1_gap_count": 0,
        },
    )])

    events = log.read_all()
    closed = [event for event in events if event.type == "flow.goal.closed"]
    assert any(decision.action == "bridge" for decision in decisions)
    assert len(closed) == 1
    assert closed[0].payload["flow_kind"] == "issue"
    assert closed[0].payload["source_event_id"] == "flow-discovery-clean"


def test_verify_parity_scan_request_starts_reader_fanout(tmp_path: Path) -> None:
    _state_dir, log, transport, orch = _parity_scan_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        id="verify-parity-scan-request-1",
        type="verify.parity_scan.requested",
        actor="verify-lane-0",
        correlation_id="trace-parity-scan",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-parity-scan",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "candidate_ref": "cand/CANGJIE",
        },
    )])

    events = log.read_all()
    started = [event for event in events if event.type == "fanout.started"]
    dispatched = [event for event in events if event.type == "fanout.child.dispatched"]
    assert len(started) == 1
    assert started[0].payload["stage_id"] == "cangjie-module-parity-scan"
    assert started[0].payload["topology"] == "fanout_reader"
    assert started[0].payload["pdd_id"] == "CANGJIE"
    assert started[0].payload["target_ref"] == "cand/CANGJIE"
    assert started[0].payload["trigger_payload"]["task_map_ref"] == (
        ".zf/artifacts/CANGJIE/task_map.json"
    )
    assert {event.payload["role_instance"] for event in dispatched} == {
        "scan-contract",
        "scan-runtime",
        "scan-verification",
    }
    assert {event.payload["target_ref"] for event in dispatched} == {"cand/CANGJIE"}
    assert [sent[0] for sent in transport.sent] == [
        "scan-contract",
        "scan-runtime",
        "scan-verification",
    ]
    assert all(sent[3].trace_id == "trace-parity-scan" for sent in transport.sent)


def test_verify_passed_requests_module_parity_scan(tmp_path: Path) -> None:
    _state_dir, log, transport, orch = _parity_scan_state(tmp_path)

    decisions = orch.run_once(events=[ZfEvent(
        id="verify-passed-1",
        type="verify.passed",
        actor="zf-cli",
        correlation_id="trace-verify",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-verify",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "candidate_ref": "cand/CANGJIE",
        },
    )])

    events = log.read_all()
    requested = [event for event in events if event.type == "verify.parity_scan.requested"]
    started = [event for event in events if event.type == "fanout.started"]
    assert any(decision.action == "bridge" for decision in decisions)
    assert len(requested) == 1
    assert requested[0].payload["source_event_id"] == "verify-passed-1"
    assert requested[0].payload["candidate_ref"] == "cand/CANGJIE"
    assert requested[0].payload["task_map_ref"] == ".zf/artifacts/CANGJIE/task_map.json"
    assert len(started) == 1
    assert started[0].payload["stage_id"] == "cangjie-module-parity-scan"
    assert [sent[0] for sent in transport.sent] == [
        "scan-contract",
        "scan-runtime",
        "scan-verification",
    ]


def test_verify_fanout_success_immediately_requests_module_parity_scan(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _parity_scan_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        id="candidate-ready-1",
        type="candidate.ready",
        actor="zf-cli",
        correlation_id="trace-verify",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-verify",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "candidate_ref": "cand/CANGJIE",
        },
    )])
    started = [
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "cangjie-candidate-verification"
    ][0]
    manifest = json.loads(
        (
            state_dir
            / "fanouts"
            / started.payload["fanout_id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    child = manifest["children"][0]

    orch.run_once(events=[ZfEvent(
        id="verify-child-completed-1",
        type="verify.child.completed",
        actor=child["role_instance"],
        correlation_id="trace-verify",
        payload={
            "fanout_id": started.payload["fanout_id"],
            "trace_id": "trace-verify",
            "stage_id": "cangjie-candidate-verification",
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "role_instance": child["role_instance"],
            "status": "completed",
        },
    )])

    events = log.read_all()
    assert [event.type for event in events].count("verify.passed") == 1
    requested = [
        event for event in events
        if event.type == "verify.parity_scan.requested"
    ]
    assert len(requested) == 1
    assert requested[0].payload["source"] == "verify_passed_bridge"
    started_stages = [
        event.payload["stage_id"]
        for event in events
        if event.type == "fanout.started"
    ]
    assert started_stages == [
        "cangjie-candidate-verification",
        "cangjie-module-parity-scan",
    ]
    assert [sent[0] for sent in transport.sent] == [
        "verify-lane-0",
        "scan-contract",
        "scan-runtime",
        "scan-verification",
    ]


def test_module_parity_scan_completed_with_gaps_amends_task_map(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)

    decisions = orch.run_once(events=[ZfEvent(
        id="parity-scan-completed-gaps",
        type="module.parity.scan.completed",
        actor="zf-cli",
        correlation_id="trace-parity-gaps",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-parity-gaps",
            "task_map_ref": task_map_ref,
            "candidate_ref": "cand/CANGJIE",
            "gap_tasks": [{
                "task_id": "CANGJIE-WEB-GAP-001",
                "module_id": "web-dashboard",
                "parent_task_id": "CANGJIE-WEB-001",
                "affinity_tag": "web-tui",
                "owner_role": "dev",
                "claim_paths": ["web/src/**", "packages/web-adapter/**"],
                "acceptance": ["WebChat reaches Cangjie runtime"],
                "verify_commands": ["npm run test:e2e:webchat"],
                "source_refs": ["hermes-agent/web"],
            }],
        },
    )])

    events = log.read_all()
    assert any(decision.action == "bridge" for decision in decisions)
    assert any(event.type == "gap_plan.ready" for event in events)
    amended = next(event for event in events if event.type == "task_map.amended")
    ready = next(event for event in events if event.type == "task_map.ready")
    assert amended.payload["gap_task_ids"] == ["CANGJIE-WEB-GAP-001"]
    assert ready.payload["resume_scope"] == "gap_tasks_only"
    assert ready.payload["task_ids"] == ["CANGJIE-WEB-GAP-001"]
    task = TaskStore(state_dir / "kanban.json").get("CANGJIE-WEB-GAP-001")
    assert task is not None
    assert task.status == "in_progress"
    assert transport.sent and transport.sent[0][0] == "dev-lane-0"


def test_module_parity_scan_completed_without_gaps_closes_and_starts_judge(
    tmp_path: Path,
) -> None:
    _state_dir, log, transport, orch = _parity_scan_state(tmp_path)

    decisions = orch.run_once(events=[ZfEvent(
        id="parity-scan-completed-clean",
        type="cangjie.module.parity.scan.completed",
        actor="zf-cli",
        correlation_id="trace-parity-clean",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-parity-clean",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "candidate_ref": "cand/CANGJIE",
            "open_p0_p1_gap_count": 0,
        },
    )])

    events = log.read_all()
    closed = [event for event in events if event.type == "module.parity.closed"]
    started = [event for event in events if event.type == "fanout.started"]
    assert any(decision.action == "bridge" for decision in decisions)
    assert len(closed) == 1
    assert closed[0].payload["source_event_id"] == "parity-scan-completed-clean"
    assert [event.payload["stage_id"] for event in started] == ["cangjie-final-judge"]
    assert [sent[0] for sent in transport.sent] == ["judge-refactor"]


def test_module_parity_scan_fanout_success_immediately_starts_judge(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _parity_scan_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        id="verify-parity-scan-request-1",
        type="verify.parity_scan.requested",
        actor="zf-cli",
        correlation_id="trace-parity",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-parity",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
            "candidate_ref": "cand/CANGJIE",
        },
    )])
    started = [
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "cangjie-module-parity-scan"
    ][0]
    manifest_path = (
        state_dir
        / "fanouts"
        / started.payload["fanout_id"]
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for child in manifest["children"]:
        orch.run_once(events=[ZfEvent(
            type="refactor.scan.completed",
            actor=child["role_instance"],
            correlation_id="trace-parity",
            payload={
                "fanout_id": started.payload["fanout_id"],
                "trace_id": "trace-parity",
                "stage_id": "cangjie-module-parity-scan",
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "role_instance": child["role_instance"],
                "status": "completed",
                "report": {
                    "status": "passed",
                    "recommendation": "approve",
                    "summary": "NO-OPEN-P0-P1-GAPS",
                    "open_p0_p1_gap_count": 0,
                    "parity_status": "closed",
                },
            },
        )])

    events = log.read_all()
    assert [event.type for event in events].count(
        "module.parity.scan.completed",
    ) == 1
    closed = [event for event in events if event.type == "module.parity.closed"]
    assert len(closed) == 1
    assert closed[0].payload["source"] == "module_parity_scan_bridge"
    started_stages = [
        event.payload["stage_id"]
        for event in events
        if event.type == "fanout.started"
    ]
    assert started_stages == [
        "cangjie-module-parity-scan",
        "cangjie-final-judge",
    ]
    assert [sent[0] for sent in transport.sent] == [
        "scan-contract",
        "scan-runtime",
        "scan-verification",
        "judge-refactor",
    ]


def test_gap_plan_ready_amends_task_map_and_dispatches_gap_task(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)
    gap_plan_ref = ".zf/artifacts/CANGJIE/gap-plan.json"
    gap_plan_path = state_dir / "artifacts" / "CANGJIE" / "gap-plan.json"
    gap_plan_path.write_text(
        json.dumps({
            "schema_version": "module-gap-plan.v1",
            "module_id": "web-dashboard",
            "gap_tasks": [{
                "task_id": "CANGJIE-WEB-GAP-001",
                "module_id": "web-dashboard",
                "parent_task_id": "CANGJIE-WEB-001",
                "affinity_tag": "web-tui",
                "owner_role": "dev",
                "claim_paths": ["web/src/**", "packages/web-adapter/**"],
                "acceptance": ["WebChat reaches Cangjie runtime"],
                "verify_commands": ["npm run test:e2e:webchat"],
                "source_refs": ["hermes-agent/web"],
            }],
        }),
        encoding="utf-8",
    )

    gap_event = ZfEvent(
        id="gap-plan-1",
        type="gap_plan.ready",
        actor="zf-cli",
        correlation_id="trace-gap",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-gap",
            "task_map_ref": task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "source_commit": "base123",
            "candidate_base_commit": "base123",
        },
    )

    decisions = orch.run_once(events=[gap_event])
    events = log.read_all()

    assert any(decision.action == "bridge" for decision in decisions)
    assert [event.type for event in events[:3]] == [
        "task_map.amend.requested",
        "task_map.amended",
        "task_map.ready",
    ]
    amended = next(event for event in events if event.type == "task_map.amended")
    ready = next(event for event in events if event.type == "task_map.ready")
    assert amended.payload["gap_task_ids"] == ["CANGJIE-WEB-GAP-001"]
    assert ready.payload["task_ids"] == ["CANGJIE-WEB-GAP-001"]
    assert ready.payload["resume_scope"] == "gap_tasks_only"
    amended_path = state_dir.joinpath(*Path(ready.payload["task_map_ref"]).parts[1:])
    amended_task_map = json.loads(amended_path.read_text(encoding="utf-8"))
    assert [task["task_id"] for task in amended_task_map["tasks"]] == [
        "CANGJIE-WEB-001",
        "CANGJIE-WEB-GAP-001",
    ]

    task = TaskStore(state_dir / "kanban.json").get("CANGJIE-WEB-GAP-001")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev-lane-0"
    assert task.contract.parent_task_id == "CANGJIE-WEB-001"
    assert task.contract.evidence_contract["module_id"] == "web-dashboard"
    assert task.contract.evidence_contract["gap_kind"] == "module_parity_gap"
    assert task.contract.evidence_contract["affinity_tag"] == "web-tui"
    assert transport.sent and transport.sent[0][0] == "dev-lane-0"

    restart_transport = _RecordingTransport()
    restart_orch = Orchestrator(state_dir, _config(state_dir), restart_transport)  # type: ignore[arg-type]
    restart_decisions = restart_orch.run_once(events=[gap_event])
    events_after_restart = log.read_all()
    assert any(decision.action == "noop" for decision in restart_decisions)
    assert len([
        event for event in events_after_restart
        if event.type == "task_map.amended"
    ]) == 1
    assert len([
        event for event in events_after_restart
        if event.type == "task_map.ready"
    ]) == 1
    assert not restart_transport.sent


def test_flow_neutral_gap_plan_ready_amends_task_map_and_dispatches_gap_task(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)

    gap_event = ZfEvent(
        id="flow-gap-plan-1",
        type="flow.gap_plan.ready",
        actor="zf-cli",
        correlation_id="trace-flow-gap",
        payload={
            "schema_version": "goal-gap-plan.v1",
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "goal_id": "CANGJIE",
            "goal_kind": "refactor",
            "gap_category": "parity_gap",
            "trace_id": "trace-flow-gap",
            "task_map_ref": task_map_ref,
            "gap_plan_ref": "reports/CANGJIE/flow-gap-plan.json",
            "gap_tasks": [{
                "task_id": "CANGJIE-FLOW-GAP-001",
                "parent_task_id": "CANGJIE-WEB-001",
                "affinity_tag": "web-tui",
                "owner_role": "dev",
                "claim_paths": ["web/src/**", "tests/**"],
                "acceptance": ["flow-neutral gap is implemented"],
                "verify_commands": ["uv run pytest tests/test_module_gap_plan_runtime.py"],
                "source_refs": ["reports/CANGJIE/flow-gap-plan.json"],
            }],
        },
    )

    decisions = orch.run_once(events=[gap_event])
    events = log.read_all()

    assert any(decision.action == "bridge" for decision in decisions)
    requested = next(event for event in events if event.type == "task_map.amend.requested")
    amended = next(event for event in events if event.type == "task_map.amended")
    ready = next(event for event in events if event.type == "task_map.ready")
    assert requested.payload["gap_event_type"] == "flow.gap_plan.ready"
    assert amended.payload["gap_event_type"] == "flow.gap_plan.ready"
    assert ready.payload["gap_event_type"] == "flow.gap_plan.ready"
    assert ready.payload["resume_scope"] == "gap_tasks_only"
    assert ready.payload["task_ids"] == ["CANGJIE-FLOW-GAP-001"]
    task = TaskStore(state_dir / "kanban.json").get("CANGJIE-FLOW-GAP-001")
    assert task is not None
    assert task.status == "in_progress"
    assert task.contract.evidence_contract["goal_kind"] == "refactor"
    assert task.contract.evidence_contract["gap_category"] == "parity_gap"
    assert transport.sent and transport.sent[0][0] == "dev-lane-0"

    restart_transport = _RecordingTransport()
    restart_orch = Orchestrator(state_dir, _config(state_dir), restart_transport)  # type: ignore[arg-type]
    restart_decisions = restart_orch.run_once(events=[gap_event])
    events_after_restart = log.read_all()
    assert any(decision.action == "noop" for decision in restart_decisions)
    assert len([event for event in events_after_restart if event.type == "task_map.amended"]) == 1
    assert not restart_transport.sent


def test_goal_gap_plan_ready_amends_task_map_and_dispatches_gap_task(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)

    decisions = orch.run_once(events=[ZfEvent(
        id="goal-gap-plan-1",
        type="goal.gap_plan.ready",
        actor="zf-cli",
        correlation_id="trace-goal-gap",
        payload={
            "schema_version": "goal-gap-plan.v1",
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "goal_id": "CANGJIE",
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "trace_id": "trace-goal-gap",
            "task_map_ref": task_map_ref,
            "gap_tasks": [{
                "task_id": "CANGJIE-GOAL-GAP-001",
                "claim_paths": ["src/**", "tests/**"],
                "acceptance": ["goal gap is closed"],
                "verify_commands": ["uv run pytest tests/test_module_gap_plan_runtime.py"],
                "source_refs": ["reports/CANGJIE/goal-gap-plan.json"],
            }],
        },
    )])

    events = log.read_all()
    assert any(decision.action == "bridge" for decision in decisions)
    ready = next(event for event in events if event.type == "task_map.ready")
    assert ready.payload["gap_event_type"] == "goal.gap_plan.ready"
    assert ready.payload["task_ids"] == ["CANGJIE-GOAL-GAP-001"]
    assert transport.sent and transport.sent[0][0] == "dev-lane-0"


def test_gap_plan_ready_without_gap_tasks_fails_closed(tmp_path: Path) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    task_map_ref = _write_base_task_map(state_dir)

    decision = orch.run_once(events=[ZfEvent(
        id="gap-plan-empty",
        type="gap_plan.ready",
        actor="zf-cli",
        correlation_id="trace-gap",
        payload={
            "pdd_id": "CANGJIE",
            "feature_id": "CANGJIE",
            "trace_id": "trace-gap",
            "task_map_ref": task_map_ref,
            "gap_tasks": [],
        },
    )])[0]

    assert decision.action == "block"
    failed = [event for event in log.read_all() if event.type == "task_map.amend.failed"]
    assert failed
    assert failed[-1].payload["reason"] == "gap_plan.ready contains no gap_tasks"
