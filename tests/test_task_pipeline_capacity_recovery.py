"""Parameterised deterministic lane admission, not real-provider throughput."""
import pytest

from tests.test_task_pipeline_reconciler import _operation, _policy, _task
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler


@pytest.mark.parametrize("impl_capacity,verify_capacity", [(1, 1), (3, 2), (8, 8), (12, 4)])
def test_valid_results_survive_recovery_with_configured_lane_mix(impl_capacity, verify_capacity):
    policy = _policy()
    count = impl_capacity + verify_capacity
    policy["max_active_task_pipelines"] = count
    policy["backpressure"] = {"max_unverified_tasks": count, "max_integration_queue": count}
    for stage, capacity in [("impl", impl_capacity), ("verify", verify_capacity)]:
        policy["pools"][stage].update(capacity=capacity,
            role_instances=[f"{stage}-{i}" for i in range(capacity)])
    ready = [f"verified-input-{i}" for i in range(verify_capacity)]
    fresh = [f"new-{i}" for i in range(impl_capacity)]
    tasks = [_task(task_id, priority=1) for task_id in ready + fresh]
    operations = [_operation(task_id, "impl", "settled") for task_id in ready]
    result = TaskPipelineReconciler().reconcile(policy=policy, tasks=tasks,
                                               operations=operations, attempts=[])
    assert {item["task_id"] for item in result["dispatchable"]["verify"]} == set(ready)
    assert {item["task_id"] for item in result["dispatchable"]["impl"]} == set(fresh)
    placements = [item["role_instance"] for stage in ["impl", "verify"]
                  for item in result["dispatchable"][stage]]
    assert len(placements) == len(set(placements)) == count
    replay = TaskPipelineReconciler().reconcile(policy=policy, tasks=reversed(tasks),
                                               operations=reversed(operations), attempts=[])
    assert replay == result
