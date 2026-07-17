"""Run Manager action routing, preflight, and no-progress projection."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from zf.runtime.run_manager_rework_triage import (
    ORCHESTRATOR_TRIAGE_ACTIONS,
    triage_action_preflight,
    triage_expected_downstream_events,
)


SAFE_BATCH_ACTIONS = frozenset({"repair_failed_children", "reemit_candidate_ready"})
SAFE_TASK_ACTIONS = frozenset({
    "needs_stage_dispatch",
    "needs_stage_replan",
    "needs_rework_dispatch",
    "needs_task_ref_repair",
    "needs_gate_dispatch",
    "blocked_external_gate",
    "needs_terminal_closeout",
})
CANDIDATE_REWORK_ACTIONS = frozenset({"retrigger", "replan", "escalate"})
DIAGNOSIS_ACTIONS = frozenset({"diagnose-attention"})
WORKER_LIFECYCLE_ACTIONS = frozenset({"worker-lifecycle-recover"})
REPAIR_CLOSEOUT_ACTIONS = frozenset({"repair-closeout-validate"})
RESIDENT_AGENT_ACTIONS = frozenset({"resident-agent-reprompt"})
OWNER_APPROVAL_ACTIONS = frozenset({"failure-closeout-activate"})
INTERVENTION_CLASSES = frozenset({
    "none",
    "wait",
    "auto_recover",
    "diagnose",
    "repair_harness",
    "repair_project",
    "semantic_replan",
    "human_decision",
    "operator_resume",
    "manual_review",
    "safe_halt",
})
ACTION_POLICIES = frozenset({
    "auto_decide",
    "needs_diagnosis",
    "needs_approval",
    "human_escalate",
    "safe_halt",
    # 纯观测事件的合法策略:不产生补救动作,只进投影/attention 计数
    # (registry 有 11 个 projection_only 事件在用;2026-07-04 D 批收编)。
    "informational",
})
SUPPORTED_BATCH_ACTIONS = SAFE_BATCH_ACTIONS | {"trigger_rework"}
SUPPORTED_TASK_ACTIONS = SAFE_TASK_ACTIONS
NO_PROGRESS_EVENT_TYPES = frozenset({
    "task.dispatch_context.bound",
    "fanout.child.stale_completion",
    "run.manager.action.blocked",
    "run.manager.action.failed",
    "run.manager.action.verify.failed",
    "run.manager.autoresearch.consumed",
    "run.manager.human_decision.rejected",
    "human.escalate",
    "integration.failed",
    "candidate.quality.failed",
})


@dataclass(frozen=True)
class ActionRoute:
    safe_resume_action: str
    failure_class: str
    owner_route: str
    action_policy: str
    intervention_class: str
    attempt_cap: int
    expected_downstream_events: tuple[str, ...]

    @property
    def verify_condition(self) -> str:
        return "expected_downstream_event:" + ",".join(self.expected_downstream_events)


def route_for_safe_action(safe_resume_action: str) -> ActionRoute:
    safe_resume_action = str(safe_resume_action or "")
    if safe_resume_action in {
        "needs_stage_dispatch",
        "needs_stage_replan",
        "needs_rework_dispatch",
        "needs_task_ref_repair",
        "needs_gate_dispatch",
    }:
        return ActionRoute(
            safe_resume_action=safe_resume_action,
            failure_class="deterministic_task_resume",
            owner_route="controlled_action",
            action_policy="auto_decide",
            intervention_class="auto_recover",
            attempt_cap=2,
            expected_downstream_events=tuple(sorted(expected_downstream_events(safe_resume_action))),
        )
    if safe_resume_action in SAFE_BATCH_ACTIONS:
        return ActionRoute(
            safe_resume_action=safe_resume_action,
            failure_class="deterministic_resume",
            owner_route="controlled_action",
            action_policy="auto_decide",
            intervention_class="auto_recover",
            attempt_cap=2,
            expected_downstream_events=tuple(sorted(expected_downstream_events(safe_resume_action))),
        )
    if safe_resume_action == "trigger_rework":
        return ActionRoute(
            safe_resume_action=safe_resume_action,
            failure_class="task_map_drift",
            owner_route="run_manager",
            action_policy="needs_diagnosis",
            intervention_class="semantic_replan",
            attempt_cap=1,
            expected_downstream_events=tuple(sorted(expected_downstream_events(safe_resume_action))),
        )
    if safe_resume_action == "ship-retry":
        return ActionRoute(
            safe_resume_action=safe_resume_action,
            failure_class="goal_delivery_failed",
            owner_route="controlled_action",
            action_policy="needs_approval",
            intervention_class="human_decision",
            attempt_cap=2,
            expected_downstream_events=(
                "run.delivery.settled",
                "run.delivery.failed",
                "run.delivery.blocked",
            ),
        )
    return ActionRoute(
        safe_resume_action=safe_resume_action,
        failure_class="unknown_complex",
        owner_route="run_manager",
        action_policy="needs_diagnosis",
        intervention_class="diagnose",
        attempt_cap=1,
        expected_downstream_events=tuple(sorted(expected_downstream_events("diagnose_attention"))),
    )


def classify_recovery_context(action: dict[str, Any]) -> dict[str, Any]:
    route = route_for_safe_action(str(action.get("safe_resume_action") or ""))
    return {
        **asdict(route),
        "expected_downstream_events": list(route.expected_downstream_events),
        "verify_condition": route.verify_condition,
        "route_registry": "run-manager-router.v1",
    }


SURGERY_ACTIONS = frozenset({
    # ZF-E2E-PRDCTL-P2-8:operator 手术动作词表——RM 可提案,恒需 owner
    # 批准(payload 补键 re-emit / 简报重投 / 死决策 dismiss / ship 重试)。
    "payload-repair-reemit",
    "briefing-redeliver",
    "human-decision-dismiss",
    "ship-retry",
})


def decide_action_policy(
    *,
    action: str,
    payload: dict[str, Any],
    mutating_resume_supported: bool = False,
) -> dict[str, Any]:
    if action in SURGERY_ACTIONS:
        return _decision(
            "needs_approval",
            executable=False,
            payload=payload,
            preflight={"status": "passed", "missing": []},
            reason="operator-surgery action always requires owner approval",
        )
    if action in ORCHESTRATOR_TRIAGE_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="orchestrator triage action is missing required evidence",
            )
        if str(payload.get("action_policy") or "") == "human_escalate":
            return _decision(
                "human_escalate",
                executable=False,
                payload=payload,
                preflight=preflight,
                reason="orchestrator triage advice requires an owner decision",
            )
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="Run Manager owns the bounded orchestrator triage handoff",
        )
    if action in DIAGNOSIS_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_approval",
                executable=False,
                payload=payload,
                preflight=preflight,
                reason="attention diagnosis action is missing required evidence",
            )
        if str(payload.get("action_policy") or "") == "needs_approval":
            return _decision(
                "needs_approval",
                executable=False,
                payload=payload,
                preflight=preflight,
                reason="diagnosis identified a blocker that requires owner approval",
            )
        return _decision(
            "needs_diagnosis",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="attention diagnosis is non-mutating and owned by Run Manager diagnosis",
        )
    if action in WORKER_LIFECYCLE_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="worker lifecycle recovery needs diagnosis before mutation",
            )
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="worker lifecycle recovery emits a bounded respawn request",
        )
    if action in REPAIR_CLOSEOUT_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="repair closeout validation is missing required evidence",
            )
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="repair closeout validation is read-only and allowlisted",
        )
    if action in RESIDENT_AGENT_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="resident agent reprompt is missing pane or briefing evidence",
            )
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="resident agent reprompt resends an existing observe briefing to its own pane",
        )
    if action in OWNER_APPROVAL_ACTIONS:
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="owner-approved action is missing required evidence",
            )
        return _decision(
            "needs_approval",
            executable=False,
            payload=payload,
            preflight=preflight,
            reason=f"{action} requires explicit owner approval",
        )
    if action == "candidate-rework-apply":
        preflight = preflight_action(action=action, payload=payload)
        if preflight["status"] == "blocked":
            return _decision(
                "human_escalate",
                executable=False,
                payload=payload,
                preflight=preflight,
                reason="; ".join(preflight["failures"]),
            )
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="candidate rework controlled action is deterministic",
        )
    if action == "workflow-task-resume":
        preflight = preflight_action(action=action, payload=payload)
        safe_action = str(payload.get("safe_resume_action") or "")
        action_policy = str(payload.get("action_policy") or "")
        owner_route = str(payload.get("owner_route") or "")
        if preflight["status"] == "blocked":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason="task-level workflow resume preflight blocked automatic execution",
            )
        if (
            safe_action in SAFE_TASK_ACTIONS
            and str(payload.get("failure_class") or "") == "deterministic_task_resume"
            and action_policy in {"", "auto_decide"}
        ):
            return _decision(
                "auto_decide",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason=f"{safe_action} is an idempotent task-level resume",
            )
        if action_policy == "needs_diagnosis" or owner_route in {"autoresearch", "run_manager"}:
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason=f"workflow task action {safe_action!r} requires diagnosis",
            )
        return _decision(
            "needs_approval",
            executable=False,
            payload=payload,
            preflight=preflight,
            reason=f"workflow task action {safe_action!r} requires approval",
        )
    if action != "workflow-batch-resume":
        if (
            str(payload.get("checkpoint_id") or "")
            and (
                str(payload.get("fingerprint") or "")
                or [str(value) for value in payload.get("source_event_ids") or [] if str(value).strip()]
            )
        ):
            diagnosis_payload = {
                "failure_class": "unknown_complex",
                "owner_route": "run_manager",
                "action_policy": "needs_diagnosis",
                "intervention_class": "diagnose",
                **payload,
            }
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=diagnosis_payload,
                reason=f"unclassified action {action!r} routes to Run Manager diagnosis",
            )
        return _decision(
            "needs_approval",
            executable=False,
            payload=payload,
            reason=f"action {action!r} is not in the automatic policy",
        )
    preflight = preflight_action(
        action=action,
        payload=payload,
        mutating_resume_supported=mutating_resume_supported,
    )
    safe_action = str(payload.get("safe_resume_action") or "")
    action_policy = str(payload.get("action_policy") or "")
    owner_route = str(payload.get("owner_route") or "")
    if preflight["status"] == "blocked":
        if safe_action == "trigger_rework":
            return _decision(
                "needs_diagnosis",
                executable=True,
                payload=payload,
                preflight=preflight,
                reason=(
                    "trigger_rework requires Run Manager diagnosis before the "
                    "controlled mutating action can be enabled"
                ),
            )
        return _decision(
            "needs_diagnosis",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="preflight blocked automatic execution",
        )
    if (
        safe_action in SAFE_BATCH_ACTIONS
        and str(payload.get("failure_class") or "") == "deterministic_resume"
        and action_policy in {"", "auto_decide"}
    ):
        return _decision(
            "auto_decide",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason=f"{safe_action} is idempotent deterministic resume",
        )
    if action_policy == "needs_diagnosis" or owner_route in {"autoresearch", "run_manager"}:
        return _decision(
            "needs_diagnosis",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason=f"workflow batch action {safe_action!r} requires diagnosis",
        )
    return _decision(
        "needs_approval",
        executable=False,
        payload=payload,
        preflight=preflight,
        reason=f"workflow batch action {safe_action!r} requires approval",
    )


def preflight_action(
    *,
    action: str,
    payload: dict[str, Any],
    mutating_resume_supported: bool = False,
) -> dict[str, Any]:
    failures = []
    warnings = []
    safe_action = str(payload.get("safe_resume_action") or "")
    checkpoint_id = str(payload.get("checkpoint_id") or "")
    if action in ORCHESTRATOR_TRIAGE_ACTIONS:
        return triage_action_preflight(action, payload)
    if action in DIAGNOSIS_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not (
            str(payload.get("fingerprint") or "")
            or [str(value) for value in payload.get("source_event_ids") or [] if str(value).strip()]
        ):
            failures.append("missing_attention_evidence")
        expected = sorted(expected_downstream_events(safe_action or "diagnose_attention"))
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action or "diagnose_attention",
            "expected_downstream_events": expected,
            "verify_condition": (
                str(payload.get("verify_condition") or "")
                or "expected_downstream_event:" + ",".join(expected)
            ),
        }
    if action in WORKER_LIFECYCLE_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("instance_id") or payload.get("role_instance") or ""):
            failures.append("missing_instance_id")
        if not (str(payload.get("task_id") or "") or str(payload.get("briefing_ref") or "")):
            failures.append("missing_worker_ownership_evidence")
        expected = sorted(expected_downstream_events(safe_action or "worker_lifecycle_recover"))
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action or "worker_lifecycle_recover",
            "expected_downstream_events": expected,
            "verify_condition": str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected),
        }
    if action in REPAIR_CLOSEOUT_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("worktree_path") or payload.get("worktree") or ""):
            failures.append("missing_worktree_path")
        plan = payload.get("verification_plan")
        if not isinstance(plan, list) or not plan:
            failures.append("missing_verification_plan")
        expected = ["run.manager.action.applied"]
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action or "repair_closeout_validate",
            "expected_downstream_events": expected,
            "verify_condition": str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected),
        }
    if action in RESIDENT_AGENT_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("tmux_session") or ""):
            failures.append("missing_tmux_session")
        if not str(payload.get("instance_id") or payload.get("role_instance") or ""):
            failures.append("missing_instance_id")
        if not str(payload.get("briefing_path") or ""):
            failures.append("missing_briefing_path")
        expected = sorted(expected_downstream_events(safe_action or "resident_agent_reprompt"))
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action or "resident_agent_reprompt",
            "expected_downstream_events": expected,
            "verify_condition": str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected),
        }
    if action in OWNER_APPROVAL_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("manifest_ref") or ""):
            failures.append("missing_manifest_ref")
        expected = sorted(expected_downstream_events(safe_action or "failure_closeout_activate"))
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action or "failure_closeout_activate",
            "expected_downstream_events": expected,
            "verify_condition": str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected),
        }
    if action == "candidate-rework-apply":
        rework_action = str(payload.get("candidate_rework_action") or "")
        if rework_action not in CANDIDATE_REWORK_ACTIONS:
            failures.append("unsupported_candidate_rework_action")
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("pdd_id") or ""):
            failures.append("missing_pdd_id")
        if not str(payload.get("source_event_id") or ""):
            failures.append("missing_source_event_id")
        if rework_action == "retrigger":
            if not str(payload.get("task_map_ref") or ""):
                failures.append("missing_task_map_ref")
            if not str(payload.get("source_commit") or ""):
                failures.append("missing_source_commit")
            if not str(payload.get("candidate_base_commit") or ""):
                failures.append("missing_candidate_base_commit")
        expected = [
            str(value) for value in payload.get("expected_downstream_events") or []
            if str(value).strip()
        ]
        if not expected:
            failures.append("missing_expected_downstream_events")
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "candidate_rework_action": rework_action,
            "expected_downstream_events": sorted(expected),
            "verify_condition": str(payload.get("verify_condition") or ""),
        }
    if action == "workflow-task-resume":
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not safe_action or safe_action == "no_action":
            failures.append("missing_safe_resume_action")
        elif safe_action not in SUPPORTED_TASK_ACTIONS:
            warnings.append("unsupported_task_safe_resume_action_routes_to_diagnosis")
        if not str(payload.get("task_id") or ""):
            failures.append("missing_task_id")
        expected = sorted(expected_downstream_events(safe_action))
        verify_condition = str(payload.get("verify_condition") or "")
        if not verify_condition:
            warnings.append("missing_verify_condition")
        return {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "blocked" if failures else "passed",
            "failures": failures,
            "warnings": warnings,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_action,
            "expected_downstream_events": expected,
            "verify_condition": verify_condition or "expected_downstream_event:" + ",".join(expected),
        }
    if action != "workflow-batch-resume":
        failures.append("unsupported_action")
    if not checkpoint_id:
        failures.append("missing_checkpoint_id")
    if not safe_action or safe_action == "no_action":
        failures.append("missing_safe_resume_action")
    elif safe_action not in SUPPORTED_BATCH_ACTIONS:
        warnings.append("unsupported_safe_resume_action_routes_to_diagnosis")
    if safe_action == "trigger_rework" and not mutating_resume_supported:
        failures.append("mutating_resume_requires_human_decision")
    expected = sorted(expected_downstream_events(safe_action))
    verify_condition = str(payload.get("verify_condition") or "")
    if not verify_condition:
        warnings.append("missing_verify_condition")
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": warnings,
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": safe_action,
        "expected_downstream_events": expected,
        "verify_condition": verify_condition or "expected_downstream_event:" + ",".join(expected),
        "mutating_resume_supported": mutating_resume_supported,
    }


def intervention_class_for_decision(
    decision: str,
    *,
    payload: dict[str, Any],
) -> str:
    """Return the action-controlling intervention class for a router decision."""

    decision = str(decision or "")
    if decision == "auto_decide":
        value = str(payload.get("intervention_class") or "")
        return value if value in INTERVENTION_CLASSES else "auto_recover"
    if decision == "needs_diagnosis":
        return "diagnose"
    if decision in {"human_escalate", "needs_approval"}:
        return "human_decision"
    if decision == "safe_halt":
        return "safe_halt"
    value = str(payload.get("intervention_class") or "none")
    return value if value in INTERVENTION_CLASSES else "none"


def build_no_progress_projection(
    events: list[Any],
    *,
    threshold: int = 3,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        if etype not in NO_PROGRESS_EVENT_TYPES:
            continue
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        status = str(payload.get("status") or payload.get("next_route") or "")
        if etype == "run.manager.autoresearch.consumed" and status not in {"failed", "human_escalate"}:
            continue
        fp = _no_progress_fingerprint(
            etype,
            payload,
            fallback=str(getattr(event, "id", "") or ""),
        )
        counts[fp] += 1
        latest[fp] = {
            "fingerprint": fp,
            "count": counts[fp],
            "event_id": str(getattr(event, "id", "") or ""),
            "event_type": etype,
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "safe_resume_action": str(payload.get("safe_resume_action") or ""),
            "action_policy": str(payload.get("action_policy") or ""),
            "owner_route": str(payload.get("owner_route") or ""),
            "failure_class": str(payload.get("failure_class") or ""),
            "verify_condition": str(payload.get("verify_condition") or ""),
            "reason": str(payload.get("reason") or payload.get("decision") or status),
        }
    tripped = [
        row for row in latest.values()
        if int(row.get("count") or 0) >= threshold
    ]
    tripped.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("event_id") or "")))
    return {
        "schema_version": "run-manager.no-progress.v1",
        "is_derived_projection": True,
        "threshold": threshold,
        "status": "tripped" if tripped else "clear",
        "summary": {
            "fingerprints": len(counts),
            "tripped": len(tripped),
        },
        "items": tripped,
    }


def recovery_closeout_contract_report(
    *,
    event_types: set[str] | None = None,
) -> dict[str, Any]:
    """Audit recoverable event/problem specs for closeout routing metadata."""

    from zf.runtime.event_problem_registry import EVENT_PROBLEM_SPECS

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    specs = [
        spec for event_type, spec in sorted(EVENT_PROBLEM_SPECS.items())
        if event_types is None or event_type in event_types
    ]
    for spec in specs:
        if not _spec_needs_recovery_closeout(spec):
            continue
        action = _safe_action_for_spec(spec)
        route = route_for_safe_action(action)
        entry = {
            "event_type": spec.event_type,
            "problem_class": spec.problem_class,
            "failure_class": spec.failure_class,
            "owner_route": spec.owner_route,
            "action_policy": spec.action_policy,
            "intervention_class": spec.intervention_class,
            "suggested_action_kind": spec.suggested_action_kind,
            "safe_resume_action": action,
            "attempt_cap": route.attempt_cap,
            "expected_downstream_events": list(route.expected_downstream_events),
            "verify_condition": route.verify_condition,
            "human_escalation": spec.supervisor_attention,
        }
        entries.append(entry)
        for field in (
            "owner_route",
            "action_policy",
            "intervention_class",
            "suggested_action_kind",
        ):
            if not str(getattr(spec, field) or "").strip():
                errors.append({
                    "event_type": spec.event_type,
                    "kind": "recovery_closeout_field_missing",
                    "field": field,
                    "message": f"{spec.event_type} missing {field}",
                })
        if spec.action_policy not in ACTION_POLICIES and spec.action_policy != "kernel_consumed":
            errors.append({
                "event_type": spec.event_type,
                "kind": "recovery_closeout_action_policy_unknown",
                "field": "action_policy",
                "message": f"{spec.event_type} action_policy {spec.action_policy!r} is not registered",
            })
        if spec.intervention_class not in INTERVENTION_CLASSES and spec.intervention_class != "aggregate_result":
            errors.append({
                "event_type": spec.event_type,
                "kind": "recovery_closeout_intervention_class_unknown",
                "field": "intervention_class",
                "message": (
                    f"{spec.event_type} intervention_class "
                    f"{spec.intervention_class!r} is not registered"
                ),
            })
        if not route.expected_downstream_events:
            errors.append({
                "event_type": spec.event_type,
                "kind": "recovery_closeout_expected_downstream_missing",
                "field": "expected_downstream_events",
                "message": f"{spec.event_type} has no expected downstream event",
            })
        if action == "diagnose_attention" and spec.suggested_action_kind not in {
            "diagnose_attention",
            "diagnose_flow_stage_failure",
            "diagnose_flow_discovery_failure",
            "request_goal_gap_plan",
            "follow_workflow_rework",
            "investigate_runtime_bug",
            "repair_harness_bug",
            "repair_project_bug",
        }:
            warnings.append({
                "event_type": spec.event_type,
                "kind": "recovery_closeout_routes_to_generic_diagnosis",
                "message": (
                    f"{spec.event_type} suggested_action_kind "
                    f"{spec.suggested_action_kind!r} falls back to diagnose_attention"
                ),
            })
    return {
        "schema_version": "run-manager.recovery-closeout-contract.v1",
        "ok": not errors,
        "summary": {
            "checked": len(entries),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "entries": entries,
        "errors": errors,
        "warnings": warnings,
    }


def _spec_needs_recovery_closeout(spec: Any) -> bool:
    if getattr(spec, "owner_route", "") == "kernel_aggregate":
        return False
    semantics = tuple(getattr(spec, "run_manager_semantics", ()) or ())
    return (
        "pending_action" in semantics
        or "post_terminal_work" in semantics
        or bool(getattr(spec, "autoresearch_eligible", False))
        or str(getattr(spec, "supervisor_attention", "none") or "none") != "none"
    )


def _safe_action_for_spec(spec: Any) -> str:
    suggested = str(getattr(spec, "suggested_action_kind", "") or "")
    if suggested in SAFE_BATCH_ACTIONS | SAFE_TASK_ACTIONS | {
        "trigger_rework", "ship-retry",
    }:
        return suggested
    if suggested in {
        "needs_stage_dispatch",
        "needs_stage_replan",
        "needs_rework_dispatch",
        "needs_task_ref_repair",
        "needs_gate_dispatch",
        "blocked_external_gate",
        "needs_terminal_closeout",
    }:
        return suggested
    return "diagnose_attention"


def _no_progress_fingerprint(
    event_type: str,
    payload: dict[str, Any],
    *,
    fallback: str,
) -> str:
    if event_type == "task.dispatch_context.bound":
        source = str(payload.get("source") or "")
        if source == "writer_fanout_task_binding_recovery":
            raw = "|".join([
                "writer-fanout-binding-recovery-loop",
                str(payload.get("fanout_id") or ""),
                str(payload.get("child_id") or ""),
                str(payload.get("dispatch_id") or ""),
            ])
            return "rmfp-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    if event_type == "fanout.child.stale_completion":
        raw = "|".join([
            "fanout-stale-completion-loop",
            str(payload.get("reason") or ""),
            str(payload.get("fanout_id") or ""),
            str(payload.get("child_id") or ""),
            str(payload.get("source_event_type") or ""),
        ])
        return "rmfp-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    if event_type in {"integration.failed", "candidate.quality.failed"}:
        gates = payload.get("quality_gates_failed")
        if not isinstance(gates, list):
            gates = payload.get("gates_failed")
        gate_key = ",".join(
            sorted(str(value) for value in gates or [] if str(value).strip())
        )
        if gate_key:
            raw = "|".join([
                "candidate-quality-loop",
                str(payload.get("run_id") or payload.get("pdd_id") or ""),
                str(payload.get("stage_id") or payload.get("stage") or ""),
                gate_key,
            ])
            return "rmfp-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return fingerprint(payload, fallback=fallback)


def expected_downstream_events(safe_action: str) -> set[str]:
    triage_events = triage_expected_downstream_events(safe_action)
    if triage_events:
        return triage_events
    if safe_action == "diagnose_attention":
        return {"run.manager.autoresearch.requested", "run.manager.resident.prompted"}
    if safe_action == "worker_lifecycle_recover":
        return {"worker.respawn.requested"}
    if safe_action == "repair_closeout_validate":
        return {"run.manager.action.applied"}
    if safe_action == "resident_agent_reprompt":
        return {"run.manager.resident.prompted"}
    if safe_action == "failure_closeout_activate":
        return {"failure.closeout.activated", "run.manager.action.applied"}
    if safe_action == "needs_stage_dispatch":
        return {"task.dispatched", "workflow.resume.applied"}
    if safe_action == "needs_rework_dispatch":
        return {"task.rework.requested", "workflow.resume.applied"}
    if safe_action == "needs_stage_replan":
        return {"workflow.resume.applied"}
    if safe_action == "needs_task_ref_repair":
        return {"task.ref.repair.requested", "workflow.resume.applied"}
    if safe_action in {"needs_gate_dispatch", "blocked_external_gate", "needs_terminal_closeout"}:
        return {"stage.transition.stalled", "workflow.resume.applied"}
    if safe_action == "reemit_candidate_ready":
        return {"candidate.ready", "workflow.resume.applied"}
    if safe_action in {"repair_failed_children", "trigger_rework"}:
        return {"task_map.ready", "workflow.resume.applied"}
    return {"workflow.resume.applied"}


def fingerprint(payload: dict[str, Any], *, fallback: str) -> str:
    return str(
        payload.get("fingerprint")
        or payload.get("failure_fingerprint")
        or payload.get("checkpoint_id")
        or payload.get("idempotency_key")
        or stable_hash(payload)
        or fallback
    )


def stable_hash(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("run_id") or payload.get("pdd_id") or ""),
        str(payload.get("stage_id") or payload.get("stage") or ""),
        str(payload.get("action") or payload.get("safe_resume_action") or ""),
        str(payload.get("task_id") or payload.get("candidate_id") or ""),
        str(payload.get("reason") or payload.get("status") or ""),
        str(payload.get("verify_condition") or ""),
    ]
    raw = "|".join(parts)
    if not raw.strip("|"):
        return ""
    return "rmfp-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _decision(
    decision: str,
    *,
    executable: bool,
    payload: dict[str, Any],
    reason: str,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "run-manager.decision-policy.v1",
        "decision": decision,
        "executable": executable,
        "failure_class": str(payload.get("failure_class") or "unknown_complex"),
        "owner_route": str(payload.get("owner_route") or ""),
        "action_policy": str(payload.get("action_policy") or ""),
        "intervention_class": intervention_class_for_decision(decision, payload=payload),
        "verify_condition": str(payload.get("verify_condition") or ""),
        "reason": reason,
        "preflight": preflight or {},
    }


__all__ = [
    "ACTION_POLICIES",
    "CANDIDATE_REWORK_ACTIONS",
    "DIAGNOSIS_ACTIONS",
    "INTERVENTION_CLASSES",
    "OWNER_APPROVAL_ACTIONS",
    "ORCHESTRATOR_TRIAGE_ACTIONS",
    "REPAIR_CLOSEOUT_ACTIONS",
    "RESIDENT_AGENT_ACTIONS",
    "SAFE_BATCH_ACTIONS",
    "SAFE_TASK_ACTIONS",
    "SUPPORTED_BATCH_ACTIONS",
    "SUPPORTED_TASK_ACTIONS",
    "WORKER_LIFECYCLE_ACTIONS",
    "ActionRoute",
    "build_no_progress_projection",
    "classify_recovery_context",
    "decide_action_policy",
    "expected_downstream_events",
    "fingerprint",
    "intervention_class_for_decision",
    "preflight_action",
    "recovery_closeout_contract_report",
    "route_for_safe_action",
    "stable_hash",
]
