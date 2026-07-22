"""autoresearch loop data models and journal serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoopConfig:
    """Outer-loop configuration. Worktree + parent_state_dir are required;
    everything else has sensible defaults so a typical invocation is a
    one-liner."""
    scenarios: list[str] = field(default_factory=list)
    worktree: Path = Path("/tmp/zaofu-autoresearch")
    parent_state_dir: Path | None = None
    max_iterations: int = 10
    budget_usd: float = 200.0
    output_dir: Path | None = None
    reflect_backend: str = "claude-code"
    fix_wait_strategy: str = "head_change"   # head_change | manual | none
    fix_wait_timeout: int = 1800             # seconds
    config_template: Path = Path("examples/dev-codex-backends.yaml")
    # §9 playwright screenshot — empty url disables, default mcp image.
    screenshot_url: str = ""                 # e.g. "http://127.0.0.1:8765"
    screenshot_docker_image: str = "mcp/playwright:latest"
    screenshot_shot_js: Path = Path("tools/playwright-shot.js")
    # §10 bypass-autoresearch — drive inner harness directly with the
    # operator's own yaml + seed instead of autoresearch's scenarios.
    bypass_autoresearch: bool = False
    yaml_template: Path | None = None        # cp into <worktree>/zf.yaml
    seed_text: str = ""                      # zh-CN seed user.message
    expected_done: int = 1                   # terminal done events to wait for
    inner_wait_timeout: int = 900            # seconds polling terminal done
    review_gate: str = "off"                 # off | auto | always
    # Standalone loops keep legacy task creation; resident callers disable it.
    backlog_on_failure: bool = True


def default_metric_sources() -> dict[str, str]:
    """Design references for autoresearch/eval/LOP projection fields."""
    return {
        "profile": "docs/design/45-baseline-strict-harness-profiles.md:28-36",
        "boundary": "docs/design/45-baseline-strict-harness-profiles.md:128-153",
        "strict_escalation": "docs/design/46-long-horizon-workunit-feedback-design.md:423-451",
        "work_unit_split": "docs/design/46-long-horizon-workunit-feedback-design.md:140-179",
        "baseline_gates": "docs/design/45-baseline-strict-harness-profiles.md:184-206",
        "strict_gates": "docs/design/45-baseline-strict-harness-profiles.md:263-282",
        "evaluator_rubric": "docs/design/45-baseline-strict-harness-profiles.md:430-446",
        "rework_accounting": "docs/design/45-baseline-strict-harness-profiles.md:316-334",
        "why_not_done": "docs/design/46-long-horizon-workunit-feedback-design.md:197-247",
        "completion_route": "docs/design/46-long-horizon-workunit-feedback-design.md:281-315",
        "freshness": "docs/design/46-long-horizon-workunit-feedback-design.md:527-560",
        "chaos_recovery": "docs/design/46-long-horizon-workunit-feedback-design.md:582-616",
        "store_boundary": "docs/design/46-long-horizon-workunit-feedback-design.md:674-687",
        "validity": "docs/design/51-autoresearch-enhanced-self-evolution-design.md:226-245",
        "product_score": "docs/design/51-autoresearch-enhanced-self-evolution-design.md:396-407",
        "instrument_score": "docs/design/51-autoresearch-enhanced-self-evolution-design.md:409-419",
        "holdout": "docs/design/51-autoresearch-enhanced-self-evolution-design.md:328-356",
    }


@dataclass(frozen=True)
class AutoresearchMetricSnapshot:
    harness_profile: str = "baseline"
    boundary: str = "worker_task"
    effective_profile: str = "baseline"
    strict_escalated: bool = False
    strict_trigger_reason: str = ""
    work_unit_count: int | None = None
    split_quality_blockers: list[str] = field(default_factory=list)
    scope_file_count: int | None = None
    integration_required: bool | None = None


@dataclass(frozen=True)
class EvalMetricSnapshot:
    functionality_score: int | None = None
    evidence_quality_score: int | None = None
    architecture_compliance_score: int | None = None
    regression_risk_score: int | None = None
    verdict: str = "not_collected"
    required_command_passed: bool | None = None
    terminal_evidence_present: bool | None = None
    no_open_blockers: bool | None = None
    clean_handoff_present: bool | None = None
    coverage_warning: bool | None = None
    mutation_warning: bool | None = None
    minor_review_followup: bool | None = None
    evidence_reissue_required: bool | None = None
    critic_gate_passed: bool | None = None
    review_gate_passed: bool | None = None
    test_gate_passed: bool | None = None
    judge_gate_passed: bool | None = None
    quality_gates_passed: bool | None = None
    clean_state_passed: bool | None = None
    rework_type: str = ""
    product_rework_count: int = 0
    infra_retry_count: int = 0
    evidence_reissue_count: int = 0


@dataclass(frozen=True)
class LopFreshnessSnapshot:
    last_heartbeat_age_sec: float | None = None
    last_event_age_sec: float | None = None
    last_file_change_age_sec: float | None = None
    last_test_age_sec: float | None = None
    last_evidence_age_sec: float | None = None
    idle_duration_sec: float | None = None
    context_usage_ratio: float | None = None
    token_delta_recent: int | None = None
    worktree_head_changed: bool = False


@dataclass(frozen=True)
class LopRecoverySnapshot:
    chaos_case_count: int = 0
    chaos_passed_count: int = 0
    recovery_time_sec: float | None = None
    route_accuracy: bool | None = None
    residual_risk: str = ""
    context_route_reason: str = ""
    resume_packet_path: str = ""


@dataclass(frozen=True)
class LopMetricSnapshot:
    state: str = "healthy"
    recommended_action: str = "continuation"
    why_not_done_count: int = 0
    blocking_why_not_done_count: int = 0
    missing_evidence_count: int = 0
    required_event_missing_count: int = 0
    next_required_event: str = ""
    observed_route: str = ""
    route_reason: str = ""
    freshness: LopFreshnessSnapshot = field(default_factory=LopFreshnessSnapshot)
    recovery: LopRecoverySnapshot = field(default_factory=LopRecoverySnapshot)


@dataclass(frozen=True)
class ValidityTriageSnapshot:
    status: str = "pass"
    risk_labels: list[str] = field(default_factory=list)
    evidence_debt: list[dict[str, Any]] = field(default_factory=list)
    recommended_probe: dict[str, str] = field(default_factory=dict)
    allowed_claims: list[str] = field(default_factory=list)
    blocked_claims: list[str] = field(default_factory=list)
    protected_paths_touched: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreSnapshot:
    total: float | None = None
    components: dict[str, float | None] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutoresearchEvalMetrics:
    metric_sources: dict[str, str] = field(default_factory=default_metric_sources)
    autoresearch: AutoresearchMetricSnapshot = field(
        default_factory=AutoresearchMetricSnapshot,
    )
    eval: EvalMetricSnapshot = field(default_factory=EvalMetricSnapshot)
    lop: LopMetricSnapshot = field(default_factory=LopMetricSnapshot)
    validity: ValidityTriageSnapshot = field(default_factory=ValidityTriageSnapshot)
    product_score: ScoreSnapshot = field(default_factory=ScoreSnapshot)
    instrument_score: ScoreSnapshot = field(default_factory=ScoreSnapshot)


@dataclass(frozen=True)
class EvalSnapshot:
    """A single-point evaluation reading. All fields integer/float so
    delta computation is straightforward subtraction."""
    healthy_metrics: int
    warning_metrics: int
    critical_metrics: int
    coordinator_ratio: float
    open_backlog_count: int
    rework_looped: int
    completed_tasks: int


@dataclass(frozen=True)
class EvalDelta:
    """Iteration N vs iteration N-1 snapshot delta. ``verdict`` is a
    deterministic classification computed from the numeric deltas; it is
    NOT the LLM's verdict (that lives in ReflectionResult)."""
    healthy_delta: int
    critical_delta: int
    coordinator_delta: float
    backlog_delta: int
    completed_delta: int
    verdict: str   # improved | regressed | unchanged


@dataclass(frozen=True)
class ReflectionResult:
    """LLM meta-critique output. ``raw_response`` is kept so we can
    forensically inspect when JSON parse fell back to defaults."""
    verdict: str               # better_fix_exists | best_so_far | regression | unknown
    alternatives: list[str]
    risk: str                  # low | medium | high
    rec_for_next_iter: str
    raw_response: str


@dataclass(frozen=True)
class IterationRecord:
    iter: int
    started_at: str
    scenario: str
    run_id: str
    run_status: str            # passed | passed_after_rework | failed | fatal | validate_failed
    tasks_done: int
    expected_done: int
    eval: EvalSnapshot
    delta: EvalDelta | None
    reflect: ReflectionResult | None
    git_head: str
    head_changed_since_prev: bool
    summary: str
    outcome: str = ""          # passed | failed | fatal; normalized from run_status
    stop_reason: str = ""
    final_status_if_stopped: str = ""
    validation_kinds: list[str] = field(default_factory=list)
    rework_count: int = 0
    passed_after_rework: int = 0
    pending_rework_count: int = 0
    # §9: relative path under output_dir if screenshot succeeded, else None.
    # error is preserved on failure for journal forensics.
    screenshot_path: str | None = None
    screenshot_error: str = ""
    review_gate: dict[str, Any] = field(default_factory=dict)
    autoresearch_eval: AutoresearchEvalMetrics = field(
        default_factory=AutoresearchEvalMetrics,
    )


@dataclass(frozen=True)
class LoopResult:
    iterations: int
    final_status: str          # done | converged | budget_exhausted | no_progress | aborted | max_iter_unmet
    journal_path: Path
    report_path: Path


# ---------------------------------------------------------------------------
# JSON serialization (§1)
# ---------------------------------------------------------------------------


def _eval_to_dict(s: EvalSnapshot) -> dict[str, Any]:
    return {
        "healthy_metrics": s.healthy_metrics,
        "warning_metrics": s.warning_metrics,
        "critical_metrics": s.critical_metrics,
        "coordinator_ratio": s.coordinator_ratio,
        "open_backlog_count": s.open_backlog_count,
        "rework_looped": s.rework_looped,
        "completed_tasks": s.completed_tasks,
    }


def _eval_from_dict(d: dict[str, Any]) -> EvalSnapshot:
    return EvalSnapshot(
        healthy_metrics=int(d["healthy_metrics"]),
        warning_metrics=int(d["warning_metrics"]),
        critical_metrics=int(d["critical_metrics"]),
        coordinator_ratio=float(d["coordinator_ratio"]),
        open_backlog_count=int(d["open_backlog_count"]),
        rework_looped=int(d["rework_looped"]),
        completed_tasks=int(d["completed_tasks"]),
    )


def _delta_to_dict(d: EvalDelta) -> dict[str, Any]:
    return {
        "healthy_delta": d.healthy_delta,
        "critical_delta": d.critical_delta,
        "coordinator_delta": d.coordinator_delta,
        "backlog_delta": d.backlog_delta,
        "completed_delta": d.completed_delta,
        "verdict": d.verdict,
    }


def _delta_from_dict(d: dict[str, Any]) -> EvalDelta:
    return EvalDelta(
        healthy_delta=int(d["healthy_delta"]),
        critical_delta=int(d["critical_delta"]),
        coordinator_delta=float(d["coordinator_delta"]),
        backlog_delta=int(d["backlog_delta"]),
        completed_delta=int(d["completed_delta"]),
        verdict=str(d["verdict"]),
    )


def _reflect_to_dict(r: ReflectionResult) -> dict[str, Any]:
    return {
        "verdict": r.verdict,
        "alternatives": list(r.alternatives),
        "risk": r.risk,
        "rec_for_next_iter": r.rec_for_next_iter,
        "raw_response": r.raw_response,
    }


def _reflect_from_dict(d: dict[str, Any]) -> ReflectionResult:
    return ReflectionResult(
        verdict=str(d["verdict"]),
        alternatives=[str(x) for x in (d.get("alternatives") or [])],
        risk=str(d["risk"]),
        rec_for_next_iter=str(d["rec_for_next_iter"]),
        raw_response=str(d.get("raw_response", "")),
    )


def _autoresearch_metric_to_dict(s: AutoresearchMetricSnapshot) -> dict[str, Any]:
    return {
        "harness_profile": s.harness_profile,
        "boundary": s.boundary,
        "effective_profile": s.effective_profile,
        "strict_escalated": s.strict_escalated,
        "strict_trigger_reason": s.strict_trigger_reason,
        "work_unit_count": s.work_unit_count,
        "split_quality_blockers": list(s.split_quality_blockers),
        "scope_file_count": s.scope_file_count,
        "integration_required": s.integration_required,
    }


def _autoresearch_metric_from_dict(d: dict[str, Any]) -> AutoresearchMetricSnapshot:
    return AutoresearchMetricSnapshot(
        harness_profile=str(d.get("harness_profile", "baseline")),
        boundary=str(d.get("boundary", "worker_task")),
        effective_profile=str(d.get("effective_profile", "baseline")),
        strict_escalated=bool(d.get("strict_escalated", False)),
        strict_trigger_reason=str(d.get("strict_trigger_reason", "")),
        work_unit_count=(
            int(d["work_unit_count"]) if d.get("work_unit_count") is not None else None
        ),
        split_quality_blockers=[
            str(x) for x in (d.get("split_quality_blockers") or [])
        ],
        scope_file_count=(
            int(d["scope_file_count"]) if d.get("scope_file_count") is not None else None
        ),
        integration_required=(
            bool(d["integration_required"])
            if d.get("integration_required") is not None
            else None
        ),
    )


def _eval_metric_to_dict(s: EvalMetricSnapshot) -> dict[str, Any]:
    return {
        "functionality_score": s.functionality_score,
        "evidence_quality_score": s.evidence_quality_score,
        "architecture_compliance_score": s.architecture_compliance_score,
        "regression_risk_score": s.regression_risk_score,
        "verdict": s.verdict,
        "required_command_passed": s.required_command_passed,
        "terminal_evidence_present": s.terminal_evidence_present,
        "no_open_blockers": s.no_open_blockers,
        "clean_handoff_present": s.clean_handoff_present,
        "coverage_warning": s.coverage_warning,
        "mutation_warning": s.mutation_warning,
        "minor_review_followup": s.minor_review_followup,
        "evidence_reissue_required": s.evidence_reissue_required,
        "critic_gate_passed": s.critic_gate_passed,
        "review_gate_passed": s.review_gate_passed,
        "test_gate_passed": s.test_gate_passed,
        "judge_gate_passed": s.judge_gate_passed,
        "quality_gates_passed": s.quality_gates_passed,
        "clean_state_passed": s.clean_state_passed,
        "rework_type": s.rework_type,
        "product_rework_count": s.product_rework_count,
        "infra_retry_count": s.infra_retry_count,
        "evidence_reissue_count": s.evidence_reissue_count,
    }


def _maybe_bool(d: dict[str, Any], key: str) -> bool | None:
    return bool(d[key]) if d.get(key) is not None else None


def _eval_metric_from_dict(d: dict[str, Any]) -> EvalMetricSnapshot:
    return EvalMetricSnapshot(
        functionality_score=(
            int(d["functionality_score"]) if d.get("functionality_score") is not None else None
        ),
        evidence_quality_score=(
            int(d["evidence_quality_score"]) if d.get("evidence_quality_score") is not None else None
        ),
        architecture_compliance_score=(
            int(d["architecture_compliance_score"])
            if d.get("architecture_compliance_score") is not None
            else None
        ),
        regression_risk_score=(
            int(d["regression_risk_score"]) if d.get("regression_risk_score") is not None else None
        ),
        verdict=str(d.get("verdict", "not_collected")),
        required_command_passed=_maybe_bool(d, "required_command_passed"),
        terminal_evidence_present=_maybe_bool(d, "terminal_evidence_present"),
        no_open_blockers=_maybe_bool(d, "no_open_blockers"),
        clean_handoff_present=_maybe_bool(d, "clean_handoff_present"),
        coverage_warning=_maybe_bool(d, "coverage_warning"),
        mutation_warning=_maybe_bool(d, "mutation_warning"),
        minor_review_followup=_maybe_bool(d, "minor_review_followup"),
        evidence_reissue_required=_maybe_bool(d, "evidence_reissue_required"),
        critic_gate_passed=_maybe_bool(d, "critic_gate_passed"),
        review_gate_passed=_maybe_bool(d, "review_gate_passed"),
        test_gate_passed=_maybe_bool(d, "test_gate_passed"),
        judge_gate_passed=_maybe_bool(d, "judge_gate_passed"),
        quality_gates_passed=_maybe_bool(d, "quality_gates_passed"),
        clean_state_passed=_maybe_bool(d, "clean_state_passed"),
        rework_type=str(d.get("rework_type", "")),
        product_rework_count=int(d.get("product_rework_count", 0)),
        infra_retry_count=int(d.get("infra_retry_count", 0)),
        evidence_reissue_count=int(d.get("evidence_reissue_count", 0)),
    )


def _freshness_to_dict(s: LopFreshnessSnapshot) -> dict[str, Any]:
    return {
        "last_heartbeat_age_sec": s.last_heartbeat_age_sec,
        "last_event_age_sec": s.last_event_age_sec,
        "last_file_change_age_sec": s.last_file_change_age_sec,
        "last_test_age_sec": s.last_test_age_sec,
        "last_evidence_age_sec": s.last_evidence_age_sec,
        "idle_duration_sec": s.idle_duration_sec,
        "context_usage_ratio": s.context_usage_ratio,
        "token_delta_recent": s.token_delta_recent,
        "worktree_head_changed": s.worktree_head_changed,
    }


def _freshness_from_dict(d: dict[str, Any]) -> LopFreshnessSnapshot:
    return LopFreshnessSnapshot(
        last_heartbeat_age_sec=(
            float(d["last_heartbeat_age_sec"]) if d.get("last_heartbeat_age_sec") is not None else None
        ),
        last_event_age_sec=(
            float(d["last_event_age_sec"]) if d.get("last_event_age_sec") is not None else None
        ),
        last_file_change_age_sec=(
            float(d["last_file_change_age_sec"]) if d.get("last_file_change_age_sec") is not None else None
        ),
        last_test_age_sec=(
            float(d["last_test_age_sec"]) if d.get("last_test_age_sec") is not None else None
        ),
        last_evidence_age_sec=(
            float(d["last_evidence_age_sec"]) if d.get("last_evidence_age_sec") is not None else None
        ),
        idle_duration_sec=(
            float(d["idle_duration_sec"]) if d.get("idle_duration_sec") is not None else None
        ),
        context_usage_ratio=(
            float(d["context_usage_ratio"]) if d.get("context_usage_ratio") is not None else None
        ),
        token_delta_recent=(
            int(d["token_delta_recent"]) if d.get("token_delta_recent") is not None else None
        ),
        worktree_head_changed=bool(d.get("worktree_head_changed", False)),
    )


def _recovery_to_dict(s: LopRecoverySnapshot) -> dict[str, Any]:
    return {
        "chaos_case_count": s.chaos_case_count,
        "chaos_passed_count": s.chaos_passed_count,
        "recovery_time_sec": s.recovery_time_sec,
        "route_accuracy": s.route_accuracy,
        "residual_risk": s.residual_risk,
        "context_route_reason": s.context_route_reason,
        "resume_packet_path": s.resume_packet_path,
    }


def _recovery_from_dict(d: dict[str, Any]) -> LopRecoverySnapshot:
    return LopRecoverySnapshot(
        chaos_case_count=int(d.get("chaos_case_count", 0)),
        chaos_passed_count=int(d.get("chaos_passed_count", 0)),
        recovery_time_sec=(
            float(d["recovery_time_sec"]) if d.get("recovery_time_sec") is not None else None
        ),
        route_accuracy=_maybe_bool(d, "route_accuracy"),
        residual_risk=str(d.get("residual_risk", "")),
        context_route_reason=str(d.get("context_route_reason", "")),
        resume_packet_path=str(d.get("resume_packet_path", "")),
    )


def _lop_metric_to_dict(s: LopMetricSnapshot) -> dict[str, Any]:
    return {
        "state": s.state,
        "recommended_action": s.recommended_action,
        "why_not_done_count": s.why_not_done_count,
        "blocking_why_not_done_count": s.blocking_why_not_done_count,
        "missing_evidence_count": s.missing_evidence_count,
        "required_event_missing_count": s.required_event_missing_count,
        "next_required_event": s.next_required_event,
        "observed_route": s.observed_route,
        "route_reason": s.route_reason,
        "freshness": _freshness_to_dict(s.freshness),
        "recovery": _recovery_to_dict(s.recovery),
    }


def _lop_metric_from_dict(d: dict[str, Any]) -> LopMetricSnapshot:
    return LopMetricSnapshot(
        state=str(d.get("state", "healthy")),
        recommended_action=str(d.get("recommended_action", "continuation")),
        why_not_done_count=int(d.get("why_not_done_count", 0)),
        blocking_why_not_done_count=int(d.get("blocking_why_not_done_count", 0)),
        missing_evidence_count=int(d.get("missing_evidence_count", 0)),
        required_event_missing_count=int(d.get("required_event_missing_count", 0)),
        next_required_event=str(d.get("next_required_event", "")),
        observed_route=str(d.get("observed_route", "")),
        route_reason=str(d.get("route_reason", "")),
        freshness=_freshness_from_dict(d.get("freshness") or {}),
        recovery=_recovery_from_dict(d.get("recovery") or {}),
    )


def _validity_to_dict(s: ValidityTriageSnapshot) -> dict[str, Any]:
    return {
        "status": s.status,
        "risk_labels": list(s.risk_labels),
        "evidence_debt": list(s.evidence_debt),
        "recommended_probe": dict(s.recommended_probe),
        "allowed_claims": list(s.allowed_claims),
        "blocked_claims": list(s.blocked_claims),
        "protected_paths_touched": list(s.protected_paths_touched),
    }


def _validity_from_dict(d: dict[str, Any]) -> ValidityTriageSnapshot:
    return ValidityTriageSnapshot(
        status=str(d.get("status", "pass")),
        risk_labels=[str(item) for item in (d.get("risk_labels") or [])],
        evidence_debt=[
            item for item in (d.get("evidence_debt") or [])
            if isinstance(item, dict)
        ],
        recommended_probe={
            str(k): str(v)
            for k, v in (d.get("recommended_probe") or {}).items()
        },
        allowed_claims=[str(item) for item in (d.get("allowed_claims") or [])],
        blocked_claims=[str(item) for item in (d.get("blocked_claims") or [])],
        protected_paths_touched=[
            str(item) for item in (d.get("protected_paths_touched") or [])
        ],
    )


def _score_to_dict(s: ScoreSnapshot) -> dict[str, Any]:
    return {
        "total": s.total,
        "components": dict(s.components),
        "missing_inputs": list(s.missing_inputs),
        "notes": list(s.notes),
    }


def _score_from_dict(d: dict[str, Any]) -> ScoreSnapshot:
    components: dict[str, float | None] = {}
    for key, value in (d.get("components") or {}).items():
        components[str(key)] = float(value) if value is not None else None
    return ScoreSnapshot(
        total=float(d["total"]) if d.get("total") is not None else None,
        components=components,
        missing_inputs=[str(item) for item in (d.get("missing_inputs") or [])],
        notes=[str(item) for item in (d.get("notes") or [])],
    )


def _autoresearch_eval_to_dict(s: AutoresearchEvalMetrics) -> dict[str, Any]:
    return {
        "metric_sources": dict(s.metric_sources),
        "autoresearch": _autoresearch_metric_to_dict(s.autoresearch),
        "eval": _eval_metric_to_dict(s.eval),
        "lop": _lop_metric_to_dict(s.lop),
        "validity": _validity_to_dict(s.validity),
        "product_score": _score_to_dict(s.product_score),
        "instrument_score": _score_to_dict(s.instrument_score),
    }


def _autoresearch_eval_from_dict(d: dict[str, Any] | None) -> AutoresearchEvalMetrics:
    if not isinstance(d, dict):
        return AutoresearchEvalMetrics()
    sources = default_metric_sources()
    sources.update({
        str(k): str(v)
        for k, v in (d.get("metric_sources") or {}).items()
    })
    return AutoresearchEvalMetrics(
        metric_sources=sources,
        autoresearch=_autoresearch_metric_from_dict(d.get("autoresearch") or {}),
        eval=_eval_metric_from_dict(d.get("eval") or {}),
        lop=_lop_metric_from_dict(d.get("lop") or {}),
        validity=_validity_from_dict(d.get("validity") or {}),
        product_score=_score_from_dict(d.get("product_score") or {}),
        instrument_score=_score_from_dict(d.get("instrument_score") or {}),
    )


def record_to_dict(rec: IterationRecord) -> dict[str, Any]:
    return {
        "iter": rec.iter,
        "started_at": rec.started_at,
        "scenario": rec.scenario,
        "run_id": rec.run_id,
        "run_status": rec.run_status,
        "outcome": rec.outcome,
        "tasks_done": rec.tasks_done,
        "expected_done": rec.expected_done,
        "eval": _eval_to_dict(rec.eval),
        "delta": _delta_to_dict(rec.delta) if rec.delta is not None else None,
        "reflect": _reflect_to_dict(rec.reflect) if rec.reflect is not None else None,
        "git_head": rec.git_head,
        "head_changed_since_prev": rec.head_changed_since_prev,
        "summary": rec.summary,
        "stop_reason": rec.stop_reason,
        "final_status_if_stopped": rec.final_status_if_stopped,
        "validation_kinds": list(rec.validation_kinds),
        "rework_count": rec.rework_count,
        "passed_after_rework": rec.passed_after_rework,
        "pending_rework_count": rec.pending_rework_count,
        "screenshot_path": rec.screenshot_path,
        "screenshot_error": rec.screenshot_error,
        "review_gate": dict(rec.review_gate),
        "autoresearch_eval": _autoresearch_eval_to_dict(rec.autoresearch_eval),
    }


def record_from_dict(d: dict[str, Any]) -> IterationRecord:
    return IterationRecord(
        iter=int(d["iter"]),
        started_at=str(d["started_at"]),
        scenario=str(d["scenario"]),
        run_id=str(d["run_id"]),
        run_status=str(d["run_status"]),
        outcome=str(d.get("outcome", "")),
        tasks_done=int(d["tasks_done"]),
        expected_done=int(d["expected_done"]),
        eval=_eval_from_dict(d["eval"]),
        delta=_delta_from_dict(d["delta"]) if d.get("delta") is not None else None,
        reflect=_reflect_from_dict(d["reflect"]) if d.get("reflect") is not None else None,
        git_head=str(d["git_head"]),
        head_changed_since_prev=bool(d["head_changed_since_prev"]),
        summary=str(d["summary"]),
        stop_reason=str(d.get("stop_reason", "")),
        final_status_if_stopped=str(d.get("final_status_if_stopped", "")),
        validation_kinds=[
            str(item) for item in (d.get("validation_kinds") or [])
        ],
        rework_count=int(d.get("rework_count", 0)),
        passed_after_rework=int(d.get("passed_after_rework", 0)),
        pending_rework_count=int(d.get("pending_rework_count", 0)),
        screenshot_path=(d.get("screenshot_path") or None),
        screenshot_error=str(d.get("screenshot_error", "")),
        review_gate=(
            dict(d.get("review_gate") or {})
            if isinstance(d.get("review_gate") or {}, dict)
            else {}
        ),
        autoresearch_eval=_autoresearch_eval_from_dict(d.get("autoresearch_eval")),
    )


def append_journal_entry(journal_path: Path, record: IterationRecord) -> None:
    """Append a single IterationRecord as a JSONL row. Creates parent
    directories if missing. ensure_ascii=False to preserve zh-CN."""
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record_to_dict(record), ensure_ascii=False)
    with journal_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

__all__ = [
    "LoopConfig",
    "default_metric_sources",
    "AutoresearchMetricSnapshot",
    "EvalMetricSnapshot",
    "LopFreshnessSnapshot",
    "LopRecoverySnapshot",
    "LopMetricSnapshot",
    "ValidityTriageSnapshot",
    "ScoreSnapshot",
    "AutoresearchEvalMetrics",
    "EvalSnapshot",
    "EvalDelta",
    "ReflectionResult",
    "IterationRecord",
    "LoopResult",
    "record_to_dict",
    "record_from_dict",
    "append_journal_entry",
]
