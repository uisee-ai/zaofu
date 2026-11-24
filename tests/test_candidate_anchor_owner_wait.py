"""A scoped owner wait protects its anchor until the orphan clock can run again."""
from dataclasses import replace

import pytest

from tests.test_orphan_timeout import config, state_dir, transport
from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.candidate_anchor_owner_wait import CandidateAnchorWaits
from zf.runtime.orchestrator import Orchestrator


def _history():
    def event(kind, event_id, *, actor="kernel", cause=None, task=None, **body):
        return ZfEvent(type=kind, id=event_id, origin="kernel", actor=actor, task_id=task,
                       correlation_id="R1", causation_id=cause, payload={"workflow_run_id": "R1", **body})

    binding = {"pdd_id": "root", "task_map_generation": "generation", "plan_artifact_package_digest": "digest"}
    lane = {"fanout_id": "fanout", "stage_id": "verify", "pdd_id": "root"}
    gate = event("human.escalate", "gate", actor="run-manager", blocking_scope="run",
                 checkpoint_id="owner-checkpoint", decision_token="owner-test")
    freeze = event("candidate.ready", "freeze", **binding, status="frozen", freeze_id="f1",
                   schema_version="candidate-freeze-receipt.v1", candidate_ref="refs/heads/candidate/root",
                   candidate_head_commit="a" * 40)
    start = event("fanout.started", "start", **lane, task="root", trigger_event_id="freeze",
                  target_ref=freeze.payload["candidate_ref"])
    aggregate = event("fanout.aggregate.completed", "aggregate", **{**binding, **lane}, status="failed")
    failure = event("test.failed", "failure", cause="aggregate", **{**binding, **lane}, failure_scope="candidate",
                    target_ref=freeze.payload["candidate_ref"], failure_kind="infra")
    return [gate, freeze, start, aggregate, failure]


def test_gated_candidate_anchor_holds_only_its_own_run_task_and_source():
    events = _history()
    assert CandidateAnchorWaits(events).holds("R1", "root")
    assert not CandidateAnchorWaits(events).holds("R2", "root")
    assert not CandidateAnchorWaits(events).holds("R1", "unrelated")


@pytest.mark.parametrize("fault", ["side", "forged_gate", "different_gate_run", "different_gate_task",
    "missing_gate", "missing_checkpoint", "orphan_only", "no_digest", "wrong_generation", "wrong_aggregate",
    "untrusted_failure", "different_target", "new_plan", "new_candidate", "new_fanout", "terminal", "released"])
def test_stale_unbound_or_unowned_failure_is_orphan_eligible(fault):
    gate, freeze, start, aggregate, failure = _history()
    events = [gate, freeze, start, aggregate, failure]
    if fault == "side":
        events[0] = replace(gate, payload={**gate.payload, "blocking_scope": "side"})
    elif fault == "forged_gate":
        events[0] = replace(gate, origin="worker")
    elif fault == "different_gate_run":
        events[0] = replace(gate, payload={**gate.payload, "workflow_run_id": "other"})
    elif fault == "different_gate_task":
        events[0] = replace(gate, payload={**gate.payload, "blocking_scope": "task", "task_id": "other"})
    elif fault == "missing_gate":
        events.pop(0)
    elif fault == "missing_checkpoint":
        events[0] = replace(gate, payload={**gate.payload, "checkpoint_id": ""})
    elif fault == "orphan_only":
        events[0] = replace(gate, actor="orchestrator", payload={"failure_class": "orphaned_task"})
    elif fault == "no_digest":
        events[3] = replace(aggregate, payload={**aggregate.payload, "plan_artifact_package_digest": ""})
    elif fault == "wrong_generation":
        events[-1] = replace(failure, payload={**failure.payload, "task_map_generation": "other"})
    elif fault == "wrong_aggregate":
        events[-1] = replace(failure, causation_id="absent")
    elif fault == "untrusted_failure":
        events[-1] = replace(failure, origin="worker")
    elif fault == "different_target":
        events[2] = replace(start, payload={**start.payload, "target_ref": "other"})
    elif fault == "new_plan":
        events.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel", correlation_id="R1"))
    elif fault == "new_candidate":
        events.append(replace(freeze, id="new-freeze"))
    elif fault == "new_fanout":
        events.append(replace(start, id="new-start", payload={**start.payload, "fanout_id": "replacement"}))
    elif fault == "terminal":
        events.append(ZfEvent(type="run.completed", origin="kernel", correlation_id="R1"))
    elif fault == "released":
        events.append(ZfEvent(type="run.manager.human_decision.applied", origin="kernel",
                              payload={"decision_token": gate.payload["decision_token"]}))
    assert not CandidateAnchorWaits(events).holds("R1", "root")


def test_sweep_releases_wait_with_a_full_clock_then_escalates_the_true_orphan(state_dir, config, transport):
    config.runtime.run_manager.resident_agent.enabled = True
    runtime = Orchestrator(state_dir, config, transport)
    runtime.task_store.add(Task(id="root", title="qualification", status="in_progress"))
    events = _history()
    for event in events:
        runtime.event_log.append(event)
    runtime._dispatch_epoch["root"] = 1
    now = 2000
    runtime._now = lambda: now
    runtime._check_orphaned_tasks()
    assert runtime.task_store.get("root").status == "in_progress"
    assert runtime._orphan_owner_waits == {"root"}
    runtime.event_log.append(ZfEvent(type="run.manager.human_decision.applied", origin="kernel",
        payload={"decision_token": events[0].payload["decision_token"]}))
    now += 3600
    runtime._check_orphaned_tasks()
    assert not runtime._orphan_owner_waits
    assert runtime._dispatch_epoch["root"] == now
    assert runtime.task_store.get("root").status == "in_progress"
    now += 1799
    runtime._check_orphaned_tasks()
    assert runtime.task_store.get("root").status == "in_progress"
    now += 2
    runtime._check_orphaned_tasks()
    assert runtime.task_store.get("root").status == "backlog"
    assert [e.task_id for e in runtime.event_log.read_all() if e.type == "task.orphaned"] == ["root"]


def test_sweep_shares_global_recovery_reductions_between_runs(monkeypatch):
    from unittest.mock import Mock
    from zf.runtime import candidate_anchor_owner_wait, run_manager

    first = _history()
    second = [replace(e, id=e.id + "-second", correlation_id="R2", payload={
        **e.payload, "workflow_run_id": "R2",
        **({"pdd_id": "second-root"} if "pdd_id" in e.payload else {}),
        **({"fanout_id": "fanout-second"} if "fanout_id" in e.payload else {}),
        **({"target_ref": "refs/heads/candidate/second-root"} if "target_ref" in e.payload else {}),
        **({"candidate_ref": "refs/heads/candidate/second-root"} if "candidate_ref" in e.payload else {}),
        **({"trigger_event_id": "freeze-second"} if e.type == "fanout.started" else {}),
        **({"decision_token": "second-owner", "checkpoint_id": "second-decision"} if e.type == "human.escalate" else {}),
    }, causation_id=e.causation_id + "-second" if e.causation_id else None) for e in first]
    diagnoses = Mock(wraps=candidate_anchor_owner_wait.pending_candidate_infrastructure_diagnoses)
    gates = Mock(wraps=run_manager._pending_human_decisions)
    monkeypatch.setattr(candidate_anchor_owner_wait, "pending_candidate_infrastructure_diagnoses", diagnoses)
    monkeypatch.setattr(run_manager, "_pending_human_decisions", gates)
    waits = CandidateAnchorWaits([*first[1:], *second[1:], first[0], second[0]])
    assert waits.holds("R1", "root") and waits.holds("R2", "second-root")
    assert diagnoses.call_count == gates.call_count == 1


def test_closed_gate_before_restart_does_not_spend_old_dispatch_time(state_dir, config, transport):
    config.runtime.run_manager.resident_agent.enabled = True
    runtime = Orchestrator(state_dir, config, transport)
    runtime.task_store.add(Task(id="root", title="qualification", status="in_progress"))
    for e in _history():
        runtime.event_log.append(e)
    runtime._dispatch_epoch["root"] = 1
    runtime._now = lambda: 4000
    runtime._check_orphaned_tasks()
    assert "root" in runtime._orphan_owner_waits
    runtime.event_log.append(ZfEvent(type="run.manager.human_decision.applied", origin="kernel",
        payload={"decision_token": "owner-test"}))
    restarted = Orchestrator(state_dir, config, transport)
    assert restarted._orphan_owner_waits == set()
    now = 10000
    restarted._now = lambda: now
    restarted._check_orphaned_tasks()
    assert restarted.task_store.get("root").status == "in_progress"
    assert "root" not in restarted._dispatch_epoch
    restarted._apply_housekeeping(ZfEvent(type="dev.build.done", task_id="root", actor="dev"))
    assert restarted._dispatch_epoch["root"] == now
