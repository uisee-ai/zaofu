"""Direct inner-harness bypass runner for autoresearch loop."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from zf.autoresearch.loop_types import LoopConfig


_FATAL_INNER_EVENT_TYPES = frozenset({
    "orchestrator.dispatch_failed",
    "task.orphaned",
    # NOTE: worker.respawn.failed deliberately NOT here. It fires
    # whenever a tmux pane disappears (operator kill / stuck recovery
    # / temporary subprocess crash). In bypass-loop runs it fired
    # only because the outer loop was killed while judge was being
    # dispatched — a benign signal, not a fatal harness failure.
    # Kernel-side stale-handoff detection (60f8058
    # _heartbeat_current_task_still_owned) reduces but does not
    # eliminate the trigger frequency.
    "worker.stuck.recovery_failed",
    "run.failed",
    "ship.failed",
})


def _rewrite_tmux_session(yaml_text: str, new_session: str) -> str:
    """Find the ``session.tmux_session`` field in a zf.yaml and replace
    its value with ``new_session``. Idempotent / regex-based so we don't
    depend on PyYAML; falls back to appending if no existing field.

    The yaml shape in zaofu always has:
        session:
          tmux_session: <name>
    """
    import re as _re
    pattern = _re.compile(
        r"(^session:\s*\n(?:\s+[^\n]*\n)*?\s+tmux_session:\s*)([^\n]*)",
        _re.MULTILINE,
    )
    match = pattern.search(yaml_text)
    if match:
        return pattern.sub(rf"\g<1>{new_session}", yaml_text, count=1)
    # No session block: append one at end.
    return yaml_text.rstrip() + f"\nsession:\n  tmux_session: {new_session}\n"


def _rewrite_state_dir_absolute(yaml_text: str, absolute_state_dir: Path) -> str:
    """Replace ``project.state_dir`` with an absolute path.

    Without this, every reader role's worktree resolves state_dir
    against its own cwd (project_context.resolve_project_context line
    27-29) and writes to /tmp/<worktree>/.zf/workdirs/<role>/project/.zf
    — fragmenting truth across N role-local .zf trees, so kanban.json
    at the project root stays empty even when the orchestrator emits
    task.created.
    """
    import re as _re
    pattern = _re.compile(
        r"(^project:\s*\n(?:\s+[^\n]*\n)*?\s+state_dir:\s*)([^\n]*)",
        _re.MULTILINE,
    )
    match = pattern.search(yaml_text)
    if match:
        old = match.group(2).strip()
        yaml_text = pattern.sub(
            rf"\g<1>{absolute_state_dir}", yaml_text, count=1,
        )
        # B8: runtime.workdirs.root / skills.pool / lock_file nest under the
        # OLD state_dir name (e.g. `.zf-mixed/workdirs`). Rewrite those
        # path-prefix references too, else WorkdirManager's PathGuard rejects
        # workdirs as outside the (now absolute) state_dir and `zf start`
        # aborts (calc-fullflow bypass-loop 2s abort). Match the trailing
        # slash so a `.zf` old value can't corrupt a `.zf-mixed` token.
        if old and old != str(absolute_state_dir):
            yaml_text = yaml_text.replace(f"{old}/", f"{absolute_state_dir}/")
        return yaml_text
    # project block exists but no state_dir? Inject one.
    project_block = _re.search(r"^project:\s*\n", yaml_text, _re.MULTILINE)
    if project_block:
        insert_at = project_block.end()
        return (
            yaml_text[:insert_at]
            + f"  state_dir: {absolute_state_dir}\n"
            + yaml_text[insert_at:]
        )
    return (
        yaml_text.rstrip()
        + f"\nproject:\n  state_dir: {absolute_state_dir}\n"
    )


def _disable_worktree_mode(yaml_text: str) -> str:
    """Force ``runtime.workdirs.enabled`` to ``false``.

    Some yaml presets use ``mode: worktree`` — each role runs in its
    own git worktree under .zf/workdirs/<role>/project. dev-1 writes
    src/<file>.py THERE, but the kernel's terminal verification on
    judge.passed (yaml's project.verification command) runs in the
    main worktree's cwd which has no src/<file>.py → pytest rc=1 →
    discriminator.failed → task.rework loop, never reaching done.

    For bypass mode where we don't care about per-role git isolation
    and just want a working pipeline, setting
    ``runtime.workdirs.enabled: false`` keeps all roles in the main
    cwd (no per-role workdir fan-out at all) so dev's writes are
    visible to verification.

    (Tried ``mode: dry-run`` first — that writes per-role metadata
    but skips creating the project_path dirs, leading to 45×
    worker.respawn.failed with Errno 2 in iter 2.)

    Not auto-applied in ``bypass_inner_run``; call sites can choose
    when worktree-mode interferes with their verification path.
    """
    import re as _re
    pattern = _re.compile(
        r"(^\s*workdirs:\s*\n(?:\s+[^\n]*\n)*?\s+enabled:\s*)(true|True|false|False)",
        _re.MULTILINE,
    )
    if pattern.search(yaml_text):
        return pattern.sub(r"\g<1>false", yaml_text, count=1)
    pattern_block = _re.compile(r"^(\s*)workdirs:\s*\n", _re.MULTILINE)
    m = pattern_block.search(yaml_text)
    if m:
        insert_at = m.end()
        indent = m.group(1) + "  "
        return (
            yaml_text[:insert_at]
            + f"{indent}enabled: false\n"
            + yaml_text[insert_at:]
        )
    return yaml_text


def _is_terminal_done_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", ""))
    if event_type in {"task.archived", "task.done", "task.done.accepted"}:
        return True
    if event_type == "task.status_changed":
        payload = event.get("payload")
        return isinstance(payload, dict) and payload.get("to") == "done"
    return False


def _terminal_task_id(event: dict[str, Any]) -> str:
    task_id = event.get("task_id")
    if task_id:
        return str(task_id)
    payload = event.get("payload")
    if isinstance(payload, dict) and payload.get("task_id"):
        return str(payload["task_id"])
    return ""


def _is_rework_event(event: dict[str, Any]) -> bool:
    return str(event.get("type") or "") in {
        "task.rework.requested",
        "task.rework.started",
    }


def _scan_terminal_done_fatal_rework(events_path: Path) -> dict[str, Any]:
    if not events_path.exists():
        return {
            "done_count": 0,
            "fatal": None,
            "rework_count": 0,
            "passed_after_rework": 0,
            "pending_rework_count": 0,
            "rework_events": [],
            "validation_kinds": [],
        }
    done_task_ids: set[str] = set()
    anonymous_done = 0
    rework_task_ids: set[str] = set()
    anonymous_rework = 0
    fatal: dict[str, Any] | None = None
    rework_events: list[dict[str, Any]] = []
    validation_kinds: set[str] = set()
    with events_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type", "")
            if _is_terminal_done_event(e):
                task_id = _terminal_task_id(e)
                if task_id:
                    done_task_ids.add(task_id)
                else:
                    anonymous_done += 1
            elif t in _FATAL_INNER_EVENT_TYPES and fatal is None:
                fatal = e
            if _is_rework_event(e):
                task_id = _terminal_task_id(e)
                if task_id:
                    rework_task_ids.add(task_id)
                else:
                    anonymous_rework += 1
                rework_events.append(e)
            if t == "task.contract.update":
                payload = e.get("payload")
                contract = payload.get("contract") if isinstance(payload, dict) else {}
                validation = (
                    contract.get("validation")
                    if isinstance(contract, dict)
                    else None
                )
                if isinstance(validation, dict):
                    kind = str(validation.get("kind") or validation.get("type") or "")
                    if kind:
                        validation_kinds.add(kind)

    passed_after_rework = len(done_task_ids & rework_task_ids)
    if anonymous_done and anonymous_rework:
        passed_after_rework += min(anonymous_done, anonymous_rework)
    pending_rework = len(rework_task_ids - done_task_ids)
    if anonymous_rework > anonymous_done:
        pending_rework += anonymous_rework - anonymous_done
    return {
        "done_count": len(done_task_ids) + anonymous_done,
        "fatal": fatal,
        "rework_count": len(rework_task_ids) + anonymous_rework,
        "passed_after_rework": passed_after_rework,
        "pending_rework_count": pending_rework,
        "rework_events": rework_events,
        "validation_kinds": sorted(validation_kinds),
    }


def _count_terminal_done_and_fatal(events_path: Path) -> tuple[int, dict[str, Any] | None]:
    """Tail events.jsonl.

    Returns (terminal done task count, first fatal event or None). The
    current kernel's canonical terminal signal is task.status_changed(to=done);
    task.archived is accepted for older/archive-oriented runners.
    """
    scan = _scan_terminal_done_fatal_rework(events_path)
    return int(scan["done_count"]), scan["fatal"]


def _count_archived_and_fatal(events_path: Path) -> tuple[int, dict[str, Any] | None]:
    """Backward-compatible alias for older tests/imports."""
    return _count_terminal_done_and_fatal(events_path)


def _try_ff_merge_worker_branches(
    worktree: Path,
    already_merged: set[str],
) -> None:
    """Fast-forward merge any ``worker/*`` branches into main.

    Without this, dev's commits live on ``worker/dev`` but main HEAD
    has no src/<file>.py — review/test worktrees (built from main)
    miss the file and pytest fails. Idempotent: only attempts each
    branch once per iter, and a non-ff branch is silently skipped.
    """
    try:
        proc = subprocess.run(
            ["git", "branch", "--list", "worker/*", "--format=%(refname:short)"],
            cwd=worktree, capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return
        for branch in proc.stdout.split():
            branch = branch.strip()
            if not branch.startswith("worker/") or branch in already_merged:
                continue
            already_merged.add(branch)
            # Use merge with --ff-only so we don't accidentally create
            # merge commits that confuse the kernel.
            subprocess.run(
                ["git", "-c", "user.email=loop@zaofu", "-c", "user.name=loop",
                 "merge", "--ff-only", branch],
                cwd=worktree, capture_output=True, text=True, timeout=20,
            )
    except Exception:
        return


def bypass_inner_run(
    *,
    scenario: str,
    run_id: str,
    cfg: LoopConfig,
) -> dict[str, Any]:
    """Minimal inner runner that bypasses autoresearch scaffolding.

    Each iter: wipe + cp yaml + zf init + zf start + emit user.message
    + poll terminal done events + zf stop. Returns the same dict shape
    the loop driver consumes (status / tasks_done / expected_done /
    fatal_event / report_path).

    All failures are returned as ``status='failed'`` or ``'fatal'``
    rather than raised; this is the inner runner protocol — the loop
    driver records the failure and reflects on it.
    """
    import shutil as _shutil
    import time as _time

    state_dir = cfg.worktree / ".zf"

    # Pre-flight: yaml_template must exist.
    if cfg.yaml_template is None or not cfg.yaml_template.exists():
        return {
            "status": "failed",
            "tasks_done": 0,
            "expected_done": cfg.expected_done,
            "state_dir": str(state_dir),
            "worktree": str(cfg.worktree),
            "fatal_event": {
                "type": "config.invalid",
                "reason": f"yaml_template not found: {cfg.yaml_template}",
            },
            "report_path": "",
        }

    # 1. Wipe state dir.
    if state_dir.exists():
        _shutil.rmtree(state_dir, ignore_errors=True)
    cfg.worktree.mkdir(parents=True, exist_ok=True)

    # 2. Copy yaml + rewrite session + state_dir.
    #
    # tmux_session → per-iter unique name (avoids panes leaking from
    # iter N-1 into iter N).
    #
    # project.state_dir → absolute path (otherwise each role's git
    # worktree resolves .zf against its own cwd and writes to
    # <worktree>/<role>/project/.zf, fragmenting truth).
    yaml_dest = cfg.worktree / "zf.yaml"
    yaml_text = cfg.yaml_template.read_text(encoding="utf-8")
    iter_session = f"zf-loop-{run_id}"
    yaml_text = _rewrite_tmux_session(yaml_text, iter_session)
    absolute_state_dir = (cfg.worktree / ".zf").resolve()
    yaml_text = _rewrite_state_dir_absolute(yaml_text, absolute_state_dir)
    yaml_dest.write_text(yaml_text, encoding="utf-8")

    # 2.5 git init + commit zf.yaml so worktree-mode roles see the
    # yaml in their per-role worktree. Without committing, git worktree
    # add checks out HEAD which has no zf.yaml, so role workers fall
    # back to <worktree>/<role>/project/.zf and fragment truth.
    if not (cfg.worktree / ".git").exists():
        subprocess.run(
            ["git", "init", "-q"],
            cwd=cfg.worktree, capture_output=True, text=True, timeout=30,
        )
    subprocess.run(
        ["git", "-c", "user.email=loop@zaofu", "-c", "user.name=loop",
         "add", "zf.yaml"],
        cwd=cfg.worktree, capture_output=True, text=True, timeout=30,
    )
    subprocess.run(
        ["git", "-c", "user.email=loop@zaofu", "-c", "user.name=loop",
         "commit", "-q", "--allow-empty", "-m", "loop iter init zf.yaml"],
        cwd=cfg.worktree, capture_output=True, text=True, timeout=30,
    )

    # 3. zf init --force inside worktree.
    init = subprocess.run(
        ["zf", "init", "--force"],
        cwd=cfg.worktree, capture_output=True, text=True, timeout=60,
    )
    if init.returncode != 0:
        return {
            "status": "failed",
            "tasks_done": 0,
            "expected_done": cfg.expected_done,
            "state_dir": str(state_dir),
            "worktree": str(cfg.worktree),
            "fatal_event": {
                "type": "init.failed",
                "reason": init.stderr.strip()[:500] or "zf init returned non-zero",
            },
            "report_path": "",
        }

    # 4. zf start — runs orchestrator main loop in-process (NOT a
    # detached daemon despite the name), so we must Popen it in the
    # background and wait for loop.started in events.jsonl as the
    # readiness signal, then proceed. Lifetime managed by `zf stop`
    # at step 7.
    state_dir.mkdir(parents=True, exist_ok=True)
    start_log = state_dir / "loop-zf-start.log"
    events_path = state_dir / "events.jsonl"
    start_log_fh = start_log.open("w")
    start_proc = subprocess.Popen(
        ["zf", "start"],
        cwd=cfg.worktree,
        stdout=start_log_fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait for loop.started to fire — readiness signal that orchestrator
    # is consuming the event stream and ready to wake on user.message.
    ready_deadline = _time.time() + 180
    is_ready = False
    while _time.time() < ready_deadline:
        if start_proc.poll() is not None:
            # zf start died unexpectedly.
            break
        try:
            with events_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(ev, dict) and ev.get("type") == "loop.started":
                        is_ready = True
                        break
                if is_ready:
                    break
        except FileNotFoundError:
            pass
        _time.sleep(2)

    if not is_ready:
        # Couldn't reach readiness. Tear down.
        subprocess.run(
            ["zf", "stop"], cwd=cfg.worktree,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=120,
        )
        start_proc.terminate()
        try:
            start_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            start_proc.kill()
        start_log_fh.close()
        subprocess.run(
            ["tmux", "kill-session", "-t", iter_session],
            capture_output=True, text=True, timeout=10,
        )
        return {
            "status": "failed",
            "tasks_done": 0,
            "expected_done": cfg.expected_done,
            "state_dir": str(state_dir),
            "worktree": str(cfg.worktree),
            "fatal_event": {
                "type": "start.failed",
                "reason": (start_log.read_text()[-500:] if start_log.exists()
                           else "loop.started never fired within 180s"),
            },
            "report_path": str(events_path),
        }

    # 5. emit user.message with the seed.
    payload = json.dumps({"text": cfg.seed_text}, ensure_ascii=False)
    subprocess.run(
        ["zf", "emit", "user.message", "--actor", "human", "--payload", payload],
        cwd=cfg.worktree, capture_output=True, text=True, timeout=30,
    )

    # 6. Poll terminal done count until expected or timeout.
    events_path = state_dir / "events.jsonl"
    deadline = _time.time() + cfg.inner_wait_timeout
    done_count = 0
    fatal: dict[str, Any] | None = None
    merged_workers: set[str] = set()
    scan = _scan_terminal_done_fatal_rework(events_path)
    while _time.time() < deadline:
        scan = _scan_terminal_done_fatal_rework(events_path)
        done_count = int(scan["done_count"])
        fatal = scan["fatal"]
        if fatal is not None:
            break
        if done_count >= cfg.expected_done:
            break
        # Best-effort: fast-forward merge any new worker/* branches so
        # downstream review/test workers in main worktree see dev's
        # commits.
        _try_ff_merge_worker_branches(cfg.worktree, merged_workers)
        _time.sleep(10)

    # 7. zf stop (graceful) + terminate the start_proc + defensive
    # tmux kill-session as belt-and-suspenders.
    subprocess.run(
        ["zf", "stop"], cwd=cfg.worktree,
        capture_output=True, text=True, timeout=120,
    )
    # zf stop signals the orchestrator main loop to exit; wait for the
    # start_proc to terminate so we don't leak background processes.
    try:
        start_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        start_proc.terminate()
        try:
            start_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            start_proc.kill()
    start_log_fh.close()
    subprocess.run(
        ["tmux", "kill-session", "-t", iter_session],
        capture_output=True, text=True, timeout=10,
    )

    if fatal is not None:
        status = "fatal"
    elif done_count >= cfg.expected_done:
        status = (
            "passed_after_rework"
            if int(scan.get("passed_after_rework", 0)) > 0
            else "passed"
        )
    elif int(scan.get("pending_rework_count", 0)) > 0:
        status = "failed_pending_rework"
    else:
        status = "failed"

    return {
        "status": status,
        "tasks_done": done_count,
        "expected_done": cfg.expected_done,
        "state_dir": str(state_dir),
        "worktree": str(cfg.worktree),
        "fatal_event": fatal,
        "rework_count": int(scan.get("rework_count", 0)),
        "passed_after_rework": int(scan.get("passed_after_rework", 0)),
        "pending_rework_count": int(scan.get("pending_rework_count", 0)),
        "rework_events": scan.get("rework_events", [])[-5:],
        "validation_kinds": scan.get("validation_kinds", []),
        "report_path": str(events_path),
    }

__all__ = ["bypass_inner_run"]
