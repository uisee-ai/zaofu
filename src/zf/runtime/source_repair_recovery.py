"""Evidence binding for resuming a workflow after a harness repair restart."""
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def pending_validation_diagnosis(item: dict[str, Any]) -> dict[str, Any] | None:
    """Route a retained validation failure to an agent, without retrying it."""
    validation = item.get("validation") or {}
    failed_id = str(validation.get("event_id") or "")
    if (item.get("status") != "needs_review" or validation.get("status") != "failed"
            or not failed_id or item.get("next_allowed_action") != "repair_validation_failed"):
        return None
    continuation = item.get("continuation") or {}
    return {
        "schema_version": "run-manager.pending-action.v1", "action": "diagnose-attention",
        "checkpoint_id": "repair-validation-diagnosis-" + failed_id,
        "safe_resume_action": "diagnose_attention", "fingerprint": "repair-validation:" + failed_id,
        "queue_id": item.get("queue_id"), "candidate_id": item.get("candidate_id"),
        "source_event_ids": [failed_id], "continuation": continuation,
        "source_commit": item.get("source_commit"), "base_commit": item.get("base_commit"),
        "worktree_path": item.get("worktree_path"),
        "repair_contract_digest": item.get("repair_contract_digest"),
        "workflow_run_id": continuation.get("workflow_run_id", ""),
        "task_id": continuation.get("task_id", ""),
        "failure_class": "repair_validation_failed", "owner_route": "run_manager",
        "suggested_route": "run_manager_resident_agent", "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "expected_downstream_events": ["run.manager.resident.prompted", "run.manager.autoresearch.requested"],
        "expected_output": ["run.manager.agent.recommendation"],
        "reason": "Repair validation failed: " + str(validation.get("reason") or "unknown")
            + "; inspect the frozen candidate and retained validation result before choosing recovery. "
              "A diagnosis does not authorize retry, rebase, apply, or replacement of failed evidence.",
    }


def pending_restart_diagnosis(item: dict[str, Any]) -> dict[str, Any] | None:
    """An unobserved apply/restart needs a bounded diagnosis, never blind reapply."""
    progress = next((row for row in reversed(item.get("events") or []) if row.get("event_type") in {
        "run.manager.repair.merge.merging", "run.manager.repair.merge.merged",
    }), None)
    if not progress:
        return None
    try:
        at = datetime.fromisoformat(progress["ts"].replace("Z", "+00:00"))
        if at.tzinfo is None or (datetime.now(timezone.utc) - at).total_seconds() < 120:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    continuation = item.get("continuation") or {}
    return {
        "schema_version": "run-manager.pending-action.v1", "action": "diagnose-attention",
        "checkpoint_id": "repair-restart-diagnosis-" + progress["event_id"],
        "safe_resume_action": "diagnose_attention", "fingerprint": str(item.get("fingerprint") or item.get("queue_id")),
        "queue_id": item.get("queue_id"), "source_event_ids": [progress["event_id"]],
        "workflow_run_id": continuation.get("workflow_run_id", ""),
        "failure_class": "repair_restart_outcome_unknown", "owner_route": "run_manager",
        "suggested_route": "run_manager_resident_agent", "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose", "expected_downstream_events": ["run.manager.resident.prompted", "run.manager.autoresearch.requested"],
        "expected_output": ["run.manager.agent.recommendation"],
        "reason": "Repair apply/restart has no matching reactor load receipt after 120s; inspect retained effects and host supervision before choosing recovery.",
    }


def rollback_unstarted_repair(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Revert only our clean, still-current commit when actuator spawn failed.

    Preserve history and refuse to touch a concurrent edit or commit. This is
    not a rollback after an unknown boot outcome or after workload resumption.
    """
    from zf.runtime.source_repair_apply import _git, _git_text

    applied, base = receipt.get("applied_commit"), receipt.get("base_commit")
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if (not applied or not base or str(root.resolve()) != receipt.get("source_root")
            or _git_text(root, "rev-parse", "HEAD") != applied
            or _git_text(root, "rev-parse", "HEAD^") != base
            or not status["ok"] or status["stdout"].strip()):
        return {"status": "blocked", "reason": "rollback_source_changed"}
    outcome = _git(root, "revert", "--no-edit", applied)
    if not outcome["ok"]:
        _git(root, "revert", "--abort")
        return {"status": "failed", "reason": "rollback_revert_failed"}
    if _git_text(root, "rev-parse", "HEAD^{tree}") != _git_text(root, "rev-parse", f"{base}^{{tree}}"):
        return {"status": "failed", "reason": "rollback_tree_mismatch"}
    return {"status": "reverted", "rollback_commit": _git_text(root, "rev-parse", "HEAD"),
            "base_commit": base, "reverted_commit": applied}


def repaired_runtime_observed(
    state_dir: Path | None, events: list[ZfEvent], receipt: dict[str, Any],
) -> bool:
    """Require the latest reactor boot to load this applied source and state.

    A session creation alone is not a running reactor; an older matching boot
    must not hide a later stopped/unknown or mismatched controller.
    """
    if state_dir is None or not all(receipt.get(key) for key in (
        "applied_commit", "source_root", "source_tree_sha256",
    )):
        return False
    lifecycle = next((e for e in reversed(events)
                      if e.type in {"loop.started", "loop.stopped"}), None)
    if lifecycle is None or lifecycle.type != "loop.started" or lifecycle.origin != "kernel":
        return False
    ref = lifecycle.payload.get("runtime_load_ref")
    if not isinstance(ref, dict) or ref.get("kind") != "runtime-load":
        return False
    try:
        hydrated = hydrate_sidecar_ref(state_dir, ref)
        body = hydrated.payload
        return bool(hydrated.ok and isinstance(body, dict)
                    and body.get("schema_version") == "runtime-load.v1"
                    and isinstance(body.get("state_dir"), str)
                    and Path(body["state_dir"]).resolve() == state_dir.resolve()
                    and body.get("source_root") == receipt["source_root"]
                    and body.get("source_revision") == receipt["applied_commit"]
                    and body.get("source_tree_sha256") == receipt["source_tree_sha256"])
    except (OSError, ValueError, TypeError):
        return False
