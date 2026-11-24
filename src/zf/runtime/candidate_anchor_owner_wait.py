"""Recognize a frozen Candidate anchor waiting on its admitted diagnosis owner."""

from collections import defaultdict
from functools import cached_property

from zf.core.events import ZfEvent
from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
from zf.runtime.candidate_plan_recovery import candidate_recovery_superseded
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.run_continuation import human_decisions_block_action


class CandidateAnchorWaits:
    """Share the ledger indexes and pending recovery projection within one orphan sweep."""

    def __init__(self, events: list[ZfEvent]):
        self.events = events

    @cached_property
    def _kernel_events(self):
        by_run = defaultdict(list)
        for event in self.events:
            if event.origin == "kernel":
                run = event.payload.get("workflow_run_id") or event.payload.get("run_id") or event.correlation_id
                if run:
                    by_run[run].append(event)
        return by_run

    @cached_property
    def _gates(self):
        from zf.runtime.run_manager import _pending_human_decisions

        return _pending_human_decisions(self.events)

    @cached_property
    def _diagnoses(self):
        return pending_candidate_infrastructure_diagnoses(self.events)

    def holds(self, run_id: str, task_id: str) -> bool:
        if not run_id or not task_id:
            return False
        scoped = self._kernel_events.get(run_id, [])
        if any(e.type in {"run.goal.completed", "run.goal.failed", "run.aborted", "run.cancelled",
                          "run.failed", "run.completed"} for e in scoped):
            return False
        failure = next((e for e in reversed(scoped) if e.payload.get("pdd_id") == task_id and
                        e.type in {"test.failed", "verify.failed", "test.passed", "verify.passed"}), None)
        if not failure or failure.type not in {"test.failed", "verify.failed"} or failure.payload.get("failure_kind") != "infra":
            return False
        fanout = failure.payload.get("fanout_id")
        start = next((e for e in reversed(scoped) if e.type == "fanout.started"
                      and e.payload.get("pdd_id") == task_id
                      and e.payload.get("stage_id") == failure.payload.get("stage_id")), None)
        aggregate = next((e for e in scoped if e.id == failure.causation_id), None)
        candidate = next((e for e in reversed(scoped) if e.type == "candidate.ready"), None)
        if (not fanout or not failure.payload.get("stage_id")
                or not start or start.payload.get("fanout_id") != fanout
                or not aggregate or aggregate.type != "fanout.aggregate.completed"
                or aggregate.payload.get("fanout_id") != fanout or aggregate.payload.get("status") != "failed"
                or aggregate.payload.get("stage_id") != failure.payload["stage_id"]
                or aggregate.payload.get("pdd_id") != task_id or not candidate
                or candidate.payload.get("pdd_id") != task_id or start.payload.get("trigger_event_id") != candidate.id
                or not scoped.index(candidate) < scoped.index(start) < scoped.index(aggregate) < scoped.index(failure)
                or candidate_recovery_superseded(scoped, candidate, run_id)):
            return False
        try:
            frozen = frozen_candidate_for_payload(self.events, run_id, payload={"candidate_event_id": candidate.id})
        except (ValueError, KeyError):
            return False
        if (not frozen or frozen.origin != "kernel" or frozen.payload.get("status") != "frozen"
                or frozen.payload.get("pdd_id") != task_id or not frozen.payload.get("candidate_head_commit")
                or not frozen.payload.get("candidate_ref")
                or failure.payload.get("target_ref") != frozen.payload["candidate_ref"]
                or start.payload.get("target_ref") != frozen.payload["candidate_ref"]
                or any(not frozen.payload.get(key) or not all(e.payload.get(key) == frozen.payload[key]
                       for e in (failure, aggregate)) for key in ("task_map_generation", "plan_artifact_package_digest"))):
            return False

        # A bare orphan notice cannot perpetuate its own waiting condition.
        gate_ids = {e.id for e in scoped if e.type == "human.escalate" and e.actor == "run-manager"
                    and e.payload.get("checkpoint_id") and e.payload.get("decision_token")
                    and (e.payload.get("blocking_scope") == "run" or
                         (e.payload.get("blocking_scope") == "task" and e.payload.get("task_id") == task_id))}
        if not gate_ids:
            return False
        gates = [gate for gate in self._gates if gate["event_id"] in gate_ids]
        if not gates:
            return False
        return any(action.get("source_event_id") == failure.id and action.get("workflow_run_id") == run_id
                   and action.get("owner_route") == "run_manager" and action.get("action") == "diagnose-attention"
                   and human_decisions_block_action(gates, action, events=self.events)
                   for action in self._diagnoses)
