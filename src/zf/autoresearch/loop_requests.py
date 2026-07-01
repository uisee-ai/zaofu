"""Autoresearch loop request contracts and read-only projection.

Supervisor emits lightweight requests; a separate resident consumer may choose
to execute them. This keeps the periodic Supervisor tick deterministic and
prevents a heavy LLM loop from becoming part of the kernel path.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

LOOP_REQUEST_SCHEMA_VERSION = "autoresearch.loop-request.v0"
LOOP_REQUESTED = "autoresearch.loop.requested"
LOOP_ACCEPTED = "autoresearch.loop.accepted"
LOOP_SKIPPED = "autoresearch.loop.skipped"
LOOP_STARTED = "autoresearch.loop.started"
LOOP_COMPLETED = "autoresearch.loop.completed"
LOOP_FAILED = "autoresearch.loop.failed"
LOOP_EVENTS = {
    LOOP_REQUESTED,
    LOOP_ACCEPTED,
    LOOP_SKIPPED,
    LOOP_STARTED,
    LOOP_COMPLETED,
    LOOP_FAILED,
}
DEFAULT_LOOP_SCENARIOS = ["controlled-stuck-recovery"]
DEFAULT_EXPECTED_OUTPUT = ["diagnosis_report", "reproduction_steps", "patch_proposal"]
RESEARCH_MODE_CONTRACTS: dict[str, dict[str, Any]] = {
    "probe": {
        "artifact_kind": "research_probe_report.v1",
        "output_kind": "research_probe_report",
        "budget_cap": {"max_runs": 1, "max_minutes": 15},
        "expected_output": ["research_probe_report", "evidence_refs", "replan_or_continue_recommendation"],
    },
    "predict": {
        "artifact_kind": "risk_forecast.v1",
        "output_kind": "risk_forecast",
        "budget_cap": {"max_runs": 1, "max_minutes": 10},
        "expected_output": ["hypothesis_queue", "risk_forecast", "recommended_guardrails"],
    },
    "scenario": {
        "artifact_kind": "scenario_pack.v1",
        "output_kind": "scenario_pack",
        "budget_cap": {"max_runs": 1, "max_minutes": 20},
        "expected_output": ["scenario_pack", "edge_cases", "verification_prompt_refs"],
    },
    "debug": {
        "artifact_kind": "diagnosis_report.v1",
        "output_kind": "diagnosis_report",
        "budget_cap": {"max_runs": 1, "max_minutes": 30},
        "expected_output": DEFAULT_EXPECTED_OUTPUT,
    },
    "evals": {
        "artifact_kind": "eval_comparison.v1",
        "output_kind": "eval_comparison",
        "budget_cap": {"max_runs": 1, "max_minutes": 20},
        "expected_output": ["eval_result", "eval_comparison", "blocking_gate_reasons"],
    },
    "learn": {
        "artifact_kind": "capability_deposition.v1",
        "output_kind": "capability_deposition",
        "budget_cap": {"max_runs": 1, "max_minutes": 10},
        "expected_output": ["capability_deposition", "trigger_conditions", "verification_refs"],
    },
}
RESEARCH_MODE_EXPECTED_OUTPUTS: dict[str, list[str]] = {
    mode: list(contract["expected_output"])
    for mode, contract in RESEARCH_MODE_CONTRACTS.items()
}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def normalize_research_mode(value: Any) -> str:
    mode = _clean_str(value).lower()
    return mode if mode in RESEARCH_MODE_EXPECTED_OUTPUTS else "debug"


def research_mode_contract(mode: Any) -> dict[str, Any]:
    normalized = normalize_research_mode(mode)
    contract = dict(RESEARCH_MODE_CONTRACTS[normalized])
    contract["mode"] = normalized
    contract["expected_output"] = list(contract.get("expected_output") or [])
    contract["budget_cap"] = dict(contract.get("budget_cap") or {})
    return contract


def build_research_mode_artifact_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    mode = normalize_research_mode(payload.get("mode") or payload.get("research_mode"))
    contract = research_mode_contract(mode)
    request_id = loop_request_id_from_payload(payload, fallback=str(payload.get("source_event_id") or mode))
    envelope: dict[str, Any] = {
        "schema_version": "autoresearch.mode-artifact-envelope.v1",
        "mode": mode,
        "loop_request_id": request_id,
        "artifact_kind": str(contract.get("artifact_kind") or ""),
        "output_kind": str(contract.get("output_kind") or ""),
        "expected_output": list(payload.get("expected_output") or contract.get("expected_output") or []),
        "budget_cap": dict(contract.get("budget_cap") or {}),
        "apply_policy": "proposal_only",
        "proposal_only": True,
        "direct_mainline_apply": False,
        "refs": {
            "source_event_id": _clean_str(payload.get("source_event_id")),
            "source_insight_ref": _clean_str(payload.get("source_insight_ref")),
            "handoff_ref": _clean_str(payload.get("handoff_ref")),
            "proposal_ref": _clean_str(payload.get("proposal_ref")),
        },
    }
    if mode == "scenario":
        envelope["scenario_pack"] = {
            "schema_version": "scenario_pack.v1",
            "runbook_refs": _string_list(payload.get("runbook_refs")),
            "e2e_prompt_refs": _string_list(payload.get("e2e_prompt_refs")),
            "scenarios": _string_list(payload.get("scenarios")),
            "status": "proposal_only",
        }
    elif mode == "learn":
        envelope["deposition"] = {
            "schema_version": "capability_deposition.v1",
            "target": _clean_str(payload.get("deposition_target") or "docs/skills/backlog proposal"),
            "trigger_conditions": _string_list(payload.get("trigger_conditions")),
            "verification_refs": _string_list(payload.get("verification_refs")),
            "status": "proposal_only",
        }
    return redact_obj(envelope)


def loop_request_id_from_payload(payload: dict[str, Any], *, fallback: str) -> str:
    explicit = _clean_str(payload.get("loop_request_id") or payload.get("request_id"))
    if explicit:
        return explicit
    trigger_id = _clean_str(payload.get("trigger_id") or payload.get("invocation_id"))
    fingerprint = _clean_str(payload.get("fingerprint"))
    seed = "|".join(part for part in (trigger_id, fingerprint, fallback) if part)
    return "arlp-" + _sha1(seed or fallback)[:12]


def build_loop_request_payload(
    trigger_payload: dict[str, Any],
    *,
    source_event_id: str,
) -> dict[str, Any]:
    request_id = loop_request_id_from_payload(
        trigger_payload,
        fallback=source_event_id,
    )
    scenarios = _string_list(trigger_payload.get("scenarios")) or DEFAULT_LOOP_SCENARIOS
    mode = normalize_research_mode(
        trigger_payload.get("research_mode")
        or trigger_payload.get("mode")
        or "debug"
    )
    expected_output = (
        _string_list(trigger_payload.get("expected_output"))
        or RESEARCH_MODE_EXPECTED_OUTPUTS[mode]
    )
    return redact_obj({
        "schema_version": LOOP_REQUEST_SCHEMA_VERSION,
        "loop_request_id": request_id,
        "trigger_id": _clean_str(trigger_payload.get("trigger_id") or source_event_id),
        "invocation_id": _clean_str(trigger_payload.get("invocation_id")),
        "source": _clean_str(trigger_payload.get("source") or "autoresearch.trigger.accepted"),
        "source_event_id": source_event_id,
        "mode": mode,
        "source_insight_ref": _clean_str(trigger_payload.get("source_insight_ref")),
        "handoff_ref": _clean_str(trigger_payload.get("handoff_ref")),
        "insight_type": _clean_str(trigger_payload.get("insight_type")),
        "expected_output": expected_output,
        "level": "diagnose",
        "apply_policy": "proposal_only",
        "proposal_only": True,
        "sandbox_required": True,
        "requires_owner_approval_for_apply": True,
        "direct_mainline_apply": False,
        "severity": _clean_str(trigger_payload.get("severity")),
        "reason": _clean_str(trigger_payload.get("reason") or trigger_payload.get("trigger_reason")),
        "fingerprint": _clean_str(trigger_payload.get("fingerprint")),
        "evidence_paths": _string_list(trigger_payload.get("evidence_paths")),
        "scenarios": scenarios,
        "budget": trigger_payload.get("budget") if isinstance(trigger_payload.get("budget"), dict) else {
            "max_runs": 1,
            "max_minutes": 30,
        },
        "mode_contract": research_mode_contract(mode),
        "fix_wait_strategy": _clean_str(trigger_payload.get("fix_wait_strategy") or "none"),
    })


def handled_loop_request_ids(events: list[ZfEvent]) -> set[str]:
    handled: set[str] = set()
    for event in events:
        if event.type not in LOOP_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = loop_request_id_from_payload(payload, fallback=event.id)
        if request_id:
            handled.add(request_id)
    return handled


def loop_request_exists(events: list[ZfEvent], request_id: str) -> bool:
    if not request_id:
        return False
    return request_id in handled_loop_request_ids(events)


def loop_requests_projection(events: list[ZfEvent]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    by_status: Counter[str] = Counter()
    for event in events:
        if event.type not in LOOP_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = loop_request_id_from_payload(payload, fallback=event.id)
        row = rows.setdefault(request_id, {
            "loop_request_id": request_id,
            "status": "unknown",
            "trigger_id": "",
            "invocation_id": "",
            "fingerprint": "",
            "severity": "",
            "scenarios": [],
            "last_event_id": "",
            "last_event_type": "",
            "last_event_at": "",
            "reason": "",
            "run_dir": "",
            "eval_result_ref": "",
            "mode": "",
            "source_insight_ref": "",
            "handoff_ref": "",
            "expected_output": [],
            "proposal_only": False,
        })
        if event.type == LOOP_REQUESTED:
            row["status"] = "requested"
        elif event.type == LOOP_ACCEPTED:
            row["status"] = "accepted"
        elif event.type == LOOP_STARTED:
            row["status"] = "started"
        elif event.type == LOOP_COMPLETED:
            row["status"] = "completed"
        elif event.type == LOOP_FAILED:
            row["status"] = "failed"
        elif event.type == LOOP_SKIPPED:
            row["status"] = "skipped"
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        row["last_event_at"] = event.ts
        for key in (
            "trigger_id",
            "invocation_id",
            "fingerprint",
            "severity",
            "reason",
            "run_dir",
            "eval_result_ref",
            "mode",
            "source_insight_ref",
            "handoff_ref",
        ):
            value = _clean_str(payload.get(key))
            if value:
                row[key] = value
        if "proposal_only" in payload:
            row["proposal_only"] = bool(payload.get("proposal_only"))
        expected_output = _string_list(payload.get("expected_output"))
        if expected_output:
            row["expected_output"] = expected_output
        scenarios = _string_list(payload.get("scenarios"))
        if scenarios:
            row["scenarios"] = scenarios
    for row in rows.values():
        by_status[str(row.get("status") or "unknown")] += 1
    return {
        "schema_version": "autoresearch.loop-requests.projection.v0",
        "is_derived_projection": True,
        "summary": {
            "total": len(rows),
            "pending": by_status.get("requested", 0) + by_status.get("accepted", 0),
            "active": by_status.get("started", 0),
            "completed": by_status.get("completed", 0),
            "failed": by_status.get("failed", 0),
            "skipped": by_status.get("skipped", 0),
            "by_status": dict(sorted(by_status.items())),
        },
        "recent": sorted(
            rows.values(),
            key=lambda row: str(row.get("last_event_at") or ""),
        )[-50:],
    }


def project_loop_requests(state_dir: Path) -> dict[str, Any]:
    events_path = Path(state_dir) / "events.jsonl"
    if not events_path.exists():
        return loop_requests_projection([])
    log = EventLog(events_path)
    try:
        return loop_requests_projection(log.read_all())
    finally:
        log.close()


__all__ = [
    "LOOP_REQUEST_SCHEMA_VERSION",
    "LOOP_REQUESTED",
    "LOOP_ACCEPTED",
    "LOOP_SKIPPED",
    "LOOP_STARTED",
    "LOOP_COMPLETED",
    "LOOP_FAILED",
    "LOOP_EVENTS",
    "DEFAULT_LOOP_SCENARIOS",
    "RESEARCH_MODE_EXPECTED_OUTPUTS",
    "RESEARCH_MODE_CONTRACTS",
    "build_research_mode_artifact_envelope",
    "build_loop_request_payload",
    "handled_loop_request_ids",
    "loop_request_exists",
    "loop_request_id_from_payload",
    "loop_requests_projection",
    "normalize_research_mode",
    "project_loop_requests",
    "research_mode_contract",
]
