"""A restart must not queue old instructions behind a newly owned reader turn."""

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_fanout_hardening import _state
from zf.core.events import ZfEvent


def test_lost_reader_waits_for_new_owner_without_resetting_busy_state(tmp_path):
    _state, log, transport, runtime, _config, child = _durable_reader_state(tmp_path)
    role = child["role_instance"]
    log.append(ZfEvent(type="worker.launch_artifact.written", actor="zf-cli",
        payload={"instance_id": role, "launch_attempt": 2}))
    log.append(ZfEvent(type="fanout.child.dispatched", payload={
        "role_instance": role, "fanout_id": "new-work", "child_id": "new-child", "run_id": "new-run",
    }))
    runtime._set_worker_state(role, "busy", reason="new reader owns role", force=True)
    before, sent = len(log.read_all()), len(transport.sent)
    assert not runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    assert runtime._last_worker_state[role] == "busy"
    assert len(transport.sent) == sent
    assert not [e for e in log.read_all()[before:] if e.type == "fanout.child.dispatch_lost"]
    log.append(ZfEvent(type="fanout.child.completed", payload={
        "fanout_id": "new-work", "child_id": "new-child", "run_id": "new-run"}))
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    assert len(transport.sent) == sent + 1


@pytest.mark.parametrize("new_authority", ["generation", "redrive", "none"])
def test_recovery_order_uses_trigger_receipts_across_reset_attempts(tmp_path, new_authority):
    _, _, _, runtime = _state(tmp_path)
    old = {"stage_id": "plan", "trace_id": "RUN", "trigger_event_id": "old-request",
           "pdd_id": "P", "feature_id": "P", "aggregate_config": {"success_event": "task_map.ready"},
           "children": [{"role_instance": "planner"}],
           "trigger_payload": {"workflow_run_id": "RUN", "rework_attempt": 2}}
    new = {**old, "trigger_event_id": "new-request",
           "trigger_payload": {"workflow_run_id": "RUN", "rework_attempt": 1}}
    if new_authority == "generation":
        new["trigger_payload"].update(operator_authorized=True,
            operator_authorization_ref="owner:approved", stage_replan_generation="next")
    if new_authority == "redrive":
        new["trigger_payload"]["redrive_of"] = "old-request"
    args = {"manifests": [("old", old), ("new", new)],
            "started_order": {"new": 0, "old": 1},
            "trigger_order": {"old-request": 0, "new-request": 1}}
    assert runtime._newer_reader_replan_fanout(fanout_id="old", manifest=old, **args) == (
        "new" if new_authority != "none" else "")
    assert runtime._newer_reader_replan_fanout(fanout_id="new", manifest=new, **args) == ""
