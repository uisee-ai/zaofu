"""A malformed Impl object must be repairable in its original Task attempt."""
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_result_submit import _runtime
from tests.test_task_host_actions import case
from tests.test_task_publication import prepare
from tests.test_typed_result_admission import _contract, _implementation_result
from zf.core.events import ZfEvent
from zf.runtime.implementation_commit import published_retry_commit, validate_impl_commit
from zf.runtime.result_submit import (
    ResultSubmitError, SemanticResultSubmitService,
    bind_operation_submit_capability, provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot
from zf.runtime.task_host_manifest import git
from zf.runtime.task_publication import reconcile_task_publication
from zf.runtime.workflow_operation import WorkflowOperationService


def _running_impl(tmp_path, object_format):
    runtime = _runtime(tmp_path)
    root = runtime.state_dir / "custom-workdirs/task/project"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "worker-task", "--object-format=" + object_format],
                   cwd=root, check=True)
    for message in ("base", "implementation"):
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", message], cwd=root, check=True)
    contract = _contract()
    contract["base_commit"] = git(root, "rev-parse", "HEAD^")
    desc = write_task_contract_snapshot(runtime.state_dir, contract)
    semantic = _implementation_result(contract, desc)
    identity = {**{k: semantic[k] for k in ("workflow_run_id", "task_id", "contract_revision",
                "task_map_generation", "contract_snapshot_ref", "contract_snapshot_digest")},
                "source_branch": "worker-task", "stage_id": "impl", "run_id": "attempt-1",
                "role_instance": "dev-1", "task_ref": contract["task_ref"],
                "task_pipeline_stage": "impl", "operation_generation": 3,
                "base_commit": contract["base_commit"], "workdir": str(root)}
    token = provision_role_submit_credential(runtime.state_dir, "dev-1").read_text().strip()
    ops = WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                   event_writer=runtime.event_writer)
    args = dict(workflow_run_id="run-1", operation_id="op-impl", task_id="TASK-1",
                role_instance="dev-1", active_attempt_id="attempt-1", lease_id="lease-1")
    requested = ops.ensure_operation(**args, operation_type="task-stage", request={
        **identity, "output_profile_id": "implementation", "output_profile_revision": "1",
        "semantic_result_submit_mode": "blocking", "result_identity": identity,
        "result_scratch_ref": "tmp/result-submit/attempt-1/result.json",
        "canonical_success_event": "dev.build.done", "canonical_failure_event": "dev.blocked"})
    ops.mark_started(**args, dispatch_id="dispatch-1", request_hash=requested.request_hash)
    bind_operation_submit_capability(runtime.state_dir, operation_id="op-impl", role_instance="dev-1",
                                     attempt_id="attempt-1", lease_id="lease-1")
    runtime.event_writer.append(ZfEvent(type="task.pipeline.stage.dispatched", origin="kernel",
        task_id="TASK-1", payload={**identity, **args, "dispatch_id": "dispatch-1"}))
    service = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                          event_writer=runtime.event_writer)
    return runtime, root, service, dict(operation_id="op-impl", role_instance="dev-1", credential=token), semantic


def _commit_result(semantic, value):
    semantic["target_commit"] = value
    check = semantic["self_check"]
    check["source_commit"] = check["target_commit"] = value
    check["command_receipts"][0]["target_commit"] = value


@pytest.mark.parametrize("object_format,prefix_length", [("sha1", 8), ("sha256", 41), ("sha256", 64)])
def test_wrong_oid_repair_in_original_attempt(tmp_path, object_format, prefix_length):
    runtime, root, service, args, semantic = _running_impl(tmp_path, object_format)
    head = git(root, "rev-parse", "HEAD")
    for invalid in (head + "8", git(root, "rev-parse", "HEAD^"), "HEAD"):
        _commit_result(semantic, invalid)
        before = [e.id for e in runtime.event_log.read_all()]
        check = service.validate_plan_candidate(**args, semantic_result=semantic)
        assert check["status"] == "failed", check
        assert any(row["code"] == "implementation_commit_invalid" and head in row["message"]
                   for row in check["errors"]), check
        assert [e.id for e in runtime.event_log.read_all()] == before
    _commit_result(semantic, head + "8")
    with pytest.raises(ResultSubmitError) as exc:
        service.submit(**args, semantic_result=semantic)
    assert head in str(exc.value.details), exc.value.details
    assert not any(e.type in {"workflow.call.result.admitted", "task.ref.rejected"}
                   for e in runtime.event_log.read_all())
    _commit_result(semantic, head[:prefix_length])
    check = service.validate_plan_candidate(**args, semantic_result=semantic)
    assert check["status"] == "passed", check
    success = service.submit(**args, semantic_result=semantic)
    assert success.canonical_event_type == "dev.build.done"
    admitted = next(e for e in runtime.event_log.read_all() if e.id == success.admitted_event_id)
    result = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["control_result_ref"]).payload
    assert result["target_commit"] == head[:prefix_length] and result["attempt_id"] == "attempt-1"


@pytest.fixture
def published_case(case):
    service, _, identity = case
    service.writer.default_origin = "kernel"
    identity["request_hash"] = "request-hash"
    service, root = prepare(case)
    assert reconcile_task_publication(service) == 1
    assert reconcile_task_publication(service) == 1
    events = service.writer.event_log.read_all()
    retry = next(e for e in events if e.type == "task.rework.requested")
    effect = next(e for e in events if e.type == "runtime.action.attempt.completed")
    assert retry.payload["publication_event_id"] == effect.id
    receipt = hydrate_sidecar_ref(service.state_dir, effect.payload["receipt_ref"]).payload
    placement = {"workflow_run_id": retry.payload["workflow_run_id"], "task_id": retry.payload["task_id"],
                 "task_map_generation": retry.payload["task_map_generation"], "operation_generation": 2,
                 "workdir": str(root), "source_branch": receipt["manifest"]["branch"],
                 "contract_snapshot_digest": receipt["manifest"]["contract_digest"],
                 "rework_feedback": [{"category": "task_pipeline_operator_retry", "blocking_event_id": retry.id}]}
    expected = git(root, "rev-parse", "HEAD")
    return service, root, events, retry, effect, placement, expected


def test_retry_uses_authenticated_preceding_publication(published_case):
    service, _, events, retry, effect, placement, expected = published_case
    assert published_retry_commit(service.state_dir, placement, events) == expected
    historic_retry = replace(retry, payload={k: v for k, v in retry.payload.items() if k != "publication_event_id"})
    historic = [historic_retry if e == retry else e for e in events]
    assert published_retry_commit(service.state_dir, placement, historic) == expected
    assert published_retry_commit(service.state_dir, {**placement, "operation_generation": 3}, historic) == ""
    with pytest.raises(ValueError):
        published_retry_commit(service.state_dir, {**placement, "workdir": "/foreign"}, events)
    with pytest.raises(ValueError):
        published_retry_commit(service.state_dir, placement,
                               [replace(e, origin="agent") if e == effect else e for e in events])
    (service.state_dir / effect.payload["receipt_ref"]["ref"]).write_text("{}")
    with pytest.raises(ValueError):
        published_retry_commit(service.state_dir, placement, events)


def test_published_impl_briefing_and_completion_bind_the_same_commit(published_case):
    from zf.core.config.schema import RoleConfig
    from zf.core.task.store import TaskStore
    from zf.runtime.task_pipeline_briefing import write_task_pipeline_briefing

    service, root, _, _, _, placement, published = published_case
    runtime = SimpleNamespace(state_dir=service.state_dir, config=service.config, event_log=service.writer.event_log)
    task = TaskStore(service.state_dir / "kanban.json").get(placement["task_id"])
    base = git(root, "rev-parse", "HEAD^")
    operation = {**placement, "task_pipeline_stage": "impl", "operation_id": "op-2", "attempt_id": "a2",
                 "active_attempt_id": "a2", "lease_id": "l2", "dispatch_id": "d2", "placement_epoch": 2,
                 "semantic_result_submit_mode": "blocking"}
    operation["rework_feedback"] = [*placement["rework_feedback"], {"finding_id": "independent-sort-failure"}]

    def render(payload):
        return write_task_pipeline_briefing(
            runtime, role=RoleConfig(name="dev", instance_id="dev", backend="mock"),
            task=task, stage="impl", workspace=SimpleNamespace(project_path=root, base_commit=base),
            contract_snapshot={}, payload=payload, prepared=SimpleNamespace(operation_id="op-2"),
        ).read_text()

    ordinary = render({**operation, "operation_generation": 3})
    assert f"- target_commit: `{base}`" in ordinary and "reproduce these failures first" in ordinary
    assert "Reassess published commit" not in ordinary
    brief = render(operation)
    assert f"- target_commit: `{published}`" in brief and f"Reassess published commit {published}" in brief
    assert "reproduce these failures first" not in brief
    assert "each separate product finding" in brief and "independent-sort-failure" in brief
    assert "required self-checks" in brief and "for the Owner separately" in brief
    assert "Commit task changes with explicit pathspecs" not in brief
    conflict = render({**operation, "target_commit": base})
    assert f"- target_commit: `{base}`" in conflict and f"Reassess published commit {published}" in conflict
    assert "report the observed hashes" in conflict
    service.writer.append(ZfEvent(type="task.pipeline.stage.dispatched", origin="kernel", payload=operation))
    events = service.writer.event_log.read_all()
    validate_impl_commit(service.state_dir, operation, {"target_commit": published}, events)
    with pytest.raises(ValueError, match="target_commit must resolve to"):
        validate_impl_commit(service.state_dir, operation, {"target_commit": base}, events)
    git(root, "commit", "--allow-empty", "-m", "test: simulate a different workspace target")
    with pytest.raises(ValueError, match="differs from its host publication"):
        validate_impl_commit(service.state_dir, operation, {"target_commit": git(root, "rev-parse", "HEAD")}, events)
