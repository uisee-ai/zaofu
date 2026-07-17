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
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config(*, durable: bool = False, target_ref: str = "candidate/${task_id}") -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-b", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(
            flow_metadata={
                "result_protocol": {"mode": "blocking"},
            } if durable else {},
            stages=[
                WorkflowStageConfig(
                    id="review-wave",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=["review-a", "review-b"],
                    target_ref=target_ref,
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="review.approved",
                        failure_event="review.rejected",
                    ),
                ),
            ],
        ),
    )


def _state(
    tmp_path: Path,
    *,
    config: ZfConfig | None = None,
    workflow_anchor: bool = False,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-1",
        title="Review candidate",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            evidence_contract={"workflow_fanout_anchor": True}
            if workflow_anchor else {},
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config or _config(), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _empty_state(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _config(), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _run_until_sent(orch: Orchestrator, transport: _RecordingTransport, expected: int) -> None:
    for _ in range(6):
        if len(transport.sent) >= expected:
            return
        orch.run_once()


def _durable_review_terminal(*, fanout_id: str, child: dict) -> ZfEvent:
    child_payload = dict(child.get("payload") or {})
    identity = {
        "workflow_run_id": "wf-durable-review",
        "task_id": "TASK-1",
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
        "verdict": "passed",
        "failure_class": "none",
        **identity,
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
        "requirement_results": [{
            "acceptance_id": f"AC-{child['child_id']}",
            "status": "passed",
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "evidence_refs": ["test:workflow-invoke"],
            "findings": [],
            "reproduction_commands": ["pytest"],
        }],
    }
    return ZfEvent(
        type="review.child.completed",
        actor=child["role_instance"],
        task_id="TASK-1",
        correlation_id="wf-durable-review",
        payload={
            **child_payload,
            **identity,
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "role_instance": child["role_instance"],
            "stage_id": "review-wave",
            "status": "completed",
            "verification_result": verification_result,
            "report": {
                "child_id": child["child_id"],
                "status": "passed",
                "summary": "durable nested review passed",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["test:workflow-invoke"],
                "requirement_coverage_matrix": [{
                    "acceptance_id": f"AC-{child['child_id']}",
                    "status": "passed",
                    "verification_owner": "task_verify",
                    "verification_tier": "runtime",
                    "evidence_refs": ["test:workflow-invoke"],
                    "findings": [],
                }],
            },
        },
    )


def test_workflow_invoke_accepts_declared_pattern_and_emits_fanout_intent(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "requested_by": "qa",
            "reason": "risk review",
            "source": "web",
            "source_refs": {
                "channel_id": "ch-zaofu",
                "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            },
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
            "expected_output": "review report",
        },
    )])

    events = log.read_all()
    assert any(event.type == "workflow.invoke.accepted" for event in events)
    accepted = next(event for event in events if event.type == "workflow.invoke.accepted")
    assert accepted.payload["source_refs"]["channel_id"] == "ch-zaofu"
    assert accepted.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
    fanout = next(event for event in events if event.type == "task.fanout.requested")
    assert fanout.payload["requested_specialists"] == ["review-a", "review-b"]
    assert fanout.payload["expected_output"] == "review report"
    assert fanout.payload["artifact_refs"] == [{"path": "channels/ch-zaofu/spec.md"}]


def test_durable_workflow_invoke_and_compiled_children_are_restart_deduped(
    tmp_path: Path,
) -> None:
    _state_dir, log, transport, orch = _state(
        tmp_path,
        config=_config(durable=True, target_ref=""),
        workflow_anchor=True,
    )
    payload = {
        "task_id": "TASK-1",
        "pattern_id": "review-wave",
        "dispatch_id": "disp-1",
        "requested_by": "qa",
        "reason": "durable review",
        "source_refs": {},
        "workflow_run_id": "wf-durable-review",
        "expected_output": "review report",
    }

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="wf-durable-review",
        payload=dict(payload),
    )])
    # Simulate an input-event replay after the parent operation was started.
    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="wf-durable-review",
        payload=dict(payload),
    )])

    events = log.read_all()
    assert sum(event.type == "workflow.invoke.accepted" for event in events) == 1
    assert sum(event.type == "task.fanout.requested" for event in events) == 1
    parent_requested = [
        event for event in events
        if event.type == "workflow.operation.requested"
    ]
    assert len(parent_requested) == 1
    parent_operation_id = parent_requested[0].payload["operation_id"]
    assert parent_requested[0].payload["operation_type"] == "workflow"

    _run_until_sent(orch, transport, 2)

    events = log.read_all()
    operation_requests = [
        event for event in events
        if event.type == "workflow.operation.requested"
    ]
    assert len(operation_requests) == 3
    child_requests = [
        event for event in operation_requests
        if event.payload["operation_type"] == "fanout_reader_child"
    ]
    assert len(child_requests) == 2
    assert all(
        event.payload["parent_operation_id"] == parent_operation_id
        for event in child_requests
    )
    child_started = [
        event for event in events
        if event.type == "workflow.operation.started"
        and event.payload["operation_id"] != parent_operation_id
    ]
    event_positions = {event.id: index for index, event in enumerate(events)}
    assert max(event_positions[event.id] for event in child_requests) < min(
        event_positions[event.id] for event in child_started
    )
    assert len(transport.sent) == 2
    dispatched = [
        event for event in events
        if event.type == "fanout.child.dispatched"
    ]
    assert all(
        event.payload["payload"]["workflow_run_id"] == "wf-durable-review"
        for event in dispatched
    )

    fanout_started = next(event for event in events if event.type == "fanout.started")
    manifest_path = (
        _state_dir / "fanouts" / fanout_started.payload["fanout_id"] / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for child in manifest["children"]:
        orch.run_once(events=[_durable_review_terminal(
            fanout_id=manifest["fanout_id"],
            child=child,
        )])
    aggregate = next(
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload["fanout_id"] == manifest["fanout_id"]
    )
    restarted = Orchestrator(
        _state_dir,
        _config(durable=True, target_ref=""),
        _RecordingTransport(),
    )  # type: ignore[arg-type]
    restarted.run_once(events=[aggregate])

    events = log.read_all()
    parent_settled = next(
        event for event in events
        if event.type == "workflow.operation.settled"
        and event.payload["operation_id"] == parent_operation_id
    )
    parent_admitted = next(
        event for event in events
        if event.type == "workflow.call.result.admitted"
        and event.payload["operation_id"] == parent_operation_id
    )
    assert parent_admitted.payload["control_result_schema"] == (
        "fanout-aggregate-result.v1"
    )
    assert parent_settled.payload["admitted_call_result_ref"]["ref"] == (
        parent_admitted.payload["envelope_ref"]["ref"]
    )


def test_prd_workflow_invoke_uses_source_ref_as_scan_target(tmp_path: Path) -> None:
    state_dir, log, _transport, orch = _empty_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-PRD",
        payload={
            "kind": "prd",
            "task_id": "TASK-PRD",
            "pattern_id": "review-wave",
            "requested_by": "qa",
            "reason": "run PRD scan",
            "source_refs": {
                "source_ref": "docs/prd/tiny-notes.md",
                "workflow_input_manifest_ref": "workflow-inputs/wf-prd/manifest.json",
            },
            "workflow_input_manifest_ref": "workflow-inputs/wf-prd/manifest.json",
            "artifact_refs": [{"path": "artifacts/workflow/wf-prd/acceptance-matrix.json"}],
            "expected_output": "scan PRD",
        },
    )])

    fanout = next(event for event in log.read_all() if event.type == "task.fanout.requested")
    assert fanout.payload["target_ref"] == "docs/prd/tiny-notes.md"
    assert fanout.payload["prompt_kind"] == "prd"

    _run_until_sent(orch, _transport, 1)

    manifests = sorted((state_dir / "fanouts").glob("*/manifest.json"))
    assert manifests
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["target_ref"] == "docs/prd/tiny-notes.md"
    if _transport.sent:
        briefing = _transport.sent[0][1].read_text(encoding="utf-8")
        assert "- target_ref: `docs/prd/tiny-notes.md`" in briefing


def test_workflow_invoke_cold_start_anchor_is_not_dispatched_directly(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _empty_state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-COLD",
        payload={
            "task_id": "TASK-COLD",
            "pattern_id": "review-wave",
            "request_id": "wf-cold",
            "workflow_input_manifest_ref": "artifacts/workflow/wf-cold/workflow-input-manifest.json",
            "artifact_refs": [{"path": "artifacts/workflow/wf-cold/acceptance-matrix.json"}],
            "expected_output": "review report",
        },
    )])

    events = log.read_all()
    assert any(event.type == "task.created" for event in events)
    assert any(event.type == "workflow.invoke.accepted" for event in events)
    assert any(event.type == "task.fanout.requested" for event in events)
    assert not any(event.type == "task.dispatched" for event in events)
    assert transport.sent == []
    task = TaskStore(state_dir / "kanban.json").get("TASK-COLD")
    assert task is not None
    assert task.contract.evidence_contract["workflow_fanout_anchor"] is True

    _run_until_sent(orch, transport, 2)

    events = log.read_all()
    assert any(event.type == "fanout.started" for event in events)
    assert not any(event.type == "task.fanout.rejected" for event in events)
    assert not any(event.type == "task.dispatched" for event in events)
    if transport.sent:
        assert transport.sent[0][1].exists()
        assert getattr(transport.sent[0][3], "trace_id", "")


def test_workflow_invoke_rejects_blocking_open_questions(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "requested_by": "qa",
            "reason": "risk review",
            "source": "web",
            "source_refs": {"channel_id": "ch-zaofu"},
            "open_questions": ["which target?"],
        },
    )])

    events = log.read_all()
    rejected = next(event for event in events if event.type == "workflow.invoke.rejected")
    assert rejected.payload["reason"] == "blocking open questions"
    assert not any(event.type == "task.fanout.requested" for event in events)


def test_task_fanout_request_rejects_missing_expected_output(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": [],
            "requested_specialists": ["review-a"],
            "risk": "",
        },
    )])

    rejected = next(event for event in log.read_all() if event.type == "task.fanout.rejected")
    assert rejected.payload["reason"] == "expected_output missing"


def test_task_fanout_request_rejects_write_capability(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": [],
            "requested_specialists": ["review-a"],
            "expected_output": "review report",
            "risk": "",
            "write_files": ["src/app.py"],
        },
    )])

    rejected = next(event for event in log.read_all() if event.type == "task.fanout.rejected")
    assert rejected.payload["reason"] == "reader fanout cannot request write capability"


def test_task_fanout_request_propagates_workflow_input_refs_to_children(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": ["docs/"],
            "requested_specialists": ["review-a", "review-b"],
            "expected_output": "review report",
            "risk": "",
            "source_refs": {
                "channel_id": "ch-zaofu",
                "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            },
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
        },
    )])

    events = log.read_all()
    fanout = next(event for event in events if event.type == "fanout.requested")
    assert fanout.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
    child_events = [event for event in events if event.type == "fanout.child.dispatched"]
    assert len(child_events) == 2
    assert all(event.payload["scope"] == ["docs/"] for event in child_events)
    assert all(
        event.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
        for event in child_events
    )
    assert all(event.payload["artifact_refs"] == [{"path": "channels/ch-zaofu/spec.md"}] for event in child_events)
