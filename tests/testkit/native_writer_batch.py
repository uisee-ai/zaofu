"""Raw two-task planner input and completed-task preservation oracle."""

from dataclasses import asdict

from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def batch_continuity_evidence(events: list[ZfEvent], interrupted_dispatch_id: str) -> list[str]:
    """Permit proven cross-task fresh starts, never interrupted-operation downgrades."""
    [cutoff] = [i for i, event in enumerate(events) if event.id == interrupted_dispatch_id]
    interrupted = events[cutoff]
    assert interrupted.type == "task.pipeline.stage.dispatched"
    accepted = []
    for index, event in enumerate(events):
        if event.type != "role.lifecycle.continuity.downgraded":
            continue
        assert index < cutoff, "continuity downgraded during recovery or terminal replay"
        assert event.payload.get("reason") == "provider_session_resume_not_proven"
        assert event.payload.get("previous_state") == "suspended"
        role = event.payload.get("instance_id")
        assert role and role == interrupted.payload["role_instance"]
        prior = [(i, e) for i, e in enumerate(events[:index])
                 if e.type == "task.pipeline.stage.dispatched" and e.payload.get("role_instance") == role]
        assert prior, "no previous task-stage dispatch proves a task boundary"
        prior_index, previous = prior[-1]
        following = [e for e in events[index + 1:]
                     if e.type == "task.pipeline.stage.dispatched" and e.payload.get("role_instance") == role]
        assert following and following[0].id == interrupted_dispatch_id
        assert event.task_id == interrupted.payload["task_id"]
        assert previous.payload["task_id"] != interrupted.payload["task_id"]
        for field in ("task_stage_session_binding", "provider_session_id"):
            assert previous.payload[field] and interrupted.payload[field]
            assert previous.payload[field] != interrupted.payload[field], field
        boundary = events[prior_index + 1:index]
        assert any(e.type == "task.done" and e.task_id == previous.payload["task_id"] for e in boundary)
        assert any(e.type == "role.lifecycle.suspended" and e.payload.get("instance_id") == role
                   and e.payload.get("reason") == "task_stage_session_archived" for e in boundary)
        accepted.append(event.id)
    return accepted


def writer_batch_items(marker: str) -> list[dict]:
    return [{
        "task_id": f"TASK-{index}", "title": f"Write {marker}-{index} to {name}",
        "scope": name, "allowed_paths": [name],
        "blocked_by": ["TASK-1"] if index == 2 else [],
        "acceptance_criteria": [f"{name} contains exactly {marker}-{index} and a newline"],
        "verification": f"git cat-file -e HEAD:{name}",
        "validation": {"commands": [{
            "id": f"file-{index}", "command": f"git cat-file -e HEAD:{name}",
            "tier": "runtime", "rolling_smoke": True, "deterministic": True, "reusable": True,
        }]},
    } for index, name in enumerate(("a.txt", "b.txt"), 1)]


def completed_task_evidence(runtime, task_id: str) -> dict:
    """Compare full persisted attempts, settlements and bound integration bodies."""
    assert runtime.task_store.get(task_id).status == "done"
    events = runtime.event_log.read_all()
    dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                  and e.payload["task_id"] == task_id]
    assert [e.payload["task_pipeline_stage"] for e in dispatches] == ["impl", "verify"]
    attempts = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    rows = [attempts.get(e.payload["attempt_id"]) for e in dispatches]
    assert all(row["status"] == "succeeded" for row in rows)
    operation_ids = {e.payload["operation_id"] for e in dispatches}
    settlements = [e for e in events if e.type == "workflow.operation.settled"
                   and e.payload.get("operation_id") in operation_ids]
    assert len(settlements) == 2
    [integrated] = [e for e in events if e.type == "integration.queue.integrated"
                    and (e.task_id or e.payload.get("task_id")) == task_id]
    receipt = hydrate_sidecar_ref(runtime.state_dir, integrated.payload["receipt_ref"]).payload
    [done] = [e for e in events if e.type == "task.done" and e.task_id == task_id]
    return {"dispatches": [asdict(e) for e in dispatches], "attempts": rows,
            "settlements": [asdict(e) for e in settlements],
            "integrated": asdict(integrated), "receipt": receipt, "done": asdict(done)}
