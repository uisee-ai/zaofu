"""Learning artifact projection for Loop closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_projection_common import dedupe, payload
from zf.runtime.loop_learning_promotion import (
    LOOP_LEARNING_PROMOTION_MATERIALIZED,
    LOOP_LEARNING_PROMOTION_REJECTED,
    LOOP_LEARNING_PROMOTION_REQUESTED,
)

LOOP_LEARNING_MATERIALIZED = "loop.learning.materialized"


def build_loop_learning(
    *,
    events: list[tuple[int, ZfEvent]],
    loops: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_loop = {str(item.get("loop_id") or ""): item for item in loops}
    by_candidate = {str(item.get("candidate_id") or ""): item for item in candidates}
    rows: dict[str, dict[str, Any]] = {}

    for verification in verifications:
        status = str(verification.get("status") or "")
        if status in {"", "pending", "running"}:
            continue
        candidate = by_candidate.get(str(verification.get("candidate_id") or "")) or {}
        loop = by_loop.get(str(verification.get("loop_id") or "")) or {}
        diagnosis = candidate.get("diagnosis") if isinstance(candidate.get("diagnosis"), dict) else {}
        row = _learning_row(verification, loop, candidate, diagnosis)
        rows[row["learning_id"]] = row

    for _seq, event in events:
        if event.type not in {
            LOOP_LEARNING_MATERIALIZED,
            LOOP_LEARNING_PROMOTION_REQUESTED,
            LOOP_LEARNING_PROMOTION_MATERIALIZED,
            LOOP_LEARNING_PROMOTION_REJECTED,
        }:
            continue
        data = payload(event)
        learning_id = str(data.get("learning_id") or event.id or "")
        if not learning_id:
            continue
        row = rows.setdefault(learning_id, _empty_row(learning_id))
        if event.type in {
            LOOP_LEARNING_PROMOTION_REQUESTED,
            LOOP_LEARNING_PROMOTION_MATERIALIZED,
            LOOP_LEARNING_PROMOTION_REJECTED,
        }:
            promotion_status = {
                LOOP_LEARNING_PROMOTION_REQUESTED: "requested",
                LOOP_LEARNING_PROMOTION_MATERIALIZED: "materialized",
                LOOP_LEARNING_PROMOTION_REJECTED: "rejected",
            }[event.type]
            row.update({
                "learning_id": learning_id,
                "loop_id": str(data.get("loop_id") or row.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or row.get("candidate_id") or ""),
                "action_id": str(data.get("action_id") or row.get("action_id") or ""),
                "verification_id": str(data.get("verification_id") or row.get("verification_id") or ""),
                "artifact_kind": str(data.get("artifact_kind") or data.get("kind") or row.get("artifact_kind") or ""),
                "artifact_ref": str(data.get("artifact_ref") or row.get("artifact_ref") or ""),
                "promotion_id": str(data.get("promotion_id") or row.get("promotion_id") or ""),
                "promotion_target": str(data.get("target") or row.get("promotion_target") or ""),
                "promotion_status": promotion_status,
                "promotion_ref": str(data.get("proposal_ref") or row.get("promotion_ref") or ""),
                "promotion_reason": str(data.get("reason") or row.get("promotion_reason") or ""),
                "promotion_event_id": event.id,
                "updated_at": event.ts,
            })
            _extend(row, "promotion_event_ids", [event.id])
            _extend(row, "event_ids", [event.id])
            _extend(row, "evidence_refs", _string_list(data.get("evidence_refs")))
            continue
        row.update({
            "learning_id": learning_id,
            "loop_id": str(data.get("loop_id") or row.get("loop_id") or ""),
            "candidate_id": str(data.get("candidate_id") or row.get("candidate_id") or ""),
            "action_id": str(data.get("action_id") or row.get("action_id") or ""),
            "verification_id": str(data.get("verification_id") or row.get("verification_id") or ""),
            "artifact_kind": str(data.get("artifact_kind") or data.get("kind") or row.get("artifact_kind") or ""),
            "artifact_ref": str(data.get("artifact_ref") or row.get("artifact_ref") or ""),
            "promotion_path": str(data.get("promotion_path") or row.get("promotion_path") or ""),
            "status": str(data.get("status") or "materialized"),
            "materialized_event_id": event.id,
            "updated_at": event.ts,
        })
        _extend(row, "event_ids", [event.id])
        _extend(row, "evidence_refs", _string_list(data.get("evidence_refs")))

    return sorted(rows.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def materialize_loop_learning_artifacts(
    *,
    state_dir: Path,
    learning: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write learning artifact JSON files under the configured state dir."""

    root = Path(state_dir) / "loop" / "learning"
    root.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for row in learning:
        if not row.get("learning_id"):
            continue
        path = root / f"{_safe_name(str(row['learning_id']))}.json"
        if path.exists():
            continue
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        copy = dict(row)
        copy["artifact_ref"] = str(path.relative_to(Path(state_dir)))
        written.append(copy)
    return written


def _learning_row(
    verification: dict[str, Any],
    loop: dict[str, Any],
    candidate: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    fix_layer = str(diagnosis.get("fix_layer") or candidate.get("fix_layer") or loop.get("fix_layer") or "unknown")
    status = str(verification.get("status") or "")
    artifact_kind = _artifact_kind(fix_layer, status)
    learning_id = f"learning:{artifact_kind}:{_stable_id(verification.get('verification_id'), fix_layer, status)}"
    evidence_refs = dedupe(
        _string_list(verification.get("evidence_refs"))
        + _string_list(diagnosis.get("evidence_refs"))
        + _string_list(loop.get("event_ids"))
    )
    return {
        "learning_id": learning_id,
        "loop_id": str(verification.get("loop_id") or loop.get("loop_id") or ""),
        "candidate_id": str(verification.get("candidate_id") or candidate.get("candidate_id") or ""),
        "action_id": str(verification.get("action_id") or ""),
        "verification_id": str(verification.get("verification_id") or ""),
        "artifact_kind": artifact_kind,
        "artifact_ref": f"loop-learning://{learning_id}",
        "status": "candidate",
        "fix_layer": fix_layer,
        "verification_status": status,
        "promotion_path": _promotion_path(artifact_kind),
        "promotion_status": "not_requested",
        "promotion_target": _promotion_target(artifact_kind),
        "promotion_id": "",
        "promotion_ref": "",
        "promotion_event_ids": [],
        "promotion_reason": "",
        "summary": _summary(artifact_kind, fix_layer, status),
        "event_ids": _string_list(verification.get("event_ids")),
        "evidence_refs": evidence_refs,
        "updated_at": str(verification.get("updated_at") or ""),
    }


def _artifact_kind(fix_layer: str, status: str) -> str:
    if fix_layer == "gate_evidence":
        return "eval_rule_proposal" if status != "passed" else "task_contract_rule"
    if fix_layer == "agent_runtime":
        return "runbook_note" if status == "passed" else "supervisor_insight"
    if fix_layer in {"workflow", "replan"}:
        return "workflow_patch_proposal"
    if fix_layer == "task_contract":
        return "task_contract_rule"
    if fix_layer == "autoresearch":
        return "supervisor_insight"
    return "regression_fixture"


def _promotion_path(kind: str) -> str:
    return {
        "eval_rule_proposal": "operator_review -> evaluator_rule",
        "workflow_patch_proposal": "operator_review -> backlog -> workflow_patch",
        "task_contract_rule": "operator_review -> task_contract_gate",
        "supervisor_insight": "operator_review -> supervisor_rule",
        "runbook_note": "operator_review -> runbook",
        "regression_fixture": "operator_review -> regression_suite",
    }.get(kind, "operator_review")


def _promotion_target(kind: str) -> str:
    return {
        "eval_rule_proposal": "eval_rule_proposal",
        "workflow_patch_proposal": "workflow_patch_proposal",
        "runbook_note": "runbook_note",
        "supervisor_insight": "runbook_note",
    }.get(kind, "backlog_candidate")


def _summary(kind: str, fix_layer: str, status: str) -> str:
    return f"Learn {kind} from {fix_layer} loop verification {status}"


def _empty_row(learning_id: str) -> dict[str, Any]:
    return {
        "learning_id": learning_id,
        "loop_id": "",
        "candidate_id": "",
        "action_id": "",
        "verification_id": "",
        "artifact_kind": "",
        "artifact_ref": "",
        "status": "candidate",
        "promotion_path": "",
        "promotion_status": "not_requested",
        "promotion_target": "",
        "promotion_id": "",
        "promotion_ref": "",
        "promotion_event_ids": [],
        "promotion_reason": "",
        "event_ids": [],
        "evidence_refs": [],
        "updated_at": "",
    }


def _extend(row: dict[str, Any], key: str, values: list[str]) -> None:
    row[key] = dedupe([*row.get(key, []), *values])


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
