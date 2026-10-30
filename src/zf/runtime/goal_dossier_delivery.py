"""Terminal Goal Dossier materialization and owner delivery request.

This service is a read-side closeout.  It never admits completion and never
mutates canonical task/run state.  A projection failure is retried on the next
tick while the already-admitted run terminal remains untouched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from zf.runtime.owner_visible_autodeliver import owner_visible_autodeliver_enabled
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt
from zf.runtime.goal_dossier import (
    build_goal_dossier,
    render_goal_dossier_markdown,
    write_goal_dossier_projection,
)
from zf.runtime.goal_dossier_consistency import (
    evaluate_goal_dossier_delivery_readiness,
)
from zf.runtime.run_scope import event_run_id, run_aliases


SCHEMA_VERSION = "goal-dossier-delivery.v1"
TERMINAL_EVENT_TYPES = frozenset({"run.goal.completed", "run.goal.blocked"})
OWNER_MESSAGE_REQUESTED = "owner.visible_message.requested"
DOSSIER_INCONSISTENT = "goal.dossier.inconsistent"


@dataclass(frozen=True)
class GoalDossierDeliveryResult:
    considered: int = 0
    materialized: int = 0
    requested: int = 0
    skipped: int = 0
    failed: int = 0
    changed: bool = False


def materialize_terminal_goal_deliveries(
    *,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    project_id: str = "",
    config: Any | None = None,
    project_root: Path | None = None,
) -> GoalDossierDeliveryResult:
    """Materialize every unhandled terminal and request owner delivery once."""

    state_dir = Path(state_dir)
    events = event_log.read_all()
    aliases = run_aliases(events)
    requested_ids = {
        str((event.payload or {}).get("message_id") or "")
        for event in events
        if event.type == OWNER_MESSAGE_REQUESTED
        and isinstance(event.payload, dict)
    }
    inconsistent_keys = {
        (
            str((event.payload or {}).get("terminal_event_id") or ""),
            str((event.payload or {}).get("dossier_source_fingerprint") or ""),
        )
        for event in events
        if event.type == DOSSIER_INCONSISTENT
        and isinstance(event.payload, dict)
    }
    considered = materialized = requested = skipped = failed = 0
    for index, terminal in enumerate(events):
        if terminal.type not in TERMINAL_EVENT_TYPES:
            continue
        considered += 1
        run_id = event_run_id(terminal, aliases=aliases)
        if not run_id:
            failed += 1
            _write_materialization_state(
                state_dir,
                run_id="unscoped",
                terminal=terminal,
                status="failed",
                reason="terminal event has no unambiguous run scope",
            )
            continue
        completed_projection = _completed_materialization(
            state_dir,
            run_id=run_id,
            terminal=terminal,
            requested_ids=requested_ids,
        )
        if completed_projection:
            skipped += 1
            continue
        terminal_events = events[:index + 1]
        try:
            dossier = build_goal_dossier(
                state_dir,
                run_id,
                events=terminal_events,
                project_id=project_id,
            )
            receipt_path: Path | None = None
            receipt: dict[str, Any] | None = None
            if terminal.type == "run.goal.completed":
                receipt = build_goal_completion_receipt(
                    terminal_events,
                    run_id=run_id,
                    generated_at=str(dossier.get("generated_at") or _now()),
                    project_id=project_id,
                )
            readiness = evaluate_goal_dossier_delivery_readiness(
                state_dir=state_dir,
                dossier=dossier,
                receipt=receipt,
                terminal=terminal,
            )
            dossier = dict(dossier)
            dossier["delivery_readiness"] = readiness
            dossier_path = write_goal_dossier_projection(state_dir, dossier)
            markdown_path = dossier_path.parent / "dossier.md"
            atomic_write_text(
                markdown_path,
                render_goal_dossier_markdown(dossier),
            )
            if receipt is not None:
                receipt_path = dossier_path.parent / "goal-completion-receipt.v1.json"
                atomic_write_text(
                    receipt_path,
                    json.dumps(redact_obj(receipt), ensure_ascii=False, indent=2)
                    + "\n",
                )
            materialized += 1
            consistency_issues = list(readiness.get("issues") or [])
            if (
                terminal.type == "run.goal.completed"
                and readiness.get("status") != "ready"
            ):
                failed += 1
                inconsistency_key = (
                    terminal.id,
                    str(dossier.get("source_fingerprint") or ""),
                )
                if inconsistency_key not in inconsistent_keys:
                    writer.emit(
                        DOSSIER_INCONSISTENT,
                        actor="zf-goal-dossier-delivery",
                        task_id=None,
                        causation_id=terminal.id,
                        correlation_id=terminal.correlation_id or run_id,
                        payload={
                            "schema_version": "goal-dossier-consistency.v1",
                            "workflow_run_id": run_id,
                            "goal_id": str(dossier.get("goal_id") or ""),
                            "terminal_event_id": terminal.id,
                            "terminal_event_type": terminal.type,
                            "dossier_source_fingerprint": str(
                                dossier.get("source_fingerprint") or ""
                            ),
                            "completion_receipt_fingerprint": str(
                                (receipt or {}).get("source_fingerprint") or ""
                            ),
                            "dossier_ref": dossier_path.relative_to(
                                state_dir
                            ).as_posix(),
                            "completion_receipt_ref": (
                                receipt_path.relative_to(state_dir).as_posix()
                                if receipt_path is not None
                                else ""
                            ),
                            "diagnostics": consistency_issues,
                            "reason": (
                                "terminal Dossier disagrees with immutable "
                                "completion truth"
                            ),
                        },
                    )
                    inconsistent_keys.add(inconsistency_key)
                _write_materialization_state(
                    state_dir,
                    run_id=run_id,
                    terminal=terminal,
                    status="inconsistent",
                    reason=json.dumps(
                        consistency_issues,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    dossier=dossier,
                    dossier_path=dossier_path,
                    receipt_path=receipt_path,
                )
                continue
            summary = owner_summary_from_goal_dossier(
                dossier,
                receipt=receipt,
            )
            from zf.runtime.owner_delivery_narrative import (
                prepare_owner_delivery_narrative_operation,
                write_owner_delivery_composite,
            )

            prepared_narrative = None
            if config is not None:
                prepared_narrative = prepare_owner_delivery_narrative_operation(
                    state_dir=state_dir,
                    project_root=Path(project_root or state_dir.parent),
                    config=config,
                    event_log=event_log,
                    writer=writer,
                    terminal=terminal,
                    dossier=dossier,
                    dossier_path=dossier_path,
                    receipt=receipt,
                    receipt_path=receipt_path,
                )
            narrative_reason = (
                "semantic_narrative_pending_factual_fallback_delivered"
                if prepared_narrative is not None
                else "semantic_narrative_unavailable_factual_fallback_delivered"
            )
            composite_path = write_owner_delivery_composite(
                state_dir=state_dir,
                run_id=run_id,
                dossier_ref=dossier_path.relative_to(state_dir).as_posix(),
                dossier_source_fingerprint=str(
                    dossier.get("source_fingerprint") or ""
                ),
                completion_receipt_ref=(
                    receipt_path.relative_to(state_dir).as_posix()
                    if receipt_path is not None
                    else ""
                ),
                terminal_event_id=terminal.id,
                narrative_status="degraded",
                narrative_reason=narrative_reason,
            )
            message_id = _message_id(
                run_id=run_id,
                terminal_event_id=terminal.id,
                source_fingerprint=str(dossier.get("source_fingerprint") or ""),
            )
            if message_id in requested_ids:
                skipped += 1
                _write_materialization_state(
                    state_dir,
                    run_id=run_id,
                    terminal=terminal,
                    status="delivered_requested",
                    message_id=message_id,
                    dossier=dossier,
                    dossier_path=dossier_path,
                    receipt_path=receipt_path,
                )
                continue
            payload = _owner_request_payload(
                project_id=project_id,
                run_id=run_id,
                terminal=terminal,
                dossier=dossier,
                dossier_path=dossier_path,
                markdown_path=markdown_path,
                receipt=receipt,
                receipt_path=receipt_path,
                summary=summary,
                message_id=message_id,
                narrative_status="degraded",
                narrative_reason=narrative_reason,
                composite_path=composite_path,
            )
            writer.emit(
                OWNER_MESSAGE_REQUESTED,
                actor="zf-goal-dossier-delivery",
                task_id=None,
                causation_id=terminal.id,
                correlation_id=terminal.correlation_id or run_id,
                payload=payload,
            )
            requested_ids.add(message_id)
            requested += 1
            _write_materialization_state(
                state_dir,
                run_id=run_id,
                terminal=terminal,
                status="delivered_requested",
                message_id=message_id,
                dossier=dossier,
                dossier_path=dossier_path,
                receipt_path=receipt_path,
            )
        except Exception as exc:
            failed += 1
            _write_materialization_state(
                state_dir,
                run_id=run_id,
                terminal=terminal,
                status="failed",
                reason=str(exc),
            )
    return GoalDossierDeliveryResult(
        considered=considered,
        materialized=materialized,
        requested=requested,
        skipped=skipped,
        failed=failed,
        changed=bool(requested or failed),
    )


def owner_summary_from_goal_dossier(
    dossier: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the single owner summary used by Web, Inbox and Feishu."""

    goal = dossier.get("goal") if isinstance(dossier.get("goal"), Mapping) else {}
    terminal = (
        dossier.get("terminal")
        if isinstance(dossier.get("terminal"), Mapping)
        else {}
    )
    state = dossier.get("state") if isinstance(dossier.get("state"), Mapping) else {}
    counts = state.get("task_counts") if isinstance(state.get("task_counts"), Mapping) else {}
    matrix = (
        dossier.get("claim_to_evidence")
        if isinstance(dossier.get("claim_to_evidence"), Mapping)
        else {}
    )
    claim_summary = (
        matrix.get("summary")
        if isinstance(matrix.get("summary"), Mapping)
        else {}
    )
    gaps = dossier.get("gaps") if isinstance(dossier.get("gaps"), list) else []
    status = str(terminal.get("status") or goal.get("status") or "unknown")
    completed = status == "completed"
    title = "目标已完成" if completed else "目标交付被阻塞"
    objective = str(goal.get("objective") or dossier.get("goal_id") or "本次任务")
    task_total = int(counts.get("total") or 0)
    task_terminal = int(counts.get("terminal") or 0)
    mandatory = int(claim_summary.get("mandatory_claims") or 0)
    closed = int(claim_summary.get("closed_claims") or 0)
    summary = (
        f"{objective}：{task_terminal}/{task_total} 个任务终态，"
        f"{closed}/{mandatory} 个必选目标声明闭合，"
        f"剩余 {len(gaps)} 个缺口。"
    )
    next_action = str(terminal.get("next_action") or "")
    if not completed and not next_action:
        next_action = "查看 Goal Dossier 的 gaps 与 incident history 后决定恢复或终止。"
    return redact_obj({
        "status": status,
        "title": title,
        "summary": summary,
        "objective": objective,
        "task_counts": {
            "total": task_total,
            "terminal": task_terminal,
            "open": int(counts.get("open") or 0),
        },
        "claim_counts": {
            "mandatory": mandatory,
            "closed": closed,
            "open_gaps": int(claim_summary.get("open_gaps") or len(gaps)),
        },
        "evidence_count": len(dossier.get("evidence_index") or []),
        "gap_count": len(gaps),
        "next_action": next_action,
        "completion_receipt_fingerprint": str(
            (receipt or {}).get("source_fingerprint") or ""
        ),
    })


def _owner_request_payload(
    *,
    project_id: str,
    run_id: str,
    terminal: ZfEvent,
    dossier: Mapping[str, Any],
    dossier_path: Path,
    markdown_path: Path,
    receipt: Mapping[str, Any] | None,
    receipt_path: Path | None,
    summary: Mapping[str, Any],
    message_id: str,
    narrative_status: str,
    narrative_reason: str,
    composite_path: Path,
) -> dict[str, Any]:
    blocked = terminal.type == "run.goal.blocked"
    deep_link = (
        "/?page=runs"
        f"&run_id={quote(run_id, safe='')}"
    )
    if project_id:
        deep_link += f"&project={quote(project_id, safe='')}"
    state_root = dossier_path.parents[3]
    return redact_obj({
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "message_kind": "run_terminal_delivery",
        "delivery_class": "run_terminal",
        "delivery_targets": ["web", *(["feishu"] if owner_visible_autodeliver_enabled() else [])],
        "notification_policy": "owner_terminal_delivery",
        "source": "goal-dossier",
        "handled_by": "goal-dossier",
        "project_id": project_id,
        "run_id": run_id,
        "goal_id": str(dossier.get("goal_id") or ""),
        "terminal_event_id": terminal.id,
        "terminal_event_type": terminal.type,
        "terminal_status": str(summary.get("status") or ""),
        "severity": "high" if blocked else "info",
        "title": str(summary.get("title") or ""),
        "summary": str(summary.get("summary") or ""),
        "objective": str(summary.get("objective") or ""),
        "task_counts": dict(summary.get("task_counts") or {}),
        "claim_counts": dict(summary.get("claim_counts") or {}),
        "evidence_count": int(summary.get("evidence_count") or 0),
        "gap_count": int(summary.get("gap_count") or 0),
        "next_action": str(summary.get("next_action") or ""),
        "human_action_required": blocked,
        "dossier_ref": dossier_path.relative_to(state_root).as_posix(),
        "dossier_markdown_ref": markdown_path.relative_to(state_root).as_posix(),
        "dossier_source_fingerprint": str(
            dossier.get("source_fingerprint") or ""
        ),
        "completion_receipt_ref": (
            receipt_path.relative_to(state_root).as_posix()
            if receipt_path is not None
            else ""
        ),
        "completion_receipt_fingerprint": str(
            (receipt or {}).get("source_fingerprint") or ""
        ),
        "narrative_status": narrative_status,
        "narrative_reason": narrative_reason,
        "owner_delivery_composite_ref": composite_path.relative_to(
            state_root
        ).as_posix(),
        "web_deep_link": deep_link,
    })


def _write_materialization_state(
    state_dir: Path,
    *,
    run_id: str,
    terminal: ZfEvent,
    status: str,
    reason: str = "",
    message_id: str = "",
    dossier: Mapping[str, Any] | None = None,
    dossier_path: Path | None = None,
    receipt_path: Path | None = None,
) -> None:
    safe_run_id = _safe_segment(run_id)
    path = (
        state_dir
        / "projections"
        / "goals"
        / safe_run_id
        / "delivery-materialization.v1.json"
    )
    root = state_dir.resolve()
    entry = {
        "status": status,
        "terminal_event_id": terminal.id,
        "terminal_event_type": terminal.type,
        "message_id": message_id,
        "dossier_ref": (
            dossier_path.resolve().relative_to(root).as_posix()
            if dossier_path is not None
            else ""
        ),
        "dossier_source_fingerprint": str(
            (dossier or {}).get("source_fingerprint") or ""
        ),
        "completion_receipt_ref": (
            receipt_path.resolve().relative_to(root).as_posix()
            if receipt_path is not None
            else ""
        ),
        "reason": reason,
        "updated_at": _now(),
    }
    previous = _read_json_object(path)
    deliveries = (
        dict(previous.get("deliveries") or {})
        if isinstance(previous.get("deliveries"), dict)
        else {}
    )
    deliveries[terminal.id] = entry
    payload = {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "run_id": run_id,
        **entry,
        "deliveries": deliveries,
    }
    atomic_write_text(
        path,
        json.dumps(redact_obj(payload), ensure_ascii=False, indent=2) + "\n",
    )


def _completed_materialization(
    state_dir: Path,
    *,
    run_id: str,
    terminal: ZfEvent,
    requested_ids: set[str],
) -> bool:
    directory = state_dir / "projections" / "goals" / _safe_segment(run_id)
    state_path = directory / "delivery-materialization.v1.json"
    dossier_path = directory / "goal-dossier.v1.json"
    receipt_path = directory / "goal-completion-receipt.v1.json"
    state = _read_json_object(state_path)
    if not state:
        return False
    deliveries = state.get("deliveries")
    entry = (
        deliveries.get(terminal.id)
        if isinstance(deliveries, dict)
        and isinstance(deliveries.get(terminal.id), dict)
        else state
    )
    message_id = str(entry.get("message_id") or "")
    return bool(
        entry.get("status") == "delivered_requested"
        and entry.get("terminal_event_id") == terminal.id
        and message_id
        and message_id in requested_ids
        and dossier_path.is_file()
        and (
            terminal.type != "run.goal.completed"
            or receipt_path.is_file()
        )
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _message_id(
    *,
    run_id: str,
    terminal_event_id: str,
    source_fingerprint: str,
) -> str:
    raw = "\0".join((run_id, terminal_event_id, source_fingerprint))
    return "goal-delivery-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _safe_segment(value: str) -> str:
    text = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in str(value or "unscoped")
    ).strip(".-")
    return text or "unscoped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "GoalDossierDeliveryResult",
    "SCHEMA_VERSION",
    "materialize_terminal_goal_deliveries",
    "owner_summary_from_goal_dossier",
]
