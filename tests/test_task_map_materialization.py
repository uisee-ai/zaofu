from __future__ import annotations

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.task_map_materialization import (
    commit_task_map_materialization,
    prepare_task_map_materialization,
)


def test_materialization_rolls_forward_after_store_fault(tmp_path):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    tasks = [
        Task(
            id="A",
            title="A",
            status="backlog",
            contract=TaskContract(behavior="a", verification="true"),
        ),
        Task(
            id="B",
            title="B",
            status="backlog",
            blocked_by=["A"],
            contract=TaskContract(behavior="b", verification="true"),
        ),
    ]
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=tasks,
        task_map_ref="artifacts/task-map.json",
        package_id="planpkg-package-sha",
        package_ref="artifacts/plan-packages/p.json",
        package_digest="package-sha",
        writer=writer,
    )

    with pytest.raises(RuntimeError, match="injected materialization fault"):
        commit_task_map_materialization(
            state_dir=state_dir,
            plan=plan,
            descriptor=descriptor,
            writer=writer,
            fail_after_store_write=True,
        )

    result = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )
    replay = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )

    assert [task.id for task in TaskStore(state_dir / "kanban.json").list_all()] == ["A", "B"]
    assert result["status"] == "committed"
    assert result["plan_artifact_package_id"] == "planpkg-package-sha"
    assert replay == result
    events = writer.event_log.read_all()
    assert sum(event.type == "task_map.materialization.prepared" for event in events) == 1
    assert sum(event.type == "task_map.materialization.committed" for event in events) == 1
    assert sum(event.type == "task.created" for event in events) == 2


def test_materialization_replaces_existing_semantics_without_rolling_back_binding(
    tmp_path,
):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-REPLAN",
        title="Replanned task",
        contract=TaskContract(
            behavior="R1 behavior",
            verification="R1 verification",
            evidence_contract={
                "contract_revision": "R1",
                "task_map_generation": "G1",
            },
        ),
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-current",
            request_revision=2,
            workflow_run_id="run-current",
        ),
    ))
    r2_task = Task(
        id="TASK-REPLAN",
        title="Replanned task",
        contract=TaskContract(
            behavior="R2 behavior with SENTINEL-AC",
            verification="R2 verification",
            evidence_contract={
                "contract_revision": "R2",
                "task_map_generation": "G2",
            },
        ),
    )
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=[r2_task],
        task_map_ref="artifacts/task-map-r2.json",
        writer=writer,
    )

    result = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )

    current = store.get(r2_task.id)
    assert current is not None
    assert result["created_task_ids"] == []
    assert result["skipped_task_ids"] == [r2_task.id]
    assert current.contract.behavior == "R2 behavior with SENTINEL-AC"
    assert current.contract.verification == "R2 verification"
    assert current.contract.evidence_contract["contract_revision"] == "R2"
    assert current.contract.evidence_contract["task_map_generation"] == "G2"
    assert current.execution_binding == TaskExecutionBinding(
        owner="workflow",
        request_id="request-current",
        request_revision=2,
        workflow_run_id="run-current",
    )
    assert current.contract_authority_sequence == 1
    assert current.contract_authority_revision


def test_revised_map_updates_backlog_dependencies_and_scheduler_readiness(tmp_path):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store = TaskStore(state_dir / "kanban.json")
    store.add_many([
        Task(id="A", status="backlog", contract=TaskContract(behavior="A")),
        Task(id="B", status="backlog", blocked_by=["A"],
             contract=TaskContract(behavior="old")),
    ])
    assert [task.id for task in store.ready()] == ["A"]
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=[Task(id="A", status="backlog", contract=TaskContract(behavior="A")),
               Task(id="B", status="backlog", contract=TaskContract(behavior="new"))],
        task_map_ref="revised-task-map.json", writer=writer,
    )
    result = commit_task_map_materialization(state_dir=state_dir, plan=plan,
                                              descriptor=descriptor, writer=writer)
    assert store.get("B").blocked_by == []
    assert {task.id for task in store.ready()} == {"A", "B"}
    from tests.test_task_pipeline_reconciler import _policy
    from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler

    policy = _policy()
    policy["mode"] = "blocking"
    policy["pools"]["impl"].update(capacity=2, role_instances=["impl-1", "impl-2"])
    projection = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=store.list_all(), operations=[], attempts=[],
    )
    assert {item["task_id"] for item in projection["dispatchable"]["impl"]} == {"A", "B"}
    assert commit_task_map_materialization(state_dir=state_dir, plan=plan,
                                           descriptor=descriptor, writer=writer) == result
    assert sum(e.type == "task_map.materialization.committed"
               for e in writer.event_log.read_all()) == 1


def test_revised_map_rejects_active_dependency_changes_before_new_tasks(tmp_path):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store = TaskStore(state_dir / "kanban.json")
    store.add_many([
        Task(id="A", status="backlog"),
        Task(id="B", status="in_progress", blocked_by=["A"]),
    ])
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=[Task(id="B", status="backlog"), Task(id="C", status="backlog")],
        task_map_ref="revised-task-map.json", writer=writer,
    )
    with pytest.raises(ValueError, match="cannot change dependencies of non-backlog task B"):
        commit_task_map_materialization(state_dir=state_dir, plan=plan,
                                         descriptor=descriptor, writer=writer)
    assert store.get("B").blocked_by == ["A"]
    assert store.get("C") is None


@pytest.mark.parametrize("change_contract", [False, True])
@pytest.mark.parametrize("race_kind", ["dispatch", "dependency"])
def test_dependency_update_cas_rejects_task_started_during_materialization(
    tmp_path, monkeypatch, change_contract, race_kind,
):
    from zf.runtime.task_contract_authority import TaskContractAuthorityConflict

    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store = TaskStore(state_dir / "kanban.json")
    store.add_many([
        Task(id="A", status="backlog"),
        Task(id="C", status="backlog"),
        Task(id="B", status="backlog", blocked_by=["A"],
             contract=TaskContract(behavior="old")),
    ])
    initial, initial_ref = prepare_task_map_materialization(
        state_dir=state_dir, tasks=[store.get("A"), store.get("B")],
        task_map_ref="old.json", writer=writer,
    )
    commit_task_map_materialization(state_dir=state_dir, plan=initial,
                                    descriptor=initial_ref, writer=writer)
    task = store.get("B")
    task.blocked_by = []
    if change_contract:
        task.contract.behavior = "new"
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir, tasks=[task], task_map_ref="new.json", writer=writer,
    )
    method = "compare_and_update_contract" if change_contract else "compare_and_update_fields"
    original = getattr(TaskStore, method)

    def start_before_compare(self, task_id, **kwargs):
        if race_kind == "dispatch":
            self.update(task_id, status="in_progress")
        else:
            self.update(task_id, blocked_by=["C"])
        return original(self, task_id, **kwargs)

    monkeypatch.setattr(TaskStore, method, start_before_compare)
    with pytest.raises(TaskContractAuthorityConflict):
        commit_task_map_materialization(state_dir=state_dir, plan=plan,
                                         descriptor=descriptor, writer=writer)
    current = store.get("B")
    assert current.status == ("in_progress" if race_kind == "dispatch" else "backlog")
    assert current.blocked_by == (["A"] if race_kind == "dispatch" else ["C"])
