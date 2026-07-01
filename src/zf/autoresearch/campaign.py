"""Autoresearch campaign definitions and plan generation."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from zf.autoresearch.review_gate import normalize_review_gate_mode
from zf.autoresearch.scenarios import resolve_scenario, scenario_names


@dataclass(frozen=True)
class CampaignScenario:
    scenario: str
    purpose: str
    metrics: tuple[str, ...]
    hard_assertions: tuple[str, ...]
    budget_usd: float


@dataclass(frozen=True)
class AutoresearchCampaign:
    name: str
    description: str
    scenarios: tuple[CampaignScenario, ...]
    pass_criteria: tuple[str, ...]


@dataclass(frozen=True)
class CampaignPlanPaths:
    output_dir: Path
    json_path: Path
    markdown_path: Path
    script_path: Path


HARNESS_HARDENING_CAMPAIGN = AutoresearchCampaign(
    name="harness-hardening",
    description=(
        "Targeted autoresearch campaign for long-horizon harness acceptance: "
        "parallel dev pressure, stuck recovery, rework convergence, and manual "
        "intervention guardrails."
    ),
    scenarios=(
        CampaignScenario(
            scenario="positive-pressure-4dev",
            purpose="Exercise four dev replicas and full terminal gates.",
            metrics=(
                "tasks_done",
                "dev_replicas_used",
                "test_replicas_used",
                "duplicate_success_event_count",
                "terminal_evidence_coverage",
            ),
            hard_assertions=(
                "tasks_done >= 4",
                "fatal_count == 0",
                "duplicate_success_event_count == 0",
                "terminal_evidence_coverage == 1.0",
            ),
            budget_usd=260.0,
        ),
        CampaignScenario(
            scenario="controlled-stuck-recovery",
            purpose="Validate heartbeat-driven stuck detection and recovery.",
            metrics=(
                "stuck_injection_requested_count",
                "worker_stuck_count",
                "worker_stuck_recovered_count",
                "worker_stuck_recovery_failed_count",
                "tasks_done",
            ),
            hard_assertions=(
                "stuck_injection_requested_count >= 1",
                "worker_stuck_count >= 1",
                "worker_stuck_recovered_count >= 1",
                "worker_stuck_recovery_failed_count == 0",
                "tasks_done >= 1",
                "fatal_count == 0",
            ),
            budget_usd=180.0,
        ),
        CampaignScenario(
            scenario="fail-rework-converge",
            purpose="Verify fail-closed review/test/judge feedback converges.",
            metrics=(
                "rework_signal_count",
                "task_done_blocked_count",
                "tasks_done",
                "discriminator_failed_count",
            ),
            hard_assertions=(
                "rework_signal_count >= 1 or task_done_blocked_count >= 1",
                "tasks_done >= 1",
                "fatal_count == 0",
            ),
            budget_usd=220.0,
        ),
        CampaignScenario(
            scenario="manual-intervention-guard",
            purpose="Check external/manual state changes cannot bypass truth.",
            metrics=(
                "invalid_transition_count",
                "task_done_blocked_count",
                "tasks_done",
                "terminal_evidence_coverage",
            ),
            hard_assertions=(
                "tasks_done >= 1",
                "fatal_count == 0",
                "terminal_evidence_coverage == 1.0",
            ),
            budget_usd=180.0,
        ),
    ),
    pass_criteria=(
        "All scenarios write report.md and events-summary.json.",
        "No scenario records fatal_count > 0.",
        "No scenario records duplicate_success_event_count > 0.",
        "All terminal tasks have task.done.evidence.",
        "Controlled stuck recovery records an injected worker.stuck and worker.stuck.recovered.",
        "Controlled stuck recovery records no worker.stuck.recovery_failed.",
    ),
)


FULL_VALIDATION_COMMON_METRICS = (
    "tasks_done",
    "expected_done",
    "fatal_count",
    "duplicate_success_event_count",
    "terminal_evidence_coverage",
)

FULL_VALIDATION_COMMON_ASSERTIONS = (
    "report.md exists",
    "events-summary.json exists",
    "tasks_done >= expected_done",
    "fatal_count == 0",
    "duplicate_success_event_count == 0",
    "terminal_evidence_coverage == 1.0",
    "inner-runner.log has no runner fatal",
)


def _full_validation_scenario(
    *,
    scenario: str,
    purpose: str,
    metrics: tuple[str, ...],
    hard_assertions: tuple[str, ...],
    budget_usd: float,
) -> CampaignScenario:
    return CampaignScenario(
        scenario=scenario,
        purpose=purpose,
        metrics=FULL_VALIDATION_COMMON_METRICS + metrics,
        hard_assertions=FULL_VALIDATION_COMMON_ASSERTIONS + hard_assertions,
        budget_usd=budget_usd,
    )


FULL_VALIDATION_CAMPAIGN = AutoresearchCampaign(
    name="full-validation",
    description=(
        "Deterministic full-validation autoresearch planning campaign. Phase 0 "
        "runs no-provider preflight checks, Phase 1 runs the controlled stuck "
        "single-scenario smoke first, and Phase 2 expands to all built-in "
        "scenarios with bounded budgets. This campaign only writes replayable "
        "plans; do not start real provider long runs from implementation tasks."
    ),
    scenarios=(
        _full_validation_scenario(
            scenario="controlled-stuck-recovery",
            purpose=(
                "First single-scenario smoke for heartbeat stuck detection and "
                "recovery before widening provider spend."
            ),
            metrics=(
                "stuck_injection_requested_count",
                "worker_stuck_count",
                "worker_stuck_recovered_count",
                "worker_stuck_recovery_failed_count",
                "stuck_injection_satisfied",
            ),
            hard_assertions=(
                "stuck_injection_requested_count >= 1",
                "worker_stuck_count >= 1",
                "worker_stuck_recovered_count >= 1",
                "worker_stuck_recovery_failed_count == 0",
                "stuck_injection_satisfied == true",
            ),
            budget_usd=180.0,
        ),
        _full_validation_scenario(
            scenario="positive-pressure-4dev",
            purpose=(
                "Exercise four independent dev lanes and terminal gates under "
                "parallel pressure."
            ),
            metrics=(
                "dev_replicas_used",
                "test_replicas_used",
            ),
            hard_assertions=(
                "len(dev_replicas_used) >= 4",
                "len(test_replicas_used) >= 1",
            ),
            budget_usd=260.0,
        ),
        _full_validation_scenario(
            scenario="fail-rework-converge",
            purpose=(
                "Verify a fail-closed review/test/judge signal routes bounded "
                "rework and still converges."
            ),
            metrics=(
                "rework_signal_count",
                "task_done_blocked_count",
                "discriminator_failed_count",
            ),
            hard_assertions=(
                "rework_signal_count >= 1 or task_done_blocked_count >= 1",
            ),
            budget_usd=220.0,
        ),
        _full_validation_scenario(
            scenario="manual-intervention-guard",
            purpose=(
                "Check manual or external terminal-state changes cannot bypass "
                "kernel evidence gates."
            ),
            metrics=(
                "invalid_transition_count",
                "task_done_blocked_count",
            ),
            hard_assertions=(
                "manual terminal done without evidence is blocked or audited",
            ),
            budget_usd=180.0,
        ),
        _full_validation_scenario(
            scenario="self-eval-backlog",
            purpose=(
                "Validate the self-eval backlog bridge, no-op pass path, repair "
                "contract evidence, and reproducible docs."
            ),
            metrics=(
                "done_evidence_count",
                "self_eval_failure_backlog_idempotent",
                "self_eval_pass_backlog_created",
            ),
            hard_assertions=(
                "failed self-eval backlog creation is idempotent",
                "passing self-eval does not create backlog",
                "repair contract and evidence are actionable",
                "docs/README usage evidence reaches terminal gates",
            ),
            budget_usd=340.0,
        ),
        _full_validation_scenario(
            scenario="spec-validate-hardening",
            purpose=(
                "Validate verification-literal and tdd_ref scope graph "
                "hardening through tests and runtime evidence."
            ),
            metrics=(
                "done_evidence_count",
                "verification_literal_regression_evidence",
                "tdd_ref_scope_regression_evidence",
            ),
            hard_assertions=(
                "verification literal hardening has regression tests and runtime evidence",
                "tdd_ref scope graph hardening has regression tests and runtime evidence",
            ),
            budget_usd=280.0,
        ),
    ),
    pass_criteria=(
        "Phase 0 no-provider preflight passes before any provider run.",
        "Phase 1 controlled-stuck-recovery passes before running the full set.",
        "Phase 2 scenarios run in the generated order with per-scenario budgets.",
        "All scenarios write report.md and events-summary.json.",
        "No scenario records fatal_count > 0.",
        "No scenario records duplicate_success_event_count > 0.",
        "All terminal tasks have task.done.evidence.",
        "Failures stop expansion and create or update a replayable backlog item.",
    ),
)


BUILTIN_CAMPAIGNS: dict[str, AutoresearchCampaign] = {
    HARNESS_HARDENING_CAMPAIGN.name: HARNESS_HARDENING_CAMPAIGN,
    FULL_VALIDATION_CAMPAIGN.name: FULL_VALIDATION_CAMPAIGN,
}


def campaign_names() -> list[str]:
    return sorted(BUILTIN_CAMPAIGNS)


def resolve_campaign(name: str) -> AutoresearchCampaign:
    campaign = BUILTIN_CAMPAIGNS.get(name)
    if campaign is None:
        known = ", ".join(campaign_names())
        raise ValueError(f"unknown autoresearch campaign {name!r}; known: {known}")
    for item in campaign.scenarios:
        if item.scenario not in scenario_names():
            raise ValueError(
                f"campaign {name!r} references unknown scenario {item.scenario!r}"
            )
    return campaign


def write_campaign_plan(
    *,
    campaign: AutoresearchCampaign,
    output_dir: Path,
    worktree_root: Path,
    config_template: Path,
    use_tmux: bool = True,
    review_gate: str = "off",
) -> CampaignPlanPaths:
    """Write a runnable campaign plan without starting provider CLIs."""
    review_gate_mode = normalize_review_gate_mode(review_gate)
    output_dir.mkdir(parents=True, exist_ok=True)
    worktree_root = worktree_root.resolve()
    config_template = config_template.resolve()
    scenarios = []
    for item in campaign.scenarios:
        scenario = resolve_scenario(item.scenario)
        worktree = worktree_root / item.scenario
        cmd = [
            "PYTHONPATH=\"$(pwd)/src\"",
            "python3",
            "-m",
            "zf.cli.main",
            "autoresearch",
            "run",
            "--scenario",
            item.scenario,
            "--worktree",
            str(worktree),
            "--config",
            str(config_template),
            "--expected-done",
            str(scenario.expected_done),
            "--timeout",
            str(scenario.timeout_seconds),
            "--budget-usd",
            f"{item.budget_usd:.1f}",
            "--backlog-on-failure",
            "--confirm",
        ]
        if review_gate_mode != "off":
            cmd.extend(["--review-gate", review_gate_mode])
        if use_tmux:
            cmd.append("--tmux")
            cmd.extend(["--tmux-session", f"zf-ar-{campaign.name}-{item.scenario}"])
        if item.scenario == "controlled-stuck-recovery":
            cmd.extend([
                "--inject-worker-stuck",
                "--inject-worker-stuck-instance",
                "dev-1",
            ])
        scenarios.append({
            **asdict(item),
            "expected_done": scenario.expected_done,
            "timeout_seconds": scenario.timeout_seconds,
            "worktree": str(worktree),
            "review_gate": review_gate_mode,
            "command": " ".join(_quote_command_parts(cmd)),
        })

    payload = {
        "campaign": campaign.name,
        "description": campaign.description,
        "worktree_root": str(worktree_root),
        "config_template": str(config_template),
        "review_gate": review_gate_mode,
        "pass_criteria": list(campaign.pass_criteria),
        "scenarios": scenarios,
    }
    json_path = output_dir / "campaign.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    markdown_path = output_dir / "campaign.md"
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")

    script_path = output_dir / "run-campaign.sh"
    script_path.write_text(_render_script(payload), encoding="utf-8")
    script_path.chmod(0o755)
    return CampaignPlanPaths(
        output_dir=output_dir,
        json_path=json_path,
        markdown_path=markdown_path,
        script_path=script_path,
    )


def _quote_command_parts(parts: list[str]) -> list[str]:
    out: list[str] = []
    for part in parts:
        if part.startswith("PYTHONPATH="):
            out.append(part)
        else:
            out.append(shlex.quote(part))
    return out


def _render_markdown(payload: dict) -> str:
    lines = [
        f"# Autoresearch Campaign: {payload['campaign']}",
        "",
        payload["description"],
        "",
        f"- worktree_root: `{payload['worktree_root']}`",
        f"- config_template: `{payload['config_template']}`",
        f"- review_gate: `{payload.get('review_gate', 'off')}`",
        "",
        "## Pass Criteria",
        "",
    ]
    for item in payload["pass_criteria"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Scenarios", ""])
    for scenario in payload["scenarios"]:
        lines.extend([
            f"### {scenario['scenario']}",
            "",
            scenario["purpose"],
            "",
            f"- expected_done: {scenario['expected_done']}",
            f"- timeout_seconds: {scenario['timeout_seconds']}",
            f"- budget_usd: {scenario['budget_usd']}",
            f"- review_gate: `{scenario.get('review_gate', 'off')}`",
            f"- worktree: `{scenario['worktree']}`",
            "",
            "Metrics:",
            "",
        ])
        for metric in scenario["metrics"]:
            lines.append(f"- `{metric}`")
        lines.extend(["", "Hard assertions:", ""])
        for assertion in scenario["hard_assertions"]:
            lines.append(f"- `{assertion}`")
        lines.extend(["", "Command:", "", "```bash", scenario["command"], "```", ""])
    return "\n".join(lines)


def _render_script(payload: dict) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'ROOT="${ZAOFU_ROOT:-/path/to/zaofu}"',
        'cd "$ROOT"',
        "",
    ]
    for scenario in payload["scenarios"]:
        lines.extend([
            f"echo '[autoresearch-campaign] {scenario['scenario']}'",
            scenario["command"],
            "",
        ])
    return "\n".join(lines)
