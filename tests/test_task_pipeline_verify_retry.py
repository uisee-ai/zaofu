"""Verify execution repair must not discard a passing Impl or invent a verdict."""

import argparse
import json

import pytest

from tests.test_task_pipeline_reconciler import _policy
from zf.cli.recover import register
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.workflow_operation import reduce_workflow_operations


def seed(
    tmp_path,
    monkeypatch,
    *,
    verify_only=False,
    verdict="abstained",
    execution="failed",
    missing_source=False,
    missing_impl=False,
    mismatch="",
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: retry\n  state_dir: state\nroles: []\n'
    )
    state = tmp_path / "state"
    state.mkdir()
    task = Task(
        id="T1",
        status="in_progress",
        execution_binding=TaskExecutionBinding(workflow_run_id="run"),
        contract=TaskContract(
            behavior="original",
            evidence_contract={
                "task_map_generation": "map",
                **({"runtime_only": True} if verify_only else {}),
            },
        ),
    )
    TaskStore(state / "kanban.json").add(task)
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    identity = {
        "workflow_run_id": "run",
        "task_id": "T1",
        "task_map_generation": "map",
        "operation_generation": 2,
    }
    writer.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                **identity,
                "schema_version": "task-pipeline-generation.v1",
                "task_ids": ["T1"],
            },
        )
    )
    for stage in ["verify"] if verify_only or missing_impl else ["impl", "verify"]:
        p = {
            **identity,
            "operation_id": stage,
            "task_pipeline_stage": stage,
            "request_hash": "hash-" + stage,
            "active_attempt_id": stage + "-attempt",
        }
        writer.append(
            ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p)
        )
        source = writer.append(
            ZfEvent(
                type="task.pipeline.verify.failed"
                if stage == "verify"
                else "dev.build.done",
                origin="kernel",
                task_id="T1",
                payload=p,
            )
        )
        body = {
            "schema_version": "verification-result.v1",
            **p,
            **current_task_contract_identity(task),
            "attempt_id": p["active_attempt_id"],
            "target_commit": "a" * 40,
            "verdict": verdict if stage == "verify" else "passed",
            "execution_status": execution,
        }
        if stage == "verify" and mismatch:
            body[mismatch] = "wrong"
        descriptor = write_immutable_json_sidecar(
            state,
            body,
            root="test-results",
            kind="call_control_result",
            schema_version="verification-result.v1",
            created_by="test",
            source_event_id="missing"
            if missing_source and stage == "verify"
            else source.id,
        )
        writer.append(
            ZfEvent(
                type="workflow.call.result.admitted",
                task_id="T1",
                payload={
                    **p,
                    "semantic_verdict": body["verdict"],
                    "control_result_ref": descriptor,
                },
            )
        )
        writer.append(
            ZfEvent(type="workflow.operation.settled", task_id="T1", payload=p)
        )
    (state / "refs").mkdir()
    (state / "refs/task-index.json").write_text(
        json.dumps({"T1": {"source_commit": "a" * 40}})
    )
    return state, log, writer, task, descriptor


def invoke(state, apply=False, restart_from_impl=False):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(
        [
            "recover",
            "task-verify",
            "--state-dir",
            str(state),
            "--operation",
            "verify",
            "--reason",
            "input lifecycle repaired and regression checked",
            *(["--apply"] if apply else []),
            *(["--restart-from-impl"] if restart_from_impl else []),
        ]
    )
    return args.func(args)


def test_cli_can_refresh_impl_evidence_after_verify_execution_failure(tmp_path, monkeypatch, capsys):
    state, log, _, task, _ = seed(tmp_path, monkeypatch)
    before = log.path.read_bytes()
    assert invoke(state, restart_from_impl=True) == 0
    assert log.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["next_stage"] == "impl"
    assert invoke(state, True, restart_from_impl=True) == 0
    applied = log.path.read_bytes()
    assert invoke(state, True, restart_from_impl=True) == 0
    assert log.path.read_bytes() == applied
    ops = reduce_workflow_operations(log.read_all())
    assert ops["impl"]["semantic_verdict"] == "passed"
    assert ops["verify"]["semantic_verdict"] == "abstained"
    from types import SimpleNamespace
    from zf.runtime.task_pipeline_runtime import _verify_rework_feedback
    feedback = _verify_rework_feedback(
        SimpleNamespace(state_dir=state), operation_rows=ops.values(),
        task_id="T1", operation_generation=2,
    )
    assert feedback[0]["source"] == "operator_verify_impl_refresh"
    assert feedback[0]["reason"] == "input lifecycle repaired and regression checked"
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=[task], operations=ops.values(), attempts=[],
    )
    assert plan["dispatchable"]["verify"] == []
    assert plan["dispatchable"]["impl"][0]["operation_generation"] == "3"
    from zf.runtime.task_pipeline_verify_retry import verify_impl_generation
    assert verify_impl_generation(
        log.read_all(), task_id="T1", workflow_run_id="run",
        task_map_generation="map", operation_generation=3,
    ) == 3  # A new self-check is mandatory; do not relabel the old receipt.


@pytest.mark.parametrize("fault", ["verify_only", "missing_impl", "failed_impl", "retry_mode"])
def test_evidence_refresh_rejects_unsafe_restart_without_writes(tmp_path, monkeypatch, capsys, fault):
    state, log, writer, _, _ = seed(
        tmp_path, monkeypatch, verify_only=fault == "verify_only",
        missing_impl=fault == "missing_impl",
    )
    if fault == "failed_impl":
        writer.append(ZfEvent(type="workflow.operation.failed", task_id="T1",
                             payload={"operation_id": "impl", "workflow_run_id": "run"}))
    elif fault == "retry_mode":
        assert invoke(state, True) == 0
    before = log.path.read_bytes()
    assert invoke(state, True, restart_from_impl=True) == 1
    assert log.path.read_bytes() == before


@pytest.mark.parametrize("verify_only", [False, True])
def test_cli_retries_only_verify_and_preserves_failure_and_impl(
    tmp_path, monkeypatch, capsys, verify_only
):
    state, log, _, task, _ = seed(tmp_path, monkeypatch, verify_only=verify_only)
    original = log.path.read_bytes()
    assert invoke(state) == 0
    assert log.path.read_bytes() == original
    assert json.loads(capsys.readouterr().out)["target_commit"] == "a" * 40
    assert invoke(state, True) == 0
    applied = log.path.read_bytes()
    assert invoke(state, True) == 0 and log.path.read_bytes() == applied
    assert applied.startswith(original)
    ops = reduce_workflow_operations(log.read_all())
    assert (
        ops["verify"]["status"] == "superseded"
        and ops["verify"]["semantic_verdict"] == "abstained"
    )
    if not verify_only:
        assert (
            ops["impl"]["status"] == "settled"
            and ops["impl"]["semantic_verdict"] == "passed"
        )
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=[task], operations=ops.values(), attempts=[]
    )
    assert plan["dispatchable"]["impl"] == []
    assert plan["dispatchable"]["verify"][0]["operation_generation"] == "3"
    assert (
        len([e for e in log.read_all() if e.type == "workflow.operation.superseded"])
        == 1
    )


@pytest.mark.parametrize(
    "fault",
    [
        "product_failed",
        "passed",
        "completed_execution",
        "corrupt",
        "missing_source",
        "contract",
        "target",
        "new_operation",
        "new_verify",
        "new_attempt",
        "paused",
        "run_done",
        "old_map",
        "task_done",
        "operation_id",
        "request_hash",
        "attempt_id",
        "operation_generation",
    ],
)
@pytest.mark.parametrize("restart_from_impl", [False, True])
def test_rejects_unsafe_or_semantic_retries_without_writes(
    tmp_path, monkeypatch, capsys, fault, restart_from_impl
):
    state, log, writer, task, descriptor = seed(
        tmp_path,
        monkeypatch,
        verdict={"product_failed": "failed", "passed": "passed"}.get(
            fault, "abstained"
        ),
        execution="completed" if fault == "completed_execution" else "failed",
        missing_source=fault == "missing_source",
        mismatch=fault
        if fault
        in {"operation_id", "request_hash", "attempt_id", "operation_generation"}
        else "",
    )
    if fault == "corrupt":
        (state / descriptor["ref"]).write_text("{}")
    elif fault == "contract":
        task.contract.behavior = "changed"
        TaskStore(state / "kanban.json").update(task.id, contract=task.contract)
    elif fault == "target":
        (state / "refs/task-index.json").write_text(
            json.dumps({"T1": {"source_commit": "b" * 40}})
        )
    elif fault == "task_done":
        TaskStore(state / "kanban.json").update(task.id, status="done")
    elif fault in {"new_operation", "new_verify"}:
        p = {
            "workflow_run_id": "run",
            "task_id": "T1",
            "task_map_generation": "map",
            "operation_generation": 3,
            "operation_id": "new",
            "task_pipeline_stage": "verify",
        }
        writer.append(
            ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p)
        )
        if fault == "new_verify":
            writer.append(
                ZfEvent(type="workflow.operation.settled", task_id="T1", payload=p)
            )
    elif fault == "new_attempt":
        from zf.core.state.task_attempts import TaskAttemptStore

        TaskAttemptStore(state / "task_attempts.json").ensure_for_dispatch(
            run_id="run",
            task_id="T1",
            dispatch_id="new",
            operation_id="new",
            role="verify",
            instance_id="verify-1",
            briefing_ref="",
            created_at="2026-11-07T00:00:00+00:00",
            lease_expires_at="",
            max_attempts=3,
        )
    elif fault == "paused":
        writer.append(ZfEvent(type="run.paused", payload={"workflow_run_id": "run"}))
    elif fault == "run_done":
        writer.append(ZfEvent(type="run.completed", payload={"workflow_run_id": "run"}))
    elif fault == "old_map":
        writer.append(
            ZfEvent(
                type="task.pipeline.generation.admitted",
                payload={
                    "schema_version": "task-pipeline-generation.v1",
                    "workflow_run_id": "run",
                    "task_map_generation": "new",
                    "task_ids": ["T1"],
                },
            )
        )
    before = log.path.read_bytes()
    assert invoke(state, True, restart_from_impl=restart_from_impl) == 1
    assert log.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


@pytest.mark.parametrize(
    "fault",
    ["", "repeated_retry", "refresh_impl", "unauthorized", "new_impl", "contract", "target", "gap"],
)
def test_retry_target_admission_reuses_only_authorized_exact_impl(
    tmp_path, monkeypatch, capsys, fault
):
    from types import SimpleNamespace
    from zf.runtime.impl_self_check import (
        write_impl_self_check,
        self_check_payload_fields,
    )
    from zf.runtime.task_pipeline_targets import prepare_verify_target
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting
    from zf.runtime.workflow_operation import WorkflowOperationService

    state, log, writer, task, original_result = seed(tmp_path, monkeypatch)
    contract = {
        **current_task_contract_identity(task),
        "task_ref": "refs/zf/tasks/T1",
        "acceptance_criteria": [
            {"acceptance_id": "AC-1", "mandatory": True, "verification_command_ids": []}
        ],
        "verification_commands": [],
    }
    descriptor = {"ref": "artifacts/contract.json", "sha256": "d" * 64}
    body = {
        "schema_version": "impl-self-check.v1",
        **current_task_contract_identity(task),
        "attempt_id": "impl-attempt",
        "source_commit": "a" * 40,
        "target_commit": "a" * 40,
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
        "command_receipts": [],
        "acceptance_results": [
            {
                "acceptance_id": "AC-1",
                "status": "passed",
                "command_receipt_ids": [],
                "evidence_refs": ["artifacts/proof.json"],
                "residual_risks": [],
            }
        ],
        "evidence_refs": ["artifacts/proof.json"],
        "residual_risks": [],
    }
    checked = write_impl_self_check(
        state, body, source_event_id="build", created_by="dev"
    )
    writer.append(
        ZfEvent(
            type="impl.self_check.completed",
            task_id="T1",
            payload={
                **self_check_payload_fields(checked),
                "workflow_run_id": "run",
                "task_map_generation": "map",
                "operation_generation": 2,
                "target_commit": "a" * 40,
            },
        )
    )
    if fault == "unauthorized":
        WorkflowOperationService(
            state_dir=state, event_log=log, event_writer=writer
        ).supersede(
            operation_id="verify",
            request_hash="hash-verify",
            workflow_run_id="run",
            task_id="T1",
            reason="unrelated replacement",
        )
    else:
        assert invoke(state, True, restart_from_impl=fault == "refresh_impl") == 0
    if fault == "repeated_retry":
        from zf.runtime.task_pipeline_verify_retry import request_verify_retry

        p = {
            "operation_id": "verify-3",
            "task_id": "T1",
            "workflow_run_id": "run",
            "task_map_generation": "map",
            "task_pipeline_stage": "verify",
            "operation_generation": 3,
            "active_attempt_id": "verify-attempt-3",
            "request_hash": "hash-verify-3",
        }
        writer.append(
            ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p)
        )
        failed = writer.append(
            ZfEvent(type="task.pipeline.verify.failed", task_id="T1", payload=p)
        )
        result = {
            **json.loads((state / original_result["ref"]).read_text()),
            **p,
            "attempt_id": "verify-attempt-3",
        }
        result_ref = write_immutable_json_sidecar(
            state,
            result,
            root="test-results",
            kind="call_control_result",
            schema_version="verification-result.v1",
            created_by="test",
            source_event_id=failed.id,
        )
        writer.append(
            ZfEvent(
                type="workflow.call.result.admitted",
                task_id="T1",
                payload={
                    **p,
                    "semantic_verdict": "abstained",
                    "control_result_ref": result_ref,
                },
            )
        )
        writer.append(
            ZfEvent(type="workflow.operation.settled", task_id="T1", payload=p)
        )
        assert (
            request_verify_retry(
                state_dir=state,
                event_log=log,
                event_writer=writer,
                operation_id="verify-3",
                reason="second execution repair",
                apply=True,
            )["status"]
            == "requested"
        )
        plan = TaskPipelineReconciler().reconcile(
            policy=_policy(),
            tasks=[task],
            operations=reduce_workflow_operations(log.read_all()).values(),
            attempts=[],
        )
        assert plan["dispatchable"]["impl"] == []
        assert plan["dispatchable"]["verify"][0]["operation_generation"] == "4"
    if fault == "new_impl":
        writer.append(
            ZfEvent(
                type="workflow.operation.requested",
                task_id="T1",
                payload={
                    "operation_id": "impl-new",
                    "task_id": "T1",
                    "workflow_run_id": "run",
                    "task_map_generation": "map",
                    "task_pipeline_stage": "impl",
                    "operation_generation": 3,
                },
            )
        )
    if fault == "contract":
        contract["contract_revision"] = "changed"
    target = "b" * 40 if fault == "target" else "a" * 40
    entry = {
        "source_commit": target,
        "workdir": str(tmp_path),
        "task_ref": contract["task_ref"],
    }
    runtime = SimpleNamespace(
        state_dir=state,
        project_root=tmp_path,
        event_log=log,
        event_writer=writer,
        _task_ref_entry=lambda _: entry,
    )
    monkeypatch.setattr("zf.runtime.task_pipeline_runtime._git", lambda *a: target)
    args = dict(
        task_id="T1",
        workflow_run_id="run",
        task_map_generation="map",
        operation_generation=4 if fault in {"gap", "repeated_retry"} else 3,
        workspace=SimpleNamespace(project_path=tmp_path),
        contract_snapshot=contract,
        contract_descriptor=descriptor,
    )
    if fault == "refresh_impl":
        # A route normalization changed the immutable snapshot, not the code.
        descriptor = {"ref": "artifacts/new-contract.json", "sha256": "e" * 64}
        args["contract_descriptor"] = descriptor
        with pytest.raises((TaskPipelineWaiting, ValueError)):
            prepare_verify_target(runtime, **args)
        refreshed = {**body, "attempt_id": "impl-refresh-attempt",
                     "contract_snapshot_ref": descriptor["ref"],
                     "contract_snapshot_digest": descriptor["sha256"]}
        checked = write_impl_self_check(state, refreshed, source_event_id="new-build", created_by="dev")
        writer.append(ZfEvent(type="impl.self_check.completed", task_id="T1", payload={
            **self_check_payload_fields(checked), "workflow_run_id": "run",
            "task_map_generation": "map", "operation_generation": 3,
            "target_commit": "a" * 40,
        }))
    if fault not in {"", "repeated_retry", "refresh_impl"}:
        with pytest.raises((TaskPipelineWaiting, ValueError)):
            prepare_verify_target(runtime, **args)
    else:
        result = prepare_verify_target(runtime, **args)
        assert result["target_commit"] == "a" * 40
        assert result["impl_self_check_ref"] == checked["ref"]
        assert result["impl_self_check_digest"] == checked["sha256"]
