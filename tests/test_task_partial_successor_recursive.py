"""Repeated partial deliveries must preserve all proven unpublished patches."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_task_partial_successor import scenario, git
from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_partial_successor import partial_successor_proof
from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base
from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError
from zf.runtime.worker_execution_envelope import build_worker_execution_envelope
from zf.runtime.workflow_operation import WorkflowOperationService


def repeated_scenario(tmp_path, fault=""):
    runtime, middle, context, writer, config, candidate, partial = scenario(tmp_path)
    base = task_pipeline_workspace_base(runtime, task=middle, generation_context=context)
    if fault in {"forged_tree", "wrong_partial_parent", "extra_parent"}:
        tree = git(tmp_path, "rev-parse", (candidate if fault == "forged_tree" else base) + "^{tree}")
        parents = [candidate, partial]
        if fault == "wrong_partial_parent":
            parents[1] = git(tmp_path, "rev-parse", partial + "^")
        if fault == "extra_parent":
            parents.append(git(tmp_path, "rev-parse", partial + "^"))
        args = ["commit-tree", tree, "-m", "untrusted composite"]
        for parent in parents:
            args += ["-p", parent]
        base = git(tmp_path, *args)
    git(tmp_path, "checkout", "-qb", "worker/middle", base)
    (tmp_path / "gap.txt").write_text("second partial\n")
    git(tmp_path, "add", "--", "gap.txt")
    git(tmp_path, "commit", "-qm", "second partial")
    head = git(tmp_path, "rev-parse", "HEAD")
    identity = current_task_contract_identity(middle)
    p = {**identity, "workflow_run_id": "run", "task_id": middle.id, "operation_id": "impl-middle",
         "task_pipeline_stage": "impl", "operation_generation": 1, "workspace_generation": 1,
         "placement_epoch": 1, "task_stage_session_binding": "middle-binding", "role_instance": "dev",
         "attempt_id": "middle-attempt", "dispatch_id": "middle-dispatch", "lease_id": "middle-lease",
         "workdir": str(tmp_path), "source_branch": "worker/middle", "base_commit": base,
         "target_ref": base, "canonical_success_event": "dev.build.done",
         "canonical_failure_event": "dev.blocked", "output_profile_id": "implementation",
         "output_profile_revision": "1"}
    # A real current identity is required by the continuation admission reader.
    assert identity.get("task_map_generation")
    service = WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=writer)
    ensured = service.ensure_operation(workflow_run_id="run", operation_id=p["operation_id"],
        operation_type="task-stage", request=p, task_id=middle.id, role_instance="dev",
        active_attempt_id=p["attempt_id"], lease_id=p["lease_id"])
    p["request_hash"] = ensured.request_hash
    writer.append(ZfEvent(type="workflow.operation.started", origin="kernel", task_id=middle.id,
        payload={**p, "active_attempt_id": p["attempt_id"]}))
    p["worker_execution_envelope"] = build_worker_execution_envelope(payload=p,
        role=SimpleNamespace(instance_id="dev", name="dev", backend="mock", skills=[]),
        execution_profile=SimpleNamespace(profile_id="bounded-direct-v1", profile_digest="profile"))
    writer.append(ZfEvent(type="task.pipeline.stage.dispatched", origin="kernel", task_id=middle.id, payload=p))
    blocked = writer.emit("dev.blocked", task_id=middle.id, actor="dev", payload={**p, "source_commit": head})
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        runtime.state_dir, profile_id="implementation", revision="1", event_type="dev.blocked",
        semantic_result={"target_commit": head, "verdict": "blocked"},
        identity={k:v for k,v in p.items() if k not in {"worker_execution_envelope", "workdir"}},
        source_event_id=blocked.id, actor="dev", task_id=middle.id, correlation_id="run")
    assert not adapted.issues
    writer.append(ZfEvent(type="workflow.call.result.admitted", origin="kernel", task_id=middle.id,
        payload={"workflow_run_id": "run", "operation_id": p["operation_id"], "request_hash": ensured.request_hash,
            "control_result_ref": adapted.descriptor, "semantic_verdict": "blocked",
            "admission_status": "admitted", "source_event_id": blocked.id}))
    service.settle(workflow_run_id="run", operation_id=p["operation_id"], request_hash=ensured.request_hash,
        admitted_call_result_ref=adapted.descriptor, task_id=middle.id)
    git(tmp_path, "checkout", "candidate/FEATURE")
    (tmp_path / "later.txt").write_text("second producer\n")
    git(tmp_path, "add", "--", "later.txt")
    git(tmp_path, "commit", "-qm", "second producer")
    candidate = git(tmp_path, "rev-parse", "HEAD")
    writer.append(ZfEvent(type="candidate.updated", origin="kernel", payload={
        "incremental": True, "workflow_run_id": "run", "candidate_head": candidate}))
    last = deepcopy(middle)
    last.id, last.key = "LAST", "FEATURE:LAST"
    last.contract.source_ref = "git:" + head
    last.contract.evidence_contract["supersedes_task_ids"] = [middle.id]
    TaskStore(runtime.state_dir / "kanban.json").add(last)
    writer.append(ZfEvent(type="task.created", task_id=last.id, payload={"feature_id": "FEATURE"}))
    return runtime, last, {**context, "dispatch_base_commit": head}, writer, config, candidate, partial, head


def test_second_partial_round_composes_all_work_without_publication(tmp_path):
    runtime, task, context, _, _, candidate, first, second = repeated_scenario(tmp_path)
    before = git(tmp_path, "show-ref")
    combined = task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    for path, value in {"product.txt":"partial implementation", "gap.txt":"second partial",
                        "concurrent.txt":"other task", "later.txt":"second producer"}.items():
        assert git(tmp_path, "show", combined + ":" + path) == value
    proof = partial_successor_proof(runtime, task_id=task.id, partial_head=second,
                                   candidate_head=candidate, workflow_run_id="run")
    assert proof["partial_commits"] == [first, second]
    assert proof["partial_paths"] == ["gap.txt", "product.txt"]
    assert git(tmp_path, "show-ref") == before
    assert task_pipeline_workspace_base(runtime, task=task, generation_context=context) == combined


@pytest.mark.parametrize("fault", ["forged_tree", "wrong_partial_parent", "extra_parent",
    "inherited_scope", "ancestor_branch_moved", "ancestor_run", "ancestor_missing_admission"])
def test_recursive_partial_proof_rejects_unproven_inherited_work(tmp_path, fault):
    runtime, task, context, _, _, candidate, _, second = repeated_scenario(tmp_path, fault)
    store = TaskStore(runtime.state_dir / "kanban.json")
    if fault == "inherited_scope":
        task.contract.scope = ["gap.txt"]
        store.update(task.id, contract=task.contract)
    if fault == "ancestor_run":
        middle = store.get("NEXT")
        middle.contract.evidence_contract["workflow_run_id"] = "foreign"
        store.update(middle.id, contract=middle.contract)
    if fault == "ancestor_branch_moved":
        git(tmp_path, "branch", "-f", "worker/partial", candidate)
    if fault == "ancestor_missing_admission":
        events = [e for e in runtime.event_log.read_all()
                  if not (e.type == "workflow.call.result.admitted" and e.task_id == "PART")]
        runtime.event_log = SimpleNamespace(read_all=lambda: events)
    before = git(tmp_path, "show-ref")
    assert not partial_successor_proof(runtime, task_id=task.id, partial_head=second,
                                      candidate_head=candidate, workflow_run_id="run")
    with pytest.raises(TaskPipelineRuntimeError, match="base diverged"):
        task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    assert git(tmp_path, "show-ref") == before


def test_recursive_partial_is_wired_into_real_dispatch(tmp_path, monkeypatch):
    from pathlib import Path
    from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage

    runtime, task, context, _, config, candidate, _, partial = repeated_scenario(tmp_path)
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = 'worktree'
    config.runtime.workdirs.provision_paths = []
    runtime.config = config
    role = RoleConfig(name='dev', backend='mock', lifecycle=RoleLifecycleConfig(mode='on_demand'))
    runtime._find_role_by_instance = lambda _: role
    context['task_map_generation'] = 'last-map'
    captured = []

    class SnapshotBoundary(Exception):
        pass

    def capture(*args, **kwargs):
        captured.append(kwargs['workspace'])
        raise SnapshotBoundary

    monkeypatch.setattr('zf.runtime.task_pipeline_targets.prepare_contract_snapshot', capture)
    with pytest.raises(SnapshotBoundary):
        dispatch_task_pipeline_stage(runtime, policy={}, task=task,
            assignment={'stage': 'impl', 'role_instance': role.instance_id},
            generation_context=context, operation_rows=[], attempt_rows=[])
    workspace = captured[0]
    project = Path(workspace.project_path)
    assert git(project, 'show', 'HEAD:product.txt') == 'partial implementation'
    assert git(project, 'show', 'HEAD:gap.txt') == 'second partial'
    assert git(project, 'show', 'HEAD:later.txt') == 'second producer'
    assert git(project, 'rev-parse', 'HEAD') == workspace.base_commit
    assert git(project, 'merge-base', '--is-ancestor', partial, workspace.base_commit) == ''
    assert git(tmp_path, 'rev-parse', 'candidate/FEATURE') == candidate


@pytest.mark.parametrize('conflict', [False, True])
def test_recursive_partial_candidate_integration_replays_both_rounds(tmp_path, conflict):
    from zf.runtime.candidates import CandidateRebuilder
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.task_refs import TaskRefManager

    runtime, task, context, writer, config, candidate, first, second = repeated_scenario(tmp_path)
    workspace_base = task_pipeline_workspace_base(runtime, task=task, generation_context=context)
    if conflict:
        (tmp_path / 'product.txt').write_text('conflicting candidate\n')
        git(tmp_path, 'add', '--', 'product.txt')
        git(tmp_path, 'commit', '-qm', 'concurrent conflict')
        candidate = git(tmp_path, 'rev-parse', 'HEAD')
    git(tmp_path, 'checkout', '-qb', 'worker/last', workspace_base)
    (tmp_path / 'gap.txt').write_text('completed gap\n')
    git(tmp_path, 'add', '--', 'gap.txt')
    git(tmp_path, 'commit', '-qm', 'complete last gap')
    head = git(tmp_path, 'rev-parse', 'HEAD')
    updated = TaskRefManager(state_dir=runtime.state_dir, project_root=tmp_path, config=config).process_dev_build_done(
        ZfEvent(type='dev.build.done', actor='dev', task_id=task.id, payload={
            'source_commit': head, 'source_branch': 'worker/last', 'base_git_head': workspace_base,
            'feature_id': 'FEATURE', 'files_touched': ['gap.txt']}))
    assert updated.status == 'updated'
    rebuilder = CandidateRebuilder(state_dir=runtime.state_dir, project_root=tmp_path,
                                  config=config, event_log=runtime.event_log)
    result = rebuilder.integrate_task_pipeline_task(task_id=task.id, workflow_run_id='run',
        task_map_generation='last-map', operation_generation=1, pipeline_key='last-pipeline',
        dispatch_base_commit=second, contract_revision='next-contract', event_writer=writer,
        causation_id='verify-admitted')
    if conflict:
        assert result.status != 'integrated'
        assert git(tmp_path, 'rev-parse', 'candidate/FEATURE') == candidate
        return
    assert result.status == 'integrated', result
    receipt = hydrate_sidecar_ref(runtime.state_dir, result.payload['receipt_ref']).payload
    assert receipt['adopted_base_commits'] == [first, second]
    assert receipt['integration_base_head'] == candidate
    new = receipt['new_candidate_head']
    for path, value in {'product.txt':'partial implementation', 'gap.txt':'completed gap',
                        'concurrent.txt':'other task', 'later.txt':'second producer'}.items():
        assert git(tmp_path, 'show', new + ':' + path) == value
