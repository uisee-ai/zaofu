"""Test the normal host tick after a bounded diagnostic reaches a quiescent parent."""

from datetime import datetime, timedelta, timezone
import time

import pytest

from tests.test_run_manager import _reflect_config, _state
from tests.test_run_manager_reflection import _wait
from tests.test_tick_services import _FakeOrchestrator
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.quiescent import quiescent_now
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.mark.parametrize(
    "case",
    [
        "match",
        "wrong_gate",
        "another_run",
        "other_receipt_run",
        "missing_source",
        "forged",
        "preserved",
        "cancelled",
        "delivered",
        "paused",
        "extra_gate",
        "cancel_run",
        "other_gate_correlation",
        "processed",
        "fake_receipt",
        "unmatched_receipt",
    ],
)
def test_quiescent_host_reconciles_only_the_failed_diagnosis_it_requested(tmp_path, monkeypatch, case):
    state, log, _ = _state(tmp_path)
    config = _reflect_config()
    config.goal.enabled = config.goal.quiescent_after_escalate = True
    config.runtime.run_manager.resident_agent.enabled = False
    orch = _FakeOrchestrator(state, config)
    writer = EventWriter(log)
    orch.event_writer = EventWriter(log, default_origin="kernel")
    run = "business"

    def emit(kind, *, when=0, actor="orchestrator", origin="kernel", correlation="", **fields):
        return writer.append(
            ZfEvent(
                type=kind,
                origin=origin,
                actor=actor,
                ts=(datetime.now(timezone.utc) - timedelta(minutes=30) + timedelta(seconds=when)).isoformat(),
                correlation_id=correlation,
                payload=fields,
            )
        )

    emit("run.goal.started", workflow_run_id=run, run_id=run)
    gate = emit(
        "human.escalate",
        when=1,
        origin="",
        reason="owner decision required",
        correlation="another" if case == "other_gate_correlation" else "",
        **({"workflow_run_id": "other"} if case == "another_run" else {}),
    )
    if case == "extra_gate":
        emit("human.escalate", when=2, origin="", reason="unrelated owner confirmation")
    request = emit(
        "run.manager.autoresearch.requested",
        when=3,
        actor="run-manager",
        request_id="rmar-1",
        origin="worker" if case == "forged" else "kernel",
        workflow_run_id=run,
        source_event_ids=[] if case == "missing_source" else ["wrong"] if case == "wrong_gate" else [gate.id],
    )
    emit("autoresearch.loop.requested", when=4, loop_request_id="rmar-1", workflow_run_id=run)
    if case == "delivered":
        emit("run.goal.completed", when=5, workflow_run_id=run, run_id=run)
    if case == "cancel_run":
        emit("run.cancelled", when=5, workflow_run_id=run, run_id=run)
    if case == "paused":
        emit("run.paused", when=5, workflow_run_id=run, run_id=run)
    problem = emit(
        "autoresearch.loop.failed",
        when=6,
        actor="other" if case == "fake_receipt" else "zf-autoresearch-resident",
        origin="",
        correlation="another" if case == "other_receipt_run" else run,
        loop_request_id="unrelated" if case == "unmatched_receipt" else "rmar-1",
        diagnosis={
            "status": case if case in {"preserved", "cancelled"} else "incomplete",
            "failure_class": "diagnosis_timeout",
        },
    )
    if case == "processed":
        from zf.runtime.autoresearch_failure_handoff import pending_autoresearch_followups

        (action,) = pending_autoresearch_followups(log.read_all())
        emit(
            "run.manager.action.blocked",
            when=7,
            actor="run-manager",
            checkpoint_id=action["checkpoint_id"],
            workflow_run_id=run,
            source_event_ids=action["source_event_ids"],
        )
    # Warm the existing gate and its cursor. A service restart retains the ledger.
    if case not in {"delivered", "cancel_run"}:
        assert quiescent_now(log.read_all(), config=config, now_epoch=time.time()).quiescent
    projection = build_run_manager_projection(
        state, events=log.read_all(), config=config, project_root=tmp_path
    )
    selected = projection["continuation"]["next_operation"] or {}
    if case == "match":
        assert selected.get("action") == "diagnose-attention"
    calls = []

    def decide(prompt, **kwargs):
        calls.append(1)
        return {"verdict": "best_so_far", "recommended_route": "wait"}

    def manager(**kwargs):
        kwargs.pop("orchestrator")
        return run_manager_tick(
            **kwargs,
            auto_execute=True,
            spawn_repairs=False,
            action_filter={"diagnose-attention"},
            reflect_fn=decide,
        )

    monkeypatch.setattr("zf.runtime.tick_services._run_run_manager", manager)
    service_state = TickServiceState()
    run_standard_tick_services(orch, state=service_state, now=time.monotonic())
    if case == "match":
        _wait(lambda: any(e.type == "run.manager.reflect.completed" for e in log.read_all()))
    run_standard_tick_services(orch, state=service_state, now=time.monotonic() + 3600)
    run_standard_tick_services(orch, state=service_state, now=time.monotonic() + 7200)
    es = log.read_all()
    reflects = [e for e in es if e.type == "run.manager.reflect.requested"]
    if case == "match":
        assert len(reflects) == 1 and calls == [1]
        assert {request.id, problem.id} <= set(reflects[0].payload["source_event_ids"])
        (reflected,) = [e for e in es if e.type == "run.manager.reflect.completed"]
        (blocked,) = [e for e in es if e.type == "run.manager.action.blocked"]
        assert blocked.causation_id == reflected.id and "recommended waiting" in blocked.payload["reason"]
    else:
        assert not reflects and not calls
    assert not any(
        e.type in {"plan.approved", "task.pipeline.stage.dispatched", "human.escalation.acknowledged"}
        for e in es
    )
