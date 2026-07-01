"""doc 84 B — replan adoption re-drive on owner approval.

doc 84 A made owner approval a deterministic gate in
``_prepare_replan_adoption``: an owner-gated replan blocks at
``replan.adoption.awaiting_owner`` until a matching
``replan.owner_decision.approved`` exists. But nothing re-drives the
adoption after the owner approves — there is no retry of a blocked
product-delivery manifest (verified 2026-06-10) — so the approval would
sit unactuated.

This module closes that half: a tick sweep that, for every
``awaiting_owner`` adoption whose owner has since approved and which has
not already completed/been-rejected, reloads the candidate task-map +
source-index from the refs the awaiting event carried and re-runs
``ingest_task_map_to_kanban``. A's owner gate then sees the approval and
the adoption completes. Idempotent: a completed/rejected adoption is
skipped, and ``ingest`` itself dedupes on the eval idempotency key.

Truth stays in ``events.jsonl`` and ``TaskStore``; this is a kernel-owned
re-drive, not a second control plane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.task_map import (
    load_source_index,
    load_task_map,
    resolve_artifact_file,
)

_ADOPTION_RESOLVED = {
    "replan.adoption.completed",
    "replan.adoption.owner_rejected",
}


def _payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def redrive_owner_approved_adoptions(
    state_dir: Path,
    *,
    project_root: Path,
    writer: EventWriter | None = None,
    actor: str = "zf-replan-redrive",
) -> list[str]:
    """Re-drive every ``awaiting_owner`` adoption the owner has approved.

    Returns the list of candidate ``task_map_ref`` values successfully
    re-driven this pass (empty when there is nothing to do).
    """
    state_dir = Path(state_dir)
    log = getattr(writer, "event_log", None) or EventLog(state_dir / "events.jsonl")
    if writer is None:
        writer = EventWriter(log)

    resolved: set[str] = set()
    approved_refs: set[str] = set()
    awaiting: dict[str, dict[str, Any]] = {}
    for event in log.read_all():
        etype = getattr(event, "type", "")
        payload = _payload(event)
        if etype in _ADOPTION_RESOLVED:
            ref = str(payload.get("task_map_ref") or "")
            if ref:
                resolved.add(ref)
        elif etype == "replan.owner_decision.approved":
            for key in ("candidate_task_map_ref", "eval_ref", "eval_id", "proposal_ref"):
                value = str(payload.get(key) or "")
                if value:
                    approved_refs.add(value)
        elif etype == "replan.adoption.awaiting_owner":
            ref = str(payload.get("task_map_ref") or "")
            if ref:
                awaiting[ref] = payload  # latest awaiting record wins

    redriven: list[str] = []
    for task_map_ref, payload in awaiting.items():
        if task_map_ref in resolved:
            continue
        replan_eval = payload.get("replan_eval")
        refs = payload.get("refs")
        if not isinstance(replan_eval, dict) or not isinstance(refs, dict):
            continue
        eval_id = str(replan_eval.get("idempotency_key") or payload.get("eval_id") or "")
        match_keys = {key for key in (task_map_ref, eval_id) if key}
        if not (approved_refs & match_keys):
            continue
        try:
            task_map = load_task_map(
                resolve_artifact_file(
                    refs["task_map_ref"],
                    project_root=Path(project_root),
                    state_dir=state_dir,
                )
            )
            source_index = None
            source_index_ref = str(refs.get("source_index_ref") or "")
            if source_index_ref:
                source_index = load_source_index(
                    resolve_artifact_file(
                        source_index_ref,
                        project_root=Path(project_root),
                        state_dir=state_dir,
                    )
                )
            result = ingest_task_map_to_kanban(
                state_dir,
                task_map,
                source_refs=refs,
                source_index=source_index,
                source_index_ref=source_index_ref,
                replan_eval=replan_eval,
                task_map_ref=refs["task_map_ref"],
                writer=writer,
                actor=actor,
            )
        except Exception as exc:  # artifact load / ingest is real I/O — fail soft
            writer.emit(
                "replan.adoption.redrive_failed",
                actor=actor,
                payload={"task_map_ref": task_map_ref, "error": str(exc)},
            )
            continue
        if result.passed:
            redriven.append(task_map_ref)
    return redriven
