"""A read-only re-dispatch must not invalidate retained integration receipts."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.runtime.task_refs import TaskRefManager
from tests.test_task_refs import _init_repo, _git, _config


@pytest.fixture
def refs(tmp_path):
    old = _init_repo(tmp_path)
    state = tmp_path / ".zf"
    state.mkdir()
    config = _config(state)
    manager = TaskRefManager(state_dir=state, project_root=tmp_path, config=config)
    task = Task(id="AUDIT", contract=TaskContract(evidence_contract={"execution_mode": "runtime_only"}))
    manager.admit_read_only_target(task_id=task.id, source_commit=old, trigger_event_id="old-generation", trace_id="run")
    (tmp_path / "extra.txt").write_text("new numeric feature")
    _git(tmp_path, "add", "extra.txt")
    _git(tmp_path, "commit", "-q", "-m", "candidate")
    head = _git(tmp_path, "rev-parse", "HEAD")
    manager.admit_read_only_target(task_id=task.id, source_commit=head, trigger_event_id="new-generation", trace_id="run")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    service = SimpleNamespace(state_dir=state, project_root=tmp_path, config=config, writer=writer, actor="operator")
    return SimpleNamespace(service=service, manager=manager, task=task, old=old, head=head,
        receipt={"task_ref": "task/AUDIT", "task_commit": old},
        context={"workflow_run_id": "run", "generation_admitted_event_id": "new-generation"})


def test_native_manager_restores_exact_read_only_ref_idempotently(refs):
    from zf.runtime.plan_completion_refs import validate_reference, restore_reference
    r = refs
    proof = validate_reference(r.service, r.task, r.receipt, r.context, r.head)
    manifest = {"workflow_run_id": "run", "prior_task_map_generation": "old"}
    row = {"task_id": r.task.id, "task_ref_proof": proof}
    request = ZfEvent(type="controlled.action.requested")
    restore_reference(r.service, row, manifest, {"ref": "reviewed-proof", "sha256": "review"}, request)
    assert _git(r.service.project_root, "rev-parse", "task/AUDIT") == r.old
    assert r.manager._read_index_unlocked()["AUDIT"]["source_commit"] == r.old
    assert validate_reference(r.service, r.task, r.receipt, r.context, r.head) == proof
    restore_reference(r.service, row, manifest, {}, request)
    events = r.service.writer.event_log.read_all()
    assert len(events) == 1 and events[0].type == "task.ref.updated"
    assert events[0].payload["completion_reuse_ref"]["sha256"] == "review"


@pytest.mark.parametrize("fault", ["writer", "run", "generation", "head", "index"])
def test_unknown_ref_changes_are_not_rewound(refs, fault):
    from zf.runtime.plan_completion_refs import validate_reference
    r = refs
    if fault == "writer": r.task.contract.evidence_contract = {}
    if fault == "run": r.context["workflow_run_id"] = "other"
    if fault == "generation": r.context["generation_admitted_event_id"] = "other"
    if fault == "head": r.head = r.old
    if fault == "index": _git(r.service.project_root, "update-ref", "refs/heads/task/AUDIT", r.old)
    with pytest.raises(ValueError):
        validate_reference(r.service, r.task, r.receipt, r.context, r.head)


def test_read_only_ref_restore_uses_git_cas(refs):
    r = refs
    with pytest.raises(RuntimeError):
        r.manager.admit_read_only_target(task_id="AUDIT", source_commit=r.old,
            expected_current_commit=r.old, trigger_event_id="review", trace_id="run")
    assert _git(r.service.project_root, "rev-parse", "task/AUDIT") == r.head
    assert r.manager._read_index_unlocked()["AUDIT"]["source_commit"] == r.head


def test_restored_reference_revalidates_native_integration_receipt(tmp_path):
    from tests.test_candidate_incremental import _setup, _integrate
    from zf.runtime.candidates import CandidateRebuilder
    from zf.runtime.candidate_incremental import hydrate_task_integration_receipt, CandidateIncrementalError
    from zf.runtime.plan_completion_refs import validate_reference, restore_reference

    state, config, log, writer, base, source = _setup(tmp_path)
    _integrate(tmp_path, state, config, log, writer, base)
    integrated = next(e for e in log.read_all() if e.type == "integration.queue.integrated")
    rebuilder = CandidateRebuilder(state_dir=state, project_root=tmp_path, config=config, event_log=log)
    original = hydrate_task_integration_receipt(rebuilder, integrated.payload["receipt_ref"])
    _git(tmp_path, "checkout", "--detach", original["new_candidate_head"])
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "other candidate work")
    head = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/heads/candidate/F-11111111", head)
    manager = TaskRefManager(state_dir=state, project_root=tmp_path, config=config)
    manager.admit_read_only_target(task_id="TASK-A", source_commit=head,
                                  trigger_event_id="new-generation", trace_id="run-1")
    with pytest.raises(CandidateIncrementalError, match="TaskRef is stale"):
        hydrate_task_integration_receipt(rebuilder, integrated.payload["receipt_ref"])
    service = SimpleNamespace(state_dir=state, project_root=tmp_path, config=config, writer=writer, actor="operator")
    task = Task(id="TASK-A", contract=TaskContract(evidence_contract={"execution_mode": "runtime_only"}))
    proof = validate_reference(service, task, original,
        {"workflow_run_id": "run-1", "generation_admitted_event_id": "new-generation"}, head)
    restore_reference(service, {"task_id": task.id, "task_ref_proof": proof},
        {"workflow_run_id": "run-1", "prior_task_map_generation": "map-g1"},
        {"ref": "bound-review"}, ZfEvent(type="controlled.action.requested"))
    assert hydrate_task_integration_receipt(rebuilder, integrated.payload["receipt_ref"]) == original
