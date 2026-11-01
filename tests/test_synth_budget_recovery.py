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


def _blocked_synth(tmp_path, monkeypatch, *, refusal=False):
    state, log, runtime, fanout = _task_map_synth_orchestrator(
        tmp_path, complete_child=False
    )
    runtime.config.workflow.execution_profiles["direct-v1"] = ExecutionProfileConfig(
        limits=ExecutionProfileLimitsConfig(max_usage_samples=60, cost_budget_usd=8)
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
                "exceeded_dimensions": ["usage_samples"],
                "measurement": {"usage_samples": 60},
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
        else "workflow_budget_exceeded:usage_samples",
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
