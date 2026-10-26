from tests.test_task_pipeline_reconciler import _operation, _policy, _task
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler


def test_eight_lane_batch_respects_remaining_unverified_capacity():
    policy = _policy()
    policy["max_active_task_pipelines"] = 16
    policy["backpressure"]["max_unverified_tasks"] = 8
    policy["pools"]["impl"].update(capacity=8, role_instances=[f"impl-{i}" for i in range(8)])
    policy["pools"]["verify"].update(capacity=8, role_instances=[f"verify-{i}" for i in range(8)])
    tasks = [_task("A", priority=1), *[_task(f"B{i}", priority=2) for i in range(8)]]
    projection = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=tasks,
        operations=[_operation("A", "impl", "settled")], attempts=[],
    )
    assert len(projection["dispatchable"]["verify"]) == 1
    assert len(projection["dispatchable"]["impl"]) == 7
    assert len({r["role_instance"] for r in projection["dispatchable"]["impl"]}) == 7


def test_full_verify_backpressure_drains_verify_without_starting_fresh_impl():
    policy = _policy()
    policy["max_active_task_pipelines"] = 16
    policy["backpressure"]["max_unverified_tasks"] = 8
    policy["pools"]["impl"].update(capacity=8, role_instances=[f"impl-{i}" for i in range(8)])
    policy["pools"]["verify"].update(capacity=8, role_instances=[f"verify-{i}" for i in range(8)])
    tasks = [_task(f"A{i}", priority=1) for i in range(8)] + [_task("B", priority=2)]
    projection = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=tasks,
        operations=[_operation(f"A{i}", "impl", "settled") for i in range(8)], attempts=[],
    )
    assert len(projection["dispatchable"]["verify"]) == 8
    assert projection["dispatchable"]["impl"] == []
