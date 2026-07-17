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
from typing import Any, Callable, Mapping

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.module_parity import (
    MODULE_PARITY_SCAN_COMPLETED_EVENTS,
    is_module_parity_scan_completed_event,
)
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TaskStore
from zf.runtime.event_problem_registry import (
    RUN_MANAGER_PENDING_EVENT_TYPES,
    RUN_MANAGER_POST_TERMINAL_EVENT_TYPES,
    looks_actionable_event,
    spec_for_event,
)
from zf.runtime.channel_reply_remediation import pending_channel_reply_exhausted_actions
from zf.runtime.autoresearch_invocation import recovery_case_id_from_payload
from zf.autoresearch.self_repair import (
    candidate_from_dict,
    candidate_with_diagnosis,
    repair_task_payload_from_candidate,
    write_candidate_artifact,
)
from zf.runtime.repair_dispatch import pending_repair_dispatches
from zf.runtime.pane_probe import build_runtime_pane_probe
from zf.runtime.problem_taxonomy import problem_envelope_from_action
from zf.runtime.run_manager_advisor import build_replan_advisor_projection
from zf.runtime.run_manager_reports import (
    build_regression_backlog_candidates,
    build_retrospective_markdown,
)
from zf.runtime.run_manager_rework_triage import (
    TRIAGE_APPLY_ACTION,
    TRIAGE_RECORDED,
    TRIAGE_REQUEST_ACTION,
    TRIAGE_REQUESTED,
    active_rework_triage_task_ids,
    is_semantic_triage_cap,
    pending_rework_triage_actions,
)
from zf.runtime.recovery_context import write_task_recovery_context
from zf.runtime.run_manager_router import (
    ACTION_POLICIES,
    SAFE_BATCH_ACTIONS,
    SAFE_TASK_ACTIONS,
    build_no_progress_projection,
    classify_recovery_context as router_classify_recovery_context,
    decide_action_policy as router_decide_action_policy,
    expected_downstream_events as router_expected_downstream_events,
    preflight_action,
)
from zf.runtime.semantic_replan import (
    SEMANTIC_REPLAN_ACTION,
    enrich_semantic_replan_action,
)
from zf.runtime.run_manager_wait_hint import (
    build_resident_repair_policy_projection,
    build_wait_hint_projection,
)
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions
from zf.runtime.terminal_events import latest_quiescent_run_terminal
from zf.runtime.workflow_resume import build_workflow_resume_projection


RUN_MANAGER_SCHEMA_VERSION = "run-manager.v1"
RUN_CONTEXT_BUNDLE_SCHEMA_VERSION = "run-context-bundle.v1"
RUN_MANAGER_CONTEXT_SIDECAR_SCHEMA_VERSION = "run-manager.context-bundle.v1"
RUN_MANAGER_READ_SET_SCHEMA_VERSION = "run-manager.read-set.v1"
RUN_GOAL_SCHEMA_VERSION = "run-goal.v1"
RUN_GOAL_COMPLETION_CLAIMED = "run.goal.completion.claimed"
RUN_GOAL_COMPLETION_BLOCKED = "run.goal.completion.blocked"
RUN_GOAL_COMPLETION_REJECTED = "run.goal.completion.rejected"
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
# FIX-5①:同 checkpoint 的 verify.failed 有界重试上限,达到即停止重规划。
_ACTION_VERIFY_FAILED_CAP = 3
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
    "goal.gap_plan.ready",
    "flow.gap_plan.ready",
    "flow.discovery.requested",
    "flow.discovery.completed",
    "flow.goal.closed",
    *RUN_MANAGER_POST_TERMINAL_EVENT_TYPES,
    "module.parity.gap_plan.ready",
    *MODULE_PARITY_SCAN_COMPLETED_EVENTS,
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
_SAFE_TASK_ACTIONS = set(SAFE_TASK_ACTIONS)
_ACTION_POLICIES = set(ACTION_POLICIES)
_DIAGNOSIS_REQUEST_STALE_SECONDS = 300
# Once the resident anchors a request (loop.accepted/started) the bounded
# runner guarantees a terminal event, so the stale window widens to ~2x the
# 30min runner fallback bound; it only fires if the resident itself died.
_DIAGNOSIS_ANCHORED_STALE_SECONDS = 3600


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
    transport: object | None = None,
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

    deterministic_actions_applied = 0
    deterministic_actions_failed = 0
    try:
        from zf.runtime.fanout_recovery import (
            recover_unrecorded_writer_fanout_results,
        )

        fanout_recovery = recover_unrecorded_writer_fanout_results(
            state_dir=state_dir,
            config=config,
            project_root=project_root,
            event_log=event_log,
            transport=transport,
        )
        if fanout_recovery.recovered:
            writer.emit(
                RUN_MANAGER_ACTION_APPLIED,
                actor="run-manager",
                causation_id=started.id,
                payload={
                    "schema_version": "run-manager.action.v1",
                    "action": "fanout-terminal-recover",
                    "safe_resume_action": "recover_unrecorded_writer_fanout_results",
                    "status": "applied",
                    "reason": "writer fanout result events were missing child terminal records",
                    "candidate_count": len(fanout_recovery.candidates),
                    "events_appended": fanout_recovery.events_appended,
                    "terminals_appended": fanout_recovery.terminals_appended,
                    "expected_downstream_events": [
                        "fanout.child.completed",
                        "fanout.child.failed",
                        "fanout.child.dispatched",
                    ],
                    "verify_condition": (
                        "expected_downstream_event:"
                        "fanout.child.completed,fanout.child.failed,fanout.child.dispatched"
                    ),
                },
            )
            deterministic_actions_applied += 1
            events = _read_events(state_dir, event_log=event_log)
    except Exception as exc:
        writer.emit(
            RUN_MANAGER_ACTION_FAILED,
            actor="run-manager",
            causation_id=started.id,
            payload={
                "schema_version": "run-manager.action.v1",
                "action": "fanout-terminal-recover",
                "safe_resume_action": "recover_unrecorded_writer_fanout_results",
                "status": "failed",
                "reason": str(exc)[:400],
            },
        )
        deterministic_actions_failed += 1
        events = _read_events(state_dir, event_log=event_log)

    repairs_accepted = _accept_autoresearch_repairs(events, writer)
    if repairs_accepted:
        events = _read_events(state_dir, event_log=event_log)
    autoresearch_consumed = _consume_autoresearch_results(state_dir, events, writer)
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
    (
        agent_recommendations_consumed,
        agent_recommendation_autoresearch,
        agent_recommendation_reflects,
        agent_recommendation_repairs,
        agent_recommendation_actions_applied,
        agent_recommendation_actions_blocked,
        agent_recommendation_actions_failed,
    ) = (
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

        if not _resident_owns_self_repair_execution(config):
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

    actions_applied = deterministic_actions_applied + agent_recommendation_actions_applied
    actions_blocked = agent_recommendation_actions_blocked
    actions_failed = deterministic_actions_failed + agent_recommendation_actions_failed
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
                break_reason = _outcome_no_progress_break(events, action)
                if break_reason:
                    # 结果级熔断:同签名动作 3 连发零进展,拒绝再烧。
                    try:
                        writer.emit(
                            "run.manager.action.no_progress_break",
                            actor="run-manager",
                            task_id=str(action.get("task_id") or "") or None,
                            payload={
                                "schema_version": "run-manager.no-progress-break.v1",
                                "task_id": str(action.get("task_id") or ""),
                                "safe_resume_action": str(action.get("safe_resume_action") or ""),
                                "action": str(action.get("action") or ""),
                                "checkpoint_id": str(action.get("checkpoint_id") or ""),
                                "reason": break_reason,
                            },
                            causation_id=started.id,
                        )
                    except Exception:
                        pass
                    actions_blocked += 1
                    continue
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
                if _diagnosis_action_request_stalled(events, action):
                    _emit_failed_action(
                        writer,
                        action,
                        causation_id=started.id,
                        reason=(
                            "run manager autoresearch request became stale without "
                            "autoresearch.loop.completed or autoresearch.loop.failed"
                        ),
                    )
                    actions_failed += 1
                    events = _read_events(state_dir, event_log=event_log)
                    continue
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
    strict = getattr(config.workflow, "strict_triggers", None)
    triage_threshold = int(getattr(strict, "rework_attempts_gte", 0) or 0)
    resident_agent = build_resident_agent_projection(events, config=config)
    rework_triage_actions = pending_rework_triage_actions(
        events,
        threshold=triage_threshold,
        stale_seconds=_DIAGNOSIS_REQUEST_STALE_SECONDS,
        advisor_available=any(
            str(getattr(role, "name", "") or "") == "orchestrator"
            for role in getattr(config, "roles", []) or []
        ),
        resident_advisor=resident_agent,
    )
    rework_triage_actions = [
        enrich_semantic_replan_action(
            action,
            state_dir=state_dir,
            events=events,
            config=config,
        )
        for action in rework_triage_actions
    ]
    for action in rework_triage_actions:
        action_name = str(action.get("action") or "")
        action["preflight"] = preflight_action(
            action=action_name,
            payload=action,
        )
        action["policy_decision"] = router_decide_action_policy(
            action=action_name,
            payload=action,
        )
    triage_owned_tasks = active_rework_triage_task_ids(
        events,
        threshold=triage_threshold,
    )
    workflow_actions = [
        *_pending_workflow_task_actions(workflow_resume, events),
        *_pending_workflow_batch_actions(workflow_resume, events),
    ]
    workflow_actions = [
        action for action in workflow_actions
        if str(action.get("task_id") or "") not in triage_owned_tasks
    ]
    attempt_recovery_actions = _pending_task_attempt_recovery_actions(
        state_dir,
        config=config,
        events=events,
    )
    worker_actions = _pending_worker_lifecycle_actions(
        state_dir,
        workflow_resume,
        events,
    )
    resident_actions = _pending_resident_agent_actions(resident_agent, events)
    repair_validation_actions = _pending_repair_validation_actions(merge_queue, events)
    failure_closeout_actions = _pending_failure_closeout_activation_actions(events)
    channel_reply_actions = []
    for action in pending_channel_reply_exhausted_actions(events):
        if _action_seen(events, action):
            continue
        action["preflight"] = preflight_action(action="diagnose-attention", payload=action)
        action["policy_decision"] = router_decide_action_policy(
            action="diagnose-attention", payload=action,
        )
        channel_reply_actions.append(action)
    post_repair_continuation_actions = _pending_post_repair_continuation_actions(
        merge_queue,
        events,
    )
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
    semantic_event_actions = []
    if (
        not workflow_actions
        and not post_repair_continuation_actions
        and not candidate_actions
        and not human_gate_actions
        and completion_profile.get("status") != "complete"
    ):
        semantic_event_actions = _pending_semantic_event_actions(
            events,
            triage_threshold=triage_threshold,
        )
        semantic_event_actions = [
            action for action in semantic_event_actions
            if str(action.get("task_id") or "") not in triage_owned_tasks
        ]
    attention_actions = []
    if (
        not workflow_actions
        and not post_repair_continuation_actions
        and not candidate_actions
        and not human_gate_actions
        and not semantic_event_actions
        and completion_profile.get("status") != "complete"
    ):
        attention_actions = _pending_attention_diagnostic_actions(
            state_dir,
            events,
            resident_agent=resident_agent,
        )
        attention_actions = [
            action for action in attention_actions
            if str(action.get("task_id") or "") not in triage_owned_tasks
        ]
    runtime_pane_snapshot = _safe_runtime_pane_snapshot(
        state_dir,
        config=config,
        project_root=project_root,
    )
    base_pending_actions = [
        *rework_triage_actions,
        *workflow_actions,
        *attempt_recovery_actions,
        *worker_actions,
        *resident_actions,
        *repair_validation_actions,
        *failure_closeout_actions,
        *channel_reply_actions,
        *post_repair_continuation_actions,
        *candidate_actions,
        *human_gate_actions,
        *semantic_event_actions,
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
    attempt_contexts = _task_attempt_contexts(state_dir / "projections")
    if attempt_contexts:
        pending_actions = [
            _action_with_attempt_context(action, attempt_contexts)
            for action in pending_actions
        ]
    pending_actions = _prioritize_pending_actions(pending_actions)
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
    budget_status = _budget_status_block(state_dir, config=config)
    budget_diagnostics = _budget_diagnostics_projection(
        events,
        budget=budget_status,
    )
    status_explain = build_run_status_explain_projection(
        state_dir,
        events=events,
        budget=budget_status,
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
        "budget_diagnostics": budget_diagnostics,
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


def _prioritize_pending_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run deterministic resume/recover actions before generic diagnosis."""

    return sorted(actions, key=_pending_action_priority)


def _pending_action_priority(action: dict[str, Any]) -> tuple[int, str, str]:
    action_name = str(action.get("action") or "")
    safe_action = str(action.get("safe_resume_action") or "")
    readiness = _pending_action_readiness(action)
    if readiness == "ready_to_execute":
        if action_name in {"workflow-task-resume", "workflow-batch-resume"}:
            if safe_action in _SAFE_TASK_ACTIONS or safe_action in _SAFE_BATCH_ACTIONS:
                return (0, action_name, str(action.get("checkpoint_id") or ""))
        if action_name == "worker-lifecycle-recover":
            return (10, action_name, str(action.get("checkpoint_id") or ""))
        if action_name == "resident-agent-reprompt":
            return (20, action_name, str(action.get("checkpoint_id") or ""))
        if action_name in {
            "repair-closeout-validate",
            "candidate-rework-apply",
            "failure-closeout-activate",
        }:
            return (30, action_name, str(action.get("checkpoint_id") or ""))
        return (40, action_name, str(action.get("checkpoint_id") or ""))
    if readiness == "needs_diagnosis":
        return (70, action_name, str(action.get("checkpoint_id") or ""))
    if readiness == "human_blocked":
        return (80, action_name, str(action.get("checkpoint_id") or ""))
    if readiness == "preflight_blocked":
        return (90, action_name, str(action.get("checkpoint_id") or ""))
    return (60, action_name, str(action.get("checkpoint_id") or ""))


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
    monitor_state = str(monitor.get("state") or "")
    blocking = bool(status_explain.get("blocking"))
    if monitor_state in {"blocked", "needs_human", "repair_closeout_required"}:
        blocking = True
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
    if (
        monitor_state == "healthy_waiting"
        and str(no_progress.get("status") or "") == "tripped"
        and pending_actions
    ):
        monitor_state = "diagnosis_pending"
    return redact_obj({
        "schema_version": "run-manager.monitor.v1",
        "is_derived_projection": True,
        "completion_status": str(completion_profile.get("status") or "unknown"),
        "monitor_state": monitor_state,
        "wait_reason": str(status_explain.get("wait_reason") or ""),
        "next_auto_action": str(status_explain.get("next_auto_action") or ""),
        "blocking": blocking,
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
        (
            RUN_MANAGER_AGENT_OBSERVATION,
            RUN_MANAGER_AGENT_RECOMMENDATION,
            TRIAGE_RECORDED,
        ),
    )
    if agent_event is None:
        # Runtime event windows may include archived observations before the
        # latest active prompt. Any resident response proves the agent has
        # consumed at least one turn, so do not project a false first-observation
        # stall after log rotation.
        agent_event = _last_event(
            events,
            (
                RUN_MANAGER_AGENT_OBSERVATION,
                RUN_MANAGER_AGENT_RECOMMENDATION,
                TRIAGE_RECORDED,
            ),
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
                "prompted resident agent did not emit observation, recommendation, or triage advice"
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
    if not _diagnosis_action_may_reprompt_resident(action):
        return None
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


def _diagnosis_action_may_reprompt_resident(action: dict[str, Any]) -> bool:
    """Only reprompt resident agent for resident-agent health diagnosis.

    Generic workflow/fanout attention still needs a concrete resume proposal;
    merely proving the resident pane was prompted is not evidence that the
    target worker or fanout child recovered.
    """

    failure_class = str(action.get("failure_class") or "")
    if failure_class == "run_manager_resident_agent_stalled":
        return True
    return str(action.get("suggested_route") or "") == "run_manager_resident_agent"


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
    open_attention, suppressed_attention = _suppress_stale_open_attention(
        events,
        open_attention,
    )
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
    completion_blockers: list[Any] = []
    if isinstance(completion_profile, dict):
        completion_status = str(completion_profile.get("status") or "")
        raw_blockers = completion_profile.get("blockers")
        if isinstance(raw_blockers, list):
            completion_blockers = list(raw_blockers)
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
    elif (
        _blocking_human_decisions(events)
        and not _has_auto_ready_actions(pending_actions)
    ):
        state = "needs_human"
    elif _has_pending_repair_closeout(events):
        state = "repair_closeout_required"
    elif any(event.type in {RUN_MANAGER_REPAIR_ACCEPTED, "autoresearch.repair.dispatched"} for event in events[-20:]):
        state = "repair_in_flight"
    elif completion_status == "blocked" or completion_blockers:
        state = "blocked"
    elif open_attention and not pending_actions:
        state = "silent_stall"
    closeout_projection_status = "not_applicable"
    if completion_status == "complete":
        closeout_projection_status = (
            "needs_reconciliation"
            if residual_in_flight or residual_open_attention else "clear"
        )
    return {
        "schema_version": "run-manager.monitor.v1",
        "state": state,
        "closeout_projection_status": closeout_projection_status,
        "current_phase": _derive_phase(events),
        "lane_occupancy": dict(sorted(lane_counts.items())),
        "in_flight_tasks": display_in_flight,
        "residual_in_flight_tasks": residual_in_flight,
        "open_attention": display_open_attention,
        "residual_open_attention": residual_open_attention,
        "suppressed_open_attention": suppressed_attention[-20:],
        "pending_actions": len(pending_actions),
        "last_action": _event_summary(last_action) if last_action else {},
        "next_wait": _next_wait(state, pending_actions, open_attention),
    }


_ATTENTION_RECOVERY_EVENTS = frozenset({
    "worker.heartbeat",
    "worker.state.changed",
    "task.assigned",
    "task.dispatched",
    "task.rework.requested",
    "dev.build.done",
    "task.ref.updated",
    "workflow.resume.applied",
    "fanout.started",
    "fanout.child.completed",
    "fanout.aggregate.completed",
    "lane.stage.completed",
    "candidate.ready",
    "verify.passed",
    "test.passed",
    "judge.passed",
    RUN_COMPLETED,
    "task.done",
    "task.done.accepted",
})


def _suppress_stale_open_attention(
    events: list[ZfEvent],
    open_attention: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for item in open_attention:
        evidence = _stale_attention_recovery_evidence(events, item)
        if evidence:
            suppressed.append({
                **evidence,
                "fingerprint": str(item.get("fingerprint") or ""),
                "attention_id": str(item.get("attention_id") or item.get("id") or ""),
                "status": "false_positive",
                "confidence": "high",
            })
        else:
            kept.append(item)
    return kept, suppressed


def _stale_attention_recovery_evidence(
    events: list[ZfEvent],
    item: dict[str, Any],
) -> dict[str, Any]:
    source_event_id = _attention_source_event_id(item)
    source_idx = next(
        (idx for idx, event in enumerate(events) if event.id == source_event_id),
        -1,
    )
    if source_idx < 0:
        return {}
    task_id = str(item.get("task_id") or "").strip()
    lane = str(
        item.get("lane")
        or item.get("assignee")
        or item.get("worker")
        or item.get("instance_id")
        or ""
    ).strip()
    for event in events[source_idx + 1:]:
        if event.type not in _ATTENTION_RECOVERY_EVENTS:
            continue
        if not _recovery_event_matches_attention(event, task_id=task_id, lane=lane):
            continue
        terminal = event.type in {
            "judge.passed",
            RUN_COMPLETED,
            "task.done",
            "task.done.accepted",
        }
        return {
            "evidence_window": {
                "source_event_id": source_event_id,
                "recovery_event_id": event.id,
            },
            "last_progress_event": _event_summary(event),
            "newer_terminal_event": _event_summary(event) if terminal else {},
        }
    return {}


def _attention_source_event_id(item: dict[str, Any]) -> str:
    raw_ids = item.get("source_event_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            text = str(value or "").strip()
            if text:
                return text
    return str(
        item.get("source_event_id")
        or item.get("trigger_event_id")
        or item.get("event_id")
        or ""
    ).strip()


def _recovery_event_matches_attention(
    event: ZfEvent,
    *,
    task_id: str,
    lane: str,
) -> bool:
    if task_id and _event_task_id(event) == task_id:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    if lane:
        values = {
            str(payload.get("worker") or ""),
            str(payload.get("assignee") or ""),
            str(payload.get("instance") or ""),
            str(payload.get("instance_id") or ""),
            str(event.actor or ""),
        }
        if lane in values:
            return True
    return not task_id and not lane


def _event_task_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(event.task_id or payload.get("task_id") or "").strip()


def _budget_status_block(state_dir: Path, *, config) -> dict[str, Any]:
    """FIX-13②(bizsim r4 F13):explain 带预算区块(cap/spent/enforcement/
    exceeded)——预算被在途燃烧穿透时 operator 一眼可见。"""
    cap = getattr(config, "global_budget_usd", None) if config else None
    if cap is None:
        return {}
    try:
        from zf.core.cost.tracker import CostTracker

        spent = float(CostTracker(state_dir / "cost.jsonl").total_usd())
    except Exception:
        spent = -1.0
    return {
        "global_budget_usd": float(cap),
        "spent_usd": round(spent, 4),
        "enforcement_enabled": bool(
            getattr(config, "budget_enforcement_enabled", True),
        ),
        "exceeded": bool(spent >= 0 and spent >= float(cap)),
    }


def _budget_diagnostics_projection(
    events: list[ZfEvent],
    *,
    budget: dict[str, Any],
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type != "cost.budget.exceeded":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        scope = str(payload.get("scope") or "global").strip() or "global"
        role = str(payload.get("role") or payload.get("role_name") or "").strip()
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        budget_usd = _float_or_none(payload.get("budget_usd"))
        current_usd = _float_or_none(
            payload.get("current_usd")
            if payload.get("current_usd") is not None
            else payload.get("spent_usd")
        )
        key = ":".join([
            "cost.budget.exceeded",
            scope,
            role or "_",
            "" if budget_usd is None else f"{budget_usd:.4f}",
        ])
        row = rows.setdefault(key, {
            "fingerprint": key,
            "scope": scope,
            "role": role,
            "task_id": task_id,
            "budget_usd": budget_usd,
            "current_usd": current_usd,
            "level": "exceeded",
            "first_event_id": event.id,
            "first_seen_at": event.ts,
            "latest_event_id": event.id,
            "latest_seen_at": event.ts,
            "event_count": 0,
            "notification_policy": "owner_on_human_required",
            "recovery_policy": "run_manager",
            "owner_visible_default": False,
            "recommended_action": "diagnose_budget_exceeded",
        })
        row["event_count"] = int(row.get("event_count") or 0) + 1
        row["latest_event_id"] = event.id
        row["latest_seen_at"] = event.ts
        if current_usd is not None:
            row["current_usd"] = current_usd
        if not row.get("task_id") and task_id:
            row["task_id"] = task_id
    items = sorted(rows.values(), key=lambda item: str(item.get("latest_seen_at") or ""))
    return redact_obj({
        "schema_version": "run-manager.budget-diagnostics.v1",
        "is_derived_projection": True,
        "status": "exceeded" if items or bool(budget.get("exceeded")) else "clear",
        "budget": budget,
        "items": items,
        "summary": {
            "open": len(items),
            "event_count": sum(int(item.get("event_count") or 0) for item in items),
            "owner_visible_default": False,
            "notification_policy": "owner_on_human_required",
        },
    })


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return round(float(value), 4)
    except Exception:
        return None


def build_run_status_explain_projection(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    budget: dict[str, Any] | None = None,
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
        "budget": budget or {},
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
            "task_attempts": "projections/task_attempts.json",
        },
    })


def build_run_goal_projection(
    events: list[ZfEvent],
    *,
    blocker_threshold: int = 3,
    run_id: str = "",
) -> dict[str, Any]:
    """Build one run's goal projection from a shared runtime event stream.

    ``state_dir`` can contain sequential or concurrent workflow requests.
    Legacy unscoped facts remain valid only when the log has exactly one known
    run; in multi-run histories the caller must supply or derive a run identity.
    """

    from zf.runtime.run_scope import (
        events_for_run,
        known_run_ids,
        resolve_run_for_event,
        resolve_run_id,
    )

    all_events = list(events)
    requested_run_id = str(run_id or "").strip()
    selected_run_id = resolve_run_id(all_events, requested_run_id)
    if not selected_run_id and not requested_run_id:
        for event in reversed(all_events):
            if event.type not in {
                "run.goal.started",
                "run.goal.updated",
                "run.goal.completed",
                "run.goal.blocked",
            }:
                continue
            selected_run_id = resolve_run_for_event(all_events, event)
            if selected_run_id:
                break
    if not selected_run_id and not requested_run_id:
        known = known_run_ids(all_events)
        if len(known) == 1:
            selected_run_id = next(iter(known))
    scoped_events = (
        events_for_run(all_events, run_id=selected_run_id)
        if selected_run_id
        else ([] if requested_run_id else all_events)
    )

    status = "unknown"
    objective = ""
    active_run_id = ""
    source_event_id = ""
    blockers: Counter[str] = Counter()
    last_blocker: dict[str, Any] = {}
    for event in scoped_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "run.goal.started":
            status = "active"
            active_run_id = str(
                payload.get("run_id") or event.correlation_id or event.id
            )
            objective = str(payload.get("objective") or "")
            source_event_id = event.id
        elif event.type == "run.goal.updated":
            status = str(payload.get("status") or status or "active")
            objective = str(payload.get("objective") or objective)
            active_run_id = str(payload.get("run_id") or active_run_id)
            source_event_id = event.id
        elif event.type == "run.goal.completed":
            status = "complete"
            active_run_id = str(payload.get("run_id") or active_run_id)
            source_event_id = event.id
        elif event.type == "run.goal.blocked":
            status = "blocked"
            active_run_id = str(payload.get("run_id") or active_run_id)
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
    if status == "unknown" and any(
        event.type == "loop.started" for event in scoped_events
    ):
        status = "active"
    blocked_ready = bool(
        status != "complete"
        and blockers
        and max(blockers.values()) >= blocker_threshold
    )
    from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs

    handoff = reduce_attempt_handoffs(
        scoped_events,
        workflow_run_id=selected_run_id,
    )
    completion_gate_status = "not_claimed"
    for event in scoped_events:
        if event.type == RUN_GOAL_COMPLETION_CLAIMED:
            completion_gate_status = "claimed"
        elif event.type == RUN_GOAL_COMPLETION_BLOCKED:
            completion_gate_status = "blocked"
        elif event.type == RUN_GOAL_COMPLETION_REJECTED:
            completion_gate_status = "rejected"
        elif event.type == "run.goal.completed":
            completion_gate_status = "passed"
    return {
        "schema_version": RUN_GOAL_SCHEMA_VERSION,
        "is_derived_projection": True,
        "run_id": selected_run_id or active_run_id,
        "objective": objective,
        "status": status,
        "blocked_ready": blocked_ready,
        "blocker_threshold": blocker_threshold,
        "last_blocker": last_blocker,
        "source_event_id": source_event_id,
        "delivery_phase": handoff["delivery_phase"],
        "open_feedback_count": handoff["open_feedback_count"],
        "pending_handoff_count": handoff["pending_handoff_count"],
        "completion_gate_status": completion_gate_status,
        "attempt_handoff_schema_version": handoff["schema_version"],
    }


def run_goal_completion_claim_event(
    events: list[ZfEvent], *, cause: ZfEvent
) -> ZfEvent | None:
    """Turn a semantic Judge verdict into a non-terminal completion claim."""

    from zf.runtime.run_scope import resolve_run_for_event

    payload = cause.payload if isinstance(cause.payload, dict) else {}
    result = (
        payload.get("goal_closure_result")
        if isinstance(payload.get("goal_closure_result"), dict)
        else {}
    )
    run_id = str(result.get("workflow_run_id") or payload.get("workflow_run_id") or "")
    run_id = run_id or resolve_run_for_event(events, cause)
    if not run_id:
        # Several concurrent runs plus an unscoped Judge is ambiguous. Do not
        # let it close whichever run happened to be most recently projected.
        return None
    proj = build_run_goal_projection(events, run_id=run_id)
    run_id = str(proj.get("run_id") or run_id)
    if proj.get("status") != "active" or not run_id:
        return None
    goal_id = str(
        result.get("goal_id")
        or payload.get("goal_id")
        or payload.get("feature_id")
        or payload.get("pdd_id")
        or ""
    )
    target_commit = str(
        result.get("target_commit")
        or payload.get("target_commit")
        or payload.get("candidate_head_commit")
        or payload.get("source_commit")
        or ""
    )
    task_map_generation = str(
        result.get("task_map_generation")
        or payload.get("task_map_generation")
        or ""
    )
    admitted_ref = payload.get("admitted_call_result_ref")
    admitted_ref = dict(admitted_ref) if isinstance(admitted_ref, Mapping) else {}
    control_ref = payload.get("control_result_ref")
    control_ref = dict(control_ref) if isinstance(control_ref, Mapping) else {}
    claim_semantics = {
        "run_id": run_id,
        "goal_id": goal_id,
        "task_map_generation": task_map_generation,
        "target_commit": target_commit,
        "goal_claim_set_digest": str(result.get("goal_claim_set_digest") or ""),
        "closure_fact_digest": str(result.get("closure_fact_digest") or ""),
        "admitted_call_result_digest": str(admitted_ref.get("sha256") or ""),
    }
    claim_id = "goal-claim-" + hashlib.sha256(
        json.dumps(
            claim_semantics,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    if any(
        event.type == RUN_GOAL_COMPLETION_CLAIMED
        and isinstance(event.payload, dict)
        and str(event.payload.get("claim_id") or "") == claim_id
        for event in events
    ):
        return None
    return ZfEvent(
        type=RUN_GOAL_COMPLETION_CLAIMED,
        actor="zf-cli",
        causation_id=cause.id,
        correlation_id=cause.correlation_id or run_id,
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "goal_id": goal_id,
            "pdd_id": str(payload.get("pdd_id") or goal_id),
            "feature_id": str(
                payload.get("feature_id") or payload.get("pdd_id") or goal_id
            ),
            "claim_id": claim_id,
            "objective": str(proj.get("objective") or ""),
            "claim_type": (
                "admitted_goal_closure_result"
                if result else "legacy_semantic_judge_verdict"
            ),
            "reason": f"semantic completion claim: {cause.type}",
            "source_event_id": cause.id,
            "source_event_type": cause.type,
            "task_id": str(cause.task_id or payload.get("task_id") or ""),
            "task_map_generation": task_map_generation,
            "target_commit": target_commit,
            "candidate_ref": str(result.get("candidate_ref") or payload.get("candidate_ref") or ""),
            "goal_claim_set_ref": str(result.get("goal_claim_set_ref") or ""),
            "goal_claim_set_digest": str(result.get("goal_claim_set_digest") or ""),
            "closure_fact_ref": str(result.get("closure_fact_ref") or ""),
            "closure_fact_digest": str(result.get("closure_fact_digest") or ""),
            "admitted_call_result_ref": admitted_ref,
            "control_result_ref": control_ref,
            "operation_id": str(payload.get("operation_id") or ""),
        },
    )


def run_goal_completion_gate_event(
    events: list[ZfEvent],
    *,
    claim: ZfEvent,
    required_operation_ids: list[str] | tuple[str, ...] = (),
    delivery_policy: str = "report_only",
) -> ZfEvent | None:
    """Evaluate one active claim against current mechanical truth.

    ``run.goal.completed`` remains the only successful run truth.  The gate is
    replay-safe and never lets a worker resolution claim close verifier
    feedback by itself.
    """

    if claim.type != RUN_GOAL_COMPLETION_CLAIMED:
        return None
    claim_payload = claim.payload if isinstance(claim.payload, dict) else {}
    claim_id = str(claim_payload.get("claim_id") or claim.id)
    if any(
        event.type in {"run.goal.completed", RUN_GOAL_COMPLETION_REJECTED}
        and isinstance(event.payload, dict)
        and str(event.payload.get("claim_id") or "") == claim_id
        for event in events
    ):
        return None
    from zf.runtime.run_scope import events_for_run, resolve_run_id

    run_id = resolve_run_id(events, str(claim_payload.get("run_id") or ""))
    if not run_id:
        return None
    scoped_events = events_for_run(events, run_id=run_id)
    proj = build_run_goal_projection(scoped_events, run_id=run_id)
    if proj.get("status") != "active" or not run_id:
        return None

    from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs

    handoff = reduce_attempt_handoffs(
        scoped_events,
        workflow_run_id=run_id,
    )
    blockers: list[str] = []
    if int(handoff.get("open_feedback_count") or 0):
        blockers.append("open_feedback")
    if int(handoff.get("pending_handoff_count") or 0):
        blockers.append("pending_handoff")
    blocking_human = _blocking_human_decisions(scoped_events)
    if blocking_human:
        blockers.append("pending_human_decision")
    active_attempts = [
        item
        for item in handoff.get("active_attempts") or []
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    if active_attempts:
        blockers.append("active_attempt")
    claim_target = str((claim.payload or {}).get("target_commit") or "").strip()
    verified_target = _latest_independent_verify_target(scoped_events)
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operation_views = reduce_workflow_operations(scoped_events)
    unsettled_required_operations = [
        operation_id
        for operation_id in dict.fromkeys(
            str(item).strip() for item in required_operation_ids if str(item).strip()
        )
        if str((operation_views.get(operation_id) or {}).get("status") or "")
        != "settled"
    ]
    if unsettled_required_operations:
        blockers.append("unsettled_required_operation")

    invalid_reasons = _completion_claim_invalid_reasons(
        scoped_events,
        claim_payload=claim_payload,
    )
    if claim_target and verified_target and claim_target != verified_target:
        invalid_reasons.append("verification_target_mismatch")
    invalid_reasons = list(dict.fromkeys(invalid_reasons))

    shared = {
        "run_id": run_id,
        "workflow_run_id": run_id,
        "goal_id": str(claim_payload.get("goal_id") or ""),
        "pdd_id": str(
            claim_payload.get("pdd_id") or claim_payload.get("goal_id") or ""
        ),
        "feature_id": str(
            claim_payload.get("feature_id") or claim_payload.get("pdd_id")
            or claim_payload.get("goal_id") or ""
        ),
        "claim_id": claim_id,
        "objective": str((claim.payload or {}).get("objective") or proj.get("objective") or ""),
        "claim_event_id": claim.id,
        "source_event_id": str((claim.payload or {}).get("source_event_id") or ""),
        "target_commit": claim_target,
        "verified_target_commit": verified_target,
        "delivery_phase": str(handoff.get("delivery_phase") or ""),
        "open_feedback_count": int(handoff.get("open_feedback_count") or 0),
        "pending_handoff_count": int(handoff.get("pending_handoff_count") or 0),
        "required_operation_ids": list(dict.fromkeys(required_operation_ids)),
        "unsettled_required_operation_ids": unsettled_required_operations,
        "task_map_generation": str(claim_payload.get("task_map_generation") or ""),
        "goal_claim_set_ref": str(claim_payload.get("goal_claim_set_ref") or ""),
        "goal_claim_set_digest": str(claim_payload.get("goal_claim_set_digest") or ""),
        "admitted_call_result_ref": dict(
            claim_payload.get("admitted_call_result_ref") or {}
        ) if isinstance(claim_payload.get("admitted_call_result_ref"), Mapping) else {},
        "delivery_policy": str(delivery_policy or "report_only"),
    }
    if invalid_reasons:
        return ZfEvent(
            type=RUN_GOAL_COMPLETION_REJECTED,
            actor="zf-cli",
            causation_id=claim.id,
            correlation_id=claim.correlation_id or run_id,
            payload={
                **shared,
                "invalid_reasons": invalid_reasons,
                "reason": "completion claim identity is stale or invalid",
            },
        )
    if blockers:
        fingerprint = _completion_blocker_fingerprint(claim_id, blockers, shared)
        if any(
            event.type == RUN_GOAL_COMPLETION_BLOCKED
            and isinstance(event.payload, dict)
            and str(event.payload.get("claim_id") or "") == claim_id
            and str(event.payload.get("blocker_fingerprint") or "") == fingerprint
            for event in scoped_events
        ):
            return None
        return ZfEvent(
            type=RUN_GOAL_COMPLETION_BLOCKED,
            actor="zf-cli",
            causation_id=claim.id,
            correlation_id=claim.correlation_id or run_id,
            payload={
                **shared,
                "blockers": blockers,
                "blocker_fingerprint": fingerprint,
                "blocking_human_decision_count": len(blocking_human),
                "active_attempt_count": len(active_attempts),
                "reason": "completion claim has recoverable blockers",
            },
        )

    if _delivery_requires_ship(delivery_policy):
        delivery = _delivery_status(scoped_events, claim_id=claim_id)
        if delivery == "settled":
            pass
        elif delivery == "not_requested":
            operation_id = f"delivery-{claim_id}"
            return ZfEvent(
                type="run.delivery.requested",
                actor="zf-cli",
                causation_id=claim.id,
                correlation_id=claim.correlation_id or run_id,
                payload={
                    **shared,
                    "delivery_operation_id": operation_id,
                    "candidate_ref": str(claim_payload.get("candidate_ref") or ""),
                    "reason": "completion gate requires candidate delivery",
                },
            )
        else:
            delivery_blockers = [
                "delivery_failed" if delivery == "failed" else "delivery_pending"
            ]
            fingerprint = _completion_blocker_fingerprint(
                claim_id, delivery_blockers, shared,
            )
            if any(
                event.type == RUN_GOAL_COMPLETION_BLOCKED
                and isinstance(event.payload, dict)
                and str(event.payload.get("claim_id") or "") == claim_id
                and str(event.payload.get("blocker_fingerprint") or "") == fingerprint
                for event in scoped_events
            ):
                return None
            return ZfEvent(
                type=RUN_GOAL_COMPLETION_BLOCKED,
                actor="zf-cli",
                causation_id=claim.id,
                correlation_id=claim.correlation_id or run_id,
                payload={
                    **shared,
                    "blockers": delivery_blockers,
                    "blocker_fingerprint": fingerprint,
                    "reason": "completion claim is waiting for delivery",
                },
            )
    return ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        causation_id=claim.id,
        correlation_id=claim.correlation_id or run_id,
        payload={
            **shared,
            "reason": "completion claim passed deterministic gate",
        },
    )


def _completion_claim_invalid_reasons(
    events: list[ZfEvent],
    *,
    claim_payload: Mapping[str, Any],
) -> list[str]:
    if str(claim_payload.get("claim_type") or "") != "admitted_goal_closure_result":
        return []
    required = (
        "run_id", "goal_id", "claim_id", "task_map_generation",
        "target_commit", "goal_claim_set_ref", "goal_claim_set_digest",
        "closure_fact_ref", "closure_fact_digest",
    )
    invalid = [
        f"missing_{field}"
        for field in required
        if not str(claim_payload.get(field) or "").strip()
    ]
    admitted_ref = claim_payload.get("admitted_call_result_ref")
    if not isinstance(admitted_ref, Mapping) or not str(admitted_ref.get("ref") or ""):
        invalid.append("missing_admitted_call_result_ref")
    current = None
    for event in reversed(events):
        if event.type not in {"flow.goal.closed", "module.parity.closed"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(body.get("workflow_run_id") or "") == str(claim_payload.get("run_id") or "")
            and str(body.get("goal_id") or "") == str(claim_payload.get("goal_id") or "")
        ):
            current = body
            break
    if current is None:
        invalid.append("closure_fact_missing")
        return invalid
    for claim_key, closure_key in (
        ("task_map_generation", "task_map_generation"),
        ("target_commit", "candidate_head_commit"),
        ("goal_claim_set_digest", "goal_claim_set_digest"),
        ("closure_fact_digest", "closure_fact_digest"),
    ):
        if str(claim_payload.get(claim_key) or "") != str(current.get(closure_key) or ""):
            invalid.append(f"stale_{claim_key}")
    return list(dict.fromkeys(invalid))


def _completion_blocker_fingerprint(
    claim_id: str,
    blockers: list[str],
    shared: Mapping[str, Any],
) -> str:
    body = {
        "claim_id": claim_id,
        "blockers": sorted(set(blockers)),
        "open_feedback_count": int(shared.get("open_feedback_count") or 0),
        "pending_handoff_count": int(shared.get("pending_handoff_count") or 0),
        "unsettled_required_operation_ids": sorted(
            str(item) for item in shared.get("unsettled_required_operation_ids", [])
        ),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _delivery_requires_ship(policy: str) -> bool:
    return str(policy or "").strip().lower() in {
        "ship", "ship_candidate", "candidate_ship", "code_merge", "merge",
    }


def _delivery_status(events: list[ZfEvent], *, claim_id: str) -> str:
    status = "not_requested"
    for event in events:
        if event.type not in {
            "run.delivery.requested", "run.delivery.settled",
            "run.delivery.failed", "run.delivery.blocked",
        }:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        if str(body.get("claim_id") or "") != claim_id:
            continue
        if event.type == "run.delivery.requested":
            status = "pending"
        elif event.type == "run.delivery.settled":
            status = "settled"
        else:
            status = "failed"
    return status


def _latest_independent_verify_target(events: list[ZfEvent]) -> str:
    """Return the latest explicit target accepted by an independent verifier.

    Empty targets are ignored for legacy/light flows.  The gate compares only
    when both Judge and Verify bind an immutable target, avoiding a semantic
    guess for workflows that do not produce candidate commits.
    """

    for event in reversed(events):
        if event.type not in {
            "verify.passed",
            "test.passed",
            "review.approved",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        target = str(
            payload.get("target_commit")
            or payload.get("candidate_head_commit")
            or payload.get("source_commit")
            or ""
        ).strip()
        if target:
            return target
    return ""


def run_goal_completion_event(
    events: list[ZfEvent], *, cause: ZfEvent
) -> ZfEvent | None:
    """Compatibility helper returning the gated outcome for one Judge event."""

    claim = run_goal_completion_claim_event(events, cause=cause)
    if claim is None:
        return None
    return run_goal_completion_gate_event([*events, claim], claim=claim)


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
    blocking_human = [
        item for item in pending_human
        if _is_blocking_human_decision(item)
    ]
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
    if blocking_human:
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
        "blocking_human_decisions": blocking_human,
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


def _spine_summary(projections_dir: Path) -> dict[str, Any]:
    """131-P0-6:只读引用 shadow spine 投影,展示级 enrich,不改变任何决策。"""
    summary: dict[str, Any] = {}
    for key, name in (
        ("health", "workflow_health.json"),
        ("runs", "workflow_spine.json"),
        ("task_attempts", "task_attempts.json"),
    ):
        try:
            data = json.loads((projections_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if key == "health":
            summary["counters"] = data.get("counters") or {}
            summary["last_event_ts"] = data.get("last_event_ts") or ""
        elif key == "runs":
            summary["runs"] = data.get("runs") or {}
        else:
            contexts = _task_attempt_contexts(projections_dir)
            if contexts:
                summary["task_attempts"] = {
                    "task_count": len(contexts),
                    "open_attempts": sum(
                        int(item.get("open_attempts") or 0)
                        for item in contexts.values()
                    ),
                    "counted_failures": sum(
                        int(item.get("counted_failures") or 0)
                        for item in contexts.values()
                    ),
                    # Compact run-manager projection: enough for operator/RM
                    # explain, while the full attempt ledger stays in the
                    # projection source ref.
                    "tasks": {
                        task_id: contexts[task_id]
                        for task_id in sorted(contexts)[:50]
                    },
                }
    return summary


def _task_attempt_contexts(projections_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((projections_dir / "task_attempts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        return {}
    contexts: dict[str, dict[str, Any]] = {}
    for task_id, entry in tasks.items():
        if not isinstance(entry, dict):
            continue
        attempts = entry.get("attempts")
        attempts = attempts if isinstance(attempts, list) else []
        latest = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
        contexts[str(task_id)] = {
            "schema_version": "run-manager.attempt-context.v1",
            "task_id": str(task_id),
            "source_ref": f"projections/task_attempts.json#tasks.{task_id}",
            "attempt_count": int(entry.get("attempt_count") or len(attempts)),
            "current_owner": str(entry.get("current_owner") or latest.get("role") or ""),
            "latest_attempt_key": str(
                entry.get("latest_attempt_key") or latest.get("attempt_key") or ""
            ),
            "latest_state": str(entry.get("latest_state") or latest.get("state") or ""),
            "lease_state": str(entry.get("lease_state") or latest.get("lease_state") or ""),
            "last_terminal": str(entry.get("last_terminal") or ""),
            "open_attempts": int(entry.get("open_attempts") or 0),
            "counted_failures": int(entry.get("counted_failures") or 0),
        }
    return contexts


def _action_with_attempt_context(
    action: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    task_id = str(action.get("task_id") or "")
    if task_id and contexts.get(task_id):
        matched.append(contexts[task_id])
    for key in ("failed_children", "pending_children", "completed_task_ids"):
        for raw in action.get(key) or []:
            ref = str(raw or "")
            if not ref:
                continue
            context = contexts.get(ref)
            if context is None:
                context = next(
                    (
                        item for candidate, item in contexts.items()
                        if ref.endswith(candidate)
                    ),
                    None,
                )
            if context is not None and context not in matched:
                matched.append(context)
    if not matched:
        return action
    updated = dict(action)
    if task_id and matched:
        updated.setdefault("attempt_context", matched[0])
    if matched:
        updated.setdefault("attempt_contexts", matched[:10])
    source_refs = [
        str(item) for item in updated.get("source_refs") or []
        if str(item).strip()
    ]
    for context in matched:
        ref = str(context.get("source_ref") or "")
        if ref and ref not in source_refs:
            source_refs.append(ref)
    if source_refs:
        updated["source_refs"] = source_refs
    return updated


def write_run_manager_projections(state_dir: Path, projection: dict[str, Any]) -> None:
    state_dir = Path(state_dir)
    projections_dir = state_dir / "projections"
    spine = _spine_summary(projections_dir)
    if spine:
        projection = {**projection, "spine_summary": spine}
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
        "manifest_ref": str(action.get("manifest_ref") or ""),
        "output_dir": str(action.get("output_dir") or ""),
        "limit": action.get("limit") if action.get("limit") is not None else None,
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
        # FIX-3(bizsim r4 F1/F8):① blocking_scope——无 workflow 锚点的
        # 审批不冻结全 run 派发(side);② approval_command——给 operator
        # 确切的批准入口(r4 教训:直调目标动作活干成但 lease 不放)。
        "blocking_scope": (
            "run"
            if any(
                str(action.get(key) or "").strip()
                for key in ("task_id", "stage_id", "lane", "fanout_id", "run_id", "pdd_id")
            )
            else "side"
        ),
        "approval_command": (
            "POST /api/projects/<id>/actions/"
            "human-decision-approve-controlled-action "
            f"payload={{\"decision_token\": \"{_human_decision_token(action)}\"}}"
        ),
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
) -> tuple[int, int, int, int, int, int, int]:
    consumed = 0
    autoresearch_requested = 0
    reflects_completed = 0
    repairs_accepted = 0
    actions_applied = 0
    actions_blocked = 0
    actions_failed = 0
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
                blocked_event = _emit_source_repair_not_allowed(
                    writer,
                    action,
                    payload,
                    causation_id=event.id,
                    reason=reason,
                )
                downstream_event_ids.append(blocked_event.id)
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
        elif route == "controlled_action":
            decision = action.get("policy_decision")
            decision = decision if isinstance(decision, dict) else {}
            decision_name = str(decision.get("decision") or "")
            if decision_name == "auto_decide":
                before = len(writer.event_log.read_all())
                action_status = _execute_controlled_run_action(
                    state_dir=state_dir,
                    writer=writer,
                    config=config,
                    project_root=project_root,
                    action=action,
                    causation_id=causation_id,
                )
                after_events = writer.event_log.read_all()[before:]
                downstream_event_ids.extend(
                    item.id for item in after_events
                    if item.type in {
                        RUN_MANAGER_ACTION_APPLIED,
                        RUN_MANAGER_ACTION_BLOCKED,
                        RUN_MANAGER_ACTION_FAILED,
                        RUN_MANAGER_ACTION_VERIFY_PASSED,
                        RUN_MANAGER_ACTION_VERIFY_FAILED,
                        "workflow.resume.applied",
                        RUN_MANAGER_RESIDENT_PROMPTED,
                        "worker.respawn.requested",
                    }
                )
                if action_status == "applied":
                    actions_applied += 1
                elif action_status == "blocked":
                    actions_blocked += 1
                else:
                    actions_failed += 1
                reason = f"controlled_action_status={action_status}"
            elif decision_name == "needs_diagnosis":
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
                        item.id for item in after_events
                        if item.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
                    )
                    reason = "controlled_action_requires_diagnosis"
                else:
                    reason = "controlled_action_diagnosis_request_already_exists"
            elif decision_name in {"human_escalate", "needs_approval", "safe_halt"}:
                status = "blocked"
                reason = str(decision.get("reason") or "controlled action requires owner decision")
                _emit_blocked_action(
                    writer,
                    action,
                    causation_id=causation_id,
                    reason=reason,
                    human=decision_name in {"human_escalate", "needs_approval"},
                )
                actions_blocked += 1
            else:
                status = "blocked"
                reason = f"controlled action has unsupported policy decision {decision_name!r}"
                _emit_blocked_action(
                    writer,
                    action,
                    causation_id=causation_id,
                    reason=reason,
                    human=False,
                )
                actions_blocked += 1
        elif route == "human":
            if (
                str(action.get("safe_resume_action") or "")
                == "needs_terminal_closeout"
                and _current_terminal_signal(events) is not None
            ):
                status = "wait"
                reason = "terminal closeout is handled by deterministic run manager closeout"
            else:
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
    return (
        consumed,
        autoresearch_requested,
        reflects_completed,
        repairs_accepted,
        actions_applied,
        actions_blocked,
        actions_failed,
    )


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


def _emit_source_repair_not_allowed(
    writer: EventWriter,
    action: dict[str, Any],
    payload: dict[str, Any],
    *,
    causation_id: str,
    reason: str,
) -> ZfEvent:
    checkpoint_id = str(action.get("checkpoint_id") or payload.get("checkpoint_id") or "")
    fingerprint = str(action.get("fingerprint") or payload.get("fingerprint") or "")
    title = str(
        payload.get("title")
        or action.get("title")
        or "Run Manager source repair is disabled"
    )
    summary = str(
        payload.get("summary")
        or action.get("summary")
        or "Run Manager diagnosed a source-level repair, but source repair is disabled."
    )
    approval_ref = "source-repair:" + hashlib.sha1(
        (checkpoint_id or fingerprint or causation_id).encode("utf-8")
    ).hexdigest()[:12]
    return writer.emit(
        "approval.requested",
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "approval.requested.v1",
            "approval_ref": approval_ref,
            "source_role": "run_manager",
            "owner_route": "run_manager",
            "title": title,
            "summary": summary,
            "reason": reason,
            "checkpoint_id": checkpoint_id,
            "fingerprint": fingerprint,
            "source_event_id": causation_id,
            "repair_not_allowed": True,
            "repair_permission": {
                "required_config": "runtime.run_manager.source_repair.enabled",
                "current": False,
                "allowed_operator_actions": [
                    "approve_source_repair_once",
                    "create_backlog_only",
                    "snooze",
                ],
            },
            "patch_area": payload.get("patch_area") or action.get("patch_area") or [],
            "minimal_repro": payload.get("minimal_repro") or action.get("minimal_repro") or "",
            "approve_action": "approve-source-repair-once",
            "reject_action": "create-backlog-only",
        },
    )


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


def _controlled_action_name_from_recommendation(payload: dict[str, Any]) -> str:
    requested = payload.get("controlled_action") or payload.get("action")
    if isinstance(requested, dict):
        requested = requested.get("action") or requested.get("kind")
    requested_name = str(requested or "").strip()
    supported = {
        "workflow-task-resume",
        "workflow-batch-resume",
        "worker-lifecycle-recover",
        "repair-closeout-validate",
        "resident-agent-reprompt",
        "candidate-rework-apply",
        "failure-closeout-activate",
        "diagnose-attention",
        # ZF-E2E-PRDCTL-P2-8 手术动作(router 恒 needs_approval)
        "payload-repair-reemit",
        "briefing-redeliver",
        "human-decision-dismiss",
        "ship-retry",
    }
    if requested_name in supported:
        return requested_name
    safe_action = str(payload.get("safe_resume_action") or "").strip()
    if safe_action in _SAFE_TASK_ACTIONS:
        return "workflow-task-resume"
    if safe_action in _SAFE_BATCH_ACTIONS:
        return "workflow-batch-resume"
    safe_to_action = {
        "worker_lifecycle_recover": "worker-lifecycle-recover",
        "repair_closeout_validate": "repair-closeout-validate",
        "resident_agent_reprompt": "resident-agent-reprompt",
        "failure_closeout_activate": "failure-closeout-activate",
        "diagnose_attention": "diagnose-attention",
    }
    return safe_to_action.get(safe_action, "agent-recommendation")


def _action_from_agent_recommendation(
    event: ZfEvent,
    payload: dict[str, Any],
) -> dict[str, Any]:
    checkpoint_id = str(
        payload.get("checkpoint_id")
        or payload.get("source_action_checkpoint_id")
        or "agent-recommendation-" + hashlib.sha1((event.id or "").encode("utf-8")).hexdigest()[:12]
    )
    route = _recommendation_route(payload)
    safe_resume_action = str(payload.get("safe_resume_action") or "agent_recommendation")
    action_name = (
        _controlled_action_name_from_recommendation(payload)
        if route == "controlled_action" else "agent-recommendation"
    )
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": action_name,
        "safe_resume_action": safe_resume_action,
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
    for key in (
        "task_id",
        "fanout_id",
        "pdd_id",
        "feature_id",
        "stage_id",
        "trace_id",
        "tmux_session",
        "session_mode",
        "briefing_path",
        "instance_id",
        "role_instance",
        "briefing_ref",
        "candidate_ref",
        "candidate_head_commit",
        "candidate_base_commit",
        "override_task_map_ref",
        "manifest_ref",
        "worktree_path",
        "queue_id",
    ):
        if payload.get(key) is not None:
            action[key] = payload.get(key)
    if payload.get("mutating_resume_supported") is not None:
        action["mutating_resume_supported"] = bool(payload.get("mutating_resume_supported"))
    expected_downstream = _string_list(payload.get("expected_downstream_events"))
    if expected_downstream:
        action["expected_downstream_events"] = expected_downstream
    if route == "controlled_action":
        if action_name in {"workflow-task-resume", "workflow-batch-resume"}:
            action.update(router_classify_recovery_context(action))
        elif action_name == "diagnose-attention":
            action.update({
                "safe_resume_action": "diagnose_attention",
                "failure_class": str(action.get("failure_class") or "resident_agent_recommendation"),
                "owner_route": "run_manager",
                "action_policy": "needs_diagnosis",
                "intervention_class": "diagnose",
                "expected_downstream_events": sorted(_expected_downstream_events("diagnose_attention")),
            })
        action["preflight"] = preflight_action(
            action=action_name,
            payload=action,
            mutating_resume_supported=bool(action.get("mutating_resume_supported")),
        )
        action["policy_decision"] = router_decide_action_policy(
            action=action_name,
            payload=action,
            mutating_resume_supported=bool(action.get("mutating_resume_supported")),
        )
    return action


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
    recovery_case_id = recovery_case_id_from_payload(action)
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
        "recovery_case_id": recovery_case_id,
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
        "workflow_run_id": str(action.get("workflow_run_id") or ""),
        "run_id": str(action.get("run_id") or ""),
        "trace_id": str(action.get("trace_id") or ""),
        "failure_scope": str(action.get("failure_scope") or ""),
        "plan_admission_incident_id": str(action.get("plan_admission_incident_id") or ""),
        "expected_fault": bool(action.get("expected_fault")),
        "original_trigger_event_id": str(action.get("original_trigger_event_id") or ""),
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
    if action_name == TRIAGE_REQUEST_ACTION:
        return _execute_orchestrator_triage_request(
            writer=writer,
            action=action,
            causation_id=causation_id,
            state_dir=state_dir,
            config=config,
        )
    if action_name == TRIAGE_APPLY_ACTION:
        return _execute_orchestrator_triage_advice(
            writer=writer,
            action=action,
            causation_id=causation_id,
            state_dir=state_dir,
            config=config,
        )
    if action_name == SEMANTIC_REPLAN_ACTION:
        return _execute_semantic_replan_request(
            writer=writer,
            action=action,
            causation_id=causation_id,
            state_dir=state_dir,
            config=config,
        )
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
    if action_name == "failure-closeout-activate":
        return _execute_operator_controlled_action(
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


def _execute_orchestrator_triage_request(
    *,
    writer: EventWriter,
    action: dict[str, Any],
    causation_id: str,
    state_dir: Path,
    config: ZfConfig,
) -> str:
    context_ref = _write_recovery_context_for_action(
        state_dir=state_dir,
        writer=writer,
        action=action,
        source_event_id=causation_id,
    )
    if context_ref is None:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason="semantic triage recovery context could not be materialized",
            human=False,
        )
        return "blocked"
    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=causation_id,
        payload={"schema_version": "run-manager.action.v1", **_action_payload(action)},
    )
    requested = writer.emit(
        TRIAGE_REQUESTED,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=planned.id,
        correlation_id=str(action.get("request_id") or "") or None,
        payload={
            "schema_version": "orchestrator.rework-triage.request.v1",
            "request_id": str(action.get("request_id") or ""),
            "checkpoint_id": str(action.get("checkpoint_id") or ""),
            "task_id": str(action.get("task_id") or ""),
            "role": str(action.get("role") or ""),
            "failure_fingerprint": str(action.get("fingerprint") or ""),
            "failure_count": int(action.get("failure_count") or 0),
            "failure_event_ids": _string_list(action.get("failure_event_ids")),
            "source_event_ids": _string_list(action.get("source_event_ids")),
            "trigger_event_type": str(action.get("trigger_event_type") or ""),
            "summary": str(action.get("summary") or ""),
            "recovery_context_ref": context_ref,
            "owner_route": "run_manager",
            "apply_policy": "proposal_only",
            "allowed_recommendations": [
                "continue_rework",
                "precise_rework",
                "revise_contract",
                "split_task",
                "replan",
                "diagnose",
                "human",
            ],
        },
    )
    outcome = writer.emit(
        RUN_MANAGER_ACTION_APPLIED,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied",
            "event_id": requested.id,
            "emitted_event_ids": [requested.id],
        },
    )
    _post_verify_action(
        writer,
        action,
        {"ok": True, "status": "applied", "emitted_event_ids": [requested.id]},
        causation_id=outcome.id,
        state_dir=state_dir,
        config=config,
    )
    return "applied"


def _execute_semantic_replan_request(
    *,
    writer: EventWriter,
    action: dict[str, Any],
    causation_id: str,
    state_dir: Path,
    config: ZfConfig,
) -> str:
    trigger = str(action.get("semantic_replan_trigger") or "").strip()
    task_map_ref = str(action.get("task_map_ref") or "").strip()
    pdd_id = str(action.get("pdd_id") or action.get("feature_id") or "").strip()
    if not trigger or not task_map_ref or not pdd_id:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason="semantic replan requires trigger, pdd_id, and task_map_ref",
            human=False,
        )
        return "blocked"
    context_ref = _write_recovery_context_for_action(
        state_dir=state_dir,
        writer=writer,
        action=action,
        source_event_id=causation_id,
    )
    if context_ref is None:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason="semantic replan recovery context could not be materialized",
            human=False,
        )
        return "blocked"
    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=causation_id,
        payload={"schema_version": "run-manager.action.v1", **_action_payload(action)},
    )
    request = writer.emit(
        trigger,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=planned.id,
        correlation_id=str(action.get("request_id") or "") or None,
        payload={
            "schema_version": "semantic-replan-request.v1",
            "request_id": str(action.get("request_id") or ""),
            "checkpoint_id": str(action.get("checkpoint_id") or ""),
            "pdd_id": pdd_id,
            "feature_id": str(action.get("feature_id") or pdd_id),
            "trace_id": str(action.get("trace_id") or action.get("request_id") or ""),
            "task_id": str(action.get("task_id") or ""),
            "task_map_ref": task_map_ref,
            "source_index_ref": str(action.get("source_index_ref") or ""),
            "source_commit": str(action.get("source_commit") or ""),
            "candidate_base_commit": str(action.get("candidate_base_commit") or ""),
            "candidate_ref": str(action.get("candidate_ref") or ""),
            "target_ref": str(action.get("target_ref") or action.get("candidate_ref") or ""),
            "recommended_action": str(action.get("recommended_action") or "replan"),
            "guidance": str(action.get("guidance") or ""),
            "failure_fingerprint": str(action.get("fingerprint") or ""),
            "failure_count": int(action.get("failure_count") or 0),
            "failure_event_ids": _string_list(action.get("failure_event_ids")),
            "source_event_ids": _string_list(action.get("source_event_ids")),
            "affected_task_ids": _string_list(action.get("affected_task_ids")),
            "supersedes_task_ids": _string_list(action.get("supersedes_task_ids")),
            "supersede_policy": "replace_failed_task_when_gap_tasks_materialized",
            "recovery_context_ref": context_ref,
            "semantic_replan_stage_id": str(action.get("semantic_replan_stage_id") or ""),
            "semantic_replan_role": str(action.get("semantic_replan_role") or ""),
            "owner_route": "run_manager",
            "apply_policy": "artifact_first",
            "source": "run_manager_semantic_replan",
        },
    )
    outcome = writer.emit(
        RUN_MANAGER_ACTION_APPLIED,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied",
            "event_id": request.id,
            "emitted_event_ids": [request.id],
            "recovery_context_ref": context_ref,
        },
    )
    _post_verify_action(
        writer,
        action,
        {"ok": True, "status": "applied", "emitted_event_ids": [request.id]},
        causation_id=outcome.id,
        state_dir=state_dir,
        config=config,
    )
    return "applied"


def _execute_orchestrator_triage_advice(
    *,
    writer: EventWriter,
    action: dict[str, Any],
    causation_id: str,
    state_dir: Path,
    config: ZfConfig,
) -> str:
    task_id = str(action.get("task_id") or "")
    role = str(action.get("role") or "")
    recommendation = str(action.get("recommended_action") or "")
    if recommendation not in {"continue_rework", "precise_rework"}:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason=(
                "semantic replan advice requires a materialized plan artifact "
                "before apply"
            ),
            human=False,
        )
        return "blocked"
    task_store = TaskStore(Path(state_dir) / "kanban.json")
    task = task_store.get(task_id)
    if task is None or not role:
        _emit_blocked_action(
            writer,
            action,
            causation_id=causation_id,
            reason="triage rework apply requires an existing task and target role",
            human=False,
        )
        return "blocked"
    planned = writer.emit(
        RUN_MANAGER_ACTION_PLANNED,
        actor="run-manager",
        task_id=task_id,
        causation_id=causation_id,
        payload={"schema_version": "run-manager.action.v1", **_action_payload(action)},
    )
    task_store.update(task_id, status="in_progress", assigned_to=role)
    rework = writer.emit(
        "task.rework.requested",
        actor="run-manager",
        task_id=task_id,
        causation_id=planned.id,
        correlation_id=str(action.get("request_id") or "") or None,
        payload={
            "schema_version": "task-rework-request.v1",
            "source": "orchestrator_rework_triage",
            "request_id": str(action.get("request_id") or ""),
            "task_id": task_id,
            "role": role,
            "assignee": role,
            "failure_fingerprint": str(action.get("fingerprint") or ""),
            "recommended_action": recommendation,
            "guidance": str(action.get("guidance") or ""),
            "recorded_event_id": str(action.get("recorded_event_id") or ""),
            "source_event_ids": _string_list(action.get("source_event_ids")),
            "recovery_owner": "run_manager",
        },
    )
    assigned = writer.emit(
        "task.assigned",
        actor="run-manager",
        task_id=task_id,
        causation_id=rework.id,
        correlation_id=str(action.get("request_id") or "") or None,
        payload={
            "task_id": task_id,
            "role": role,
            "assignee": role,
            "source": "orchestrator_rework_triage",
            "rework_request_event_id": rework.id,
            "failure_fingerprint": str(action.get("fingerprint") or ""),
        },
    )
    outcome = writer.emit(
        RUN_MANAGER_ACTION_APPLIED,
        actor="run-manager",
        task_id=task_id,
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "applied",
            "event_id": assigned.id,
            "emitted_event_ids": [rework.id, assigned.id],
        },
    )
    _post_verify_action(
        writer,
        action,
        {
            "ok": True,
            "status": "applied",
            "emitted_event_ids": [rework.id, assigned.id],
        },
        causation_id=outcome.id,
        state_dir=state_dir,
        config=config,
    )
    return "applied"


def _execute_operator_controlled_action(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig,
    project_root: Path | None,
    action: dict[str, Any],
    causation_id: str,
) -> str:
    from zf.runtime.control_actions import ControlledActionService

    action_name = str(action.get("action") or "")
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
            action=action_name,
            requested_action=action_name,
            payload=_operator_controlled_action_payload(action),
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
    if status in {"blocked", "approval_required"}:
        event_type = RUN_MANAGER_ACTION_BLOCKED
    emitted_result = {
        **result,
        "emitted_event_ids": [
            str(result.get("event_id") or ""),
            *[
                str(value) for value in result.get("emitted_event_ids") or []
                if str(value).strip()
            ],
        ],
    }
    outcome = writer.emit(
        event_type,
        actor="run-manager",
        causation_id=planned.id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": status,
            "controlled_action_result": result,
            "event_id": str(result.get("event_id") or ""),
            "reason": str(result.get("reason") or ""),
        },
    )
    _post_verify_action(
        writer,
        action,
        emitted_result,
        causation_id=outcome.id,
        state_dir=state_dir,
        config=config,
    )
    return "applied" if ok else "blocked" if event_type == RUN_MANAGER_ACTION_BLOCKED else "failed"


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
    _post_verify_action(
        writer,
        action,
        result,
        causation_id=outcome.id,
        state_dir=state_dir,
        config=config,
    )
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
    if str(action.get("semantic_triage_request_id") or ""):
        context_ref = _write_recovery_context_for_action(
            state_dir=state_dir,
            writer=writer,
            action=action,
            source_event_id=causation_id,
        )
        if context_ref is None:
            _emit_blocked_action(
                writer,
                action,
                causation_id=causation_id,
                reason="resident semantic triage context could not be materialized",
                human=False,
            )
            return "blocked"
        action = {
            **action,
            "recovery_context_ref": context_ref,
            "source_ref": str(context_ref.get("ref") or ""),
        }
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
    semantic_request_id = str(action.get("semantic_triage_request_id") or "").strip()
    if semantic_request_id:
        task_id = str(action.get("task_id") or "").strip()
        fingerprint = str(action.get("fingerprint") or "").strip()
        lines.extend([
            "",
            "这是第三次同指纹失败的 proposal-only 语义分诊。读取 source_ref 指向的 "
            "recovery context；不要直接改 TaskStore、重派任务或触发 replan。",
            "选择且只选择一个 recommended_action: continue_rework, precise_rework, "
            "revise_contract, split_task, replan, diagnose, human。",
            "通过 `zf emit orchestrator.rework.triage.recorded` 返回建议，payload 必须包含:",
            f"- request_id: `{semantic_request_id}`",
            f"- task_id: `{task_id}`",
            f"- failure_fingerprint: `{fingerprint}`",
            "- recommended_action、guidance、apply_policy=proposal_only、evidence_event_ids。",
            "发出该事件后结束本 turn。",
        ])
    return "\n".join(lines)


def _write_recovery_context_for_action(
    *,
    state_dir: Path,
    writer: EventWriter,
    action: dict[str, Any],
    source_event_id: str,
) -> dict[str, Any] | None:
    task_id = str(action.get("task_id") or "").strip()
    request_id = str(
        action.get("request_id")
        or action.get("semantic_triage_request_id")
        or action.get("checkpoint_id")
        or ""
    ).strip()
    if not task_id or not request_id:
        return None
    try:
        from zf.runtime.event_window import read_runtime_events

        return write_task_recovery_context(
            state_dir,
            read_runtime_events(writer.event_log, state_dir),
            task_id=task_id,
            failure_event_ids=_string_list(action.get("failure_event_ids")),
            request_id=request_id,
            source_event_id=source_event_id,
        )
    except Exception:
        return None


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
    state_dir: Path | None = None,
    config: ZfConfig | None = None,
) -> None:
    expected = {
        str(value) for value in action.get("expected_downstream_events") or []
        if str(value).strip()
    }
    if not expected:
        expected = _expected_downstream_events(str(action.get("safe_resume_action") or ""))
    emitted = _observed_event_types_for_action(
        writer.event_log,
        action,
        result,
        expected=expected,
    )
    passed = bool(expected.intersection(emitted))
    failure_reason = "" if passed else "expected downstream event not observed"
    resume_pending_reason = ""
    closeout_observed = bool(emitted.intersection({
        "task.done",
        "task.done.accepted",
        "test.passed",
        "verify.passed",
        "judge.passed",
        "flow.goal.closed",
    })) or _workflow_resume_applied_closeout_observed(writer, action)
    resume_pending_reason = _workflow_resume_checkpoint_still_pending(
        writer,
        action,
        state_dir=state_dir,
        config=config,
    )
    if resume_pending_reason and not closeout_observed:
        passed = False
        failure_reason = resume_pending_reason
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
            "reason": failure_reason,
        },
    )


def _workflow_resume_applied_closeout_observed(
    writer: EventWriter,
    action: dict[str, Any],
) -> bool:
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    source_event_id = str(action.get("source_event_id") or "").strip()
    if not checkpoint_id and not source_event_id:
        return False
    try:
        events = writer.event_log.read_all()
    except Exception:
        return False
    for event in events:
        if event.type != "workflow.resume.applied":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("reason") or "").strip() == "stage transition stalled":
            continue
        marker_values = {
            str(payload.get("checkpoint_id") or ""),
            str(payload.get("resume_checkpoint_ref") or ""),
            str(payload.get("idempotency_key") or ""),
            str(payload.get("source_event_id") or ""),
            str(event.causation_id or ""),
        }
        if checkpoint_id and checkpoint_id in marker_values:
            return True
        if source_event_id and source_event_id in marker_values:
            return True
    return False


def _workflow_resume_checkpoint_still_pending(
    writer: EventWriter,
    action: dict[str, Any],
    *,
    state_dir: Path | None,
    config: ZfConfig | None,
) -> str:
    if state_dir is None or config is None:
        return ""
    if str(action.get("action") or "") not in {
        "workflow-batch-resume",
        "workflow-task-resume",
    }:
        return ""
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    if not checkpoint_id:
        return ""
    safe_action = str(action.get("safe_resume_action") or "").strip()
    try:
        event_list = writer.event_log.read_all()
    except Exception:
        event_list = []
    if _workflow_resume_has_newer_progress(event_list, action):
        return ""
    # ZF-E2E-PRDCTL-P1-4: the filing guard now suppresses re-filing a
    # checkpoint that proved gate-unroutable, so the rebuilt projection no
    # longer shows it pending — but the applied action still failed to
    # achieve its goal. Keep post-verify honest via the event itself.
    for event in event_list:
        if event.type != "workflow.resume.gate_unroutable":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("checkpoint_idempotency_key") or "") == checkpoint_id:
            return "workflow resume gate unroutable for this checkpoint"
    try:
        projection = build_workflow_resume_projection(
            state_dir,
            config,
            events=event_list,
        )
    except Exception as exc:
        return f"workflow resume post-verify projection failed: {exc}"
    for bucket in ("checkpoints", "batch_checkpoints"):
        for item in projection.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            item_checkpoint_id = str(
                item.get("id")
                or item.get("checkpoint_id")
                or item.get("idempotency_key")
                or item.get("resume_checkpoint_ref")
                or ""
            )
            if item_checkpoint_id != checkpoint_id:
                continue
            pending_action = str(item.get("safe_resume_action") or "")
            if pending_action and pending_action != "no_action":
                if safe_action and pending_action != safe_action:
                    return (
                        "workflow resume checkpoint still pending with "
                        f"{pending_action!r} after {safe_action!r}"
                    )
                return "workflow resume checkpoint still pending after action"
    return ""


def _workflow_resume_has_newer_progress(
    events: list[ZfEvent],
    action: dict[str, Any],
) -> bool:
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    source_event_id = str(action.get("source_event_id") or "").strip()
    task_id = str(action.get("task_id") or "").strip()
    pdd_id = str(action.get("pdd_id") or action.get("feature_id") or "").strip()
    fanout_id = str(
        action.get("fanout_id") or action.get("upstream_fanout_id") or ""
    ).strip()
    source_idx = -1
    for idx, event in enumerate(events):
        if source_event_id and event.id == source_event_id:
            source_idx = idx
        payload = event.payload if isinstance(event.payload, dict) else {}
        if checkpoint_id and checkpoint_id in {
            str(payload.get("checkpoint_id") or ""),
            str(payload.get("resume_checkpoint_ref") or ""),
            str(payload.get("idempotency_key") or ""),
        }:
            source_idx = idx
    tail = events[source_idx + 1:] if source_idx >= 0 else events
    for event in tail:
        if event.type not in _ATTENTION_RECOVERY_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if task_id and _event_task_id(event) == task_id:
            return True
        if pdd_id and pdd_id in {
            str(payload.get("pdd_id") or ""),
            str(payload.get("feature_id") or ""),
        }:
            return True
        if fanout_id and fanout_id in {
            str(payload.get("fanout_id") or ""),
            str(payload.get("upstream_fanout_id") or ""),
        }:
            return True
        completed = {
            str(value) for value in payload.get("completed_task_ids") or []
            if str(value).strip()
        }
        if task_id and task_id in completed:
            return True
    return False


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


def _consume_autoresearch_results(
    state_dir: Path,
    events: list[ZfEvent],
    writer: EventWriter,
) -> int:
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
        count += _consume_autoresearch_candidate_result(
            state_dir=state_dir,
            events=events,
            writer=writer,
            request_event=requests[request_id],
            result_event=event,
            request_id=request_id,
            status=status,
        )
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


def _consume_autoresearch_candidate_result(
    *,
    state_dir: Path,
    events: list[ZfEvent],
    writer: EventWriter,
    request_event: ZfEvent,
    result_event: ZfEvent,
    request_id: str,
    status: str,
) -> int:
    """Close one unverified candidate only after structured diagnosis facts.

    A loop exit code only tells us that the diagnostic runner exited.  It is
    not evidence of a reproducible ZaoFu source defect.  The candidate crosses
    the source-repair boundary only when the loop result explicitly carries a
    confirmed reproduction, evidence refs, and a narrow repair scope.
    """

    request_payload = _safe_mapping(request_event.payload)
    recovery_case_id = recovery_case_id_from_payload(
        request_payload,
        fallback=request_id,
    )
    candidate_event = _candidate_for_recovery_case(
        events,
        request_id=request_id,
        recovery_case_id=recovery_case_id,
    )
    if candidate_event is None:
        return 0
    created_payload = _safe_mapping(candidate_event.payload)
    candidate_data = _safe_mapping(created_payload.get("candidate"))
    if not candidate_data:
        return 0
    candidate_id = str(candidate_data.get("candidate_id") or "")
    if not candidate_id or _candidate_terminal_exists(events, candidate_id):
        return 0

    candidate = candidate_from_dict(candidate_data)
    result_payload = _safe_mapping(result_event.payload)
    diagnosis = _safe_mapping(result_payload.get("diagnosis"))
    if not diagnosis:
        diagnosis = result_payload
    evidence_paths = _diagnosis_paths(diagnosis)
    repair_scope = _diagnosis_scope(diagnosis)
    reproduction = str(
        diagnosis.get("reproduction_status")
        or diagnosis.get("reproduction")
        or diagnosis.get("status")
        or ""
    ).strip().lower()

    if candidate.expected_fault or candidate.failure_scope == "plan_admission":
        candidate_status = "dismissed"
        reason = "expected plan-admission fault is not a source-repair defect"
    elif status == "failed":
        candidate_status = "dismissed"
        reason = "autoresearch diagnosis failed before reproducing a source defect"
    elif reproduction in {"confirmed", "reproduced"} and evidence_paths and repair_scope:
        candidate_status = "confirmed"
        reason = "diagnosis reproduced the defect with scoped repair evidence"
    else:
        candidate_status = "dismissed"
        reason = (
            "diagnosis completed without explicit reproduced status, evidence refs, "
            "and a narrow repair scope"
        )

    updated = candidate_with_diagnosis(
        candidate,
        status=candidate_status,
        diagnosis_evidence_paths=evidence_paths,
        repair_scope=repair_scope,
        resolution_reason=reason,
    )
    candidate_path = write_candidate_artifact(state_dir, updated)
    repair_task_payload = repair_task_payload_from_candidate(
        updated,
        candidate_path=candidate_path,
    )
    writer.emit(
        f"autoresearch.bug_candidate.{candidate_status}",
        actor="run-manager",
        task_id=result_event.task_id or request_event.task_id,
        causation_id=result_event.id,
        correlation_id=result_event.correlation_id or request_event.correlation_id,
        payload={
            "schema_version": "autoresearch.bug-candidate-status.v1",
            "candidate": updated.to_dict(),
            "candidate_id": updated.candidate_id,
            "candidate_path": str(candidate_path),
            "recovery_case_id": updated.recovery_case_id,
            "request_id": request_id,
            "source_event_id": result_event.id,
            "source_event_type": result_event.type,
            "status": candidate_status,
            "reason": reason,
            "repair_task_payload": repair_task_payload,
        },
    )
    return 1


def _candidate_for_recovery_case(
    events: list[ZfEvent],
    *,
    request_id: str,
    recovery_case_id: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != "autoresearch.bug_candidate.created":
            continue
        payload = _safe_mapping(event.payload)
        candidate = _safe_mapping(payload.get("candidate"))
        if str(candidate.get("status") or "unverified") != "unverified":
            continue
        if recovery_case_id and str(candidate.get("recovery_case_id") or "") == recovery_case_id:
            return event
        if request_id and str(candidate.get("loop_request_id") or "") == request_id:
            return event
    return None


def _candidate_terminal_exists(events: list[ZfEvent], candidate_id: str) -> bool:
    for event in events:
        if event.type not in {
            "autoresearch.bug_candidate.confirmed",
            "autoresearch.bug_candidate.dismissed",
            "autoresearch.bug_candidate.superseded",
        }:
            continue
        payload = _safe_mapping(event.payload)
        if str(payload.get("candidate_id") or "") == candidate_id:
            return True
    return False


def _diagnosis_paths(diagnosis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("evidence_paths", "evidence_refs", "artifact_refs"):
        values.extend(_string_list(diagnosis.get(key)))
    for key in ("artifact_ref", "report_ref", "proposal_ref"):
        value = str(diagnosis.get(key) or "").strip()
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _diagnosis_scope(diagnosis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("repair_scope", "affected_paths", "changed_paths", "proposed_files"):
        values.extend(_string_list(diagnosis.get(key)))
    return list(dict.fromkeys(values))


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
            status = _execute_controlled_run_action(
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
            # FIX-6(bizsim r4 F2):审批解锁后请求 level 重扫——阻塞期间
            # 被 wait 掉的 stage 触发边沿由 orchestrator reactor 补孵化
            # (_on_workflow_reconcile_requested,孵化幂等判重)。
            writer.emit(
                "workflow.reconcile.requested",
                actor="run-manager",
                causation_id=event.id,
                payload={
                    "source": "human_decision_applied",
                    "decision_token": token,
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


def _emit_failed_action(
    writer: EventWriter,
    action: dict[str, Any],
    *,
    causation_id: str,
    reason: str,
) -> None:
    writer.emit(
        RUN_MANAGER_ACTION_FAILED,
        actor="run-manager",
        causation_id=causation_id,
        payload={
            "schema_version": "run-manager.action.v1",
            **_action_payload(action),
            "status": "failed",
            "reason": reason,
        },
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


def _pending_task_attempt_recovery_actions(
    state_dir: Path,
    *,
    config: ZfConfig,
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    """Convert task attempt projection gaps into Run Manager actions.

    This is intentionally downstream of workflow_resume: if a task already has
    a concrete workflow resume checkpoint, that path remains the owner.
    """

    workflow = getattr(config, "workflow", None)
    lease_grace_s = float(getattr(workflow, "attempt_lease_grace_s", 900.0) or 900.0)
    actions = pending_task_attempt_recovery_actions(
        state_dir / "projections",
        lease_grace_s=lease_grace_s,
        max_retry_attempts=_max_task_attempt_retries(config),
    )
    out: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if _action_seen(events, action):
            continue
        out.append(action)
    return out


def _max_task_attempt_retries(config: ZfConfig) -> int:
    values = [
        int(getattr(role, "max_rework_attempts", 0) or 0)
        for role in getattr(config, "roles", []) or []
    ]
    values = [value for value in values if value > 0]
    return max(values) if values else 3


def _resident_owns_self_repair_execution(config: ZfConfig) -> bool:
    resident = getattr(getattr(config, "runtime", None), "autoresearch_resident", None)
    return bool(
        resident
        and getattr(resident, "enabled", False)
        and getattr(resident, "self_repair_consumer", False)
    )


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


_INFRA_ACTORS = frozenset({
    "zf-cli", "run-manager", "zf-supervisor", "zf-runtime",
    "zf-autoresearch", "operator", "",
})


def _run_has_recent_worker_activity(
    events: list[ZfEvent], *, grace_seconds: int = 300
) -> bool:
    """True if a worker (non-infrastructure role instance) emitted an event
    within ``grace_seconds`` of the latest event — i.e. the run is actively
    progressing, not idle/stuck.

    2026-07-08 E2E T1: ``failure.closeout.materialized`` can fire from
    resident-agent-stall detection or autoresearch acceptance-evidence noise
    while lanes are mid-turn (xhigh codex turns run minutes; heartbeats are
    sparse but the run is alive and producing). Escalating failure-closeout to
    the owner then is premature. Defer until the run is genuinely idle past the
    grace window. Mirrors the autoresearch failure_signals last-seen grace
    (only silence beyond grace counts as stalled)."""
    latest: datetime | None = None
    latest_worker: datetime | None = None
    for event in events:
        value = str(getattr(event, "ts", "") or "")
        if not value:
            continue
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if latest is None or ts > latest:
            latest = ts
        if str(getattr(event, "actor", "") or "") not in _INFRA_ACTORS:
            if latest_worker is None or ts > latest_worker:
                latest_worker = ts
    if latest is None or latest_worker is None:
        return False
    return (latest - latest_worker).total_seconds() < grace_seconds


def _pending_failure_closeout_activation_actions(events: list[ZfEvent]) -> list[dict[str, Any]]:
    if _current_run_completed_closeout(events) is not None:
        return []
    # T1 (2026-07-08 E2E): defer owner escalation while the run is actively
    # progressing. failure-closeout is the common chokepoint for premature
    # escalation (resident-agent-stall + autoresearch noise both route here);
    # gating it on recent worker activity suppresses the false "run stalled"
    # escalation during long in-flight lane turns. Genuine idleness (silence
    # past the grace window) still escalates on a later tick.
    if _run_has_recent_worker_activity(events):
        return []
    activated_refs = {
        str((event.payload or {}).get("manifest_ref") or "")
        for event in events
        if event.type == "failure.closeout.activated" and isinstance(event.payload, dict)
    }
    # 2026-07-10 E2E retest: a shipped run kept escalating pre-ship failure
    # manifests to the owner (PRD: ship.completed=1 yet human.escalate on
    # "failure-closeout-activate requires owner approval"). A ship.completed
    # LATER than the materialized manifest means the run has since delivered —
    # activating that stale failure closeout is moot. Manifests materialized
    # after the ship still activate.
    last_ship_index = -1
    for index, event in enumerate(events):
        if event.type == "ship.completed":
            last_ship_index = index
    out: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event.type != "failure.closeout.materialized":
            continue
        if index < last_ship_index:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        manifest_ref = str(payload.get("manifest_ref") or "").strip()
        if not manifest_ref or manifest_ref in activated_refs:
            continue
        if _safe_int(payload.get("materialized_count")) <= 0:
            continue
        checkpoint_id = "failure-closeout-activate-" + hashlib.sha1(
            manifest_ref.encode("utf-8")
        ).hexdigest()[:12]
        action = {
            "schema_version": "run-manager.pending-action.v1",
            "action": "failure-closeout-activate",
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": "failure_closeout_activate",
            "manifest_ref": manifest_ref,
            "output_dir": str(payload.get("output_dir") or "tasks/active"),
            "source_event_id": event.id,
            "source_event_type": event.type,
            "source_event_ids": [event.id] if event.id else [],
            "failure_class": "failure_closeout_activation",
            "owner_route": "controlled_action",
            "action_policy": "needs_approval",
            "intervention_class": "human_decision",
            "expected_downstream_events": sorted(_expected_downstream_events("failure_closeout_activate")),
            "verify_condition": (
                "expected_downstream_event:"
                + ",".join(sorted(_expected_downstream_events("failure_closeout_activate")))
            ),
            "route_registry": "run-manager-router.v1",
            "reason": "failure closeout manifest is ready and requires owner approval before tasks/active promotion",
        }
        if _action_seen(events, action):
            continue
        action["preflight"] = preflight_action(
            action="failure-closeout-activate",
            payload=action,
        )
        action["policy_decision"] = decide_action_policy(
            action="failure-closeout-activate",
            payload=action,
        )
        out.append(action)
    return out


def _pending_post_repair_continuation_actions(
    repair_merge_queue: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    items = repair_merge_queue.get("items") if isinstance(repair_merge_queue, dict) else []
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "merged":
            continue
        continuation = item.get("continuation")
        continuation = continuation if isinstance(continuation, dict) else {}
        if not continuation.get("resume_original_workflow"):
            continue
        checkpoint_id = str(
            continuation.get("checkpoint_id")
            or continuation.get("resume_checkpoint_ref")
            or continuation.get("idempotency_key")
            or item.get("checkpoint_id")
            or ""
        ).strip()
        safe_action = str(
            continuation.get("safe_resume_action")
            or item.get("safe_resume_action")
            or ""
        ).strip()
        fingerprint = str(item.get("fingerprint") or item.get("queue_id") or "post-repair")
        checkpoint = checkpoint_id or (
            "post-repair-continuation-"
            + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
        )
        source_event_ids = _repair_queue_event_ids(item)
        if _post_repair_continuation_seen(
            events,
            checkpoint_id=checkpoint,
            fingerprint=fingerprint,
        ):
            continue
        if checkpoint_id and safe_action:
            action = {
                "schema_version": "run-manager.pending-action.v1",
                "action": "workflow-batch-resume",
                "checkpoint_id": checkpoint_id,
                "safe_resume_action": safe_action,
                "fingerprint": fingerprint,
                "queue_id": str(item.get("queue_id") or ""),
                "candidate_id": str(item.get("candidate_id") or ""),
                "source_commit": str(item.get("source_commit") or ""),
                "source_title": str(item.get("source_title") or ""),
                "continuation": continuation,
                "source_event_ids": source_event_ids,
                "reason": "self-repair merged; resume original workflow continuation",
            }
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
        else:
            action = {
                "schema_version": "run-manager.pending-action.v1",
                "action": "diagnose-attention",
                "checkpoint_id": checkpoint,
                "safe_resume_action": "diagnose_attention",
                "fingerprint": fingerprint,
                "failure_class": (
                    "self_repair_post_merge_continuation_missing_checkpoint"
                ),
                "owner_route": "run_manager",
                "action_policy": "needs_diagnosis",
                "intervention_class": "diagnose",
                "queue_id": str(item.get("queue_id") or ""),
                "candidate_id": str(item.get("candidate_id") or ""),
                "continuation": continuation,
                "source_event_ids": source_event_ids,
                "title": "Self-repair merged but continuation checkpoint is missing",
                "summary": (
                    "repair merge completed and requested original workflow resume, "
                    "but no checkpoint_id/safe_resume_action was recorded"
                ),
                "recommended_actions": [
                    "inspect_repair_closeout_continuation",
                    "find_latest_workflow_resume_checkpoint",
                    "return_resume_or_wait_proposal_to_run_manager",
                ],
                "expected_output": [
                    "continuation_checkpoint",
                    "safe_resume_action",
                    "resume_or_wait_decision",
                ],
                "expected_downstream_events": ["run.manager.autoresearch.requested"],
                "verify_condition": (
                    "expected_downstream_event:run.manager.autoresearch.requested"
                ),
                "route_registry": "run-manager-router.v1",
            }
            action["preflight"] = preflight_action(
                action="diagnose-attention",
                payload=action,
            )
            action["policy_decision"] = decide_action_policy(
                action="diagnose-attention",
                payload=action,
            )
        if _action_seen(events, action):
            continue
        out.append(action)
    return out


def _repair_queue_event_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for event in item.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "").strip()
        if event_id and event_id not in ids:
            ids.append(event_id)
    return ids


def _post_repair_continuation_seen(
    events: list[ZfEvent],
    *,
    checkpoint_id: str,
    fingerprint: str,
) -> bool:
    if not checkpoint_id and not fingerprint:
        return False
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {RUN_MANAGER_AUTORESEARCH_REQUESTED, RUN_MANAGER_ACTION_APPLIED}:
            if checkpoint_id and str(payload.get("checkpoint_id") or "") == checkpoint_id:
                return True
            if fingerprint and str(payload.get("fingerprint") or "") == fingerprint:
                return True
        if event.type == "workflow.resume.applied" and checkpoint_id:
            if str(
                payload.get("checkpoint_id")
                or payload.get("resume_checkpoint_ref")
                or payload.get("idempotency_key")
                or ""
            ) == checkpoint_id:
                return True
        if event.type == RUN_COMPLETED:
            return True
    return False


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


def _pending_semantic_event_actions(
    events: list[ZfEvent],
    *,
    triage_threshold: int = 0,
) -> list[dict[str, Any]]:
    from zf.runtime.rework_triage import REWORK_TRIAGE_TRIGGER_EVENTS

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, event in enumerate(events):
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "autoresearch.trigger.accepted"
            and str(event_payload.get("source") or "")
            == "autoresearch.invocation.accepted"
        ):
            continue
        if event.type == "task.rework.capped" and is_semantic_triage_cap(
            event,
            threshold=triage_threshold,
        ):
            # Dedicated RM -> orchestrator triage path owns this event.  The
            # generic semantic diagnosis path would create a competing owner.
            continue
        if (
            event.task_id
            and event.type in REWORK_TRIAGE_TRIGGER_EVENTS
            and event.type != "task.rework.capped"
        ):
            # Task-scoped failures are L1-owned until the configured semantic
            # threshold emits task.rework.capped. Candidate-level failures
            # (without task_id) still enter Run Manager immediately.
            continue
        if event.type == "run.goal.completion.blocked":
            blockers = {
                str(item)
                for item in event_payload.get("blockers", [])
                if str(item).strip()
            }
            if blockers and blockers <= {"active_attempt", "delivery_pending"}:
                continue
            if "delivery_failed" in blockers and _matching_delivery_failure_exists(
                events, event,
            ):
                continue
        spec = spec_for_event(event.type)
        if event.type in RUN_MANAGER_PENDING_EVENT_TYPES:
            if spec is None or spec.event_class != "expected_negative":
                continue
            action = _semantic_event_diagnostic_action(event, spec)
        elif spec is None and _is_unknown_actionable_workflow_event(event):
            action = _unknown_actionable_event_diagnostic_action(event)
        else:
            continue
        if _event_superseded_by_later_progress(events, event):
            continue
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


def _is_unknown_actionable_workflow_event(event: ZfEvent) -> bool:
    event_type = str(event.type or "")
    if not looks_actionable_event(event_type):
        return False
    # These are Run Manager / control-loop feedback events, not workflow
    # producer outputs. Let the existing no-progress and repair ledgers consume
    # them instead of preempting with a generic unknown-actionable diagnosis.
    if event_type.startswith((
        "run.manager.",
        "autoresearch.",
        "supervisor.",
        "loop.",
        "repair.action.",
        "owner.visible_message.",
    )):
        return False
    return True


def _unknown_actionable_event_diagnostic_action(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    scope = _semantic_event_scope(event, payload)
    fingerprint = f"unknown-actionable:{event.type}:{scope}"
    checkpoint_id = "unknown-actionable-" + hashlib.sha1(
        fingerprint.encode("utf-8")
    ).hexdigest()[:12]
    reason = str(
        payload.get("reason")
        or payload.get("error")
        or payload.get("summary")
        or f"unregistered actionable event {event.type}"
    )
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "diagnose_attention",
        "fingerprint": fingerprint,
        "failure_class": "unknown_actionable_event",
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
        "severity": "high",
        "title": f"Unregistered actionable event: {event.type}",
        "summary": reason,
        "task_id": str(event.task_id or payload.get("task_id") or ""),
        "source_event_ids": [event.id] if event.id else [],
        "source_ref": f"events.jsonl#{event.id}" if event.id else "events.jsonl",
        "source": "event_contract",
        "suggested_route": "run_manager_recovery",
        "suggested_action": {
            "kind": "diagnose_unknown_actionable_event",
            "event_type": event.type,
            "scope": scope,
        },
        "recommended_actions": [
            "inspect_event_payload_and_producer",
            "add_event_problem_registry_contract_or_mark_projection_only",
            "decide_controlled_resume_rework_or_human_escalation",
        ],
        "expected_output": [
            "diagnosis_report",
            "event_contract_decision",
            "recommended_recovery_or_registry_patch",
        ],
        "route_registry": "event-problem-registry.v1",
    }


def _semantic_event_diagnostic_action(
    event: ZfEvent,
    spec: Any,
) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    fingerprint = str(
        payload.get("fingerprint")
        or payload.get("blocker_fingerprint")
        or f"semantic:{event.type}:{_semantic_event_scope(event, payload)}"
    )
    checkpoint_id = "semantic-diagnosis-" + hashlib.sha1(
        fingerprint.encode("utf-8")
    ).hexdigest()[:12]
    reason = str(
        payload.get("reason")
        or payload.get("error")
        or payload.get("summary")
        or spec.title
        or event.type
    )
    blocker_recovery = (
        _goal_completion_blocker_recovery(payload)
        if event.type == "run.goal.completion.blocked"
        else {}
    )
    suggested_action_kind = str(
        blocker_recovery.get("suggested_action_kind")
        or spec.suggested_action_kind
        or "diagnose_semantic_event"
    )
    direct_controlled_action = (
        suggested_action_kind
        if suggested_action_kind in {"ship-retry"}
        else ""
    )
    safe_resume_action = direct_controlled_action or "diagnose_attention"
    expected_downstream = list(blocker_recovery.get("expected_downstream_events") or [])
    if not expected_downstream:
        expected_downstream = sorted(
            router_expected_downstream_events(safe_resume_action)
        ) if direct_controlled_action else [
            "run.manager.autoresearch.requested",
            "run.manager.resident.prompted",
            "flow.gap_plan.ready",
            "goal.gap_plan.ready",
            "flow.goal.closed",
        ]
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": direct_controlled_action or "diagnose-attention",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": safe_resume_action,
        "fingerprint": fingerprint,
        "failure_class": spec.failure_class,
        "owner_route": spec.owner_route or "run_manager",
        "action_policy": str(
            blocker_recovery.get("action_policy")
            or spec.action_policy
            or "needs_diagnosis"
        ),
        "intervention_class": str(
            blocker_recovery.get("intervention_class")
            or spec.intervention_class
            or "semantic_replan"
        ),
        "verify_condition": "expected_downstream_event:" + ",".join(
            expected_downstream
        ),
        "expected_downstream_events": expected_downstream,
        "severity": spec.severity or "high",
        "title": spec.title or event.type,
        "summary": reason,
        "task_id": str(event.task_id or payload.get("task_id") or ""),
        "source_event_ids": [event.id] if event.id else [],
        "source_ref": f"events.jsonl#{event.id}" if event.id else "events.jsonl",
        "source": spec.source,
        "suggested_route": spec.suggested_route,
        "suggested_action": {
            "kind": suggested_action_kind,
            "event_type": event.type,
            "scope": _semantic_event_scope(event, payload),
        },
        "recommended_actions": list(
            blocker_recovery.get("recommended_actions") or [
                "inspect_semantic_failure_event",
                "compare_goal_or_parity_gap_evidence",
                "request_gap_plan_or_semantic_replan",
                "ask_autoresearch_if_recovery_is_not_mechanical",
            ]
        ),
        "expected_output": [
            "diagnosis_report",
            "gap_or_no_gap_decision",
            "recommended_replan_or_resume_action",
            "post_verify_downstream_event",
        ],
        "route_registry": "event-problem-registry.v1",
    }
    if direct_controlled_action:
        for key in (
            "workflow_run_id", "run_id", "goal_id", "claim_id",
            "delivery_operation_id", "candidate_ref", "target_ref",
            "target_commit",
        ):
            value = payload.get(key)
            if value not in (None, "", [], {}):
                action[key] = value
    return action


def _goal_completion_blocker_recovery(payload: dict[str, Any]) -> dict[str, Any]:
    blockers = {
        str(item)
        for item in payload.get("blockers", [])
        if str(item).strip()
    }
    if "pending_human_decision" in blockers:
        return {
            "suggested_action_kind": "await_goal_human_decision",
            "action_policy": "needs_approval",
            "intervention_class": "human_decision",
            "expected_downstream_events": [
                "human.decision.resolved",
                "run.manager.human_decision.applied",
            ],
            "recommended_actions": [
                "project_the_pending_decision_to_the_owner",
                "apply_only_an_explicit_approved_decision",
                "re_evaluate_the_same_completion_claim",
            ],
        }
    if {"open_feedback", "pending_handoff"} <= blockers:
        return {
            "suggested_action_kind": "reconcile_goal_feedback_and_handoff",
            "expected_downstream_events": [
                "rework.feedback.verified_closed",
                "attempt.handoff.closed",
            ],
            "recommended_actions": [
                "resume_the_feedback_owner",
                "verify_feedback_closure",
                "close_the_attempt_handoff",
                "re_evaluate_the_same_completion_claim",
            ],
        }
    if "open_feedback" in blockers:
        return {
            "suggested_action_kind": "reconcile_goal_feedback",
            "expected_downstream_events": [
                "rework.feedback.verified_closed",
                "rework.feedback.residual",
            ],
            "recommended_actions": [
                "resume_the_feedback_owner",
                "verify_feedback_closure",
                "re_evaluate_the_same_completion_claim",
            ],
        }
    if "pending_handoff" in blockers:
        return {
            "suggested_action_kind": "reconcile_goal_attempt_handoff",
            "expected_downstream_events": [
                "attempt.handoff.acknowledged",
                "attempt.handoff.closed",
            ],
            "recommended_actions": [
                "reconcile_the_current_attempt_handoff",
                "preserve_the_original_lane_affinity",
                "re_evaluate_the_same_completion_claim",
            ],
        }
    if "unsettled_required_operation" in blockers:
        return {
            "suggested_action_kind": "await_required_workflow_operation",
            "expected_downstream_events": [
                "workflow.operation.settled",
                "workflow.operation.failed",
                "workflow.operation.blocked",
            ],
            "recommended_actions": [
                "inspect_the_required_operation",
                "resume_or_repair_its_current_attempt",
                "re_evaluate_the_same_completion_claim",
            ],
        }
    return {}


def _matching_delivery_failure_exists(
    events: list[ZfEvent],
    blocked: ZfEvent,
) -> bool:
    payload = blocked.payload if isinstance(blocked.payload, dict) else {}
    claim_id = str(payload.get("claim_id") or "")
    run_id = str(payload.get("run_id") or payload.get("workflow_run_id") or "")
    return any(
        event.type in {"run.delivery.failed", "run.delivery.blocked"}
        and isinstance(event.payload, dict)
        and str(event.payload.get("claim_id") or "") == claim_id
        and (
            not run_id
            or str(
                event.payload.get("run_id")
                or event.payload.get("workflow_run_id")
                or event.correlation_id
                or ""
            ) == run_id
        )
        for event in events
    )


def _semantic_event_scope(event: ZfEvent, payload: dict[str, Any]) -> str:
    for key in (
        "pdd_id",
        "feature_id",
        "trace_id",
        "target_ref",
        "candidate_ref",
        "task_id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    if event.correlation_id:
        return f"trace_id:{event.correlation_id}"
    if event.task_id:
        return f"task_id:{event.task_id}"
    return event.id or event.type


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
        route = str(item.get("suggested_route") or item.get("recommended_route") or "")
        if route in {"plan_revision", "l2_orchestrator"} or str(
            item.get("failure_scope") or ""
        ) == "plan_admission":
            # Planner/task-map remediation is handled by the workflow replan
            # route; Run Manager must not turn it into a source diagnosis.
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
        "notification_policy": str(item.get("notification_policy") or ""),
        "recovery_policy": str(item.get("recovery_policy") or ""),
        "recommended_actions": _attention_recommended_actions(item, fingerprint=fingerprint),
        "expected_output": [
            "diagnosis_report",
            "recommended_run_manager_action",
            "evidence_refs",
            "expected_downstream_event",
        ],
        "route_registry": "run-manager-router.v1",
    }
    suggested_action = action["suggested_action"]
    for key in (
        "workflow_run_id",
        "run_id",
        "trace_id",
        "failure_scope",
        "plan_admission_incident_id",
        "expected_fault",
        "original_trigger_event_id",
    ):
        value = item.get(key)
        if value in (None, "", [], {}):
            value = suggested_action.get(key)
        if value not in (None, "", [], {}):
            action[key] = value
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
    if "cost.budget" in text or "budget_exceeded" in text or "budget exceeded" in text:
        return "cost_budget_exceeded"
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
    elif failure_class == "cost_budget_exceeded":
        recommendations.extend([
            "inspect_budget_diagnostics_projection",
            "summarize_budget_state",
            "recommend_continue_or_stop_decision_if_needed",
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
        "approval_ref": str(action.get("approval_ref") or ""),
        "manifest_ref": str(action.get("manifest_ref") or ""),
        "output_dir": str(action.get("output_dir") or ""),
        "limit": action.get("limit") if action.get("limit") is not None else None,
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
        "request_id": str(action.get("request_id") or ""),
        "role": str(action.get("role") or ""),
        "failure_count": int(action.get("failure_count") or 0),
        "failure_event_ids": _string_list(action.get("failure_event_ids")),
        "source_event_ids": _string_list(action.get("source_event_ids")),
        "recorded_event_id": str(action.get("recorded_event_id") or ""),
        "recommended_action": str(action.get("recommended_action") or ""),
        "guidance": str(action.get("guidance") or ""),
        "semantic_replan_trigger": str(action.get("semantic_replan_trigger") or ""),
        "semantic_replan_stage_id": str(action.get("semantic_replan_stage_id") or ""),
        "semantic_replan_role": str(action.get("semantic_replan_role") or ""),
        "affected_task_ids": _string_list(action.get("affected_task_ids")),
        "supersedes_task_ids": _string_list(action.get("supersedes_task_ids")),
        "recovery_context_ref": (
            action.get("recovery_context_ref")
            if isinstance(action.get("recovery_context_ref"), dict)
            else {}
        ),
        "candidate_id": str(action.get("candidate_id") or ""),
        "branch": str(action.get("branch") or ""),
        "worktree_path": str(action.get("worktree_path") or action.get("worktree") or ""),
        "source_title": str(action.get("source_title") or ""),
        "verification_plan": action.get("verification_plan")
        if isinstance(action.get("verification_plan"), list) else [],
        "risk_classification": action.get("risk_classification")
        if isinstance(action.get("risk_classification"), dict) else {},
        "continuation": action.get("continuation")
        if isinstance(action.get("continuation"), dict) else {},
    }


def _operator_controlled_action_payload(action: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "source": "run-manager",
        "approval_ref": str(action.get("approval_ref") or ""),
        "manifest_ref": str(action.get("manifest_ref") or ""),
        "output_dir": str(action.get("output_dir") or ""),
    }
    if action.get("limit") is not None:
        payload["limit"] = action.get("limit")
    return payload


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
        "approval_ref": str(merged.get("approval_ref") or ""),
        "manifest_ref": str(merged.get("manifest_ref") or ""),
        "output_dir": str(merged.get("output_dir") or ""),
        "limit": merged.get("limit") if merged.get("limit") is not None else None,
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
    terminal_closeout = _current_run_completed_closeout(events)
    rows = []
    for token, payload in escalations.items():
        if token in resolved or str(payload.get("event_id") or "") in resolved:
            continue
        source_event = _event_by_id(events, str(payload.get("event_id") or ""))
        if source_event is not None and _event_superseded_by_later_progress(events, source_event):
            continue
        blocking_scope = str(payload.get("blocking_scope") or "run")
        if (
            terminal_closeout is not None
            and not _is_blocking_human_decision({"blocking_scope": blocking_scope})
        ):
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
            # FIX-3:blocking_scope/approval_command 随包透传;legacy 无
            # scope 的包按 run 处理(fail-closed)。
            "blocking_scope": blocking_scope,
            "approval_command": str(payload.get("approval_command") or ""),
        })
    return sorted(rows, key=lambda item: item.get("created_ts") or "")


def _blocking_human_decisions(events: list[ZfEvent]) -> list[dict[str, Any]]:
    return [
        item for item in _pending_human_decisions(events)
        if _is_blocking_human_decision(item)
    ]


def _is_blocking_human_decision(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return True
    scope = str(ref.get("blocking_scope") or "run").strip().lower().replace("-", "_")
    return scope in {
        "",
        "run",
        "workflow",
        "main",
        "main_flow",
        "main_flow_blocking",
        "blocking",
    }


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
    "flow.discovery.completed",
    "goal.rescan.completed",
    "flow.gap_plan.ready",
    "goal.gap_plan.ready",
    "flow.goal.closed",
    "goal.closure.closed",
    *MODULE_PARITY_SCAN_COMPLETED_EVENTS,
    "module.parity.closed",
    "judge.passed",
    "candidate.promoted",
    "rework.feedback.verified_closed",
    "attempt.handoff.closed",
    "human.decision.resolved",
    "run.manager.human_decision.applied",
    "run.delivery.settled",
    "run.goal.completed",
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
    if is_module_parity_scan_completed_event(event.type):
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
    if source_event.type in {
        "run.goal.completion.blocked",
        "run.goal.completion.rejected",
        "run.delivery.failed",
        "run.delivery.blocked",
    }:
        source_claim = str(source_payload.get("claim_id") or "")
        success_claim = str(success_payload.get("claim_id") or "")
        if source_claim and success_claim:
            return source_claim == success_claim
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
        "claim_id",
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
    return latest_quiescent_run_terminal(events)


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


# 结果级熔断的"进展"口径:任一正向里程碑即算世界变好。
_OUTCOME_PROGRESS_EVENTS = frozenset({
    "dev.build.done",
    "workflow.child.completed",
    "fanout.child.completed",
    "review.approved",
    "verify.passed",
    "judge.passed",
    "static_gate.passed",
    "task.done",
})


def _outcome_no_progress_break(events: list[ZfEvent], action: dict[str, Any]) -> str:
    """结果级熔断(avbs-r5 教训):同 (task, safe_resume_action) 连续 3 次
    applied 而任务零正向进展 → 拒绝再执行。RM 的 action.verify 只验证
    "动作已应用",不验证"局面变好"——r5 里 251 次 rework 每次都
    verify.passed。窗口取最近 3 次,进展一旦出现计数自然复位。"""
    task_id = str(action.get("task_id") or "")
    safe_action = str(action.get("safe_resume_action") or "")
    if not task_id or not safe_action:
        return ""
    applied_idx = []
    for idx, event in enumerate(events):
        if event.type != "workflow.resume.applied":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(event.task_id or payload.get("task_id") or "") != task_id:
            continue
        if str(payload.get("safe_resume_action") or "") != safe_action:
            continue
        applied_idx.append(idx)
    if len(applied_idx) < 3:
        return ""
    window_start = applied_idx[-3]
    for event in events[window_start + 1:]:
        if event.type not in _OUTCOME_PROGRESS_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(event.task_id or payload.get("task_id") or "") == task_id:
            return ""
    return (
        f"safe_resume_action {safe_action!r} applied "
        f"{len(applied_idx)} times for {task_id} without any task progress"
    )


def _action_seen(events: list[ZfEvent], action: dict[str, Any]) -> bool:
    checkpoint_id = str(action.get("checkpoint_id") or "")
    if not checkpoint_id:
        return False
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED and str(
            payload.get("checkpoint_id")
            or payload.get("resume_checkpoint_ref")
            or payload.get("idempotency_key")
            or ""
        ) == checkpoint_id:
            if not _is_diagnosis_action(action):
                continue
            if _diagnosis_action_request_stalled(events, action):
                continue
            return True
        if event.type in {
            RUN_MANAGER_ACTION_APPLIED,
            RUN_MANAGER_ACTION_BLOCKED,
            RUN_MANAGER_ACTION_FAILED,
            "workflow.resume.applied",
        } and str(payload.get("checkpoint_id") or payload.get("resume_checkpoint_ref") or payload.get("idempotency_key") or "") == checkpoint_id:
            return True
        if (
            event.type == RUN_MANAGER_RESIDENT_PROMPTED
            and str(payload.get("source_action_checkpoint_id") or "") == checkpoint_id
        ):
            return True
    # FIX-5①(bizsim r4 $697 空转账单):verify.failed 不在上面的去重集,
    # "applied 成功但预期下游事件未现"的动作会被逐 tick 无限重规划。
    # 同 checkpoint 的 verify.failed 达到上限即视为 seen——3 条失败事件
    # 本身就是可查痕迹,后续交 attention/operator,不再自动重烧。
    verify_failed = 0
    for event in events:
        if event.type != RUN_MANAGER_ACTION_VERIFY_FAILED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("checkpoint_id") or "") == checkpoint_id:
            verify_failed += 1
            if verify_failed >= _ACTION_VERIFY_FAILED_CAP:
                return True
    return False


def _diagnosis_action_request_stalled(
    events: list[ZfEvent],
    action: dict[str, Any],
    *,
    stale_seconds: int = _DIAGNOSIS_REQUEST_STALE_SECONDS,
    anchored_stale_seconds: int = _DIAGNOSIS_ANCHORED_STALE_SECONDS,
) -> bool:
    if not _is_diagnosis_action(action):
        return False
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    if not checkpoint_id:
        return False
    fingerprint = _fingerprint(action, fallback=checkpoint_id)
    request_index = -1
    request_event: ZfEvent | None = None
    request_id = ""
    for index, event in enumerate(events):
        if event.type != RUN_MANAGER_AUTORESEARCH_REQUESTED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("checkpoint_id") or "") != checkpoint_id and (
            fingerprint
            and str(payload.get("fingerprint") or "") != fingerprint
        ):
            continue
        request_index = index
        request_event = event
        request_id = _autoresearch_request_id(event, payload)
    if request_event is None:
        return False
    if _current_terminal_signal(events) is not None:
        return False
    # 2026-07-10 E2E retest: anchor staleness age to the latest matching loop
    # lifecycle event, not the request. A loop legitimately running past the
    # 300s window (codex/claude agents inside) was declared stale mid-flight,
    # and a resident dedupe-skip (autoresearch.loop.skipped) was invisible
    # here, so skipped requests always burned the full window before failing
    # (PRD: 8 stale action.failed with 1 real loop run).
    liveness_event = request_event
    for event in events[request_index + 1:]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {
            RUN_MANAGER_ACTION_APPLIED,
            RUN_MANAGER_ACTION_BLOCKED,
            RUN_MANAGER_ACTION_FAILED,
        } and str(payload.get("checkpoint_id") or "") == checkpoint_id:
            return False
        if event.type == RUN_MANAGER_AUTORESEARCH_CONSUMED and (
            str(payload.get("request_id") or "") == request_id
            or str(payload.get("checkpoint_id") or "") == checkpoint_id
        ):
            return False
        if event.type in {
            "autoresearch.loop.completed",
            "autoresearch.loop.failed",
            "autoresearch.loop.skipped",
        }:
            result_request_id = _autoresearch_result_request_id(event, payload)
            if result_request_id and result_request_id == request_id:
                return False
            if str(payload.get("checkpoint_id") or "") == checkpoint_id:
                return False
        if event.type in {
            # Three anchor grades, all under the wide anchored window:
            # requested = the reactor forwarded it into the loop queue (R6:
            # requests arriving while the resident is synchronously inside a
            # bounded loop get no ack until that loop ends — the forward is
            # the only timely receipt), accepted = resident queued it,
            # started = executing. Only a request with NO trace at all runs
            # on the tight window.
            "autoresearch.loop.requested",
            "autoresearch.loop.accepted",
            "autoresearch.loop.started",
        }:
            result_request_id = _autoresearch_result_request_id(event, payload)
            if (result_request_id and result_request_id == request_id) or (
                str(payload.get("checkpoint_id") or "") == checkpoint_id
            ):
                liveness_event = event
    # Two staleness windows (2026-07-10 R5): an unacknowledged request stales
    # at the tight window, but once the resident has anchored it (accepted =
    # queued, started = executing) the terminal answer is guaranteed by the
    # bounded runner — a queued/running request is not dead, just slow. The
    # anchored window (~2x the 30min runner fallback bound) is the backstop
    # for a resident that died after anchoring.
    window = stale_seconds if liveness_event is request_event else anchored_stale_seconds
    age_seconds = _event_age_seconds(liveness_event, datetime.now(timezone.utc))
    return age_seconds is not None and age_seconds >= window


def _is_diagnosis_action(action: dict[str, Any]) -> bool:
    decision = action.get("policy_decision")
    decision = decision if isinstance(decision, dict) else {}
    return bool(
        str(action.get("safe_resume_action") or "") == "diagnose_attention"
        or str(action.get("action") or "") == "diagnose-attention"
        or str(decision.get("decision") or "") == "needs_diagnosis"
    )


def _collect_emitted_event_ids(result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if str(result.get("event_id") or ""):
        ids.add(str(result.get("event_id")))
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


def _observed_event_types_for_action(
    event_log: EventLog,
    action: dict[str, Any],
    result: dict[str, Any],
    *,
    expected: set[str],
) -> set[str]:
    observed = _emitted_event_types(event_log, _collect_emitted_event_ids(result))
    checkpoint_id = str(action.get("checkpoint_id") or "").strip()
    source_event_id = str(action.get("source_event_id") or "").strip()
    if not expected or not (checkpoint_id or source_event_id):
        return observed
    try:
        events = event_log.read_all()
    except Exception:
        return observed
    for event in events:
        if event.type not in expected:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        marker_values = {
            str(payload.get("checkpoint_id") or ""),
            str(payload.get("resume_checkpoint_ref") or ""),
            str(payload.get("idempotency_key") or ""),
            str(payload.get("source_event_id") or ""),
            str(event.causation_id or ""),
        }
        if checkpoint_id and checkpoint_id in marker_values:
            observed.add(event.type)
            continue
        if source_event_id and source_event_id in marker_values:
            observed.add(event.type)
    return observed


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
            if no_progress.get("status") == "tripped":
                next_auto_action = "diagnose_or_escalate_stale_recovery"
                wait_reason = "diagnosis_required_no_progress_tripped"
            else:
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
        # FIX-3(bizsim r4 F1):按 blocking_scope 分级。无任何 workflow
        # 锚点(task/stage/lane/fanout/run)的审批(如频道侧成员失败的
        # closeout)不再冻结全 run 派发——r4 实锚:一个频道回复失败的
        # 审批把主链锁死 50min+,间接烧掉 $697 空转。legacy 无 scope
        # 字段按 run 处理(fail-closed)。
        run_refs = [
            ref for ref in pending_human
            if _is_blocking_human_decision(ref)
        ]
        side_refs = [
            ref for ref in pending_human
            if isinstance(ref, dict)
            and not _is_blocking_human_decision(ref)
        ]
        return {
            "owner_route": "human",
            "intervention_class": "human_decision",
            "wait_reason": (
                "human_decision_pending"
                if run_refs else "side_decision_pending"
            ),
            "next_auto_action": (
                "wait_for_human_decision"
                if run_refs else "continue_dispatch"
            ),
            "blocking": bool(run_refs),
            "blocking_refs": run_refs,
            "side_blocking_refs": side_refs,
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
        "attempt_context": (
            action.get("attempt_context")
            if isinstance(action.get("attempt_context"), dict)
            else {}
        ),
        "attempt_contexts": (
            action.get("attempt_contexts")
            if isinstance(action.get("attempt_contexts"), list)
            else []
        ),
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
    recovery_case_id = recovery_case_id_from_payload(action)
    return "rmar-" + hashlib.sha1(recovery_case_id.encode("utf-8")).hexdigest()[:12]


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
