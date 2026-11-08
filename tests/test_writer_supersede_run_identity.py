"""The real writer adoption caller supplies authoritative terminal lineage."""
import json
from dataclasses import replace

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _pending_semantic_event_actions
from tests.test_writer_task_package_rebind import _setup


def test_orchestrator_supersede_carries_loaded_run_without_trigger(tmp_path):
    orch, store, loaded, log = _setup(tmp_path)
    failed = orch.event_writer.append(ZfEvent(type="task.attempt.failed", origin="kernel",
        task_id="TASK-1", payload={"workflow_run_id": "RUN-1", "task_id": "TASK-1"}))
    body = json.loads(loaded.task_map_path.read_text())
    body["amend"] = {"superseded_task_ids": ["TASK-1"]}
    loaded.task_map_path.write_text(json.dumps(body))
    successor = replace(loaded, task_items=loaded.task_items[1:])
    orch._ensure_writer_tasks_canonical(successor)
    terminal = next(e for e in log.read_all() if e.type == "task.superseded")
    assert terminal.task_id == "TASK-1"
    assert terminal.correlation_id == "RUN-1"
    assert terminal.payload["workflow_run_id"] == "RUN-1"
    assert store.get("TASK-1").status == "cancelled"
    assert _pending_semantic_event_actions([failed, terminal]) == []
