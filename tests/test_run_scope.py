from __future__ import annotations

from zf.core.events import ZfEvent
from zf.runtime.run_scope import events_for_run, resolve_run_id, run_aliases


def test_approved_run_anchor_outranks_pre_run_synthesis_namespace() -> None:
    run_id = "prd-e2e-closure"
    synthesis_id = "workflow-request:prd-e2e-closure:r2"
    events = [
        ZfEvent(
            type="workflow.operation.requested",
            correlation_id=run_id,
            payload={"workflow_run_id": synthesis_id},
        ),
        ZfEvent(
            type="run.goal.started",
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": synthesis_id,
                "objective": "Ship greeting",
            },
        ),
        ZfEvent(
            type="workflow.invoke.requested",
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": run_id,
            },
        ),
        ZfEvent(
            type="run.goal.completed",
            correlation_id=run_id,
            payload={
                "run_id": synthesis_id,
                "workflow_run_id": synthesis_id,
                "claim_id": "claim-1",
                "target_commit": "a" * 40,
            },
        ),
    ]

    aliases = run_aliases(events)

    assert aliases[run_id] == run_id
    assert aliases[synthesis_id] == run_id
    assert resolve_run_id(events, run_id) == run_id
    assert resolve_run_id(events, synthesis_id) == run_id
    assert events_for_run(events, run_id=run_id) == events


def test_pre_run_operation_keeps_legacy_identity_without_approved_anchor() -> None:
    request_id = "request-only"
    synthesis_id = "workflow-request:request-only:r1"
    events = [
        ZfEvent(
            type="workflow.operation.requested",
            correlation_id=request_id,
            payload={"workflow_run_id": synthesis_id},
        ),
    ]

    aliases = run_aliases(events)

    assert aliases[synthesis_id] == synthesis_id
    assert aliases[request_id] == synthesis_id


def test_conflicting_event_does_not_union_two_canonical_runs() -> None:
    events = [
        ZfEvent(
            type="run.goal.started",
            correlation_id="workflow-A",
            payload={"run_id": "workflow-A"},
        ),
        ZfEvent(
            type="run.goal.started",
            correlation_id="workflow-B",
            payload={"run_id": "workflow-B"},
        ),
        ZfEvent(
            type="goal.closure.synthesized",
            correlation_id="workflow-A",
            payload={"workflow_run_id": "workflow-B"},
        ),
    ]

    aliases = run_aliases(events)

    assert aliases["workflow-A"] == "workflow-A"
    assert aliases["workflow-B"] == "workflow-B"


def test_goal_projection_reuses_identity_mapping_without_cross_call_cache(monkeypatch) -> None:
    from zf.runtime import run_scope
    from zf.runtime.run_manager import build_run_goal_projection

    original = run_scope.run_aliases
    calls = []
    def count(rows):
        calls.append(True)
        return original(rows)
    monkeypatch.setattr(run_scope, "run_aliases", count)
    events = [ZfEvent(type="run.goal.started", correlation_id="run-a",
                      payload={"run_id": "run-a", "objective": "ship"})]
    result = build_run_goal_projection(events, run_id="run-a")
    assert result["run_id"] == "run-a"
    assert result["status"] == "active"
    assert len(calls) == 1
    events.append(ZfEvent(type="run.goal.completed", correlation_id="run-a",
                          payload={"run_id": "run-a"}))
    assert build_run_goal_projection(events, run_id="run-a")["status"] == "complete"
    assert len(calls) == 2
