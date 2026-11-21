"""Retire a failed candidate only after its admitted replacement was applied."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.state.locks import locked_path
from zf.runtime.repair_dispatch import hydrate_repair_contract
from zf.runtime.repair_validation_binding import validation_matches_candidate

ACTION = "repair-closeout-dispose"
DISCARDED = "run.manager.repair.merge.discarded"


def _trusted_failure(event, events: list) -> bool:
    if event is None:
        return False
    if event.origin == "kernel":
        return True
    # Older controlled operator calls used an unset EventWriter origin. Admit
    # only their complete intent -> planned -> outcome chain, never a worker.
    if event.origin or event.actor != "run-manager":
        return False
    planned = next((e for e in events if e.id == event.causation_id
                    and e.type == "run.manager.action.planned"
                    and not e.origin and e.actor == "run-manager"), None)
    if planned is None or any(planned.payload.get(key) != event.payload.get(key) for key in (
        "action", "queue_id", "repair_contract_digest", "checkpoint_id", "request_id",
        "source_commit", "previous_validation_event_id",
    )):
        return False
    request = next((e for e in events if e.id == planned.causation_id
                    and e.id == event.payload.get("request_id")
                    and e.type == "operator.intent.created" and e.origin == "external"
                    and e.actor == "operator"), None)
    body = (request.payload.get("action_request") or {}) if request else {}
    return bool(request and request.payload.get("action") == "repair-closeout-validate"
                and event.payload.get("action") == "repair-closeout-validate"
                and all(body.get(key) == event.payload.get(key)
                        for key in ("queue_id", "repair_contract_digest", "previous_validation_event_id")))


def receipt_matches(item: dict, payload: dict) -> bool:
    return bool(item.get("status") == "needs_review"
        and item.get("validation", {}).get("status") == "failed"
        and payload.get("action") == ACTION
        and payload.get("disposition") == "replaced"
        and payload.get("previous_validation_event_id") == item["validation"].get("event_id")
        and all(payload.get(key) and payload[key] == item.get(key)
                for key in ("queue_id", "source_commit", "repair_contract_digest"))
        and payload.get("replacement_queue_id") != item.get("queue_id")
        and all(payload.get(key) for key in (
            "replacement_queue_id", "replacement_contract_digest", "replacement_apply_event_id",
            "replacement_acceptance_event_id", "reason")))


def prepare_disposition(*, state_dir: Path, events: list, config: Any,
                        payload: dict, request_event_id: str) -> dict:
    from zf.runtime.run_manager import build_repair_merge_queue

    policy = config.runtime.run_manager.source_repair if config else None
    if policy is None or not policy.enabled:
        raise ValueError("source_repair_not_authorized")
    rows = {row["queue_id"]: row for row in build_repair_merge_queue(events)["items"]}
    old = rows.get(str(payload.get("queue_id") or ""), {})
    new = rows.get(str(payload.get("replacement_queue_id") or ""), {})
    if not old.get("repair_contract_required") or old.get("status") != "needs_review":
        raise ValueError("repair_disposition_queue_not_current")
    validation = old.get("validation") or {}
    if (validation.get("status") != "failed" or not payload.get("previous_validation_event_id")
            or payload["previous_validation_event_id"] != validation.get("event_id")
            or payload.get("repair_contract_digest") != old.get("repair_contract_digest")):
        raise ValueError("repair_disposition_failure_mismatch")
    failed = next((e for e in events if e.id == validation["event_id"]), None)
    if not _trusted_failure(failed, events):
        raise ValueError("repair_disposition_failure_not_trusted")
    hydrate_repair_contract(state_dir, old.get("repair_contract_ref"),
                           expected_digest=old["repair_contract_digest"])
    if (not new.get("repair_contract_required") or new.get("status") != "merged"
            or old["queue_id"] == new.get("queue_id")):
        raise ValueError("repair_disposition_replacement_not_applied")
    result = (new.get("validation") or {}).get("result") or {}
    verified = next((e for e in events if e.id == new.get("validation", {}).get("event_id")
                     and e.type == "run.manager.action.applied" and e.origin == "kernel"), None)
    if (new["validation"].get("status") != "passed" or not result.get("ok")
            or verified is None
            or result.get("repair_contract_digest") != new.get("repair_contract_digest")
            or not validation_matches_candidate({**new, "validation_result": result})):
        raise ValueError("repair_disposition_replacement_not_validated")
    contract = hydrate_repair_contract(state_dir, new.get("repair_contract_ref"),
                                      expected_digest=new["repair_contract_digest"])
    accepted = next((e for e in events if e.id == contract.get("source_event_id")
                     and e.type == "run.manager.repair.accepted" and e.origin == "kernel"), None)
    if accepted is None or accepted.payload.get("candidate_id") != new.get("candidate_id"):
        raise ValueError("repair_disposition_replacement_not_admitted")
    task = accepted.payload.get("repair_task_payload") or {}
    identity = task.get("failure_identity") or {}
    continuation = task.get("problem_continuation") or {}
    linked = (identity.get("previous_queue_id") == old["queue_id"]
              and identity.get("event_id") == failed.id)
    linked |= (continuation.get("original_candidate_id") == old["queue_id"]
               and failed.id in (continuation.get("failure_event_ids") or []))
    if not linked:
        raise ValueError("repair_disposition_replacement_failure_unbound")
    for key in ("workflow_run_id", "task_id"):
        expected = (old.get("continuation") or {}).get(key)
        if expected and expected != (contract.get("continuation") or {}).get(key):
            raise ValueError("repair_disposition_replacement_scope_mismatch:" + key)
    merged = next((e for e in reversed(events)
        if e.type == "run.manager.repair.merge.merged" and e.origin == "kernel"
        and e.payload.get("queue_id") == new["queue_id"]
        and e.payload.get("repair_contract_digest") == new["repair_contract_digest"]
        and e.payload.get("source_commit") == new["source_commit"]
        and e.payload.get("applied_commit") == new.get("applied_commit")), None)
    if merged is None or not new.get("applied_commit"):
        raise ValueError("repair_disposition_replacement_apply_unbound")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("repair_disposition_reason_required")
    key = hashlib.sha256((old["queue_id"] + old["repair_contract_digest"] + merged.id).encode()).hexdigest()[:24]
    action = {
        "action": ACTION, "safe_resume_action": "repair_closeout_dispose",
        "checkpoint_id": "repair-dispose-" + key,
        # Retiring an obsolete candidate is a separate action, never a reset of
        # the failed repair's case or permission to retry it.
        "recovery_case_id": "repair-disposition-" + key,
        "original_failure_case_id": str(failed.payload.get("recovery_case_id") or ""),
        "failure_class": "repair_candidate_disposition", "disposition": "replaced",
        "queue_id": old["queue_id"], "source_commit": old["source_commit"],
        "repair_contract_digest": old["repair_contract_digest"],
        "previous_validation_event_id": failed.id,
        "replacement_queue_id": new["queue_id"],
        "replacement_contract_digest": new["repair_contract_digest"],
        "replacement_acceptance_event_id": accepted.id,
        "replacement_apply_event_id": merged.id, "reason": reason,
        "source_event_ids": [failed.id, accepted.id, merged.id, request_event_id],
        "workflow_run_id": (old.get("continuation") or {}).get("workflow_run_id", ""),
        "task_id": (old.get("continuation") or {}).get("task_id", ""),
        "expected_downstream_events": [DISCARDED],
        "verify_condition": "expected_downstream_event:" + DISCARDED,
    }
    from zf.runtime.run_manager import decide_action_policy, preflight_action
    action["preflight"] = preflight_action(action=ACTION, payload=action)
    action["policy_decision"] = decide_action_policy(action=ACTION, payload=action)
    return action


def execute_disposition(*, state_dir, writer, config, action, causation_id):
    from zf.runtime.run_manager import _post_verify_action, action_gate_reason, _pending_human_decisions

    # Recheck current evidence immediately before the append-only disposition.
    prepared = prepare_disposition(state_dir=state_dir, events=writer.event_log.read_all(),
        config=config, payload=action, request_event_id=causation_id)
    # The incoming resident request case already passed its admission gate.
    # Check this candidate's own disposition case too, then emit only under it.
    # Candidate retirement must not change the original/diagnosis case status.
    events = writer.event_log.read_all()
    if action_gate_reason(state_dir, events, prepared, _pending_human_decisions(events)):
        return "blocked"
    planned = writer.emit("run.manager.action.planned", actor="run-manager",
                          causation_id=causation_id, payload=prepared)
    discarded = writer.emit(DISCARDED, actor="run-manager", causation_id=planned.id,
                            payload=prepared)
    applied = writer.emit("run.manager.action.applied", actor="run-manager",
                          causation_id=discarded.id, payload={**prepared, "status": "applied"})
    _post_verify_action(writer, prepared, {"ok": True, "status": "applied",
        "emitted_event_ids": [discarded.id, applied.id]}, causation_id=applied.id)
    return "applied"


def execute_action(service, *, requested, action, requested_action, payload):
    from zf.runtime.run_manager import _execute_controlled_run_action

    try:
        with locked_path(service.state_dir / "locks" / "run-manager-tick"):
            prepared = prepare_disposition(state_dir=service.state_dir,
                events=service.writer.event_log.read_all(), config=service.config,
                payload=payload, request_event_id=requested.id)
            if payload.get("dry_run") is True:
                return {"ok": True, "status": "preview", "action": action, "prepared": prepared}
            status = _execute_controlled_run_action(state_dir=service.state_dir,
                writer=service.writer, config=service.config, project_root=service.project_root,
                action=prepared, causation_id=requested.id)
            if status != "applied":
                raise ValueError("repair_disposition_" + status)
    except (OSError, ValueError, KeyError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
                               task_id=None, reason=str(exc), status_code=409)
    service._completed(requested=requested, event=requested, action=action,
        requested_action=requested_action, task_id=prepared.get("task_id") or None, status=status)
    return {"ok": True, "status": status, "action": action, "checkpoint_id": prepared["checkpoint_id"]}
