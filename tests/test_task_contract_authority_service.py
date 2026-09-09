from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.housekeeping import apply_task_contract_event
from zf.runtime.task_contract_authority import (
    TaskContractAuthorityConflict,
    TaskContractAuthorityService,
)
from zf.core.events.model import ZfEvent


def _service(tmp_path: Path) -> tuple[TaskStore, EventLog, TaskContractAuthorityService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    return store, log, TaskContractAuthorityService(
        task_store=store,
        event_writer=EventWriter(log),
        state_dir=state_dir,
    )


def test_contract_authority_cas_rejects_second_stale_writer(tmp_path: Path) -> None:
    store, log, service = _service(tmp_path)
    store.add(Task(
        id="TASK-CAS",
        title="CAS",
        contract=TaskContract(behavior="R1", verification="true"),
    ))
    writer_one = store.get("TASK-CAS")
    writer_two = store.get("TASK-CAS")
    assert writer_one is not None and writer_two is not None

    first = service.replace(
        writer_one,
        contract=TaskContract(behavior="R2", verification="true"),
        source="test_replan",
    )
    with pytest.raises(TaskContractAuthorityConflict):
        service.replace(
            writer_two,
            contract=TaskContract(behavior="stale R1 write", verification="false"),
            source="late_projection",
        )

    current = store.get("TASK-CAS")
    assert current is not None
    assert current.contract.behavior == "R2"
    assert current.contract_authority_revision == first.authority_revision
    assert current.contract_authority_sequence == 1
    assert [event.type for event in log.read_all()] == [
        "task.contract.mutation.prepared",
        "task.contract.revision.applied",
        "task.contract.update",
        "task.contract.change.rejected",
    ]


def test_execution_binding_is_first_class_and_audit_replay_is_noop(
    tmp_path: Path,
) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-WORKFLOW",
        title="Workflow",
        contract=TaskContract(behavior="current", verification="true"),
    ))
    task = store.get("TASK-WORKFLOW")
    assert task is not None
    applied = service.replace(
        task,
        contract=task.contract,
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-r2",
            request_revision=2,
            workflow_run_id="run-r2",
        ),
        source="workflow_submit",
    )

    apply_task_contract_event(store, ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id=task.id,
        payload={
            "source": "task.create-from-contract",
            "contract": asdict(TaskContract(behavior="stale creation")),
        },
    ))

    current = store.get(task.id)
    assert current is not None
    assert current.contract.behavior == "current"
    assert current.execution_binding.owner == "workflow"
    assert current.execution_binding.request_id == "request-r2"
    assert current.contract_authority_revision == applied.authority_revision


def test_projection_metadata_patch_cannot_replace_semantic_contract(
    tmp_path: Path,
) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-PROJECTION",
        title="Projection",
        contract=TaskContract(behavior="R2", verification="pytest current"),
    ))
    task = store.get("TASK-PROJECTION")
    assert task is not None
    applied = service.replace(
        task,
        contract=task.contract,
        source="task_map_materialization",
    )

    service.patch_metadata(task.id, {
        "task_doc_ref": ".zf/task_docs/TASK-PROJECTION/task.md",
        "source_revision": "source-current",
        "capsule_revision": "capsule-current",
    })

    current = store.get(task.id)
    assert current is not None
    assert current.contract.behavior == "R2"
    assert current.contract.verification == "pytest current"
    assert current.contract.task_doc_ref.endswith("task.md")
    assert current.contract_authority_revision == applied.authority_revision
    assert current.contract_authority_sequence == 1


def test_stale_semantic_writer_cannot_roll_back_current_projection_metadata(
    tmp_path: Path,
) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-METADATA-RACE",
        title="Projection race",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    stale = store.get("TASK-METADATA-RACE")
    assert stale is not None
    service.patch_metadata(stale.id, {
        "task_doc_ref": ".zf/task_docs/current/task.md",
        "contract_revision": "contract-current",
        "acceptance_evidence": {"AC-1": ["evt-current"]},
    })

    mutation = service.replace(
        stale,
        contract=TaskContract(
            behavior="R2",
            verification="pytest tests/test_current.py",
        ),
        source="semantic_replan",
    )

    assert mutation.task.contract.behavior == "R2"
    assert mutation.task.contract.task_doc_ref.endswith("current/task.md")
    assert mutation.task.contract.contract_revision == "contract-current"
    assert mutation.task.contract.acceptance_evidence == {
        "AC-1": ["evt-current"],
    }


def test_contract_authority_can_reopen_terminal_task_with_cas(
    tmp_path: Path,
) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-REOPEN",
        title="Terminal",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    original = store.get("TASK-REOPEN")
    assert original is not None
    first = service.replace(
        original,
        contract=original.contract,
        source="initial_materialization",
    )
    store.update(first.task.id, status="done")
    archived = store.get(first.task.id)
    assert archived is not None and archived.status == "done"

    reopened = service.replace(
        archived,
        contract=TaskContract(behavior="R2", verification="pytest -q"),
        source="replan",
        task_updates={"status": "backlog", "completed_at": None},
        reopen_terminal=True,
    )

    current = store.get(first.task.id)
    assert current is not None
    assert current.status == "backlog"
    assert current.contract.behavior == "R2"
    assert current.contract_authority_sequence == 2
    assert reopened.task.contract_authority_revision == current.contract_authority_revision


def test_contract_authority_can_reopen_terminal_task_with_same_contract(
    tmp_path: Path,
) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-REOPEN-SAME-CONTRACT",
        title="Terminal",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    original = store.get("TASK-REOPEN-SAME-CONTRACT")
    assert original is not None
    stamped = service.replace(original, contract=original.contract, source="initial")
    store.update(stamped.task.id, status="cancelled")
    archived = store.get(stamped.task.id)
    assert archived is not None and archived.status == "cancelled"

    reopened = service.replace(
        archived,
        contract=archived.contract,
        source="retry",
        task_updates={"status": "backlog", "cancelled_at": None},
        reopen_terminal=True,
    )

    current = store.get(stamped.task.id)
    assert current is not None
    assert current.status == "backlog"
    assert reopened.changed is True


def test_operational_task_patch_does_not_invalidate_contract_snapshot(
    tmp_path: Path,
) -> None:
    store, log, service = _service(tmp_path)
    store.add(Task(
        id="TASK-DISPATCH",
        title="Dispatch",
        contract=TaskContract(behavior="current", verification="pytest"),
    ))
    task = store.get("TASK-DISPATCH")
    assert task is not None
    stamped = service.replace(
        task,
        contract=task.contract,
        source="task_map_materialization",
    )
    event_count = len(log.read_all())

    patched = service.replace(
        stamped.task,
        contract=stamped.task.contract,
        source="writer_dispatch_owner_binding",
        task_updates={
            "assigned_to": "dev-lane-0",
            "active_dispatch_id": "dispatch-current",
        },
    )

    current = store.get(task.id)
    assert current is not None
    assert current.assigned_to == "dev-lane-0"
    assert current.active_dispatch_id == "dispatch-current"
    assert current.contract_authority_revision == stamped.authority_revision
    assert current.contract_authority_sequence == stamped.authority_sequence
    assert patched.authority_revision == stamped.authority_revision
    assert patched.changed is False
    assert len(log.read_all()) == event_count


def test_change_request_replay_returns_existing_receipt_without_new_revision(
    tmp_path: Path,
) -> None:
    store, log, service = _service(tmp_path)
    store.add(Task(
        id="TASK-REQUEST",
        title="Request",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    request = ZfEvent(
        type="task.contract.change.requested",
        actor="orchestrator",
        task_id="TASK-REQUEST",
        payload={
            "contract": asdict(TaskContract(
                behavior="R2",
                verification="pytest tests/test_current.py",
            )),
            "expected_authority_revision": "",
            "source": "orchestrator_replan",
        },
    )

    first = service.apply_change_request(
        request,
        allowed_actors={"orchestrator"},
    )
    replay = service.apply_change_request(
        request,
        allowed_actors={"orchestrator"},
    )

    assert first is not None and replay is not None
    assert first.authority_revision == replay.authority_revision
    assert replay.changed is False
    current = store.get("TASK-REQUEST")
    assert current is not None
    assert current.contract_authority_sequence == 1
    assert sum(
        event.type == "task.contract.revision.applied"
        for event in log.read_all()
    ) == 1
    assert not any(
        event.type == "task.contract.change.rejected"
        for event in log.read_all()
    )


def test_stamped_task_rejects_unconditional_contract_update(tmp_path: Path) -> None:
    store, _, service = _service(tmp_path)
    store.add(Task(
        id="TASK-NO-BYPASS",
        title="No bypass",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    task = store.get("TASK-NO-BYPASS")
    assert task is not None
    service.replace(
        task,
        contract=task.contract,
        source="task_map_materialization",
    )

    with pytest.raises(ValueError, match="TaskContractAuthorityService"):
        store.update(
            task.id,
            contract=TaskContract(behavior="stale", verification="false"),
        )

    current = store.get(task.id)
    assert current is not None
    assert current.contract.behavior == "R1"
