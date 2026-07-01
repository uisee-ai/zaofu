"""Run-level recovery owner projections and tick actions.

Run Manager is a thin owner over existing deterministic actuators. It derives
state from events/projections, asks ``ControlledActionService`` to mutate truth,
and records post-verification events. It does not introduce a second queue or
write kernel-managed stores directly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TaskStore
from zf.runtime.repair_dispatch import pending_repair_dispatches
from zf.runtime.pane_probe import build_runtime_pane_probe
from zf.runtime.problem_taxonomy import problem_envelope_from_action
from zf.runtime.run_manager_advisor import build_replan_advisor_projection
from zf.runtime.run_manager_reports import (
    build_regression_backlog_candidates,
    build_retrospective_markdown,
)
from zf.runtime.run_manager_router import (
    ACTION_POLICIES,
    SAFE_BATCH_ACTIONS,
    build_no_progress_projection,
    classify_recovery_context as router_classify_recovery_context,
    decide_action_policy as router_decide_action_policy,
    expected_downstream_events as router_expected_downstream_events,
    preflight_action,
)
from zf.runtime.run_manager_wait_hint import (
    build_resident_repair_policy_projection,
    build_wait_hint_projection,
)
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.workflow_resume import build_workflow_resume_projection


RUN_MANAGER_SCHEMA_VERSION = "run-manager.v1"
RUN_CONTEXT_BUNDLE_SCHEMA_VERSION = "run-context-bundle.v1"
RUN_MANAGER_CONTEXT_SIDECAR_SCHEMA_VERSION = "run-manager.context-bundle.v1"
RUN_MANAGER_READ_SET_SCHEMA_VERSION = "run-manager.read-set.v1"
RUN_GOAL_SCHEMA_VERSION = "run-goal.v1"
REPAIR_LEDGER_SCHEMA_VERSION = "repair-ledger.v1"
REPAIR_MERGE_QUEUE_SCHEMA_VERSION = "repair-merge-queue.v1"
RUN_COMPLETION_PROFILE_SCHEMA_VERSION = "run-completion-profile.v1"
RUN_STATUS_EXPLAIN_SCHEMA_VERSION = "run-status-explain.v1"
RUN_MANAGER_REPAIR_ACCEPTED = "run.manager.repair.accepted"
RUN_MANAGER_REPAIR_REJECTED = "run.manager.repair.rejected"
RUN_MANAGER_REPAIR_BLOCKED = "run.manager.repair.blocked"
RUN_MANAGER_REPAIR_MERGE_QUEUED = "run.manager.repair.merge.queued"
RUN_MANAGER_REPAIR_MERGE_MERGING = "run.manager.repair.merge.merging"
RUN_MANAGER_REPAIR_MERGE_MERGED = "run.manager.repair.merge.merged"
RUN_MANAGER_REPAIR_MERGE_NEEDS_REVIEW = "run.manager.repair.merge.needs_review"
RUN_MANAGER_REPAIR_MERGE_DISCARDED = "run.manager.repair.merge.discarded"
RUN_MANAGER_AUTORESEARCH_REQUESTED = "run.manager.autoresearch.requested"
RUN_MANAGER_AUTORESEARCH_CONSUMED = "run.manager.autoresearch.consumed"
RUN_MANAGER_REFLECT_REQUESTED = "run.manager.reflect.requested"
RUN_MANAGER_REFLECT_COMPLETED = "run.manager.reflect.completed"
RUN_MANAGER_HUMAN_DECISION_APPLIED = "run.manager.human_decision.applied"
RUN_MANAGER_HUMAN_DECISION_REJECTED = "run.manager.human_decision.rejected"
RUN_MANAGER_ACTION_PLANNED = "run.manager.action.planned"
RUN_MANAGER_ACTION_APPLIED = "run.manager.action.applied"
RUN_MANAGER_ACTION_BLOCKED = "run.manager.action.blocked"
RUN_MANAGER_ACTION_FAILED = "run.manager.action.failed"
RUN_MANAGER_ACTION_VERIFY_PASSED = "run.manager.action.verify.passed"
RUN_MANAGER_ACTION_VERIFY_FAILED = "run.manager.action.verify.failed"
RUN_COMPLETED = "run.completed"
RUN_MANAGER_TICK_STARTED = "run.manager.tick.started"
RUN_MANAGER_TICK_COMPLETED = "run.manager.tick.completed"
RUN_MANAGER_TRANSITION = "run.manager.transition"
RUN_MANAGER_RESIDENT_SPAWNED = "run.manager.resident.spawned"
RUN_MANAGER_RESIDENT_PROMPTED = "run.manager.resident.prompted"
RUN_MANAGER_AGENT_OBSERVATION = "run.manager.agent.observation"
RUN_MANAGER_AGENT_RECOMMENDATION = "run.manager.agent.recommendation"
RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED = "run.manager.agent.recommendation.consumed"
HUMAN_ESCALATION_SENT = "human.escalation.sent"
HUMAN_ESCALATION_FAILED = "human.escalation.failed"
HUMAN_ESCALATION_ACKNOWLEDGED = "human.escalation.acknowledged"
_TERMINAL_SIGNAL_EVENTS = (
    "run.goal.completed",
    RUN_COMPLETED,
    "ship.completed",
    "ship.done",
    "judge.passed",
    "task.done",
)
_POST_TERMINAL_WORK_EVENTS = frozenset({
    "run.goal.started",
    "task_map.ready",
    "task_map.amended",
    "gap_plan.ready",
    "module.parity.gap_plan.ready",
    "cangjie.module.parity.scan.completed",
    "task.assigned",
    "task.dispatched",
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.child.completed",
    "fanout.child.failed",
    "fanout.aggregate.completed",
    "candidate.ready",
    "candidate.quality.failed",
    "integration.failed",
    "workflow.resume.applied",
    "dev.build.done",
    "dev.failed",
    "dev.blocked",
    "task.ref.updated",
    "task.ref.rejected",
})

_RUN_MANAGER_REPAIR_TERMINALS = {
    RUN_MANAGER_REPAIR_ACCEPTED,
    RUN_MANAGER_REPAIR_REJECTED,
    RUN_MANAGER_REPAIR_BLOCKED,
}
_SAFE_BATCH_ACTIONS = set(SAFE_BATCH_ACTIONS)
_ACTION_POLICIES = set(ACTION_POLICIES)


@dataclass(frozen=True)
class RunManagerTickResult:
    projection_written: bool = False
    actions_applied: int = 0
    actions_blocked: int = 0
    actions_failed: int = 0
    repairs_accepted: int = 0
    repairs_dispatched: int = 0
    repair_closeouts: int = 0
    autoresearch_consumed: int = 0
    autoresearch_requested: int = 0
    reflect_requested: int = 0
    reflect_completed: int = 0
    agent_recommendations_consumed: int = 0
    human_decisions_applied: int = 0
    human_decisions_rejected: int = 0
    closeout_events: int = 0

    @property
    def changed(self) -> bool:
        return any((
            self.projection_written,
            self.actions_applied,
            self.actions_blocked,
            self.actions_failed,
            self.repairs_accepted,
            self.repairs_dispatched,
            self.repair_closeouts,
            self.autoresearch_consumed,
            self.autoresearch_requested,
            self.reflect_requested,
            self.reflect_completed,
            self.agent_recommendations_consumed,
            self.human_decisions_applied,
            self.human_decisions_rejected,
            self.closeout_events,
        ))


def run_manager_tick(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    event_log: EventLog | None = None,
    auto_execute: bool = True,
    action_filter: set[str] | None = None,
    spawn_repairs: bool = True,
    repair_backend: str = "",
    reflect_fn: Callable[..., Any] | None = None,
) -> RunManagerTickResult:
    """Run one bounded Run Manager observe/decide/act/verify tick."""

    state_dir = Path(state_dir)
    config = config or ZfConfig()
    event_log = event_log or writer.event_log
    events = _read_events(state_dir, event_log=event_log)
    started = writer.emit(
        RUN_MANAGER_TICK_STARTED,
        actor="run-manager",
        payload={
            "schema_version": "run-manager.tick.v1",
            "state_dir": str(state_dir),
            "auto_execute": auto_execute,
        },
    )

    repairs_accepted = _accept_autoresearch_repairs(events, writer)
    if repairs_accepted:
        events = _read_events(state_dir, event_log=event_log)
    autoresearch_consumed = _consume_autoresearch_results(events, writer)
    if autoresearch_consumed:
        events = _read_events(state_dir, event_log=event_log)
    human_decisions_applied, human_decisions_rejected = _consume_human_decisions(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project_root,
        events=events,
        causation_id=started.id,
    )
    if human_decisions_applied or human_decisions_rejected:
        events = _read_events(state_dir, event_log=event_log)
    agent_recommendations_consumed, agent_recommendation_autoresearch, agent_recommendation_reflects, agent_recommendation_repairs = (
        _consume_agent_recommendations(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            events=events,
            causation_id=started.id,
            reflect_fn=reflect_fn,
        )
    )
    repairs_accepted += agent_recommendation_repairs
    if agent_recommendations_consumed:
        events = _read_events(state_dir, event_log=event_log)

    repairs_dispatched = 0
    repair_closeouts = 0
    try:
        from zf.runtime.self_repair_runner import (
            dispatch_pending_self_repairs,
            emit_self_repair_closeouts,
        )

        repairs_dispatched = dispatch_pending_self_repairs(
            events,
            writer,
            request_types=(RUN_MANAGER_REPAIR_ACCEPTED,),
            dispatch_actor="run-manager",
            spawn=spawn_repairs,
            backend=repair_backend,
        )
        if repairs_dispatched:
            events = _read_events(state_dir, event_log=event_log)
        repair_closeouts = emit_self_repair_closeouts(events, writer)
        if repair_closeouts:
            events = _read_events(state_dir, event_log=event_log)
    except Exception as exc:
        writer.emit(
            RUN_MANAGER_REPAIR_BLOCKED,
            actor="run-manager",
            causation_id=started.id,
            payload={
                "schema_version": "run-manager.repair.v1",
                "reason": "repair_executor_failed",
                "error": str(exc),
            },
        )

    projection = build_run_manager_projection(
        state_dir,
        events=_read_events(state_dir, event_log=event_log),
        config=config,
        project_root=project_root,
    )
    closeout_events = _emit_run_completed_closeout_if_ready(
        writer,
        projection=projection,
        events=_read_events(state_dir, event_log=event_log),
        config=config,
        causation_id=started.id,
    )
    if closeout_events:
        projection = build_run_manager_projection(
            state_dir,
            events=_read_events(state_dir, event_log=event_log),
            config=config,
            project_root=project_root,
        )
    write_run_manager_projections(state_dir, projection)

    actions_applied = 0
    actions_blocked = 0
    actions_failed = 0
    autoresearch_requested = agent_recommendation_autoresearch
    reflect_requested = agent_recommendation_reflects
    reflect_completed = agent_recommendation_reflects
    applied_safe_actions: list[str] = []
    if auto_execute:
        for action in projection.get("pending_actions", []):
            if not isinstance(action, dict):
                continue
            if _action_seen(events, action):
                continue
            if action_filter is not None and str(action.get("action") or "") not in action_filter:
                continue
            decision = action.get("policy_decision")
            decision = decision if isinstance(decision, dict) else {}
            if decision.get("decision") == "auto_decide":
                status = _execute_controlled_run_action(
                    state_dir=state_dir,
                    writer=writer,
                    config=config,
                    project_root=project_root,
                    action=action,
                    causation_id=started.id,
                )
                if status == "applied":
                    actions_applied += 1
                    applied_safe_actions.append(str(action.get("safe_resume_action") or ""))
                elif status == "blocked":
                    actions_blocked += 1
                else:
                    actions_failed += 1
                events = _read_events(state_dir, event_log=event_log)
            elif decision.get("decision") == "needs_diagnosis":
                diagnosis_satisfied = False
                resident_action = _resident_diagnosis_reprompt_action(
                    action,
                    projection.get("resident_agent")
                    if isinstance(projection.get("resident_agent"), dict)
                    else {},
                )
                if resident_action is not None:
                    status = _execute_resident_agent_reprompt(
                        state_dir=state_dir,
                        writer=writer,
                        config=config,
                        project_root=project_root,
                        action=resident_action,
                        causation_id=started.id,
                    )
                    if status == "applied":
                        actions_applied += 1
                        applied_safe_actions.append("resident_agent_reprompt")
                    elif status == "blocked":
                        actions_blocked += 1
                    else:
                        actions_failed += 1
                    events = _read_events(state_dir, event_log=event_log)
                    if status != "applied":
                        reflected = _maybe_invoke_run_manager_reflect(
                            state_dir=state_dir,
                            writer=writer,
                            config=config,
                            project_root=project_root,
                            action=action,
                            projection=projection,
                            causation_id=started.id,
                            reflect_fn=reflect_fn,
                        )
                        if reflected:
                            reflect_requested += 1
                            reflect_completed += 1
                            events = _read_events(state_dir, event_log=event_log)
                else:
                    reflected = _maybe_invoke_run_manager_reflect(
                        state_dir=state_dir,
                        writer=writer,
                        config=config,
                        project_root=project_root,
                        action=action,
                        projection=projection,
                        causation_id=started.id,
                        reflect_fn=reflect_fn,
                    )
                    if reflected:
                        reflect_requested += 1
                        reflect_completed += 1
                        events = _read_events(state_dir, event_log=event_log)
                if not diagnosis_satisfied and _emit_autoresearch_request(
                    state_dir=state_dir,
                    writer=writer,
                    action=action,
                    projection=projection,
                    project_root=project_root,
                    causation_id=started.id,
                ):
                    autoresearch_requested += 1
                    events = _read_events(state_dir, event_log=event_log)
            elif decision.get("decision") in {"human_escalate", "needs_approval"}:
                _emit_blocked_action(
                    writer,
                    action,
                    causation_id=started.id,
                    reason=str(decision.get("reason") or "action requires operator"),
                    human=True,
                )
                actions_blocked += 1
                events = _read_events(state_dir, event_log=event_log)

    transition = _emit_tick_transition(
        writer,
        projection=projection,
        causation_id=started.id,
        actions_applied=actions_applied,
        actions_blocked=actions_blocked,
        actions_failed=actions_failed,
        repairs_dispatched=repairs_dispatched,
        repair_closeouts=repair_closeouts,
        autoresearch_requested=autoresearch_requested,
        reflect_requested=reflect_requested,
        reflect_completed=reflect_completed,
        autoresearch_consumed=autoresearch_consumed,
        applied_safe_actions=applied_safe_actions,
        closeout_events=closeout_events,
    )
    transition_name = (
        str(transition.payload.get("transition") or "")
        if transition is not None else "continue_waiting"
    )
    completed = writer.emit(
        RUN_MANAGER_TICK_COMPLETED,
        actor="run-manager",
        causation_id=started.id,
        payload={
            "schema_version": "run-manager.tick.v1",
            "projection_ref": "projections/run_manager.json",
            "actions_applied": actions_applied,
            "actions_blocked": actions_blocked,
            "actions_failed": actions_failed,
            "repairs_accepted": repairs_accepted,
            "repairs_dispatched": repairs_dispatched,
            "repair_closeouts": repair_closeouts,
            "autoresearch_consumed": autoresearch_consumed,
            "autoresearch_requested": autoresearch_requested,
            "reflect_requested": reflect_requested,
            "reflect_completed": reflect_completed,
            "agent_recommendations_consumed": agent_recommendations_consumed,
            "human_decisions_applied": human_decisions_applied,
            "human_decisions_rejected": human_decisions_rejected,
            "closeout_events": closeout_events,
            "transition_event_id": transition.id if transition is not None else "",
            "transition_event_written": transition is not None,
            "transition": transition_name,
        },
    )
    projection = build_run_manager_projection(
        state_dir,
        events=_read_events(state_dir, event_log=event_log),
        config=config,
        project_root=project_root,
    )
    projection["last_tick_event_id"] = completed.id
    projection["last_tick_summary"] = {
        "schema_version": "run-manager.tick-summary.v1",
        "started_event_id": started.id,
        "completed_event_id": completed.id,
        "transition_event_id": transition.id if transition is not None else "",
        "transition_event_written": transition is not None,
        "transition": transition_name,
        "projection_ref": "projections/run_manager.json",
        "actions_applied": actions_applied,
        "actions_blocked": actions_blocked,
        "actions_failed": actions_failed,
        "repairs_accepted": repairs_accepted,
        "repairs_dispatched": repairs_dispatched,
        "repair_closeouts": repair_closeouts,
        "autoresearch_consumed": autoresearch_consumed,
        "autoresearch_requested": autoresearch_requested,
        "reflect_requested": reflect_requested,
        "reflect_completed": reflect_completed,
        "agent_recommendations_consumed": agent_recommendations_consumed,
        "human_decisions_applied": human_decisions_applied,
        "human_decisions_rejected": human_decisions_rejected,
        "closeout_events": closeout_events,
    }
    write_run_manager_projections(state_dir, projection)
    return RunManagerTickResult(
        projection_written=True,
        actions_applied=actions_applied,
        actions_blocked=actions_blocked,
        actions_failed=actions_failed,
        repairs_accepted=repairs_accepted,
        repairs_dispatched=repairs_dispatched,
        repair_closeouts=repair_closeouts,
        autoresearch_consumed=autoresearch_consumed,
        autoresearch_requested=autoresearch_requested,
        reflect_requested=reflect_requested,
        reflect_completed=reflect_completed,
        agent_recommendations_consumed=agent_recommendations_consumed,
        human_decisions_applied=human_decisions_applied,
        human_decisions_rejected=human_decisions_rejected,
        closeout_events=closeout_events,
    )


def build_run_manager_projection(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    config = config or ZfConfig()
    events = list(events) if events is not None else _read_events(state_dir)
    goal = build_run_goal_projection(events)
    ledger = build_repair_ledger(events)
    merge_queue = build_repair_merge_queue(events)
    no_progress = build_no_progress_projection(events)
    completion_profile = build_run_completion_profile(
        events,
        goal=goal,
        repair_ledger=ledger,
        repair_merge_queue=merge_queue,
    )
    context = build_run_context_bundle(state_dir, events, project_root=project_root)
    workflow_resume = _workflow_resume_projection(state_dir, config, events)
    workflow_actions = [
        *_pending_workflow_task_actions(workflow_resume, events),
        *_pending_workflow_batch_actions(workflow_resume, events),
    ]
    worker_actions = _pending_worker_lifecycle_actions(
        state_dir,
        workflow_resume,
        events,
    )
    resident_agent = build_resident_agent_projection(events, config=config)
    resident_actions = _pending_resident_agent_actions(resident_agent, events)
    repair_validation_actions = _pending_repair_validation_actions(merge_queue, events)
    candidate_actions = [
        action for action in _pending_candidate_rework_actions(
            state_dir,
            config,
            events,
            project_root=project_root,
        )
        if not _candidate_rework_shadowed_by_workflow(action, workflow_actions)
    ]
    human_gate_actions = []
    candidate_human_only = _candidate_actions_are_human_escalations(candidate_actions)
    if not workflow_actions and (not candidate_actions or candidate_human_only):
        human_gate_actions = _pending_human_gate_repair_resume_actions(
            state_dir,
            events,
            resident_agent=resident_agent,
        )
        if human_gate_actions and candidate_human_only:
            candidate_actions = []
    attention_actions = []
    if (
        not workflow_actions
        and not candidate_actions
        and not human_gate_actions
        and completion_profile.get("status") != "complete"
    ):
        attention_actions = _pending_attention_diagnostic_actions(
            state_dir,
            events,
            resident_agent=resident_agent,
        )
    runtime_pane_snapshot = _safe_runtime_pane_snapshot(
        state_dir,
        config=config,
        project_root=project_root,
    )
    base_pending_actions = [
        *workflow_actions,
        *worker_actions,
        *resident_actions,
        *repair_validation_actions,
        *candidate_actions,
        *human_gate_actions,
        *attention_actions,
    ]
    unknown_gap_actions = []
    if (
        not base_pending_actions
        and completion_profile.get("status") != "complete"
    ):
        unknown_gap_actions = _pending_unknown_gap_diagnostic_actions(
            state_dir,
            events,
            no_progress=no_progress,
            runtime_pane_snapshot=runtime_pane_snapshot,
            resident_agent=resident_agent,
        )
    pending_actions = [
        *base_pending_actions,
        *unknown_gap_actions,
    ]
    pending_actions = [
        _action_with_problem_envelope(action)
        for action in pending_actions
    ]
    monitor = build_run_monitor_projection(
        state_dir,
        events=events,
        pending_actions=pending_actions,
        completion_profile=completion_profile,
    )
    timeline = build_run_manager_timeline(events)
    advisor = build_replan_advisor_projection(
        events,
        no_progress=no_progress,
        completion_profile=completion_profile,
        repair_ledger=ledger,
    )
    wait_hints = build_wait_hint_projection(
        monitor=monitor,
        completion_profile=completion_profile,
        repair_merge_queue=merge_queue,
        no_progress=no_progress,
    )
    status_explain = build_run_status_explain_projection(
        state_dir,
        events=events,
        goal=goal,
        completion_profile=completion_profile,
        monitor=monitor,
        pending_actions=pending_actions,
        no_progress=no_progress,
        repair_merge_queue=merge_queue,
        wait_hints=wait_hints,
    )
    retrospective_md = build_retrospective_markdown(projection={
        "summary": {
            "goal_status": goal.get("status", "unknown"),
            "completion_status": completion_profile.get("status", "unknown"),
            "pending_actions": len(pending_actions),
            "blocked_actions": sum(
                1 for item in pending_actions
                if (item.get("policy_decision") or {}).get("decision")
                in {"human_escalate", "needs_approval", "safe_halt"}
            ),
        },
        "monitor": monitor,
        "timeline": timeline,
        "advisor": advisor,
    })
    report_artifacts = {
        "schema_version": "run-manager.report-artifacts.v1",
        "is_derived_projection": True,
        "retrospective_markdown": retrospective_md,
        "backlog_candidates": build_regression_backlog_candidates(projection={
            "no_progress": no_progress,
        }),
    }
    run_manager_monitor = build_run_manager_monitor_projection(
        completion_profile=completion_profile,
        monitor=monitor,
        status_explain=status_explain,
        pending_actions=pending_actions,
        no_progress=no_progress,
        advisor=advisor,
        wait_hints=wait_hints,
        resident_agent=resident_agent,
        runtime_pane_snapshot=runtime_pane_snapshot,
    )
    by_decision = Counter(
        str((item.get("policy_decision") or {}).get("decision") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    by_failure_class = Counter(
        str(item.get("failure_class") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    by_owner_route = Counter(
        str(item.get("owner_route") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    by_problem_class = Counter(
        str((item.get("problem_envelope") or {}).get("problem_class") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    return redact_obj({
        "schema_version": RUN_MANAGER_SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": _now(),
        "state_dir": str(state_dir),
        "project_root": str(project_root or state_dir.parent),
        "goal": goal,
        "completion_profile": completion_profile,
        "repair_ledger": ledger,
        "repair_merge_queue": merge_queue,
        "repair_closeout_gate": build_repair_closeout_gate(merge_queue),
        "no_progress": no_progress,
        "advisor": advisor,
        "wait_hints": wait_hints,
        "status_explain": status_explain,
        "runtime_pane_snapshot": runtime_pane_snapshot,
        "run_manager_monitor": run_manager_monitor,
        "resident_repair_worker": build_resident_repair_policy_projection(),
        "resident_agent": resident_agent,
        "report_artifacts": report_artifacts,
        "run_context_bundle": context,
        "monitor": monitor,
        "timeline": timeline,
        "policy": {
            "schema_version": "run-manager.policy.v1",
            "auto_actions": sorted(_SAFE_BATCH_ACTIONS),
            "approval_actions": ["trigger_rework"],
            "forbid_actions": [],
            "decision_values": sorted(_ACTION_POLICIES),
            "by_pending_decision": dict(sorted(by_decision.items())),
            "by_failure_class": dict(sorted(by_failure_class.items())),
            "by_owner_route": dict(sorted(by_owner_route.items())),
            "by_problem_class": dict(sorted(by_problem_class.items())),
        },
        "pending_actions": pending_actions,
        "summary": {
            "pending_actions": len(pending_actions),
            "executable_actions": sum(
                1 for item in pending_actions
                if (item.get("policy_decision") or {}).get("executable")
            ),
            "blocked_actions": sum(
                1 for item in pending_actions
                if (item.get("policy_decision") or {}).get("decision")
                in {"human_escalate", "needs_approval", "safe_halt"}
            ),
            "repair_fingerprints": ledger.get("summary", {}).get("fingerprints", 0),
            "goal_status": goal.get("status", "unknown"),
            "completion_status": completion_profile.get("status", "unknown"),
            "no_progress_status": no_progress.get("status", "clear"),
            "advisor_recommendations": (advisor.get("summary") or {}).get("recommendations", 0),
            "wait_hints": (wait_hints.get("summary") or {}).get("hints", 0),
            "resident_agent_status": resident_agent.get("status"),
            "pane_snapshot_status": runtime_pane_snapshot.get("enabled"),
            "failure_classes": dict(sorted(by_failure_class.items())),
            "problem_classes": dict(sorted(by_problem_class.items())),
        },
    })


def _action_with_problem_envelope(action: dict[str, Any]) -> dict[str, Any]:
    updated = dict(action)
    if not isinstance(updated.get("problem_envelope"), dict):
        updated["problem_envelope"] = problem_envelope_from_action(updated)
    return redact_obj(updated)


def _safe_runtime_pane_snapshot(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    project_root: Path | None,
) -> dict[str, Any]:
    try:
        return build_runtime_pane_probe(
            state_dir,
            config=config,
            project_root=project_root,
            capture_lines=30,
        )
    except Exception as exc:
        return {
            "schema_version": "runtime.pane_probe.v0",
            "is_derived_projection": True,
            "enabled": False,
            "reason": "probe_failed",
            "error": str(exc),
            "state_dir": str(state_dir),
            "summary": {
                "expected": 0,
                "observed": 0,
                "mismatch": 0,
                "missing": 0,
                "by_status": {},
            },
            "panes": [],
        }


def build_run_manager_monitor_projection(
    *,
    completion_profile: dict[str, Any],
    monitor: dict[str, Any],
    status_explain: dict[str, Any],
    pending_actions: list[dict[str, Any]],
    no_progress: dict[str, Any],
    advisor: dict[str, Any],
    wait_hints: dict[str, Any],
    resident_agent: dict[str, Any],
    runtime_pane_snapshot: dict[str, Any],
) -> dict[str, Any]:
    pending_by_decision = Counter(
        str((item.get("policy_decision") or {}).get("decision") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    pending_by_owner = Counter(
        str(item.get("owner_route") or "unknown")
        for item in pending_actions
        if isinstance(item, dict)
    )
    pane_summary = runtime_pane_snapshot.get("summary")
    pane_summary = pane_summary if isinstance(pane_summary, dict) else {}
    return redact_obj({
        "schema_version": "run-manager.monitor.v1",
        "is_derived_projection": True,
        "completion_status": str(completion_profile.get("status") or "unknown"),
        "monitor_state": str(monitor.get("state") or ""),
        "wait_reason": str(status_explain.get("wait_reason") or ""),
        "next_auto_action": str(status_explain.get("next_auto_action") or ""),
        "blocking": bool(status_explain.get("blocking")),
        "summary": {
            "pending_actions": len(pending_actions),
            "pending_by_decision": dict(sorted(pending_by_decision.items())),
            "pending_by_owner": dict(sorted(pending_by_owner.items())),
            "no_progress_status": str(no_progress.get("status") or "clear"),
            "advisor_recommendations": int((advisor.get("summary") or {}).get("recommendations") or 0),
            "wait_hints": int((wait_hints.get("summary") or {}).get("hints") or 0),
            "resident_agent_status": str(resident_agent.get("status") or ""),
            "pane_expected": int(pane_summary.get("expected") or 0),
            "pane_observed": int(pane_summary.get("observed") or 0),
            "pane_missing": int(pane_summary.get("missing") or 0),
            "pane_mismatch": int(pane_summary.get("mismatch") or 0),
        },
        "refs": {
            "run_manager": "projections/run_manager.json",
            "status_explain": "projections/run_status_explain.json",
            "runtime_pane_snapshot": "projections/runtime_pane_snapshot.json",
            "supervisor": "projections/supervisor/snapshot.json",
        },
        "pane_status_counts": pane_summary.get("by_status") or {},
    })


def build_resident_agent_projection(
    events: list[ZfEvent],
    *,
    config: ZfConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Track the optional resident Run Manager agent as an observed peer."""

    current = now or datetime.now(timezone.utc)
    enabled = False
    instance_id = "run-manager"
    threshold_seconds = 600
    if config is not None:
        try:
            resident = config.runtime.run_manager.resident_agent
            enabled = bool(resident.enabled)
            instance_id = str(getattr(resident, "instance_id", "") or instance_id)
        except Exception:
            enabled = False
    spawned_index, spawned = _last_event_with_index(events, (RUN_MANAGER_RESIDENT_SPAWNED,))
    prompted_index, prompted = _last_event_with_index(events, (RUN_MANAGER_RESIDENT_PROMPTED,))
    if spawned is not None:
        enabled = True
        payload = spawned.payload if isinstance(spawned.payload, dict) else {}
        instance_id = str(payload.get("instance_id") or instance_id)
    spawned_payload = spawned.payload if spawned and isinstance(spawned.payload, dict) else {}
    agent_event = _last_event_after_index(
        events,
        prompted_index,
        (RUN_MANAGER_AGENT_OBSERVATION, RUN_MANAGER_AGENT_RECOMMENDATION),
    )
    if agent_event is None:
        # Runtime event windows may include archived observations before the
        # latest active prompt. Any resident response proves the agent has
        # consumed at least one turn, so do not project a false first-observation
        # stall after log rotation.
        agent_event = _last_event(
            events,
            (RUN_MANAGER_AGENT_OBSERVATION, RUN_MANAGER_AGENT_RECOMMENDATION),
        )
    prompt_payload = prompted.payload if prompted and isinstance(prompted.payload, dict) else {}
    prompt_sent = bool(prompt_payload.get("prompted")) if prompted else False
    prompt_age = _event_age_seconds(prompted, current) if prompted else None
    stalled = bool(
        enabled
        and spawned is not None
        and prompted is not None
        and prompt_sent
        and agent_event is None
        and prompt_age is not None
        and prompt_age >= threshold_seconds
    )
    if not enabled:
        status = "disabled"
    elif spawned is None:
        status = "not_spawned"
    elif prompted is None or not prompt_sent:
        status = "not_prompted"
    elif agent_event is not None:
        status = "observing"
    elif stalled:
        status = "stalled"
    else:
        status = "pending_first_observation"
    source_event_ids = [
        event.id for event in (spawned, prompted, agent_event)
        if event is not None and event.id
    ]
    return redact_obj({
        "schema_version": "run-manager.resident-agent-projection.v1",
        "is_derived_projection": True,
        "enabled": enabled,
        "instance_id": instance_id,
        "session_mode": str(spawned_payload.get("session_mode") or ""),
        "tmux_session": str(spawned_payload.get("tmux_session") or ""),
        "briefing_path": str(prompt_payload.get("briefing_path") or ""),
        "status": status,
        "stalled": stalled,
        "watchdog": {
            "enabled": enabled,
            "threshold_seconds": threshold_seconds,
            "prompt_age_seconds": prompt_age,
            "reason": (
                "prompted resident agent did not emit observation or recommendation"
                if stalled else ""
            ),
        },
        "spawned_event": _event_summary(spawned) if spawned else {},
        "prompted_event": _event_summary(prompted) if prompted else {},
        "latest_agent_event": _event_summary(agent_event) if agent_event else {},
        "latest_agent_event_id": agent_event.id if agent_event else "",
        "source_event_ids": source_event_ids,
        "source_refs": [
            f"events.jsonl#{event_id}" for event_id in source_event_ids
        ],
        "suggested_action": (
            "diagnose_resident_run_manager_agent"
            if stalled else "wait"
        ),
    })


def _pending_resident_agent_actions(
    resident_agent: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    if str(resident_agent.get("status") or "") != "stalled":
        return []
    source_event_ids = [
        str(value) for value in resident_agent.get("source_event_ids") or []
        if str(value).strip()
    ]
    prompt_event = resident_agent.get("prompted_event")
    prompt_event = prompt_event if isinstance(prompt_event, dict) else {}
    prompt_event_id = str(prompt_event.get("event_id") or "")
    fingerprint = "run-manager-resident-stalled:" + (prompt_event_id or "unknown")
    can_reprompt = bool(
        str(resident_agent.get("tmux_session") or "").strip()
        and str(resident_agent.get("briefing_path") or "").strip()
    )
    action_name = "resident-agent-reprompt" if can_reprompt else "diagnose-attention"
    safe_resume_action = "resident_agent_reprompt" if can_reprompt else "diagnose_attention"
    expected_downstream = (
        ["run.manager.resident.prompted"]
        if can_reprompt else ["run.manager.autoresearch.requested"]
    )
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": action_name,
        "checkpoint_id": "resident-agent-stall-" + hashlib.sha1(
            fingerprint.encode("utf-8")
        ).hexdigest()[:12],
        "safe_resume_action": safe_resume_action,
        "fingerprint": fingerprint,
        "failure_class": "run_manager_resident_agent_stalled",
        "owner_route": "controlled_action" if can_reprompt else "autoresearch",
        "action_policy": "auto_decide" if can_reprompt else "needs_diagnosis",
        "intervention_class": "auto_recover" if can_reprompt else "diagnose",
        "verify_condition": "expected_downstream_event:" + ",".join(expected_downstream),
        "expected_downstream_events": expected_downstream,
        "tmux_session": str(resident_agent.get("tmux_session") or ""),
        "session_mode": str(resident_agent.get("session_mode") or ""),
        "briefing_path": str(resident_agent.get("briefing_path") or ""),
        "instance_id": str(resident_agent.get("instance_id") or "run-manager"),
        "role_instance": str(resident_agent.get("instance_id") or "run-manager"),
        "attention_id": "resident-agent:" + (prompt_event_id or "prompted"),
        "severity": "high",
        "title": "Resident Run Manager agent has not emitted observations",
        "summary": (
            "run.manager.resident.prompted was emitted, but no "
            "run.manager.agent.observation or run.manager.agent.recommendation "
            "followed before the watchdog threshold."
        ),
        "source_event_ids": source_event_ids,
        "source_ref": f"events.jsonl#{prompt_event_id}" if prompt_event_id else "",
        "suggested_route": "autoresearch_trigger",
        "suggested_action": {
            "kind": "diagnose_resident_run_manager_agent",
            "instance_id": str(resident_agent.get("instance_id") or "run-manager"),
            "prompt_event_id": prompt_event_id,
            "recommended_next_steps": [
                "verify dedicated tmux pane target",
                "inspect provider approval prompt",
                "reprompt resident run manager if the pane is alive",
            ],
        },
        "recommended_actions": [
            "inspect_run_manager_pane",
            "reprompt_resident_run_manager",
            "fix_provider_approval_or_cli_context",
        ],
        "expected_output": [
            "resident_agent_diagnosis",
            "pane_target",
            "provider_prompt_state",
            "recommended_reprompt_or_repair_action",
        ],
        "route_registry": "run-manager-router.v1",
    }
    if _action_seen(events, action):
        return []
    action["preflight"] = preflight_action(
        action=action_name,
        payload=action,
    )
    action["policy_decision"] = decide_action_policy(
        action=action_name,
        payload=action,
    )
    return [action]


def _resident_agent_can_reprompt(resident_agent: dict[str, Any] | None) -> bool:
    if not isinstance(resident_agent, dict):
        return False
    if str(resident_agent.get("status") or "") in {"", "disabled", "not_spawned"}:
        return False
    return bool(
        str(resident_agent.get("tmux_session") or "").strip()
        and str(resident_agent.get("briefing_path") or "").strip()
        and str(resident_agent.get("instance_id") or "run-manager").strip()
    )


def _resident_diagnosis_reprompt_action(
    action: dict[str, Any],
    resident_agent: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _resident_agent_can_reprompt(resident_agent):
        return None
    resident_agent = resident_agent if isinstance(resident_agent, dict) else {}
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    if not checkpoint_id:
        return None
    reprompt = {
        **action,
        "schema_version": "run-manager.pending-action.v1",
        "action": "resident-agent-reprompt",
        "safe_resume_action": "resident_agent_reprompt",
        "owner_route": "run_manager",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "verify_condition": "expected_downstream_event:run.manager.resident.prompted",
        "expected_downstream_events": ["run.manager.resident.prompted"],
        "tmux_session": str(resident_agent.get("tmux_session") or ""),
        "session_mode": str(resident_agent.get("session_mode") or ""),
        "briefing_path": str(resident_agent.get("briefing_path") or ""),
        "instance_id": str(resident_agent.get("instance_id") or "run-manager"),
        "role_instance": str(resident_agent.get("instance_id") or "run-manager"),
        "diagnosis_source_action": str(action.get("action") or ""),
        "suggested_route": "run_manager_resident_agent",
    }
    reprompt["preflight"] = preflight_action(
        action="resident-agent-reprompt",
        payload=reprompt,
    )
    reprompt["policy_decision"] = decide_action_policy(
        action="resident-agent-reprompt",
        payload=reprompt,
    )
    return reprompt


def build_run_monitor_projection(
    state_dir: Path,
    *,
    events: list[ZfEvent],
    pending_actions: list[dict[str, Any]],
    completion_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tasks = _read_tasks(state_dir)
    lane_counts: Counter[str] = Counter()
    in_flight = []
    for task in tasks:
        status = str(getattr(task, "status", "") or "")
        assignee = str(getattr(task, "assigned_to", "") or "")
        if assignee:
            lane_counts[assignee] += 1
        if status in {"in_progress", "review", "verify", "test", "running"}:
            in_flight.append({
                "task_id": str(getattr(task, "id", "") or ""),
                "status": status,
                "assigned_to": assignee,
                "title": str(getattr(task, "title", "") or ""),
            })
    supervisor = _read_json(state_dir / "projections" / "supervisor" / "snapshot.json")
    attention = supervisor.get("attention_items") if isinstance(supervisor, dict) else []
    open_attention = [
        item for item in (attention if isinstance(attention, list) else [])
        if isinstance(item, dict) and str(item.get("status") or "open") in {"", "open", "unacknowledged"}
    ]
    last_action = _last_event(events, (
        RUN_MANAGER_ACTION_APPLIED,
        RUN_MANAGER_ACTION_BLOCKED,
        RUN_MANAGER_ACTION_FAILED,
        RUN_MANAGER_ACTION_VERIFY_PASSED,
        RUN_MANAGER_ACTION_VERIFY_FAILED,
        RUN_MANAGER_HUMAN_DECISION_APPLIED,
        RUN_MANAGER_HUMAN_DECISION_REJECTED,
    ))
    completion_status = ""
    if isinstance(completion_profile, dict):
        completion_status = str(completion_profile.get("status") or "")
    display_in_flight = in_flight[-50:]
    display_open_attention = len(open_attention)
    residual_in_flight: list[dict[str, str]] = []
    residual_open_attention = 0
    state = "healthy_waiting"
    if completion_status == "complete":
        state = "complete"
        residual_in_flight = display_in_flight
        residual_open_attention = display_open_attention
        display_in_flight = []
        display_open_attention = 0
    elif any((item.get("policy_decision") or {}).get("decision") in {"human_escalate", "safe_halt"} for item in pending_actions):
        state = "needs_human"
    elif _pending_human_decisions(events) and not _has_auto_ready_actions(pending_actions):
        state = "needs_human"
    elif _has_pending_repair_closeout(events):
        state = "repair_closeout_required"
    elif any(event.type in {RUN_MANAGER_REPAIR_ACCEPTED, "autoresearch.repair.dispatched"} for event in events[-20:]):
        state = "repair_in_flight"
    elif open_attention and not pending_actions:
        state = "silent_stall"
    return {
        "schema_version": "run-manager.monitor.v1",
        "state": state,
        "current_phase": _derive_phase(events),
        "lane_occupancy": dict(sorted(lane_counts.items())),
        "in_flight_tasks": display_in_flight,
        "residual_in_flight_tasks": residual_in_flight,
        "open_attention": display_open_attention,
        "residual_open_attention": residual_open_attention,
        "pending_actions": len(pending_actions),
        "last_action": _event_summary(last_action) if last_action else {},
        "next_wait": _next_wait(state, pending_actions, open_attention),
    }


def build_run_status_explain_projection(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    goal: dict[str, Any],
    completion_profile: dict[str, Any],
    monitor: dict[str, Any],
    pending_actions: list[dict[str, Any]],
    no_progress: dict[str, Any],
    repair_merge_queue: dict[str, Any],
    wait_hints: dict[str, Any],
) -> dict[str, Any]:
    """Explain the current run state from existing read-only projections."""

    pending_action = next((item for item in pending_actions if isinstance(item, dict)), None)
    event_list = list(events or [])
    pending_execution = _pending_execution_summary(event_list, pending_actions)
    explanation = _status_explain_decision(
        pending_action=pending_action,
        completion_profile=completion_profile,
        monitor=monitor,
        no_progress=no_progress,
        repair_merge_queue=repair_merge_queue,
    )
    in_flight = monitor.get("in_flight_tasks")
    in_flight = in_flight if isinstance(in_flight, list) else []
    active = in_flight[0] if in_flight else {}
    active = active if isinstance(active, dict) else {}
    return redact_obj({
        "schema_version": RUN_STATUS_EXPLAIN_SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": _now(),
        "state_dir": str(state_dir),
        "current_phase": str(monitor.get("current_phase") or "unknown"),
        "active_task_id": str(active.get("task_id") or ""),
        "active_lane": str(active.get("assigned_to") or ""),
        "goal_status": str(goal.get("status") or "unknown"),
        "completion_status": str(completion_profile.get("status") or "unknown"),
        "monitor_state": str(monitor.get("state") or "unknown"),
        "owner_route": explanation["owner_route"],
        "intervention_class": explanation["intervention_class"],
        "wait_reason": explanation["wait_reason"],
        "next_auto_action": explanation["next_auto_action"],
        "blocking": explanation["blocking"],
        "blocking_refs": explanation["blocking_refs"],
        "maintenance_refs": explanation.get("maintenance_refs", []),
        "pending_execution": pending_execution,
        "pending_actions": [
            _pending_action_explain(item, pending_execution=pending_execution)
            for item in pending_actions
        ],
        "wait_hints": wait_hints.get("items") if isinstance(wait_hints, dict) else [],
        "source_refs": {
            "run_manager": "projections/run_manager.json",
            "events": "events.jsonl",
            "kanban": "kanban.json",
            "supervisor": "projections/supervisor/snapshot.json",
        },
    })


def build_run_goal_projection(
    events: list[ZfEvent],
    *,
    blocker_threshold: int = 3,
) -> dict[str, Any]:
    status = "unknown"
    objective = ""
    run_id = ""
    source_event_id = ""
    blockers: Counter[str] = Counter()
    last_blocker: dict[str, Any] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "run.goal.started":
            status = "active"
            run_id = str(payload.get("run_id") or event.correlation_id or event.id)
            objective = str(payload.get("objective") or "")
            source_event_id = event.id
        elif event.type == "run.goal.updated":
            status = str(payload.get("status") or status or "active")
            objective = str(payload.get("objective") or objective)
            run_id = str(payload.get("run_id") or run_id)
            source_event_id = event.id
        elif event.type == "run.goal.completed":
            status = "complete"
            run_id = str(payload.get("run_id") or run_id)
            source_event_id = event.id
        elif event.type == "run.goal.blocked":
            status = "blocked"
            run_id = str(payload.get("run_id") or run_id)
            source_event_id = event.id
        if event.type in {
            RUN_MANAGER_ACTION_VERIFY_FAILED,
            RUN_MANAGER_ACTION_BLOCKED,
            "human.escalate",
        }:
            key = _fingerprint(payload, fallback=event.id)
            blockers[key] += 1
            last_blocker = {
                "fingerprint": key,
                "count": blockers[key],
                "event_id": event.id,
                "event_type": event.type,
            }
    if status == "unknown" and any(event.type == "loop.started" for event in events):
        status = "active"
    blocked_ready = bool(
        status != "complete"
        and blockers
        and max(blockers.values()) >= blocker_threshold
    )
    return {
        "schema_version": RUN_GOAL_SCHEMA_VERSION,
        "is_derived_projection": True,
        "run_id": run_id,
        "objective": objective,
        "status": status,
        "blocked_ready": blocked_ready,
        "blocker_threshold": blocker_threshold,
        "last_blocker": last_blocker,
        "source_event_id": source_event_id,
    }


def build_repair_ledger(events: list[ZfEvent]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in {
            "autoresearch.repair.dispatch_requested",
            RUN_MANAGER_REPAIR_ACCEPTED,
            RUN_MANAGER_REPAIR_REJECTED,
            RUN_MANAGER_REPAIR_BLOCKED,
            "autoresearch.repair.dispatched",
            "autoresearch.repair.dispatch_blocked",
            "autoresearch.repair.closeout.required",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        fp = _fingerprint(payload, fallback=event.id)
        row = entries.setdefault(fp, {
            "fingerprint": fp,
            "attempts": [],
            "events": [],
            "status": "requested",
            "last_event_id": "",
            "last_event_type": "",
            "next_allowed_action": "run_manager_intake",
        })
        attempt = _safe_int(payload.get("attempt"))
        row["events"].append(_event_summary(event))
        if attempt not in row["attempts"]:
            row["attempts"].append(attempt)
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        if event.type == RUN_MANAGER_REPAIR_ACCEPTED:
            row["status"] = "accepted"
            row["next_allowed_action"] = "dispatch_repair_worker"
        elif event.type == "autoresearch.repair.dispatched":
            row["status"] = "dispatched"
            row["next_allowed_action"] = "wait_for_closeout"
        elif event.type == "autoresearch.repair.closeout.required":
            row["status"] = "closeout_required"
            row["next_allowed_action"] = "operator_merge_or_reject"
        elif event.type in {RUN_MANAGER_REPAIR_REJECTED, RUN_MANAGER_REPAIR_BLOCKED, "autoresearch.repair.dispatch_blocked"}:
            row["status"] = "blocked"
            row["next_allowed_action"] = "human_escalate"
    rows = sorted(entries.values(), key=lambda item: str(item.get("last_event_id") or ""))
    return redact_obj({
        "schema_version": REPAIR_LEDGER_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "fingerprints": len(rows),
            "blocked": sum(1 for row in rows if row.get("status") == "blocked"),
            "closeout_required": sum(1 for row in rows if row.get("status") == "closeout_required"),
        },
        "items": rows,
    })


def build_repair_merge_queue(events: list[ZfEvent]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    terminal_statuses = {"merged", "discarded"}
    for event in events:
        if event.type not in {
            "autoresearch.repair.closeout.required",
            RUN_MANAGER_REPAIR_MERGE_QUEUED,
            RUN_MANAGER_REPAIR_MERGE_MERGING,
            RUN_MANAGER_REPAIR_MERGE_MERGED,
            RUN_MANAGER_REPAIR_MERGE_NEEDS_REVIEW,
            RUN_MANAGER_REPAIR_MERGE_DISCARDED,
            RUN_MANAGER_ACTION_APPLIED,
            RUN_MANAGER_ACTION_FAILED,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {RUN_MANAGER_ACTION_APPLIED, RUN_MANAGER_ACTION_FAILED} and (
            str(payload.get("action") or "") != "repair-closeout-validate"
        ):
            continue
        key = _repair_merge_key(payload, fallback=event.id)
        row = entries.setdefault(key, {
            "queue_id": key,
            "fingerprint": _fingerprint(payload, fallback=key),
            "candidate_id": str(payload.get("candidate_id") or ""),
            "candidate_path": str(payload.get("candidate_path") or ""),
            "branch": str(payload.get("branch") or payload.get("candidate_branch") or ""),
            "worktree_path": str(payload.get("worktree_path") or payload.get("worktree") or ""),
            "source_commit": str(payload.get("source_commit") or ""),
            "source_title": str(payload.get("source_title") or ""),
            "risk_classification": payload.get("risk_classification")
            if isinstance(payload.get("risk_classification"), dict) else {},
            "verification_plan": payload.get("verification_plan")
            if isinstance(payload.get("verification_plan"), list) else [],
            "continuation": payload.get("continuation")
            if isinstance(payload.get("continuation"), dict) else {},
            "restart_strategy": str(payload.get("restart_strategy") or ""),
            "safe_boundary": str(payload.get("safe_boundary") or ""),
            "state_snapshot_required": bool(payload.get("state_snapshot_required", False)),
            "replay_required": bool(payload.get("replay_required", False)),
            "apply_candidate": _repair_closeout_apply_candidate(payload),
            "validation": {},
            "status": "closeout_required",
            "next_allowed_action": "operator_merge_or_reject",
            "events": [],
            "last_event_id": "",
            "last_event_type": "",
        })
        row["events"].append(_event_summary(event))
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        if event.type == RUN_MANAGER_REPAIR_MERGE_QUEUED:
            row["status"] = "queued"
            row["next_allowed_action"] = "merge_worker"
        elif event.type == RUN_MANAGER_REPAIR_MERGE_MERGING:
            row["status"] = "merging"
            row["next_allowed_action"] = "wait_for_merge_result"
        elif event.type == RUN_MANAGER_REPAIR_MERGE_MERGED:
            row["status"] = "merged"
            row["next_allowed_action"] = "none"
        elif event.type == RUN_MANAGER_REPAIR_MERGE_NEEDS_REVIEW:
            row["status"] = "needs_review"
            row["next_allowed_action"] = "operator_review"
        elif event.type == RUN_MANAGER_REPAIR_MERGE_DISCARDED:
            row["status"] = "discarded"
            row["next_allowed_action"] = "none"
        elif event.type in {RUN_MANAGER_ACTION_APPLIED, RUN_MANAGER_ACTION_FAILED}:
            action_name = str(payload.get("action") or "")
            if action_name == "repair-closeout-validate":
                row["validation"] = {
                    "status": "passed" if event.type == RUN_MANAGER_ACTION_APPLIED else "failed",
                    "event_id": event.id,
                    "reason": str(payload.get("reason") or ""),
                    "result": payload.get("validation_result")
                    if isinstance(payload.get("validation_result"), dict) else {},
                }
                if event.type == RUN_MANAGER_ACTION_FAILED:
                    row["status"] = "needs_review"
                    row["next_allowed_action"] = "repair_validation_failed"
    rows = sorted(entries.values(), key=lambda item: str(item.get("last_event_id") or ""))
    return redact_obj({
        "schema_version": REPAIR_MERGE_QUEUE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("status") not in terminal_statuses),
            "closeout_required": sum(1 for row in rows if row.get("status") == "closeout_required"),
            "queued": sum(1 for row in rows if row.get("status") == "queued"),
            "merging": sum(1 for row in rows if row.get("status") == "merging"),
            "needs_review": sum(1 for row in rows if row.get("status") == "needs_review"),
            "merged": sum(1 for row in rows if row.get("status") == "merged"),
            "discarded": sum(1 for row in rows if row.get("status") == "discarded"),
        },
        "items": rows,
    })


def build_repair_closeout_gate(repair_merge_queue: dict[str, Any]) -> dict[str, Any]:
    summary = repair_merge_queue.get("summary") if isinstance(repair_merge_queue, dict) else {}
    pending = int((summary or {}).get("pending") or 0)
    needs_review = int((summary or {}).get("needs_review") or 0)
    closeout_required = int((summary or {}).get("closeout_required") or 0)
    status = "clear"
    if pending:
        status = "needs_review" if needs_review else "blocked"
    return {
        "schema_version": "run-manager.repair-closeout-gate.v1",
        "is_derived_projection": True,
        "status": status,
        "auto_merge": False,
        "pending": pending,
        "closeout_required": closeout_required,
        "needs_review": needs_review,
        "allowed_events": [
            RUN_MANAGER_REPAIR_MERGE_QUEUED,
            RUN_MANAGER_REPAIR_MERGE_MERGING,
            RUN_MANAGER_REPAIR_MERGE_MERGED,
            RUN_MANAGER_REPAIR_MERGE_NEEDS_REVIEW,
            RUN_MANAGER_REPAIR_MERGE_DISCARDED,
        ],
        "reason": "repair closeout requires explicit event-sourced merge/reject decision"
        if pending else "",
    }


def _repair_closeout_apply_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    risk = payload.get("risk_classification")
    risk = risk if isinstance(risk, dict) else {}
    verification_plan = payload.get("verification_plan")
    verification_plan = verification_plan if isinstance(verification_plan, list) else []
    risk_value = str(risk.get("risk") or "unknown")
    allowed = bool(risk.get("controlled_apply_allowed")) and risk_value == "low"
    decision = "controlled_apply_candidate" if allowed else "human_approval_required"
    return {
        "schema_version": "run-manager.repair-closeout-apply-candidate.v1",
        "decision": decision,
        "risk": risk_value,
        "verification_required": True,
        "verification_plan_count": len(verification_plan),
        "auto_merge": False,
        "approval_token": _repair_merge_key(payload, fallback="")
        if not allowed else "",
    }


def build_run_completion_profile(
    events: list[ZfEvent],
    *,
    goal: dict[str, Any],
    repair_ledger: dict[str, Any],
    repair_merge_queue: dict[str, Any],
) -> dict[str, Any]:
    pending_human = _pending_human_decisions(events)
    failed_verifications = _open_verify_failures(events)
    _ = repair_ledger
    repair_blockers = _blocking_repair_merge_counts(events, repair_merge_queue)
    closeout_required = repair_blockers["closeout_required"]
    merge_pending = repair_blockers["pending"]
    blockers = []
    terminal_signal = _current_terminal_signal(events)
    terminal_payload = (
        terminal_signal.payload
        if terminal_signal is not None and isinstance(terminal_signal.payload, dict)
        else {}
    )
    terminal_run_passed = (
        terminal_signal is not None
        and terminal_signal.type == RUN_COMPLETED
        and str(terminal_payload.get("status") or "passed") == "passed"
    )
    if pending_human:
        blockers.append("pending_human_decision")
    if (closeout_required or merge_pending) and not terminal_run_passed:
        blockers.append("repair_closeout_pending")
    if failed_verifications:
        blockers.append("action_verify_failed")
    goal_status = str(goal.get("status") or "unknown")
    if goal_status == "complete" or terminal_signal is not None:
        status = "blocked" if blockers else "complete"
    elif goal_status == "blocked" or bool(goal.get("blocked_ready")):
        status = "blocked"
    else:
        status = "active" if goal_status == "active" else goal_status
    return redact_obj({
        "schema_version": RUN_COMPLETION_PROFILE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "status": status,
        "goal_status": goal_status,
        "terminal_signal": _event_summary(terminal_signal) if terminal_signal else {},
        "blockers": blockers,
        "pending_human_decisions": pending_human,
        "open_verify_failures": failed_verifications,
        "repair_closeout_required": closeout_required,
        "repair_merge_pending": merge_pending,
    })


def build_run_manager_timeline(
    events: list[ZfEvent],
    *,
    limit: int = 80,
) -> dict[str, Any]:
    wanted_prefixes = (
        "run.manager.",
        "human.escalation.",
        "autoresearch.repair.",
    )
    wanted_exact = {
        "human.escalate",
        "workflow.resume.applied",
        "workflow.resume.control_action.result",
        "task_map.ready",
        "candidate.ready",
        "autoresearch.loop.completed",
        "autoresearch.loop.failed",
    }
    rows: list[dict[str, Any]] = []
    for event in events:
        if not (event.type in wanted_exact or event.type.startswith(wanted_prefixes)):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        rows.append({
            **_event_summary(event),
            "actor": event.actor,
            "transition": str(payload.get("transition") or ""),
            "action": str(payload.get("action") or payload.get("safe_resume_action") or ""),
            "decision": str(payload.get("decision") or ""),
            "reason": str(payload.get("reason") or ""),
            "next_route": str(payload.get("next_route") or payload.get("next_allowed_action") or ""),
            "verify_condition": str(payload.get("verify_condition") or ""),
        })
    clipped = rows[-limit:]
    return redact_obj({
        "schema_version": "run-manager.timeline.v1",
        "is_derived_projection": True,
        "limit": limit,
        "total": len(rows),
        "items": clipped,
    })


def build_run_context_bundle(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    refs = {
        "events_jsonl": str(state_dir / "events.jsonl"),
        "kanban_json": str(state_dir / "kanban.json"),
        "workflow_resume_projection": str(state_dir / "projections" / "workflow_resume.json"),
        "supervisor_snapshot": str(state_dir / "projections" / "supervisor" / "snapshot.json"),
    }
    existing_refs = {
        key: value for key, value in refs.items()
        if Path(value).exists()
    }
    return {
        "schema_version": RUN_CONTEXT_BUNDLE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "project_root": str(project_root or state_dir.parent),
        "state_dir": str(state_dir),
        "refs": existing_refs,
        "event_window": {
            "count": len(events),
            "first_event_id": events[0].id if events else "",
            "last_event_id": events[-1].id if events else "",
            "last_event_type": events[-1].type if events else "",
        },
        "failure_fingerprints": _recent_failure_fingerprints(events),
    }


def _legacy_run_manager_context_ref() -> str:
    return "projections/run_manager.json#run_context_bundle"


def _safe_ref_slug(value: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-"
        for ch in str(value or "").strip()
    ).strip("-._")
    return cleaned[:96] or "unknown"


def _run_manager_read_set(
    *,
    action: dict[str, Any],
    context: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    refs = context.get("refs") if isinstance(context.get("refs"), dict) else {}
    pending_actions = projection.get("pending_actions")
    return redact_obj({
        "schema_version": RUN_MANAGER_READ_SET_SCHEMA_VERSION,
        "source_refs": {
            "legacy_context_ref": _legacy_run_manager_context_ref(),
            "projection_ref": "projections/run_manager.json",
            "action_source_ref": str(action.get("source_ref") or ""),
            "context_refs": dict(refs),
        },
        "source_event_ids": _string_list(action.get("source_event_ids")),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "fingerprint": _fingerprint(action, fallback="run-manager-context"),
        "pending_action_count": len(pending_actions) if isinstance(pending_actions, list) else 0,
    })


def _write_run_manager_context_sidecars(
    *,
    state_dir: Path,
    request_id: str,
    action: dict[str, Any],
    projection: dict[str, Any],
    project_root: Path | None,
    source_event_id: str,
) -> dict[str, Any]:
    context = projection.get("run_context_bundle")
    if not isinstance(context, dict) or not context:
        context = build_run_context_bundle(
            state_dir,
            _read_events(state_dir),
            project_root=project_root,
        )
    safe_request = _safe_ref_slug(request_id)
    context_payload = redact_obj({
        "schema_version": RUN_MANAGER_CONTEXT_SIDECAR_SCHEMA_VERSION,
        "request_id": request_id,
        "legacy_context_ref": _legacy_run_manager_context_ref(),
        "action": action,
        "context": context,
        "monitor": projection.get("monitor") if isinstance(projection.get("monitor"), dict) else {},
        "status_explain": (
            projection.get("status_explain")
            if isinstance(projection.get("status_explain"), dict) else {}
        ),
        "wait_hints": projection.get("wait_hints") if isinstance(projection.get("wait_hints"), dict) else {},
    })
    context_ref = write_sidecar_json(
        state_dir,
        f"diagnostics/run-manager/{safe_request}/context.json",
        context_payload,
        kind="diagnostic_trace",
        schema_version=RUN_MANAGER_CONTEXT_SIDECAR_SCHEMA_VERSION,
        created_by="run-manager",
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "actor": "run-manager",
            "purpose": "diagnosis",
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(action.get("summary") or action.get("title") or request_id)[:200],
    )
    read_set_ref = write_sidecar_json(
        state_dir,
        f"diagnostics/run-manager/{safe_request}/read_set.json",
        _run_manager_read_set(action=action, context=context, projection=projection),
        kind="diagnostic_trace",
        schema_version=RUN_MANAGER_READ_SET_SCHEMA_VERSION,
        created_by="run-manager",
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "actor": "run-manager",
            "purpose": "diagnosis-read-set",
        },
        retention={"class": "audit_required"},
        required=True,
        preview=f"read_set:{request_id}",
    )
    return {
        "context_ref": context_ref,
        "read_set_ref": read_set_ref,
        "legacy_context_ref": _legacy_run_manager_context_ref(),
    }


def decide_action_policy(
    *,
    action: str,
    payload: dict[str, Any],
    mutating_resume_supported: bool = False,
) -> dict[str, Any]:
    return router_decide_action_policy(
        action=action,
        payload=payload,
        mutating_resume_supported=mutating_resume_supported,
    )


def classify_recovery_context(action: dict[str, Any]) -> dict[str, Any]:
    return router_classify_recovery_context(action)


def write_run_manager_projections(state_dir: Path, projection: dict[str, Any]) -> None:
    state_dir = Path(state_dir)
    projections_dir = state_dir / "projections"
    atomic_write_text(
        projections_dir / "run_manager.json",
        json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "run_goal.json",
        json.dumps(projection.get("goal", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "repair_ledger.json",
        json.dumps(projection.get("repair_ledger", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "repair_merge_queue.json",
        json.dumps(projection.get("repair_merge_queue", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "run_status_explain.json",
        json.dumps(projection.get("status_explain", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "runtime_pane_snapshot.json",
        json.dumps(projection.get("runtime_pane_snapshot", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        projections_dir / "run_manager_monitor.json",
        json.dumps(projection.get("run_manager_monitor", {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    for name in (
        "no_progress",
        "advisor",
        "wait_hints",
        "resident_repair_worker",
        "resident_agent",
        "report_artifacts",
    ):
        atomic_write_text(
            projections_dir / f"run_manager_{name}.json",
            json.dumps(projection.get(name, {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def emit_human_escalation_package(
    writer: EventWriter,
    *,
    action: dict[str, Any],
    reason: str,
    causation_id: str | None = None,
) -> ZfEvent:
    payload = {
        "schema_version": "human-escalation-package.v1",
        "decision_token": _human_decision_token(action),
        "approval_ref": "human:" + _human_decision_token(action),
        "run_id": str(action.get("run_id") or action.get("pdd_id") or ""),
        "stage": str(action.get("stage_id") or ""),
        "task_id": str(action.get("task_id") or ""),
        "lane": str(action.get("lane") or ""),
        "fingerprint": _fingerprint(action, fallback=str(action.get("checkpoint_id") or "")),
        "failure_class": str(action.get("failure_class") or ""),
        "owner_route": str(action.get("owner_route") or ""),
        "action_policy": str(action.get("action_policy") or ""),
        "intervention_class": str(action.get("intervention_class") or ""),
        "verify_condition": str(action.get("verify_condition") or ""),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "recent_action": str(action.get("action") or ""),
        "action": str(action.get("action") or ""),
        "safe_resume_action": str(action.get("safe_resume_action") or ""),
        "fanout_id": str(action.get("fanout_id") or ""),
        "pdd_id": str(action.get("pdd_id") or ""),
        "feature_id": str(action.get("feature_id") or ""),
        "trace_id": str(action.get("trace_id") or ""),
        "task_map_ref": str(action.get("task_map_ref") or ""),
        "source_index_ref": str(action.get("source_index_ref") or ""),
        "source_commit": str(action.get("source_commit") or ""),
        "target_ref": str(action.get("target_ref") or ""),
        "candidate_ref": str(action.get("candidate_ref") or ""),
        "candidate_base_commit": str(action.get("candidate_base_commit") or ""),
        "candidate_head_commit": str(action.get("candidate_head_commit") or ""),
        "diff_ref": str(action.get("diff_ref") or ""),
        "source_event_id": str(action.get("source_event_id") or ""),
        "source_event_type": str(action.get("source_event_type") or ""),
        "mutating_resume_supported": bool(action.get("mutating_resume_supported")),
        "source_event_ids": [
            str(value) for value in action.get("source_event_ids") or []
            if str(value).strip()
        ],
        "suggested_options": ["approve_controlled_action", "request_autoresearch", "safe_halt"],
        "question": "请选择是否允许 Run Manager 执行该高风险恢复动作。",
        "reason": reason,
    }
    escalate = writer.emit(
        "human.escalate",
        actor="run-manager",
        task_id=payload["task_id"] or None,
        causation_id=causation_id,
        payload=payload,
    )
    writer.emit(
        HUMAN_ESCALATION_SENT,
        actor="run-manager",
        task_id=payload["task_id"] or None,
        causation_id=escalate.id,
        correlation_id=escalate.correlation_id,
        payload={
            **payload,
            "delivery_target": "web",
            "delivery_status": "sent",
            "escalation_event_id": escalate.id,
            "source_message_id": escalate.id,
        },
    )
    return escalate


def _consume_agent_recommendations(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    events: list[ZfEvent],
    causation_id: str,
    reflect_fn: Callable[..., Any] | None,
) -> tuple[int, int, int, int]:
    consumed = 0
    autoresearch_requested = 0
    reflects_completed = 0
    repairs_accepted = 0
    for event in events:
        if event.type != RUN_MANAGER_AGENT_RECOMMENDATION:
            continue
        if _agent_recommendation_seen(events, event):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        action = _action_from_agent_recommendation(event, payload)
        route = _recommendation_route(payload)
        status = "consumed"
        reason = ""
        downstream_event_ids: list[str] = []
        if route == "autoresearch":
            before = len(writer.event_log.read_all())
            if _emit_autoresearch_request(
                state_dir=state_dir,
                writer=writer,
                action=action,
                projection={},
                project_root=project_root,
                causation_id=causation_id,
            ):
                autoresearch_requested += 1
                after_events = writer.event_log.read_all()[before:]
                downstream_event_ids.extend(
                    event.id for event in after_events
                    if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
                )
            else:
                reason = "autoresearch_request_already_exists"
        elif route == "reflect":
            if not _run_manager_reflect_enabled(config, reflect_fn):
                status = "blocked"
                reason = "run manager reflect is disabled"
            elif _maybe_invoke_run_manager_reflect(
                state_dir=state_dir,
                writer=writer,
                config=config,
                project_root=project_root,
                action=action,
                projection={},
                causation_id=causation_id,
                reflect_fn=reflect_fn,
            ):
                reflects_completed += 1
            else:
                reason = "reflect_request_already_exists_or_disabled"
        elif route == "repair":
            if not _run_manager_source_repair_enabled(config):
                status = "blocked"
                reason = "runtime.run_manager.source_repair.enabled is false"
            else:
                before = len(writer.event_log.read_all())
                if _emit_repair_acceptance_from_agent(writer, action, payload, causation_id=causation_id):
                    repairs_accepted += 1
                    after_events = writer.event_log.read_all()[before:]
                    downstream_event_ids.extend(
                        event.id for event in after_events
                        if event.type == RUN_MANAGER_REPAIR_ACCEPTED
                    )
                else:
                    reason = "repair_request_already_exists"
        elif route == "human":
            escalation = emit_human_escalation_package(
                writer,
                action=action,
                reason=str(payload.get("reason") or "resident Run Manager requested human decision"),
                causation_id=causation_id,
            )
            downstream_event_ids.append(escalation.id)
        elif route in {"wait", ""}:
            status = "wait"
            reason = "resident recommendation requested wait/noop"
        else:
            status = "blocked"
            reason = f"unsupported resident recommendation route {route!r}"
        writer.emit(
            RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED,
            actor="run-manager",
            causation_id=event.id,
            correlation_id=event.correlation_id or event.id,
            payload={
                "schema_version": "run-manager.agent-recommendation.v1",
                "source_event_id": event.id,
                "route": route,
                "status": status,
                "reason": reason,
                "checkpoint_id": str(action.get("checkpoint_id") or ""),
                "fingerprint": str(action.get("fingerprint") or ""),
                "downstream_event_ids": downstream_event_ids,
            },
        )
        consumed += 1
    return consumed, autoresearch_requested, reflects_completed, repairs_accepted


def _agent_recommendation_seen(events: list[ZfEvent], recommendation: ZfEvent) -> bool:
    for event in events:
        if event.type != RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("source_event_id") or "") == recommendation.id:
            return True
    return False


def _run_manager_source_repair_enabled(config: ZfConfig) -> bool:
    source_repair = getattr(
        getattr(getattr(config, "runtime", None), "run_manager", None),
        "source_repair",
        None,
    )
    return bool(getattr(source_repair, "enabled", False))


def _recommendation_route(payload: dict[str, Any]) -> str:
    route = payload.get("recommended_route")
    if route is None:
        route = payload.get("recommended_action")
    if isinstance(route, dict):
        route = route.get("route") or route.get("kind") or route.get("action")
    normalized = str(route or "").strip().lower().replace("-", "_")
    aliases = {
        "request_autoresearch": "autoresearch",
        "autoresearch_debug": "autoresearch",
        "reflection": "reflect",
        "run_manager_reflect": "reflect",
        "repair": "repair",
        "repair_worker": "repair",
        "self_repair": "repair",
        "run_manager_repair": "repair",
        "dispatch_repair_worker": "repair",
        "bounded_repair": "repair",
        "human_escalate": "human",
        "owner_question": "human",
        "noop": "wait",
    }
    return aliases.get(normalized, normalized)


def _action_from_agent_recommendation(
    event: ZfEvent,
    payload: dict[str, Any],
) -> dict[str, Any]:
    checkpoint_id = str(
        payload.get("checkpoint_id")
        or payload.get("source_action_checkpoint_id")
        or "agent-recommendation-" + hashlib.sha1((event.id or "").encode("utf-8")).hexdigest()[:12]
    )
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": "agent-recommendation",
        "safe_resume_action": str(payload.get("safe_resume_action") or "agent_recommendation"),
        "checkpoint_id": checkpoint_id,
        "fingerprint": _fingerprint(payload, fallback=checkpoint_id),
        "failure_class": str(payload.get("failure_class") or "resident_agent_recommendation"),
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "verify_condition": str(payload.get("verify_condition") or ""),
        "title": str(payload.get("title") or "Resident Run Manager recommendation"),
        "summary": str(payload.get("summary") or payload.get("recommendation") or ""),
        "source_event_ids": [event.id] if event.id else [],
        "source_ref": f"events.jsonl#{event.id}" if event.id else "",
        "recommended_actions": _string_list(payload.get("recommended_actions")),
        "expected_output": _string_list(payload.get("expected_output")),
    }


def _emit_repair_acceptance_from_agent(
    writer: EventWriter,
    action: dict[str, Any],
    payload: dict[str, Any],
    *,
    causation_id: str,
) -> bool:
    fingerprint = _fingerprint(action, fallback=str(action.get("checkpoint_id") or "agent-repair"))
    attempt = _safe_int(payload.get("attempt"))
    key = (fingerprint, attempt)
    for event in writer.event_log.read_all():
        if event.type not in _RUN_MANAGER_REPAIR_TERMINALS:
            continue
        existing = event.payload if isinstance(event.payload, dict) else {}
        if _repair_key(existing) == key:
            return False
    repair_task_payload = payload.get("repair_task_payload")
    if not isinstance(repair_task_payload, dict):
        repair_task_payload = _repair_task_payload_from_agent_action(action, payload)
    writer.emit(
        RUN_MANAGER_REPAIR_ACCEPTED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.repair.v1",
            "fingerprint": fingerprint,
            "attempt": attempt,
            "candidate_id": str(
                payload.get("candidate_id")
                or action.get("checkpoint_id")
                or "resident-repair"
            ),
            "candidate_path": str(
                payload.get("candidate_path")
                or action.get("source_ref")
                or "projections/run_manager.json#resident-recommendation"
            ),
            "repair_task_payload": repair_task_payload,
            "source_event_id": (action.get("source_event_ids") or [""])[0],
            "decision": "accepted",
            "executor": "self_repair_runner",
            "source": "run_manager_resident_recommendation",
        },
    )
    return True


def _repair_task_payload_from_agent_action(
    action: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    scope = payload.get("scope") or ["src/zf/**", "tests/**"]
    if not isinstance(scope, list):
        scope = [str(scope)]
    verification = str(
        payload.get("verification")
        or payload.get("verify_command")
        or "run focused pytest for the changed runtime path"
    )
    title = str(
        payload.get("title")
        or action.get("title")
        or "Run Manager resident repair"
    )
    behavior = str(
        payload.get("summary")
        or payload.get("recommendation")
        or action.get("summary")
        or title
    )
    return {
        "title": title,
        "contract": {
            "schema_version": "task-contract.v1",
            "phase": "zaofu_self_repair",
            "behavior": behavior,
            "verification": verification,
            "verification_tiers": ["static", "runtime"],
            "scope": scope,
            "acceptance": "focused verification passes and no runtime truth is edited directly",
            "owner_role": "run-manager-repair-worker",
            "complexity": "complex",
            "evidence_contract": {
                "source": "run_manager_resident_recommendation",
                "source_event_ids": _string_list(action.get("source_event_ids")),
                "source_ref": str(action.get("source_ref") or ""),
                "fingerprint": _fingerprint(action, fallback="resident-repair"),
            },
        },
    }


def _emit_autoresearch_request(
    *,
    state_dir: Path,
    writer: EventWriter,
    action: dict[str, Any],
    projection: dict[str, Any],
    project_root: Path | None,
    causation_id: str,
) -> bool:
    request_id = _run_manager_autoresearch_request_id(action)
    if _request_id_seen(writer.event_log, RUN_MANAGER_AUTORESEARCH_REQUESTED, request_id):
        return False
    try:
        context_refs = _write_run_manager_context_sidecars(
            state_dir=state_dir,
            request_id=request_id,
            action=action,
            projection=projection,
            project_root=project_root,
            source_event_id=causation_id,
        )
    except Exception as exc:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason=f"run manager context sidecar failed: {exc}",
            human=False,
        )
        return False
    payload = {
        "schema_version": "run-manager.autoresearch-request.v2",
        "request_id": request_id,
        "fingerprint": _fingerprint(action, fallback=request_id),
        "failure_class": str(action.get("failure_class") or "unknown_complex"),
        "owner_route": str(action.get("owner_route") or "run_manager"),
        "action_policy": str(action.get("action_policy") or "needs_diagnosis"),
        "intervention_class": str(action.get("intervention_class") or "diagnose"),
        "verify_condition": str(action.get("verify_condition") or ""),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "action": str(action.get("action") or ""),
        "safe_resume_action": str(action.get("safe_resume_action") or ""),
        "fanout_id": str(action.get("fanout_id") or ""),
        "pdd_id": str(action.get("pdd_id") or ""),
        "stage_id": str(action.get("stage_id") or ""),
        "source_event_ids": [
            str(value) for value in action.get("source_event_ids") or []
            if str(value).strip()
        ],
        "source_ref": str(action.get("source_ref") or ""),
        "attention_id": str(action.get("attention_id") or ""),
        "title": str(action.get("title") or ""),
        "summary": str(action.get("summary") or ""),
        "recommended_actions": _string_list(action.get("recommended_actions")),
        **context_refs,
        "apply_policy": "proposal_only",
        "expected_output": (
            _string_list(action.get("expected_output"))
            or [
                "diagnosis_report",
                "reproduction_steps",
                "patch_or_resume_proposal",
            ]
        ),
        "resume_policy": "return_proposal_to_run_manager",
    }
    writer.emit(
        RUN_MANAGER_AUTORESEARCH_REQUESTED,
        actor="run-manager",
        causation_id=causation_id,
        correlation_id=request_id,
        payload=payload,
    )
    return True


def _maybe_invoke_run_manager_reflect(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    projection: dict[str, Any],
    causation_id: str,
    reflect_fn: Callable[..., Any] | None,
    force: bool = False,
) -> bool:
    if not force and not _run_manager_reflect_enabled(config, reflect_fn):
        return False
    request_id = _run_manager_reflect_request_id(action)
    if _request_id_seen(writer.event_log, RUN_MANAGER_REFLECT_COMPLETED, request_id):
        return False
    try:
        context_refs = _write_run_manager_context_sidecars(
            state_dir=state_dir,
            request_id=request_id,
            action=action,
            projection=projection,
            project_root=project_root,
            source_event_id=causation_id,
        )
    except Exception as exc:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason=f"run manager reflect context sidecar failed: {exc}",
            human=False,
        )
        return False
    if not _request_id_seen(writer.event_log, RUN_MANAGER_REFLECT_REQUESTED, request_id):
        writer.emit(
            RUN_MANAGER_REFLECT_REQUESTED,
            actor="run-manager",
            causation_id=causation_id,
            correlation_id=request_id,
            payload={
                "schema_version": "run-manager.reflect.v2",
                "request_id": request_id,
                "checkpoint_id": str(action.get("checkpoint_id") or ""),
                "fingerprint": _fingerprint(action, fallback=request_id),
                "failure_class": str(action.get("failure_class") or "unknown_complex"),
                **context_refs,
                "apply_policy": "proposal_only",
                "source_event_ids": _string_list(action.get("source_event_ids")),
            },
        )
    prompt = _build_run_manager_reflect_prompt(
        state_dir=state_dir,
        project_root=project_root,
        action=action,
        projection=projection,
    )
    result = _invoke_reflect_function(prompt, config=config, reflect_fn=reflect_fn)
    payload = _reflection_payload(result)
    writer.emit(
        RUN_MANAGER_REFLECT_COMPLETED,
        actor="run-manager",
        causation_id=causation_id,
        correlation_id=request_id,
        payload={
            "schema_version": "run-manager.reflect.v2",
            "request_id": request_id,
            "checkpoint_id": str(action.get("checkpoint_id") or ""),
            "fingerprint": _fingerprint(action, fallback=request_id),
            **context_refs,
            "apply_policy": "proposal_only",
            **payload,
        },
    )
    return True


def _run_manager_reflect_enabled(
    config: ZfConfig,
    reflect_fn: Callable[..., Any] | None,
) -> bool:
    if reflect_fn is not None:
        return True
    try:
        return bool(config.runtime.run_manager.reflect.enabled)
    except Exception:
        return False


def _run_manager_reflect_backend(config: ZfConfig) -> str:
    try:
        reflect = config.runtime.run_manager.reflect
        return str(reflect.backend or config.runtime.run_manager.backend or "claude-code")
    except Exception:
        return "claude-code"


def _run_manager_reflect_timeout(config: ZfConfig) -> int:
    try:
        return int(config.runtime.run_manager.reflect.timeout_seconds or 180)
    except Exception:
        return 180


def _invoke_reflect_function(
    prompt: str,
    *,
    config: ZfConfig,
    reflect_fn: Callable[..., Any] | None,
) -> Any:
    backend = _run_manager_reflect_backend(config)
    timeout_seconds = _run_manager_reflect_timeout(config)
    if reflect_fn is not None:
        try:
            return reflect_fn(prompt, backend=backend, timeout_seconds=timeout_seconds)
        except TypeError:
            return reflect_fn(prompt)
    from zf.autoresearch.loop_reflect import invoke_reflection_llm

    return invoke_reflection_llm(
        prompt,
        backend=backend,
        timeout_seconds=timeout_seconds,
    )


def _reflection_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        verdict = str(result.get("verdict") or "unknown")
        risk = str(result.get("risk") or "medium")
        alternatives = _string_list(result.get("alternatives"))
        recommendation = str(
            result.get("rec_for_next_iter")
            or result.get("recommendation")
            or ""
        )
        raw_response = str(result.get("raw_response") or "")
        route = str(result.get("recommended_route") or "")
    else:
        verdict = str(getattr(result, "verdict", "unknown") or "unknown")
        risk = str(getattr(result, "risk", "medium") or "medium")
        alternatives = _string_list(getattr(result, "alternatives", []))
        recommendation = str(getattr(result, "rec_for_next_iter", "") or "")
        raw_response = str(getattr(result, "raw_response", "") or "")
        route = ""
    return {
        "verdict": verdict,
        "risk": risk,
        "alternatives": alternatives,
        "recommendation": recommendation,
        "recommended_route": route or _reflection_recommended_route(verdict, risk),
        "raw_response": raw_response[:2000],
    }


def _reflection_recommended_route(verdict: str, risk: str) -> str:
    if verdict in {"better_fix_exists", "regression", "unknown"}:
        return "autoresearch"
    if risk == "high":
        return "human"
    return "wait"


def _run_manager_reflect_request_id(action: dict[str, Any]) -> str:
    raw = "|".join([
        str(action.get("checkpoint_id") or ""),
        str(action.get("safe_resume_action") or ""),
        _fingerprint(action, fallback=""),
    ])
    return "rmrf-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _build_run_manager_reflect_prompt(
    *,
    state_dir: Path,
    project_root: Path | None,
    action: dict[str, Any],
    projection: dict[str, Any],
) -> str:
    compact = {
        "state_dir": str(state_dir),
        "project_root": str(project_root or state_dir.parent),
        "action": _action_payload(action),
        "status_explain": projection.get("status_explain") or {},
        "monitor": projection.get("monitor") or {},
        "no_progress": projection.get("no_progress") or {},
        "advisor": projection.get("advisor") or {},
        "runtime_pane_snapshot_summary": (
            (projection.get("runtime_pane_snapshot") or {}).get("summary")
            if isinstance(projection.get("runtime_pane_snapshot"), dict) else {}
        ),
    }
    return (
        "你是 ZaoFu Run Manager 的只读反思 advisor。不要改文件,不要执行命令,"
        "不要写 runtime truth。只基于下面 JSON 判断下一步最小安全路线。\n\n"
        "输出严格 JSON: {\"verdict\":\"better_fix_exists|best_so_far|regression|unknown\","
        "\"alternatives\":[\"...\"],\"risk\":\"low|medium|high\","
        "\"rec_for_next_iter\":\"...\"}\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True)
    )


def _emit_tick_transition(
    writer: EventWriter,
    *,
    projection: dict[str, Any],
    causation_id: str,
    actions_applied: int,
    actions_blocked: int,
    actions_failed: int,
    repairs_dispatched: int,
    repair_closeouts: int,
    autoresearch_requested: int,
    reflect_requested: int,
    reflect_completed: int,
    autoresearch_consumed: int,
    applied_safe_actions: list[str],
    closeout_events: int = 0,
) -> ZfEvent | None:
    transition = _transition_name(
        projection=projection,
        actions_applied=actions_applied,
        actions_blocked=actions_blocked,
        actions_failed=actions_failed,
        repairs_dispatched=repairs_dispatched,
        repair_closeouts=repair_closeouts,
        autoresearch_requested=autoresearch_requested,
        reflect_requested=reflect_requested,
        reflect_completed=reflect_completed,
        autoresearch_consumed=autoresearch_consumed,
        applied_safe_actions=applied_safe_actions,
        closeout_events=closeout_events,
    )
    if transition == "continue_waiting":
        return None
    return writer.emit(
        RUN_MANAGER_TRANSITION,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.transition.v1",
            "transition": transition,
            "projection_ref": "projections/run_manager.json",
            "actions_applied": actions_applied,
            "actions_blocked": actions_blocked,
            "actions_failed": actions_failed,
            "repairs_dispatched": repairs_dispatched,
            "repair_closeouts": repair_closeouts,
            "autoresearch_requested": autoresearch_requested,
            "reflect_requested": reflect_requested,
            "reflect_completed": reflect_completed,
            "autoresearch_consumed": autoresearch_consumed,
            "closeout_events": closeout_events,
            "pending_actions": int(
                (projection.get("summary") or {}).get("pending_actions") or 0
            ),
            "goal_status": str((projection.get("goal") or {}).get("status") or "unknown"),
            "monitor_state": str((projection.get("monitor") or {}).get("state") or "unknown"),
        },
    )


def _transition_name(
    *,
    projection: dict[str, Any],
    actions_applied: int,
    actions_blocked: int,
    actions_failed: int,
    repairs_dispatched: int,
    repair_closeouts: int,
    autoresearch_requested: int,
    reflect_requested: int,
    reflect_completed: int,
    autoresearch_consumed: int,
    applied_safe_actions: list[str],
    closeout_events: int = 0,
) -> str:
    if closeout_events:
        return "terminal_closeout"
    if actions_applied:
        if "resident_agent_reprompt" in applied_safe_actions:
            return "resident_agent_reprompt"
        if "reemit_candidate_ready" in applied_safe_actions:
            return "reemit_candidate_ready"
        if any(action.startswith("needs_") for action in applied_safe_actions):
            return "apply_task_resume"
        if "worker_lifecycle_recover" in applied_safe_actions:
            return "recover_worker_lifecycle"
        return "apply_batch_resume"
    if autoresearch_requested:
        return "invoke_autoresearch"
    if reflect_requested or reflect_completed:
        return "invoke_reflect"
    if repairs_dispatched:
        return "dispatch_repair_worker"
    if repair_closeouts:
        return "repair_closeout"
    if autoresearch_consumed:
        return "consume_autoresearch_result"
    if actions_blocked:
        return "escalate_human"
    if actions_failed:
        return "terminal_blocked"
    completion = projection.get("completion_profile")
    if isinstance(completion, dict) and completion.get("status") == "blocked":
        return "terminal_blocked"
    if isinstance(completion, dict) and completion.get("status") == "complete":
        return "terminal_success"
    if str((projection.get("goal") or {}).get("status") or "") == "complete":
        return "terminal_success"
    return "continue_waiting"


def _emit_run_completed_closeout_if_ready(
    writer: EventWriter,
    *,
    projection: dict[str, Any],
    events: list[ZfEvent],
    config: ZfConfig,
    causation_id: str,
) -> int:
    completion = projection.get("completion_profile")
    if not isinstance(completion, dict):
        return 0
    if completion.get("status") != "complete":
        return 0
    terminal = completion.get("terminal_signal")
    terminal = terminal if isinstance(terminal, dict) else {}
    terminal_type = str(terminal.get("event_type") or "")
    if terminal_type == RUN_COMPLETED:
        return 0
    if terminal_type in {"ship.completed", "ship.done"}:
        return 0
    if not terminal_type:
        return 0
    git_cfg = getattr(getattr(config, "runtime", None), "git", None)
    auto_ship_candidate = bool(
        getattr(git_cfg, "auto_ship_on_candidate_complete", False)
    )
    auto_ship_judge = bool(getattr(git_cfg, "auto_ship_on_judge_passed", False))
    if auto_ship_candidate or auto_ship_judge:
        return 0
    if _current_run_completed_closeout(events) is not None:
        return 0
    source_event = _event_by_id(events, str(terminal.get("event_id") or ""))
    source_payload = (
        source_event.payload
        if source_event is not None and isinstance(source_event.payload, dict)
        else {}
    )
    goal = projection.get("goal")
    goal = goal if isinstance(goal, dict) else {}
    payload = {
        "schema_version": "run-completed-closeout.v1",
        "status": "passed",
        "completion_status": "complete",
        "release_status": "not_shipped",
        "ship_status": "not_requested",
        "reason": "quality gates completed and auto ship is disabled",
        "run_id": str(
            source_payload.get("run_id")
            or goal.get("run_id")
            or source_payload.get("pdd_id")
            or (
                source_event.correlation_id
                if source_event is not None else ""
            )
        ),
        "pdd_id": str(source_payload.get("pdd_id") or ""),
        "trace_id": str(source_payload.get("trace_id") or ""),
        "candidate_ref": str(
            source_payload.get("candidate_ref")
            or source_payload.get("target_ref")
            or source_payload.get("candidate_branch")
            or ""
        ),
        "candidate_head_commit": str(
            source_payload.get("candidate_head_commit")
            or source_payload.get("head")
            or source_payload.get("source_commit")
            or ""
        ),
        "terminal_signal": terminal,
        "auto_ship_on_candidate_complete": auto_ship_candidate,
        "auto_ship_on_judge_passed": auto_ship_judge,
        "projection_ref": "projections/run_manager.json",
    }
    writer.emit(
        RUN_COMPLETED,
        actor="run-manager",
        causation_id=causation_id,
        correlation_id=source_event.correlation_id if source_event is not None else None,
        payload=payload,
    )
    return 1


def _execute_controlled_run_action(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    action_name = str(action.get("action") or "")
    if action_name == "workflow-batch-resume":
        return _execute_workflow_batch_resume(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            action=action,
            causation_id=causation_id,
        )
    if action_name == "workflow-task-resume":
        return _execute_workflow_batch_resume(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            action=action,
            causation_id=causation_id,
        )
    if action_name == "worker-lifecycle-recover":
        return _execute_worker_lifecycle_recover(
            writer=writer,
            action=action,
            causation_id=causation_id,
        )
    if action_name == "repair-closeout-validate":
        return _execute_repair_closeout_validate(
            writer=writer,
            action=action,
            causation_id=causation_id,
        )
    if action_name == "resident-agent-reprompt":
        return _execute_resident_agent_reprompt(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            action=action,
            causation_id=causation_id,
        )
    if action_name == "candidate-rework-apply":
        return _execute_candidate_rework_apply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            action=action,
            causation_id=causation_id,
        )
    _emit_blocked_action(
        writer,
        action,
        causation_id=causation_id,
        reason=f"unsupported run manager action {action_name!r}",
        human=False,
    )
    return "blocked"


def _execute_workflow_batch_resume(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    from zf.runtime.control_actions import ControlledActionService

    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
        },
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor="run-manager",
        source="run-manager",
        surface="run-manager",
    )
    try:
        requested_action = str(action.get("action") or "workflow-batch-resume")
        result = service.execute(
            action="workflow-batch-resume",
            requested_action=requested_action,
            payload={
                "checkpoint_id": str(action.get("checkpoint_id") or ""),
                "safe_resume_action": str(action.get("safe_resume_action") or ""),
                "override_task_map_ref": str(action.get("override_task_map_ref") or ""),
                "mutating_resume_supported": bool(action.get("mutating_resume_supported")),
            },
            requested=planned,
        )
    except Exception as exc:
        writer.emit(
            RUN_MANAGER_ACTION_FAILED,
            actor="run-manager",
            causation_id=planned.id,
            payload={
                "schema_version": "run-manager.action.v1",
                **_action_payload(action),
                "status": "failed",
                "reason": str(exc),
            },
        )
        return "failed"
    status = str(result.get("status") or "")
    ok = bool(result.get("ok"))
    event_type = RUN_MANAGER_ACTION_APPLIED if ok else RUN_MANAGER_ACTION_FAILED
    if status == "blocked":
        event_type = RUN_MANAGER_ACTION_BLOCKED
    outcome = writer.emit(
        event_type,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": status,
            "controlled_action_result": result.get("action_result", {}),
            "event_id": str(result.get("event_id") or ""),
            "reason": str(result.get("reason") or ""),
        },
    )
    _post_verify_action(writer, action, result, causation_id=outcome.id)
    if event_type == RUN_MANAGER_ACTION_APPLIED:
        return "applied" if status != "no_op" else "no_op"
    return "blocked" if event_type == RUN_MANAGER_ACTION_BLOCKED else "failed"


def _execute_worker_lifecycle_recover(
    *,
    writer: EventWriter,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
        },
    )
    instance_id = str(action.get("instance_id") or action.get("role_instance") or "")
    if not instance_id:
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason="worker lifecycle recovery requires instance_id",
            human=False,
        )
        return "blocked"
    requested = writer.emit(
        "worker.respawn.requested",
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.worker-lifecycle-recover.v1",
            "instance_id": instance_id,
            "role_instance": instance_id,
            "task_id": str(action.get("task_id") or ""),
            "briefing_ref": str(action.get("briefing_ref") or ""),
            "reason": str(action.get("reason") or "run manager worker lifecycle recovery"),
            "checkpoint_id": str(action.get("checkpoint_id") or ""),
            "source_event_ids": [
                str(value) for value in action.get("source_event_ids") or []
                if str(value).strip()
            ],
        },
    )
    outcome = writer.emit(
        RUN_MANAGER_ACTION_APPLIED,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied",
            "event_id": requested.id,
            "reason": "worker respawn requested",
        },
    )
    _post_verify_action(
        writer,
        action,
        {"ok": True, "status": "applied", "emitted_event_ids": [requested.id]},
        causation_id=outcome.id,
    )
    return "applied"


def _execute_candidate_rework_apply(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    from zf.runtime.control_actions import ControlledActionService

    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
        },
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor="run-manager",
        source="run-manager",
        surface="run-manager",
    )
    try:
        result = service.execute(
            action="candidate-rework-apply",
            requested_action="candidate-rework-apply",
            payload=action,
            requested=planned,
        )
    except Exception as exc:
        writer.emit(
            RUN_MANAGER_ACTION_FAILED,
            actor="run-manager",
            causation_id=planned.id,
            payload={
                "schema_version": "run-manager.action.v1",
                **_action_payload(action),
                "status": "failed",
                "reason": str(exc),
            },
        )
        return "failed"
    status = str(result.get("status") or "")
    ok = bool(result.get("ok"))
    event_type = RUN_MANAGER_ACTION_APPLIED if ok else RUN_MANAGER_ACTION_FAILED
    if status == "blocked":
        event_type = RUN_MANAGER_ACTION_BLOCKED
    outcome = writer.emit(
        event_type,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": status,
            "controlled_action_result": result.get("action_result", {}),
            "event_id": str(result.get("event_id") or ""),
            "reason": str(result.get("reason") or ""),
        },
    )
    _post_verify_action(writer, action, result, causation_id=outcome.id)
    if event_type == RUN_MANAGER_ACTION_APPLIED:
        return "applied" if status != "no_op" else "no_op"
    return "blocked" if event_type == RUN_MANAGER_ACTION_BLOCKED else "failed"


def _execute_repair_closeout_validate(
    *,
    writer: EventWriter,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    from zf.runtime.run_manager_repair_validation import execute_repair_verification_plan

    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
        },
    )
    result = execute_repair_verification_plan(
        worktree=str(action.get("worktree_path") or action.get("worktree") or ""),
        verification_plan=list(action.get("verification_plan") or []),
    )
    ok = bool(result.get("ok"))
    event_type = RUN_MANAGER_ACTION_APPLIED if ok else RUN_MANAGER_ACTION_FAILED
    outcome = writer.emit(
        event_type,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied" if ok else "failed",
            "event_id": planned.id,
            "reason": str(result.get("reason") or ""),
            "validation_result": result,
        },
    )
    _post_verify_action(
        writer,
        action,
        {"ok": ok, "status": "applied" if ok else "failed", "emitted_event_ids": [outcome.id]},
        causation_id=outcome.id,
    )
    return "applied" if ok else "failed"


def _execute_resident_agent_reprompt(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
        },
    )
    briefing_path = _resident_briefing_path(state_dir, action)
    if briefing_path is None or not briefing_path.exists():
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason="resident briefing path is missing",
        )
        return "blocked"
    tmux_session = str(action.get("tmux_session") or "").strip()
    instance_id = str(action.get("instance_id") or action.get("role_instance") or "run-manager")
    if not tmux_session or not instance_id:
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason="resident tmux target is incomplete",
        )
        return "blocked"
    target = f"{tmux_session}:{instance_id}"
    pane = _resident_pane_display(target)
    if not pane.get("ok"):
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason=str(pane.get("reason") or "resident pane is not reachable"),
        )
        return "blocked"
    current_command = str(pane.get("current_command") or "").strip().lower()
    if current_command in {"bash", "sh", "zsh", "fish", "tmux"}:
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason=f"resident pane current command is not an agent: {current_command}",
        )
        return "blocked"
    current_path = str(pane.get("current_path") or "")
    if project_root is not None and current_path and not _path_under(current_path, project_root):
        _emit_blocked_action(
            writer,
            action,
            causation_id=planned.id,
            reason="resident pane cwd is outside project root",
        )
        return "blocked"
    briefing_refreshed = _refresh_resident_briefing(
        briefing_path=briefing_path,
        state_dir=state_dir,
        project_root=project_root,
        config=config,
    )
    prompt = briefing_path.read_text(encoding="utf-8")
    focus = _resident_action_focus_prompt(action)
    if focus:
        prompt = prompt.rstrip() + "\n\n" + focus + "\n"
    sent = _send_tmux_prompt(target, prompt)
    if not sent.get("ok"):
        writer.emit(
            RUN_MANAGER_ACTION_FAILED,
            actor="run-manager",
            causation_id=planned.id,
            payload={
                "schema_version": "run-manager.action.v1",
                **_action_payload(action),
                "status": "failed",
                "reason": str(sent.get("reason") or "tmux send failed"),
            },
        )
        return "failed"
    prompted = writer.emit(
        RUN_MANAGER_RESIDENT_PROMPTED,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.resident.v1",
            "instance_id": instance_id,
            "briefing_path": str(briefing_path),
            "prompted": True,
            "reprompt": True,
            "tmux_session": tmux_session,
            "target": target,
            "source_action_checkpoint_id": str(action.get("checkpoint_id") or ""),
            "briefing_refreshed": briefing_refreshed,
        },
    )
    outcome = writer.emit(
        RUN_MANAGER_ACTION_APPLIED,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied",
            "event_id": planned.id,
            "emitted_event_ids": [prompted.id],
            "target": target,
        },
    )
    _post_verify_action(
        writer,
        action,
        {"ok": True, "status": "applied", "emitted_event_ids": [prompted.id]},
        causation_id=outcome.id,
    )
    return "applied"


def _refresh_resident_briefing(
    *,
    briefing_path: Path,
    state_dir: Path,
    project_root: Path | None,
    config: ZfConfig,
) -> bool:
    if project_root is None:
        return False
    try:
        from zf.runtime.run_manager_resident import (
            build_resident_run_manager_briefing,
            build_resident_run_manager_role,
        )

        role = build_resident_run_manager_role(config)
        if role is None:
            return False
        prompt = build_resident_run_manager_briefing(
            project_root=project_root,
            state_dir=state_dir,
            role=role,
        )
        atomic_write_text(briefing_path, prompt)
        return True
    except Exception:
        return False


def _resident_briefing_path(
    state_dir: Path,
    action: dict[str, Any],
) -> Path | None:
    raw = str(action.get("briefing_path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(state_dir) / path
    return path


def _resident_action_focus_prompt(action: dict[str, Any]) -> str:
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    failure_class = str(action.get("failure_class") or "").strip()
    title = str(action.get("title") or "").strip()
    summary = str(action.get("summary") or "").strip()
    source_ref = str(action.get("source_ref") or "").strip()
    if not any((checkpoint_id, failure_class, title, summary, source_ref)):
        return ""
    recommended = _string_list(action.get("recommended_actions"))
    expected = _string_list(action.get("expected_output"))
    lines = [
        "## Current Focus",
        "",
        f"- checkpoint_id: `{checkpoint_id}`",
        f"- failure_class: `{failure_class}`",
        f"- source_action: `{str(action.get('diagnosis_source_action') or action.get('action') or '')}`",
    ]
    if title:
        lines.append(f"- title: {title}")
    if summary:
        lines.append(f"- summary: {summary}")
    if source_ref:
        lines.append(f"- source_ref: `{source_ref}`")
    if recommended:
        lines.append("- recommended_actions: " + ", ".join(recommended))
    if expected:
        lines.append("- expected_output: " + ", ".join(expected))
    lines.extend([
        "",
        "请优先围绕 Current Focus 做一次观察,输出 "
        "`run.manager.agent.observation` 或 `run.manager.agent.recommendation`。",
    ])
    return "\n".join(lines)


def _resident_pane_display(target: str) -> dict[str, Any]:
    try:
        from zf.runtime.tmux import tmux_env

        proc = subprocess.run(
            [
                "tmux", "display-message",
                "-p", "-t", target,
                "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_dead}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=tmux_env(),
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "reason": (proc.stderr or proc.stdout or "").strip()[:240]}
    pane_id, current_command, current_path, pane_dead = _split_tmux_display(proc.stdout)
    if pane_dead == "1":
        return {
            "ok": False,
            "reason": "resident pane is dead",
            "pane": pane_id,
            "current_command": current_command,
            "current_path": current_path,
        }
    return {
        "ok": True,
        "pane": pane_id,
        "current_command": current_command,
        "current_path": current_path,
    }


def _send_tmux_prompt(target: str, prompt: str) -> dict[str, Any]:
    try:
        from zf.runtime.tmux import tmux_env

        env = tmux_env()
        paste = subprocess.run(
            ["tmux", "send-keys", "-t", target, prompt],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
        if paste.returncode != 0:
            return {"ok": False, "reason": (paste.stderr or paste.stdout or "").strip()[:240]}
        time.sleep(0.5)
        submit = subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
        if submit.returncode != 0:
            return {"ok": False, "reason": (submit.stderr or submit.stdout or "").strip()[:240]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True}


def _split_tmux_display(value: str) -> tuple[str, str, str, str]:
    parts = (value or "").strip().split("\t", 3)
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def _path_under(raw_path: str, root: Path) -> bool:
    try:
        Path(raw_path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except Exception:
        return False


def _post_verify_action(
    writer: EventWriter,
    action: dict[str, Any],
    result: dict[str, Any],
    *,
    causation_id: str,
) -> None:
    expected = {
        str(value) for value in action.get("expected_downstream_events") or []
        if str(value).strip()
    }
    if not expected:
        expected = _expected_downstream_events(str(action.get("safe_resume_action") or ""))
    emitted = _emitted_event_types(writer.event_log, _collect_emitted_event_ids(result))
    passed = bool(expected.intersection(emitted))
    writer.emit(
        RUN_MANAGER_ACTION_VERIFY_PASSED if passed else RUN_MANAGER_ACTION_VERIFY_FAILED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.verify.v1",
            **_action_payload(action),
            "expected_event_types": sorted(expected),
            "observed_event_types": sorted(emitted),
            "status": "passed" if passed else "failed",
            "reason": "" if passed else "expected downstream event not observed",
        },
    )


def _accept_autoresearch_repairs(events: list[ZfEvent], writer: EventWriter) -> int:
    accepted_keys = {
        _repair_key(event.payload if isinstance(event.payload, dict) else {})
        for event in events
        if event.type in _RUN_MANAGER_REPAIR_TERMINALS
    }
    count = 0
    for req in pending_repair_dispatches(events):
        key = (req.fingerprint, req.attempt)
        if key in accepted_keys:
            continue
        writer.emit(
            RUN_MANAGER_REPAIR_ACCEPTED,
            actor="run-manager",
            causation_id=req.event_id or None,
            payload={
                "schema_version": "run-manager.repair.v1",
                "fingerprint": req.fingerprint,
                "attempt": req.attempt,
                "candidate_id": req.candidate_id,
                "candidate_path": req.candidate_path,
                "repair_task_payload": req.repair_task_payload,
                "source_event_id": req.event_id,
                "decision": "accepted",
                "executor": "self_repair_runner",
            },
        )
        count += 1
    return count


def _consume_autoresearch_results(events: list[ZfEvent], writer: EventWriter) -> int:
    requests: dict[str, ZfEvent] = {}
    for event in events:
        if event.type != RUN_MANAGER_AUTORESEARCH_REQUESTED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = _autoresearch_request_id(event, payload)
        requests[request_id] = event
    if not requests:
        return 0

    consumed_event_ids = {
        str((event.payload or {}).get("source_event_id") or "")
        for event in events
        if event.type == RUN_MANAGER_AUTORESEARCH_CONSUMED
        and isinstance(event.payload, dict)
    }
    consumed_request_ids = {
        str((event.payload or {}).get("request_id") or "")
        for event in events
        if event.type == RUN_MANAGER_AUTORESEARCH_CONSUMED
        and isinstance(event.payload, dict)
    }
    count = 0
    for event in events:
        if event.type not in {"autoresearch.loop.completed", "autoresearch.loop.failed"}:
            continue
        if event.id in consumed_event_ids:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = _autoresearch_result_request_id(event, payload)
        if not request_id or request_id not in requests or request_id in consumed_request_ids:
            continue
        status = "failed" if event.type.endswith(".failed") else "completed"
        writer.emit(
            RUN_MANAGER_AUTORESEARCH_CONSUMED,
            actor="run-manager",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload={
                "schema_version": "run-manager.autoresearch-consumed.v1",
                "request_id": request_id,
                "source_event_id": event.id,
                "source_event_type": event.type,
                "status": status,
                "fingerprint": _fingerprint(payload, fallback=request_id),
                "proposal_refs": _autoresearch_proposal_refs(payload),
                "next_route": _autoresearch_next_route(status, payload),
            },
        )
        consumed_request_ids.add(request_id)
        count += 1
    return count


def _consume_human_decisions(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    events: list[ZfEvent],
    causation_id: str,
) -> tuple[int, int]:
    applied = 0
    rejected = 0
    escalations = _human_escalations_by_token(events)
    seen = _seen_human_decisions(events)
    for event in events:
        if event.type != HUMAN_ESCALATION_ACKNOWLEDGED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        token = _human_decision_event_token(event, payload)
        if not token or token in seen or event.id in seen:
            continue
        escalation = escalations.get(token, {})
        action = _human_decision_action(payload, escalation)
        decision = str(payload.get("decision") or payload.get("resolution") or "acknowledged")
        if decision == "approve_controlled_action":
            action["mutating_resume_supported"] = True
            status = _execute_workflow_batch_resume(
                state_dir=state_dir,
                writer=writer,
                config=config,
                project_root=project_root,
                action=action,
                causation_id=event.id,
            )
            writer.emit(
                RUN_MANAGER_HUMAN_DECISION_APPLIED,
                actor="run-manager",
                task_id=event.task_id,
                causation_id=event.id,
                correlation_id=event.correlation_id,
                payload={
                    "schema_version": "run-manager.human-decision.v1",
                    "decision_token": token,
                    "source_event_id": event.id,
                    "source_escalation_event_id": str(escalation.get("event_id") or ""),
                    "decision": decision,
                    "next_route": "controlled_action",
                    "controlled_action_status": status,
                    **_action_payload(action),
                },
            )
            applied += 1
            seen.add(token)
            seen.add(event.id)
            continue
        if decision == "request_autoresearch":
            requested = _emit_autoresearch_request(
                state_dir=state_dir,
                writer=writer,
                action=action,
                projection={},
                project_root=project_root,
                causation_id=event.id,
            )
            writer.emit(
                RUN_MANAGER_HUMAN_DECISION_APPLIED,
                actor="run-manager",
                task_id=event.task_id,
                causation_id=event.id,
                correlation_id=event.correlation_id,
                payload={
                    "schema_version": "run-manager.human-decision.v1",
                    "decision_token": token,
                    "source_event_id": event.id,
                    "source_escalation_event_id": str(escalation.get("event_id") or ""),
                    "decision": decision,
                    "next_route": "autoresearch",
                    "autoresearch_requested": requested,
                    **_action_payload(action),
                },
            )
            applied += 1
            seen.add(token)
            seen.add(event.id)
            continue
        writer.emit(
            RUN_MANAGER_HUMAN_DECISION_REJECTED,
            actor="run-manager",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload={
                "schema_version": "run-manager.human-decision.v1",
                "decision_token": token,
                "source_event_id": event.id,
                "source_escalation_event_id": str(escalation.get("event_id") or ""),
                "decision": decision,
                "next_route": "safe_halt" if decision == "safe_halt" else "none",
                "reason": str(payload.get("reason") or ""),
                **_action_payload(action),
            },
        )
        rejected += 1
        seen.add(token)
        seen.add(event.id)
    return applied, rejected


def _emit_blocked_action(
    writer: EventWriter,
    action: dict[str, Any],
    *,
    causation_id: str,
    reason: str,
    human: bool = False,
) -> None:
    blocked = writer.emit(
        RUN_MANAGER_ACTION_BLOCKED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "blocked",
            "reason": reason,
        },
    )
    if human:
        emit_human_escalation_package(
            writer,
            action=action,
            reason=reason,
            causation_id=blocked.id,
        )


def _pending_workflow_task_actions(
    projection: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for checkpoint in projection.get("checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        safe_action = str(checkpoint.get("safe_resume_action") or "")
        if not safe_action or safe_action == "no_action":
            continue
        checkpoint_id = str(checkpoint.get("idempotency_key") or "")
        action = {
            "schema_version": "run-manager.pending-action.v1",
            "action": "workflow-task-resume",
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action,
            "task_id": str(checkpoint.get("task_id") or ""),
            "stage_id": str(checkpoint.get("expected_next_stage") or ""),
            "expected_next_stage": str(checkpoint.get("expected_next_stage") or ""),
            "expected_next_role": str(checkpoint.get("expected_next_role") or ""),
            "source_event_id": str(checkpoint.get("last_trusted_event_id") or ""),
            "source_event_type": str(checkpoint.get("source_event_type") or ""),
            "blocking_event_id": str(checkpoint.get("blocking_event_id") or ""),
            "source_event_ids": [
                str(value) for value in checkpoint.get("evidence_event_ids") or []
                if str(value).strip()
            ],
            "reason": str(checkpoint.get("reason") or ""),
        }
        if _action_seen(events, action):
            continue
        action.update(classify_recovery_context(action))
        action["preflight"] = preflight_action(
            action="workflow-task-resume",
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action="workflow-task-resume",
            payload=action,
        )
        out.append(action)
    return out


def _pending_workflow_batch_actions(
    projection: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for checkpoint in projection.get("batch_checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        safe_action = str(checkpoint.get("safe_resume_action") or "")
        if not safe_action or safe_action == "no_action":
            continue
        action = {
            "schema_version": "run-manager.pending-action.v1",
            "action": "workflow-batch-resume",
            "checkpoint_id": str(checkpoint.get("checkpoint_id") or ""),
            "source_event_id": str(checkpoint.get("source_event_id") or ""),
            "source_event_type": str(checkpoint.get("source_event_type") or ""),
            "safe_resume_action": safe_action,
            "fanout_id": str(checkpoint.get("fanout_id") or ""),
            "pdd_id": str(checkpoint.get("pdd_id") or ""),
            "feature_id": str(checkpoint.get("feature_id") or ""),
            "stage_id": str(checkpoint.get("stage_id") or ""),
            "trace_id": str(checkpoint.get("trace_id") or ""),
            "task_map_ref": str(checkpoint.get("task_map_ref") or ""),
            "source_index_ref": str(checkpoint.get("source_index_ref") or ""),
            "source_commit": str(checkpoint.get("source_commit") or ""),
            "target_ref": str(checkpoint.get("target_ref") or ""),
            "candidate_base_commit": str(checkpoint.get("candidate_base_commit") or ""),
            "diff_ref": str(checkpoint.get("diff_ref") or ""),
            "upstream_fanout_id": str(checkpoint.get("upstream_fanout_id") or ""),
            "failed_children": [
                str(value) for value in checkpoint.get("failed_children") or []
            ],
            "completed_task_ids": [
                str(value) for value in checkpoint.get("completed_task_ids") or []
            ],
            "pending_children": [
                str(value) for value in checkpoint.get("pending_children") or []
            ],
            "candidate_ref": str(checkpoint.get("candidate_ref") or ""),
            "candidate_head_commit": str(checkpoint.get("candidate_head_commit") or ""),
            "source_event_ids": [
                str(value) for value in checkpoint.get("evidence_event_ids") or []
            ],
            "reason": str(checkpoint.get("reason") or ""),
            "escalated": bool(checkpoint.get("escalated")),
            "mutating_resume_supported": bool(checkpoint.get("mutating_resume_supported")),
        }
        if _action_seen(events, action):
            continue
        action.update(classify_recovery_context(action))
        action["preflight"] = preflight_action(
            action="workflow-batch-resume",
            payload=action,
            mutating_resume_supported=bool(action.get("mutating_resume_supported")),
        )
        action["policy_decision"] = decide_action_policy(
            action="workflow-batch-resume",
            payload=action,
            mutating_resume_supported=bool(action.get("mutating_resume_supported")),
        )
        out.append(action)
    return out


def _pending_worker_lifecycle_actions(
    state_dir: Path,
    projection: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    registry = projection.get("worker_registry")
    registry = registry if isinstance(registry, dict) else {}
    stale_workers = registry.get("stale")
    rows = stale_workers if isinstance(stale_workers, list) else []
    if not rows:
        return []
    meta_by_instance = _role_session_meta(state_dir)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("instance_id") or "")
        if not instance_id:
            continue
        meta = meta_by_instance.get(instance_id, {})
        heartbeat = meta.get("last_heartbeat_payload")
        heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
        task_id = str(
            row.get("task_id")
            or meta.get("current_task_id")
            or heartbeat.get("current_task_id")
            or heartbeat.get("task_id")
            or ""
        )
        briefing_ref = str(
            row.get("briefing_ref")
            or meta.get("briefing_ref")
            or meta.get("current_briefing_ref")
            or heartbeat.get("briefing_ref")
            or ""
        )
        reason = _worker_lifecycle_reason(row)
        source_event_ids = _worker_lifecycle_source_events(
            events,
            instance_id=instance_id,
            task_id=task_id,
        )
        action = {
            "schema_version": "run-manager.pending-action.v1",
            "action": "worker-lifecycle-recover",
            "checkpoint_id": _worker_lifecycle_checkpoint_id(
                instance_id,
                task_id,
                reason,
            ),
            "safe_resume_action": "worker_lifecycle_recover",
            "instance_id": instance_id,
            "role_instance": instance_id,
            "task_id": task_id,
            "briefing_ref": briefing_ref,
            "source_event_ids": source_event_ids,
            "reason": reason,
            "failure_class": "worker_lifecycle_stale",
            "owner_route": "controlled_action",
            "action_policy": "auto_decide",
            "intervention_class": "auto_recover",
            "expected_downstream_events": sorted(
                _expected_downstream_events("worker_lifecycle_recover")
            ),
            "verify_condition": "expected_downstream_event:worker.respawn.requested",
            "route_registry": "run-manager-router.v1",
        }
        if _action_seen(events, action):
            continue
        action["preflight"] = preflight_action(
            action="worker-lifecycle-recover",
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action="worker-lifecycle-recover",
            payload=action,
        )
        out.append(action)
    return out


def _pending_repair_validation_actions(
    repair_merge_queue: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    items = repair_merge_queue.get("items") if isinstance(repair_merge_queue, dict) else []
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "closeout_required":
            continue
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        if str(validation.get("status") or "") in {"passed", "failed"}:
            continue
        plan = item.get("verification_plan")
        if not isinstance(plan, list) or not plan:
            continue
        action = {
            "schema_version": "run-manager.pending-action.v1",
            "action": "repair-closeout-validate",
            "checkpoint_id": _repair_validation_checkpoint_id(item),
            "safe_resume_action": "repair_closeout_validate",
            "queue_id": str(item.get("queue_id") or ""),
            "fingerprint": str(item.get("fingerprint") or ""),
            "candidate_id": str(item.get("candidate_id") or ""),
            "branch": str(item.get("branch") or ""),
            "worktree_path": str(item.get("worktree_path") or ""),
            "source_commit": str(item.get("source_commit") or ""),
            "source_title": str(item.get("source_title") or ""),
            "risk_classification": item.get("risk_classification")
            if isinstance(item.get("risk_classification"), dict) else {},
            "verification_plan": plan,
            "continuation": item.get("continuation")
            if isinstance(item.get("continuation"), dict) else {},
            "failure_class": "self_repair_validation",
            "owner_route": "controlled_action",
            "action_policy": "auto_decide",
            "intervention_class": "auto_recover",
            "expected_downstream_events": ["run.manager.action.applied"],
            "verify_condition": "expected_downstream_event:run.manager.action.applied",
            "route_registry": "run-manager-router.v1",
        }
        if _action_seen(events, action):
            continue
        action["preflight"] = preflight_action(
            action="repair-closeout-validate",
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action="repair-closeout-validate",
            payload=action,
        )
        out.append(action)
    return out


def _repair_validation_checkpoint_id(item: dict[str, Any]) -> str:
    raw = "|".join([
        "repair-closeout-validate",
        str(item.get("queue_id") or ""),
        str(item.get("source_commit") or ""),
        str(item.get("worktree_path") or ""),
    ])
    return "repair-validate-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _worker_lifecycle_reason(row: dict[str, Any]) -> str:
    reasons = row.get("reasons")
    parts = []
    for item in reasons if isinstance(reasons, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        field = str(item.get("field") or "")
        value = str(item.get("value") or "")
        parts.append(":".join(part for part in (code, field, value) if part))
    return "; ".join(parts) or "stale worker registry row"


def _worker_lifecycle_source_events(
    events: list[ZfEvent],
    *,
    instance_id: str,
    task_id: str,
) -> list[str]:
    out: list[str] = []
    for event in reversed(events):
        if len(out) >= 5:
            break
        if event.type not in {
            "worker.stuck",
            "worker.probe.silent",
            "worker.respawn.failed",
            "worker.stuck.recovery_failed",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_instance = str(
            payload.get("instance_id")
            or payload.get("role_instance")
            or event.actor
            or ""
        )
        event_task = str(event.task_id or payload.get("task_id") or "")
        if instance_id and event_instance and event_instance != instance_id:
            continue
        if task_id and event_task and event_task != task_id:
            continue
        out.append(event.id)
    return list(reversed(out))


def _worker_lifecycle_checkpoint_id(
    instance_id: str,
    task_id: str,
    reason: str,
) -> str:
    raw = "|".join(["worker-lifecycle", instance_id, task_id, reason])
    return "wlife-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pending_unknown_gap_diagnostic_actions(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    no_progress: dict[str, Any],
    runtime_pane_snapshot: dict[str, Any],
    resident_agent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    item = _unknown_gap_attention_item(
        state_dir,
        events,
        no_progress=no_progress,
        runtime_pane_snapshot=runtime_pane_snapshot,
    )
    if item is None:
        return []
    action = _unknown_gap_diagnostic_action(item, resident_agent=resident_agent)
    if _action_seen(events, action):
        return []
    action_name = str(action.get("action") or "diagnose-attention")
    action["preflight"] = preflight_action(
        action=action_name,
        payload=action,
    )
    action["policy_decision"] = decide_action_policy(
        action=action_name,
        payload=action,
    )
    return [action]


def _pending_human_gate_repair_resume_actions(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    resident_agent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not _unknown_gap_run_is_active(state_dir):
        return []
    for decision in reversed(_pending_human_decisions(events)):
        if not _human_gate_needs_repair_resume(decision):
            continue
        item = _human_gate_repair_resume_item(
            state_dir=state_dir,
            decision=decision,
            events=events,
        )
        action = _unknown_gap_diagnostic_action(item, resident_agent=resident_agent)
        if _action_seen(events, action):
            continue
        action_name = str(action.get("action") or "diagnose-attention")
        action["preflight"] = preflight_action(
            action=action_name,
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action=action_name,
            payload=action,
        )
        return [action]
    return []


def _human_gate_needs_repair_resume(decision: dict[str, Any]) -> bool:
    text = " ".join([
        str(decision.get("reason") or ""),
        " ".join(str(value) for value in decision.get("suggested_options") or []),
        str(decision.get("fingerprint") or ""),
        str(decision.get("checkpoint_id") or ""),
    ]).lower()
    return any(
        marker in text
        for marker in (
            "candidate rework exhausted",
            "reviewer findings unresolved",
            "parity-closure",
            "promote human gate",
            "rework exhausted",
        )
    )


def _human_gate_repair_resume_item(
    *,
    state_dir: Path,
    decision: dict[str, Any],
    events: list[ZfEvent],
) -> dict[str, Any]:
    event_id = str(decision.get("event_id") or "")
    source_event_ids = _unique_event_ids([
        event_id,
        *_latest_failure_event_ids(events),
    ])
    reason = str(decision.get("reason") or "human gate requires repair/resume")
    return _unknown_gap_item(
        state_dir=state_dir,
        kind="human_gate_repair_resume",
        fingerprint=f"human-gate-repair-resume:{event_id or reason}",
        failure_class="candidate_rework_exhausted_unresolved",
        title="Human gate needs repair/resume diagnosis",
        summary=(
            "A candidate-level human gate is pending, but the run is still "
            f"active and the gate reason indicates unresolved repair work: {reason}"
        ),
        source_event_ids=source_event_ids,
        source_ref=(
            f"events.jsonl#{source_event_ids[0]}"
            if source_event_ids else "projections/run_manager.json"
        ),
        suggested_action={
            "kind": "repair_resume_from_human_gate",
            "human_event_id": event_id,
            "reason": reason,
            "decision_token": str(decision.get("decision_token") or ""),
        },
        recommended_actions=[
            "inspect_latest_verify_or_integration_failure",
            "produce_repair_resume_plan",
            "apply_controlled_resume_or_project_repair",
            "re-run_candidate_verification",
        ],
    )


def _latest_failure_event_ids(events: list[ZfEvent], *, limit: int = 4) -> list[str]:
    out: list[str] = []
    for event in reversed(events):
        if event.type not in {
            "verify.failed",
            "integration.failed",
            "candidate.quality.failed",
            "fanout.aggregate.completed",
        }:
            continue
        if event.type == "fanout.aggregate.completed":
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("status") or "") != "failed":
                continue
        out.append(event.id)
        if len(out) >= limit:
            break
    return list(reversed(out))


def _unique_event_ids(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        event_id = str(value or "").strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        out.append(event_id)
    return out


def _unknown_gap_attention_item(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    no_progress: dict[str, Any],
    runtime_pane_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    no_progress_items = no_progress.get("items") if isinstance(no_progress, dict) else []
    if str(no_progress.get("status") if isinstance(no_progress, dict) else "") == "tripped":
        first = next((item for item in no_progress_items if isinstance(item, dict)), {})
        fingerprint = str(first.get("fingerprint") or "no-progress")
        source_event_ids = [
            str(item.get("event_id") or "")
            for item in no_progress_items
            if isinstance(item, dict) and str(item.get("event_id") or "")
        ][:10]
        return _unknown_gap_item(
            state_dir=state_dir,
            kind="no_progress_breaker",
            fingerprint=f"unknown-gap:no-progress:{fingerprint}",
            failure_class="no_progress_breaker",
            title="Run Manager no-progress breaker needs diagnosis",
            summary=(
                "No known recovery action is pending, but no-progress "
                "projection has tripped for a repeated failure fingerprint."
            ),
            source_event_ids=source_event_ids,
            source_ref=(
                f"events.jsonl#{source_event_ids[-1]}"
                if source_event_ids else "projections/run_manager_no_progress.json"
            ),
            suggested_action={
                "kind": "diagnose_no_progress_breaker",
                "no_progress_items": no_progress_items,
            },
            recommended_actions=[
                "inspect_no_progress_fingerprint",
                "classify_missing_recovery_route",
                "propose_controlled_action_or_repair",
            ],
        )

    if not _unknown_gap_run_is_active(state_dir):
        return None

    pane_summary = runtime_pane_snapshot.get("summary")
    pane_summary = pane_summary if isinstance(pane_summary, dict) else {}
    expected = _safe_int(pane_summary.get("expected"))
    observed = _safe_int(pane_summary.get("observed"))
    missing = _safe_int(pane_summary.get("missing"))
    if expected > 0 and observed == 0 and missing >= expected:
        return _unknown_gap_item(
            state_dir=state_dir,
            kind="all_panes_missing",
            fingerprint=f"unknown-gap:all-panes-missing:{expected}",
            failure_class="unknown_runtime_gap",
            title="Active run has no observed worker panes",
            summary=(
                f"Runtime expects {expected} tmux pane(s), but none are "
                "observed and no known Run Manager action is pending."
            ),
            source_event_ids=_tail_event_ids(events),
            source_ref="projections/runtime_pane_snapshot.json",
            suggested_action={
                "kind": "diagnose_missing_worker_panes",
                "expected_panes": expected,
                "observed_panes": observed,
                "missing_panes": missing,
            },
            recommended_actions=[
                "inspect_runtime_pane_snapshot",
                "verify_zf_start_loop_is_alive",
                "restart_missing_tmux_workers_if_safe",
                "resume_from_checkpoint",
            ],
        )

    if not events:
        return _unknown_gap_item(
            state_dir=state_dir,
            kind="active_run_no_events",
            fingerprint="unknown-gap:active-run-no-events",
            failure_class="unknown_runtime_gap",
            title="Active run has no events",
            summary=(
                "Session or kanban indicates active work, but events.jsonl "
                "has no events and no known Run Manager action is pending."
            ),
            source_event_ids=[],
            source_ref="events.jsonl",
            suggested_action={
                "kind": "diagnose_active_run_without_events",
                "runtime_state": _runtime_state(state_dir),
            },
            recommended_actions=[
                "verify_entry_trigger_was_emitted",
                "inspect_zf_start_process",
                "replay_missing_kickoff_if_contract_allows",
            ],
        )
    return None


def _unknown_gap_item(
    *,
    state_dir: Path,
    kind: str,
    fingerprint: str,
    failure_class: str,
    title: str,
    summary: str,
    source_event_ids: list[str],
    source_ref: str,
    suggested_action: dict[str, Any],
    recommended_actions: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "attention-item.v0",
        "attention_id": "unknown-gap-" + hashlib.sha1(
            fingerprint.encode("utf-8")
        ).hexdigest()[:12],
        "source": "run_manager_unknown_gap",
        "kind": kind,
        "fingerprint": fingerprint,
        "failure_class": failure_class,
        "severity": "high",
        "status": "open",
        "title": title,
        "summary": summary,
        "task_id": "",
        "source_event_ids": source_event_ids,
        "source_ref": source_ref,
        "suggested_route": "run_manager",
        "suggested_action": suggested_action,
        "recommended_actions": recommended_actions,
        "state_dir": str(state_dir),
    }


def _unknown_gap_diagnostic_action(
    item: dict[str, Any],
    *,
    resident_agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action = _attention_diagnostic_action(item, resident_agent=None)
    action["failure_class"] = str(item.get("failure_class") or "unknown_runtime_gap")
    action["source"] = str(item.get("source") or "run_manager_unknown_gap")
    action["kind"] = str(item.get("kind") or "")
    action["recommended_actions"] = _string_list(item.get("recommended_actions"))
    action["source_ref"] = str(item.get("source_ref") or "projections/run_manager.json")
    action["expected_output"] = [
        "diagnosis_report",
        "root_cause_classification",
        "recommended_run_manager_action",
        "post_verify_downstream_event",
    ]
    return action


def _unknown_gap_run_is_active(state_dir: Path) -> bool:
    if _runtime_state(state_dir) == "active":
        return True
    for task in _read_tasks(state_dir):
        status = str(getattr(task, "status", "") or "")
        if status in {"in_progress", "review", "verify", "test", "running"}:
            return True
    return False


def _runtime_state(state_dir: Path) -> str:
    path = Path(state_dir) / "session.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("runtime_state") or "").strip()


def _tail_event_ids(events: list[ZfEvent], *, limit: int = 5) -> list[str]:
    return [
        event.id for event in events[-limit:]
        if str(getattr(event, "id", "") or "").strip()
    ]


def _pending_candidate_rework_actions(
    state_dir: Path,
    config: ZfConfig,
    events: list[ZfEvent],
    *,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    runtime_events = _candidate_rework_event_window(state_dir, events)
    try:
        from zf.runtime.candidate_rework import plan_candidate_rework

        plans = plan_candidate_rework(runtime_events, max_attempts=2, config=config)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for plan in plans:
        action = _candidate_rework_action_from_plan(
            state_dir,
            runtime_events,
            plan,
            project_root=project_root,
        )
        if _action_seen(events, action):
            continue
        action["preflight"] = preflight_action(
            action="candidate-rework-apply",
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action="candidate-rework-apply",
            payload=action,
        )
        out.append(action)
    return out


def _pending_attention_diagnostic_actions(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    resident_agent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    supervisor = _read_json(state_dir / "projections" / "supervisor" / "snapshot.json")
    items = supervisor.get("attention_items") if isinstance(supervisor, dict) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "open") not in {"", "open", "unacknowledged"}:
            continue
        action = _attention_diagnostic_action(item, resident_agent=resident_agent)
        fingerprint = str(action.get("fingerprint") or "")
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if _action_seen(events, action):
            continue
        action_name = str(action.get("action") or "diagnose-attention")
        action["preflight"] = preflight_action(
            action=action_name,
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action=action_name,
            payload=action,
        )
        out.append(action)
        if len(out) >= 10:
            break
    return out


def _attention_diagnostic_action(
    item: dict[str, Any],
    *,
    resident_agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_event_ids = _string_list(item.get("source_event_ids"))
    if not source_event_ids and str(item.get("source_event_id") or ""):
        source_event_ids = [str(item.get("source_event_id") or "")]
    fingerprint = str(
        item.get("fingerprint")
        or item.get("attention_id")
        or item.get("title")
        or "|".join(source_event_ids)
        or "attention"
    )
    checkpoint_id = "attention-diagnosis-" + hashlib.sha1(
        fingerprint.encode("utf-8")
    ).hexdigest()[:12]
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "diagnose_attention",
        "fingerprint": fingerprint,
        "failure_class": _attention_failure_class(item, fingerprint=fingerprint),
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "verify_condition": (
            "expected_downstream_event:"
            "run.manager.autoresearch.requested,run.manager.resident.prompted"
        ),
        "expected_downstream_events": [
            "run.manager.autoresearch.requested",
            "run.manager.resident.prompted",
        ],
        "attention_id": str(item.get("attention_id") or ""),
        "severity": str(item.get("severity") or ""),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or item.get("message") or ""),
        "task_id": str(item.get("task_id") or ""),
        "fanout_id": str(item.get("fanout_id") or ""),
        "stage_id": str(item.get("stage_id") or ""),
        "lane": str(item.get("lane") or item.get("assignee") or ""),
        "source_event_ids": source_event_ids,
        "source_ref": str(
            item.get("source_ref")
            or (f"events.jsonl#{source_event_ids[0]}" if source_event_ids else "")
        ),
        "suggested_route": str(item.get("suggested_route") or ""),
        "suggested_action": _safe_mapping(item.get("suggested_action")),
        "recommended_actions": _attention_recommended_actions(item, fingerprint=fingerprint),
        "expected_output": [
            "diagnosis_report",
            "recommended_run_manager_action",
            "evidence_refs",
            "expected_downstream_event",
        ],
        "route_registry": "run-manager-router.v1",
    }
    return action


def _attention_failure_class(item: dict[str, Any], *, fingerprint: str) -> str:
    text = " ".join([
        fingerprint,
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("suggested_action") or ""),
    ]).lower()
    if "silent_stall" in text or "worker_noop" in text or "terminal" in text:
        return "worker_noop_or_terminal_missing"
    if "human.escalate" in text or "human" in text:
        return "human_attention_delivery"
    if "feishu" in text or "delivery" in text or "owner.visible" in text:
        return "owner_visible_delivery"
    return "runtime_attention"


def _attention_recommended_actions(
    item: dict[str, Any],
    *,
    fingerprint: str,
) -> list[str]:
    recommendations = [
        "inspect_source_events",
        "propose_resume_or_rework_action",
    ]
    failure_class = _attention_failure_class(item, fingerprint=fingerprint)
    if failure_class == "worker_noop_or_terminal_missing":
        recommendations.extend([
            "replay_worker_briefing",
            "respawn_worker_lane",
            "rehydrate_task_state",
        ])
    elif failure_class == "owner_visible_delivery":
        recommendations.append("notification_fallback_to_inbox")
    elif failure_class == "human_attention_delivery":
        recommendations.append("repair_human_delivery_route")
    else:
        recommendations.append("return_diagnosis_for_run_manager")
    return recommendations


def _candidate_rework_action_from_plan(
    state_dir: Path,
    events: list[ZfEvent],
    plan: object,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    pdd_id = str(getattr(plan, "pdd_id", "") or "")
    anchor = _candidate_rework_anchor(
        state_dir,
        events,
        pdd_id,
        project_root=project_root,
    )
    rework_action = str(getattr(plan, "action", "") or "")
    expected = _candidate_rework_expected_events(rework_action)
    rework_summary = dict(getattr(plan, "rework_summary", {}) or {})
    if rework_action == "retrigger" and rework_summary.get("gap_tasks"):
        expected.add("task_map.amended")
    intervention = {
        "retrigger": "auto_recover",
        "replan": "semantic_replan",
        "escalate": "human_decision",
    }.get(rework_action, "diagnose")
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": "candidate-rework-apply",
        "checkpoint_id": _candidate_rework_checkpoint_id(plan),
        "safe_resume_action": f"candidate_{rework_action}",
        "candidate_rework_action": rework_action,
        "pdd_id": pdd_id,
        "feature_id": str(anchor.get("feature_id") or ""),
        "trace_id": str(getattr(plan, "trace_id", "") or ""),
        "target_ref": str(anchor.get("target_ref") or getattr(plan, "target_ref", "") or ""),
        "source_commit": str(anchor.get("source_commit") or ""),
        "candidate_base_commit": str(
            anchor.get("candidate_base_commit") or anchor.get("source_commit") or ""
        ),
        "source_index_ref": str(anchor.get("source_index_ref") or ""),
        "task_map_ref": str(anchor.get("task_map_ref") or ""),
        "source_event_id": str(getattr(plan, "source_event_id", "") or ""),
        "source_event_type": str(getattr(plan, "source_event_type", "") or ""),
        "source_event_ids": [str(getattr(plan, "source_event_id", "") or "")],
        "rework_attempt": int(getattr(plan, "attempt", 0) or 0),
        "rework_feedback": list(getattr(plan, "feedback", ()) or ()),
        "failed_task_ids": list(getattr(plan, "failed_task_ids", ()) or ()),
        "classification": str(getattr(plan, "classification", "") or ""),
        "rework_categories": list(getattr(plan, "failure_categories", ()) or ()),
        "rework_summary": rework_summary,
        "failure_class": _candidate_rework_failure_class(rework_action),
        "owner_route": _candidate_rework_owner_route(rework_action),
        "action_policy": "auto_decide",
        "intervention_class": intervention,
        "attempt_cap": 2,
        "expected_downstream_events": sorted(expected),
        "verify_condition": "expected_downstream_event:" + ",".join(sorted(expected)),
        "route_registry": "run-manager-router.v1",
    }
    return action


def _candidate_rework_event_window(
    state_dir: Path,
    events: list[ZfEvent],
) -> list[ZfEvent]:
    try:
        from zf.runtime.event_window import read_runtime_events

        expanded = read_runtime_events(EventLog(Path(state_dir) / "events.jsonl"), Path(state_dir))
        return expanded if len(expanded) >= len(events) else events
    except Exception:
        return events


def _candidate_rework_anchor(
    state_dir: Path,
    events: list[ZfEvent],
    pdd_id: str,
    *,
    project_root: Path | None = None,
) -> dict[str, str]:
    anchor_keys = (
        "source_commit",
        "candidate_base_commit",
        "task_map_ref",
        "source_index_ref",
        "feature_id",
        "target_ref",
    )
    anchor: dict[str, str] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_pdd = str(payload.get("pdd_id") or "")
        if event_pdd != pdd_id or event.type not in {
            "task_map.ready",
            "candidate.ready",
            "fanout.started",
            "fanout.child.failed",
        }:
            continue
        for key in anchor_keys:
            value = str(payload.get(key) or "")
            if value:
                anchor[key] = value
    if not anchor.get("task_map_ref") or not anchor.get("source_commit"):
        try:
            data = json.loads(
                (Path(state_dir) / "tmp" / f"task-map-ready-{pdd_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            if isinstance(data, dict):
                for key in anchor_keys:
                    value = str(data.get(key) or "")
                    if value:
                        anchor[key] = value
        except Exception:
            pass
    if pdd_id and not anchor.get("task_map_ref"):
        artifact_ref = Path(state_dir) / "artifacts" / pdd_id / "task_map.json"
        if artifact_ref.exists():
            anchor["task_map_ref"] = str(artifact_ref)
    if anchor.get("task_map_ref") and (
        not anchor.get("source_commit") or not anchor.get("candidate_base_commit")
    ):
        head = _candidate_rework_git_head(state_dir, project_root=project_root)
        if head:
            if not anchor.get("source_commit"):
                anchor["source_commit"] = head
            if not anchor.get("candidate_base_commit"):
                anchor["candidate_base_commit"] = head
    return anchor


def _candidate_rework_git_head(
    state_dir: Path,
    *,
    project_root: Path | None = None,
) -> str:
    """Recover a commit anchor for artifact-only candidate rework events."""

    git_root = Path(project_root) if project_root is not None else Path(state_dir).parent
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _candidate_rework_checkpoint_id(plan: object) -> str:
    raw = "|".join([
        "candidate-rework",
        str(getattr(plan, "pdd_id", "") or ""),
        str(getattr(plan, "source_event_id", "") or ""),
        str(getattr(plan, "action", "") or ""),
        str(getattr(plan, "attempt", "") or ""),
    ])
    return "crw-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _candidate_rework_expected_events(action: str) -> set[str]:
    if action == "retrigger":
        return {"task_map.ready"}
    if action == "replan":
        return {"orchestrator.replan_requested"}
    if action == "escalate":
        return {"human.escalate", "owner.visible_message.requested"}
    return {"run.manager.action.failed"}


def _candidate_rework_failure_class(action: str) -> str:
    if action == "retrigger":
        return "candidate_rework_retrigger"
    if action == "replan":
        return "candidate_rework_replan"
    if action == "escalate":
        return "candidate_rework_exhausted"
    return "candidate_rework_unknown"


def _candidate_rework_owner_route(action: str) -> str:
    if action == "replan":
        return "orchestrator_replan"
    if action == "escalate":
        return "human_escalation"
    return "controlled_action"


def _candidate_rework_shadowed_by_workflow(
    action: dict[str, Any],
    workflow_actions: list[dict[str, Any]],
) -> bool:
    source_id = str(action.get("source_event_id") or "")
    if not source_id:
        return False
    for workflow_action in workflow_actions:
        safe_action = str(workflow_action.get("safe_resume_action") or "")
        if safe_action not in {"repair_failed_children", "reemit_candidate_ready"}:
            continue
        if source_id in {
            str(value) for value in workflow_action.get("source_event_ids") or []
            if str(value).strip()
        }:
            return True
    return False


def _candidate_actions_are_human_escalations(actions: list[dict[str, Any]]) -> bool:
    if not actions:
        return False
    for action in actions:
        decision = action.get("policy_decision")
        decision = decision if isinstance(decision, dict) else {}
        if str(action.get("candidate_rework_action") or "") == "escalate":
            continue
        if str(decision.get("decision") or "") in {"human_escalate", "needs_approval"}:
            continue
        return False
    return True


def _workflow_resume_projection(
    state_dir: Path,
    config: ZfConfig,
    events: list[ZfEvent],
) -> dict[str, Any]:
    try:
        return build_workflow_resume_projection(
            state_dir,
            config,
            events=events,
            tasks=_read_tasks(state_dir),
        )
    except Exception as exc:
        return {
            "schema_version": "workflow-resume.projection-error.v1",
            "summary": {"batch_pending": 0},
            "batch_checkpoints": [],
            "error": str(exc),
        }


def _read_events(state_dir: Path, *, event_log: EventLog | None = None) -> list[ZfEvent]:
    if event_log is not None:
        try:
            from zf.runtime.event_window import read_runtime_events

            return read_runtime_events(event_log, Path(state_dir))
        except Exception:
            try:
                return list(event_log.read_all())
            except Exception:
                pass
    try:
        log = EventLog(Path(state_dir) / "events.jsonl")
        try:
            from zf.runtime.event_window import read_runtime_events

            return read_runtime_events(log, Path(state_dir))
        except Exception:
            return log.read_all()
    except Exception:
        return []


def _read_tasks(state_dir: Path) -> list[Any]:
    try:
        path = Path(state_dir) / "kanban.json"
        if not path.exists():
            return []
        return TaskStore(path).list_all()
    except Exception:
        return []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _role_session_meta(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(state_dir) / "role_sessions.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    meta = data.get("instance_meta")
    if not isinstance(meta, dict):
        return {}
    return {
        str(instance_id): dict(row) if isinstance(row, dict) else {}
        for instance_id, row in meta.items()
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): value[key] for key in value}


def _action_payload(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": str(action.get("action") or ""),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "safe_resume_action": str(action.get("safe_resume_action") or ""),
        "task_id": str(action.get("task_id") or ""),
        "instance_id": str(action.get("instance_id") or action.get("role_instance") or ""),
        "candidate_rework_action": str(action.get("candidate_rework_action") or ""),
        "failure_class": str(action.get("failure_class") or ""),
        "owner_route": str(action.get("owner_route") or ""),
        "action_policy": str(action.get("action_policy") or ""),
        "intervention_class": str(action.get("intervention_class") or ""),
        "verify_condition": str(action.get("verify_condition") or ""),
        "task_map_ref": str(action.get("task_map_ref") or ""),
        "source_index_ref": str(action.get("source_index_ref") or ""),
        "source_commit": str(action.get("source_commit") or ""),
        "target_ref": str(action.get("target_ref") or ""),
        "candidate_ref": str(action.get("candidate_ref") or ""),
        "candidate_base_commit": str(action.get("candidate_base_commit") or ""),
        "candidate_head_commit": str(action.get("candidate_head_commit") or ""),
        "expected_downstream_events": [
            str(value) for value in action.get("expected_downstream_events") or []
            if str(value).strip()
        ],
        "fanout_id": str(action.get("fanout_id") or ""),
        "pdd_id": str(action.get("pdd_id") or ""),
        "feature_id": str(action.get("feature_id") or ""),
        "stage_id": str(action.get("stage_id") or ""),
        "trace_id": str(action.get("trace_id") or ""),
        "diff_ref": str(action.get("diff_ref") or ""),
        "source_event_id": str(action.get("source_event_id") or ""),
        "source_event_type": str(action.get("source_event_type") or ""),
        "mutating_resume_supported": bool(action.get("mutating_resume_supported")),
        "queue_id": str(action.get("queue_id") or ""),
        "fingerprint": str(action.get("fingerprint") or ""),
        "candidate_id": str(action.get("candidate_id") or ""),
        "branch": str(action.get("branch") or ""),
        "worktree_path": str(action.get("worktree_path") or action.get("worktree") or ""),
        "source_commit": str(action.get("source_commit") or ""),
        "source_title": str(action.get("source_title") or ""),
        "verification_plan": action.get("verification_plan")
        if isinstance(action.get("verification_plan"), list) else [],
        "risk_classification": action.get("risk_classification")
        if isinstance(action.get("risk_classification"), dict) else {},
        "continuation": action.get("continuation")
        if isinstance(action.get("continuation"), dict) else {},
    }


def _human_decision_token(action: dict[str, Any]) -> str:
    raw = "|".join([
        str(action.get("run_id") or action.get("pdd_id") or ""),
        str(action.get("checkpoint_id") or ""),
        str(action.get("safe_resume_action") or ""),
        _fingerprint(action, fallback=""),
    ])
    return "hdec-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _human_decision_event_token(event: ZfEvent, payload: dict[str, Any]) -> str:
    raw = str(
        payload.get("decision_token")
        or payload.get("response_token")
        or payload.get("approval_ref")
        or payload.get("source_message_id")
        or payload.get("escalation_event_id")
        or ""
    )
    if raw.startswith("human:"):
        raw = raw.removeprefix("human:")
    return raw or event.causation_id or event.id


def _human_escalations_by_token(events: list[ZfEvent]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    lease_index: dict[str, str] = {}
    for event in events:
        if event.type not in {"human.escalate", HUMAN_ESCALATION_SENT}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        token = _human_decision_event_token(event, payload)
        if not token:
            continue
        lease_key = _human_decision_lease_key(payload)
        if lease_key:
            previous = lease_index.get(lease_key)
            if previous and previous in out:
                source_ids = list(out[previous].get("source_event_ids") or [])
                if event.id not in source_ids:
                    source_ids.append(event.id)
                out[previous]["source_event_ids"] = source_ids
                out[previous]["last_refreshed_at"] = event.ts
                out[previous]["lease_key"] = lease_key
                continue
            lease_index[lease_key] = token
        out[token] = {
            **payload,
            "event_id": event.id,
            "event_type": event.type,
            "created_ts": event.ts,
            "last_refreshed_at": event.ts,
            "lease_key": lease_key,
            "source_event_ids": [event.id],
        }
    return out


def _seen_human_decisions(events: list[ZfEvent]) -> set[str]:
    seen: set[str] = set()
    for event in events:
        if event.type not in {
            RUN_MANAGER_HUMAN_DECISION_APPLIED,
            RUN_MANAGER_HUMAN_DECISION_REJECTED,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("decision_token", "source_event_id"):
            value = str(payload.get(key) or "")
            if value:
                seen.add(value)
    return seen


def _human_decision_action(
    decision_payload: dict[str, Any],
    escalation_payload: dict[str, Any],
) -> dict[str, Any]:
    merged = {**escalation_payload, **decision_payload}
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": str(merged.get("action") or "workflow-batch-resume"),
        "checkpoint_id": str(merged.get("checkpoint_id") or ""),
        "safe_resume_action": str(merged.get("safe_resume_action") or ""),
        "fanout_id": str(merged.get("fanout_id") or ""),
        "pdd_id": str(merged.get("pdd_id") or merged.get("run_id") or ""),
        "stage_id": str(merged.get("stage_id") or merged.get("stage") or ""),
        "failure_class": str(merged.get("failure_class") or "human_approved"),
        "owner_route": str(merged.get("owner_route") or "human_escalation"),
        "action_policy": str(merged.get("action_policy") or "human_decision"),
        "intervention_class": str(merged.get("intervention_class") or "human_decision"),
        "verify_condition": str(merged.get("verify_condition") or ""),
        "candidate_ref": str(merged.get("candidate_ref") or ""),
        "candidate_base_commit": str(merged.get("candidate_base_commit") or ""),
        "candidate_head_commit": str(merged.get("candidate_head_commit") or ""),
        "task_map_ref": str(merged.get("task_map_ref") or ""),
        "source_index_ref": str(merged.get("source_index_ref") or ""),
        "source_commit": str(merged.get("source_commit") or ""),
        "target_ref": str(merged.get("target_ref") or ""),
        "source_event_id": str(merged.get("source_event_id") or ""),
        "source_event_type": str(merged.get("source_event_type") or ""),
        "mutating_resume_supported": bool(merged.get("mutating_resume_supported")),
        "source_event_ids": [
            str(value) for value in merged.get("source_event_ids") or []
            if str(value).strip()
        ],
    }
    if not action["verify_condition"]:
        expected = sorted(_expected_downstream_events(action["safe_resume_action"]))
        action["verify_condition"] = "expected_downstream_event:" + ",".join(expected)
    return action


def _pending_human_decisions(events: list[ZfEvent]) -> list[dict[str, Any]]:
    escalations = _human_escalations_by_token(events)
    resolved = _seen_human_decisions(events)
    rows = []
    for token, payload in escalations.items():
        if token in resolved or str(payload.get("event_id") or "") in resolved:
            continue
        source_event = _event_by_id(events, str(payload.get("event_id") or ""))
        if source_event is not None and _event_superseded_by_later_progress(events, source_event):
            continue
        rows.append({
            "decision_token": token,
            "event_id": str(payload.get("event_id") or ""),
            "event_type": str(payload.get("event_type") or ""),
            "created_ts": str(payload.get("created_ts") or ""),
            "last_refreshed_at": str(payload.get("last_refreshed_at") or payload.get("created_ts") or ""),
            "lease_key": str(payload.get("lease_key") or ""),
            "task_id": str(payload.get("task_id") or ""),
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "fingerprint": str(payload.get("fingerprint") or ""),
            "reason": str(payload.get("reason") or ""),
            "source_event_ids": [
                str(value) for value in payload.get("source_event_ids") or []
                if str(value).strip()
            ],
            "suggested_options": [
                str(value) for value in payload.get("suggested_options") or []
            ],
        })
    return sorted(rows, key=lambda item: item.get("created_ts") or "")


def _event_by_id(events: list[ZfEvent], event_id: str) -> ZfEvent | None:
    if not event_id:
        return None
    for event in events:
        if event.id == event_id:
            return event
    return None


def _human_decision_lease_key(payload: dict[str, Any]) -> str:
    owner = str(payload.get("owner_route") or payload.get("owner") or "human").strip()
    checkpoint = str(payload.get("checkpoint_id") or payload.get("checkpoint") or "").strip()
    fingerprint = str(payload.get("fingerprint") or "").strip()
    decision_class = str(
        payload.get("decision_class")
        or payload.get("failure_class")
        or payload.get("safe_resume_action")
        or payload.get("action")
        or "human_decision"
    ).strip()
    stable = checkpoint or fingerprint
    if not stable:
        return ""
    return "|".join([
        owner or "human",
        stable,
        decision_class or "human_decision",
    ])


_PROGRESS_SUCCESS_EVENTS = frozenset({
    "task_map.ready",
    "fanout.started",
    "workflow.resume.applied",
    "verify.passed",
    "cangjie.module.parity.scan.completed",
    "module.parity.closed",
    "judge.passed",
    "candidate.promoted",
})


def _event_superseded_by_later_progress(
    events: list[ZfEvent],
    source_event: ZfEvent,
) -> bool:
    source_payload = source_event.payload if isinstance(source_event.payload, dict) else {}
    seen_source = False
    for event in events:
        if event.id == source_event.id:
            seen_source = True
            continue
        if not seen_source:
            continue
        if not _is_progress_success_event(event):
            continue
        success_payload = event.payload if isinstance(event.payload, dict) else {}
        if _progress_success_matches_source(source_event, source_payload, event, success_payload):
            return True
    return False


def _is_progress_success_event(event: ZfEvent) -> bool:
    if event.type not in _PROGRESS_SUCCESS_EVENTS:
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type == "cangjie.module.parity.scan.completed":
        try:
            return int(payload.get("open_p0_p1_gap_count") or 0) <= 0
        except (TypeError, ValueError):
            return False
    return True


def _progress_success_matches_source(
    source_event: ZfEvent,
    source_payload: dict[str, Any],
    success_event: ZfEvent,
    success_payload: dict[str, Any],
) -> bool:
    source_scope = _progress_scope(source_payload)
    success_scope = _progress_scope(success_payload)
    if source_scope and success_scope:
        return any(
            value and success_scope.get(key) == value
            for key, value in source_scope.items()
        )
    if source_scope:
        return False
    if source_event.type in {
        "human.escalate",
        HUMAN_ESCALATION_SENT,
        RUN_MANAGER_ACTION_FAILED,
        RUN_MANAGER_ACTION_VERIFY_FAILED,
        "autoresearch.repair.closeout.required",
    }:
        return _is_run_level_recovery_source(source_payload, source_event.type)
    return False


def _progress_scope(payload: dict[str, Any]) -> dict[str, str]:
    scope: dict[str, str] = {}
    for key in (
        "pdd_id",
        "feature_id",
        "run_id",
        "trace_id",
        "candidate_ref",
        "target_ref",
    ):
        value = str(payload.get(key) or "")
        if value:
            canonical_key = "candidate_ref" if key == "target_ref" else key
            scope[canonical_key] = value
    return scope


def _is_run_level_recovery_source(payload: dict[str, Any], event_type: str) -> bool:
    if event_type == RUN_MANAGER_ACTION_VERIFY_FAILED:
        return True
    if event_type == "autoresearch.repair.closeout.required":
        continuation = payload.get("continuation")
        return isinstance(continuation, dict) and bool(continuation.get("resume_original_workflow"))
    text = " ".join([
        str(payload.get("reason") or ""),
        str(payload.get("summary") or ""),
        str(payload.get("failure_class") or ""),
        str(payload.get("owner_route") or ""),
        str(payload.get("action") or ""),
        str(payload.get("safe_resume_action") or ""),
    ]).lower()
    return any(
        marker in text
        for marker in (
            "candidate rework exhausted",
            "reviewer findings unresolved",
            "resident run manager",
            "run_manager",
            "agent_recommendation",
            "repair",
        )
    )


def _blocking_repair_merge_counts(
    events: list[ZfEvent],
    repair_merge_queue: dict[str, Any],
) -> dict[str, int]:
    terminal_statuses = {"merged", "discarded"}
    pending = 0
    closeout_required = 0
    for row in repair_merge_queue.get("items") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        if status in terminal_statuses:
            continue
        if _repair_merge_row_superseded_by_later_progress(events, row):
            continue
        pending += 1
        if status == "closeout_required":
            closeout_required += 1
    return {
        "pending": pending,
        "closeout_required": closeout_required,
    }


def _repair_merge_row_superseded_by_later_progress(
    events: list[ZfEvent],
    row: dict[str, Any],
) -> bool:
    event_ids: list[str] = []
    for event_id in (str(row.get("last_event_id") or ""),):
        if event_id:
            event_ids.append(event_id)
    for item in row.get("events") or []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "")
        if event_id and event_id not in event_ids:
            event_ids.append(event_id)
    for event_id in event_ids:
        source_event = _event_by_id(events, event_id)
        if source_event is not None and _event_superseded_by_later_progress(events, source_event):
            return True
    return False


def _open_verify_failures(events: list[ZfEvent]) -> list[dict[str, Any]]:
    latest: dict[str, ZfEvent] = {}
    for event in events:
        if event.type not in {
            RUN_MANAGER_ACTION_VERIFY_PASSED,
            RUN_MANAGER_ACTION_VERIFY_FAILED,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = _fingerprint(payload, fallback=event.id)
        latest[key] = event
    rows = []
    for key, event in latest.items():
        if event.type != RUN_MANAGER_ACTION_VERIFY_FAILED:
            continue
        if _event_superseded_by_later_progress(events, event):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        rows.append({
            "fingerprint": key,
            "event_id": event.id,
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "reason": str(payload.get("reason") or ""),
        })
    return rows


def _has_pending_repair_closeout(events: list[ZfEvent]) -> bool:
    queue = build_repair_merge_queue(events)
    return _blocking_repair_merge_counts(events, queue)["pending"] > 0


def _repair_merge_key(payload: dict[str, Any], *, fallback: str) -> str:
    raw = str(
        payload.get("queue_id")
        or payload.get("candidate_id")
        or payload.get("candidate_path")
        or payload.get("worktree_path")
        or payload.get("branch")
        or payload.get("candidate_branch")
        or _fingerprint(payload, fallback=fallback)
        or fallback
    )
    return raw


def _current_terminal_signal(events: list[ZfEvent]) -> ZfEvent | None:
    terminal = _last_event(events, _TERMINAL_SIGNAL_EVENTS)
    if terminal is None:
        return None
    try:
        terminal_index = next(
            idx for idx, event in enumerate(events)
            if event.id == terminal.id
        )
    except StopIteration:
        return terminal
    for event in events[terminal_index + 1:]:
        if event.type in _POST_TERMINAL_WORK_EVENTS:
            return None
    return terminal


def _current_run_completed_closeout(events: list[ZfEvent]) -> ZfEvent | None:
    completed = _last_event(events, (RUN_COMPLETED,))
    if completed is None:
        return None
    try:
        completed_index = next(
            idx for idx, event in enumerate(events)
            if event.id == completed.id
        )
    except StopIteration:
        return completed
    for event in events[completed_index + 1:]:
        if event.type in _POST_TERMINAL_WORK_EVENTS:
            return None
    return completed


def _has_auto_ready_actions(pending_actions: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict)
        and _pending_action_readiness(item) == "ready_to_execute"
        for item in pending_actions
    )


def _action_seen(events: list[ZfEvent], action: dict[str, Any]) -> bool:
    checkpoint_id = str(action.get("checkpoint_id") or "")
    if not checkpoint_id:
        return False
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {
            RUN_MANAGER_ACTION_APPLIED,
            RUN_MANAGER_ACTION_BLOCKED,
            RUN_MANAGER_ACTION_FAILED,
            RUN_MANAGER_AUTORESEARCH_REQUESTED,
            "workflow.resume.applied",
        } and str(payload.get("checkpoint_id") or payload.get("resume_checkpoint_ref") or payload.get("idempotency_key") or "") == checkpoint_id:
            return True
        if (
            event.type == RUN_MANAGER_RESIDENT_PROMPTED
            and str(payload.get("source_action_checkpoint_id") or "") == checkpoint_id
        ):
            return True
    return False


def _collect_emitted_event_ids(result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for event_id in result.get("emitted_event_ids") or []:
        if str(event_id):
            ids.add(str(event_id))
    resume = result.get("resume_result")
    if not isinstance(resume, dict):
        return ids
    for key in ("results", "batch_results"):
        for item in resume.get(key) or []:
            if not isinstance(item, dict):
                continue
            for event_id in item.get("emitted_event_ids") or []:
                if str(event_id):
                    ids.add(str(event_id))
    return ids


def _emitted_event_types(event_log: EventLog, event_ids: set[str]) -> set[str]:
    if not event_ids:
        return set()
    try:
        events = event_log.read_all()
    except Exception:
        return set()
    return {event.type for event in events if event.id in event_ids}


def _expected_downstream_events(safe_action: str) -> set[str]:
    return router_expected_downstream_events(safe_action)


def _last_event(events: list[ZfEvent], types: tuple[str, ...]) -> ZfEvent | None:
    wanted = set(types)
    for event in reversed(events):
        if event.type in wanted:
            return event
    return None


def _last_event_with_index(
    events: list[ZfEvent],
    types: tuple[str, ...],
) -> tuple[int, ZfEvent | None]:
    wanted = set(types)
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.type in wanted:
            return index, event
    return -1, None


def _last_event_after_index(
    events: list[ZfEvent],
    index: int,
    types: tuple[str, ...],
) -> ZfEvent | None:
    if index < 0:
        return _last_event(events, types)
    wanted = set(types)
    for event in reversed(events[index + 1:]):
        if event.type in wanted:
            return event
    return None


def _event_summary(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "event_id": event.id,
        "event_type": event.type,
        "ts": event.ts,
        "task_id": event.task_id or str(payload.get("task_id") or ""),
        "fingerprint": _fingerprint(payload, fallback=""),
        "status": str(payload.get("status") or payload.get("decision") or ""),
    }


def _event_age_seconds(event: ZfEvent | None, now: datetime) -> int | None:
    if event is None:
        return None
    value = str(event.ts or "")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))


def _derive_phase(events: list[ZfEvent]) -> str:
    for event in reversed(events):
        etype = event.type
        if (
            etype.endswith(".passed")
            or etype in {
                "judge.passed",
                "task.done",
                RUN_COMPLETED,
                "ship.completed",
                "ship.done",
            }
        ):
            return "done"
        if etype.startswith("verify.") or etype.startswith("test."):
            return "verify"
        if etype.startswith("review."):
            return "review"
        if etype in {"dev.build.done", "static_gate.passed"}:
            return "impl_exit"
        if etype in {"task.dispatched", "fanout.child.dispatched"}:
            return "impl"
        if etype == "task_map.ready":
            return "impl_ready"
        if etype.startswith("plan.") or etype == "task_map.generated":
            return "plan"
        if etype.startswith("scan.") or etype.endswith(".scan.requested"):
            return "scan"
    return "unknown"


def _next_wait(state: str, pending_actions: list[dict[str, Any]], open_attention: list[dict[str, Any]]) -> str:
    if pending_actions:
        return "run_manager_action"
    if state == "complete":
        return "complete"
    if state == "blocked":
        return "completion_blocked"
    if state == "needs_human":
        return "human_decision"
    if state == "repair_closeout_required":
        return "repair_merge_decision"
    if state == "repair_in_flight":
        return "repair_closeout"
    if open_attention:
        return "classification_or_action"
    return "next_runtime_event"


def _status_explain_decision(
    *,
    pending_action: dict[str, Any] | None,
    completion_profile: dict[str, Any],
    monitor: dict[str, Any],
    no_progress: dict[str, Any],
    repair_merge_queue: dict[str, Any],
) -> dict[str, Any]:
    if pending_action:
        decision = pending_action.get("policy_decision")
        decision = decision if isinstance(decision, dict) else {}
        decision_name = str(decision.get("decision") or "")
        intervention = str(
            decision.get("intervention_class")
            or pending_action.get("intervention_class")
            or "none"
        )
        if decision_name == "auto_decide":
            next_auto_action = str(pending_action.get("safe_resume_action") or "workflow-batch-resume")
            wait_reason = "run_manager_action_ready"
            blocking = False
        elif decision_name == "needs_diagnosis":
            next_auto_action = (
                "invoke_autoresearch"
                if str(pending_action.get("owner_route") or "") == "autoresearch"
                else "run_manager_diagnosis"
            )
            wait_reason = "diagnosis_required"
            blocking = False
        elif decision_name in {"human_escalate", "needs_approval"}:
            next_auto_action = "wait_for_human_decision"
            wait_reason = "human_decision_required"
            blocking = True
        elif decision_name == "safe_halt":
            next_auto_action = "none"
            wait_reason = "safe_halt"
            blocking = True
        else:
            next_auto_action = "continue_waiting"
            wait_reason = "pending_action_waiting"
            blocking = False
        return {
            "owner_route": str(pending_action.get("owner_route") or ""),
            "intervention_class": intervention,
            "wait_reason": wait_reason,
            "next_auto_action": next_auto_action,
            "blocking": blocking,
            "blocking_refs": _blocking_refs_from_action(pending_action),
        }

    pending_human = completion_profile.get("pending_human_decisions")
    if pending_human:
        return {
            "owner_route": "human",
            "intervention_class": "human_decision",
            "wait_reason": "human_decision_pending",
            "next_auto_action": "wait_for_human_decision",
            "blocking": True,
            "blocking_refs": pending_human,
        }
    if completion_profile.get("status") == "complete":
        maintenance = []
        if int((repair_merge_queue.get("summary") or {}).get("pending") or 0) > 0:
            maintenance = repair_merge_queue.get("items") or []
        return {
            "owner_route": "none",
            "intervention_class": "maintenance" if maintenance else "none",
            "wait_reason": "complete_with_maintenance_pending" if maintenance else "complete",
            "next_auto_action": "none",
            "blocking": False,
            "blocking_refs": [],
            "maintenance_refs": maintenance,
        }
    if int((repair_merge_queue.get("summary") or {}).get("pending") or 0) > 0:
        return {
            "owner_route": "operator",
            "intervention_class": "manual_review",
            "wait_reason": "repair_closeout_pending",
            "next_auto_action": "wait_for_manual_review",
            "blocking": True,
            "blocking_refs": repair_merge_queue.get("items") or [],
        }
    if no_progress.get("status") == "tripped":
        return {
            "owner_route": "autoresearch",
            "intervention_class": "diagnose",
            "wait_reason": "no_progress_tripped",
            "next_auto_action": "invoke_autoresearch_or_escalate",
            "blocking": False,
            "blocking_refs": no_progress.get("items") or [],
        }
    if completion_profile.get("status") == "blocked":
        return {
            "owner_route": "run-manager",
            "intervention_class": "safe_halt",
            "wait_reason": "completion_blocked",
            "next_auto_action": "none",
            "blocking": True,
            "blocking_refs": completion_profile.get("blockers") or [],
        }
    if monitor.get("state") == "silent_stall":
        return {
            "owner_route": "run-manager",
            "intervention_class": "diagnose",
            "wait_reason": "silent_stall",
            "next_auto_action": "classify_or_diagnose",
            "blocking": False,
            "blocking_refs": [],
        }
    return {
        "owner_route": "run-manager",
        "intervention_class": "wait",
        "wait_reason": str(monitor.get("next_wait") or "next_runtime_event"),
        "next_auto_action": "continue_waiting",
        "blocking": False,
        "blocking_refs": [],
    }


def _pending_execution_summary(
    events: list[ZfEvent],
    pending_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    last_tick = _last_event(events, (RUN_MANAGER_TICK_COMPLETED,))
    tick_payload = last_tick.payload if last_tick and isinstance(last_tick.payload, dict) else {}
    ready = [
        item for item in pending_actions
        if _pending_action_readiness(item) == "ready_to_execute"
    ]
    applied = _safe_int(tick_payload.get("actions_applied"))
    blocked = _safe_int(tick_payload.get("actions_blocked"))
    failed = _safe_int(tick_payload.get("actions_failed"))
    status = "no_pending_actions"
    reason = ""
    if ready:
        if last_tick and applied == 0 and blocked == 0 and failed == 0:
            status = "ready_but_last_tick_no_action"
            reason = "latest Run Manager tick applied/blocked/failed no actions despite ready pending actions"
        else:
            status = "ready_to_execute"
            reason = "at least one pending action is auto-decidable and passed preflight"
    elif pending_actions:
        status = "pending_but_not_auto_ready"
        reason = "pending actions require diagnosis, human decision, or preflight repair"
    return {
        "schema_version": "run-manager.pending-execution.v1",
        "status": status,
        "reason": reason,
        "ready_actions": len(ready),
        "pending_actions": len(pending_actions),
        "last_tick_event_id": last_tick.id if last_tick else "",
        "last_tick_transition": str(tick_payload.get("transition") or ""),
        "last_tick_actions_applied": applied,
        "last_tick_actions_blocked": blocked,
        "last_tick_actions_failed": failed,
    }


def _pending_action_readiness(action: dict[str, Any]) -> str:
    decision = action.get("policy_decision")
    decision = decision if isinstance(decision, dict) else {}
    preflight = action.get("preflight")
    preflight = preflight if isinstance(preflight, dict) else {}
    decision_name = str(decision.get("decision") or "")
    if str(preflight.get("status") or "") == "blocked":
        return "preflight_blocked"
    if decision_name == "auto_decide":
        return "ready_to_execute"
    if decision_name == "needs_diagnosis":
        return "needs_diagnosis"
    if decision_name in {"human_escalate", "needs_approval", "safe_halt"}:
        return "human_blocked"
    return "waiting"


def _pending_action_explain(
    action: dict[str, Any],
    *,
    pending_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = action.get("policy_decision")
    decision = decision if isinstance(decision, dict) else {}
    readiness = _pending_action_readiness(action)
    skip_reason = ""
    if (
        readiness == "ready_to_execute"
        and isinstance(pending_execution, dict)
        and pending_execution.get("status") == "ready_but_last_tick_no_action"
    ):
        skip_reason = "last_tick_no_action"
    return {
        "action": str(action.get("action") or ""),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "safe_resume_action": str(action.get("safe_resume_action") or ""),
        "task_id": str(action.get("task_id") or ""),
        "instance_id": str(action.get("instance_id") or action.get("role_instance") or ""),
        "candidate_rework_action": str(action.get("candidate_rework_action") or ""),
        "failure_class": str(action.get("failure_class") or ""),
        "owner_route": str(action.get("owner_route") or ""),
        "action_policy": str(action.get("action_policy") or ""),
        "intervention_class": str(action.get("intervention_class") or ""),
        "decision_intervention_class": str(decision.get("intervention_class") or ""),
        "policy_decision": str(decision.get("decision") or ""),
        "verify_condition": str(action.get("verify_condition") or ""),
        "expected_downstream_events": [
            str(value) for value in action.get("expected_downstream_events") or []
            if str(value).strip()
        ],
        "preflight_status": str((action.get("preflight") or {}).get("status") or ""),
        "readiness": readiness,
        "skip_reason": skip_reason,
    }


def _blocking_refs_from_action(action: dict[str, Any]) -> list[dict[str, str]]:
    refs = []
    for key in ("checkpoint_id", "task_id", "instance_id", "fanout_id", "pdd_id", "candidate_ref"):
        value = str(action.get(key) or "")
        if value:
            refs.append({"kind": key, "ref": value})
    for event_id in action.get("source_event_ids") or []:
        if str(event_id):
            refs.append({"kind": "event", "ref": str(event_id)})
    return refs


def _recent_failure_fingerprints(events: list[ZfEvent]) -> list[dict[str, Any]]:
    rows = []
    for event in reversed(events):
        if len(rows) >= 20:
            break
        if event.type.endswith(".failed") or event.type in {"human.escalate", RUN_MANAGER_ACTION_BLOCKED}:
            payload = event.payload if isinstance(event.payload, dict) else {}
            rows.append({
                "event_id": event.id,
                "event_type": event.type,
                "fingerprint": _fingerprint(payload, fallback=event.id),
            })
    return list(reversed(rows))


def _fingerprint(payload: dict[str, Any], *, fallback: str) -> str:
    return str(
        payload.get("fingerprint")
        or payload.get("failure_fingerprint")
        or payload.get("checkpoint_id")
        or payload.get("idempotency_key")
        or fallback
    )


def _repair_key(payload: dict[str, Any]) -> tuple[str, int]:
    return _fingerprint(payload, fallback=""), _safe_int(payload.get("attempt"))


def _autoresearch_request_id(event: ZfEvent, payload: dict[str, Any]) -> str:
    return str(
        payload.get("request_id")
        or payload.get("loop_request_id")
        or event.correlation_id
        or event.id
    )


def _run_manager_autoresearch_request_id(action: dict[str, Any]) -> str:
    raw = "|".join([
        str(action.get("checkpoint_id") or ""),
        str(action.get("safe_resume_action") or ""),
        _fingerprint(action, fallback=""),
    ])
    return "rmar-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _request_id_seen(event_log: EventLog, event_type: str, request_id: str) -> bool:
    if not request_id:
        return False
    try:
        events = event_log.read_all()
    except Exception:
        return False
    for event in events:
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("request_id") or "") == request_id:
            return True
    return False


def _autoresearch_result_request_id(event: ZfEvent, payload: dict[str, Any]) -> str:
    return str(
        payload.get("run_manager_request_id")
        or payload.get("request_id")
        or payload.get("loop_request_id")
        or payload.get("source_request_id")
        or event.correlation_id
        or ""
    )


def _autoresearch_proposal_refs(payload: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key in (
        "proposal_ref",
        "proposal_path",
        "artifact_ref",
        "artifact_refs",
        "candidate_path",
        "report_ref",
        "eval_result_ref",
    ):
        value = payload.get(key)
        if value:
            refs[key] = value
    return refs


def _autoresearch_next_route(status: str, payload: dict[str, Any]) -> str:
    if status == "failed":
        return "human_escalate"
    if payload.get("repair_task_payload") or payload.get("repair_request"):
        return "repair_worker"
    if _autoresearch_proposal_refs(payload):
        return "proposal_review"
    return "no_op"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "HUMAN_ESCALATION_ACKNOWLEDGED",
    "HUMAN_ESCALATION_FAILED",
    "HUMAN_ESCALATION_SENT",
    "REPAIR_LEDGER_SCHEMA_VERSION",
    "REPAIR_MERGE_QUEUE_SCHEMA_VERSION",
    "RUN_COMPLETION_PROFILE_SCHEMA_VERSION",
    "RUN_CONTEXT_BUNDLE_SCHEMA_VERSION",
    "RUN_GOAL_SCHEMA_VERSION",
    "RUN_MANAGER_AUTORESEARCH_CONSUMED",
    "RUN_MANAGER_AUTORESEARCH_REQUESTED",
    "RUN_MANAGER_REFLECT_COMPLETED",
    "RUN_MANAGER_REFLECT_REQUESTED",
    "RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED",
    "RUN_MANAGER_HUMAN_DECISION_APPLIED",
    "RUN_MANAGER_HUMAN_DECISION_REJECTED",
    "RUN_MANAGER_ACTION_APPLIED",
    "RUN_MANAGER_ACTION_BLOCKED",
    "RUN_MANAGER_ACTION_FAILED",
    "RUN_MANAGER_ACTION_PLANNED",
    "RUN_MANAGER_ACTION_VERIFY_FAILED",
    "RUN_MANAGER_ACTION_VERIFY_PASSED",
    "RUN_MANAGER_REPAIR_ACCEPTED",
    "RUN_MANAGER_REPAIR_BLOCKED",
    "RUN_MANAGER_REPAIR_REJECTED",
    "RUN_MANAGER_REPAIR_MERGE_DISCARDED",
    "RUN_MANAGER_REPAIR_MERGE_MERGED",
    "RUN_MANAGER_REPAIR_MERGE_MERGING",
    "RUN_MANAGER_REPAIR_MERGE_NEEDS_REVIEW",
    "RUN_MANAGER_REPAIR_MERGE_QUEUED",
    "RUN_MANAGER_SCHEMA_VERSION",
    "RUN_MANAGER_TICK_COMPLETED",
    "RUN_MANAGER_TICK_STARTED",
    "RUN_MANAGER_TRANSITION",
    "RunManagerTickResult",
    "build_repair_ledger",
    "build_repair_merge_queue",
    "build_run_context_bundle",
    "build_run_completion_profile",
    "build_run_goal_projection",
    "build_run_manager_projection",
    "build_run_manager_monitor_projection",
    "build_run_status_explain_projection",
    "build_run_manager_timeline",
    "build_run_monitor_projection",
    "classify_recovery_context",
    "decide_action_policy",
    "emit_human_escalation_package",
    "run_manager_tick",
    "write_run_manager_projections",
]
