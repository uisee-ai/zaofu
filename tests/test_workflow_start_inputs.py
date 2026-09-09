from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from zf.runtime.workflow_anchor import is_workflow_managed_task
from zf.runtime.workflow_start_inputs import ensure_workflow_managed_task


def test_workflow_start_reopens_authority_stamped_cancelled_task(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store.add(Task(
        id="TASK-CANCELLED",
        title="Retry an approved workflow",
        contract=TaskContract(
            behavior="Run the approved fix.",
            verification="Verify the resulting candidate.",
        ),
    ))
    task = store.get("TASK-CANCELLED")
    assert task is not None
    stamped = TaskContractAuthorityService(
        task_store=store,
        event_writer=writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=task.contract,
        source="test_setup",
    ).task
    archived = store.update(
        stamped.id,
        status="cancelled",
        cancelled_at="2026-09-07T00:00:00Z",
    )
    assert archived is not None

    ensure_workflow_managed_task(
        state_dir=state_dir,
        workflow_task=archived,
        writer=writer,
        actor="web",
        causation_id="evt-approved-proposal",
        correlation_id="workflow-retry",
    )

    reopened = store.get(archived.id)
    assert reopened is not None
    assert reopened.status == "backlog"
    assert reopened.cancelled_at is None
    assert reopened.contract_authority_revision != (
        archived.contract_authority_revision
    )
    assert is_workflow_managed_task(reopened)
