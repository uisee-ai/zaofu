"""Autoresearch cycle projection helpers for delivery-trace.v1."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

EventSlice = Sequence[tuple[int, ZfEvent]]

_AUTORESEARCH_PREFIX = "autoresearch."
_TERMINAL_STATUS = {"completed", "failed", "rejected", "skipped"}


def build_autoresearch_cycles(
    *,
    events: EventSlice,
    feature_id: str,
    task_ids: set[str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, ZfEvent]]] = {}
    for seq, event in events:
        if not event.type.startswith(_AUTORESEARCH_PREFIX):
            continue
        payload = _payload(event)
        if not _event_linked(event, payload, feature_id, task_ids):
            continue
        groups.setdefault(_cycle_key(seq, event, payload), []).append((seq, event))

    out: list[dict[str, Any]] = []
    for key, group in groups.items():
        payloads = [_payload(event) for _seq, event in group]
        latest_payload = payloads[-1] if payloads else {}
        status = _status(group)
        scores = _score_fields(payloads)
        review_gate = any("review_gate" in event.type for _seq, event in group)
        out.append({
            "cycle_id": f"autoresearch:{key}",
            "kind": "autoresearch_review_gate" if review_gate else "autoresearch",
            "status": status,
            "trigger": _first_non_empty(
                payloads,
                ("trigger", "failure_class", "reason", "source", "source_event_type"),
                fallback=group[0][1].type,
            ),
            "policy": _first_non_empty(
                payloads,
                ("policy", "autoresearch_policy", "output_mode", "mode"),
                fallback="proposal_only",
            ),
            "deposition": _first_non_empty(
                payloads,
                ("deposition", "adoption_mode", "effect", "result_mode"),
                fallback=str(latest_payload.get("policy") or "proposal_only"),
            ),
            "sandbox": _sandbox_label(payloads),
            "task_ids": _cycle_task_ids(group, task_ids),
            "started_at": _first_ts(group),
            "ended_at": _last_ts(group) if status in _TERMINAL_STATUS else "",
            "score_delta": scores["score_delta"],
            "baseline_score": scores["baseline_score"],
            "candidate_score": scores["candidate_score"],
            "evidence_refs": _evidence_refs(group),
            "review_gate": _review_gate_fields(payloads) if review_gate else {},
            "events": _compact_events(group),
        })
    return redact_obj(sorted(out, key=lambda item: str(item.get("started_at") or ""))[-80:])


def _event_linked(
    event: ZfEvent,
    payload: dict[str, Any],
    feature_id: str,
    task_ids: set[str],
) -> bool:
    task_id = str(event.task_id or payload.get("task_id") or "")
    if task_id and task_id in task_ids:
        return True
    for raw in payload.get("task_ids") or payload.get("completed_task_ids") or []:
        if str(raw) in task_ids:
            return True
    payload_feature = str(payload.get("feature_id") or payload.get("pdd_id") or "")
    if feature_id and payload_feature == feature_id:
        return True
    return not feature_id and not task_ids


def _cycle_key(seq: int, event: ZfEvent, payload: dict[str, Any]) -> str:
    for value in (
        event.correlation_id,
        payload.get("correlation_id"),
        payload.get("loop_id"),
        payload.get("run_id"),
        payload.get("invocation_id"),
        payload.get("autoresearch_run_id"),
        payload.get("request_id"),
        payload.get("review_gate_id"),
        payload.get("repair_id"),
        payload.get("failure_fingerprint"),
        event.task_id,
        payload.get("task_id"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return f"seq-{seq}"


def _status(group: list[tuple[int, ZfEvent]]) -> str:
    status = "requested"
    for _seq, event in group:
        if event.type.endswith(".failed"):
            status = "failed"
        elif event.type.endswith(".rejected"):
            status = "rejected"
        elif event.type.endswith(".prepared"):
            status = "completed"
        elif event.type.endswith(".completed"):
            status = "completed"
        elif event.type.endswith(".skipped") and status not in {"failed", "rejected", "completed"}:
            status = "skipped"
        elif event.type.endswith(".started") and status in {"requested", "accepted"}:
            status = "running"
        elif event.type.endswith(".accepted") and status == "requested":
            status = "accepted"
    return status


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _clean_str_list(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _cycle_task_ids(group: list[tuple[int, ZfEvent]], known_task_ids: set[str]) -> list[str]:
    values: list[str] = []
    for _seq, event in group:
        payload = _payload(event)
        values.extend([event.task_id, payload.get("task_id")])
        values.extend(payload.get("task_ids") or [])
    cleaned = _clean_str_list(values)
    return [task_id for task_id in cleaned if not known_task_ids or task_id in known_task_ids]


def _compact_events(group: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    return [{
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "task_id": str(event.task_id or _payload(event).get("task_id") or ""),
        "ts": event.ts,
        "status": _status_from_event(event),
    } for seq, event in group][-40:]


def _evidence_refs(group: list[tuple[int, ZfEvent]]) -> list[str]:
    refs: list[Any] = []
    for _seq, event in group:
        payload = _payload(event)
        refs.append(event.id)
        refs.append(payload.get("artifact_ref"))
        refs.append(payload.get("candidate_ref"))
        refs.append(payload.get("summary_ref"))
        refs.append(payload.get("closeout_artifact"))
        refs.append(payload.get("report_path"))
        refs.append(payload.get("synth_artifact"))
        refs.extend(payload.get("evidence_refs") or [])
        artifact_refs = payload.get("artifact_refs")
        if isinstance(artifact_refs, dict):
            refs.extend(artifact_refs.values())
        nested_refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
        refs.append(nested_refs.get("artifact_ref"))
        refs.extend(nested_refs.values())
    return _clean_str_list(refs)[-60:]


def _first_ts(group: list[tuple[int, ZfEvent]]) -> str:
    return str(group[0][1].ts or "") if group else ""


def _last_ts(group: list[tuple[int, ZfEvent]]) -> str:
    return str(group[-1][1].ts or "") if group else ""


def _status_from_event(event: ZfEvent) -> str:
    payload = _payload(event)
    if payload.get("status"):
        return str(payload.get("status"))
    tail = event.type.rsplit(".", 1)[-1]
    if tail in {"requested", "accepted", "rejected", "started", "completed", "failed", "skipped"}:
        return tail
    return tail


def _first_non_empty(
    payloads: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    fallback: str = "",
) -> str:
    for payload in payloads:
        for key in keys:
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return fallback


def _sandbox_label(payloads: list[dict[str, Any]]) -> str:
    for payload in payloads:
        if payload.get("sandbox") not in (None, ""):
            return str(payload.get("sandbox"))
        if payload.get("sandbox_required") not in (None, ""):
            return "required" if bool(payload.get("sandbox_required")) else "not_required"
    return ""


def _score_fields(payloads: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "score_delta": _last_number(payloads, ("score_delta", "delta", "improvement")),
        "baseline_score": _last_number(payloads, ("baseline_score", "before_score")),
        "candidate_score": _last_number(payloads, ("candidate_score", "after_score")),
    }


def _review_gate_fields(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _last_dict(payloads, "review_gate") or _last_dict(payloads, "summary")
    policy = _last_dict(payloads, "policy") or _last_dict([summary], "policy")
    refs = _last_dict(payloads, "artifact_refs") or _last_dict([summary], "artifact_refs")
    closeout = _last_dict(payloads, "closeout") or _last_dict(payloads, "result")
    decision = str(
        _last_value(payloads, "decision")
        or closeout.get("decision")
        or summary.get("decision")
        or ""
    )
    return {
        "mode": str(_last_value(payloads, "mode") or summary.get("mode") or ""),
        "status": str(_last_value(payloads, "status") or summary.get("status") or ""),
        "route": str(_last_value(payloads, "route") or summary.get("route") or policy.get("route") or ""),
        "severity": str(_last_value(payloads, "severity") or summary.get("severity") or policy.get("severity") or ""),
        "triggered": bool(_last_bool(payloads, "triggered", default=summary.get("triggered", False))),
        "decision": decision,
        "accepted": bool(_last_bool(payloads, "accepted", default=closeout.get("accepted", False))),
        "failure_fingerprint": str(
            _last_value(payloads, "failure_fingerprint")
            or summary.get("failure_fingerprint")
            or ""
        ),
        "attempt": _safe_int(_last_value(payloads, "attempt") or summary.get("attempt")),
        "attempt_cap": _safe_int(
            _last_value(payloads, "attempt_cap") or summary.get("attempt_cap")
        ),
        "budget_cap": _last_dict(payloads, "budget_cap")
        or (summary.get("budget_cap") if isinstance(summary.get("budget_cap"), dict) else {}),
        "channel_id": str(_last_value(payloads, "channel_id") or summary.get("channel_id") or ""),
        "artifact_refs": {
            str(k): str(v)
            for k, v in refs.items()
            if v not in (None, "")
        },
    }


def _last_value(payloads: list[dict[str, Any]], key: str) -> Any:
    for payload in reversed(payloads):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _last_bool(payloads: list[dict[str, Any]], key: str, *, default: Any = False) -> bool:
    for payload in reversed(payloads):
        if key in payload:
            return bool(payload.get(key))
    return bool(default)


def _last_dict(payloads: list[dict[str, Any]], key: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for payload in payloads:
        value = payload.get(key)
        if isinstance(value, dict):
            found = value
    return found


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _last_number(payloads: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    value: float | None = None
    for payload in payloads:
        score = payload.get("score") if isinstance(payload.get("score"), dict) else {}
        for key in keys:
            raw = payload.get(key)
            if raw is None and key in {"delta", "score_delta"}:
                raw = score.get("delta")
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
    return value
