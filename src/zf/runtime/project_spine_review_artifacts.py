"""Project Spine Review artifacts, proposals, and Web insight projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.config.project_context import ProjectContext
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.project_spine_review_common import (
    ARTIFACT_EVENT,
    INSIGHT_SCHEMA_VERSION,
    PROPOSAL_EVENT,
    PROPOSAL_SCHEMA_VERSION,
    REFLECTION_SCHEMA_VERSION,
    SpineReviewError,
    artifact_ref,
    read_events,
)


def render_spine_review_markdown(review: dict[str, Any]) -> str:
    lines = [
        f"# Project Spine Review: {review.get('project_name') or review.get('project_id')}",
        "",
        f"- schema: `{review.get('schema_version')}`",
        f"- review_id: `{review.get('review_id')}`",
        f"- project_id: `{review.get('project_id')}`",
        f"- project_root: `{review.get('project_root')}`",
        f"- state_dir: `{review.get('state_dir')}`",
        f"- reviewed_at: `{review.get('reviewed_at')}`",
        f"- cli_identity: `{review.get('cli_identity')}`",
        f"- verdict: `{review.get('verdict')}`",
        f"- confidence: `{review.get('confidence')}`",
        "",
        "## Design Spine",
    ]
    lines.extend(_status_block(review.get("design_spine")))
    lines.append("")
    lines.append("## Delivery Spine")
    lines.extend(_status_block(review.get("delivery_spine")))
    lines.append("")
    lines.append("## Runtime Spine")
    lines.extend(_status_block(review.get("runtime_spine")))
    lines.append("")
    lines.append("## Reflection")
    reflection = review.get("reflection") if isinstance(review.get("reflection"), dict) else {}
    lines.append(f"- root_cause_hypothesis: {reflection.get('root_cause_hypothesis') or '-'}")
    lines.append(f"- better_solution: {reflection.get('better_solution') or '-'}")
    lines.append(f"- verify: {reflection.get('verify') or '-'}")
    lines.append(f"- rollback_condition: {reflection.get('rollback_condition') or '-'}")
    lines.append(f"- history_judgment: {reflection.get('history_judgment') or '-'}")
    previous = (
        reflection.get("previous_reflections")
        if isinstance(reflection.get("previous_reflections"), list)
        else []
    )
    if previous:
        lines.append("- previous_reflections:")
        for item in previous:
            if not isinstance(item, dict):
                continue
            ref = item.get("reflection_ref") if isinstance(item.get("reflection_ref"), dict) else {}
            lines.append(
                "  - "
                f"{item.get('review_id')} "
                f"verdict={item.get('verdict') or '-'} "
                f"event={item.get('artifact_event_id') or '-'} "
                f"ref={ref.get('path') or '-'}"
            )
    alternatives = reflection.get("alternatives") if isinstance(reflection.get("alternatives"), list) else []
    if alternatives:
        lines.append("- alternatives:")
        for item in alternatives:
            if not isinstance(item, dict):
                continue
            lines.append(
                "  - "
                f"{item.get('option')}: {item.get('decision')} — {item.get('reason')}"
            )
    lines.append("")
    lines.append("## Corrective Actions")
    actions = review.get("corrective_actions")
    if isinstance(actions, list) and actions:
        for item in actions:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"`{item.get('action_id')}` {item.get('priority')} "
                f"{item.get('kind')} — {item.get('target')}"
            )
            if item.get("proposal"):
                lines.append(f"  - proposal: {item.get('proposal')}")
            if item.get("verify"):
                lines.append(f"  - verify: {item.get('verify')}")
            refs = item.get("evidence_refs")
            if refs:
                lines.append(f"  - evidence_refs: {', '.join(str(v) for v in refs)}")
    else:
        lines.append("- no corrective actions")
    lines.append("")
    lines.append("## Drift")
    for item in review.get("drift") or []:
        lines.append(f"- `{item}`")
    return "\n".join(lines).rstrip() + "\n"


def write_spine_review_artifact(
    context: ProjectContext,
    review: dict[str, Any],
) -> dict[str, Any]:
    """Persist review artifacts and append an artifact event."""
    state_dir = context.state_dir
    review_id = str(review.get("review_id") or "")
    if not review_id:
        raise SpineReviewError("review_id is required")
    artifact_dir = state_dir / "artifacts" / "reviews" / "spine" / review_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "report.md"
    review_path = artifact_dir / "review.json"
    reflection_path = artifact_dir / "reflection.json"
    reflection = dict(review.get("reflection") or {})
    reflection.setdefault("schema_version", REFLECTION_SCHEMA_VERSION)
    reflection.setdefault("review_id", review_id)
    atomic_write_text(report_path, render_spine_review_markdown(review))
    atomic_write_text(
        review_path,
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        reflection_path,
        json.dumps(reflection, ensure_ascii=False, indent=2) + "\n",
    )
    artifacts = [
        artifact_ref("report", report_path, state_dir=state_dir),
        artifact_ref("review_json", review_path, state_dir=state_dir),
        artifact_ref("reflection", reflection_path, state_dir=state_dir),
    ]
    payload = {
        "schema_version": "spine_review.artifact.created.v1",
        "review_id": review_id,
        "project_id": str(review.get("project_id") or ""),
        "verdict": str(review.get("verdict") or ""),
        "drift": list(review.get("drift") or []),
        "artifacts": artifacts,
    }
    event = EventWriter(
        event_log_from_project(state_dir, config=context.config),
    ).emit(
        ARTIFACT_EVENT,
        actor="zf-project-spine-review",
        payload=payload,
    )
    return {
        "review_id": review_id,
        "event_id": event.id,
        "artifact_dir": str(artifact_dir),
        "artifacts": artifacts,
    }


def create_spine_review_proposal(
    context: ProjectContext,
    *,
    review_id: str,
    action: str,
) -> dict[str, Any]:
    review = load_spine_review_artifact(context.state_dir, review_id)
    actions = review.get("corrective_actions")
    if not isinstance(actions, list) or not actions:
        raise SpineReviewError(f"review {review_id!r} has no corrective actions")
    selected = _select_action(actions, action)
    evidence_refs = selected.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise SpineReviewError(
            f"corrective action {selected.get('action_id')!r} lacks evidence_refs"
        )
    payload = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "review_id": review_id,
        "project_id": str(review.get("project_id") or ""),
        "action_id": str(selected.get("action_id") or ""),
        "kind": str(selected.get("kind") or ""),
        "priority": str(selected.get("priority") or ""),
        "target": str(selected.get("target") or ""),
        "proposal": str(selected.get("proposal") or ""),
        "verify": str(selected.get("verify") or ""),
        "evidence_refs": evidence_refs,
        "status": "pending",
    }
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    event = writer.emit(
        PROPOSAL_EVENT,
        actor="zf-project-spine-review",
        payload=payload,
    )
    if payload["kind"] == "split_or_replan":
        writer.emit(
            "replan.proposal.created",
            actor="zf-project-spine-review",
            causation_id=event.id,
            payload={
                "schema_version": "replan-proposal.v1",
                "artifact_id": f"spine-replan-{review_id}-{payload['action_id']}",
                "source": PROPOSAL_EVENT,
                "proposal_ref": f"spine_review:{review_id}:{payload['action_id']}",
                "review_id": review_id,
                "project_id": payload["project_id"],
                "reason": payload["proposal"],
                "risk": payload["priority"],
                "required_gate": "replan-contract-eval.v1",
                "evidence_refs": evidence_refs,
                "apply_policy": "proposal_only",
                "requires_candidate_task_map": True,
            },
        )
    return {
        "ok": True,
        "event_id": event.id,
        "proposal": payload,
    }


def load_spine_review_artifact(state_dir: Path, review_id: str) -> dict[str, Any]:
    path = Path(state_dir) / "artifacts" / "reviews" / "spine" / review_id / "review.json"
    if not path.exists():
        raise SpineReviewError(f"review artifact not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SpineReviewError(f"review artifact is not an object: {path}")
    return data


def project_spine_review_insight(
    state_dir: Path,
    *,
    project_id: str = "",
) -> dict[str, Any]:
    """Return latest persisted spine review as a compact Web insight."""
    latest = _latest_artifact_event(Path(state_dir), project_id=project_id)
    if latest is None:
        return {
            "schema_version": INSIGHT_SCHEMA_VERSION,
            "status": "empty",
            "project_id": project_id,
            "last_review": None,
            "verdict": "",
            "top_findings": [],
            "corrective_actions": [],
        }
    payload = latest.payload if isinstance(latest.payload, dict) else {}
    review_id = str(payload.get("review_id") or "")
    review: dict[str, Any] = {}
    if review_id:
        try:
            review = load_spine_review_artifact(Path(state_dir), review_id)
        except Exception:
            review = {}
    design = review.get("design_spine") if isinstance(review.get("design_spine"), dict) else {}
    delivery = review.get("delivery_spine") if isinstance(review.get("delivery_spine"), dict) else {}
    runtime = review.get("runtime_spine") if isinstance(review.get("runtime_spine"), dict) else {}
    reflection = review.get("reflection") if isinstance(review.get("reflection"), dict) else {}
    return {
        "schema_version": INSIGHT_SCHEMA_VERSION,
        "status": "ready",
        "project_id": str(payload.get("project_id") or project_id),
        "review_id": review_id,
        "last_reviewed_at": str(review.get("reviewed_at") or latest.ts),
        "artifact_event_id": latest.id,
        "verdict": str(review.get("verdict") or payload.get("verdict") or ""),
        "confidence": str(review.get("confidence") or ""),
        "design_status": str(design.get("status") or ""),
        "delivery_status": str(delivery.get("status") or ""),
        "runtime_status": str(runtime.get("status") or ""),
        "better_solution": str(reflection.get("better_solution") or ""),
        "top_findings": _top_findings(design, delivery, runtime),
        "corrective_actions": list(review.get("corrective_actions") or [])[:3],
    }


def proposal_events(state_dir: Path) -> list[dict[str, Any]]:
    events = read_events(Path(state_dir), config=None)
    rows = []
    for event in events:
        if event.type != PROPOSAL_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        rows.append({
            "event_id": event.id,
            "ts": event.ts,
            **payload,
        })
    return rows


def _status_block(value: object) -> list[str]:
    data = value if isinstance(value, dict) else {}
    lines = [f"- status: `{data.get('status') or 'unknown'}`"]
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    for finding in findings:
        lines.append(f"- finding: {finding}")
    for key in ("canonical_refs", "partial_tasks", "missing_evidence"):
        values = data.get(key)
        if isinstance(values, list) and values:
            compact = ", ".join(str(item) for item in values[:8])
            lines.append(f"- {key}: {compact}")
    return lines


def _latest_artifact_event(state_dir: Path, *, project_id: str) -> ZfEvent | None:
    events = read_events(state_dir, config=None)
    matches = []
    for event in events:
        if event.type != ARTIFACT_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if project_id and str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        matches.append(event)
    return matches[-1] if matches else None


def _top_findings(*sections: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for section in sections:
        values = section.get("findings") if isinstance(section.get("findings"), list) else []
        for value in values:
            if value not in findings:
                findings.append(str(value))
    return findings[:5]


def _select_action(actions: list[Any], action: str) -> dict[str, Any]:
    raw = str(action or "").strip()
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(actions) and isinstance(actions[index], dict):
            return actions[index]
    for item in actions:
        if not isinstance(item, dict):
            continue
        if str(item.get("action_id") or "") == raw:
            return item
    raise SpineReviewError(f"corrective action not found: {action}")
