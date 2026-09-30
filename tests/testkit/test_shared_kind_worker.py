"""The shared worker must not borrow another Run's quality receipt."""

from types import SimpleNamespace

from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.core.events import ZfEvent


def test_candidate_quality_requires_same_run_candidate_pdd_and_commit():
    rows = [ZfEvent(type="candidate.ready", payload={
        "workflow_run_id": run, "pdd_id": pdd, "candidate_head_commit": commit,
    }) for run, pdd, commit in (("run-a", "feature-a", "a" * 40), ("run-b", "feature-b", "b" * 40))]
    receipts = [ZfEvent(type="candidate.quality.passed", payload={"pdd_id": pdd, "commit": commit})
                for pdd, commit in (("feature-a", "a" * 40), ("feature-a", "b" * 40),
                                    ("feature-b", "a" * 40), ("feature-b", "b" * 40))]
    rows.extend(receipts)
    shared = SimpleNamespace(events=lambda kind: [event for event in rows if event.type == kind])
    view = SharedKindWorker(shared, ZfEvent(type="workflow.invoke.requested", payload={
        "workflow_run_id": "run-a", "goal_id": "run-a",
    }), "prd")
    assert view.events("candidate.quality.passed") == receipts[:1]
    assert view.events("candidate.ready") == rows[:1]
