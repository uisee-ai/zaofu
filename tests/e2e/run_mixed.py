"""tests/e2e/run_mixed.py — one-click mixed-backend e2e runner.

Stitches the existing pieces (prepare script + zf start + zf emit +
phase reports + invariant guards) into a single Python entrypoint so
a full mixed-backend e2e run goes from "7 manual shell steps" to
"one command + watch progress".

Pipeline:
  1. (optional) reset state in /tmp/zaofu-mixed/.zf
  2. zf start --foreground (background: spawn tmux + workers + watcher)
  4. wait for watcher session.started
  5. zf emit user.message ... × N
  6. poll events.jsonl for `task.status_changed testing→done` × N
     (or timeout)
  7. zf stop (graceful shutdown)
  8. mixed_phase_report — phase breakdown
  9. invariant guards (single_truth + cost_equality)
  10. print final summary (dispatch distribution, cost, artifacts)

Usage:
  # ALWAYS launch from the main repo root. A relative ``PYTHONPATH=src``
  # combined with launching from inside the worktree (cwd=/tmp/zaofu-mixed)
  # is the exact shadow-import trap fixed by B-WORKTREE-SHADOW-IMPORT-01,
  # which would re-trigger here for the runner's *own* main process.
  cd /path/to/zaofu       # or wherever your repo is checked out
  PYTHONPATH="$(pwd)/src" python -m tests.e2e.run_mixed \\
      --worktree /tmp/zaofu-mixed \\
      --tasks 3 \\
      --timeout 600 \\
      --confirm

The runner self-checks at boot that ``import zf`` resolves to the same
``src/zf/`` it expects (REPO_ROOT/src/zf) and aborts loud otherwise; see
``_check_runner_environment``.

This burns real claude/codex tokens. The --confirm flag is required
to proceed; otherwise the script does a dry-run that prints the plan
and exits without touching the API.

Default seeds (3 trivial functions: greet/add/reverse) are used when
no --seed-file is provided. Pass --seed-file PATH to use one prompt
per line for custom scenarios.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKTREE = Path("/tmp/zaofu-mixed")
DEFAULT_SEEDS = [
    "Task A: 新建 src/greet.py::hello(name) 返回 Hello, {name}!，"
    "tests/test_greet.py 覆盖 basic/empty/unicode；"
    "验证命令使用 python3 -m pytest tests/test_greet.py",
    "Task B: 新建 src/add_fn.py::add(a,b) 返回 a+b，"
    "tests/test_add_fn.py 覆盖 positive/negative/zero；"
    "验证命令使用 python3 -m pytest tests/test_add_fn.py",
    "Task C: 新建 src/reverse_fn.py::reverse(s) 返回 s[::-1]，"
    "tests/test_reverse_fn.py 覆盖 basic/empty/unicode；"
    "验证命令使用 python3 -m pytest tests/test_reverse_fn.py",
]

FATAL_EVENT_TYPES = frozenset({
    "orchestrator.dispatch_failed",
    "task.invalid_transition",
    "cost.budget.exceeded",
    "run.failed",
    "ship.failed",
    "task.orphaned",
    "worker.respawn.failed",
    "worker.stuck.recovery_failed",
})


# ---------------- helpers ----------------


def _subprocess_env() -> dict[str, str]:
    """Force PYTHONPATH to the runner's repo src (absolute) so subprocess
    `zf start` doesn't shadow-import from a worktree's stale src/zf/.

    The mixed-backend smoke runs in a git worktree (e.g. /tmp/zaofu-mixed)
    that has its own ``src/zf/`` at whatever commit the experiment branch
    was checked out at — typically older than the runner's branch. With
    ``cwd=worktree`` Python adds the worktree's `src` to sys.path first
    (because of CWD precedence), and the new ``backends:`` schema field
    silently disappears (RoleConfig falls back to backend='python' →
    MockAdapter → ['cat']). Diagnosed 2026-04-27.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def _check_runner_environment() -> None:
    """Abort if the runner main process imported ``zf`` from somewhere
    other than ``REPO_ROOT/src/zf``.

    The fix in ``_subprocess_env`` only protects child ``zf`` invocations.
    If the runner itself was launched from inside a worktree with a
    relative ``PYTHONPATH=src``, the runner's *main process* shadow-imports
    the worktree's stale modules — silent until something inside the
    runner happens to touch a recently-changed schema. Catch it loud here.
    """
    import zf
    actual_pkg_dir = Path(zf.__file__).resolve().parent       # .../src/zf
    expected_pkg_dir = (REPO_ROOT / "src" / "zf").resolve()
    if actual_pkg_dir == expected_pkg_dir:
        return
    sys.exit(
        f"error: runner main process imported zf from\n"
        f"         {actual_pkg_dir}\n"
        f"       but the runner's REPO_ROOT/src/zf is\n"
        f"         {expected_pkg_dir}\n"
        f"       This is the B-WORKTREE-SHADOW-IMPORT-01 trap firing on the\n"
        f"       runner's own process. Re-launch from the main repo:\n"
        f"         cd {REPO_ROOT}\n"
        f"         PYTHONPATH=\"$(pwd)/src\" python -m tests.e2e.run_mixed --confirm"
    )


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("env", _subprocess_env())
    return subprocess.run(cmd, check=False, **kw)


def _emit_user_message(worktree: Path, text: str) -> None:
    _run(
        ["zf", "emit", "user.message", "--actor", "human",
         "--payload", json.dumps({"text": text})],
        cwd=worktree,
    )


def _count_event(events_path: Path, type_: str) -> int:
    if not events_path.exists():
        return 0
    n = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == type_:
            n += 1
    return n


def _count_status_done(events_path: Path) -> int:
    """Count `task.status_changed` events with payload to=='done'."""
    if not events_path.exists():
        return 0
    n = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "task.status_changed":
            payload = e.get("payload", {}) or {}
            if payload.get("to") == "done":
                n += 1
    return n


def _scan_first_fatal_event(
    events_path: Path,
    done: int,
    expected: int,
    offset: int = 0,
) -> tuple[dict | None, int]:
    """Return the first known fatal event plus the next file offset."""
    if not events_path.exists():
        return None, 0
    size = events_path.stat().st_size
    if offset > size:
        offset = 0
    fatal = None
    with events_path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            event_type = event.get("type")
            if event_type in FATAL_EVENT_TYPES:
                fatal = event
                break
            if event_type == "loop.stopped" and done < expected:
                fatal = event
                break
        next_offset = f.tell()
    return fatal, next_offset


def _print_fatal_event(event: dict | None, elapsed_s: float) -> None:
    if not event:
        return
    payload = event.get("payload") or {}
    print("\n========== first fatal event ==========")
    print(f"type:       {event.get('type', '')}")
    print(f"task_id:    {event.get('task_id') or ''}")
    print(f"actor:      {event.get('actor') or ''}")
    print(f"elapsed:    {elapsed_s:.1f}s")
    print(f"payload:    {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def _pid_is_worktree_watcher(pid: int, worktree: Path) -> bool:
    """Return True only for this worktree's `zf start --foreground`.

    The lock file may be stale and PIDs can be reused, so validate both
    cmdline shape and cwd before sending a signal.
    """
    proc = Path("/proc") / str(pid)
    try:
        raw_cmdline = (proc / "cmdline").read_bytes()
        cwd = (proc / "cwd").resolve()
    except OSError:
        return False
    cmdline = [part for part in raw_cmdline.decode(errors="replace").split("\0") if part]
    if "start" not in cmdline or "--foreground" not in cmdline:
        return False
    try:
        return cwd == worktree.resolve()
    except OSError:
        return False


def _kill_worktree_watcher(worktree: Path) -> None:
    lock_path = worktree / ".zf" / "loop.lock"
    if not lock_path.exists():
        return
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid() or not _pid_is_worktree_watcher(pid, worktree):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_lingering(worktree: Path, session_name: str | None = None) -> None:
    """Kill leftover runtime owned by this worktree (best-effort).

    When ``session_name`` is given, also tmux kill-session to clear
    any zombie pane layout from a prior crash. Otherwise some `zf start`
    paths will skip yaml-driven layout and reuse the stale window-per-
    role session, masking pane_grid configs.
    """
    _kill_worktree_watcher(worktree)
    if session_name:
        _run(["tmux", "kill-session", "-t", session_name],
             stderr=subprocess.DEVNULL)
    time.sleep(1)


def _read_session_name(worktree: Path) -> str | None:
    """Best-effort read of session.tmux_session from worktree yaml."""
    try:
        import yaml  # noqa
        data = yaml.safe_load((worktree / "zf.yaml").read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    sess = (data.get("session") or {}).get("tmux_session")
    return sess if isinstance(sess, str) else None


# ---------------- pipeline steps ----------------


@dataclass
class RunSummary:
    tasks_seeded: int
    tasks_expected_done: int
    tasks_done: int
    elapsed_s: float
    dispatch_by_instance: dict[str, int]
    builds_done: dict[str, int]
    arch_proposals: dict[str, int]
    design_critiques: dict[str, int]
    test_passed: dict[str, int]
    judge_passed: dict[str, int]
    gate_failed: dict[str, int]
    total_cost_usd: float
    invariants: dict[str, str]    # name -> "pass" | "fail: <reason>"
    timed_out: bool
    run_status: str = "ok"


@dataclass
class WaitResult:
    status: str  # passed | fatal | timeout
    done: int
    expected: int
    elapsed_s: float
    fatal_event: dict | None = None

    @property
    def timed_out(self) -> bool:
        return self.status == "timeout"


def reset_state(worktree: Path) -> None:
    sd = worktree / ".zf"
    print(f"[reset] clearing state in {sd}")
    for p in [
        sd / "events.jsonl", sd / "kanban.json", sd / "feature_list.json",
        sd / "role_sessions.yaml", sd / "cost.jsonl",
        sd / "kanban-terminal-index.json", sd / "progress.md",
        sd / "circuits.json",
    ]:
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    for d in [
        "briefings",
        "logs",
        "memory",
        "artifacts",
        "instructions",
        "kanban",
        "feature_list",
        "events",
        "cost",
    ]:
        path = sd / d
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            path.mkdir()
    (sd / "events.jsonl").write_text("")
    (sd / "kanban.json").write_text("[]")
    (sd / "feature_list.json").write_text("[]")
    (sd / "session.yaml").write_text(
        "session_id: ''\nruntime_state: initialized\nlatest_event_offset: 0\n"
    )
    # Purge claude project sessions so deterministic UUIDs don't replay
    # stale conversations from the prior run.
    project_id = "-" + str(worktree.resolve()).lstrip("/").replace("/", "-")
    claude_dir = Path.home() / ".claude" / "projects" / project_id
    if claude_dir.exists():
        shutil.rmtree(claude_dir, ignore_errors=True)
        print(f"[reset] purged {claude_dir}")


def start_harness(worktree: Path) -> int:
    # Current `zf start` runs the watcher in foreground by default.  Older
    # versions returned after spawning workers, so this helper remains as a
    # compatibility checkpoint while start_watcher() owns the single real
    # process launch. Calling bare `zf start` here would block before seeds are
    # emitted.
    print(f"[start] deferred to background watcher in {worktree}")
    return 0


def start_watcher(worktree: Path) -> int:
    print("[watcher] zf start --foreground (background)")
    log_path = worktree / ".zf" / "logs" / "watcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events_path = worktree / ".zf" / "events.jsonl"
    # Snapshot loop.started count before launch — the foreground watcher
    # emits its own loop.started on boot, so we want count to *increase*
    # by 1 (not fix it at "2", which only holds when zf start emitted
    # session.started, which it doesn't on a fresh startup).
    before = _count_event(events_path, "loop.started")
    proc = subprocess.Popen(
        ["zf", "start", "--foreground"],
        cwd=worktree,
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=_subprocess_env(),
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if _count_event(events_path, "loop.started") > before:
            print(f"[watcher] ready (pid={proc.pid})")
            return proc.pid
        time.sleep(2)
    print(f"[watcher] WARNING: did not see new loop.started within 90s "
          f"(before={before})", file=sys.stderr)
    return proc.pid


def seed_tasks(worktree: Path, seeds: list[str]) -> None:
    print(f"[seed] emitting {len(seeds)} user.message events")
    for i, s in enumerate(seeds, 1):
        _emit_user_message(worktree, s)
        print(f"  [{i}/{len(seeds)}] {s[:60]}…")


def wait_for_done(
    worktree: Path, expected: int, timeout_s: int,
) -> WaitResult:
    """Poll until N tasks are done, a fatal event appears, or timeout fires."""
    events_path = worktree / ".zf" / "events.jsonl"
    print(f"[wait] expecting {expected} task.status_changed→done "
          f"(timeout {timeout_s}s)")
    start = time.time()
    last_done = -1
    fatal_offset = 0
    while time.time() - start < timeout_s:
        done = _count_status_done(events_path)
        elapsed = time.time() - start
        if done != last_done:
            print(f"  [{int(elapsed):>4}s] done={done}/{expected}")
            last_done = done
        if done >= expected:
            return WaitResult("passed", done, expected, elapsed)
        fatal, fatal_offset = _scan_first_fatal_event(
            events_path, done, expected, fatal_offset,
        )
        if fatal is not None:
            return WaitResult("fatal", done, expected, elapsed, fatal_event=fatal)
        time.sleep(5)
    return WaitResult(
        "timeout",
        max(last_done, 0),
        expected,
        time.time() - start,
    )


def stop_harness(worktree: Path, session_name: str | None = None) -> None:
    print("[stop] zf stop")
    _run(["zf", "stop"], cwd=worktree)
    _kill_lingering(worktree, session_name=session_name)


def run_phase_report(worktree: Path) -> None:
    """Invoke mixed_phase_report on .zf/events.jsonl (prints to stdout)."""
    events_path = worktree / ".zf" / "events.jsonl"
    if not events_path.exists():
        print("[phase-report] no events.jsonl, skipping")
        return
    print("\n========== mixed phase report ==========")
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from tests.e2e.mixed_phase_report import print_mixed_report  # noqa
        print_mixed_report(events_path)
    except Exception as e:  # noqa: BLE001
        print(f"[phase-report] FAILED: {e}", file=sys.stderr)


def run_invariant_guards(worktree: Path) -> dict[str, str]:
    """Run the two existing shell guards. Returns name → status."""
    sd = worktree / ".zf"
    out: dict[str, str] = {}
    for guard in ("invariant_single_truth.sh", "invariant_cost_equality.sh"):
        path = REPO_ROOT / "tests" / "longhorizon" / "guards" / guard
        if not path.exists():
            out[guard] = "skip: not found"
            continue
        proc = _run(
            ["bash", str(path), str(sd)],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            out[guard] = "pass"
        else:
            stderr = (proc.stderr or "").strip().replace("\n", " | ")
            out[guard] = f"fail: {stderr or 'rc=' + str(proc.returncode)}"
    return out


def collect_summary(
    worktree: Path, seeds_n: int, expected_done: int, elapsed: float, timed_out: bool,
    invariants: dict[str, str],
    run_status: str = "ok",
) -> RunSummary:
    from collections import Counter
    events_path = worktree / ".zf" / "events.jsonl"
    dispatch = Counter()
    builds_done = Counter()
    arch_proposals = Counter()
    design_critiques = Counter()
    test_passed = Counter()
    judge_passed = Counter()
    gate_failed = Counter()
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if t == "task.dispatched":
                dispatch[e.get("payload", {}).get("assignee", "?")] += 1
            elif t == "arch.proposal.done":
                arch_proposals[e.get("actor", "?")] += 1
            elif t == "design.critique.done":
                design_critiques[e.get("actor", "?")] += 1
            elif t == "dev.build.done":
                builds_done[e.get("actor", "?")] += 1
            elif t == "gate.failed":
                gate_failed[e.get("actor", "?")] += 1
            elif t == "test.passed":
                test_passed[e.get("actor", "?")] += 1
            elif t == "judge.passed":
                judge_passed[e.get("actor", "?")] += 1
    # Cost via CostTracker
    sys.path.insert(0, str(REPO_ROOT / "src"))
    total = 0.0
    try:
        from zf.core.cost.tracker import CostTracker
        cost_path = worktree / ".zf" / "cost.jsonl"
        if cost_path.exists():
            total = round(CostTracker(cost_path).total_usd(), 4)
    except Exception:
        pass
    return RunSummary(
        tasks_seeded=seeds_n,
        tasks_expected_done=expected_done,
        tasks_done=_count_status_done(events_path),
        elapsed_s=elapsed,
        dispatch_by_instance=dict(dispatch),
        builds_done=dict(builds_done),
        arch_proposals=dict(arch_proposals),
        design_critiques=dict(design_critiques),
        test_passed=dict(test_passed),
        judge_passed=dict(judge_passed),
        gate_failed=dict(gate_failed),
        total_cost_usd=total,
        invariants=invariants,
        timed_out=timed_out,
        run_status=run_status,
    )


def print_summary(summary: RunSummary) -> int:
    print("\n========== final summary ==========")
    status = summary.run_status.upper()
    if summary.timed_out:
        status = "TIMEOUT"
    print(f"status:           {status}")
    print(f"tasks seeded:     {summary.tasks_seeded}")
    print(f"tasks expected:   {summary.tasks_expected_done}")
    print(f"tasks done:       {summary.tasks_done} / {summary.tasks_expected_done}")
    print(f"elapsed:          {summary.elapsed_s:.1f}s")
    print(f"total cost:       ${summary.total_cost_usd:.4f}")
    print("dispatch by instance:")
    for k, v in sorted(summary.dispatch_by_instance.items()):
        print(f"  {k:<14} {v}")
    print(f"arch.proposal.done: {dict(summary.arch_proposals)}")
    print(f"design.critique:   {dict(summary.design_critiques)}")
    print(f"dev.build.done:   {dict(summary.builds_done)}")
    print(f"test.passed:      {dict(summary.test_passed)}")
    print(f"judge.passed:     {dict(summary.judge_passed)}")
    print(f"gate.failed:      {dict(summary.gate_failed)}")
    print("invariant guards:")
    for k, v in summary.invariants.items():
        marker = "✓" if v == "pass" else "✗"
        print(f"  {marker} {k}: {v}")
    fail_count = sum(1 for v in summary.invariants.values() if v != "pass")
    full = (summary.tasks_done >= summary.tasks_expected_done)
    return 0 if (full and fail_count == 0) else 1


# ---------------- main ----------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_mixed.py", description=__doc__)
    p.add_argument("--worktree", type=Path, default=DEFAULT_WORKTREE,
                   help="Path with .zf/ + zf.yaml (default /tmp/zaofu-mixed)")
    p.add_argument("--tasks", type=int, default=3,
                   help="Use first N default seeds (ignored if --seed-file)")
    p.add_argument("--seed-file", type=Path, default=None,
                   help="One prompt per line; overrides --tasks")
    p.add_argument(
        "--expected-done",
        type=int,
        default=None,
        help=(
            "Expected completed task count. Defaults to number of seeds. "
            "Use this when one large seed should be decomposed into multiple tasks."
        ),
    )
    p.add_argument("--timeout", type=int, default=900,
                   help="Max seconds to wait for all tasks done (default 900)")
    p.add_argument("--no-reset", action="store_true",
                   help="Skip state reset (resume from existing .zf)")
    p.add_argument("--no-stop", action="store_true",
                   help="Leave harness running after summary (for inspection)")
    p.add_argument("--confirm", action="store_true",
                   help="Required to actually run; otherwise dry-run")
    return p.parse_args(argv)


def load_seeds(args: argparse.Namespace) -> list[str]:
    if args.seed_file:
        return [
            l.strip() for l in args.seed_file.read_text().splitlines()
            if l.strip()
        ]
    return DEFAULT_SEEDS[: args.tasks]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _check_runner_environment()
    worktree: Path = args.worktree
    if not worktree.exists():
        print(f"error: worktree {worktree} not found.\n"
              f"       Run tests/e2e/scripts/prepare_mixed_e2e.sh first.",
              file=sys.stderr)
        return 2
    if not (worktree / "zf.yaml").exists():
        print(f"error: {worktree}/zf.yaml not found", file=sys.stderr)
        return 2
    seeds = load_seeds(args)
    if not seeds:
        print("error: no seeds (empty --seed-file?)", file=sys.stderr)
        return 2
    expected_done = args.expected_done if args.expected_done is not None else len(seeds)
    if expected_done < 1:
        print("error: --expected-done must be >= 1", file=sys.stderr)
        return 2
    print(f"plan: {len(seeds)} task(s), worktree={worktree}, "
          f"expected_done={expected_done}, timeout={args.timeout}s")
    print(f"yaml: {worktree}/zf.yaml")
    if not args.confirm:
        print("\n[dry-run] pass --confirm to actually start the harness "
              "(burns real claude/codex tokens).")
        for i, s in enumerate(seeds, 1):
            print(f"  seed {i}: {s[:80]}…")
        return 0

    session_name = _read_session_name(worktree)
    _kill_lingering(worktree, session_name=session_name)
    if not args.no_reset:
        reset_state(worktree)
    if start_harness(worktree) != 0:
        return 3
    start_watcher(worktree)
    seed_tasks(worktree, seeds)
    started = time.time()
    wait_result = wait_for_done(worktree, expected_done, args.timeout)
    elapsed = time.time() - started
    if not args.no_stop:
        stop_harness(worktree, session_name=session_name)
    run_phase_report(worktree)
    invariants = run_invariant_guards(worktree)
    summary = collect_summary(
        worktree,
        len(seeds),
        expected_done,
        elapsed,
        wait_result.timed_out,
        invariants,
        run_status=wait_result.status,
    )
    rc = print_summary(summary)
    if wait_result.status == "fatal":
        _print_fatal_event(wait_result.fatal_event, wait_result.elapsed_s)
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
