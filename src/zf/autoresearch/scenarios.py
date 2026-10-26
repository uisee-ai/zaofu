"""Built-in autoresearch scenarios.

The supervisor owns the outer evaluation loop. Scenario text is only the seed
for the inner harness; acceptance is still measured by deterministic events,
guards, and reports collected by the supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AutoresearchScenario:
    name: str
    seed_text: str
    expected_done: int
    timeout_seconds: int
    description: str = ""
    requires_worker_stuck_injection: bool = False


SELF_EVAL_BACKLOG = AutoresearchScenario(
    name="self-eval-backlog",
    expected_done=4,
    timeout_seconds=10800,
    description="Real all-Codex self-eval backlog bridge hardening cycle.",
    seed_text=(
        "Run a real Autoresearch-style self-evaluation hardening cycle for "
        "ZaoFu's self-eval backlog bridge. Split this into exactly four "
        "deliverable tasks so the harness can exercise four dev replicas: "
        "(1) failed self-eval creates an idempotent backlog task, "
        "(2) passing self-eval does not write backlog, "
        "(3) backlog task contract/evidence is actionable for repair, and "
        "(4) docs/README usage remains reproducible. Use TDD and do not "
        "bypass review, test, or judge gates."
    ),
)

POSITIVE_PRESSURE_4DEV = AutoresearchScenario(
    name="positive-pressure-4dev",
    expected_done=4,
    timeout_seconds=10800,
    description=(
        "Exercise four dev replicas on four independent deliverables with the "
        "full arch/critic/dev/review/test/judge/discriminator chain."
    ),
    seed_text=(
        "Run a positive-pressure four-dev harness validation for ZaoFu. Split "
        "this into exactly four independent deliverable tasks, each owned by a "
        "different dev replica and writing only its own file under "
        "docs/autoresearch-campaign/positive-pressure/: "
        "(1) scheduler.md, (2) recovery.md, (3) evidence.md, and "
        "(4) operator-guard.md. Each file must contain a concise Chinese "
        "acceptance note and a command that verifies the file exists. Use the "
        "normal arch, critic, dev, review, test, judge, discriminator flow; do "
        "not manually move task status or bypass gates."
    ),
)

CONTROLLED_STUCK_RECOVERY = AutoresearchScenario(
    name="controlled-stuck-recovery",
    expected_done=1,
    timeout_seconds=7200,
    requires_worker_stuck_injection=True,
    description=(
        "Validate worker.stuck observability and recovery under a short "
        "stuck threshold or external stuck injection."
    ),
    seed_text=(
        "Produce one deliverable: docs/autoresearch-campaign/stuck-recovery.md. "
        "It must contain a concise Chinese note describing a controlled worker "
        "stuck-recovery exercise and the command used to check that this file "
        "exists. Describe the exercise as a procedure, not as a claim that a "
        "future run has already succeeded. Product acceptance command: "
        "test -f docs/autoresearch-campaign/stuck-recovery.md. "
        "Keep this as one independent implementation task. Planner plans, "
        "Critic reviews the plan, dev writes the file, and Verify/Judge evaluate "
        "the delivered file against these product requirements."
    ),
)

FAIL_REWORK_CONVERGE = AutoresearchScenario(
    name="fail-rework-converge",
    expected_done=1,
    timeout_seconds=7200,
    description=(
        "Force one detectable review/test/judge failure and verify bounded "
        "rework converges to done."
    ),
    seed_text=(
        "Run a fail-then-rework convergence validation for ZaoFu. Implement a "
        "small documentation deliverable at "
        "docs/autoresearch-campaign/rework-converge.md and require the "
        "verification command `grep -q REWORK-COVERED "
        "docs/autoresearch-campaign/rework-converge.md`. The first submitted "
        "implementation should be evaluated strictly; if review, test, judge, "
        "or discriminator reports missing evidence, route bounded rework back "
        "through dev and converge to a final passing artifact. Do not manually "
        "move task status or bypass gates."
    ),
)

MANUAL_INTERVENTION_GUARD = AutoresearchScenario(
    name="manual-intervention-guard",
    expected_done=1,
    timeout_seconds=5400,
    description=(
        "Validate that manual or Web/Kanban-style intervention is treated as "
        "an audited request and cannot silently mutate canonical done state."
    ),
    seed_text=(
        "Run a manual-intervention guard validation for ZaoFu. Produce "
        "docs/autoresearch-campaign/manual-intervention-guard.md describing, "
        "in concise Chinese, that illegal manual done transitions must be "
        "blocked or audited rather than accepted as canonical truth. Include "
        "evidence from a local command or event trace showing the intended "
        "guard. Acceptance command: test -f "
        "docs/autoresearch-campaign/manual-intervention-guard.md. Use the "
        "normal arch, critic, dev, review, test, judge, discriminator flow."
    ),
)

SPEC_VALIDATE_HARDENING = AutoresearchScenario(
    name="spec-validate-hardening",
    expected_done=2,
    timeout_seconds=10800,
    description=(
        "Harden `zf spec validate` against the two failure modes surfaced by "
        "cangjie-cc r1 (TASK-8D3727 critic gate v1): malformed verification "
        "literals (missing PATH= prefix → rc=127) and tdd_ref outside the "
        "scope graph (blocks scope.fail_closed × TDD RED-first)."
    ),
    seed_text=(
        "Run a `zf spec validate` schema-hardening cycle for ZaoFu. Source: "
        "cangjie-cc r1 TASK-8D3727 critic gate v1 surfaced two failure modes "
        "that `zf spec validate` missed and only critic.literal-exec caught. "
        "Split into exactly two independent deliverable tasks, each owned by "
        "a different dev replica: "
        "(1) add `_check_verification_literal` to src/zf/cli/spec.py + "
        "src/zf/core/spec_parser.py — reject verification fields whose first "
        "shell token contains '/' or ':' without a preceding 'VAR=' assignment "
        "(bash interprets the token as the command and returns rc=127); also "
        "run `bash -n -c <cmd>` as a cheap shell syntax check. "
        "(2) add `_check_tdd_ref_in_scope_graph` to the same surface — every "
        "non-empty tdd_ref must appear in either the vertical's own scope or "
        "the transitive scope union over its blocked_by ancestors. "
        "Spec details, complete acceptance criteria, and the cangjie r1 plan "
        "fixture path live in backlogs/2026-05-21-0644-P1-cangjie-issue-"
        "zf-spec-validate-path-prefix-check.md and "
        "backlogs/2026-05-21-0645-P1-cangjie-issue-zf-spec-validate-"
        "tdd-scope-graph.md. Use TDD: each deliverable lands unit tests, an "
        "integration regression case, and a runtime-import grep proof per "
        ".claude/rules/code.md §Wire-Up Discipline. Do not bypass review, "
        "test, or judge gates."
    ),
)

SIMPLIFICATION_AUDIT = AutoresearchScenario(
    name="simplification-audit",
    expected_done=1,
    timeout_seconds=3600,
    description=(
        "Run one proposal-only, evidence-backed simplification audit without "
        "changing production behavior or applying a candidate."
    ),
    seed_text=(
        "Run one proposal-only simplification audit for the current repository. "
        "Use the zf-find-simplifications skill and create exactly one audit "
        "deliverable at reports/autoresearch/simplification-audit.md. Survey "
        "production, dynamic, test, and documentation consumers; identify "
        "protected architecture seams; list evidence-backed candidates and "
        "rejected candidates; state net surface reduction, behavior given up, "
        "risk, and a focused verification plan for each accepted candidate. "
        "Do not edit production source, tests, configuration, runtime truth, or "
        "apply any simplification. The report is a proposal artifact only. "
        "Acceptance command: test -f "
        "reports/autoresearch/simplification-audit.md."
    ),
)


INCIDENT_REPRODUCTION = AutoresearchScenario(
    name="incident-reproduction",
    expected_done=1,
    timeout_seconds=7200,
    description="Reproduce the referenced incident in isolation and verify a bounded repair.",
    seed_text=(
        "Diagnose the actual incident in the appended diagnostic context. Read its "
        "source event, run/task identity, exact command, artifact refs/digests and "
        "source checkout before proposing a cause. Source runtime state is read-only; "
        "use an isolated fixture and the configured state_dir for any reproduction. "
        "Deliver one bounded regression and repair candidate in this worktree. "
        "Reproduce the observed failure through its real runtime caller, retain the "
        "failing command and output (RED), then rerun the same assertion after the "
        "smallest fix (GREEN) plus affected callers. Distinguish missing/stale stage "
        "inputs, wrong run/task identity, evidence admission and product assertion "
        "failures using the referenced evidence, not guessed keywords. Keep semantic "
        "planning methods in skills/prompts and mechanical invariants in runtime. "
        "Record evidence and limitations in reports/autoresearch/incident-reproduction.md. "
        "Missing references, an unreproduced failure or an unavailable external "
        "prerequisite must remain blocked/unverified. A file-existence check, generic "
        "stuck injection, completed task count or score does not prove incident "
        "recovery. Independent test/judge must inspect RED/GREEN and original-incident "
        "coverage. Keep the normal result admission and review gates. Do not apply "
        "the candidate to the source checkout, mutate production state or claim the "
        "original run recovered without a separately authorized controlled resume."
    ),
)


BUILTIN_SCENARIOS: dict[str, AutoresearchScenario] = {
    INCIDENT_REPRODUCTION.name: INCIDENT_REPRODUCTION,
    SELF_EVAL_BACKLOG.name: SELF_EVAL_BACKLOG,
    POSITIVE_PRESSURE_4DEV.name: POSITIVE_PRESSURE_4DEV,
    CONTROLLED_STUCK_RECOVERY.name: CONTROLLED_STUCK_RECOVERY,
    FAIL_REWORK_CONVERGE.name: FAIL_REWORK_CONVERGE,
    MANUAL_INTERVENTION_GUARD.name: MANUAL_INTERVENTION_GUARD,
    SPEC_VALIDATE_HARDENING.name: SPEC_VALIDATE_HARDENING,
    SIMPLIFICATION_AUDIT.name: SIMPLIFICATION_AUDIT,
}


def scenario_names() -> list[str]:
    return sorted(BUILTIN_SCENARIOS)


def resolve_scenario(
    name: str,
    *,
    seed_file: Path | None = None,
    expected_done: int | None = None,
    timeout_seconds: int | None = None,
) -> AutoresearchScenario:
    base = BUILTIN_SCENARIOS.get(name)
    if base is None and seed_file is None:
        known = ", ".join(scenario_names())
        raise ValueError(f"unknown autoresearch scenario {name!r}; known: {known}")

    seed_text = ""
    description = ""
    requires_worker_stuck_injection = False
    default_expected = 1
    default_timeout = 3600
    if base is not None:
        seed_text = base.seed_text
        description = base.description
        default_expected = base.expected_done
        default_timeout = base.timeout_seconds
        requires_worker_stuck_injection = (
            base.requires_worker_stuck_injection
        )
    if seed_file is not None:
        seed_text = seed_file.read_text(encoding="utf-8").strip()
        if not seed_text:
            raise ValueError(f"seed file is empty: {seed_file}")
    if not seed_text:
        raise ValueError("autoresearch scenario has no seed text")

    return AutoresearchScenario(
        name=name,
        seed_text=seed_text,
        expected_done=expected_done if expected_done is not None else default_expected,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else default_timeout,
        description=description,
        requires_worker_stuck_injection=requires_worker_stuck_injection,
    )
