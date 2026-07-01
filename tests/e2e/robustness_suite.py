"""Robustness test suite entrypoint for ZaoFu.

This runner turns the robustness backlogs into an executable test plan.
The default mode runs the full deterministic coverage matrix for all nine
robustness backlogs:

  python -m tests.e2e.robustness_suite

It validates the E2E stress configs, runs backlog-scoped deterministic
pytest groups, and performs dry-run harness boot checks in isolated
workspaces. Real provider E2E runs are opt-in because they start tmux
workers and burn Claude/Codex tokens:

  python -m tests.e2e.robustness_suite --include-real mixed --confirm-real

Scenario ownership:
  - dev-codex-backends.yaml is the Codex isolation smoke.
  - dev-mixed-backends.yaml is the mixed-backend stress entry.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

CODEX_CONFIG = REPO_ROOT / "examples" / "dev-codex-backends.yaml"
MIXED_CONFIG = REPO_ROOT / "examples" / "dev-mixed-backends.yaml"

DEFAULT_CODEX_WORKTREE = Path("/tmp/zaofu-codex-smoke")
DEFAULT_MIXED_WORKTREE = Path("/tmp/zaofu-mixed-stress")

@dataclass(frozen=True)
class BacklogTestGroup:
    backlog: str
    description: str
    pytest_targets: tuple[str, ...]


BACKLOG_TEST_GROUPS = [
    BacklogTestGroup(
        backlog="2026-05-04-0617 INDEX",
        description="robustness suite entrypoint and coverage manifest",
        pytest_targets=(
            "tests/e2e/test_robustness_suite.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0618 P0 Codex smoke",
        description="Codex isolation config, hooks, session filtering, and tool closure",
        pytest_targets=(
            "tests/test_config_loader.py::test_validate_passes_for_dev_codex_backends_example",
            "tests/test_tool_closure_codex.py",
            "tests/test_codex_hook_settings.py",
            "tests/test_codex_ready_stabilization.py",
            "tests/test_codex_session_project_filter.py",
            "tests/test_codex_session_reader_cwd_fallback.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0619 P0 invariants/report contracts",
        description="phase reports, ship/archive protocol, cost equality guards",
        pytest_targets=(
            "tests/longhorizon/test_invariant_guards.py",
            "tests/e2e/test_mixed_phase_report.py",
            "tests/e2e/test_w5_phase_report.py",
            "tests/test_task_terminal_archive.py",
            "tests/test_feature_terminal_archive.py",
            "tests/test_cost_no_double_count.py",
            "tests/test_cost_backend_dimension.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0620 P1 scripted backend E2E",
        description="deterministic/fake harness lifecycle coverage",
        pytest_targets=(
            "tests/e2e/test_scripted_runner.py::test_scripted_happy_path_reaches_task_and_feature_done",
            "tests/e2e/test_scripted_runner.py::test_scripted_rework_path_loops_then_closes",
            "tests/e2e/test_scripted_runner.py::test_scripted_test_failure_reworks_dev_then_closes",
            "tests/e2e/test_scripted_runner.py::test_scripted_judge_failure_reworks_dev_then_closes",
            "tests/e2e/test_scripted_runner.py::test_scripted_duplicate_event_does_not_double_close_or_double_cost",
            "tests/e2e/test_scripted_runner.py::test_scripted_invalid_transition_is_recorded_without_state_corruption",
            "tests/integration/test_layer2_e2e.py",
            "tests/integration/test_lifecycle_full_chain.py",
            "tests/test_housekeeping_e2e.py",
            "tests/test_runtime_orchestrator.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0621 P1 mixed backend stress",
        description="mixed replica expansion, WIP, dispatch, and backend attribution",
        pytest_targets=(
            "tests/e2e/test_scripted_runner.py::test_scripted_multi_task_distributes_dev_and_test_instances",
            "tests/e2e/test_scripted_runner.py::test_scripted_quality_gates_cover_every_dev_completed_task",
            "tests/test_config_loader.py::test_validate_passes_for_dev_mixed_backends_example",
            "tests/test_config_loader_backends.py",
            "tests/test_config_schema_backends_list.py",
            "tests/test_mixed_team_preset.py",
            "tests/test_briefing_mixed_backend_roster.py",
            "tests/test_multireplica_dispatch.py",
            "tests/test_multi_task_queue_hole.py",
            "tests/test_wip_by_instance.py",
            "tests/test_dispatch_reassign_gridlock.py",
            "tests/integration/test_mixed_backend_smoke.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0622 P1 yoke/skills",
        description="skills provenance, enabled-only visibility, backend capability differences",
        pytest_targets=(
            "tests/test_skill_provenance.py",
            "tests/test_role_plugins_skills_agent.py",
            "tests/test_config_schema_instances.py",
            "tests/test_runtime_injection.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0623 P2 resilience/chaos",
        description="restart, stuck/orphan handling, malformed events, budgets, path safety",
        pytest_targets=(
            "tests/e2e/test_scripted_runner.py::test_scripted_worker_timeout_recovers_to_another_instance",
            "tests/e2e/test_scripted_runner.py::test_scripted_duplicate_event_does_not_double_close_or_double_cost",
            "tests/e2e/test_scripted_runner.py::test_scripted_invalid_transition_is_recorded_without_state_corruption",
            "tests/test_orphan_timeout.py",
            "tests/test_stuck_detection_integration.py",
            "tests/test_capture_logs_watchdog.py",
            "tests/test_recovery_briefing.py",
            "tests/test_recovery_briefing_extended.py",
            "tests/test_runtime_watcher.py",
            "tests/test_cli_restart.py",
            "tests/test_cost_budget_enforcement.py",
            "tests/test_events_log_malformed.py",
            "tests/test_path_guard.py",
            "tests/test_session_tailer.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0624 P2 observability/archive",
        description="trace, diagnostics, archive shape, cost/task linkage, scorecard",
        pytest_targets=(
            "tests/e2e/test_scripted_runner.py::test_scripted_scorecard_can_be_built_from_state_artifacts",
            "tests/e2e/test_robustness_suite.py::test_scorecard_counts_events_cost_and_archives",
            "tests/e2e/test_robustness_suite.py::test_archive_run_writes_standard_artifact_shape",
            "tests/e2e/test_robustness_suite.py::test_scorecard_can_be_rebuilt_from_archived_files_only",
            "tests/test_trace_query.py",
            "tests/test_task_trace.py",
            "tests/test_redacted_diagnostics.py",
            "tests/test_cli_trace.py",
            "tests/test_cli_cost.py",
            "tests/test_cli_archive_flags.py",
            "tests/test_events_archive.py",
            "tests/test_cost_archive.py",
        ),
    ),
    BacklogTestGroup(
        backlog="2026-05-04-0625 P3 long horizon",
        description="long-horizon loop skeleton, metrics, health report, guards",
        pytest_targets=(
            "tests/longhorizon/test_loop_skeleton.py",
            "tests/test_metrics_collector.py",
            "tests/test_metrics_cli.py",
        ),
    ),
]

SMOKE_TESTS = (
    "tests/test_config_loader.py::test_validate_passes_for_dev_mixed_backends_example",
    "tests/test_config_loader.py::test_validate_passes_for_dev_codex_backends_example",
    "tests/test_skill_provenance.py",
    "tests/e2e/test_scripted_runner.py::test_scripted_happy_path_reaches_task_and_feature_done",
    "tests/e2e/test_mixed_phase_report.py",
    "tests/e2e/test_run_mixed_pythonpath.py",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    config: Path
    default_worktree: Path
    tasks: int
    timeout_s: int
    description: str


SCENARIOS = {
    "codex": Scenario(
        name="codex",
        config=CODEX_CONFIG,
        default_worktree=DEFAULT_CODEX_WORKTREE,
        tasks=1,
        timeout_s=1800,
        description="Codex isolation smoke",
    ),
    "mixed": Scenario(
        name="mixed",
        config=MIXED_CONFIG,
        default_worktree=DEFAULT_MIXED_WORKTREE,
        tasks=3,
        timeout_s=1800,
        description="Mixed-backend E2E stress entry",
    ),
}


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Scorecard:
    scenario: str
    preset: str
    status: str
    exit_code: int
    event_count: int
    cost_entries: int
    total_cost_usd: float
    task_count: int
    task_done_count: int
    feature_done_count: int
    backend_usage: dict[str, int]
    critic_coverage_rate: float
    design_reject_count: int
    design_rework_recovery_rate: float
    codex_hook_count: int
    codex_observe_timeout_count: int
    scope_violation_count: int
    invalid_transition_count: int
    rework_capped_count: int
    premature_done_rework_count: int
    feature_liveness_blocked_count: int
    hook_orphan_count: int
    worker_stuck_count: int
    worker_orphan_count: int
    task_orphan_count: int
    stuck_orphan_count: int
    missing_required_events: list[str]
    invariant_results: dict[str, bool]
    artifact_completeness: dict[str, bool]

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "preset": self.preset,
            "status": self.status,
            "exit_code": self.exit_code,
            "event_count": self.event_count,
            "cost_entries": self.cost_entries,
            "total_cost_usd": self.total_cost_usd,
            "task_count": self.task_count,
            "task_done_count": self.task_done_count,
            "feature_done_count": self.feature_done_count,
            "backend_usage": self.backend_usage,
            "critic_coverage_rate": self.critic_coverage_rate,
            "design_reject_count": self.design_reject_count,
            "design_rework_recovery_rate": self.design_rework_recovery_rate,
            "codex_hook_count": self.codex_hook_count,
            "codex_observe_timeout_count": self.codex_observe_timeout_count,
            "scope_violation_count": self.scope_violation_count,
            "invalid_transition_count": self.invalid_transition_count,
            "rework_capped_count": self.rework_capped_count,
            "premature_done_rework_count": self.premature_done_rework_count,
            "feature_liveness_blocked_count": self.feature_liveness_blocked_count,
            "hook_orphan_count": self.hook_orphan_count,
            "worker_stuck_count": self.worker_stuck_count,
            "worker_orphan_count": self.worker_orphan_count,
            "task_orphan_count": self.task_orphan_count,
            "stuck_orphan_count": self.stuck_orphan_count,
            "missing_required_events": self.missing_required_events,
            "invariant_results": self.invariant_results,
            "artifact_completeness": self.artifact_completeness,
        }


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    return env


def run_cmd(
    name: str,
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    allow_fail: bool = False,
) -> StepResult:
    print(f"\n==> {name}")
    print("$ " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    ok = proc.returncode == 0
    if not ok and not allow_fail:
        return StepResult(name=name, ok=False, detail=f"rc={proc.returncode}")
    return StepResult(
        name=name,
        ok=ok or allow_fail,
        detail=f"rc={proc.returncode}",
    )


def validate_config(path: Path) -> StepResult:
    return run_cmd(
        f"validate {path.relative_to(REPO_ROOT)}",
        [sys.executable, "-m", "zf.cli.main", "validate", "--path", str(path)],
    )


def assert_config_topology() -> StepResult:
    print("\n==> assert config topology")
    from zf.core.config.loader import load_config

    codex = load_config(CODEX_CONFIG)
    mixed = load_config(MIXED_CONFIG)

    codex_backends = {role.backend for role in codex.roles}
    if codex_backends != {"codex"}:
        return StepResult(
            "assert config topology",
            False,
            f"codex backends={sorted(codex_backends)}",
        )
    # 11 roles since the example trimmed to orchestrator/arch/critic +
    # 4 dev + review + 2 test + judge (c8a795c-era layout).
    if len(codex.roles) != 11:
        return StepResult(
            "assert config topology",
            False,
            f"codex roles={len(codex.roles)}",
        )
    by_name = {role.name: role for role in codex.roles}
    critic = by_name.get("critic")
    review = by_name.get("review")
    if critic is None:
        return StepResult("assert config topology", False, "codex critic role missing")
    if critic.triggers != ["arch.proposal.done"]:
        return StepResult(
            "assert config topology",
            False,
            f"critic triggers={critic.triggers!r}",
        )
    if critic.publishes != ["design.critique.done", "gate.failed"]:
        return StepResult(
            "assert config topology",
            False,
            f"critic publishes={critic.publishes!r}",
        )
    # Since 29f18e2 review does code review behind the kernel static
    # gate (dev.build.done -> static gate -> static_gate.passed -> review).
    if review is None or review.triggers != ["static_gate.passed"]:
        return StepResult(
            "assert config topology",
            False,
            f"review triggers={getattr(review, 'triggers', None)!r}",
        )
    if not codex.verification.scope.fail_closed:
        return StepResult(
            "assert config topology",
            False,
            "codex verification.scope.fail_closed must be true",
        )
    if not codex.verification.architecture.enabled:
        return StepResult(
            "assert config topology",
            False,
            "codex verification.architecture.enabled must be true",
        )
    if not codex.verification.promoted.enabled:
        return StepResult(
            "assert config topology",
            False,
            "codex verification.promoted.enabled must be true",
        )

    expanded = {(role.name, role.instance_id): role.backend for role in mixed.roles}
    expected = {
        ("dev", "dev-1"): "claude-code",
        ("dev", "dev-2"): "codex",
        ("test", "test-1"): "claude-code",
        ("test", "test-2"): "codex",
    }
    for key, backend in expected.items():
        if expanded.get(key) != backend:
            return StepResult(
                "assert config topology",
                False,
                f"{key} backend={expanded.get(key)!r}, expected={backend!r}",
            )
    if len(mixed.roles) != 8:
        return StepResult(
            "assert config topology",
            False,
            f"mixed roles={len(mixed.roles)}",
        )
    print("codex: 13 roles, all codex, critic owns design critique")
    print("mixed: dev/test expand to claude-code + codex replicas")
    return StepResult("assert config topology", True)


def run_pytest_targets(
    name: str,
    targets: Iterable[str],
    extra_pytest_args: Iterable[str] = (),
) -> StepResult:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        "--no-cov",
        *extra_pytest_args,
    ]
    return run_cmd(name, cmd)


def run_backlog_test_groups(
    *,
    smoke: bool,
    extra_pytest_args: Iterable[str] = (),
) -> list[StepResult]:
    if smoke:
        return [
            run_pytest_targets(
                "smoke deterministic tests",
                SMOKE_TESTS,
                extra_pytest_args,
            )
        ]
    results: list[StepResult] = []
    for group in BACKLOG_TEST_GROUPS:
        results.append(
            run_pytest_targets(
                f"{group.backlog}: {group.description}",
                group.pytest_targets,
                extra_pytest_args,
            )
        )
    return results


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def materialize_workspace(
    scenario: Scenario,
    *,
    worktree: Path | None = None,
    budget_usd: float = 100.0,
    clean: bool = True,
) -> Path:
    import yaml

    if worktree is None:
        root = Path(tempfile.mkdtemp(prefix=f"zaofu-{scenario.name}-"))
    else:
        root = worktree
        if clean and root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)

    data = yaml.safe_load(scenario.config.read_text(encoding="utf-8"))
    suffix = f"{scenario.name}-{int(time.time())}"
    data.setdefault("project", {})["name"] = f"zaofu-{suffix}"
    data.setdefault("project", {})["state_dir"] = ".zf"
    data.setdefault("session", {})["tmux_session"] = f"zf-{suffix}"
    # ZfConfig reads the hard cap from the top-level field. Do not write a
    # shadow `cost.global_budget_usd` block; that would look configured while
    # leaving dispatch budget enforcement disabled.
    data["global_budget_usd"] = budget_usd
    data.pop("cost", None)
    (root / "zf.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    state = root / ".zf"
    for child in ["artifacts", "briefings", "logs", "memory"]:
        (state / child).mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text("", encoding="utf-8")
    write_json(state / "kanban.json", [])
    write_json(state / "feature_list.json", [])
    (state / "session.yaml").write_text(
        'session_id: ""\nruntime_state: initialized\nlatest_event_offset: 0\n',
        encoding="utf-8",
    )
    materialize_smoke_project_fixture(root)
    return root


def materialize_smoke_project_fixture(root: Path) -> None:
    """Create a minimal project that satisfies the real smoke gates.

    The dev-codex-backends preset intentionally runs both Python and Web
    quality gates. Real provider smoke workspaces are otherwise blank
    scratch dirs, so the terminal discriminator would fail on missing
    ``web/package.json`` instead of evaluating the agent's actual task.

    Keep both import roots available. The default real smoke seed asks for
    files under ``src/`` and real agents commonly write ``from src.foo`` tests,
    while the preset's full gate runs with ``PYTHONPATH=src``. Pytest's
    ``pythonpath`` setting makes the scratch project tolerant of both
    conventions without weakening the gate itself.
    """
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pytest.ini").write_text(
        "[pytest]\n"
        "pythonpath = . src\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_smoke_fixture.py").write_text(
        "def test_smoke_fixture_imports():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    web = root / "web"
    scripts = web / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (web / "package.json").write_text(
        json.dumps(
            {
                "name": "zaofu-real-smoke-fixture",
                "private": True,
                "type": "module",
                "scripts": {
                    "typecheck": "node scripts/typecheck.mjs",
                    "test": "node scripts/test.mjs",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (scripts / "typecheck.mjs").write_text(
        "console.log('web typecheck fixture ok');\n",
        encoding="utf-8",
    )
    (scripts / "test.mjs").write_text(
        "console.log('web test fixture ok');\n",
        encoding="utf-8",
    )


def run_dry_start(scenario: Scenario) -> list[StepResult]:
    root = materialize_workspace(scenario)
    print(f"\n[workspace] {scenario.name}: {root}")
    return [
        run_cmd(
            f"{scenario.name} validate materialized zf.yaml",
            [
                sys.executable,
                "-m",
                "zf.cli.main",
                "validate",
                "--path",
                str(root / "zf.yaml"),
            ],
            cwd=root,
        ),
        run_cmd(
            f"{scenario.name} zf start --dry-run",
            [sys.executable, "-m", "zf.cli.main", "start", "--dry-run"],
            cwd=root,
        ),
    ]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return default
    return json.loads(text)


def _first_existing(state_dir: Path, names: Iterable[str]) -> Path:
    for name in names:
        path = state_dir / name
        if path.exists():
            return path
    return state_dir / next(iter(names))


def _read_archive_rows(archive_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for archive in sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []:
        data = _read_json(archive, [])
        if isinstance(data, list):
            rows.extend(row for row in data if isinstance(row, dict))
    return rows


def _task_done_count(events: list[dict], archive_dir: Path) -> int:
    done: set[str] = set()
    for event in events:
        if event.get("type") != "task.status_changed":
            continue
        if (event.get("payload") or {}).get("to") == "done":
            done.add(str(event.get("task_id") or f"event:{len(done)}"))
    for row in _read_archive_rows(archive_dir):
        if row.get("status") == "done":
            done.add(str(row.get("id") or f"archive:{len(done)}"))
    return len(done)


def _feature_done_count(events: list[dict], state_dir: Path) -> int:
    done: set[str] = set()
    for event in events:
        if event.get("type") != "feature.status_changed":
            continue
        payload = event.get("payload") or {}
        if payload.get("to") == "done":
            done.add(str(event.get("feature_id") or event.get("task_id") or f"event:{len(done)}"))
    archive_dir = _first_existing(state_dir, ["feature_list", "feature_archive"])
    for row in _read_archive_rows(archive_dir):
        if row.get("status") == "done":
            done.add(str(row.get("id") or f"archive:{len(done)}"))
    return len(done)


def build_scorecard(
    *,
    state_dir: Path,
    scenario: str,
    preset: str,
    status: str = "unknown",
    exit_code: int = 0,
) -> Scorecard:
    events = _read_jsonl(state_dir / "events.jsonl")
    costs = _read_jsonl(state_dir / "cost.jsonl")
    active_task_path = _first_existing(state_dir, ["kanban.json", "kanban_active.json"])
    active_feature_path = _first_existing(
        state_dir,
        ["feature_list.json", "feature_active.json"],
    )
    task_archive_dir = _first_existing(state_dir, ["kanban", "kanban_archive"])
    feature_archive_dir = _first_existing(state_dir, ["feature_list", "feature_archive"])

    task_done_count = _task_done_count(events, task_archive_dir)
    feature_done_count = _feature_done_count(events, state_dir)
    backend_usage: dict[str, int] = {}
    for event in events:
        if event.get("type") != "agent.usage":
            continue
        backend = (event.get("payload") or {}).get("backend", "unknown")
        backend_usage[backend] = backend_usage.get(backend, 0) + 1

    active_tasks = _read_json(active_task_path, [])
    task_count = (
        len(active_tasks) if isinstance(active_tasks, list) else 0
    ) + len(_read_archive_rows(task_archive_dir))
    event_types = {event.get("type", "") for event in events}
    arch_done_count = sum(
        1 for event in events if event.get("type") == "arch.proposal.done"
    )
    design_critique_done_count = sum(
        1 for event in events if event.get("type") == "design.critique.done"
    )
    critic_coverage_rate = (
        1.0
        if arch_done_count == 0
        else min(1.0, design_critique_done_count / arch_done_count)
    )
    design_failures: list[tuple[int, str]] = []
    for idx, event in enumerate(events):
        payload = event.get("payload") or {}
        if event.get("type") != "gate.failed":
            continue
        if (
            event.get("actor") == "critic"
            or payload.get("role") == "critic"
            or payload.get("gate") in {"critic", "design", "design_critique"}
        ):
            design_failures.append((idx, str(event.get("task_id") or "")))
    recovered_design_failures = 0
    for idx, task_id in design_failures:
        later = events[idx + 1:]
        has_arch = any(
            event.get("type") == "arch.proposal.done"
            and str(event.get("task_id") or "") == task_id
            for event in later
        )
        has_critic = any(
            event.get("type") == "design.critique.done"
            and str(event.get("task_id") or "") == task_id
            for event in later
        )
        if has_arch and has_critic:
            recovered_design_failures += 1
    design_rework_recovery_rate = (
        1.0
        if not design_failures
        else recovered_design_failures / len(design_failures)
    )
    codex_hook_count = sum(
        1 for event in events if str(event.get("type", "")).startswith("codex.hook.")
    )
    codex_observe_timeout_count = sum(
        1
        for event in events
        if event.get("type") == "worker.spawn_warning"
        and (event.get("payload") or {}).get("code") == "codex_observe_timeout"
    )
    scope_violation_count = sum(
        1 for event in events if event.get("type") == "scope.violation"
    )
    invalid_transition_count = sum(
        1 for event in events if event.get("type") == "task.invalid_transition"
    )
    rework_capped_count = sum(
        1 for event in events if event.get("type") == "task.rework.capped"
    )
    premature_done_rework_count = sum(
        1
        for event in events
        if event.get("type") == "task.rework.requested"
        and (event.get("payload") or {}).get("trigger_event_type")
        == "discriminator.failed"
    )
    feature_liveness_blocked_count = sum(
        1 for event in events if event.get("type") == "feature.liveness.blocked"
    )
    required_events = [
        "session.started",
        "user.message",
        "task.created",
        "task.contract.update",
        "arch.proposal.done",
        "design.critique.done",
        "task.assigned",
        "dev.build.done",
        "review.approved",
        "test.passed",
        "judge.passed",
        "task.status_changed",
    ]
    missing_required_events = [
        event_type for event_type in required_events if event_type not in event_types
    ]
    hook_orphan_count = sum(
        1 for event in events if event.get("type") == "hook.orphan_event"
    )
    worker_stuck_count = sum(
        1 for event in events if event.get("type") == "worker.stuck"
    )
    worker_orphan_count = sum(
        1 for event in events if event.get("type") == "worker.orphaned"
    )
    task_orphan_count = sum(
        1 for event in events if event.get("type") == "task.orphan_warning"
    )
    stuck_orphan_count = (
        hook_orphan_count
        + worker_stuck_count
        + worker_orphan_count
        + task_orphan_count
    )
    total_cost_usd = sum(float(entry.get("cost_usd", 0.0)) for entry in costs)
    usage_events = sum(1 for event in events if event.get("type") == "agent.usage")
    invariant_results = {
        "has_events": bool(events),
        "usage_has_cost_projection": usage_events == 0 or bool(costs),
        "task_done_projected": task_done_count > 0,
        "feature_done_projected": feature_done_count > 0,
        "critic_chain_present": (
            arch_done_count == 0 or design_critique_done_count > 0
        ),
        "no_scope_violation": scope_violation_count == 0,
        "no_invalid_transition": invalid_transition_count == 0,
        "no_rework_capped": rework_capped_count == 0,
    }

    artifacts = {
        "events.jsonl": (state_dir / "events.jsonl").exists(),
        "cost.jsonl": (state_dir / "cost.jsonl").exists(),
        "kanban.json": active_task_path.exists(),
        "feature_list.json": active_feature_path.exists(),
        "kanban_archive": task_archive_dir.exists(),
        "feature_archive": feature_archive_dir.exists(),
        "role_sessions.yaml": (state_dir / "role_sessions.yaml").exists(),
        "session.yaml": (state_dir / "session.yaml").exists(),
    }
    return Scorecard(
        scenario=scenario,
        preset=preset,
        status=status,
        exit_code=exit_code,
        event_count=len(events),
        cost_entries=len(costs),
        total_cost_usd=total_cost_usd,
        task_count=task_count,
        task_done_count=task_done_count,
        feature_done_count=feature_done_count,
        backend_usage=backend_usage,
        critic_coverage_rate=critic_coverage_rate,
        design_reject_count=len(design_failures),
        design_rework_recovery_rate=design_rework_recovery_rate,
        codex_hook_count=codex_hook_count,
        codex_observe_timeout_count=codex_observe_timeout_count,
        scope_violation_count=scope_violation_count,
        invalid_transition_count=invalid_transition_count,
        rework_capped_count=rework_capped_count,
        premature_done_rework_count=premature_done_rework_count,
        feature_liveness_blocked_count=feature_liveness_blocked_count,
        hook_orphan_count=hook_orphan_count,
        worker_stuck_count=worker_stuck_count,
        worker_orphan_count=worker_orphan_count,
        task_orphan_count=task_orphan_count,
        stuck_orphan_count=stuck_orphan_count,
        missing_required_events=missing_required_events,
        invariant_results=invariant_results,
        artifact_completeness=artifacts,
    )


def write_scorecard(scorecard: Scorecard, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(scorecard.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def scorecard_acceptance_issues(
    scorecard: Scorecard,
    *,
    expected_done: int,
    require_codex_hooks: bool = False,
) -> list[str]:
    issues: list[str] = []
    if scorecard.task_done_count < expected_done:
        issues.append(
            f"task_done_count={scorecard.task_done_count}, expected>={expected_done}"
        )
    if scorecard.feature_done_count < 1:
        issues.append("feature_done_count < 1")
    if scorecard.missing_required_events:
        issues.append(
            "missing_required_events="
            + ",".join(scorecard.missing_required_events)
        )
    failed_invariants = [
        name for name, ok in scorecard.invariant_results.items() if not ok
    ]
    if failed_invariants:
        issues.append("failed_invariants=" + ",".join(failed_invariants))
    missing_artifacts = [
        name for name, ok in scorecard.artifact_completeness.items() if not ok
    ]
    if missing_artifacts:
        issues.append("missing_artifacts=" + ",".join(missing_artifacts))
    if require_codex_hooks and scorecard.codex_hook_count < 1:
        issues.append("codex_hook_count < 1")
    return issues


def _copy_file_or_default(src: Path, dest: Path, default: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dest)
    else:
        dest.write_text(default, encoding="utf-8")


def _copy_dir_or_empty(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    if src.exists():
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)


def _format_phase_report(events_path: Path) -> str:
    from tests.e2e.w5_phase_report import generate_report

    phases = generate_report(events_path)
    lines = [f"events: {events_path}", ""]
    for phase in phases:
        evidence = "; ".join(phase.evidence) if phase.evidence else "-"
        reasons = "; ".join(phase.fail_reasons) if phase.fail_reasons else "-"
        lines.append(f"- {phase.phase}: {phase.status}")
        lines.append(f"  evidence: {evidence}")
        lines.append(f"  reasons: {reasons}")
    return "\n".join(lines) + "\n"


def _format_cost_by_backend(cost_path: Path) -> str:
    totals: dict[str, dict[str, float]] = {}
    for entry in _read_jsonl(cost_path):
        backend = entry.get("backend") or "unknown"
        bucket = totals.setdefault(
            backend,
            {"entries": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        bucket["entries"] += 1
        bucket["input_tokens"] += float(entry.get("input_tokens", 0))
        bucket["output_tokens"] += float(entry.get("output_tokens", 0))
        bucket["cost_usd"] += float(entry.get("cost_usd", 0.0))
    if not totals:
        return "no cost entries\n"
    lines = ["backend,entries,input_tokens,output_tokens,cost_usd"]
    for backend, bucket in sorted(totals.items()):
        lines.append(
            "{backend},{entries},{input_tokens:.0f},{output_tokens:.0f},{cost_usd:.6f}".format(
                backend=backend,
                **bucket,
            )
        )
    return "\n".join(lines) + "\n"


def archive_run(
    *,
    state_dir: Path,
    dest: Path,
    scenario: str,
    preset: str,
    status: str = "unknown",
    exit_code: int = 0,
    phase_report_text: str | None = None,
    cost_report_text: str | None = None,
    clean: bool = True,
) -> Path:
    """Archive one E2E run into the standard robustness artifact shape."""
    if clean and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    active_task_path = _first_existing(state_dir, ["kanban.json", "kanban_active.json"])
    active_feature_path = _first_existing(
        state_dir,
        ["feature_list.json", "feature_active.json"],
    )
    task_archive_dir = _first_existing(state_dir, ["kanban", "kanban_archive"])
    feature_archive_dir = _first_existing(state_dir, ["feature_list", "feature_archive"])

    _copy_file_or_default(state_dir / "events.jsonl", dest / "events.jsonl", "")
    _copy_file_or_default(state_dir / "cost.jsonl", dest / "cost.jsonl", "")
    _copy_file_or_default(active_task_path, dest / "kanban_active.json", "[]\n")
    _copy_file_or_default(active_feature_path, dest / "feature_active.json", "[]\n")
    _copy_file_or_default(state_dir / "role_sessions.yaml", dest / "role_sessions.yaml", "{}\n")
    _copy_file_or_default(state_dir / "session.yaml", dest / "session.yaml", "{}\n")
    _copy_dir_or_empty(task_archive_dir, dest / "kanban_archive")
    _copy_dir_or_empty(feature_archive_dir, dest / "feature_archive")

    (dest / "phase_report.txt").write_text(
        phase_report_text or _format_phase_report(dest / "events.jsonl"),
        encoding="utf-8",
    )
    (dest / "cost_by_backend.txt").write_text(
        cost_report_text or _format_cost_by_backend(dest / "cost.jsonl"),
        encoding="utf-8",
    )
    scorecard = build_scorecard(
        state_dir=dest,
        scenario=scenario,
        preset=preset,
        status=status,
        exit_code=exit_code,
    )
    write_scorecard(scorecard, dest / "scorecard.json")
    (dest / "postmortem.md").write_text(
        "\n".join(
            [
                f"# {scenario} E2E Postmortem",
                "",
                f"- preset: {preset}",
                f"- status: {status}",
                f"- exit_code: {exit_code}",
                f"- events: {scorecard.event_count}",
                f"- task_done_count: {scorecard.task_done_count}",
                f"- feature_done_count: {scorecard.feature_done_count}",
                f"- critic_coverage_rate: {scorecard.critic_coverage_rate:.3f}",
                f"- design_reject_count: {scorecard.design_reject_count}",
                f"- total_cost_usd: {scorecard.total_cost_usd:.6f}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dest


def run_real_scenario(
    scenario: Scenario,
    *,
    worktree: Path,
    budget_usd: float,
    clean: bool,
) -> StepResult:
    root = materialize_workspace(
        scenario,
        worktree=worktree,
        budget_usd=budget_usd,
        clean=clean,
    )
    print(f"\n[real-workspace] {scenario.name}: {root}")
    print(
        "[real] This starts provider CLIs. Ensure provider login and Codex "
        "project trust are already configured for this path."
    )
    result = run_cmd(
        f"real {scenario.name} provider E2E",
        [
            sys.executable,
            "-m",
            "tests.e2e.run_mixed",
            "--worktree",
            str(root),
            "--tasks",
            str(scenario.tasks),
            "--timeout",
            str(scenario.timeout_s),
            "--confirm",
        ],
    )
    archive_dest = root / ".zf" / "archives" / f"{scenario.name}-latest"
    archive_run(
        state_dir=root / ".zf",
        dest=archive_dest,
        scenario=scenario.name,
        preset=scenario.config.stem,
        status="pass" if result.ok else "fail",
        exit_code=0 if result.ok else 1,
    )
    scorecard = build_scorecard(
        state_dir=archive_dest,
        scenario=scenario.name,
        preset=scenario.config.stem,
        status="pass" if result.ok else "fail",
    )
    issues = scorecard_acceptance_issues(
        scorecard,
        expected_done=scenario.tasks,
        require_codex_hooks=scenario.name == "codex",
    )
    detail = f"{result.detail}; archive={archive_dest}"
    if result.ok and issues:
        return StepResult(
            result.name,
            False,
            detail + "; " + "; ".join(issues),
        )
    return StepResult(result.name, result.ok, detail)


def print_summary(results: list[StepResult]) -> int:
    print("\n========== robustness suite summary ==========")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        detail = f" ({result.detail})" if result.detail else ""
        print(f"[{status}] {result.name}{detail}")
    failed = [result for result in results if not result.ok]
    if failed:
        print(f"\nfailed steps: {len(failed)}")
        return 1
    print("\nall selected steps passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-unit",
        action="store_true",
        help="Skip deterministic pytest coverage for the backlog matrix.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run the small deterministic smoke set instead of all backlog groups.",
    )
    parser.add_argument(
        "--skip-dry-run",
        action="store_true",
        help="Skip materialized zf start --dry-run checks.",
    )
    parser.add_argument(
        "--include-real",
        choices=["none", "codex", "mixed", "all"],
        default="none",
        help="Opt into real provider E2E. Requires --confirm-real.",
    )
    parser.add_argument(
        "--confirm-real",
        action="store_true",
        help="Allow real provider E2E runs.",
    )
    parser.add_argument(
        "--codex-worktree",
        type=Path,
        default=DEFAULT_CODEX_WORKTREE,
        help=f"Real Codex smoke workspace (default {DEFAULT_CODEX_WORKTREE}).",
    )
    parser.add_argument(
        "--mixed-worktree",
        type=Path,
        default=DEFAULT_MIXED_WORKTREE,
        help=f"Real mixed stress workspace (default {DEFAULT_MIXED_WORKTREE}).",
    )
    parser.add_argument(
        "--real-budget-usd",
        type=float,
        default=100.0,
        help="Budget written into real-run zf.yaml workspaces.",
    )
    parser.add_argument(
        "--reuse-real-worktree",
        action="store_true",
        help="Do not delete the real-run workspace before materializing zf.yaml.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Additional argument passed to the focused pytest command.",
    )
    return parser.parse_args(argv)


def selected_real_scenarios(kind: str) -> list[Scenario]:
    if kind == "none":
        return []
    if kind == "all":
        return [SCENARIOS["codex"], SCENARIOS["mixed"]]
    return [SCENARIOS[kind]]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.include_real != "none" and not args.confirm_real:
        print(
            "error: --include-real starts provider CLIs and requires "
            "--confirm-real",
            file=sys.stderr,
        )
        return 2

    results: list[StepResult] = []
    results.append(validate_config(CODEX_CONFIG))
    results.append(validate_config(MIXED_CONFIG))
    results.append(assert_config_topology())

    if not args.skip_unit:
        results.extend(
            run_backlog_test_groups(
                smoke=args.smoke,
                extra_pytest_args=args.pytest_arg,
            )
        )

    if not args.skip_dry_run:
        for scenario in (SCENARIOS["codex"], SCENARIOS["mixed"]):
            results.extend(run_dry_start(scenario))

    for scenario in selected_real_scenarios(args.include_real):
        worktree = (
            args.codex_worktree
            if scenario.name == "codex"
            else args.mixed_worktree
        )
        results.append(
            run_real_scenario(
                scenario,
                worktree=worktree,
                budget_usd=args.real_budget_usd,
                clean=not args.reuse_real_worktree,
            )
        )

    return print_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
