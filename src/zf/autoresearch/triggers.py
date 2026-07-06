"""Trigger policy for autoresearch.

The policy is deterministic and conservative: it accepts or skips candidate
triggers, but does not start repairs or mutate business truth by itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterable as IterableABC
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from zf.autoresearch.failure_signals import (
    FailureSignal,
    collect_failure_signals,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.event_problem_registry import autoresearch_eligible_failure_classes


DEFAULT_ELIGIBLE_FAILURE_CLASSES = tuple(sorted(set((
    "codex_realism_gap",
    "completion_without_gate",
    "control_plane_violation",
    "contract_verifier_missing_tool",
    "dispatch_preflight_blocker",
    "evaluator_drift",
    "fanout_event_contract",
    "fanout_failed",
    "fanout_runtime_failure",
    "fanout_runtime_pending",
    "handoff_stall",
    "missing_real_provider_evidence",
    "operator_access_bug",
    "orchestrator_pane_dead",
    "readonly_gate_mutation",
    "replan_followthrough_gap",
    "replan_followthrough_missing",
    "runtime_fatal",
    "self_declared_completion",
    "state_dir_violation",
    "success_handoff_stall",
    "task_ref_handoff_deadend",
    "verification_environment_missing_tool",
    "web_bind_failure",
    "worker_stuck",
    *autoresearch_eligible_failure_classes(),
))))


@dataclass(frozen=True)
class TriggerPolicy:
    enabled: bool = True
    mode: str = "supervised"  # off | manual | supervised | continuous
    repair_mode: str = "proposal_only"  # proposal_only | bounded_repair
    eligible_failure_classes: tuple[str, ...] = DEFAULT_ELIGIBLE_FAILURE_CLASSES
    severity_min: str = "high"
    cooldown_minutes: int = 30
    max_triggers_per_hour: int = 2
    max_daily_runs: int = 5
    active_self_repair: bool = False


def _config_get(section: object | None, key: str, default: object) -> object:
    if section is None:
        return default
    if isinstance(section, dict):
        return section.get(key, default)
    return getattr(section, key, default)


def trigger_policy_from_config(
    config: object | None,
    *,
    enabled: bool | None = None,
    mode: str | None = None,
    eligible_failure_classes: Iterable[str] | None = None,
    severity_min: str | None = None,
    repair_mode: str | None = None,
    cooldown_minutes: int | None = None,
    max_triggers_per_hour: int | None = None,
    max_daily_runs: int | None = None,
    active_self_repair: bool | None = None,
) -> TriggerPolicy:
    autoresearch = _config_get(config, "autoresearch", None)
    policy_config = _config_get(autoresearch, "trigger_policy", None)
    policy = TriggerPolicy(
        enabled=bool(_config_get(policy_config, "enabled", True)),
        mode=str(_config_get(policy_config, "mode", "supervised") or "supervised"),
        repair_mode=str(
            _config_get(policy_config, "repair_mode", "proposal_only")
            or "proposal_only"
        ),
        eligible_failure_classes=_normalize_failure_classes(
            _config_get(policy_config, "eligible_failure_classes", None),
            default=DEFAULT_ELIGIBLE_FAILURE_CLASSES,
        ),
        severity_min=str(_config_get(policy_config, "severity_min", "high") or "high"),
        cooldown_minutes=int(_config_get(policy_config, "cooldown_minutes", 30)),
        max_triggers_per_hour=int(
            _config_get(policy_config, "max_triggers_per_hour", 2)
        ),
        max_daily_runs=int(_config_get(policy_config, "max_daily_runs", 5)),
        active_self_repair=bool(
            _config_get(policy_config, "active_self_repair", False)
        ),
    )
    overrides: dict[str, object] = {}
    if enabled is not None:
        overrides["enabled"] = enabled
    if mode is not None:
        overrides["mode"] = mode
    if eligible_failure_classes is not None:
        overrides["eligible_failure_classes"] = _normalize_failure_classes(
            eligible_failure_classes,
            default=(),
        )
    if severity_min is not None:
        overrides["severity_min"] = severity_min
    if repair_mode is not None:
        overrides["repair_mode"] = repair_mode
    if cooldown_minutes is not None:
        overrides["cooldown_minutes"] = cooldown_minutes
    if max_triggers_per_hour is not None:
        overrides["max_triggers_per_hour"] = max_triggers_per_hour
    if max_daily_runs is not None:
        overrides["max_daily_runs"] = max_daily_runs
    if active_self_repair is not None:
        overrides["active_self_repair"] = active_self_repair
    return replace(policy, **overrides) if overrides else policy


@dataclass(frozen=True)
class TriggerDecision:
    trigger_id: str
    source_kind: str
    fingerprint: str
    severity: str
    reason: str
    failure_class: str = ""
    evidence_paths: list[str] = field(default_factory=list)
    state_dir: str = ""
    decision: str = "skipped"  # accepted | skipped
    skip_reason: str = ""
    signal_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trigger_id(fingerprint: str, created_at: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{fingerprint}|{created_at}".encode("utf-8")).hexdigest()[:12]
    return f"artrig-{digest}"


def decisions_path(state_dir: Path) -> Path:
    return state_dir / "autoresearch" / "triggers" / "decisions.jsonl"


def read_trigger_decisions(state_dir: Path) -> list[TriggerDecision]:
    path = decisions_path(state_dir)
    if not path.exists():
        return []
    decisions: list[TriggerDecision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        decisions.append(TriggerDecision(
            trigger_id=str(data.get("trigger_id") or ""),
            source_kind=str(data.get("source_kind") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            severity=str(data.get("severity") or "medium"),
            reason=str(data.get("reason") or ""),
            failure_class=str(data.get("failure_class") or ""),
            evidence_paths=[str(v) for v in data.get("evidence_paths") or []],
            state_dir=str(data.get("state_dir") or ""),
            decision=str(data.get("decision") or "skipped"),
            skip_reason=str(data.get("skip_reason") or ""),
            signal_ids=[str(v) for v in data.get("signal_ids") or []],
            created_at=str(data.get("created_at") or ""),
        ))
    return decisions


def _recent_accepted(
    decisions: Iterable[TriggerDecision],
    *,
    now: datetime,
    window: timedelta,
) -> list[TriggerDecision]:
    recent: list[TriggerDecision] = []
    for decision in decisions:
        if decision.decision != "accepted":
            continue
        ts = _parse_ts(decision.created_at)
        if ts is not None and now - ts <= window:
            recent.append(decision)
    return recent


def decide_trigger_for_signal(
    signal: FailureSignal,
    *,
    state_dir: Path,
    policy: TriggerPolicy,
    history: Iterable[TriggerDecision] = (),
    now: datetime | None = None,
) -> TriggerDecision:
    current = now or datetime.now(timezone.utc)
    created_at = current.isoformat()
    base = {
        "trigger_id": _trigger_id(signal.fingerprint, created_at),
        "source_kind": signal.source_kind,
        "fingerprint": signal.fingerprint,
        "severity": signal.severity,
        "reason": signal.summary,
        "failure_class": failure_class_for_signal(signal),
        "evidence_paths": list(signal.evidence_paths),
        "state_dir": str(state_dir),
        "signal_ids": [signal.signal_id],
        "created_at": created_at,
    }
    if not policy.enabled or policy.mode == "off":
        return TriggerDecision(**base, decision="skipped", skip_reason="disabled")
    if policy.active_self_repair:
        return TriggerDecision(**base, decision="skipped", skip_reason="self_repair_active")
    if not _failure_class_allowed(base["failure_class"], policy.eligible_failure_classes):
        return TriggerDecision(**base, decision="skipped", skip_reason="failure_class_not_eligible")

    accepted = _recent_accepted(history, now=current, window=timedelta(hours=1))
    if len(accepted) >= policy.max_triggers_per_hour:
        return TriggerDecision(**base, decision="skipped", skip_reason="hourly_budget")

    daily = _recent_accepted(history, now=current, window=timedelta(days=1))
    if len(daily) >= policy.max_daily_runs:
        return TriggerDecision(**base, decision="skipped", skip_reason="daily_budget")

    cooldown = timedelta(minutes=policy.cooldown_minutes)
    for previous in accepted:
        if previous.fingerprint != signal.fingerprint:
            continue
        ts = _parse_ts(previous.created_at)
        if ts is not None and current - ts <= cooldown:
            return TriggerDecision(**base, decision="skipped", skip_reason="cooldown")

    return TriggerDecision(**base, decision="accepted")


def failure_class_for_signal(signal: FailureSignal) -> str:
    return str(signal.category or signal.source_kind or "unknown").strip() or "unknown"


def _failure_class_allowed(failure_class: str, eligible: Iterable[str]) -> bool:
    allowed = {str(item).strip() for item in eligible if str(item).strip()}
    return "*" in allowed or failure_class in allowed


def _normalize_failure_classes(
    value: object,
    *,
    default: Iterable[str],
) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, IterableABC):
        raw = [str(item).strip() for item in value]
    else:
        raw = []
    normalized = tuple(item for item in raw if item)
    return normalized or tuple(default)


def scan_trigger_decisions(
    state_dir: Path,
    *,
    policy: TriggerPolicy | None = None,
    run_dir: Path | None = None,
) -> list[TriggerDecision]:
    state_dir = Path(state_dir)
    effective = policy or TriggerPolicy()
    if not effective.active_self_repair:
        try:
            from zf.runtime.maintenance import self_repair_active

            if self_repair_active(state_dir):
                effective = replace(effective, active_self_repair=True)
        except Exception:
            pass
    history = list(read_trigger_decisions(state_dir))
    signals = collect_failure_signals(state_dir, run_dir=run_dir)
    decisions: list[TriggerDecision] = []
    for signal in signals:
        decision = decide_trigger_for_signal(
            signal,
            state_dir=state_dir,
            policy=effective,
            history=history,
        )
        decisions.append(decision)
        history.append(decision)
    return decisions


def write_trigger_decision(
    state_dir: Path,
    decision: TriggerDecision,
    *,
    emit_event: bool = True,
    event_writer: EventWriter | None = None,
) -> None:
    path = decisions_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
    if not emit_event:
        return
    event_type = (
        "autoresearch.trigger.accepted"
        if decision.decision == "accepted"
        else "autoresearch.trigger.skipped"
    )
    writer = event_writer or EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type=event_type,
        actor="zf-autoresearch",
        payload=decision.to_dict(),
    ))


__all__ = [
    "DEFAULT_ELIGIBLE_FAILURE_CLASSES",
    "TriggerPolicy",
    "TriggerDecision",
    "decisions_path",
    "failure_class_for_signal",
    "read_trigger_decisions",
    "trigger_policy_from_config",
    "decide_trigger_for_signal",
    "scan_trigger_decisions",
    "write_trigger_decision",
]
