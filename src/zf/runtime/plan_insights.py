"""Read-only plan insight projection for supervisor autoresearch loops."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


PLAN_INSIGHT_SCHEMA_VERSION = "plan-insight.v1"
PLAN_INSIGHT_PROJECTION_SCHEMA_VERSION = "plan-insights.projection.v1"


def build_plan_insight_projection(
    *,
    events: list[ZfEvent],
    plan_integrity: dict[str, Any],
    spine_review_hint: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold runtime facts into proposal-only plan insight candidates.

    The result is a rebuildable projection. It never mutates TaskStore or
    task-map truth; downstream consumers may turn selected rows into bounded
    attention / autoresearch requests.
    """

    now = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    rows.extend(_insights_from_plan_integrity(plan_integrity))
    rows.extend(_insights_from_spine_review(spine_review_hint or {}))
    rows.extend(_insights_from_replan_eval(events))
    rows = _dedupe(rows)[:100]
    by_type: Counter[str] = Counter()
    by_route: Counter[str] = Counter()
    for row in rows:
        by_type[str(row.get("insight_type") or "unknown")] += 1
        by_route[str(row.get("recommended_route") or "unknown")] += 1
    return redact_obj({
        "schema_version": PLAN_INSIGHT_PROJECTION_SCHEMA_VERSION,
        "kind": PLAN_INSIGHT_SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": now.isoformat(),
        "summary": {
            "total": len(rows),
            "high_confidence": sum(1 for row in rows if str(row.get("confidence") or "") == "high"),
            "proposal_only": True,
            "by_type": dict(sorted(by_type.items())),
            "by_route": dict(sorted(by_route.items())),
        },
        "items": rows,
    })


def plan_insight_attention_items(plan_insights: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for insight in plan_insights.get("items") or []:
        if not isinstance(insight, dict):
            continue
        severity = _attention_severity(insight)
        if severity == "info":
            continue
        insight_id = str(insight.get("insight_id") or "")
        fingerprint = str(insight.get("fingerprint") or f"plan_insight:{insight_id}")
        out.append(redact_obj({
            "attention_id": f"attn-{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:12]}",
            "fingerprint": fingerprint,
            "source": "plan_insight",
            "severity": severity,
            "title": str(insight.get("summary") or "Plan insight requires review"),
            "summary": str(insight.get("expected_value") or insight.get("summary") or ""),
            "task_id": str(insight.get("task_id") or ""),
            "feature_id": str(insight.get("feature_id") or ""),
            "status": "open",
            "suggested_route": "autoresearch_trigger"
            if str(insight.get("recommended_route") or "") == "research_probe"
            else "plan_revision",
            "suggested_action": {
                "kind": str(insight.get("recommended_route") or "owner_question"),
                "insight_id": insight_id,
                "source_insight_ref": str(insight.get("insight_ref") or ""),
            },
            "source_refs": insight.get("source_refs") if isinstance(insight.get("source_refs"), list) else [],
            "source_event_ids": [
                str(ref.get("event_id") or "")
                for ref in (insight.get("source_refs") if isinstance(insight.get("source_refs"), list) else [])
                if isinstance(ref, dict) and str(ref.get("event_id") or "").strip()
            ],
            "evidence_paths": _evidence_paths(insight),
            "insight_type": str(insight.get("insight_type") or ""),
            "recommended_route": str(insight.get("recommended_route") or ""),
            "source_insight_ref": str(insight.get("insight_ref") or ""),
            "expected_output": _expected_output(insight),
            "confidence": str(insight.get("confidence") or ""),
        }))
    return out


def _insights_from_plan_integrity(plan_integrity: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in plan_integrity.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "")
        task_id = str(finding.get("task_id") or "")
        source_ref = str(finding.get("source_ref") or "")
        insight_type = "verification_gap" if "acceptance" in kind else "plan_gap"
        route = "correct_task" if insight_type == "verification_gap" else "research_probe"
        rows.append(_row(
            seed=f"plan-integrity:{kind}:{task_id}:{source_ref}",
            insight_type=insight_type,
            summary=str(finding.get("title") or "Plan integrity finding"),
            confidence="medium" if str(finding.get("severity") or "") == "warn" else "low",
            recommended_route=route,
            task_id=task_id,
            source_refs=[{"kind": "plan_integrity", "ref": source_ref, "finding_id": str(finding.get("finding_id") or "")}],
            expected_value=str(finding.get("summary") or "Improve task contract and execution evidence quality"),
        ))
    return rows


def _insights_from_spine_review(spine_review_hint: dict[str, Any]) -> list[dict[str, Any]]:
    if not spine_review_hint or spine_review_hint.get("empty"):
        return []
    verdict = str(spine_review_hint.get("verdict") or "")
    if verdict not in {"correct_task", "split_or_replan"}:
        return []
    review_id = str(spine_review_hint.get("review_id") or "")
    route = "correct_task" if verdict == "correct_task" else "replan_eval"
    return [_row(
        seed=f"spine-review:{review_id}:{verdict}",
        insight_type="better_route" if verdict == "split_or_replan" else "plan_gap",
        summary=str(spine_review_hint.get("summary") or f"Project spine review suggests {verdict}"),
        confidence="high",
        recommended_route=route,
        source_refs=[{"kind": "spine_review", "ref": review_id}],
        expected_value="Use project spine review evidence before continuing the current plan.",
    )]


def _insights_from_replan_eval(events: list[ZfEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events[-500:]:
        if event.type != "replan.contract_eval.completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        decision = str(payload.get("decision") or "")
        if decision not in {"revise", "reject", "escalate"}:
            continue
        failed = payload.get("failed_checks") if isinstance(payload.get("failed_checks"), list) else []
        rows.append(_row(
            seed=f"replan-eval:{payload.get('eval_id') or event.id}:{decision}",
            insight_type="execution_drift" if decision == "revise" else "plan_gap",
            summary=f"Replan contract eval returned {decision}",
            confidence="high",
            recommended_route="owner_question" if decision == "escalate" else "correct_task",
            task_id=str(payload.get("task_id") or event.task_id or ""),
            task_map_ref=str(payload.get("new_task_map_ref") or payload.get("candidate_task_map_ref") or ""),
            source_refs=[{"kind": "event", "event_id": event.id, "type": event.type}],
            expected_value=", ".join(str(item) for item in failed if str(item).strip()) or "Review failed replan contract checks.",
        ))
    return rows


def _row(
    *,
    seed: str,
    insight_type: str,
    summary: str,
    confidence: str,
    recommended_route: str,
    task_id: str = "",
    feature_id: str = "",
    task_map_ref: str = "",
    source_refs: list[dict[str, Any]] | None = None,
    expected_value: str = "",
) -> dict[str, Any]:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return {
        "kind": PLAN_INSIGHT_SCHEMA_VERSION,
        "insight_id": f"pins-{digest}",
        "insight_ref": f"projection:supervisor/plan-insights.json#pins-{digest}",
        "fingerprint": f"plan_insight:{digest}",
        "insight_type": insight_type,
        "summary": summary,
        "feature_id": feature_id,
        "task_id": task_id,
        "task_map_ref": task_map_ref,
        "source_refs": source_refs or [],
        "recommended_route": recommended_route,
        "confidence": confidence,
        "expected_value": expected_value,
        "policy": {
            "proposal_only": True,
            "owner_approval_required_for_apply": True,
        },
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("fingerprint") or row.get("insight_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _attention_severity(insight: dict[str, Any]) -> str:
    confidence = str(insight.get("confidence") or "")
    route = str(insight.get("recommended_route") or "")
    if confidence == "high" or route in {"replan_eval", "safe_pause"}:
        return "high"
    if confidence == "medium":
        return "medium"
    return "info"


def _evidence_paths(insight: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for ref in insight.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        value = str(ref.get("ref") or "")
        if value and not value.startswith(("event:", "projection:")):
            paths.append(value)
    task_map_ref = str(insight.get("task_map_ref") or "")
    if task_map_ref:
        paths.append(task_map_ref)
    return paths


def _expected_output(insight: dict[str, Any]) -> list[str]:
    route = str(insight.get("recommended_route") or "")
    if route == "research_probe":
        return ["research_probe_report", "evidence_refs", "replan_or_continue_recommendation"]
    if route == "replan_eval":
        return ["replan_proposal", "candidate_task_map_ref", "contract_eval_request"]
    if route == "correct_task":
        return ["task_contract_patch_proposal", "verification_evidence_mapping"]
    return ["operator_visible_summary", "recommended_next_action"]


__all__ = [
    "PLAN_INSIGHT_PROJECTION_SCHEMA_VERSION",
    "PLAN_INSIGHT_SCHEMA_VERSION",
    "build_plan_insight_projection",
    "plan_insight_attention_items",
]
