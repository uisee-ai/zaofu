"""Judge completion is not evidence that a specification was updated."""
import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.spec_promote import decide_promotion
from tests.test_workflow_graph_runtime import (
    _GraphBridgeReactor,
    _graph_review_test_judge_config,
)


class _SpecPromotionReactor(_GraphBridgeReactor):
    # Keep the graph fixture's accepted terminal gate, but exercise the real callback.
    _emit_spec_promote_decision = EventReactorMixin._emit_spec_promote_decision


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("tiers", [[], ["runtime"]])
def test_judge_terminal_does_not_claim_specification_update(tmp_path, exists, tiers):
    spec = tmp_path / "spec.md"
    if exists:
        spec.write_text("Original approved intent\n")
    reactor = _SpecPromotionReactor(
        cfg=_graph_review_test_judge_config(), state_dir=tmp_path,
    )
    reactor.task_store.add(Task(
        id="TASK-1", title="deliver feature", status="testing", assigned_to="judge",
        contract=TaskContract(spec_ref=str(spec), verification_tiers=tiers),
    ))
    trigger = reactor.event_writer.append(ZfEvent(
        type="judge.passed", task_id="TASK-1", correlation_id="review-run",
        # A role's extra assertion cannot introduce a promotion protocol.
        payload={"spec_promoted": True},
    ))

    decision = reactor._on_judge_passed(trigger)

    assert decision.action == "move"
    assert reactor.task_store.get("TASK-1").status == "done"
    events = reactor.event_log.read_all()
    assert not any(e.type == "spec.promote.completed" for e in events)
    [skipped] = [e for e in events if e.type == "spec.promote.skipped"]
    assert skipped.payload["reason"] == "promotion_not_verified"
    assert skipped.payload["source_event_id"] == trigger.id
    assert skipped.causation_id == trigger.id
    assert skipped.correlation_id == trigger.correlation_id
    assert spec.exists() == exists
    if exists:
        assert spec.read_text() == "Original approved intent\n"


def test_specification_reference_and_acceptance_are_not_promotion_evidence():
    decision = decide_promotion(
        task_id="T", spec_ref="docs/spec.md", has_acceptance_evidence=True,
    )
    assert not decision.is_promotion
    assert decision.reason == "promotion_not_verified"
