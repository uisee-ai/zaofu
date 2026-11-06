"""Storage faults must preserve known state and never announce nonexistent input."""
import errno

import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_operation import WorkflowOperationService
from tests.test_run_manager import _state


def test_store_enospc_preserves_last_committed_state(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="existing", title="durable"))
    old = store.path.read_bytes()
    def fail_sync(_):
        raise OSError(errno.ENOSPC, "injected disk full")
    with monkeypatch.context() as fault:
        fault.setattr("zf.core.state.atomic_io.os.fsync", fail_sync)
        with pytest.raises(OSError, match="injected disk full"):
            store.add(Task(id="new", title="not committed"))
    assert store.path.read_bytes() == old
    assert TaskStore(store.path).get("new") is None
    assert TaskStore(store.path).get("existing").title == "durable"
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_required_sidecar_write_does_not_publish_operation(tmp_path, monkeypatch):
    state, log, writer = _state(tmp_path)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    def fail_replace(*_):
        raise OSError(errno.ENOSPC, "injected disk full")
    monkeypatch.setattr("zf.core.state.atomic_io.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected disk full"):
        service.ensure_operation(workflow_run_id="run", operation_id="op", operation_type="agent",
                                 request={"prompt": "required fixed input"})
    assert not any(e.type == "workflow.operation.requested" for e in log.read_all())
