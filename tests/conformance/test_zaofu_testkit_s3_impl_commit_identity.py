"""Contract-bound implementation results must carry an immutable Git OID."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401
)
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("invalid_target", ["short", "symbolic", "receipt"])
def test_contract_bound_impl_rejects_abbreviated_git_oid(
    blocking_runtime, monkeypatch: pytest.MonkeyPatch,
    reopen: bool, invalid_target: str,
) -> None:
    """Reject before settlement; repair the same operation and reach a candidate."""

    runtime, transport, _admission = blocking_runtime
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification,
        "rolling_smoke": True, "tier": "runtime", "deterministic": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    token = provision_role_submit_credential(runtime.state_dir, "impl-1")
    verify_token = provision_role_submit_credential(runtime.state_dir, "verify-1")
    assert [decision.action for decision in runtime._dispatch_ready()] == [
        "task_pipeline_dispatch"
    ]
    context = transport.contexts[-1]
    original_submit = SemanticResultSubmitService.submit

    def reject_then_repair(self, *, semantic_result=None, **kwargs):
        body = deepcopy(dict(semantic_result or {}))
        full_commit = str(body["target_commit"])
        wrong = "HEAD" if invalid_target == "symbolic" else full_commit[:7]
        if invalid_target != "receipt":
            body["target_commit"] = wrong
            body["self_check"].update(source_commit=wrong, target_commit=wrong)
        for receipt in body["self_check"]["command_receipts"]:
            receipt["target_commit"] = wrong
        assert body["self_check"]["command_receipts"]
        attempts = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
        before_attempt = attempts.get(context.attempt_id)
        before_operation = reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]
        try:
            original_submit(self, semantic_result=body, **kwargs)
        except ResultSubmitError as failure:
            assert failure.code == "result_not_admitted"
            expected = "impl_self_check_invalid" if invalid_target == "receipt" else "implementation_target_commit_invalid"
            assert [issue["code"] for issue in failure.details["issues"]] == [expected]
        else:
            # Preserve the downstream causal signature in the original red report.
            runtime._react_to_events()
            runtime._dispatch_ready()
            events = runtime.event_log.read_all()
            diagnostic = {
                "reported_commit": wrong, "full_commit": full_commit,
                "task_ref": runtime._task_ref_entry(task.id),
                "events": [{"type": e.type, "payload": e.payload} for e in events
                           if e.type in {"dev.build.done", "task.ref.updated", "task.pipeline.stage.waiting"}],
            }
            pytest.fail(f"malformed implementation settled before TaskRef admission: {diagnostic}")
        events = runtime.event_log.read_all()
        assert not any(e.type in {"dev.build.done", "workflow.operation.settled", "task.ref.updated"} for e in events)
        assert reduce_workflow_operations(events)[context.operation_id] == before_operation
        assert attempts.get(context.attempt_id) == before_attempt
        assert before_attempt["status"] == "sent"
        assert runtime.task_store.get(task.id).active_dispatch_id == context.dispatch_id
        assert not runtime._task_ref_entry(task.id)
        # No credential reissue, replacement operation or synthetic terminal event.
        if reopen:
            self = SemanticResultSubmitService(
                state_dir=runtime.state_dir, event_log=runtime.event_log,
                event_writer=runtime.event_writer,
            )
        return original_submit(self, semantic_result=semantic_result, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SemanticResultSubmitService, "submit", reject_then_repair)
        event, body = _submit_generation_stage(
            runtime, context, token, writes={"README.md": "K551 exact identity\n"},
        )
    assert event.payload["source_commit"] == body["target_commit"]
    runtime._react_to_events()
    assert runtime._task_ref_entry(task.id)["source_commit"] == body["target_commit"]
    if reopen:
        runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    verify = transport.contexts[-1]
    assert verify.task_pipeline_stage == "verify"
    _submit_generation_stage(runtime, verify, verify_token)
    runtime._react_to_events()
    assert "task_pipeline_candidate_frozen" in [d.action for d in runtime._dispatch_ready()]
    events = runtime.event_log.read_all()
    [candidate] = [e for e in events if e.type == "candidate.ready"]
    assert _git(runtime.project_root, "show", f"{candidate.payload['commit']}:README.md") == "K551 exact identity"
    assert runtime.task_store.get(task.id).status == "done"
    for _ in range(2):
        runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 2
    events = runtime.event_log.read_all()
    assert sum(e.type == "dev.build.done" for e in events) == 1
    assert sum(e.type == "candidate.ready" for e in events) == 1
