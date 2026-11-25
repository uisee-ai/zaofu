"""Admission-bound owner limits for unattended recovery in an opted-in Run."""
from dataclasses import asdict
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent

from zf.runtime.task_host_manifest import digest


def run_grant_fields(config: ZfConfig | None) -> dict:
    manager = getattr(getattr(config, "runtime", None), "run_manager", None)
    grant = getattr(manager, "recovery_authorization", None)
    if grant is None:
        return {}
    # Bind configured authority, including the exact probe commands and envelopes,
    # source path/restart policy, role permissions, stage and Run attempt budgets.
    authority = {
        "grant": asdict(grant),
        "host": asdict(config.runtime.host_verification),
        "repair": asdict(manager.source_repair),
        "publication": asdict(config.runtime.task_publication),
        "project": asdict(config.project),
        "providers": asdict(config.providers),
        "cost": asdict(config.cost),
        "gates": {key: asdict(gate) for key, gate in config.quality_gates.items()},
        "roles": [asdict(role) for role in config.roles],
        "workflow": asdict(config.workflow),
        "security": asdict(config.security),
        "safety": asdict(config.safety),
        "constraints": asdict(config.constraints),
        "execution_routing": asdict(config.runtime.execution_routing),
        "budget_enforcement_enabled": config.budget_enforcement_enabled,
        "budget_fail_closed": config.budget_fail_closed,
        "global_budget_usd": config.global_budget_usd,
    }
    return {"recovery_authorization": {**asdict(grant), "policy_digest": digest(authority)}}


def current_run_grant(config: ZfConfig | None, events: list[ZfEvent], run_id: str) -> tuple[dict | None, str]:
    """The admission actor takes the grant from config; proposed payloads cannot grant it."""
    if not run_id:
        protected = {e.payload.get("workflow_run_id") for e in events if e.type == "run.admission.admitted"
                     and e.origin == "kernel" and e.actor == "orchestrator" and "recovery_authorization" in e.payload}
        if protected:
            from zf.runtime.run_admission import build_run_admission_projection

            if protected.intersection(build_run_admission_projection(events).active_run_ids):
                return None, "missing_or_ambiguous_run_recovery_authorization"
    recorded = [
        e for e in events if e.type == "run.admission.admitted" and e.origin == "kernel"
        and e.actor == "orchestrator" and e.payload.get("workflow_run_id") == run_id
    ]
    current = run_grant_fields(config).get("recovery_authorization")
    if current is None and not any("recovery_authorization" in e.payload for e in recorded):
        return None, ""
    if len(recorded) != 1 or not run_id:
        return None, "missing_or_ambiguous_run_recovery_authorization"
    if current != recorded[0].payload.get("recovery_authorization") or current is None:
        return None, "run_recovery_authorization_changed"
    return current, ""


def reader_grant_error(config: ZfConfig, events: list[ZfEvent], run_id: str, *,
                       state_dir: Path, operation: dict | None, failed: ZfEvent) -> str:
    grant, error = current_run_grant(config, events, run_id)
    if error or not grant:
        return error
    from zf.core.task.store import TaskStore
    from zf.runtime.run_continuation import bind_action_dependencies, blocking_decisions_for_action
    from zf.runtime.run_manager import _pending_human_decisions
    from zf.runtime.candidate_host_consumption import admitted_host_commands, available_host_receipts
    from zf.runtime.candidate_plan_recovery import current_frozen_reader_inputs
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    # This narrowly delegated path is for an already admitted Candidate execution
    # abstention. Ordinary product rejection and different Reader roles stay outside.
    if not operation or operation.get("semantic_verdict") != "abstained":
        return "unapproved_reader_failure_for_run_recovery"
    try:
        scoped = bind_action_dependencies([{"action": "child-rebuild", **operation}],
                                          TaskStore(state_dir / "kanban.json").list_all())[0]
        if blocking_decisions_for_action(_pending_human_decisions(events), scoped, events=events):
            return "reader_recovery_requires_owner_decision"
        ref = operation["admitted_control_result_ref"]
        if ref != failed.payload.get("control_result_ref"):
            return "reader_recovery_missing_signed_result"
        result = hydrate_sidecar_ref(state_dir, ref).payload
        request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
        if request["output_profile_id"] != "candidate-verify":
            return "unapproved_reader_failure_for_run_recovery"
        current_frozen_reader_inputs(state_dir, events, result)
        receipts = available_host_receipts(state_dir, {**request, "operation_id": operation["operation_id"],
            "attempt_id": operation["active_attempt_id"]})
        if not receipts:
            return "reader_recovery_missing_signed_host_receipt"
        # Independently verify the offered evidence without attributing its use
        # to the Reader or modifying its abstention.
        admitted_host_commands(state_dir, {**result,
            "host_execution_receipt_ids": [row["receipt_id"] for row in receipts]},
            operation=operation, events=events)
    except (KeyError, ValueError, OSError, TypeError):
        return "reader_recovery_candidate_binding_changed"
    used = sum(e.type == "runtime.action.attempt.started" and e.origin == "kernel" and e.actor == "run-manager"
               and e.payload.get("action") == "child-rebuild" and e.payload.get("workflow_run_id") == run_id
               for e in events)
    return "reader_rebuild_run_allowance_exhausted" if used >= grant["max_reader_rebuilds_per_run"] else ""


def reserve_reader_rebuild(config, state: Path, writer, request: ZfEvent) -> tuple[ZfEvent | None, str]:
    """Charge one kernel permit before the provider send, including a send of unknown outcome."""
    from zf.core.state.locks import locked_path
    from zf.runtime.reader_rebuild_admission import reader_rebuild_error

    run_id = request.payload.get("workflow_run_id") or ""
    with locked_path(state / "locks" / ("reader-recovery-" + digest(run_id))):
        events = writer.event_log.read_all()
        grant, error = current_run_grant(config, events, run_id)
        if error or not grant:
            return None, error
        if any(e.origin == "kernel" and e.type == "runtime.action.attempt.started"
               and e.actor == "run-manager" and e.causation_id == request.id for e in events):
            return None, "reader_recovery_reserved_inspect_original_effect"
        error = reader_rebuild_error(state, events, request.payload, config=config)
        if error:
            return None, error
        start = writer.append(ZfEvent(type="runtime.action.attempt.started", origin="kernel", actor="run-manager",
            causation_id=request.id, correlation_id=run_id, payload={"action":"child-rebuild",
            "workflow_run_id":run_id, "authority_ref":grant["approval_ref"],
            **{key:request.payload[key] for key in ("target_operation_id", "fanout_id", "fanout_child_id",
                                                    "active_attempt_id", "source_event_id")}}))
        return start, ""


def checkpoint_grant_error(config: ZfConfig | None, events: list[ZfEvent], action: dict) -> str:
    continuation = action.get("continuation") or {}
    run_id = continuation.get("workflow_run_id") or continuation.get("run_id") or ""
    grant, error = current_run_grant(config, events, run_id)
    if error or not grant:
        return error
    from zf.runtime.run_admission import build_run_admission_projection
    from zf.runtime.run_continuation import blocking_decisions_for_action
    from zf.runtime.run_manager import _pending_human_decisions, _repair_continuation_stale_reason
    from zf.runtime.run_manager_router import route_for_safe_action

    run = build_run_admission_projection(events).runs.get(run_id)
    validation = next((event for event in events if event.id == action.get("validation_event_id")), None)
    if (not run or run.status != "running" or action.get("workflow_run_id") != run_id
            or action.get("approval_ref") != grant["approval_ref"]
            or blocking_decisions_for_action(_pending_human_decisions(events), action, events=events)
            or _repair_continuation_stale_reason(events, item=action, continuation=continuation)):
        return "checkpoint_no_longer_authorized"
    if (not validation or validation.type != "run.manager.action.applied" or validation.origin != "kernel"
            or validation.actor != "run-manager" or validation.payload.get("action") != "repair-closeout-validate"
            or validation.payload.get("queue_id") != action.get("queue_id")
            or validation.payload.get("validation_result") != action.get("validation_result")):
        return "checkpoint_missing_bound_validation"
    if not (action.get("repair_contract_required") and action.get("repair_contract_digest")
            and action.get("validation_event_id") and action.get("validation_status") == "passed"
            and continuation.get("resume_original_workflow") is True and action.get("continuation_checkpoint_id")
            and action["continuation_checkpoint_id"] == (continuation.get("checkpoint_id") or continuation.get("resume_checkpoint_ref"))
            and action.get("continuation_safe_resume_action") == continuation.get("safe_resume_action")
            and action.get("continuation_safe_resume_action") in grant["safe_resume_actions"]
            and route_for_safe_action(continuation["safe_resume_action"]).action_policy == "auto_decide"):
        return "checkpoint_outside_run_recovery_authorization"
    repair = config.runtime.run_manager.source_repair
    if (not repair.enabled or not repair.publish_candidate or not repair.replay_before_restart
            or repair.apply_policy != "verified_checkpoint_apply" or repair.restart_policy != "operator_approved"
            or action.get("allow_paths") != repair.allow_paths or action.get("deny_paths") != repair.deny_paths):
        return "checkpoint_repair_policy_changed"
    used = sum(e.type == "run.manager.repair.merge.merging" and e.origin == "kernel"
               and e.actor == "run-manager" and e.payload.get("workflow_run_id") == run_id for e in events)
    return "checkpoint_restart_run_allowance_exhausted" if used >= grant["max_checkpoint_restarts_per_run"] else ""


def authorize_restart_effect(project: Path, state: Path, merge_id: str, pid: int) -> bool:
    """Recheck live config and an issued restart receipt before touching the watcher."""
    from zf.core.config.loader import ConfigError, load_config
    from zf.core.events.factory import event_log_from_project
    from zf.core.events.writer import EventWriter
    from zf.core.state.locks import locked_path
    from zf.runtime.pause_lifecycle import is_dispatch_paused
    from zf.runtime.run_admission import build_run_admission_projection
    from zf.runtime.run_continuation import blocking_decisions_for_action
    from zf.runtime.run_manager import _pending_human_decisions, _repair_continuation_stale_reason

    # The Run Manager writes the restart/merged receipts before releasing its tick lock.
    with locked_path(state / "locks" / "run-manager-tick"):
        try:
            config = load_config(project / "zf.yaml")
        except (ConfigError, ValueError, OSError):
            return False  # An invalid identity must not open or write a different Run ledger.
        if (project / config.project.state_dir).resolve() != state:
            return False
        log = event_log_from_project(state, config=config)
        events = log.read_all()
        merge = next((e for e in events if e.id == merge_id and e.origin == "kernel"
            and e.actor == "run-manager" and e.type == "run.manager.repair.merge.merging"), None)
        if not merge:
            return False
        action = merge.payload
        run_id = action.get("workflow_run_id") or ""
        request = next((e for e in events if e.causation_id == merge_id and e.origin == "kernel"
            and e.actor == "run-manager" and e.type == "runtime.restart.requested"
            and e.payload.get("actuator_pid") == pid), None)
        if not request:
            return False
        completed = next((e for e in events if e.causation_id == request.id and e.origin == "kernel"
            and e.actor == "run-manager" and e.type == "run.manager.repair.merge.merged"
            and e.payload.get("workflow_run_id") == run_id and e.payload.get("applied_commit")
            and e.payload.get("applied_commit") == request.payload.get("applied_commit")), None)
        reason = "restart_missing_applied_checkpoint"
        if completed:
            try:
                grant, error = current_run_grant(config, events, run_id)
                run = build_run_admission_projection(events).runs.get(run_id)
                if (grant and grant["approval_ref"] == action.get("approval_ref")
                        and run and run.status == "running" and not is_dispatch_paused(state, events=events)
                        and not _repair_continuation_stale_reason(events, item=action, continuation=action["continuation"])
                        and not blocking_decisions_for_action(_pending_human_decisions(events), action, events=events)):
                    return True
                reason = error or "checkpoint_no_longer_authorized"
            except (ConfigError, ValueError, OSError, TypeError, KeyError):
                reason = "restart_recovery_authorization_unavailable"
        EventWriter(log, default_origin="kernel").emit("human.escalate", actor="run-manager",
            causation_id=request.id, correlation_id=run_id, payload={"workflow_run_id": run_id,
            "checkpoint_id": action.get("continuation_checkpoint_id"), "reason": reason,
            "blocking_scope": "run", "source_event_ids": [merge_id, request.id]})
        return False
