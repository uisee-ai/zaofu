"""Deterministic eval snapshot and delta helpers for autoresearch loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.autoresearch.loop_types import (
    AutoresearchEvalMetrics,
    AutoresearchMetricSnapshot,
    EvalDelta,
    EvalMetricSnapshot,
    EvalSnapshot,
    LopFreshnessSnapshot,
    LopMetricSnapshot,
    LopRecoverySnapshot,
    ScoreSnapshot,
    ValidityTriageSnapshot,
)
from zf.autoresearch.validity import assess_validity
from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


_SUCCESS_RUN_STATUSES = frozenset({"passed", "passed_after_rework"})


def _read_kanban_backlog_count(state_dir: Path) -> int:
    kanban = state_dir / "kanban.json"
    if not kanban.exists():
        return 0
    try:
        data = json.loads(kanban.read_text() or "[]")
    except Exception:
        return 0
    if not isinstance(data, list):
        return 0
    return sum(
        1 for t in data
        if isinstance(t, dict) and t.get("status") == "backlog"
    )


def _read_kanban_tasks(state_dir: Path) -> list[dict[str, Any]]:
    kanban = state_dir / "kanban.json"
    if not kanban.exists():
        return []
    try:
        data = json.loads(kanban.read_text() or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict)]


def _complexity_strict_reasons(tasks: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for task in tasks:
        contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
        complexity = str(contract.get("complexity") or "").strip().lower()
        if complexity in {"complex", "release"}:
            reason = f"complexity={complexity}"
            if reason not in reasons:
                reasons.append(reason)
            continue
        file_surface: list[str] = []
        for key in ("scope", "affected_files", "shared_files", "exclusive_files"):
            value = contract.get(key)
            if isinstance(value, list):
                file_surface.extend(str(item) for item in value)
        if len({item for item in file_surface if item.strip()}) >= 8:
            reason = "complexity=inferred:scope>=8"
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def _read_health_snapshot(state_dir: Path) -> dict[str, Any] | None:
    """Load the most-recent cached health snapshot, if any.

    Looking for ``<state_dir>/projections/health.json`` first (fast
    path), falling back to None. The loop driver populates this cache
    before each iteration by calling ``zf kanban health --format json``.
    """
    path = state_dir / "projections" / "health.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def collect_eval_snapshot(state_dir: Path) -> EvalSnapshot:
    """Build an EvalSnapshot from the cached health.json + live kanban.

    Pure read; never invokes subprocess. The loop driver is responsible
    for refreshing health.json before calling this.
    """
    backlog_count = _read_kanban_backlog_count(state_dir)
    health = _read_health_snapshot(state_dir)
    if health is None:
        return EvalSnapshot(
            healthy_metrics=0,
            warning_metrics=0,
            critical_metrics=0,
            coordinator_ratio=0.0,
            open_backlog_count=backlog_count,
            rework_looped=0,
            completed_tasks=0,
        )

    band_summary = health.get("metrics_band_summary") or {}
    if not band_summary:
        # Older snapshots: count by iterating metric_diagnostics.
        diags = health.get("metric_diagnostics") or []
        band_summary = {
            "healthy": sum(1 for d in diags if d.get("health_band") == "healthy"),
            "warning": sum(1 for d in diags if d.get("health_band") == "warning"),
            "critical": sum(1 for d in diags if d.get("health_band") == "critical"),
        }

    coordinator = health.get("coordinator") or {}
    ratio_raw = coordinator.get("dispatch_no_action_ratio")
    if ratio_raw is None:
        ratio = 0.0
    else:
        try:
            ratio = float(ratio_raw)
        except (TypeError, ValueError):
            ratio = 0.0

    throughput = health.get("throughput") or {}
    return EvalSnapshot(
        healthy_metrics=int(band_summary.get("healthy", 0)),
        warning_metrics=int(band_summary.get("warning", 0)),
        critical_metrics=int(band_summary.get("critical", 0)),
        coordinator_ratio=ratio,
        open_backlog_count=backlog_count,
        rework_looped=int(throughput.get("rework_looped_count", 0)),
        completed_tasks=int(throughput.get("tasks_completed", 0)),
    )


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def _load_config_for_state(state_dir: Path):
    try:
        return resolve_project_context(
            explicit_state_dir=state_dir,
            load_config_with_explicit=True,
        ).config
    except (ConfigError, FileNotFoundError, ValueError):
        return None


def _event_types(events: list[ZfEvent]) -> set[str]:
    return {event.type for event in events}


def _is_terminal_done_event(event: ZfEvent) -> bool:
    if event.type in {"task.done", "task.done.accepted", "task.archived"}:
        return True
    if event.type == "task.status_changed":
        payload = event.payload if isinstance(event.payload, dict) else {}
        return payload.get("to") == "done"
    return False


def _terminal_evidence_present(events: list[ZfEvent]) -> bool | None:
    types = _event_types(events)
    if any(_is_terminal_done_event(event) for event in events):
        return True
    return _gate_bool(
        types,
        passed={"task.done.evidence"},
        failed={"task.done.blocked"},
    )


def _gate_bool(
    types: set[str],
    *,
    passed: set[str],
    failed: set[str],
) -> bool | None:
    if types & failed:
        return False
    if types & passed:
        return True
    return None


def _parse_ts(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_seconds(ts: str | None, *, now: datetime) -> float | None:
    if not ts:
        return None
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    return max(0.0, round((now - parsed).total_seconds(), 3))


def _latest_event_ts(events: list[ZfEvent], types: set[str] | None = None) -> str | None:
    for event in reversed(events):
        if types is None or event.type in types:
            return event.ts
    return None


def _latest_context_usage(events: list[ZfEvent]) -> float | None:
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in (
            "context_usage_ratio",
            "context_ratio",
            "usage_ratio",
            "ratio",
        ):
            if payload.get(key) is None:
                continue
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def _latest_context_route(events: list[ZfEvent]) -> dict[str, str]:
    context_event: ZfEvent | None = None
    for event in reversed(events):
        if event.type in {"worker.context.critical", "worker.context.warning"}:
            context_event = event
            break
    if context_event is None:
        return {}
    payload = context_event.payload if isinstance(context_event.payload, dict) else {}
    route = ""
    route_reason = str(payload.get("reason") or context_event.type)
    resume_packet_path = ""
    for event in reversed(events):
        if event.type != "completion_audit.routed":
            continue
        audit_payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.causation_id != context_event.id
            and audit_payload.get("trigger_event_id") != context_event.id
            and audit_payload.get("trigger_event_type") != context_event.type
        ):
            continue
        route = str(audit_payload.get("route") or "")
        route_reason = str(audit_payload.get("reason") or route_reason)
        resume_packet_path = str(audit_payload.get("resume_packet_path") or "")
        break
    return {
        "event_type": context_event.type,
        "reason": route_reason,
        "route": route,
        "resume_packet_path": resume_packet_path,
    }


def _count_types(events: list[ZfEvent], types: set[str]) -> int:
    return sum(1 for event in events if event.type in types)


def _readonly_gate_mutation_count(events: list[ZfEvent]) -> int:
    count = 0
    for event in events:
        if event.type not in {"review.approved", "test.passed", "judge.passed"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        changed = payload.get("changed_files")
        if isinstance(changed, list) and any(str(item).strip() for item in changed):
            count += 1
    return count


def _event_changed_files(events: list[ZfEvent]) -> list[str]:
    files: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        changed = payload.get("changed_files")
        if isinstance(changed, list):
            files.extend(str(item) for item in changed if str(item).strip())
        elif isinstance(changed, str) and changed.strip():
            files.append(changed.strip())
    return sorted(set(files))


def _evidence_paths(events: list[ZfEvent]) -> list[str]:
    paths: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("evidence_refs", "artifact_refs", "evidence_paths", "artifacts"):
            value = payload.get(key)
            if isinstance(value, list):
                paths.extend(str(item) for item in value if str(item).strip())
            elif isinstance(value, str) and value.strip():
                paths.append(value.strip())
        for key in ("report_path", "log_path", "screenshot_path", "transcript_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
    return sorted(set(paths))


def _claims(events: list[ZfEvent]) -> list[str]:
    claims: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("claim", "summary", "result", "verdict"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                claims.append(value.strip())
    return claims


def _score_average(components: dict[str, float | None]) -> float | None:
    values = [value for value in components.values() if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _product_score(
    *,
    types: set[str],
    eval_metrics: EvalMetricSnapshot,
    lop: LopMetricSnapshot,
) -> ScoreSnapshot:
    components: dict[str, float | None] = {
        "kernel_integrity": 0.0 if types & {"event.malformed", "invalid.transition"} else 1.0,
        "runtime_reliability": 0.0 if lop.state == "stalled" else 1.0,
        "control_plane_safety": 0.0 if types & {"state_dir.violation"} else 1.0,
        "web_observability": None,
        "eval_strength": (
            1.0
            if eval_metrics.quality_gates_passed is True
            else (0.0 if eval_metrics.quality_gates_passed is False else None)
        ),
        "operator_ergonomics": 1.0 if lop.why_not_done_count == 0 else 0.5,
    }
    missing = [key for key, value in components.items() if value is None]
    return ScoreSnapshot(
        total=_score_average(components),
        components=components,
        missing_inputs=missing,
        notes=["read-only projection; not a keep/discard truth"],
    )


def _instrument_score(
    *,
    validity: ValidityTriageSnapshot,
    eval_metrics: EvalMetricSnapshot,
    terminal_evidence_present: bool | None,
) -> ScoreSnapshot:
    components: dict[str, float | None] = {
        "evaluator_protected": (
            0.0 if validity.protected_paths_touched else 1.0
        ),
        "evidence_completeness": (
            1.0
            if terminal_evidence_present is True
            else (0.0 if terminal_evidence_present is False else None)
        ),
        "split_hygiene": None,
        "noise_control": None,
        "negative_control": None,
        "claim_support": 0.0 if validity.blocked_claims else 1.0,
        "clean_state": (
            1.0
            if eval_metrics.clean_state_passed is True
            else (0.0 if eval_metrics.clean_state_passed is False else None)
        ),
    }
    missing = [key for key, value in components.items() if value is None]
    return ScoreSnapshot(
        total=_score_average(components),
        components=components,
        missing_inputs=missing,
        notes=["instrument score guards against evaluator drift and weak evidence"],
    )


def _read_work_unit_count(state_dir: Path) -> int | None:
    for rel in (
        "projections/work_units.json",
        "work_units.json",
    ):
        path = state_dir / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text() or "{}")
        except Exception:
            continue
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            work_units = data.get("work_units")
            if isinstance(work_units, list):
                return len(work_units)
    return None


def _split_quality_blockers(config: Any, work_unit_count: int | None) -> list[str]:
    if config is None:
        return []
    work_units_cfg = getattr(getattr(config, "workflow", None), "work_units", None)
    if not getattr(work_units_cfg, "enabled", False):
        return []
    if work_unit_count is None:
        return ["work unit projection missing"]
    return []


def _recommended_action(run_status: str) -> str:
    if run_status in _SUCCESS_RUN_STATUSES:
        return "done"
    if run_status in {"fatal", "validate_failed", "aborted"}:
        return "retry"
    return "continuation"


def _lop_state(run_status: str, freshness: LopFreshnessSnapshot) -> str:
    if run_status in _SUCCESS_RUN_STATUSES:
        return "healthy"
    if freshness.context_usage_ratio is not None and freshness.context_usage_ratio >= 0.85:
        return "context_warn"
    if run_status in {"fatal", "validate_failed", "aborted"}:
        return "stalled"
    return "idle_warn"


def collect_autoresearch_eval_metrics(
    state_dir: Path,
    *,
    eval_snapshot: EvalSnapshot,
    run_status: str,
    head_changed_since_prev: bool,
) -> AutoresearchEvalMetrics:
    """Collect doc 45/46 autoresearch/eval/LOP metrics as a read projection."""
    events = _read_events(state_dir)
    types = _event_types(events)
    tasks = _read_kanban_tasks(state_dir)
    config = _load_config_for_state(state_dir)
    workflow = getattr(config, "workflow", None)
    harness_profile = str(getattr(workflow, "harness_profile", "baseline") or "baseline")
    strict_triggers = getattr(workflow, "strict_triggers", None)
    context_usage_ratio = _latest_context_usage(events)
    context_route = _latest_context_route(events)

    strict_reasons: list[str] = []
    rework_threshold = int(getattr(strict_triggers, "rework_attempts_gte", 0) or 0)
    if rework_threshold > 0 and eval_snapshot.rework_looped >= rework_threshold:
        strict_reasons.append(f"rework_looped >= {rework_threshold}")
    context_threshold = float(getattr(strict_triggers, "context_usage_gte", 0.0) or 0.0)
    if (
        context_threshold > 0
        and context_usage_ratio is not None
        and context_usage_ratio >= context_threshold
    ):
        strict_reasons.append(f"context_usage_ratio >= {context_threshold}")
    strict_reasons.extend(_complexity_strict_reasons(tasks))

    strict_escalated = bool(strict_reasons)
    effective_profile = "strict" if strict_escalated else harness_profile
    work_unit_count = _read_work_unit_count(state_dir)
    split_blockers = _split_quality_blockers(config, work_unit_count)
    integration_cfg = getattr(workflow, "integration", None)
    readonly_gate_mutations = _readonly_gate_mutation_count(events)

    autoresearch = AutoresearchMetricSnapshot(
        harness_profile=harness_profile,
        boundary="worker_task",
        effective_profile=effective_profile,
        strict_escalated=strict_escalated,
        strict_trigger_reason=", ".join(strict_reasons),
        work_unit_count=work_unit_count,
        split_quality_blockers=split_blockers,
        scope_file_count=None,
        integration_required=(
            bool(getattr(integration_cfg, "enabled"))
            if integration_cfg is not None
            else None
        ),
    )

    required_command_passed = _gate_bool(
        types,
        passed={"dev.build.done", "test.passed", "gate.passed"},
        failed={"test.failed", "gate.failed"},
    )
    terminal_evidence_present = _terminal_evidence_present(events)
    eval_metrics = EvalMetricSnapshot(
        verdict="not_collected",
        required_command_passed=required_command_passed,
        terminal_evidence_present=terminal_evidence_present,
        no_open_blockers=(
            not any(t.get("blocked_by") for t in tasks)
            if tasks
            else None
        ),
        clean_handoff_present=_gate_bool(
            types,
            passed={"handoff.recorded", "task.handoff.recorded"},
            failed=set(),
        ),
        mutation_warning=True if readonly_gate_mutations else None,
        evidence_reissue_required=(
            True
            if types & {"event.schema.violated", "task.evidence.reissue"}
            else None
        ),
        critic_gate_passed=_gate_bool(
            types,
            passed={"critic.approved", "critic.passed", "arch.proposal.done"},
            failed={"critic.rejected", "critic.failed", "arch.proposal.rejected"},
        ),
        review_gate_passed=_gate_bool(
            types,
            passed={"review.approved"},
            failed={"review.rejected"},
        ),
        test_gate_passed=_gate_bool(
            types,
            passed={"test.passed"},
            failed={"test.failed"},
        ),
        judge_gate_passed=_gate_bool(
            types,
            passed={"judge.passed"},
            failed={"judge.failed"},
        ),
        quality_gates_passed=(
            False
            if readonly_gate_mutations
            else _gate_bool(
                types,
                passed={"gate.passed", "discriminator.passed"},
                failed={"gate.failed"},
            )
        ),
        clean_state_passed=(
            False
            if readonly_gate_mutations
            else _gate_bool(
                types,
                passed={"clean_state.passed", "check.clean_state.passed"},
                failed={"clean_state.failed", "check.clean_state.failed"},
            )
        ),
        rework_type=(
            "gate_integrity"
            if readonly_gate_mutations
            else ("product" if types & {"review.rejected", "test.failed"} else "")
        ),
        product_rework_count=_count_types(
            events,
            {"review.rejected", "test.failed", "judge.failed", "gate.failed"},
        ) + readonly_gate_mutations,
        infra_retry_count=_count_types(
            events,
            {"worker.retry", "task.retry_scheduled", "provider.retry"},
        ),
        evidence_reissue_count=_count_types(
            events,
            {"event.schema.violated", "task.evidence.reissue"},
        ),
    )

    now = datetime.now(timezone.utc)
    freshness = LopFreshnessSnapshot(
        last_heartbeat_age_sec=_age_seconds(
            _latest_event_ts(events, {"worker.heartbeat"}),
            now=now,
        ),
        last_event_age_sec=_age_seconds(_latest_event_ts(events), now=now),
        last_test_age_sec=_age_seconds(
            _latest_event_ts(events, {"test.passed", "test.failed"}),
            now=now,
        ),
        last_evidence_age_sec=_age_seconds(
            _latest_event_ts(
                events,
                {
                    "dev.build.done",
                    "review.approved",
                    "test.passed",
                    "judge.passed",
                    "gate.passed",
                    "discriminator.passed",
                    "task.done.evidence",
                    "task.done",
                    "task.status_changed",
                },
            ),
            now=now,
        ),
        idle_duration_sec=_age_seconds(_latest_event_ts(events), now=now),
        context_usage_ratio=context_usage_ratio,
        token_delta_recent=None,
        worktree_head_changed=head_changed_since_prev,
    )
    effective_status = "failed" if readonly_gate_mutations else run_status
    recommended = _recommended_action(effective_status)
    done_like = effective_status in _SUCCESS_RUN_STATUSES
    missing_evidence = (
        0 if done_like or eval_metrics.required_command_passed is True else 1
    )
    required_event_missing = (
        0 if done_like or eval_metrics.quality_gates_passed is True else 1
    )
    why_not_done = 0 if done_like else max(1, missing_evidence + required_event_missing)
    observed_route = context_route.get("route") or recommended
    route_reason = (
        f"run_status={run_status}; readonly_gate_mutations={readonly_gate_mutations}"
        if readonly_gate_mutations
        else f"run_status={run_status}"
    )
    if context_route:
        route_reason = (
            f"{route_reason}; context_event={context_route.get('event_type', '')}; "
            f"context_route_reason={context_route.get('reason', '')}"
        )
        if context_route.get("resume_packet_path"):
            route_reason = (
                f"{route_reason}; "
                f"resume_packet_path={context_route.get('resume_packet_path', '')}"
            )
    lop = LopMetricSnapshot(
        state=_lop_state(effective_status, freshness),
        recommended_action=recommended,
        why_not_done_count=why_not_done,
        blocking_why_not_done_count=why_not_done,
        missing_evidence_count=missing_evidence,
        required_event_missing_count=required_event_missing,
        next_required_event="" if done_like else "gate.passed",
        observed_route=observed_route,
        route_reason=route_reason,
        freshness=freshness,
        recovery=LopRecoverySnapshot(
            chaos_case_count=_count_types(events, {"chaos.case.started"}),
            chaos_passed_count=_count_types(events, {"chaos.case.passed"}),
            context_route_reason=context_route.get("reason", ""),
            resume_packet_path=context_route.get("resume_packet_path", ""),
        ),
    )
    validity_raw = assess_validity(
        changed_files=_event_changed_files(events),
        claims=_claims(events),
        evidence_paths=_evidence_paths(events),
    )
    validity = ValidityTriageSnapshot(
        status=validity_raw.status,
        risk_labels=validity_raw.risk_labels,
        evidence_debt=validity_raw.evidence_debt,
        recommended_probe=validity_raw.recommended_probe,
        allowed_claims=validity_raw.allowed_claims,
        blocked_claims=validity_raw.blocked_claims,
        protected_paths_touched=validity_raw.protected_paths_touched,
    )
    return AutoresearchEvalMetrics(
        autoresearch=autoresearch,
        eval=eval_metrics,
        lop=lop,
        validity=validity,
        product_score=_product_score(
            types=types,
            eval_metrics=eval_metrics,
            lop=lop,
        ),
        instrument_score=_instrument_score(
            validity=validity,
            eval_metrics=eval_metrics,
            terminal_evidence_present=terminal_evidence_present,
        ),
    )


# ---------------------------------------------------------------------------
# Delta computation (§2)
# ---------------------------------------------------------------------------


def _classify_delta_verdict(
    *,
    completed_delta: int,
    critical_delta: int,
    backlog_delta: int,
    healthy_delta: int,
) -> str:
    """Deterministic improved / regressed / unchanged decision.

    Priority (high-to-low):
      1. completed_delta > 0  → improved (any forward task progress wins)
      2. critical_delta < 0   → improved (fewer red metrics)
      3. critical_delta > 0   → regressed
      4. backlog_delta > 0 AND completed_delta == 0 → regressed (debt growing)
      5. healthy_delta > 0    → improved (more green)
      6. otherwise            → unchanged
    """
    if completed_delta > 0:
        return "improved"
    if critical_delta < 0:
        return "improved"
    if critical_delta > 0:
        return "regressed"
    if backlog_delta > 0 and completed_delta == 0:
        return "regressed"
    if healthy_delta > 0:
        return "improved"
    return "unchanged"


def compute_eval_delta(
    prev: EvalSnapshot,
    curr: EvalSnapshot,
) -> EvalDelta:
    """Compute the iteration-vs-iteration delta + verdict."""
    healthy_d = curr.healthy_metrics - prev.healthy_metrics
    critical_d = curr.critical_metrics - prev.critical_metrics
    coord_d = round(curr.coordinator_ratio - prev.coordinator_ratio, 4)
    backlog_d = curr.open_backlog_count - prev.open_backlog_count
    completed_d = curr.completed_tasks - prev.completed_tasks
    verdict = _classify_delta_verdict(
        completed_delta=completed_d,
        critical_delta=critical_d,
        backlog_delta=backlog_d,
        healthy_delta=healthy_d,
    )
    return EvalDelta(
        healthy_delta=healthy_d,
        critical_delta=critical_d,
        coordinator_delta=coord_d,
        backlog_delta=backlog_d,
        completed_delta=completed_d,
        verdict=verdict,
    )

__all__ = [
    "collect_eval_snapshot",
    "collect_autoresearch_eval_metrics",
    "compute_eval_delta",
]
