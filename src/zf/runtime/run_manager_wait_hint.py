"""Non-blocking Run Manager wait hints and resident repair policy projection."""

from __future__ import annotations

from typing import Any


def build_wait_hint_projection(
    *,
    monitor: dict[str, Any],
    completion_profile: dict[str, Any],
    repair_merge_queue: dict[str, Any],
    no_progress: dict[str, Any],
) -> dict[str, Any]:
    hints = []
    if monitor.get("state") == "healthy_waiting" and not monitor.get("in_flight_tasks"):
        hints.append(_hint("idle_no_inflight", "No in-flight tasks are visible; wait for the next runtime event or kickoff."))
    if int((repair_merge_queue.get("summary") or {}).get("pending") or 0) > 0:
        hints.append(_hint("repair_closeout_pending", "Repair closeout is pending operator merge/reject decision."))
    if completion_profile.get("pending_human_decisions"):
        hints.append(_hint("human_decision_pending", "Human decision is pending and blocks terminal success."))
    if no_progress.get("status") == "tripped":
        hints.append(_hint("no_progress_tripped", "Same failure fingerprint crossed no-progress threshold."))
    return {
        "schema_version": "run-manager.wait-hints.v1",
        "is_derived_projection": True,
        "blocking": False,
        "summary": {"hints": len(hints)},
        "items": hints,
    }


def build_resident_repair_policy_projection() -> dict[str, Any]:
    return {
        "schema_version": "run-manager.resident-repair-policy.v1",
        "is_derived_projection": True,
        "enabled": True,
        "default_lifecycle": "on_demand",
        "execution": {
            "mode": "bounded_repair_worker",
            "trigger_event": "run.manager.repair.accepted",
            "executor": "self_repair_runner",
            "requires_preflight": True,
            "requires_closeout_gate": True,
        },
        "resident": {
            "enabled": False,
            "role": "run_manager_monitor_and_recommendation_agent",
            "can_request_repair": True,
            "requires_explicit_config": False,
            "requires_heartbeat": True,
            "requires_idle_unload": True,
            "requires_closeout_event": True,
            "auto_merge": False,
        },
        "reason": (
            "Run Manager is not monitor-only: the resident agent may request "
            "bounded repair, while the long-lived resident process itself does "
            "not directly mutate code or runtime truth."
        ),
    }


def _hint(kind: str, message: str) -> dict[str, str]:
    return {
        "kind": kind,
        "message": message,
        "severity": "info",
        "action": "observe_or_operator_decide",
    }


__all__ = ["build_resident_repair_policy_projection", "build_wait_hint_projection"]
