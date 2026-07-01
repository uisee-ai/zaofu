"""LH-6.T1: autoresearch-style 8-phase loop driver (skeleton).

Phases (from /path/to/autoresearch autonomous-loop-protocol):
  0 Preflight  — git clean + zf installed + baseline exists
  1 Review     — last 10 results.tsv rows + git log -20
  2 Ideate     — pick ONE zf.yaml knob from config_space
  3 Modify     — change that knob
  4 Commit     — experiment(longhorizon): <change>
  5 Verify     — run scenario → compute MetricsSnapshot → pick primary
  6 Decide     — delta < -10% or guard fail → git revert HEAD
  7 Log        — append results.tsv row

This file ships the skeleton: types, config space loader, phase hooks
with pluggable scenario runner. The "run scenario" phase is pluggable
so unit tests can inject a mock runner instead of spawning a real zf
harness (the real runner costs $$ in Claude API).

CLI entry:
    python -m tests.longhorizon.loop --scenario=S1 --iterations=5 \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from tests.longhorizon.metrics import compute_snapshot, snapshot_to_row
from tests.longhorizon.results_log import ResultRow, append_row, read_recent


_CONFIG_SPACE = {
    "context_window_tokens": [100_000, 150_000, 200_000],
    "recycle_threshold": [0.6, 0.7, 0.8],
    "stuck_threshold_seconds": [180, 300, 600],
    "max_rework_attempts": [2, 3, 5],
    "verification_semantic_enabled": [True, False],
}


@dataclass
class LoopConfig:
    scenario: str
    iterations: int
    state_dir: Path
    results_path: Path
    dry_run: bool = False


ScenarioRunner = Callable[[Path, str], None]


def _default_runner(state_dir: Path, scenario: str) -> None:
    """Real-scenario runner: launches zaofu + drives the scenario.

    Not called by unit tests — too expensive. Stubbed here so the CLI
    invocation of this module doesn't crash if someone runs it without
    supplying a custom runner.
    """
    raise NotImplementedError(
        "Default scenario runner is a stub — wire a real harness in "
        "tests/longhorizon/scenarios/<name>/run.py for the production "
        "loop. Unit tests inject their own runner."
    )


# ---- Phases ----

def phase_preflight(cfg: LoopConfig) -> bool:
    """Gate the loop on basic sanity. Returns False → abort."""
    # 1. Git repo exists.
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cfg.state_dir.parent, check=True, capture_output=True,
        )
    except Exception:
        return False
    # 2. Scenario file exists.
    scenario_file = (
        Path(__file__).parent / "scenarios" / f"{cfg.scenario}.yaml"
    )
    if not scenario_file.exists():
        return False
    return True


def phase_review(cfg: LoopConfig, n: int = 10) -> list[dict]:
    """Read last N rows from results.tsv + skim git log -20 (shown
    only in the CLI driver, not returned)."""
    return read_recent(cfg.results_path, n=n)


def phase_ideate(history: list[dict]) -> tuple[str, object]:
    """Pick the next knob + value. Current strategy: round-robin by
    iteration count over config_space; smarter search is LH-6.5."""
    n = len(history)
    keys = list(_CONFIG_SPACE.keys())
    key = keys[n % len(keys)]
    values = _CONFIG_SPACE[key]
    value = values[(n // len(keys)) % len(values)]
    return key, value


def phase_modify(cfg: LoopConfig, key: str, value: object) -> None:
    """Apply (key, value) by patching zf.yaml in cfg.state_dir.parent.

    Skeleton: for the skeleton we write a side file
    tests/longhorizon/last_modify.yaml describing the intended change.
    A real runner will actually update zf.yaml.
    """
    out = Path(__file__).parent / "last_modify.yaml"
    out.write_text(yaml.safe_dump({"key": key, "value": value}))


def phase_commit(cfg: LoopConfig, key: str, value: object) -> str:
    """Commit the modify change. Returns the new HEAD sha or 'dry-run'."""
    if cfg.dry_run:
        return "dry-run"
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=cfg.state_dir.parent, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m",
             f"experiment(longhorizon): {key}={value}"],
            cwd=cfg.state_dir.parent, check=True, capture_output=True,
        )
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cfg.state_dir.parent, check=True, capture_output=True,
            text=True,
        )
        return r.stdout.strip()
    except Exception:
        return "commit-failed"


def phase_verify(
    cfg: LoopConfig, runner: ScenarioRunner,
):
    """Run the scenario and compute a MetricsSnapshot from the resulting
    state_dir. Returns (snapshot, guard_status)."""
    runner(cfg.state_dir, cfg.scenario)
    snap = compute_snapshot(cfg.state_dir)
    guard_status = run_guards(cfg)
    return snap, guard_status


def phase_decide(prev_best_vcr: float, snap, guard_status: str) -> str:
    """Return a status token: baseline / keep / discard / guard_fail."""
    if guard_status.startswith("fail:"):
        return "guard_fail"
    if prev_best_vcr <= 0:
        return "baseline"
    delta = snap.vcr - prev_best_vcr
    if delta < -0.10 * prev_best_vcr:
        return "discard"
    return "keep"


def phase_log(cfg: LoopConfig, row: ResultRow) -> None:
    append_row(cfg.results_path, row)


def run_guards(cfg: LoopConfig) -> str:
    """Run each .sh under tests/longhorizon/guards/. Return 'pass' if
    all exit 0, else 'fail:<first_failed_name>'."""
    guard_dir = Path(__file__).parent / "guards"
    if not guard_dir.exists():
        return "pass"
    for script in sorted(guard_dir.glob("*.sh")):
        try:
            r = subprocess.run(
                ["bash", str(script)],
                cwd=cfg.state_dir.parent, capture_output=True, timeout=30,
            )
            if r.returncode != 0:
                return f"fail:{script.stem}"
        except Exception as e:
            return f"fail:{script.stem}:{e}"
    return "pass"


# ---- Orchestration ----

def run_loop(cfg: LoopConfig, runner: ScenarioRunner | None = None) -> int:
    """Drive N iterations. Returns the number of successful iterations
    (rows appended)."""
    runner = runner or _default_runner
    if not phase_preflight(cfg):
        return 0

    rows_written = 0
    best_vcr = 0.0
    for i in range(cfg.iterations):
        history = phase_review(cfg)
        key, value = phase_ideate(history)
        phase_modify(cfg, key, value)
        sha = phase_commit(cfg, key, value)
        try:
            snap, guard = phase_verify(cfg, runner)
        except Exception as e:
            row = ResultRow(
                iteration=i, commit=sha,
                vcr=0.0, mtts=0.0, cost_per_task=0.0, rework_ratio=0.0,
                guard_status="unrun",
                note=f"crash: {type(e).__name__}: {e}",
            )
            phase_log(cfg, row)
            rows_written += 1
            continue

        status = phase_decide(best_vcr, snap, guard)
        if status == "keep":
            best_vcr = max(best_vcr, snap.vcr)
        row = snapshot_to_row(
            snap, iteration=i, commit=sha,
            guard_status=guard, status=status,
            note=f"{key}={value}",
        )
        phase_log(cfg, row)
        rows_written += 1

        if status == "discard" and not cfg.dry_run:
            try:
                subprocess.run(
                    ["git", "revert", "--no-edit", "HEAD"],
                    cwd=cfg.state_dir.parent,
                    check=True, capture_output=True,
                )
            except Exception:
                pass
    return rows_written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="longhorizon.loop")
    p.add_argument("--scenario", default="S1")
    p.add_argument("--iterations", type=int, default=5)
    p.add_argument("--state-dir", default=".zf")
    p.add_argument(
        "--results", default=str(Path(__file__).parent / "results.tsv"),
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    cfg = LoopConfig(
        scenario=args.scenario,
        iterations=args.iterations,
        state_dir=Path(args.state_dir).resolve(),
        results_path=Path(args.results),
        dry_run=args.dry_run,
    )
    n = run_loop(cfg)
    print(f"Loop wrote {n} rows to {cfg.results_path}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
