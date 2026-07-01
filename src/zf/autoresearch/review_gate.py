"""Autoresearch review fanout gate policy and closeout validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.autoresearch.failure_signals import severity_rank
from zf.autoresearch.review_gate_context import prepare_review_gate_context

REVIEW_COUNCIL_SCHEMA = "autoresearch.review_council.v1"
REVIEW_GATE_MODES = frozenset({"off", "auto", "always"})
VALID_DECISIONS = frozenset({"approve", "revise", "block"})
VALID_REPAIR_RECOMMENDATIONS = frozenset({"manual", "authorized", "blocked", ""})


@dataclass(frozen=True)
class ReviewGatePolicyDecision:
    route: str
    reason: str
    severity: str
    required_roles: list[str] = field(default_factory=list)
    budget_cap: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewGateCloseoutResult:
    accepted: bool
    decision: str
    status: str
    errors: list[str]
    closeout_artifact: str
    report_path: str
    repair_authorization_required: bool
    repair_dispatch_requested: bool
    blocker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_review_gate_policy(failure_evidence_pack: dict[str, Any]) -> ReviewGatePolicyDecision:
    severity = str(failure_evidence_pack.get("severity") or "medium").lower()
    fingerprint = str(failure_evidence_pack.get("failure_fingerprint") or "")
    fatal_event = failure_evidence_pack.get("fatal_event")
    hypotheses = failure_evidence_pack.get("initial_hypotheses") or []
    text_parts = [fingerprint]
    for item in hypotheses:
        if isinstance(item, dict):
            text_parts.extend(str(item.get(key) or "") for key in ("category", "summary", "fingerprint"))
    text = " ".join(text_parts).lower()
    runtime_keywords = {
        "eventwriter",
        "eventlog",
        "taskstore",
        "featurestore",
        "sessionstore",
        "workflow",
        "fanout",
        "replan",
        "remediation",
        "self-repair",
        "repair",
        "dispatch",
        "handoff",
        "task_ref",
        "stuck",
        "runtime",
    }
    high = severity_rank(severity) >= severity_rank("high")
    runtime_risk = bool(fatal_event) or any(keyword in text for keyword in runtime_keywords)
    if high and runtime_risk:
        return ReviewGatePolicyDecision(
            route="fanout_gate",
            reason="high-risk runtime/workflow/fanout failure requires parallel review",
            severity=severity,
            required_roles=[
                "ar-diagnoser",
                "ar-kernel-reviewer",
                "ar-repair-planner",
                "ar-critic-verifier",
            ],
            budget_cap={"max_runs": 1, "max_minutes": 45},
        )
    if high:
        return ReviewGatePolicyDecision(
            route="lightweight_review",
            reason="high severity without runtime topology signal",
            severity=severity,
            required_roles=["ar-diagnoser", "ar-critic-verifier"],
            budget_cap={"max_runs": 1, "max_minutes": 20},
        )
    return ReviewGatePolicyDecision(
        route="direct_repair",
        reason="low/medium severity can use direct repair after reproduction",
        severity=severity,
        required_roles=[],
        budget_cap={"max_runs": 1, "max_minutes": 10},
    )


def normalize_review_gate_mode(value: Any) -> str:
    mode = str(value or "off").strip().lower()
    if mode not in REVIEW_GATE_MODES:
        known = ", ".join(sorted(REVIEW_GATE_MODES))
        raise ValueError(f"review_gate must be one of {known}")
    return mode


def prepare_review_gate_summary(
    *,
    mode: Any,
    run_status: str,
    run_dir: Path,
    state_dir: Path,
    source_root: Path,
) -> dict[str, Any]:
    """Prepare review-gate artifacts and return a compact run summary.

    ``auto`` classifies failed/non-passed runs but only marks high-risk
    ``fanout_gate`` decisions as triggered. ``always`` forces artifact
    generation for campaign/stress validation.
    """
    normalized = normalize_review_gate_mode(mode)
    if normalized == "off":
        return {
            "mode": "off",
            "status": "disabled",
            "triggered": False,
            "route": "",
            "reason": "review gate disabled",
            "artifact_refs": {},
        }
    if normalized == "auto" and str(run_status or "") == "passed":
        return {
            "mode": "auto",
            "status": "skipped",
            "triggered": False,
            "route": "direct_repair",
            "reason": "run passed; review gate not needed",
            "artifact_refs": {},
        }

    prepared = prepare_review_gate_context(
        run_dir=run_dir,
        state_dir=state_dir,
        source_root=source_root,
    )
    failure_pack = _read_json(Path(prepared.failure_evidence_pack))
    policy = classify_review_gate_policy(failure_pack)
    triggered = normalized == "always" or policy.route == "fanout_gate"
    generated_at = _now_iso()
    summary = {
        "mode": normalized,
        "status": "triggered" if triggered else "classified",
        "triggered": triggered,
        "route": policy.route,
        "reason": policy.reason,
        "severity": policy.severity,
        "run_terminal_status": str(failure_pack.get("run_terminal_status") or run_status or ""),
        "primary_failure_class": str(failure_pack.get("primary_failure_class") or ""),
        "review_gate_summary_fresh": True,
        "generated_at": generated_at,
        "failure_fingerprint": prepared.failure_fingerprint,
        "attempt": 1,
        "attempt_cap": 2,
        "budget_cap": dict(policy.budget_cap),
        "required_roles": list(policy.required_roles),
        "artifact_refs": {
            "codebase_context_pack": prepared.codebase_context_pack,
            "failure_evidence_pack": prepared.failure_evidence_pack,
            "events_summary": prepared.events_summary,
        },
        "codebase_pack_reused": prepared.codebase_pack_reused,
    }
    out = Path(run_dir) / "review-gate" / "summary.json"
    _write_json(out, {
        "schema_version": "autoresearch.review_gate.summary.v1",
        **summary,
        "policy": policy.to_dict(),
    })
    summary["artifact_refs"]["summary"] = str(out)
    return summary


def closeout_review_gate(
    *,
    run_dir: Path,
    synth_artifact: Path,
) -> ReviewGateCloseoutResult:
    run_dir = Path(run_dir)
    payload = _read_json(Path(synth_artifact))
    errors = validate_review_council_artifact(payload)
    decision = str(payload.get("decision") or "")
    blocker = _blocker_text(payload)
    accepted = not errors
    status = "accepted" if accepted else "rejected"
    out_dir = run_dir / "review-gate"
    out_dir.mkdir(parents=True, exist_ok=True)
    closeout_path = out_dir / "closeout.json"
    report_path = out_dir / "closeout.md"
    result = ReviewGateCloseoutResult(
        accepted=accepted,
        decision=decision,
        status=status,
        errors=errors,
        closeout_artifact=str(closeout_path),
        report_path=str(report_path),
        repair_authorization_required=accepted and decision == "approve",
        repair_dispatch_requested=False,
        blocker=blocker,
    )
    _write_json(closeout_path, {
        "schema_version": "autoresearch.review_gate.closeout.v1",
        "generated_at": _now_iso(),
        "synth_artifact": str(synth_artifact),
        "result": result.to_dict(),
        "decision_artifact": payload,
        "note": (
            "P0 closeout is artifact-only. Repair dispatch must go through "
            "existing authorization and runtime action paths."
        ),
    })
    _write_report(report_path, payload=payload, result=result)
    return result


def validate_review_council_artifact(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != REVIEW_COUNCIL_SCHEMA:
        errors.append(f"schema_version must be {REVIEW_COUNCIL_SCHEMA}")
    decision = str(payload.get("decision") or "")
    if decision not in VALID_DECISIONS:
        errors.append("decision must be approve, revise, or block")
    recommendation = str(payload.get("repair_authorization_recommendation") or "")
    if recommendation not in VALID_REPAIR_RECOMMENDATIONS:
        errors.append("repair_authorization_recommendation must be manual, authorized, or blocked")
    if decision == "approve":
        errors.extend(_validate_approve(payload))
        errors.extend(_critic_errors(payload))
    elif decision == "revise":
        errors.extend(_validate_revise(payload))
    elif decision == "block":
        errors.extend(_validate_block(payload))
    return errors


def _validate_approve(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_text = ("root_cause",)
    for field_name in required_text:
        if not _text(payload.get(field_name)):
            errors.append(f"decision=approve requires {field_name}")
    required_lists = ("minimal_patch_scope", "regression_commands", "evidence_refs")
    for field_name in required_lists:
        if not _string_list(payload.get(field_name)):
            errors.append(f"decision=approve requires non-empty {field_name}")
    return errors


def _validate_revise(payload: dict[str, Any]) -> list[str]:
    if (
        _string_list(payload.get("missing_evidence"))
        or _text(payload.get("next_research_prompt"))
        or _text(payload.get("revision_reason"))
        or _string_list(payload.get("critic_findings"))
    ):
        return []
    return ["decision=revise requires missing evidence or next research prompt"]


def _validate_block(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _blocker_text(payload):
        errors.append("decision=block requires blocker or risk")
    if not (
        _text(payload.get("manual_next_step"))
        or _text(payload.get("human_next_step"))
        or _text(payload.get("next_step"))
    ):
        errors.append("decision=block requires manual_next_step")
    return errors


def _critic_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for idx, finding in enumerate(payload.get("critic_findings") or []):
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").lower()
        status = str(finding.get("status") or finding.get("state") or "").lower()
        if severity in {"high", "critical"} and status not in {
            "resolved",
            "accepted",
            "mitigated",
            "closed",
        }:
            errors.append(f"critic_findings[{idx}] unresolved {severity} finding blocks approve")
    return errors


def _blocker_text(payload: dict[str, Any]) -> str:
    for key in ("blocker", "blockers", "risk", "reason"):
        value = payload.get(key)
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = _text(value)
        if text:
            return text
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"synth artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"synth artifact is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("synth artifact must be a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_report(
    path: Path,
    *,
    payload: dict[str, Any],
    result: ReviewGateCloseoutResult,
) -> Path:
    errors = "\n".join(f"- {error}" for error in result.errors) or "- none"
    evidence = "\n".join(f"- `{ref}`" for ref in _string_list(payload.get("evidence_refs"))) or "- none"
    text = (
        "# Autoresearch Review Gate Closeout\n\n"
        f"- status: `{result.status}`\n"
        f"- decision: `{result.decision}`\n"
        f"- repair_dispatch_requested: `{result.repair_dispatch_requested}`\n"
        f"- blocker: {result.blocker or 'none'}\n\n"
        "## Errors\n\n"
        f"{errors}\n\n"
        "## Evidence\n\n"
        f"{evidence}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "REVIEW_COUNCIL_SCHEMA",
    "REVIEW_GATE_MODES",
    "ReviewGateCloseoutResult",
    "ReviewGatePolicyDecision",
    "classify_review_gate_policy",
    "closeout_review_gate",
    "normalize_review_gate_mode",
    "prepare_review_gate_summary",
    "validate_review_council_artifact",
]
