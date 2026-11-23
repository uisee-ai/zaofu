"""Rebuild one authorized verification attempt from retained repair evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.state.locks import locked_path
from zf.runtime.repair_dispatch import validate_repair_contract_action
from zf.runtime.source_repair_apply import _hard_denied, _matches_any


def validator_digest() -> str:
    digest = hashlib.sha256()
    for name in ("repair_revalidation.py", "repair_checkout_contract.py",
                 "repair_validation_binding.py", "run_manager_repair_validation.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def _checkpoint(item: dict, version: str) -> str:
    key = hashlib.sha256("|".join([
        str(item.get("queue_id") or ""), str(item.get("repair_contract_digest") or ""),
        str(item.get("source_commit") or ""), version,
    ]).encode()).hexdigest()[:24]
    return "repair-revalidate-" + key


def result_matches_queue(item: dict, payload: dict, *, passed: bool) -> bool:
    from zf.runtime.repair_validation_binding import validation_matches_candidate

    validation = item.get("validation") or {}
    if (item.get("status") != "needs_review" or validation.get("status") != "failed"
            or item.get("next_allowed_action") != "repair_validation_failed"
            or payload.get("previous_validation_event_id") != validation.get("event_id")
            or not payload.get("validator_digest")
            or payload.get("checkpoint_id") != _checkpoint(item, payload["validator_digest"])
            or any(payload.get(key) != item.get(key) for key in (
                "source_commit", "base_commit", "worktree_path", "repair_contract_digest", "changed_files",
            ))):
        return False
    if not passed:
        return True
    result = payload.get("validation_result") or {}
    return bool(result.get("ok") and result.get("repair_contract_digest") == item.get("repair_contract_digest")
                and validation_matches_candidate({**item, "validation_result": result}))


def prepare_revalidation(*, state_dir: Path, events: list, config: Any,
                         payload: dict, request_event_id: str) -> dict:
    from zf.runtime.run_manager import build_repair_merge_queue, decide_action_policy, preflight_action

    policy = config.runtime.run_manager.source_repair if config else None
    if policy is None or not policy.enabled:
        raise ValueError("source_repair_not_authorized")
    queue_id = str(payload.get("queue_id") or "")
    item = next((row for row in build_repair_merge_queue(events)["items"]
                 if row["queue_id"] == queue_id), None)
    if item is None or item.get("status") != "needs_review":
        raise ValueError("repair_revalidation_queue_not_current")
    validation = item.get("validation") or {}
    previous = str(payload.get("previous_validation_event_id") or "")
    if (validation.get("status") != "failed" or not previous
            or previous != validation.get("event_id")
            or item.get("next_allowed_action") != "repair_validation_failed"):
        raise ValueError("repair_revalidation_failure_not_current")
    failed = next((event for event in events if event.id == previous), None)
    if failed is None or failed.origin not in {"", "kernel"}:
        raise ValueError("repair_revalidation_failure_not_trusted")
    contract_digest = str(payload.get("repair_contract_digest") or "")
    if not contract_digest or contract_digest != item.get("repair_contract_digest"):
        raise ValueError("repair_revalidation_contract_mismatch")
    names = item.get("changed_files") or []
    if not names or any(_hard_denied(name) or not _matches_any(name, policy.allow_paths)
                        or _matches_any(name, policy.deny_paths) for name in names):
        raise ValueError("repair_revalidation_scope_not_authorized")
    version = validator_digest()
    checkpoint = _checkpoint(item, version)
    if any(event.type in {"run.manager.action.planned", "run.manager.action.applied", "run.manager.action.failed"}
           and event.payload.get("checkpoint_id") == checkpoint for event in events):
        raise ValueError("repair_revalidation_already_attempted_for_validator")
    continuation = item.get("continuation") or {}
    action = {key: item.get(key) for key in (
        "queue_id", "fingerprint", "attempt", "candidate_id", "branch", "worktree_path",
        "base_commit", "source_commit", "source_title", "changed_files", "risk_classification",
        "verification_plan", "repair_contract_ref", "repair_contract_digest", "continuation",
    )}
    action.update({
        "schema_version": "run-manager.pending-action.v1", "action": "repair-closeout-validate",
        "checkpoint_id": checkpoint, "safe_resume_action": "repair_closeout_validate",
        "repair_contract_required": True, "previous_validation_event_id": previous,
        "validator_digest": version, "source_event_id": previous,
        "source_event_ids": [previous, request_event_id],
        "request_id": str(payload.get("request_id") or request_event_id),
        "recovery_case_id": str(failed.payload.get("recovery_case_id") or ""),
        "workflow_run_id": continuation.get("workflow_run_id", ""),
        "task_id": continuation.get("task_id", ""),
        "failure_class": "self_repair_validation", "owner_route": "controlled_action",
        "action_policy": "auto_decide", "intervention_class": "auto_recover",
        "expected_downstream_events": ["run.manager.action.applied"],
        "verify_condition": "expected_downstream_event:run.manager.action.applied",
    })
    validate_repair_contract_action(state_dir, action)
    action["preflight"] = preflight_action(action=action["action"], payload=action)
    action["policy_decision"] = decide_action_policy(action=action["action"], payload=action)
    return action


def execute_action(service, *, requested, action, requested_action, payload):
    from zf.runtime.run_manager import _execute_controlled_run_action

    try:
        with locked_path(service.state_dir / "locks" / "run-manager-tick"):
            prepared = prepare_revalidation(state_dir=service.state_dir,
                events=service.writer.event_log.read_all(), config=service.config,
                payload=payload, request_event_id=requested.id)
            if payload.get("dry_run") is True:
                return {"ok": True, "status": "preview", "action": action, "prepared": prepared}
            status = _execute_controlled_run_action(state_dir=service.state_dir,
                writer=service.writer, config=service.config, project_root=service.project_root,
                action=prepared, causation_id=requested.id)
            if status != "applied":
                raise ValueError("repair_revalidation_" + status)
    except (OSError, ValueError, KeyError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
            task_id=None, reason=str(exc), status_code=409)
    service._completed(requested=requested, event=requested, action=action,
        requested_action=requested_action, task_id=prepared.get("task_id") or None, status=status)
    return {"ok": True, "status": status, "action": action, "checkpoint_id": prepared["checkpoint_id"]}
