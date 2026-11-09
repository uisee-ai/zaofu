from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.test_task_pipeline_fault_matrix import _runtime, _running_operation_v2_attempt
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


@pytest.mark.parametrize("concurrent", [False, True])
def test_rm_and_operator_replay_share_one_attempt_redrive(tmp_path, concurrent):
    runtime = _runtime(tmp_path)
    attempt = _running_operation_v2_attempt(runtime)
    service = WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                       event_writer=runtime.event_writer)
    operation = reduce_workflow_operations(runtime.event_log.read_all())[attempt["operation_id"]]
    identity = {"operation_id": operation["operation_id"], "request_hash": operation["request_hash"],
                "workflow_run_id": "run-1", "task_id": "TASK-A"}
    service.interrupt(**identity, reason="graceful_stop", source_attempt_id=attempt["attempt_id"])
    barrier = Barrier(2)
    def apply(owner):
        if concurrent:
            barrier.wait(timeout=2)
        return service.admit_redrive(**identity, source_attempt_id=attempt["attempt_id"],
            recovery_decision_event_id=f"decision-{owner}", recovery_decision_owner=owner,
            reason="resume same unfinished attempt")
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, ["run_manager", "kernel_replay"]))
    else:
        results = [apply("run_manager"), apply("kernel_replay")]
    assert sum(event is not None for event in results) == 1
    assert reduce_workflow_operations(runtime.event_log.read_all())[attempt["operation_id"]]["redrive_count"] == 1
