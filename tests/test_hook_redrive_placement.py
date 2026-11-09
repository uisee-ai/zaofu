from copy import deepcopy

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.cli.hook_dispatch_context import active_task_pipeline_dispatch
from zf.cli.hook_workdir_guard import evaluate_workdir_write_guard


def test_redrive_workspace_uses_current_attempt_placement(tmp_path):
    state = tmp_path / "state"
    workdir = state / "workdirs/tasks/R/T/g1/project"
    workdir.mkdir(parents=True)
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    common = {"operation_id": "op", "task_id": "T", "workflow_run_id": "R",
              "task_pipeline_stage": "impl", "task_map_generation": "M",
              "workspace_generation": 1, "role_instance": "dev-0"}
    def emit(kind, payload):
        return writer.append(ZfEvent(type=kind, actor="kernel", origin="kernel",
                                   task_id="T", payload={**common, **payload}))
    emit("workflow.operation.requested", {"placement_epoch": 1})
    attempt = {"dispatch_id": "dispatch-2", "attempt_id": "a2", "lease_id": "l2", "placement_epoch": "2"}
    emit("task.attempt.started", attempt)
    emit("workflow.operation.started", {"dispatch_id": "dispatch-2", "active_attempt_id": "a2", "lease_id": "l2"})
    current = emit("task.pipeline.stage.dispatched", {**attempt, "placement_epoch": 2, "workdir": str(workdir)})
    assert active_task_pipeline_dispatch(log.read_all(), "dev-0").id == current.id
    assert evaluate_workdir_write_guard(state_dir=state, project_root=tmp_path,
        event_type="codex.hook.pre_tool_use", actor="dev-0", event_payload={
            "cwd": str(workdir), "tool_name": "Write", "tool_input": {"file_path": str(workdir / "test.py")}},
        event_log=log, event_writer=writer, causation_id=None,
        should_check_actor=lambda _: True, active_task_id_for_actor=lambda *_: "T") == 0
    forged = emit("task.pipeline.stage.dispatched", {**attempt, "attempt_id": "other", "placement_epoch": 3, "workdir": str(tmp_path)})
    assert active_task_pipeline_dispatch(log.read_all(), "dev-0").id != forged.id

    events = log.read_all()
    proof = next(e for e in events if e.type == "task.attempt.started")
    for key in ("attempt_id", "lease_id", "dispatch_id", "operation_id", "workflow_run_id", "placement_epoch"):
        altered = deepcopy(events)
        next(e for e in altered if e.id == proof.id).payload[key] = "unrelated"
        assert active_task_pipeline_dispatch(altered, "dev-0") is None, key
    altered = deepcopy(events)
    next(e for e in altered if e.id == proof.id).origin = "agent"
    assert active_task_pipeline_dispatch(altered, "dev-0") is None
