"""One-shot driver: run tests/longhorizon/loop with a REAL zaofu
harness runner. Spawns `zf start --foreground` in /tmp/zaofu-multi,
emits the S1 scenario user.message, waits for pipeline completion,
then stops — the loop then computes MetricsSnapshot and writes
results.tsv.

Usage:
    PYTHONPATH=src python3 tests/longhorizon/run_once_real.py

Timeouts are tuned for the is_prime / fibonacci class of scenario
that has already shipped happy-path e2e in prior runs (~3 min).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from zf.core.events.log import EventLog

from tests.longhorizon.loop import LoopConfig, run_loop
from tests.longhorizon.report import write_report


PROJECT = Path("/tmp/zaofu-multi")
STATE_DIR = PROJECT / ".zf"
RESULTS = Path(__file__).parent / "results.tsv"
REPORT = Path(__file__).parent / "longhorizon-health-v1.md"
USER_MESSAGE = (
    "写一个 Python 函数 fibonacci(n) 返回第 n 个斐波那契数"
    "（0,1,1,2,3,5...），放在 fib.py。然后写测试覆盖 n=0/1/5。"
)


def _wait_until(state_dir: Path, pred, timeout: int, step: float = 2) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            events = EventLog(state_dir / "events.jsonl").read_all()
        except Exception:
            events = []
        if pred(events):
            return True
        time.sleep(step)
    return False


def real_runner(state_dir: Path, scenario: str) -> None:
    """LH-6 real scenario runner — drives a full zf harness iteration."""
    project = state_dir.parent

    # 1. Clean slate — removes .zf + Claude session files from prior runs.
    if state_dir.exists():
        shutil.rmtree(state_dir)
    # ~/.claude/projects/-tmp-zaofu-multi/*.jsonl
    escape = "-" + str(project).strip("/").replace("/", "-")
    claude_dir = Path.home() / ".claude/projects" / escape
    if claude_dir.exists():
        for f in claude_dir.glob("*.jsonl"):
            try:
                f.unlink()
            except Exception:
                pass

    print(f"[real_runner] zf init in {project}", flush=True)
    subprocess.run(
        ["zf", "init", "--force"], cwd=project,
        check=True, capture_output=True,
    )

    start_log = project / ".zf-start.log"
    if start_log.exists():
        start_log.unlink()
    # tmux-wrapped launch: `zf start --foreground` runs inside a
    # dedicated driver tmux session, same as an interactive shell
    # user would do. Direct subprocess.Popen reproducibly loses the
    # orchestrator tmux window (4/4 trials) — root cause is spawn-time
    # race between Popen-child tmux env and orchestrator pane startup
    # and should be investigated in a spawn-reliability sprint.
    driver_session = "zf-loop-driver"
    subprocess.run(
        ["tmux", "kill-session", "-t", driver_session],
        capture_output=True,
    )
    print(f"[real_runner] launching zf start via tmux:{driver_session}",
          flush=True)
    launch_cmd = (
        f"cd {project} && "
        f"PYTHONUNBUFFERED=1 zf start --foreground "
        f"> {start_log} 2>&1"
    )
    subprocess.run([
        "tmux", "new-session", "-d", "-s", driver_session,
        "bash", "-c", launch_cmd,
    ], check=True)

    class _DriverProc:
        """Adapter so the existing try/finally cleans up uniformly."""
        def __init__(self, session: str) -> None:
            self.session = session

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            subprocess.run(
                ["tmux", "kill-session", "-t", self.session],
                capture_output=True,
            )

    proc = _DriverProc(driver_session)
    log_fh = None  # tmux writes the log file directly
    try:
        # Wait until `zf start` prints the "Watcher: running in foreground"
        # banner — only THEN are all worker panes spawned. Relying on the
        # loop.started event alone is a race: watcher.poll_once fires it
        # ~300ms before some workers finish spawning, and a hot dispatch
        # into a missing tmux pane crashes the whole iteration.
        def _ready() -> bool:
            try:
                text = start_log.read_text()
                return "Watcher: running in foreground" in text
            except Exception:
                return False
        t0 = time.time()
        while time.time() - t0 < 300:
            if _ready():
                break
            time.sleep(2)
        else:
            tail = start_log.read_text()[-800:]
            raise RuntimeError(
                f"zf start did not print ready banner in 300s; "
                f"last log: {tail!r}"
            )

        # Belt-and-suspenders: loop.started must also be in events.jsonl.
        if not _wait_until(
            state_dir,
            lambda evts: any(e.type == "loop.started" for e in evts),
            timeout=30,
        ):
            raise RuntimeError("loop.started missing after ready banner")

        # Extra settle time: the banner means Python-side setup is done,
        # but tmux window creation (especially the orchestrator pane)
        # can finalise slightly later. Prior run showed `can't find
        # window: orchestrator` ~200ms after banner — sleep 20s to let
        # all panes get their handlers registered.
        time.sleep(20)

        # Verify orchestrator window actually exists before emitting,
        # so the first dispatch has a pane to land in.
        check = subprocess.run(
            ["tmux", "list-windows", "-t", "zf-multi"],
            capture_output=True, text=True,
        )
        if "orchestrator" not in check.stdout:
            raise RuntimeError(
                f"orchestrator window missing after spawn: {check.stdout!r}"
            )

        print("[real_runner] emit user.message", flush=True)
        subprocess.run(
            ["zf", "emit", "user.message", "--payload",
             json.dumps({"text": USER_MESSAGE}, ensure_ascii=False)],
            cwd=project, check=True, capture_output=True,
        )

        print("[real_runner] waiting for judge.passed (up to 20 min)",
              flush=True)
        if not _wait_until(
            state_dir,
            lambda evts: any(e.type == "judge.passed" for e in evts),
            timeout=1200,
        ):
            raise RuntimeError(
                "pipeline did not reach judge.passed in 1200s"
            )

        # Give the orchestrator a window to call zf kanban move done.
        time.sleep(45)
        print("[real_runner] pipeline done, tearing down", flush=True)
    finally:
        try:
            subprocess.run(
                ["zf", "stop"], cwd=project, capture_output=True, timeout=60,
            )
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        if log_fh is not None:
            try:
                log_fh.close()
            except Exception:
                pass


def main() -> int:
    cfg = LoopConfig(
        scenario="S1",
        iterations=1,
        state_dir=STATE_DIR,
        results_path=RESULTS,
        dry_run=True,  # skip commit/revert so the loop doesn't touch git
    )
    n = run_loop(cfg, runner=real_runner)
    print(f"\nloop wrote {n} row(s) to {RESULTS}")
    if n > 0:
        write_report(RESULTS, REPORT)
        print(f"report at {REPORT}")
        print("\n--- results.tsv ---")
        print(RESULTS.read_text())
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
