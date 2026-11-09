from tests.test_task_pipeline_reconciler import _operation, _policy, _task
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler


def _view(operations):
    return TaskPipelineReconciler().reconcile(
        policy={**_policy(), "max_rework_attempts": 2},
        tasks=[_task("A", priority=1)], operations=operations, attempts=[],
    )["tasks"][0]


def _failure(generation, failure_class="product"):
    return {**_operation("A", "verify", "settled", generation),
            "semantic_verdict": "failed", "failure_class": failure_class,
            "call_result_admitted_event_id": f"failed-{generation}"}


def test_identity_generation_jump_does_not_exhaust_semantic_rework():
    view = _view([_operation("A", "impl", "settled", 9), _failure(9)])
    assert view["stage"] == "impl_rework_ready"
    assert view["next_operation_generation"] == 10
    assert view["semantic_failure_count"] == 1


def test_real_failures_exhaust_and_replayed_result_counts_once():
    rows = [_failure(1), _failure(2), _operation("A", "impl", "settled", 9), _failure(9)]
    view = _view([*rows, rows[-1]])
    assert view["stage"] == "blocked"
    assert view["semantic_blocker"]["failure_count"] == 3
    assert view["semantic_blocker"]["failure_event_ids"] == ["failed-1", "failed-2", "failed-9"]


def test_environment_failure_does_not_route_to_product_rework():
    view = _view([_operation("A", "impl", "settled", 9), _failure(9, "environment")])
    assert view["stage"] == "blocked"
    assert view["semantic_failure_count"] == 0
    assert "semantic_rework_exhausted" not in view["blockers"]
