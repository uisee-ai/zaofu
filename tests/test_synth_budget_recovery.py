"""Owner budget amendment reaches the actual synth transport without new inputs."""

from argparse import Namespace

import pytest

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
from zf.cli.goal import _run_set
from zf.core.config.schema import ExecutionProfileConfig, ExecutionProfileLimitsConfig
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume
from zf.runtime.workflow_budget_guard import _operation_limits


def _blocked_synth(tmp_path, monkeypatch, *, refusal=False, dimension="usage_samples"):
    state, log, runtime, fanout = _task_map_synth_orchestrator(
        tmp_path, complete_child=False
    )
    runtime.config.workflow.execution_profiles["direct-v1"] = ExecutionProfileConfig(
        limits=ExecutionProfileLimitsConfig(max_usage_samples=60, cost_budget_usd=8, token_budget=1000)
    )
    runtime.run_once(
        events=[
            ZfEvent(
                type="task_map.child.completed",
                actor="source-reader",
                correlation_id="trace-product",
                payload={
                    "fanout_id": fanout,
                    "child_id": "source-reader",
                    "run_id": f"run-{fanout}-source-reader",
                    "status": "completed",
                    "summary": "source read",
                    "artifact_refs": ["docs/product-prd.md"],
                },
            )
        ]
    )
    dispatch = next(e for e in log.read_all() if e.type == "fanout.synth.dispatched")
    p = dispatch.payload
    service = WorkflowOperationService(
        state_dir=state, event_log=log, event_writer=runtime.event_writer
    )
    exceeded = runtime.event_writer.append(
        ZfEvent(
            type="workflow.budget.exceeded",
            actor="zf-cli",
            payload={
                "scope": "operation",
                "scope_id": p["operation_id"],
                "workflow_run_id": "trace-product",
                "exceeded_dimensions": [dimension],
                "measurement": {"usage_samples": 60, "total_tokens": 1500},
            },
            correlation_id="trace-product",
        )
    )
    service.block(
        operation_id=p["operation_id"],
        request_hash=p["request_hash"],
        workflow_run_id="trace-product",
        reason="provider_policy_refusal"
        if refusal
        else f"workflow_budget_exceeded:{dimension}",
        causation_id=exceeded.id,
        correlation_id="trace-product",
    )
    monkeypatch.setattr(
        "zf.cli.goal._context",
        lambda _args: Namespace(state_dir=state, config=runtime.config),
    )
    monkeypatch.setattr(
        "zf.cli.goal._projection", lambda *_args: {"run_id": "trace-product"}
    )
    return state, log, runtime, dispatch


@pytest.mark.parametrize("limit", [0, 120])
def test_cli_resume_and_runtime_redispatch_preserve_synth_request(
    tmp_path, monkeypatch, limit
):
    monkeypatch.setenv("ZF_CLI_CMD", "/new/runtime/python -E -m zf.cli.main")
    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch)
    op_id = dispatch.payload["operation_id"]
    original = reduce_workflow_operations(log.read_all())[op_id]
    args = Namespace(
        objective="",
        status="active",
        reason="owner approved review continuation",
        operation=op_id,
        operation_max_usage_samples=limit,
    )
    assert _run_set(args) == 0
    checkpoints = build_workflow_resume_projection(state, runtime.config)[
        "operation_checkpoints"
    ]
    cp = next(c for c in checkpoints if c["operation_id"] == op_id)
    assert cp["safe_resume_action"] == "redrive_budget_amended_synth_operation"
    assert (
        apply_workflow_resume(state, runtime.config, checkpoint_id=cp["checkpoint_id"])[
            "applied"
        ]
        == 1
    )
    sent = len(runtime.transport.sent)
    # Exercise the real runtime recovery caller; no fabricated lost-session event.
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    updated = reduce_workflow_operations(log.read_all())[op_id]
    assert updated["status"] == "running"
    for key in [
        "request_hash",
        "request_ref",
        "active_attempt_id",
        "started_at",
        "budget_snapshot",
    ]:
        assert updated[key] == original[key]
    assert len(runtime.transport.sent) == sent + 1
    assert runtime.transport.sent[-1][3].operation_id == op_id
    assert "/new/runtime/python -E -m zf.cli.main" in runtime.transport.sent[-1][2]
    assert (
        _operation_limits(state, updated, events=log.read_all())["max_usage_samples"]
        == limit
    )
    assert (
        _operation_limits(state, updated, events=log.read_all())["cost_budget_usd"] == 8
    )
    assert not runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    assert len(runtime.transport.sent) == sent + 1


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "wrong_operation",
        "refusal",
        "stale",
        "stale_fanout",
        "request_identity",
    ],
)
def test_synth_recovery_does_not_bypass_amendment_or_currentness(
    tmp_path, monkeypatch, case
):
    state, log, runtime, dispatch = _blocked_synth(
        tmp_path, monkeypatch, refusal=case == "refusal"
    )
    op_id = dispatch.payload["operation_id"]
    sent = len(runtime.transport.sent)
    args = Namespace(
        objective="",
        status="active",
        reason="owner approval",
        operation="unrelated" if case == "wrong_operation" else op_id,
        operation_max_usage_samples=0,
    )
    if case != "absent":
        assert _run_set(args) == (1 if case in {"refusal", "wrong_operation"} else 0)
    if case == "stale":
        runtime.event_writer.append(
            ZfEvent(
                type="run.goal.blocked",
                actor="operator",
                correlation_id="trace-product",
                payload={"run_id": "trace-product", "workflow_run_id": "trace-product"},
            )
        )
    if case in {"stale_fanout", "request_identity"}:
        cp = next(
            c
            for c in build_workflow_resume_projection(state, runtime.config)[
                "operation_checkpoints"
            ]
            if c["operation_id"] == op_id
        )
        assert (
            apply_workflow_resume(
                state, runtime.config, checkpoint_id=cp["checkpoint_id"]
            )["applied"]
            == 1
        )
        if case == "stale_fanout":
            monkeypatch.setattr(
                runtime,
                "_fanout_identity_stale_reason",
                lambda *_a, **_k: ("superseded", "new-fanout"),
            )
        else:
            runtime.event_writer.append(
                ZfEvent(
                    type="fanout.synth.dispatched",
                    actor="zf-cli",
                    payload={**dispatch.payload, "request_hash": "wrong-request"},
                )
            )
    else:
        assert not any(
            c["operation_id"] == op_id
            for c in build_workflow_resume_projection(state, runtime.config)[
                "operation_checkpoints"
            ]
        )
    runtime.event_writer.append(
        ZfEvent(
            type="worker.launch_artifact.written",
            actor="zf-cli",
            payload={
                "instance_id": "task-map-synth",
                "role": "task-map-synth",
                "backend": "mock",
                "launch_attempt": 2,
                "is_resume": True,
            },
        )
    )
    assert not runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    assert len(runtime.transport.sent) == sent


@pytest.mark.parametrize("interrupted", [False, True])
def test_revoked_budget_redrive_cannot_fall_through_to_reader_restart(
    tmp_path, monkeypatch, interrupted
):
    from zf.runtime.synth_budget_recovery import recover_budget_amended_synth

    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch)
    op_id = dispatch.payload["operation_id"]
    assert _run_set(Namespace(objective="", status="active", reason="owner approved",
        operation=op_id, operation_max_usage_samples=0)) == 0
    checkpoint = next(c for c in build_workflow_resume_projection(state, runtime.config)[
        "operation_checkpoints"] if c["operation_id"] == op_id)
    assert apply_workflow_resume(state, runtime.config,
        checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    if interrupted:
        service = WorkflowOperationService(state_dir=state, event_log=log,
            event_writer=runtime.event_writer)
        service.interrupt(operation_id=op_id, request_hash=dispatch.payload["request_hash"],
            workflow_run_id="trace-product", task_id="", reason="graceful_stop")
        checkpoint = next(c for c in build_workflow_resume_projection(state, runtime.config)[
            "operation_checkpoints"] if c["operation_id"] == op_id)
        assert apply_workflow_resume(state, runtime.config,
            checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    runtime.event_writer.append(ZfEvent(type="run.goal.blocked", actor="operator",
        payload={"run_id": "trace-product"}, correlation_id="trace-product"))
    sent = len(runtime.transport.sent)
    assert recover_budget_amended_synth(runtime, dispatch, log.read_all()) is False
    runtime.event_writer.append(ZfEvent(type="worker.launch_artifact.written", actor="zf-cli",
        payload={"instance_id": dispatch.payload["role_instance"], "launch_attempt": 2}))
    assert not runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    assert len(runtime.transport.sent) == sent


@pytest.mark.parametrize(
    "case", ["cancelled", "interrupted", "semantic_reject", "revoked"]
)
def test_budget_recovery_reopens_only_mechanical_carrier_failure(
    tmp_path, monkeypatch, case
):
    from zf.runtime.fanout_recovery_runtime import (
        recover_unrecorded_reader_fanout_results,
    )
    from zf.runtime.workflow_operation import interrupt_active_workflow_operations

    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch)
    op_id = dispatch.payload["operation_id"]
    p = dispatch.payload
    runtime.event_writer.append(
        ZfEvent(
            type="fanout.cancelled",
            actor="zf-cli",
            origin="kernel",
            payload={
                "fanout_id": p["fanout_id"],
                "reason": "workflow_run_terminal:run.goal.blocked",
            },
            correlation_id="trace-product",
        )
    )
    lost = runtime.event_writer.append(
        ZfEvent(
            type="fanout.child.dispatch_lost",
            actor="zf-cli",
            origin="kernel",
            payload={**p, "child_id": "synth", "semantic_attempt_consumed": False},
            correlation_id="trace-product",
        )
    )
    failure = runtime.event_writer.append(
        ZfEvent(
            type="fanout.synth.completed",
            actor="zf-cli",
            origin="kernel",
            payload={
                **p,
                "status": "failed",
                "recommendation": "reject",
                "failure_class": "worker_noop_or_terminal_missing",
                "reason": "provider_turn_closed_without_synth_result_recovery_exhausted",
                **(
                    {"report": {"findings": ["real critic rejected"]}}
                    if case == "semantic_reject"
                    else {}
                ),
            },
            causation_id=lost.id,
            correlation_id="trace-product",
        )
    )
    assert (
        _run_set(
            Namespace(
                objective="",
                status="active",
                reason="owner approved",
                operation=op_id,
                operation_max_usage_samples=0,
            )
        )
        == 0
    )

    def resume():
        cp = next(
            c
            for c in build_workflow_resume_projection(state, runtime.config)[
                "operation_checkpoints"
            ]
            if c["operation_id"] == op_id
        )
        assert (
            apply_workflow_resume(
                state, runtime.config, checkpoint_id=cp["checkpoint_id"]
            )["applied"]
            == 1
        )
        return cp

    resume()
    if case == "interrupted":
        interrupt_active_workflow_operations(
            state_dir=state,
            event_log=log,
            event_writer=runtime.event_writer,
            reason="graceful_stop",
        )
        assert (
            resume()["safe_resume_action"]
            == "redrive_interrupted_budget_synth_operation"
        )
    if case == "revoked":
        runtime.event_writer.append(
            ZfEvent(
                type="run.goal.blocked",
                actor="operator",
                payload={"run_id": "trace-product"},
                correlation_id="trace-product",
            )
        )
    sent = len(runtime.transport.sent)
    recover_unrecorded_reader_fanout_results(runtime)
    if case in {"revoked", "semantic_reject"}:
        assert len(runtime.transport.sent) == sent
        assert (
            runtime._fanout_manifest(p["fanout_id"])["aggregate"]["status"]
            == "cancelled"
        )
        return
    assert len(runtime.transport.sent) == sent + 1
    assert reduce_workflow_operations(log.read_all())[op_id]["status"] == "running"
    assert runtime._fanout_manifest(p["fanout_id"])["aggregate"]["status"] == "started"
    assert runtime._fanout_manifest(p["fanout_id"])["synth"]["status"] == "dispatched"
    # Historical no-result failure must not poison a new tick or replay after restart.
    assert not runtime._resume_unrecorded_reader_synth_results(log.read_all())
    recover_unrecorded_reader_fanout_results(runtime)
    assert len(runtime.transport.sent) == sent + 1
    assert not any(
        e.type == "fanout.aggregate.completed" and e.causation_id == failure.id
        for e in log.read_all()
    )

    completed = runtime.event_writer.append(
        ZfEvent(
            type="fanout.synth.completed",
            actor="task-map-synth",
            correlation_id="trace-product",
            payload={
                **{
                    k: p[k]
                    for k in ("fanout_id", "stage_id", "role_instance", "run_id")
                },
                "status": "completed",
                "recommendation": "approve",
                "summary": "new review completed",
                "report": {
                    "child_id": "synth",
                    "status": "passed",
                    "summary": "new review completed",
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
        )
    )
    runtime.run_once(events=[completed])
    assert any(e.type == "task_map.ready" for e in log.read_all())
    assert (
        runtime._fanout_manifest(p["fanout_id"])["aggregate"]["status"] == "completed"
    )


def test_token_amendment_applies_to_resumed_synth_operation(tmp_path, monkeypatch):
    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch, dimension="tokens")
    op_id = dispatch.payload["operation_id"]
    assert _run_set(Namespace(objective="", status="active", reason="continue isolated validation",
                              operation=op_id, operation_token_budget=3000)) == 0
    checkpoint = next(c for c in build_workflow_resume_projection(state, runtime.config)["operation_checkpoints"]
                      if c["operation_id"] == op_id)
    assert apply_workflow_resume(state, runtime.config, checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    operation = reduce_workflow_operations(log.read_all())[op_id]
    limits = _operation_limits(state, operation, events=log.read_all())
    assert limits["token_budget"] == 3000
    assert limits["cost_budget_usd"] == 8


def test_run_token_amendment_does_not_authorize_operation_redrive(tmp_path, monkeypatch):
    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch, dimension="tokens")
    assert _run_set(Namespace(objective="", status="active", reason="run-only budget", token_budget=3000)) == 0
    checkpoints = build_workflow_resume_projection(state, runtime.config)["operation_checkpoints"]
    assert not [c for c in checkpoints if c["operation_id"] == dispatch.payload["operation_id"]]


@pytest.mark.parametrize("limit", [-1, 0, 1000, 1500, 1501])
def test_named_token_amendment_checks_blocked_usage(tmp_path, monkeypatch, limit):
    _state, log, _runtime, dispatch = _blocked_synth(tmp_path, monkeypatch, dimension="tokens")
    op_id = dispatch.payload["operation_id"]
    result = _run_set(Namespace(objective="", status="active", reason="test bounded continuation",
                                operation=op_id, operation_token_budget=limit))
    assert (result == 0) is (limit == 0 or limit > 1500)
    accepted = [e for e in log.read_all() if e.type == "run.goal.updated"]
    assert bool(accepted) is (result == 0)


def test_named_token_amendment_does_not_change_another_operation(tmp_path, monkeypatch):
    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch, dimension="tokens")
    op_id = dispatch.payload["operation_id"]
    assert _run_set(Namespace(objective="", status="active", reason="isolated continuation",
                              operation=op_id, operation_token_budget=3000)) == 0
    checkpoint = next(c for c in build_workflow_resume_projection(state, runtime.config)["operation_checkpoints"]
                      if c["operation_id"] == op_id)
    assert apply_workflow_resume(state, runtime.config, checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    operation = reduce_workflow_operations(log.read_all())[op_id]
    other = {**operation, "operation_id": "another-operation"}
    assert _operation_limits(state, other, events=log.read_all())["token_budget"] == 1000
