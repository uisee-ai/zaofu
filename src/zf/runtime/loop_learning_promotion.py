"""Token-gated promotion bridge for Loop learning artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.delivery_projection_common import dedupe

LOOP_LEARNING_PROMOTION_REQUESTED = "loop.learning.promotion.requested"
LOOP_LEARNING_PROMOTION_MATERIALIZED = "loop.learning.promotion.materialized"
LOOP_LEARNING_PROMOTION_REJECTED = "loop.learning.promotion.rejected"

PROMOTION_TARGETS = {
    "backlog_candidate",
    "eval_rule_proposal",
    "workflow_patch_proposal",
    "runbook_note",
}


@dataclass(frozen=True)
class LoopLearningPromotionRequest:
    loop_id: str
    learning_id: str
    target: str = ""
    idempotency_key: str = ""
    project_id: str = ""
    source: str = "web"


def request_loop_learning_promotion(
    *,
    projection: dict[str, Any],
    writer: EventWriter,
    state_dir: Path,
    request: LoopLearningPromotionRequest,
) -> dict[str, Any]:
    """Append promotion events and materialize a runtime proposal artifact."""

    learning = _find_learning(projection, request.learning_id, request.loop_id)
    if learning is None:
        return {
            "ok": False,
            "status": "not_found",
            "reason": "loop learning artifact not found",
            "_status_code": 404,
        }
    loop_id = str(learning.get("loop_id") or request.loop_id or "")
    target = _promotion_target(request.target, str(learning.get("artifact_kind") or ""))
    promotion_id = f"lp-{_stable_id(request.project_id, request.learning_id, target, request.idempotency_key)}"
    task_ids = _task_ids_for_learning(projection, learning)
    evidence_refs = dedupe(_string_list(learning.get("evidence_refs")) + _string_list(learning.get("event_ids")))

    requested = writer.append(ZfEvent(
        type=LOOP_LEARNING_PROMOTION_REQUESTED,
        actor="web",
        task_id=task_ids[0] if task_ids else None,
        correlation_id=f"loop-learning-promotion:{promotion_id}",
        payload={
            "promotion_id": promotion_id,
            "learning_id": learning.get("learning_id") or request.learning_id,
            "loop_id": loop_id,
            "candidate_id": learning.get("candidate_id") or "",
            "action_id": learning.get("action_id") or "",
            "verification_id": learning.get("verification_id") or "",
            "artifact_kind": learning.get("artifact_kind") or "",
            "artifact_ref": learning.get("artifact_ref") or "",
            "target": target,
            "project_id": request.project_id,
            "idempotency_key": request.idempotency_key,
            "task_ids": task_ids,
            "evidence_refs": evidence_refs,
            "source": request.source,
        },
    ))

    if target not in PROMOTION_TARGETS:
        rejected = writer.append(ZfEvent(
            type=LOOP_LEARNING_PROMOTION_REJECTED,
            actor="zf-loop",
            task_id=task_ids[0] if task_ids else None,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "promotion_id": promotion_id,
                "learning_id": request.learning_id,
                "loop_id": loop_id,
                "target": target,
                "reason": f"unsupported promotion target: {target}",
                "evidence_refs": evidence_refs,
            },
        ))
        return _response(
            promotion_id,
            requested,
            rejected,
            status="rejected",
            target=target,
            reason="unsupported promotion target",
        )

    proposal = _proposal_payload(
        promotion_id=promotion_id,
        target=target,
        learning=learning,
        task_ids=task_ids,
        evidence_refs=evidence_refs,
        project_id=request.project_id,
    )
    proposal_ref = _write_proposal(state_dir, promotion_id, proposal)
    materialized = writer.append(ZfEvent(
        type=LOOP_LEARNING_PROMOTION_MATERIALIZED,
        actor="zf-loop",
        task_id=task_ids[0] if task_ids else None,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "promotion_id": promotion_id,
            "learning_id": learning.get("learning_id") or request.learning_id,
            "loop_id": loop_id,
            "candidate_id": learning.get("candidate_id") or "",
            "action_id": learning.get("action_id") or "",
            "verification_id": learning.get("verification_id") or "",
            "artifact_kind": learning.get("artifact_kind") or "",
            "artifact_ref": learning.get("artifact_ref") or "",
            "target": target,
            "proposal_ref": proposal_ref,
            "status": "materialized",
            "evidence_refs": evidence_refs,
        },
    ))
    return _response(
        promotion_id,
        requested,
        materialized,
        status="materialized",
        target=target,
        proposal_ref=proposal_ref,
    )


def _find_learning(projection: dict[str, Any], learning_id: str, loop_id: str) -> dict[str, Any] | None:
    for item in projection.get("learning") or []:
        if not isinstance(item, dict):
            continue
        if learning_id and item.get("learning_id") != learning_id:
            continue
        if loop_id and item.get("loop_id") != loop_id:
            continue
        return item
    return None


def _promotion_target(requested: str, artifact_kind: str) -> str:
    requested = requested.strip()
    if requested:
        return requested
    if artifact_kind in {"eval_rule_proposal"}:
        return "eval_rule_proposal"
    if artifact_kind in {"workflow_patch_proposal"}:
        return "workflow_patch_proposal"
    if artifact_kind in {"runbook_note", "supervisor_insight"}:
        return "runbook_note"
    return "backlog_candidate"


def _task_ids_for_learning(projection: dict[str, Any], learning: dict[str, Any]) -> list[str]:
    loop_id = str(learning.get("loop_id") or "")
    candidate_id = str(learning.get("candidate_id") or "")
    task_ids: list[str] = []
    for loop in projection.get("loops") or []:
        if isinstance(loop, dict) and loop.get("loop_id") == loop_id:
            task_ids.extend(_string_list(loop.get("task_ids")))
    for candidate in projection.get("candidates") or []:
        if isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id:
            task_ids.extend(_string_list(candidate.get("task_ids")))
    return dedupe(task_ids)


def _proposal_payload(
    *,
    promotion_id: str,
    target: str,
    learning: dict[str, Any],
    task_ids: list[str],
    evidence_refs: list[str],
    project_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "loop-learning-promotion.v1",
        "promotion_id": promotion_id,
        "target": target,
        "project_id": project_id,
        "source": {
            "learning_id": learning.get("learning_id") or "",
            "loop_id": learning.get("loop_id") or "",
            "candidate_id": learning.get("candidate_id") or "",
            "action_id": learning.get("action_id") or "",
            "verification_id": learning.get("verification_id") or "",
            "artifact_kind": learning.get("artifact_kind") or "",
            "artifact_ref": learning.get("artifact_ref") or "",
            "fix_layer": learning.get("fix_layer") or "",
        },
        "proposal": {
            "title": _title(target, learning),
            "summary": learning.get("summary") or "",
            "promotion_path": learning.get("promotion_path") or "",
            "task_ids": task_ids,
            "evidence_refs": evidence_refs,
        },
        "promotion_policy": {
            "proposal_only": True,
            "requires_operator_review": True,
            "writes_canonical_truth": False,
        },
    }


def _title(target: str, learning: dict[str, Any]) -> str:
    kind = str(learning.get("artifact_kind") or "learning")
    fix_layer = str(learning.get("fix_layer") or "unknown")
    return f"Promote {kind} to {target} for {fix_layer}"


def _write_proposal(state_dir: Path, promotion_id: str, proposal: dict[str, Any]) -> str:
    root = Path(state_dir) / "loop" / "promotions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe_name(promotion_id)}.json"
    path.write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path.relative_to(Path(state_dir)))


def _response(
    promotion_id: str,
    requested: ZfEvent,
    terminal: ZfEvent,
    *,
    status: str,
    target: str,
    reason: str = "",
    proposal_ref: str = "",
) -> dict[str, Any]:
    out = {
        "ok": status != "rejected",
        "status": status,
        "promotion_id": promotion_id,
        "target": target,
        "request_event_id": requested.id,
        "terminal_event_id": terminal.id,
        "terminal_event_type": terminal.type,
        "_status_code": 202 if status != "rejected" else 409,
    }
    if proposal_ref:
        out["proposal_ref"] = proposal_ref
    if reason:
        out["reason"] = reason
    return out


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
