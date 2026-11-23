"""Failed actuator preparation becomes durable RM work, not repeated Git effects."""
from unittest.mock import MagicMock, patch

import pytest

from tests.test_run_manager import _config, _state
from tests.test_self_repair_runner import _dispatch_requested
from zf.core.events import ZfEvent
from zf.runtime.repair_dispatch import pending_repair_dispatches
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.self_repair_runner import dispatch_pending_self_repairs


def test_failed_dispatch_restarts_into_diagnosis_once(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    request = _dispatch_requested()
    request.payload["repair_task_payload"]["continuation"] = {"workflow_run_id": "run-original"}
    writer.append(request)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stderr="branch already exists", stdout="")
        for _ in range(3):
            assert dispatch_pending_self_repairs(log.read_all(), writer, root=str(tmp_path),
                tmp_root=str(tmp_path)) == 0
        assert sum("worktree" in call.args[0] for call in run.call_args_list) == 1
    blocked = [e for e in log.read_all() if e.type == "autoresearch.repair.dispatch_blocked"]
    assert len(blocked) == 1
    assert blocked[0].payload["source_request_id"] == request.id
    def diagnoses():
        return [a for a in build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
                if a.get("failure_class") == "repair_dispatch_failed"]
    actions = diagnoses()
    assert len(actions) == 1
    assert actions[0]["workflow_run_id"] == "run-original"
    assert actions[0]["source_event_ids"] == [request.id, blocked[0].id]
    assert actions[0]["policy_decision"]["executable"]
    run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False,
                     action_filter={"diagnose-attention"})
    assert any(e.type == "run.manager.autoresearch.requested" for e in log.read_all())
    assert diagnoses() == []
    assert pending_repair_dispatches(log.read_all()) == []


@pytest.mark.parametrize("fault", ["worker", "wrong_request", "wrong_candidate", "new_attempt"])
def test_unrelated_failure_does_not_consume_request(tmp_path, fault):
    _, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    request = writer.append(_dispatch_requested())
    payload = {**request.payload, "reason": "worktree_add_failed", "source_request_id": request.id}
    if fault == "wrong_request": payload["source_request_id"] = "another-request"
    if fault == "wrong_candidate": payload["candidate_id"] = "another-candidate"
    writer.append(ZfEvent(type="autoresearch.repair.dispatch_blocked",
        origin="worker" if fault == "worker" else "kernel", payload=payload))
    if fault == "new_attempt":
        request = writer.append(_dispatch_requested(attempt=1))
    pending = pending_repair_dispatches(log.read_all())
    assert [p.event_id for p in pending] == [request.id]
