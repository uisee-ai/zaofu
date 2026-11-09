"""Provider UI recovery must preserve completed planner work and synth identity."""

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.tmux_readiness import build_tmux_readiness_error

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator


@pytest.mark.parametrize("marker", ["quota_model_switch", "login_required", "usage_limit_reached"])
def test_synth_modal_defers_without_plan_rejection_and_recovers_same_operation(
    tmp_path, monkeypatch, marker,
):
    _, log, orch, fanout_id = _task_map_synth_orchestrator(
        tmp_path, complete_child=False,
    )
    send = orch._send_transport_task

    def blocked(*args, **kwargs):
        raise build_tmux_readiness_error("task-map-synth", {
            "failure_class": (
                "provider_quota_interactive" if marker == "usage_limit_reached"
                else "provider_interactive_blocked"
            ),
            "interactive_prompt_marker": marker,
            "current_command": "codex", "pane_alive": True,
        })

    monkeypatch.setattr(orch, "_send_transport_task", blocked)
    orch.run_once(events=[ZfEvent(
        type="task_map.child.completed", actor="source-reader",
        correlation_id="trace-product",
        payload={"fanout_id": fanout_id, "child_id": "source-reader",
                 "run_id": f"run-{fanout_id}-source-reader", "status": "completed",
                 "summary": "source read", "artifact_refs": ["docs/product-prd.md"]},
    )])
    orch._evaluate_reader_fanout(fanout_id)
    events = log.read_all()
    assert not any(e.type in {"fanout.synth.completed", "task_map.blocked"} for e in events)
    deferred = [e for e in events if e.type == "fanout.child.dispatch_deferred"
                and e.payload.get("child_id") == "synth"]
    assert len(deferred) == 1
    assert marker in deferred[0].payload["reason"]
    requested = next(e for e in events if e.type == "workflow.operation.requested"
                     and e.payload.get("operation_type") == "fanout_synth")

    monkeypatch.setattr(orch, "_send_transport_task", send)
    # Exercise the normal runtime recovery sweep, without replaying the planner.
    orch._recover_incomplete_reader_fanout_aggregates(log.read_all())
    events = log.read_all()
    dispatch = next(e for e in events if e.type == "fanout.synth.dispatched")
    assert dispatch.payload["operation_id"] == requested.payload["operation_id"]
    assert len([e for e in events if e.type == "fanout.child.dispatched"]) == 1
    assert not any(e.type == "task_map.blocked" for e in events)
