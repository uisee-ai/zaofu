from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_briefing import (
    build_orchestrator_agent_operation_briefing,
)
from zf.runtime.orchestrator_agent_operations import (
    CHECKPOINT_REQUESTED,
    DECISION_SUBMITTED,
    activate_orchestrator_agent_operation,
    interrupt_orchestrator_agent_operation,
    request_orchestrator_agent_checkpoint,
    retry_orchestrator_agent_operation,
)
from zf.runtime.orchestrator_agent_recovery import (
    reconcile_orchestrator_agent_operation_liveness,
    requeue_orchestrator_agent_checkpoint_after_respawn,
)
from zf.runtime.orchestrator_agent_transport import (
    dispatch_orchestrator_agent_operation,
)
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import load_workflow_operation


class RecordingTransport(TmuxTransport):
    def __init__(self) -> None:
        super().__init__(TmuxSession(session_name="oa-test", dry_run=True))
        self.sent: list[tuple[str, Path, str]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):
        self.sent.append((role_name, briefing_path, prompt))


class PaneDeadOnceTransport(RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def send_task(self, role_name, briefing_path, prompt, *, context=None):
        if self.fail:
            error = RuntimeError("pane is not running (reason=pane_dead)")
            error.backend = "codex"
            error.dead_reason = "pane_dead"
            error.current_command = ""
            error.process_probe = {
                "available": False,
                "pane_pid": "",
                "current_command": "",
                "processes": [],
            }
            raise error
        super().send_task(
            role_name,
            briefing_path,
            prompt,
            context=context,
        )


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="oa-test", workspace="."),
        roles=[RoleConfig(
            name="orchestrator",
            instance_id="orchestrator",
            backend="mock",
            triggers=[CHECKPOINT_REQUESTED],
            allowed_tools=["Read", "Bash(zf artifact *)", "Bash(zf result *)"],
        )],
    )


def _runtime(tmp_path: Path):
    state_dir, log, writer = _state(tmp_path)
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(),
        event_log=log,
        event_writer=writer,
    )
    return runtime


def _source(runtime) -> dict:
    return write_sidecar_json(
        runtime.state_dir,
        "artifacts/requirements/input.json",
        {"schema_version": "requirement.v1", "objective": "ship feature"},
        kind="requirement",
        schema_version="requirement.v1",
        created_by="test",
        required=True,
    )


def _request(
    runtime,
    *,
    trigger_id: str = "evt-run-start",
    original_trigger_event_id: str = "",
    checkpoint: str = "stage_barrier",
    checkpoint_policy: str = "blocking",
    payload_overrides: dict | None = None,
):
    source = _source(runtime)
    trigger = ZfEvent(
        id=trigger_id,
        type="workflow.invoke.requested",
        correlation_id="run-oa-1",
        payload={"workflow_run_id": "run-oa-1"},
    )
    replay_identity = (
        {"original_trigger_event_id": original_trigger_event_id}
        if original_trigger_event_id
        else {}
    )
    return request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint=checkpoint,
        checkpoint_policy=checkpoint_policy,
        workflow_run_id="run-oa-1",
        source_event=trigger,
        payload={
            "workflow_run_id": "run-oa-1",
            "request_revision": "1",
            **replay_identity,
            "result_refs": [{
                "source_id": "requirement",
                "kind": "requirement",
                **source,
            }],
            "aggregation_input_refs": [source],
            **dict(payload_overrides or {}),
        },
    )


def _decision(prepared) -> dict:
    result_refs = [
        {
            "ref": str(item.get("ref") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in prepared.context.input_body["aggregation_input_refs"]
    ]
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": prepared.checkpoint,
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref["sha256"],
        },
        "decision": "continue",
        "reason_codes": ["goal_is_actionable"],
        "affected_work_units": [],
        "required_followup": "continue",
        "expected_outcome": "admitted graph continues",
        "confidence": 0.9,
        "aggregation_result": {
            "schema_version": "orchestration-result.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "checkpoint": prepared.checkpoint,
            },
            "input_result_refs": result_refs,
            "selected_result_refs": result_refs,
            "rejected_result_refs": [],
            "unclosed_claim_ids": [],
            "provenance_map": [],
            "remaining_uncertainty": [],
            "recommendation": "continue",
        },
    }


def test_checkpoint_operation_is_stable_and_all_sources_are_required(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    first = _request(runtime)
    replay = _request(runtime)

    assert first.operation_id == replay.operation_id
    assert replay.replay_hit is True
    assert len([
        event for event in runtime.event_log.read_all()
        if event.type == CHECKPOINT_REQUESTED
    ]) == 1
    source_ids = {
        source["source_id"] for source in first.context.source_manifest["sources"]
    }
    required_ids = {
        row["source_id"] for row in first.context.read_policy["required_reads"]
    }
    assert required_ids == source_ids
    assert {"effective-config", "requirement", "checkpoint-input"} <= source_ids


def test_low_risk_shadow_checkpoint_skip_is_stable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.orchestration.shadow_sample_percent = 0

    first = _request(
        runtime,
        checkpoint="plan_candidate",
        checkpoint_policy="shadow",
    )
    replay = _request(
        runtime,
        checkpoint="plan_candidate",
        checkpoint_policy="shadow",
    )

    assert first.status == replay.status == "skipped"
    assert first.operation_id == replay.operation_id
    assert first.context is None
    events = runtime.event_log.read_all()
    skipped = [
        event for event in events
        if event.type == "orchestrator.semantic.checkpoint.skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "shadow_sample_not_selected"
    assert not [event for event in events if event.type == CHECKPOINT_REQUESTED]
    assert not [
        event for event in events
        if event.type == "workflow.operation.requested"
    ]


def test_selected_low_risk_shadow_uses_compact_checkpoint_pack(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.orchestration.shadow_sample_percent = 100

    prepared = _request(
        runtime,
        checkpoint="plan_candidate",
        checkpoint_policy="shadow",
    )

    assert prepared.status == "requested"
    assert prepared.context is not None
    context = prepared.context
    assert context.input_body["input_mode"] == "compact"
    assert context.input_body["risk_signals"] == []
    source_ids = {
        source["source_id"] for source in context.source_manifest["sources"]
    }
    required_ids = {
        row["source_id"] for row in context.read_policy["required_reads"]
    }
    assert {"effective-config", "effective-config-summary", "requirement"} <= source_ids
    assert required_ids == {
        "checkpoint-pack",
        "effective-config-summary",
        "requirement",
    }
    pack_source = next(
        source for source in context.source_manifest["sources"]
        if source["source_id"] == "checkpoint-pack"
    )
    pack = hydrate_sidecar_ref(runtime.state_dir, pack_source).payload
    assert pack["schema_version"] == "orchestrator-agent-compact-checkpoint-pack.v1"
    assert {
        row["source_id"] for row in pack["source_index"]
    } == {
        "checkpoint-input",
        "effective-config",
        "effective-config-summary",
        "requirement",
    }
    assert pack["required_source_ids"] == [
        "effective-config-summary",
        "requirement",
    ]
    assert pack["optional_source_ids"] == [
        "checkpoint-input",
        "effective-config",
    ]
    assert not any("payload" in row for row in pack["source_index"])
    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=runtime.state_dir,
        prepared=prepared,
    )
    assert briefing.count("zf artifact read") == 3
    assert "## Optional Canonical Inputs" in briefing


def test_shadow_risk_override_uses_exhaustive_sources(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.orchestration.shadow_sample_percent = 0

    prepared = _request(
        runtime,
        checkpoint="plan_candidate",
        checkpoint_policy="shadow",
        payload_overrides={"feedback_revision": "feedback-2"},
    )

    assert prepared.status == "requested"
    assert prepared.context is not None
    context = prepared.context
    assert context.input_body["input_mode"] == "exhaustive"
    assert context.input_body["risk_signals"] == ["feedback_revision"]
    source_ids = {
        source["source_id"] for source in context.source_manifest["sources"]
    }
    required_ids = {
        row["source_id"] for row in context.read_policy["required_reads"]
    }
    assert required_ids == source_ids
    assert {
        "effective-config",
        "requirement",
        "checkpoint-input",
    } <= required_ids
    assert "checkpoint-pack" not in source_ids


def test_equivalent_trigger_replay_keeps_running_operation_and_accepts_late_result(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    credential_path = provision_role_submit_credential(
        runtime.state_dir,
        "orchestrator",
    )
    provision_role_artifact_read_credential(
        runtime.state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    first = _request(runtime)
    activate_orchestrator_agent_operation(
        runtime,
        first,
        dispatch_id="dispatch-1",
        causation_id="evt-run-start",
    )

    replay = _request(
        runtime,
        trigger_id="evt-redispatch",
        original_trigger_event_id="evt-run-start",
    )

    assert replay.operation_id == first.operation_id
    assert replay.request_hash == first.request_hash
    assert replay.status == "running"
    assert replay.replay_hit is True
    events = runtime.event_log.read_all()
    assert len([event for event in events if event.type == CHECKPOINT_REQUESTED]) == 1
    assert not [
        event
        for event in events
        if event.type == "workflow.operation.blocked"
        and event.payload.get("reason") == "request_hash_divergence"
    ]

    for source in first.context.source_manifest["sources"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=first.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=first.operation_id,
        semantic_result=_decision(first),
        role_instance="orchestrator",
        credential=credential_path.read_text(encoding="utf-8").strip(),
    )

    assert submitted.canonical_event_type == DECISION_SUBMITTED
    assert load_workflow_operation(
        runtime.event_log,
        first.operation_id,
    )["status"] == "settled"


def test_checkpoint_briefing_is_operation_scoped(tmp_path: Path) -> None:
    prepared = _request(_runtime(tmp_path))

    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=prepared.context.input_ref and tmp_path / "state",
        prepared=prepared,
    )

    assert prepared.operation_id in briefing
    assert "zf artifact read" in briefing
    assert "zf result submit" in briefing
    assert "## Features" not in briefing
    assert "Kanban" not in briefing


def test_plan_candidate_briefing_distinguishes_logical_owner_from_lane(
    tmp_path: Path,
) -> None:
    prepared = _request(_runtime(tmp_path), checkpoint="plan_candidate")

    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=tmp_path / "state",
        prepared=prepared,
    )

    assert "logical capability or affinity labels" in briefing
    assert "logical-owner/physical-lane name mismatch is not evidence" in briefing


def test_semantic_submit_requires_complete_read_ledger(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    token_path = provision_role_submit_credential(
        runtime.state_dir,
        "orchestrator",
    )
    provision_role_artifact_read_credential(
        runtime.state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    prepared = _request(runtime)
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id="dispatch-1",
        causation_id="evt-run-start",
    )
    submitter = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    credential = token_path.read_text(encoding="utf-8").strip()

    with pytest.raises(ResultSubmitError, match="required_read_missing"):
        submitter.submit(
            operation_id=prepared.operation_id,
            semantic_result=_decision(prepared),
            role_instance="orchestrator",
            credential=credential,
        )

    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=prepared.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    submitted = submitter.submit(
        operation_id=prepared.operation_id,
        semantic_result=_decision(prepared),
        role_instance="orchestrator",
        credential=credential,
    )

    assert submitted.canonical_event_type == "orchestrator.semantic.decision.submitted"
    assert load_workflow_operation(
        runtime.event_log,
        prepared.operation_id,
    )["status"] == "settled"
    hydrated = ControlResultAdapterRegistry().profile(
        "orchestrator-semantic-decision",
        "1",
    )
    assert hydrated.schema_version == "orchestration-decision.v1"


def test_runtime_dispatches_checkpoint_with_product_briefing(tmp_path: Path) -> None:
    state_dir, log, _writer = _state(tmp_path)
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    transport = RecordingTransport()
    runtime = Orchestrator(
        state_dir,
        _config(),
        transport,
        project_root=tmp_path,
    )
    prepared = _request(runtime)
    checkpoint = next(
        event for event in log.read_all() if event.type == CHECKPOINT_REQUESTED
    )

    runtime.run_once([checkpoint])

    assert len(transport.sent) == 1
    role, briefing_path, prompt = transport.sent[0]
    assert role == "orchestrator"
    assert prepared.operation_id in briefing_path.read_text(encoding="utf-8")
    assert "typed Orchestrator Agent semantic checkpoint" in prompt
    assert load_workflow_operation(log, prepared.operation_id)["status"] == "running"


def test_checkpoint_pane_death_requeues_same_operation_after_respawn(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    transport = PaneDeadOnceTransport()
    runtime = Orchestrator(
        state_dir,
        _config(),
        transport,
        project_root=tmp_path,
    )
    prepared = _request(runtime)
    checkpoint = next(
        event for event in log.read_all() if event.type == CHECKPOINT_REQUESTED
    )
    role = runtime._find_role_by_name("orchestrator")
    assert role is not None

    dispatch_orchestrator_agent_operation(runtime, role, checkpoint)

    operation = load_workflow_operation(log, prepared.operation_id)
    assert operation is not None
    assert operation["status"] == "suspended"
    events = log.read_all()
    assert not [event for event in events if event.type == "workflow.operation.failed"]
    respawn = next(
        event for event in events if event.type == "worker.respawn.requested"
    )
    retry_checkpoint = requeue_orchestrator_agent_checkpoint_after_respawn(
        runtime,
        respawn,
        instance_id="orchestrator",
    )
    assert retry_checkpoint is not None

    transport.fail = False
    dispatch_orchestrator_agent_operation(runtime, role, retry_checkpoint)

    operation = load_workflow_operation(log, prepared.operation_id)
    assert operation is not None
    assert operation["status"] == "running"
    assert operation["retry_count"] == 1
    assert operation["retry_attempt"] == 1
    assert len(transport.sent) == 1
    assert requeue_orchestrator_agent_checkpoint_after_respawn(
        runtime,
        respawn,
        instance_id="orchestrator",
    ) is None


def test_running_checkpoint_pane_death_is_recovered_once_after_send(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.transport = SimpleNamespace(is_alive=lambda role_name: False)
    prepared = _request(runtime)
    checkpoint = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == CHECKPOINT_REQUESTED
    )
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id=checkpoint.id,
        causation_id=checkpoint.id,
    )

    reconcile_orchestrator_agent_operation_liveness(runtime)
    reconcile_orchestrator_agent_operation_liveness(runtime)

    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert operation is not None
    assert operation["status"] == "suspended"
    assert operation["reason"] == (
        "transient_transport:pane_dead:post_dispatch_probe"
    )
    events = runtime.event_log.read_all()
    assert len([
        event for event in events
        if event.type == "orchestrator.dispatch_failed"
        and event.payload.get("operation_id") == prepared.operation_id
    ]) == 1
    assert len([
        event for event in events
        if event.type == "worker.respawn.requested"
        and event.payload.get("operation_id") == prepared.operation_id
    ]) == 1
    assert len([
        event for event in events
        if event.type == "orchestrator.dispatch.retry_requested"
        and event.payload.get("operation_id") == prepared.operation_id
    ]) == 1


def test_graceful_stop_checkpoint_redrives_once_per_restart(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    transport = RecordingTransport()
    runtime = Orchestrator(
        state_dir,
        _config(),
        transport,
        project_root=tmp_path,
    )
    prepared = _request(runtime)
    role = runtime._find_role_by_name("orchestrator")
    assert role is not None
    checkpoint = next(
        event for event in log.read_all() if event.type == CHECKPOINT_REQUESTED
    )
    dispatch_orchestrator_agent_operation(runtime, role, checkpoint)

    for restart_number in (1, 2):
        interrupt_orchestrator_agent_operation(
            runtime,
            prepared,
            reason="graceful_stop",
            causation_id=f"stop-{restart_number}",
        )

        reconcile_orchestrator_agent_operation_liveness(runtime)
        reconcile_orchestrator_agent_operation_liveness(runtime)

        restart_checkpoints = [
            event
            for event in log.read_all()
            if event.type == CHECKPOINT_REQUESTED
            and event.payload.get("restart_interruption_event_id")
        ]
        assert len(restart_checkpoints) == restart_number
        operation = load_workflow_operation(log, prepared.operation_id)
        assert operation is not None
        assert operation["status"] == "requested"
        assert operation["redrive_count"] == restart_number

        dispatch_orchestrator_agent_operation(
            runtime,
            role,
            restart_checkpoints[-1],
        )
        operation = load_workflow_operation(log, prepared.operation_id)
        assert operation is not None
        assert operation["status"] == "running"

    assert len(transport.sent) == 3
    assert not [
        event
        for event in log.read_all()
        if event.type == "worker.respawn.requested"
    ]


def test_restart_cancels_superseded_plan_candidate_checkpoint(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    transport = RecordingTransport()
    runtime = Orchestrator(
        state_dir,
        _config(),
        transport,
        project_root=tmp_path,
    )
    older = _request(
        runtime,
        trigger_id="evt-plan-older",
        checkpoint="plan_candidate",
        payload_overrides={"task_map_generation": "generation-older"},
    )
    newer = _request(
        runtime,
        trigger_id="evt-plan-newer",
        checkpoint="plan_candidate",
        payload_overrides={"task_map_generation": "generation-newer"},
    )
    role = runtime._find_role_by_name("orchestrator")
    assert role is not None
    for prepared in (older, newer):
        checkpoint = next(
            event
            for event in log.read_all()
            if event.type == CHECKPOINT_REQUESTED
            and event.payload.get("operation_id") == prepared.operation_id
        )
        dispatch_orchestrator_agent_operation(runtime, role, checkpoint)
        interrupt_orchestrator_agent_operation(
            runtime,
            prepared,
            reason="graceful_stop",
            causation_id=f"stop-{prepared.operation_id}",
        )

    reconcile_orchestrator_agent_operation_liveness(runtime)
    reconcile_orchestrator_agent_operation_liveness(runtime)

    restart_checkpoints = [
        event
        for event in log.read_all()
        if event.type == CHECKPOINT_REQUESTED
        and event.payload.get("restart_interruption_event_id")
    ]
    assert [
        event.payload["operation_id"] for event in restart_checkpoints
    ] == [newer.operation_id]
    older_operation = load_workflow_operation(log, older.operation_id)
    newer_operation = load_workflow_operation(log, newer.operation_id)
    assert older_operation is not None
    assert older_operation["status"] == "cancelled"
    assert older_operation["reason"] == (
        "OA plan_candidate superseded by newer revision"
    )
    assert newer_operation is not None
    assert newer_operation["status"] == "requested"


def test_running_checkpoint_liveness_sweep_preserves_live_provider(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.transport = SimpleNamespace(is_alive=lambda role_name: True)
    prepared = _request(runtime)
    checkpoint = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == CHECKPOINT_REQUESTED
    )
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id=checkpoint.id,
        causation_id=checkpoint.id,
    )

    reconcile_orchestrator_agent_operation_liveness(runtime)

    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert operation is not None
    assert operation["status"] == "running"
    assert not [
        event
        for event in runtime.event_log.read_all()
        if event.type == "worker.respawn.requested"
    ]


def test_running_checkpoint_second_pane_death_blocks_without_retry_loop(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.transport = SimpleNamespace(is_alive=lambda role_name: False)
    prepared = _request(runtime)
    checkpoint = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == CHECKPOINT_REQUESTED
    )
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id=checkpoint.id,
        causation_id=checkpoint.id,
    )
    reconcile_orchestrator_agent_operation_liveness(runtime)
    retry_orchestrator_agent_operation(
        runtime,
        prepared,
        retry_attempt=1,
        dispatch_id=checkpoint.id,
        causation_id=checkpoint.id,
    )

    reconcile_orchestrator_agent_operation_liveness(runtime)
    reconcile_orchestrator_agent_operation_liveness(runtime)

    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert operation is not None
    assert operation["status"] == "blocked"
    assert operation["retry_count"] == 1
    assert len([
        event
        for event in runtime.event_log.read_all()
        if event.type == "worker.respawn.requested"
    ]) == 1


def test_context_sweep_wires_taskless_oa_liveness_recovery(monkeypatch) -> None:
    from zf.runtime import orchestrator_agent_recovery
    from zf.runtime import orchestrator_periodic_sweep

    called: list[str] = []
    runtime = SimpleNamespace(
        _check_context_thresholds=lambda: called.append("context"),
        _check_pending_recycles=lambda: called.append("recycles"),
        _safe_housekeeping=lambda name, callback: callback(),
    )
    monkeypatch.setattr(
        orchestrator_agent_recovery,
        "reconcile_orchestrator_agent_operation_liveness",
        lambda current: called.append("oa-liveness"),
    )

    orchestrator_periodic_sweep.run_context_sweep(runtime)

    assert called == ["context", "recycles", "oa-liveness"]


@pytest.mark.parametrize("restart", [False, True])
def test_checkpoint_requests_share_one_role_without_overwriting(tmp_path, restart):
    state_dir, log, writer = _state(tmp_path)
    transport = RecordingTransport()
    runtime = Orchestrator(state_dir, _config(), transport, project_root=tmp_path)
    first = _request(runtime, trigger_id="evt-one")
    second = _request(runtime, trigger_id="evt-two")
    requests = [e for e in log.read_all() if e.type == CHECKPOINT_REQUESTED]
    runtime.run_once(requests)
    assert len(transport.sent) == 1
    assert load_workflow_operation(log, first.operation_id)["status"] == "running"
    assert load_workflow_operation(log, second.operation_id)["status"] == "requested"
    from zf.runtime.orchestrator_agent_operations import fail_orchestrator_agent_operation
    fail_orchestrator_agent_operation(runtime, first, reason="fixture_failure", causation_id=requests[0].id)
    if restart:
        runtime = Orchestrator(state_dir, _config(), transport, project_root=tmp_path)
    runtime.run_once([])
    assert len(transport.sent) == 2
    assert load_workflow_operation(log, second.operation_id)["status"] == "running"
    runtime.run_once(requests)
    assert len(transport.sent) == 2
