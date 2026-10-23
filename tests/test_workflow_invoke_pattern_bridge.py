from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
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
from zf.runtime.run_contract import (
    bind_run_contract_workflow_artifacts,
    build_run_contract,
    write_run_contract_snapshot,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    build_task_contract_snapshot,
    task_map_generation,
    write_target_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import (
    mark_workflow_request,
    workflow_request_path,
)


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
            evidence_contract={
                "workflow_fanout_anchor": True,
                "task_map_generation": "generation-1",
            }
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


def _durable_review_terminal(
    *,
    state_dir: Path,
    fanout_id: str,
    child: dict,
) -> ZfEvent:
    child_payload = dict(child.get("payload") or {})
    task = TaskStore(state_dir / "kanban.json").get("TASK-1")
    assert task is not None
    generation = task_map_generation(task)
    contract_snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="wf-durable-review",
        task_map_generation_id=generation,
        base_commit="base-1",
        task_ref="artifacts/task-ref.json",
    )
    contract_descriptor = write_task_contract_snapshot(
        state_dir,
        contract_snapshot,
    )
    target_snapshot = build_target_snapshot(
        contract_descriptor,
        target_commit="target-1",
        contract_snapshot=contract_snapshot,
    )
    target_descriptor = write_target_snapshot(state_dir, target_snapshot)
    identity = {
        **{
            key: contract_snapshot[key]
            for key in (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
            )
        },
        "contract_snapshot_ref": contract_descriptor["ref"],
        "contract_snapshot_digest": contract_descriptor["sha256"],
        "target_snapshot_ref": target_descriptor["ref"],
        "target_snapshot_digest": target_descriptor["sha256"],
        "target_commit": "target-1",
    }
    acceptance_id = contract_snapshot["acceptance_criteria"][0]["acceptance_id"]
    verification_result = {
        "schema_version": "verification-result.v1",
        "execution_status": "completed",
        "verdict": "passed",
        "failure_class": "none",
        **identity,
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
        "requirement_results": [{
            "acceptance_id": acceptance_id,
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
                    "acceptance_id": acceptance_id,
                    "status": "passed",
                    "verification_owner": "task_verify",
                    "verification_tier": "runtime",
                    "evidence_refs": ["test:workflow-invoke"],
                    "findings": [],
                }],
            },
        },
    )


def test_workflow_invoke_accepts_declared_pattern_and_emits_fanout_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from zf.runtime import flow_role_activation

    _state_dir, log, _transport, orch = _state(tmp_path)
    activation_calls: list[dict] = []
    real_activate = flow_role_activation.activate_flow_roles

    def track_activation(orchestrator, **kwargs):
        activation_calls.append(dict(kwargs))
        return real_activate(orchestrator, **kwargs)

    monkeypatch.setattr(
        flow_role_activation,
        "activate_flow_roles",
        track_activation,
    )

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
    assert len(activation_calls) == 1
    assert activation_calls[0]["source_event_id"]



def test_cancel_after_role_activation_prevents_invoke_acceptance(tmp_path, monkeypatch):
    from zf.runtime import flow_role_activation

    state, log, transport, orch = _state(tmp_path)
    real_activate = flow_role_activation.activate_flow_roles

    def cancel_after_activation(host, **kwargs):
        activation = real_activate(host, **kwargs)
        host.event_writer.append(ZfEvent(
            type="run.cancelled", payload={"run_id": "wf-cancel"},
            correlation_id="wf-cancel"))
        return activation

    monkeypatch.setattr(flow_role_activation, "activate_flow_roles", cancel_after_activation)
    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested", actor="web", task_id="TASK-1",
        payload={"task_id": "TASK-1", "pattern_id": "review-wave",
                 "dispatch_id": "disp-1", "workflow_run_id": "wf-cancel"})])
    assert not any(row.type in {"workflow.invoke.accepted", "task.fanout.requested"}
                   for row in log.read_all())
    assert any(row.type == "workflow.invoke.rejected" and "terminal" in str(row.payload)
               for row in log.read_all())
    assert TaskStore(state / "kanban.json").get("TASK-1").status != "in_progress"
    assert not transport.sent


def test_light_invoke_cancelled_after_admission_cannot_publish_entry(tmp_path, monkeypatch):
    from zf.runtime import run_admission

    cfg = _config()
    cfg.workflow.flow_metadata = {"topology": "light", "light_entry_trigger": "light.ready"}
    state, log, transport, orch = _state(tmp_path, config=cfg)
    real_admit = run_admission.admit_workflow_invoke

    def cancel_after_admission(host, event):
        admitted = real_admit(host, event)
        host.event_writer.append(ZfEvent(type="run.cancelled",
            payload={"run_id": "wf-light-cancel"}, correlation_id="wf-light-cancel"))
        return admitted

    monkeypatch.setattr(run_admission, "admit_workflow_invoke", cancel_after_admission)
    payload = {"workflow_run_id": "wf-light-cancel", "light_entry_payload": {}}
    event = ZfEvent(type="workflow.invoke.requested", task_id="TASK-1", payload=payload)
    result = orch._admit_light_workflow_invoke(
        event, payload=payload, task_id="TASK-1", pattern_id="light", entry_trigger="light.ready")
    assert result.action == "block"
    assert not any(row.type in {"workflow.invoke.accepted", "light.ready"} for row in log.read_all())
    assert TaskStore(state / "kanban.json").get("TASK-1").status != "in_progress"


def test_cancelled_invoke_replay_cannot_reactivate_task(tmp_path):
    state, log, transport, orch = _state(tmp_path)
    accepted = ZfEvent(type="workflow.invoke.accepted", task_id="TASK-1",
                      payload={"workflow_run_id": "wf-replay-cancel", "task_id": "TASK-1"})
    orch.event_writer.append(accepted)
    orch.event_writer.append(ZfEvent(type="run.cancelled", payload={"run_id": "wf-replay-cancel"}))
    request = ZfEvent(type="workflow.invoke.requested", task_id="TASK-1", payload=accepted.payload)
    assert not orch._activate_replayed_workflow_task(request, accepted)
    assert TaskStore(state / "kanban.json").get("TASK-1").status != "in_progress"

def test_workflow_invoke_rejects_declared_writer_entry_before_side_effects(
    tmp_path: Path,
) -> None:
    config = _config()
    config.workflow.stages[0].topology = "fanout_writer_scoped"
    config.workflow.stages[0].trigger = "task_map.ready"
    _state_dir, log, transport, orch = _state(tmp_path, config=config)

    decision = orch._on_workflow_invoke_requested(ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "expected_output": "implementation",
        },
    ))

    events = log.read_all()
    assert decision is not None
    assert decision.action == "block"
    assert any(event.type == "workflow.invoke.rejected" for event in events)
    assert not any(event.type == "workflow.invoke.accepted" for event in events)
    assert not any(event.type == "task.fanout.requested" for event in events)
    assert transport.sent == []


def test_declared_writer_fanout_does_not_fall_back_to_generic_fanout(
    tmp_path: Path,
) -> None:
    config = _config()
    config.workflow.stages[0].topology = "fanout_writer_scoped"
    config.workflow.stages[0].trigger = "task_map.ready"
    _state_dir, log, transport, orch = _state(tmp_path, config=config)
    requested = ZfEvent(
        type="task.fanout.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "pattern_id": "review-wave",
            "requested_specialists": ["review-a"],
            "expected_output": "implementation",
        },
    )

    decision = orch._on_task_fanout_requested(requested)

    assert decision is not None
    assert decision.action == "block"
    events = log.read_all()
    assert any(event.type == "task.fanout.rejected" for event in events)
    assert not any(event.type == "fanout.started" for event in events)
    assert transport.sent == []


def test_workflow_invoke_starts_declared_fanout_only_after_admission(
    tmp_path: Path,
) -> None:
    config = _config()
    config.workflow.stages[0].trigger = "workflow.invoke.requested"
    _state_dir, log, _transport, orch = _state(tmp_path, config=config)
    orch.task_store.update(
        "TASK-1",
        status="in_progress",
        assigned_to="review-a",
    )
    invoke = orch.event_writer.append(ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="ch-zaofu",
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "requested_by": "qa",
            "reason": "risk review",
            "source_refs": {},
            "expected_output": "review report",
        },
    ))

    orch.run_once(events=[invoke])
    fanout_request = next(
        event for event in log.read_all()
        if event.type == "task.fanout.requested"
    )
    orch.run_once(events=[fanout_request])

    started = [
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "review-wave"
    ]
    assert len(started) == 1
    assert started[0].payload["trigger_event_id"] == fanout_request.id


def test_scoped_workflow_invoke_preserves_identity_into_declared_fanout(
    tmp_path: Path,
) -> None:
    cfg = _config(target_ref="")
    cfg.workflow.stages[0].flow_kind = "issue"
    _state_dir, log, transport, orch = _state(
        tmp_path,
        config=cfg,
        workflow_anchor=True,
    )

    identity = {
        "request_id": "req-issue-1",
        "run_id": "run-issue-1",
        "workflow_run_id": "run-issue-1",
        "flow_kind": "issue",
        "request_kind": "issue",
        "workflow_request_ref": "workflow-requests/req-issue-1.json",
        "requirement_spec_ref": "requirements/revision-0002.json",
        "requirement_spec_digest": "a" * 64,
        "request_revision": 2,
        "effective_config_ref": {
            "ref": "artifacts/workflow/effective-config.json",
            "sha256": "d" * 64,
        },
        "effective_config_digest": "d" * 64,
        "run_contract_ref": "run-contracts/run-issue-1.json",
        "run_contract_digest": "e" * 64,
    }
    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="run-issue-1",
        payload={
            **identity,
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "expected_output": "review report",
        },
    )])
    _run_until_sent(orch, transport, 2)

    events = log.read_all()
    fanout_request = next(
        event for event in events if event.type == "task.fanout.requested"
    )
    for key, value in identity.items():
        assert fanout_request.payload[key] == value
    assert any(event.type == "fanout.started" for event in events)
    assert not any(event.type == "task.fanout.rejected" for event in events)


def test_generic_workflow_invoke_preserves_goal_identity_into_child_briefing(
    tmp_path: Path,
) -> None:
    cfg = _config(target_ref="")
    cfg.workflow.stages[0].flow_kind = "workflow"
    state_dir, log, transport, orch = _state(
        tmp_path,
        config=cfg,
        workflow_anchor=True,
    )
    identity = {
        "request_id": "req-generic-1",
        "run_id": "run-generic-1",
        "workflow_run_id": "run-generic-1",
        "flow_kind": "workflow",
        "request_kind": "workflow",
        "request_revision": 3,
        "goal_id": "goal-generic-1",
        "workflow_generation": "a" * 64,
        "generic_workflow_contract_digest": "b" * 64,
        "workflow_intent": "research",
        "workflow_template": "evidence-synthesis-v1",
        "completion_profile": "artifact_delivery",
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
        }],
        "goal_claim_set_ref": (
            "artifacts/goal-closure/claim-sets/current.json"
        ),
        "goal_claim_set_digest": "c" * 64,
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "d" * 64,
        "input_result_refs": [
            "artifacts/call-results/envelopes/" + "e" * 64 + ".json"
        ],
    }

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="run-generic-1",
        payload={
            **identity,
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "expected_output": "verified report",
        },
    )])
    _run_until_sent(orch, transport, 2)

    events = log.read_all()
    accepted = next(
        event for event in events if event.type == "workflow.invoke.accepted"
    )
    fanout_request = next(
        event for event in events if event.type == "task.fanout.requested"
    )
    for key, value in identity.items():
        assert accepted.payload[key] == value
        assert fanout_request.payload[key] == value
    manifest_path = next((state_dir / "fanouts").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    child_identity_keys = (
        "workflow_run_id",
        "flow_kind",
        "request_revision",
        "goal_id",
        "workflow_generation",
        "generic_workflow_contract_digest",
        "workflow_intent",
        "workflow_template",
        "completion_profile",
        "required_delivery_artifacts",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "run_contract_ref",
        "run_contract_digest",
        "input_result_refs",
    )
    for child in manifest["children"]:
        child_payload = child["payload"]
        for key in child_identity_keys:
            assert child_payload[key] == identity[key]
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert '"goal_id": "goal-generic-1"' in briefing
    assert '"workflow_intent": "research"' in briefing
    assert identity["goal_claim_set_ref"] in briefing


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
        "effective_config_ref": {
            "ref": "artifacts/workflow/effective-config.json",
            "sha256": "d" * 64,
        },
        "effective_config_digest": "d" * 64,
        "run_contract_ref": "run-contracts/wf-durable-review.json",
        "run_contract_digest": "e" * 64,
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
    parent_request = hydrate_sidecar_ref(
        _state_dir,
        parent_requested[0].payload["request_ref"],
    ).payload["request"]
    assert parent_request["effective_config_ref"] == payload[
        "effective_config_ref"
    ]
    assert parent_request["effective_config_digest"] == "d" * 64

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
    for event in child_requests:
        child_request = hydrate_sidecar_ref(
            _state_dir,
            event.payload["request_ref"],
        ).payload["request"]
        assert child_request["effective_config_ref"] == payload[
            "effective_config_ref"
        ]
        assert child_request["effective_config_digest"] == "d" * 64
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
            state_dir=_state_dir,
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


def test_durable_workflow_entry_replan_gets_new_operation_identity(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(
        tmp_path,
        config=_config(durable=True, target_ref=""),
        workflow_anchor=True,
    )
    original_payload = {
        "task_id": "TASK-1",
        "pattern_id": "review-wave",
        "workflow_run_id": "wf-entry-replan",
        "workflow_generation": "a" * 64,
        "expected_output": "review report",
    }
    original = orch._prepare_workflow_invoke_operation(  # type: ignore[attr-defined]
        event=ZfEvent(
            type="workflow.invoke.requested",
            task_id="TASK-1",
            correlation_id="wf-entry-replan",
            payload=original_payload,
        ),
        payload=original_payload,
        task_id="TASK-1",
        pattern_id="review-wave",
        topology="fanout_reader",
        target_ref="",
        roles=["review-a", "review-b"],
    )
    assert original is not None

    replan_payload = {
        **original_payload,
        "rework_of": "evt-review-failed",
        "rework_attempt": 1,
        "rework_feedback": [{
            "severity": "high",
            "message": "Verifier timed out.",
        }],
    }
    replan = orch._prepare_workflow_invoke_operation(  # type: ignore[attr-defined]
        event=ZfEvent(
            type="workflow.invoke.requested",
            task_id="TASK-1",
            correlation_id="wf-entry-replan",
            payload=replan_payload,
        ),
        payload=replan_payload,
        task_id="TASK-1",
        pattern_id="review-wave",
        topology="fanout_reader",
        target_ref="",
        roles=["review-a", "review-b"],
    )

    assert replan is not None
    assert replan.created is True
    assert replan.operation_id != original.operation_id
    request_event = next(
        event
        for event in reversed(orch.event_log.read_all())
        if event.type == "workflow.operation.requested"
        and event.payload["operation_id"] == replan.operation_id
    )
    request = hydrate_sidecar_ref(
        state_dir,
        request_event.payload["request_ref"],
    ).payload["request"]
    assert request["rework_of"] == "evt-review-failed"
    assert request["rework_attempt"] == 1
    assert request["rework_feedback"][0]["message"] == "Verifier timed out."


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


def test_workflow_managed_parent_is_not_dispatched_as_ordinary_task(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _empty_state(tmp_path)
    task = Task(
        id="TASK-WORKFLOW-PARENT",
        title="Execute only through the selected workflow",
        contract=TaskContract(
            behavior="Run the selected workflow.",
            verification="Observe the workflow fanout.",
            verification_tiers=["runtime"],
            evidence_contract={"execution_owner": "workflow"},
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    created = ZfEvent(
        type="task.created",
        actor="web",
        task_id=task.id,
        payload={"task": {"id": task.id}},
    )
    log.append(created)

    for _ in range(3):
        orch.run_once(events=[created])

    assert transport.sent == []
    assert not any(
        event.type == "task.dispatched" and event.task_id == task.id
        for event in log.read_all()
    )


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


def test_workflow_invoke_rejects_unapproved_proposal_binding(
    tmp_path: Path,
) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    decision = orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="req-unapproved",
        payload={
            "request_id": "req-unapproved",
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "workflow_proposal_ref": {
                "ref": "artifacts/workflow/proposal.json",
                "sha256": "a" * 64,
            },
            "workflow_proposal_digest": "b" * 64,
        },
    )])

    decisions = decision
    assert decisions
    assert decisions[0].action == "block"
    rejected = next(
        event
        for event in log.read_all()
        if event.type == "workflow.invoke.rejected"
    )
    assert "binding is incomplete" in rejected.payload["reason"]
    assert not any(
        event.type == "task.fanout.requested"
        for event in log.read_all()
    )


@pytest.mark.parametrize("consumer_stale", [False, True])
def test_workflow_invoke_accepts_exact_approved_proposal_binding(
    tmp_path: Path, consumer_stale: bool,
) -> None:
    from zf.core.config.render import renderable_config_to_primitive

    state_dir, log, _transport, orch = _state(tmp_path)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(yaml.safe_dump(renderable_config_to_primitive(orch.config)))
    config = load_config(config_path)
    orch.config = config
    requirement_path = (
        state_dir / "workflow-requests" / "req-approved" / "requirement.json"
    )
    requirement_path.parent.mkdir(parents=True)
    requirement_path.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": "req-approved",
            "revision": 1,
        }),
        encoding="utf-8",
    )
    request = {
        "schema_version": "workflow.request.v1",
        "request_id": "req-approved",
        "kind": "issue",
        "status": "ready",
        "revision": 1,
        "requirement_spec_ref": str(requirement_path),
        "requirement_spec_digest": hashlib.sha256(
            requirement_path.read_bytes()
        ).hexdigest(),
        "open_questions": [],
    }
    request_path = workflow_request_path(state_dir, "req-approved")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    proposal, proposal_ref = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=config_path,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
    )
    mark_workflow_request(
        state_dir,
        "req-approved",
        status="approved",
        actor="operator",
    )
    mark_workflow_request(
        state_dir,
        "req-approved",
        status="submitted",
        actor="operator",
        run_id="run-approved",
    )
    contract = bind_run_contract_workflow_artifacts(
        build_run_contract(
            config,
            config_path=config_path,
            project_root=tmp_path,
            state_dir=state_dir,
        ),
        proposal_ref=proposal_ref,
        proposal_digest=proposal["proposal_digest"],
        effective_config_ref=proposal["effective_config_ref"],
    )
    run_contract_ref = write_run_contract_snapshot(state_dir, contract)

    if consumer_stale:
        orch.config.roles[0].model = "stale-provider-model"

    decisions = orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="run-approved",
        payload={
            "request_id": "req-approved",
            "workflow_run_id": "run-approved",
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "pattern_id": "review-wave",
            "workflow_proposal_ref": proposal_ref,
            "workflow_proposal_digest": proposal["proposal_digest"],
            "effective_config_ref": proposal["effective_config_ref"],
            "effective_config_digest": proposal[
                "effective_config_ref"
            ]["sha256"],
            "run_contract_ref": run_contract_ref["ref"],
            "run_contract_digest": run_contract_ref["contract_digest"],
        },
    )])

    if consumer_stale:
        assert decisions and decisions[0].action == "block"
        rejected = [e for e in log.read_all() if e.type == "workflow.invoke.rejected"]
        assert len(rejected) == 1
        assert "consumer_config_stale" in rejected[0].payload["reason"]
        assert not any(e.type == "task.fanout.requested" for e in log.read_all())
        return

    assert decisions and decisions[0].action == "workflow_invoke"
    assert not any(
        event.type == "workflow.invoke.rejected"
        for event in log.read_all()
    )
    requested = next(
        event
        for event in log.read_all()
        if event.type == "task.fanout.requested"
    )
    assert requested.payload["effective_config_ref"] == proposal[
        "effective_config_ref"
    ]
    assert requested.payload["run_contract_digest"] == contract[
        "contract_digest"
    ]


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
