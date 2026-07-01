"""Autoresearch loop orchestration driver."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zf.autoresearch.artifacts import write_reflection_artifacts
from zf.autoresearch.eval_result import EvalResult, GateResult
from zf.autoresearch.loop_eval import (
    collect_autoresearch_eval_metrics,
    compute_eval_delta,
)
from zf.autoresearch.loop_reflect import build_reflection_prompt
from zf.autoresearch.loop_screenshot import ScreenshotResult
from zf.autoresearch.loop_types import (
    EvalDelta,
    EvalSnapshot,
    IterationRecord,
    LoopConfig,
    LoopResult,
    ReflectionResult,
    append_journal_entry,
)


SUCCESS_RUN_STATUSES = frozenset({"passed", "passed_after_rework"})


def _is_success_run_status(status: str) -> bool:
    return str(status or "") in SUCCESS_RUN_STATUSES


def _normalized_outcome(status: str) -> str:
    status = str(status or "")
    if status in SUCCESS_RUN_STATUSES:
        return "passed"
    if status == "fatal":
        return "fatal"
    return "failed"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass
class LoopState:
    """Mutable running state the driver threads through iterations.

    Not frozen because the driver mutates these counters each loop.
    Termination decisions read from this snapshot deterministically.
    """
    cost_usd_so_far: float = 0.0
    consecutive_passed: int = 0
    consecutive_regressed: int = 0


@dataclass(frozen=True)
class LoopTerminationDecision:
    should_stop: bool
    final_status: str   # done | converged | budget_exhausted | no_progress | max_iter_unmet | running
    reason: str = ""


def should_stop_loop(
    *,
    record: IterationRecord,
    cfg: LoopConfig,
    state: LoopState,
) -> LoopTerminationDecision:
    """Decide whether to terminate the loop after this iteration.

    Priority (high-to-low):
      1. budget_exhausted — cost_usd_so_far >= cfg.budget_usd
      2. max_iter         — record.iter >= cfg.max_iterations
      3. no_progress      — 3 consecutive regressed deltas
      4. converged        — 2 consecutive passed + improved
      5. otherwise        — keep running
    """
    if state.cost_usd_so_far >= cfg.budget_usd:
        return LoopTerminationDecision(
            should_stop=True,
            final_status="budget_exhausted",
            reason=f"cost {state.cost_usd_so_far:.2f} >= budget {cfg.budget_usd:.2f}",
        )

    if record.iter >= cfg.max_iterations:
        if (
            _is_success_run_status(record.run_status)
            and record.tasks_done >= record.expected_done
        ):
            return LoopTerminationDecision(
                should_stop=True,
                final_status="done",
                reason=(
                    "stop_reason=max_iterations; outcome=passed; "
                    f"tasks_done={record.tasks_done}/{record.expected_done}"
                ),
            )
        return LoopTerminationDecision(
            should_stop=True,
            final_status="max_iter_unmet",
            reason=(
                "stop_reason=max_iterations; outcome=failed; "
                f"tasks_done={record.tasks_done}/{record.expected_done}; "
                f"missing_done={max(record.expected_done - record.tasks_done, 0)}"
            ),
        )

    if state.consecutive_regressed >= 3:
        return LoopTerminationDecision(
            should_stop=True,
            final_status="no_progress",
            reason=f"{state.consecutive_regressed} consecutive regressed deltas",
        )

    if state.consecutive_passed >= 2:
        return LoopTerminationDecision(
            should_stop=True,
            final_status="converged",
            reason=f"{state.consecutive_passed} consecutive passed iter with improved delta",
        )

    return LoopTerminationDecision(
        should_stop=False, final_status="running", reason="",
    )

def _one_line_summary(
    *, iter: int, status: str, tasks_done: int, expected: int,
    eval: EvalSnapshot, delta: EvalDelta | None,
    reflect: ReflectionResult | None,
    screenshot: str | None = None,
) -> str:
    """Single-line console + journal summary."""
    eval_part = (
        f"healthy={eval.healthy_metrics} "
        f"critical={eval.critical_metrics} "
        f"backlog={eval.open_backlog_count}"
    )
    delta_part = (
        f"Δ={delta.verdict} (critical{delta.critical_delta:+d})"
        if delta else "Δ=baseline"
    )
    reflect_part = (
        f"reflect={reflect.verdict}/{reflect.risk}"
        if reflect else "reflect=skipped"
    )
    shot_part = f" · shot={screenshot}" if screenshot else ""
    return (
        f"iter {iter}: {status} {tasks_done}/{expected} · "
        f"{eval_part} · {delta_part} · {reflect_part}{shot_part}"
    )


def _render_iteration_md(rec: IterationRecord) -> str:
    """Per-iteration markdown report shown to operators in iter-NNN.md."""
    ae = rec.autoresearch_eval
    lines = [
        f"# Iteration {rec.iter} · {rec.scenario}",
        "",
        f"- **Started**: {rec.started_at}",
        f"- **Run ID**: {rec.run_id}",
        f"- **Status**: {rec.run_status}",
        f"- **Outcome**: {rec.outcome or 'not_collected'}",
        f"- **Tasks done**: {rec.tasks_done} / {rec.expected_done}",
        f"- **Validation**: {', '.join(rec.validation_kinds) if rec.validation_kinds else 'not_collected'}",
        f"- **Rework**: total={rec.rework_count}, passed_after_rework={rec.passed_after_rework}, pending={rec.pending_rework_count}",
        f"- **Stop reason**: {rec.stop_reason or 'not_stopped'}",
        f"- **Git HEAD**: {rec.git_head}",
        f"- **HEAD changed since prev**: {rec.head_changed_since_prev}",
        "",
        "## Eval snapshot",
        "",
        f"- healthy_metrics: {rec.eval.healthy_metrics}",
        f"- warning_metrics: {rec.eval.warning_metrics}",
        f"- critical_metrics: {rec.eval.critical_metrics}",
        f"- coordinator_ratio: {rec.eval.coordinator_ratio:.3f}",
        f"- open_backlog: {rec.eval.open_backlog_count}",
        f"- rework_looped: {rec.eval.rework_looped}",
        f"- completed_tasks: {rec.eval.completed_tasks}",
        "",
        "## Autoresearch / Eval / LOP metrics",
        "",
        "### Autoresearch",
        "",
        f"- harness_profile: {ae.autoresearch.harness_profile}",
        f"- boundary: {ae.autoresearch.boundary}",
        f"- effective_profile: {ae.autoresearch.effective_profile}",
        f"- strict_escalated: {ae.autoresearch.strict_escalated}",
        f"- strict_trigger_reason: {ae.autoresearch.strict_trigger_reason or 'not_collected'}",
        f"- work_unit_count: {ae.autoresearch.work_unit_count}",
        f"- split_quality_blockers: {ae.autoresearch.split_quality_blockers}",
        f"- integration_required: {ae.autoresearch.integration_required}",
        "",
        "### Eval",
        "",
        f"- functionality_score: {ae.eval.functionality_score}",
        f"- evidence_quality_score: {ae.eval.evidence_quality_score}",
        f"- architecture_compliance_score: {ae.eval.architecture_compliance_score}",
        f"- regression_risk_score: {ae.eval.regression_risk_score}",
        f"- verdict: {ae.eval.verdict}",
        f"- required_command_passed: {ae.eval.required_command_passed}",
        f"- terminal_evidence_present: {ae.eval.terminal_evidence_present}",
        f"- quality_gates_passed: {ae.eval.quality_gates_passed}",
        f"- clean_state_passed: {ae.eval.clean_state_passed}",
        f"- product_rework_count: {ae.eval.product_rework_count}",
        f"- evidence_reissue_count: {ae.eval.evidence_reissue_count}",
        "",
        "### Validity",
        "",
        f"- status: {ae.validity.status}",
        f"- risk_labels: {ae.validity.risk_labels}",
        f"- protected_paths_touched: {ae.validity.protected_paths_touched}",
        f"- blocked_claims: {ae.validity.blocked_claims}",
        f"- recommended_probe: {ae.validity.recommended_probe}",
        "",
        "### LOP",
        "",
        f"- state: {ae.lop.state}",
        f"- recommended_action: {ae.lop.recommended_action}",
        f"- why_not_done_count: {ae.lop.why_not_done_count}",
        f"- blocking_why_not_done_count: {ae.lop.blocking_why_not_done_count}",
        f"- missing_evidence_count: {ae.lop.missing_evidence_count}",
        f"- required_event_missing_count: {ae.lop.required_event_missing_count}",
        f"- next_required_event: {ae.lop.next_required_event}",
        f"- route_reason: {ae.lop.route_reason}",
        f"- last_heartbeat_age_sec: {ae.lop.freshness.last_heartbeat_age_sec}",
        f"- last_event_age_sec: {ae.lop.freshness.last_event_age_sec}",
        f"- last_test_age_sec: {ae.lop.freshness.last_test_age_sec}",
        f"- context_usage_ratio: {ae.lop.freshness.context_usage_ratio}",
        f"- worktree_head_changed: {ae.lop.freshness.worktree_head_changed}",
        "",
        "### Review Gate",
        "",
    ]
    if rec.review_gate:
        lines += [
            f"- mode: {rec.review_gate.get('mode', '')}",
            f"- status: {rec.review_gate.get('status', '')}",
            f"- triggered: {rec.review_gate.get('triggered', False)}",
            f"- route: {rec.review_gate.get('route', '')}",
            f"- severity: {rec.review_gate.get('severity', '')}",
            f"- reason: {rec.review_gate.get('reason', '')}",
            f"- artifact_refs: {rec.review_gate.get('artifact_refs') or {}}",
            "",
        ]
    else:
        lines += ["- status: not_collected", ""]
    lines += [
        "### Scores",
        "",
        f"- product_score: {ae.product_score.total}",
        f"- product_components: {ae.product_score.components}",
        f"- product_missing_inputs: {ae.product_score.missing_inputs}",
        f"- instrument_score: {ae.instrument_score.total}",
        f"- instrument_components: {ae.instrument_score.components}",
        f"- instrument_missing_inputs: {ae.instrument_score.missing_inputs}",
        "",
    ]
    if rec.delta is not None:
        lines += [
            "## Delta vs prev iter",
            "",
            f"- verdict: **{rec.delta.verdict}**",
            f"- healthy: {rec.delta.healthy_delta:+d}",
            f"- critical: {rec.delta.critical_delta:+d}",
            f"- coordinator: {rec.delta.coordinator_delta:+.3f}",
            f"- backlog: {rec.delta.backlog_delta:+d}",
            f"- completed: {rec.delta.completed_delta:+d}",
            "",
        ]
    else:
        lines += ["## Delta vs prev iter", "", "（首轮，无基线）", ""]
    if rec.reflect is not None:
        alt_block = "\n".join(f"- {a}" for a in rec.reflect.alternatives) or "（无）"
        lines += [
            "## Reflection",
            "",
            f"- **Verdict**: {rec.reflect.verdict}",
            f"- **Risk**: {rec.reflect.risk}",
            f"- **Rec for next iter**: {rec.reflect.rec_for_next_iter}",
            "",
            "### Alternatives",
            "",
            alt_block,
            "",
        ]
    if rec.screenshot_path:
        lines += [
            "## Web kanban screenshot",
            "",
            f"![iter {rec.iter} kanban]({rec.screenshot_path})",
            "",
        ]
    elif rec.screenshot_error:
        lines += [
            "## Web kanban screenshot",
            "",
            f"⚠ capture failed: {rec.screenshot_error}",
            "",
        ]
    lines += ["## Summary", "", rec.summary, ""]
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_id(iter: int, scenario: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"loop-{iter:03d}-{scenario}-{stamp}"


def _metrics_state_dir(ar_result: dict[str, Any], fallback: Path) -> Path:
    """Return the runtime state tree that describes this iteration.

    Normal autoresearch evaluates the parent project. In bypass mode the
    inner harness owns the relevant events/kanban/health files, so the
    bypass runner reports its state_dir explicitly.
    """
    raw = ar_result.get("state_dir") or ar_result.get("metrics_state_dir")
    if raw:
        return Path(str(raw))
    return fallback


def _score_from_record(record: IterationRecord) -> dict[str, float]:
    completion = (
        min(100.0, (record.tasks_done / record.expected_done) * 100.0)
        if record.expected_done > 0 else 0.0
    )
    passed = _is_success_run_status(record.run_status)
    critical_penalty = min(float(record.eval.critical_metrics) * 10.0, 50.0)
    rework_penalty = min(float(record.rework_count) * 5.0, 30.0)
    context_ratio = record.autoresearch_eval.lop.freshness.context_usage_ratio
    context_score = 80.0
    if context_ratio is not None:
        context_score = max(0.0, 100.0 - max(0.0, context_ratio - 0.65) * 200.0)
    return {
        "correctness": completion if passed else min(completion, 60.0),
        "regression": max(0.0, 90.0 - critical_penalty),
        "stability": max(0.0, (90.0 if passed else 45.0) - critical_penalty),
        "harness_recovery": max(0.0, (85.0 if passed else 40.0) - rework_penalty),
        "context_safety": round(context_score, 2),
        "coordination": 80.0 if record.pending_rework_count == 0 else 50.0,
        "cost_efficiency": 60.0,
        "learning_value": 80.0 if record.reflect is not None else 45.0,
    }


def _write_experiment_record(
    *,
    state_dir: Path,
    record: IterationRecord,
    prev: IterationRecord | None,
    eval_result: EvalResult,
    eval_path: Path,
    trace_refs: list[str],
) -> Path:
    path = state_dir / "autoresearch" / "experiments" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "experiment_id": record.run_id,
        "parent_id": prev.run_id if prev is not None else "",
        "kind": "loop_iteration",
        "hypothesis": record.summary,
        "status": "scored",
        "gate_status": eval_result.gate_status,
        "score_total": eval_result.total_score,
        "eval_result_ref": str(eval_path),
        "trace_refs": trace_refs,
        "created_at": record.started_at,
        "updated_at": _now_iso(),
        "metadata": {
            "scenario": record.scenario,
            "iteration": record.iter,
            "run_status": record.run_status,
            "tasks_done": record.tasks_done,
            "expected_done": record.expected_done,
            "review_gate": dict(record.review_gate),
        },
    }
    row = {"event_type": "autoresearch.experiment.iteration", "payload": payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _write_iteration_eval_artifacts(
    *,
    cfg: LoopConfig,
    output_dir: Path,
    record: IterationRecord,
    prev: IterationRecord | None,
    iter_md_path: Path,
    journal_path: Path,
) -> tuple[Path, Path]:
    passed = (
        _is_success_run_status(record.run_status)
        and record.tasks_done >= record.expected_done
    )
    artifact_refs = write_reflection_artifacts(
        output_dir,
        record,
        evidence_refs=[str(journal_path), str(iter_md_path)],
        state_dir=cfg.parent_state_dir or output_dir,
    )
    artifact_path_refs = {
        key: str(value)
        for key, value in artifact_refs.items()
        if isinstance(value, str) and value
    }
    artifact_sidecar_refs = (
        artifact_refs.get("sidecar_refs")
        if isinstance(artifact_refs.get("sidecar_refs"), dict) else {}
    )
    review_gate_refs = [
        str(ref)
        for ref in (record.review_gate.get("artifact_refs") or {}).values()
        if str(ref)
    ]
    evidence_refs = {
        "journal": [str(journal_path)],
        "iteration": [str(iter_md_path)],
        "artifacts": list(artifact_path_refs.values()),
    }
    if artifact_sidecar_refs:
        evidence_refs["artifact_sidecar_refs"] = list(artifact_sidecar_refs.values())
    if review_gate_refs:
        evidence_refs["review_gate"] = review_gate_refs
    result = EvalResult(
        result_id=f"{record.run_id}-iter-{record.iter:03d}",
        scenario_id=record.scenario,
        mode="candidate",
        experiment_id=record.run_id,
        gates=[
            GateResult(
                name="autoresearch_loop_iteration",
                status="passed" if passed else "failed",
                reason=(
                    f"status={record.run_status}; "
                    f"tasks_done={record.tasks_done}/{record.expected_done}"
                ),
                evidence_refs=[str(journal_path), str(iter_md_path)],
            )
        ],
        scores=_score_from_record(record),
        evidence_refs=evidence_refs,
        metadata={
            "source": "autoresearch_loop",
            "iteration": record.iter,
            "run_status": record.run_status,
            "outcome": record.outcome,
            "delta": record.delta.verdict if record.delta is not None else "baseline",
            "reflection_artifacts": artifact_path_refs,
            "reflection_artifact_refs": artifact_sidecar_refs,
            "review_gate": dict(record.review_gate),
        },
    )
    eval_path = output_dir / "eval-results" / f"iter-{record.iter:03d}.json"
    result.write(eval_path)
    graph_path = _write_experiment_record(
        state_dir=cfg.parent_state_dir or output_dir,
        record=record,
        prev=prev,
        eval_result=result,
        eval_path=eval_path,
        trace_refs=[
            str(journal_path),
            str(iter_md_path),
            *artifact_path_refs.values(),
            *review_gate_refs,
        ],
    )
    return eval_path, graph_path


def run_loop(
    cfg: LoopConfig,
    *,
    autoresearch_fn: Callable[..., dict[str, Any]],
    eval_collector_fn: Callable[[Path], EvalSnapshot],
    reflect_fn: Callable[..., ReflectionResult],
    git_head_fn: Callable[[Path], str],
    git_diff_fn: Callable[[Path, str], str],
    backlog_fn: Callable[[Path], list[dict[str, Any]]],
    wait_for_fix_fn: Callable[..., bool],
    screenshot_fn: Callable[..., ScreenshotResult] | None = None,
) -> LoopResult:
    """Drive the autoresearch ↔ eval ↔ reflect closed loop.

    All side effects (autoresearch run / git / LLM / backlog read / wait)
    are injected so unit tests can stub them out. The driver itself is
    pure orchestration + IO to journal/markdown.
    """
    if not cfg.scenarios:
        raise ValueError("LoopConfig.scenarios must not be empty")
    if cfg.parent_state_dir is None:
        raise ValueError("LoopConfig.parent_state_dir must be resolved before run_loop")
    if cfg.output_dir is None:
        raise ValueError("LoopConfig.output_dir must be resolved before run_loop")

    parent_state_dir = cfg.parent_state_dir
    output_dir = cfg.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / "journal.jsonl"
    report_path = output_dir / "report.md"

    state = LoopState()
    prev: IterationRecord | None = None
    final_status = "running"

    for i in range(1, cfg.max_iterations + 1):
        scenario = cfg.scenarios[(i - 1) % len(cfg.scenarios)]
        run_id = _default_run_id(i, scenario)
        started_at = _now_iso()
        prev_head = prev.git_head if prev else ""

        # a. autoresearch run
        ar = autoresearch_fn(
            scenario=scenario,
            run_id=run_id,
            cfg=cfg,
        )
        iteration_state_dir = _metrics_state_dir(ar, parent_state_dir)

        # b. eval snapshot
        snap = eval_collector_fn(iteration_state_dir)

        # c. delta
        delta = (
            compute_eval_delta(prev.eval, snap) if prev is not None else None
        )

        # d. git
        head = git_head_fn(parent_state_dir)
        diff = git_diff_fn(parent_state_dir, prev_head) if prev_head else ""
        head_changed = prev_head != "" and prev_head != head

        # e. backlog snapshot
        backlog = backlog_fn(iteration_state_dir)
        autoresearch_eval = collect_autoresearch_eval_metrics(
            iteration_state_dir,
            eval_snapshot=snap,
            run_status=str(ar.get("status", "unknown")),
            head_changed_since_prev=head_changed,
        )

        # e2. playwright kanban screenshot (§9) — optional; runs before
        # reflection so the screenshot path is visible in iter-NNN.md.
        screenshot_rel: str | None = None
        screenshot_err: str = ""
        if screenshot_fn is not None and cfg.screenshot_url:
            screenshot_target = output_dir / f"iter-{i:03d}.png"
            shot = screenshot_fn(
                url=cfg.screenshot_url,
                output_path=screenshot_target,
                shot_js_path=cfg.screenshot_shot_js,
                docker_image=cfg.screenshot_docker_image,
            )
            if shot.ok and shot.path is not None:
                screenshot_rel = shot.path.name
            else:
                screenshot_err = shot.error

        # f. reflection
        run_status = str(ar.get("status", "unknown"))
        review_gate = ar.get("review_gate") or {}
        if not isinstance(review_gate, dict):
            review_gate = {}
        record_in_progress = IterationRecord(
            iter=i, started_at=started_at, scenario=scenario,
            run_id=run_id, run_status=run_status,
            tasks_done=int(ar.get("tasks_done", 0)),
            expected_done=int(ar.get("expected_done", 0)),
            eval=snap, delta=delta, reflect=None,
            git_head=head,
            head_changed_since_prev=head_changed,
            summary="",
            outcome=_normalized_outcome(run_status),
            validation_kinds=_string_list(ar.get("validation_kinds")),
            rework_count=int(ar.get("rework_count", 0) or 0),
            passed_after_rework=int(ar.get("passed_after_rework", 0) or 0),
            pending_rework_count=int(ar.get("pending_rework_count", 0) or 0),
            screenshot_path=screenshot_rel,
            screenshot_error=screenshot_err,
            review_gate=review_gate,
            autoresearch_eval=autoresearch_eval,
        )
        prompt = build_reflection_prompt(
            curr=record_in_progress, prev=prev,
            git_diff=diff, open_backlog=backlog,
        )
        reflect = reflect_fn(prompt, backend=cfg.reflect_backend)

        # g. finalize record + summary
        summary = _one_line_summary(
            iter=i,
            status=record_in_progress.run_status,
            tasks_done=record_in_progress.tasks_done,
            expected=record_in_progress.expected_done,
            eval=snap, delta=delta, reflect=reflect,
            screenshot=screenshot_rel,
        )
        record = replace(record_in_progress, reflect=reflect, summary=summary)

        # h. update state (streaks based on delta verdict + run_status)
        if _is_success_run_status(record.run_status) and (
            delta is None or delta.verdict in {"improved", "unchanged"}
        ):
            state.consecutive_passed += 1
        else:
            state.consecutive_passed = 0
        if delta is not None and delta.verdict == "regressed":
            state.consecutive_regressed += 1
        else:
            state.consecutive_regressed = 0

        # i. termination check
        decision = should_stop_loop(record=record, cfg=cfg, state=state)
        if decision.should_stop:
            final_status = decision.final_status
            record = replace(
                record,
                stop_reason=decision.reason,
                final_status_if_stopped=decision.final_status,
            )
        # j. journal + per-iter md + structured eval/experiment artifacts
        append_journal_entry(journal_path, record)
        iter_md_path = output_dir / f"iter-{i:03d}.md"
        iter_md_path.write_text(_render_iteration_md(record))
        _write_iteration_eval_artifacts(
            cfg=cfg,
            output_dir=output_dir,
            record=record,
            prev=prev,
            iter_md_path=iter_md_path,
            journal_path=journal_path,
        )
        print(summary, flush=True)

        if decision.should_stop:
            prev = record
            break

        # k. wait for HEAD change (so inner harness can land a fix)
        if cfg.fix_wait_strategy != "none":
            wait_for_fix_fn(
                parent_state_dir=parent_state_dir,
                prev_head=head,
                strategy=cfg.fix_wait_strategy,
                timeout_seconds=cfg.fix_wait_timeout,
                git_head_fn=git_head_fn,
            )

        prev = record
    else:
        # Loop exited via the for-loop iterator (no break) → reached
        # cfg.max_iterations naturally.
        final_status = "max_iter_unmet"

    # Write final report.md (operator-friendly summary).
    _write_final_report(report_path, journal_path, final_status)

    return LoopResult(
        iterations=(prev.iter if prev else 0),
        final_status=final_status,
        journal_path=journal_path,
        report_path=report_path,
    )


def _write_final_report(report_path: Path, journal_path: Path, final_status: str) -> None:
    """Aggregate journal entries into a single markdown report."""
    lines = [
        "# autoresearch loop final report",
        "",
        f"- **Final status**: {final_status}",
        f"- **Journal**: {journal_path}",
        "",
        "## Iterations",
        "",
    ]
    if journal_path.exists():
        first_sources: dict[str, str] = {}
        for raw in journal_path.read_text().splitlines():
            try:
                d = json.loads(raw)
                ae = d.get("autoresearch_eval") or {}
                lop = ae.get("lop") or {}
                validity = ae.get("validity") or {}
                product = ae.get("product_score") or {}
                instrument = ae.get("instrument_score") or {}
                state = lop.get("state", "not_collected")
                action = lop.get("recommended_action", "not_collected")
                validity_status = validity.get("status", "not_collected")
                product_total = product.get("total")
                instrument_total = instrument.get("total")
                validation = ",".join(d.get("validation_kinds") or [])
                validation_part = f", validation={validation}" if validation else ""
                review_gate = d.get("review_gate") or {}
                review_part = ""
                if isinstance(review_gate, dict) and review_gate:
                    review_part = (
                        f", review_gate={review_gate.get('status', '')}"
                        f"/{review_gate.get('route', '')}"
                    )
                stop_part = (
                    f", stop_reason={d.get('stop_reason')}"
                    if d.get("stop_reason")
                    else ""
                )
                eval_ref = report_path.parent / "eval-results" / f"iter-{int(d['iter']):03d}.json"
                eval_part = f", eval_result={eval_ref}" if eval_ref.exists() else ""
                lines.append(
                    f"- iter {d['iter']}: {d['summary']} "
                    f"(lop={state}, action={action}, "
                    f"validity={validity_status}, "
                    f"product_score={product_total}, "
                    f"instrument_score={instrument_total}"
                    f"{validation_part}{review_part}{stop_part}{eval_part})"
                )
                if not first_sources:
                    first_sources = {
                        str(k): str(v)
                        for k, v in (ae.get("metric_sources") or {}).items()
                    }
            except Exception:
                continue
        if first_sources:
            lines += [
                "",
                "## Metric sources",
                "",
            ]
            for key in sorted(first_sources):
                lines.append(f"- {key}: `{first_sources[key]}`")
    report_path.write_text("\n".join(lines) + "\n")

__all__ = [
    "LoopState",
    "LoopTerminationDecision",
    "should_stop_loop",
    "run_loop",
    "_metrics_state_dir",
]
