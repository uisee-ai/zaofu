"""A completed reader's task treatment must not poison its fresh idle session."""

from zf.core.events.model import ZfEvent
from tests.test_role_lifecycle_runtime import _runtime


def test_completed_reader_recycles_then_dispatches_again(tmp_path):
    runtime, role, transport = _runtime(tmp_path)
    # Same resident instance retains treatment identity after a fanout closes.
    runtime._active_skill_treatment_tasks = {role.instance_id: "TASK-OLD"}
    runtime._activation_skill_provenance = {(role.instance_id, "TASK-OLD"): ["old"]}
    for kind in ("fanout.child.dispatched", "fanout.child.completed"):
        runtime.event_writer.append(ZfEvent(type=kind, actor="kernel", payload={
            "fanout_id": "fanout-old", "child_id": "reader", "run_id": "read-old",
            "role_instance": role.instance_id, "task_id": "TASK-OLD",
        }))
    runtime._instance_state[role.instance_id] = "recycling"
    runtime._start_recycle(role)
    events = runtime.event_log.read_all()
    assert not [e for e in events if e.type == "worker.recycle.failed"]
    assert any(e.type == "worker.recycled" for e in events)
    assert runtime._active_skill_treatment_tasks.get(role.instance_id) is None
    assert not [key for key in runtime._activation_skill_provenance if key[0] == role.instance_id]
    assert transport.sent == []  # Never replay a completed reader.
    briefing = runtime.state_dir / "briefings" / "new.md"
    briefing.write_text("new reader contract\n")
    runtime._send_transport_task(role.instance_id, briefing, "new reader", None)
    assert transport.sent == [role.instance_id]


def test_taskless_active_reader_keeps_its_treatment(tmp_path):
    runtime, role, _ = _runtime(tmp_path)
    runtime._active_skill_treatment_tasks = {role.instance_id: "TASK-ACTIVE"}
    runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", actor="kernel", payload={
        "fanout_id": "active", "child_id": "reader", "run_id": "read-active",
        "role_instance": role.instance_id, "task_id": "TASK-ACTIVE",
    }))
    assert not runtime._inject_recovery_briefing(role, inject_idle_prompt=False)
    assert runtime._active_skill_treatment_tasks[role.instance_id] == "TASK-ACTIVE"


def test_idle_preparation_failure_preserves_diagnostic(tmp_path, monkeypatch):
    runtime, role, _ = _runtime(tmp_path)

    def broken(**_kwargs):
        raise ValueError("invalid frozen skill manifest")

    monkeypatch.setattr(runtime, "_record_skill_provenance", broken)
    assert not runtime._inject_recovery_briefing(role, inject_idle_prompt=False)
    event = runtime.event_log.read_all()[-1]
    assert event.type == "worker.recovery.skipped"
    assert event.payload["reason"] == "idle_recovery_preparation_failed"
    assert event.payload["error_type"] == "ValueError"
