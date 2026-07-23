"""Outer autoresearch supervisor.

This module runs outside the harness being evaluated. It prepares an isolated
worktree, starts the inner real-provider harness runner, watches deterministic
artifacts, writes a run report, and can upsert a repair backlog task on failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.autoresearch.review_gate import (
    normalize_review_gate_mode,
    prepare_review_gate_summary,
)
from zf.autoresearch.scenarios import AutoresearchScenario, resolve_scenario
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.package_source import installed_local_source_root
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


FATAL_EVENT_TYPES = frozenset({
    "orchestrator.dispatch_failed",
    "task.invalid_transition",
    "cost.budget.exceeded",
    "run.failed",
    "ship.failed",
    "task.orphaned",
    "worker.respawn.failed",
    "worker.recycle.failed",
    "worker.stuck.recovery_failed",
})


@dataclass(frozen=True)
class AutoresearchRunConfig:
    scenario_name: str = "self-eval-backlog"
    worktree: Path = Path("/tmp/zaofu-autoresearch")
    config_template: Path = Path("examples/tmp/dev-codex-backends.yaml")
    branch: str = ""
    seed_file: Path | None = None
    expected_done: int | None = None
    timeout_seconds: int | None = None
    budget_usd: float = 500.0
    confirm: bool = False
    reuse_worktree: bool = False
    keep_running: bool = False
    runner_module: str = "tests.e2e.run_mixed"
    run_id: str = ""
    output_dir: Path | None = None
    backlog_on_failure: bool = False
    backlog_state_dir: Path | None = None
    inject_worker_stuck: bool = False
    inject_worker_stuck_instance: str = "dev-1"
    inject_worker_stuck_timeout_seconds: int = 600
    # Overlay the developer's current uncommitted ACDMRT changes into the
    # autoresearch worktree. Disable for strict HEAD-only evaluation (CI).
    sync_dirty: bool = True
    review_gate: str = "off"


@dataclass(frozen=True)
class AutoresearchRunResult:
    status: str
    run_id: str
    worktree: Path
    run_dir: Path
    report_path: Path
    tasks_done: int
    expected_done: int
    returncode: int
    fatal_event: dict[str, Any] | None = None
    backlog_task_id: str = ""
    elapsed_seconds: float = 0.0
    review_gate: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def repo_root() -> Path:
    configured = str(os.environ.get("ZF_AUTORESEARCH_SOURCE_ROOT") or "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
        if _is_git_source_root(root):
            return root
        raise RuntimeError(
            "ZF_AUTORESEARCH_SOURCE_ROOT is not a ZaoFu Git checkout: "
            f"{root}"
        )

    source_root = Path(__file__).resolve().parents[3]
    if _is_git_source_root(source_root):
        return source_root

    installed_root = installed_local_source_root()
    if installed_root is not None and _is_git_source_root(installed_root):
        return installed_root
    raise RuntimeError(
        "Autoresearch requires a ZaoFu Git source checkout. Install ZaoFu "
        "from a local checkout or set ZF_AUTORESEARCH_SOURCE_ROOT."
    )

def _is_git_source_root(path: Path) -> bool:
    return (path / ".git").exists() and (path / "pyproject.toml").is_file()


def default_run_id(scenario_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in scenario_name)
    return f"{safe}-{utc_stamp()}"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root() / "src")
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if log_path is None:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            capture_output=True,
            check=False,
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + shlex.join(cmd) + "\n")
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"\n[exit {proc.returncode}]\n")
    return proc


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"yaml root must be a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def ensure_web_dependencies(worktree: Path, *, log_path: Path) -> str:
    """Ensure a freshly-created worktree can run Web quality gates.

    Git worktrees do not include ignored ``web/node_modules``. The harness
    presets run ``npm --prefix web ...`` checks during judge, so autoresearch
    must make those dependencies available before starting the inner run.
    Prefer linking the already-prepared repo dependencies; fall back to
    ``npm ci`` when the source checkout has not installed them.
    """
    web_dir = worktree / "web"
    package_json = web_dir / "package.json"
    package_lock = web_dir / "package-lock.json"
    if not package_json.exists() or not package_lock.exists():
        return "skipped:no-web-package"

    target_node_modules = web_dir / "node_modules"
    target_tsc = target_node_modules / ".bin" / "tsc"
    if target_tsc.exists():
        return "present"

    source_node_modules = repo_root() / "web" / "node_modules"
    source_tsc = source_node_modules / ".bin" / "tsc"
    if source_tsc.exists():
        if target_node_modules.is_symlink():
            target_node_modules.unlink()
        if not target_node_modules.exists():
            target_node_modules.symlink_to(source_node_modules, target_is_directory=True)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"linked {target_node_modules} -> {source_node_modules}\n",
                encoding="utf-8",
            )
            return "linked"

    result = _run(
        ["npm", "--prefix", str(web_dir), "ci", "--include=dev"],
        cwd=worktree,
        log_path=log_path,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "npm ci failed while preparing autoresearch worktree; "
            f"see {log_path}"
        )
    return "installed"


def sync_tracked_checkout_changes(
    worktree: Path, *, log_path: Path, enabled: bool = True,
) -> dict[str, Any]:
    """Overlay tracked dirty files (incl. deletes/renames) into worktree.

    Autoresearch evaluates the developer's current candidate, not just the last
    commit. A plain ``git worktree add ... HEAD`` misses uncommitted fixes, so
    targeted validation can keep failing on stale tests/source. Keep this to
    tracked files and never copy ``zf.yaml`` because autoresearch materializes
    its own experiment config there.

    ``enabled=False`` skips the overlay (strict HEAD evaluation) and returns
    an empty summary so the run record still captures the mode choice.

    Returns a summary dict ``{added: [...], modified: [...], deleted: [...],
    renamed: [(old, new), ...], skipped: bool}`` so callers can emit a
    ``autoresearch.candidate.synced`` event and replay the same selection.
    """
    summary: dict[str, Any] = {
        "added": [],
        "modified": [],
        "deleted": [],
        "renamed": [],
        "skipped": not enabled,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not enabled:
        log_path.write_text("skipped: strict HEAD evaluation\n", encoding="utf-8")
        return summary

    result = _run(
        # ACDMRT: Added / Copied / Deleted / Modified / Renamed / Typechanged.
        # -z keeps NUL-delimited records so rename's "old\0new" pair survives
        # paths with spaces or newlines.
        ["git", "diff", "--name-status", "-z", "--diff-filter=ACDMRT", "HEAD"],
        cwd=repo_root(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git diff failed while syncing worktree: {detail}")

    log_entries: list[str] = []
    tokens = [t for t in result.stdout.split("\0") if t]
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        # R<score> / C<score> have two path operands; A/D/M/T have one.
        if status.startswith("R") or status.startswith("C"):
            if i + 1 >= len(tokens):
                break
            old, new = tokens[i], tokens[i + 1]
            i += 2
            if new == "zf.yaml" or old == "zf.yaml":
                continue
            old_dst = worktree / old
            new_src = repo_root() / new
            new_dst = worktree / new
            if old_dst.exists():
                try:
                    old_dst.unlink()
                except OSError:
                    pass
            if new_src.is_file():
                new_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(new_src, new_dst)
            summary["renamed"].append([old, new])
            log_entries.append(f"R\t{old}\t{new}")
            continue
        code = status[:1]
        if i >= len(tokens):
            break
        rel = tokens[i]
        i += 1
        if not rel or rel == "zf.yaml":
            continue
        dst = worktree / rel
        if code == "D":
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            summary["deleted"].append(rel)
            log_entries.append(f"D\t{rel}")
            continue
        src = repo_root() / rel
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if code == "A":
            summary["added"].append(rel)
            log_entries.append(f"A\t{rel}")
        else:
            summary["modified"].append(rel)
            log_entries.append(f"{code}\t{rel}")

    log_path.write_text(
        "\n".join(log_entries) + ("\n" if log_entries else ""),
        encoding="utf-8",
    )
    return summary


def prepare_worktree(
    cfg: AutoresearchRunConfig,
    *,
    scenario: AutoresearchScenario,
    run_id: str,
    run_dir: Path,
) -> Path:
    root = repo_root()
    worktree = cfg.worktree.resolve()
    branch = cfg.branch or f"experiment/autoresearch-{run_id}"

    if worktree.exists():
        if not cfg.reuse_worktree:
            raise FileExistsError(
                f"worktree already exists: {worktree} "
                "(use --reuse-worktree to keep it)"
            )
    else:
        created = _run(
            ["git", "worktree", "add", "-b", branch, str(worktree), "HEAD"],
            cwd=root,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()
            raise RuntimeError(f"git worktree add failed: {detail}")

    synced_summary = sync_tracked_checkout_changes(
        worktree,
        log_path=run_dir / "synced-current-checkout.log",
        enabled=cfg.sync_dirty,
    )

    config_src = cfg.config_template
    if not config_src.is_absolute():
        config_src = root / config_src
    if not config_src.exists():
        raise FileNotFoundError(f"config template not found: {config_src}")

    zf_yaml = worktree / "zf.yaml"
    # r-next backlog B-5: autoresearch overwrote the user's zf.yaml in
    # --reuse-worktree mode and left the long zf-autoresearch-<run-id>
    # session name behind, breaking the next plain `zf start`. Back up
    # the original so operators have an explicit restore path. (Full
    # automatic restore needs an exit hook around the supervisor; the
    # backup is a low-risk first step.)
    if cfg.reuse_worktree and zf_yaml.exists():
        backup_path = worktree / "zf.yaml.pre-autoresearch"
        try:
            backup_path.write_text(
                zf_yaml.read_text(encoding="utf-8"), encoding="utf-8",
            )
        except OSError:
            pass
    data = _load_yaml(config_src)
    data.setdefault("project", {})["name"] = "zaofu-autoresearch"
    data["project"]["state_dir"] = ".zf"
    data.setdefault("session", {})["tmux_session"] = f"zf-autoresearch-{run_id}"
    data["global_budget_usd"] = cfg.budget_usd
    _write_yaml(zf_yaml, data)
    web_dependency_mode = ensure_web_dependencies(
        worktree,
        log_path=run_dir / "prepare-web-deps.log",
    )

    seed_path = worktree / "autoresearch-seed.txt"
    seed_path.write_text(scenario.seed_text.strip() + "\n", encoding="utf-8")

    manifest = {
        "run_id": run_id,
        "scenario": scenario.name,
        "description": scenario.description,
        "worktree": str(worktree),
        "branch": branch,
        "config_template": str(config_src),
        "zf_yaml": str(zf_yaml),
        "seed_file": str(seed_path),
        "expected_done": scenario.expected_done,
        "timeout_seconds": scenario.timeout_seconds,
        "budget_usd": cfg.budget_usd,
        "inject_worker_stuck": cfg.inject_worker_stuck,
        "inject_worker_stuck_instance": cfg.inject_worker_stuck_instance,
        "inject_worker_stuck_timeout_seconds": (
            cfg.inject_worker_stuck_timeout_seconds
        ),
        "web_dependency_mode": web_dependency_mode,
        "synced_current_checkout": synced_summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return seed_path


def build_inner_runner_command(
    cfg: AutoresearchRunConfig,
    *,
    scenario: AutoresearchScenario,
    seed_path: Path,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        cfg.runner_module,
        "--worktree",
        str(cfg.worktree.resolve()),
        "--seed-file",
        str(seed_path),
        "--expected-done",
        str(scenario.expected_done),
        "--timeout",
        str(scenario.timeout_seconds),
        "--confirm",
    ]
    if cfg.keep_running:
        cmd.append("--no-stop")
    return cmd


def read_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _dispatch_for_stuck_injection(
    events: list[dict[str, Any]],
    *,
    target_instance: str,
) -> dict[str, Any] | None:
    """Return the first task.dispatched event matching the injection target."""
    for event in events:
        if event.get("type") != "task.dispatched":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        assignee = str(payload.get("assignee") or "")
        role = str(payload.get("role") or "")
        if target_instance and target_instance not in {assignee, role}:
            continue
        if not str(event.get("task_id") or ""):
            continue
        return event
    return None


def _emit_stuck_injection(
    *,
    worktree: Path,
    run_dir: Path,
    dispatch_event: dict[str, Any],
    target_instance: str,
) -> bool:
    payload = dispatch_event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    task_id = str(dispatch_event.get("task_id") or "")
    assignee = str(payload.get("assignee") or target_instance)
    injection_payload = {
        "source": "autoresearch",
        "mode": "deterministic-worker-stuck",
        "instance_id": assignee,
        "target_instance": target_instance,
        "role": str(payload.get("role") or ""),
        "dispatch_id": str(payload.get("dispatch_id") or ""),
        "briefing": str(payload.get("briefing") or ""),
        "trigger_event_id": str(dispatch_event.get("id") or ""),
        "reason": "controlled autoresearch stuck injection",
    }
    payload_path = run_dir / "worker-stuck-injection.json"
    payload_path.write_text(
        json.dumps(injection_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    result = _run(
        [
            "zf",
            "emit",
            "autoresearch.inject.worker_stuck",
            "--actor",
            "zf-autoresearch",
            "--task",
            task_id,
            "--payload-file",
            str(payload_path),
        ],
        cwd=worktree,
        log_path=run_dir / "worker-stuck-injection.log",
    )
    return result.returncode == 0


def _run_inner_runner(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    cfg: AutoresearchRunConfig,
    run_dir: Path,
) -> subprocess.CompletedProcess[str]:
    if not cfg.inject_worker_stuck:
        return _run(cmd, cwd=cwd, log_path=log_path)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    injected = False
    injection_warning_logged = False
    last_dispatch_count = 0
    last_dispatch_at: float | None = None
    target_wait_seconds = max(1, cfg.inject_worker_stuck_timeout_seconds)
    events_path = cfg.worktree.resolve() / ".zf" / "events.jsonl"
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + shlex.join(cmd) + "\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        while proc.poll() is None:
            if not injected:
                events = read_events(events_path)
                dispatch_count = sum(
                    1 for event in events if event.get("type") == "task.dispatched"
                )
                if dispatch_count > last_dispatch_count:
                    last_dispatch_count = dispatch_count
                    last_dispatch_at = time.time()
                dispatch = _dispatch_for_stuck_injection(
                    events,
                    target_instance=cfg.inject_worker_stuck_instance,
                )
                if dispatch is not None:
                    injected = _emit_stuck_injection(
                        worktree=cfg.worktree.resolve(),
                        run_dir=run_dir,
                        dispatch_event=dispatch,
                        target_instance=cfg.inject_worker_stuck_instance,
                    )
                    log.write(
                        "\n[autoresearch] worker_stuck_injection="
                        f"{'emitted' if injected else 'emit_failed'} "
                        f"target={cfg.inject_worker_stuck_instance}\n"
                    )
                    log.flush()
                elif (
                    last_dispatch_at is not None
                    and not injection_warning_logged
                    and time.time() - last_dispatch_at >= target_wait_seconds
                ):
                    injection_warning_logged = True
                    log.write(
                        "\n[autoresearch] worker_stuck_injection=waiting "
                        f"target={cfg.inject_worker_stuck_instance} "
                        "reason=target_dispatch_not_seen_after_timeout\n"
                    )
                    log.flush()
            time.sleep(1.0)
        rc = proc.wait()
        if not injected:
            log.write(
                "\n[autoresearch] worker_stuck_injection=not_emitted "
                f"target={cfg.inject_worker_stuck_instance} "
                "reason=inner_runner_exited_before_target_dispatch\n"
            )
        log.write(f"\n[exit {rc}]\n")
    return subprocess.CompletedProcess(cmd, rc)


def summarize_events(worktree: Path, expected_done: int) -> dict[str, Any]:
    events = read_events(worktree / ".zf" / "events.jsonl")
    done = 0
    fatal = None
    fatal_count = 0
    dispatch_by_instance: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    success_seen: dict[tuple[str, str, str], int] = {}
    success_event_types = {
        "dev.build.done",
        "review.approved",
        "test.passed",
        "judge.passed",
    }
    for event in events:
        event_type = str(event.get("type") or "")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "task.status_changed":
            payload = event.get("payload") or {}
            if isinstance(payload, dict) and payload.get("to") == "done":
                done += 1
        if event_type in FATAL_EVENT_TYPES:
            fatal_count += 1
            if fatal is None:
                fatal = event
        if event_type == "task.dispatched":
            payload = event.get("payload") or {}
            if isinstance(payload, dict):
                assignee = str(payload.get("assignee") or "?")
                dispatch_by_instance[assignee] = (
                    dispatch_by_instance.get(assignee, 0) + 1
                )
        if event_type in success_event_types:
            payload = event.get("payload") or {}
            dispatch_id = ""
            if isinstance(payload, dict):
                dispatch_id = str(payload.get("dispatch_id") or "")
            if dispatch_id:
                key = (
                    str(event.get("task_id") or ""),
                    event_type,
                    dispatch_id,
                )
                success_seen[key] = success_seen.get(key, 0) + 1

    duplicate_success_event_count = sum(
        count - 1 for count in success_seen.values() if count > 1
    )
    done_evidence = event_counts.get("task.done.evidence", 0)
    terminal_evidence_coverage = 0.0
    if done:
        terminal_evidence_coverage = min(1.0, done_evidence / done)
    dev_replicas = sorted(
        assignee for assignee in dispatch_by_instance if assignee.startswith("dev-")
    )
    test_replicas = sorted(
        assignee for assignee in dispatch_by_instance if assignee.startswith("test-")
    )
    rework_signal_count = sum(
        event_counts.get(name, 0)
        for name in (
            "review.rejected",
            "test.failed",
            "judge.failed",
            "gate.failed",
            "discriminator.failed",
            "task.rework.requested",
            "task.done.blocked",
        )
    )
    derived_metrics = {
        "fatal_count": fatal_count,
        "stuck_injection_requested_count": event_counts.get(
            "autoresearch.inject.worker_stuck", 0,
        ),
        "worker_stuck_count": event_counts.get("worker.stuck", 0),
        "worker_stuck_recovered_count": event_counts.get(
            "worker.stuck.recovered", 0,
        ),
        "worker_stuck_recovery_failed_count": event_counts.get(
            "worker.stuck.recovery_failed", 0,
        ),
        "task_done_blocked_count": event_counts.get("task.done.blocked", 0),
        "done_evidence_count": done_evidence,
        "terminal_evidence_coverage": terminal_evidence_coverage,
        "discriminator_passed_count": event_counts.get("discriminator.passed", 0),
        "discriminator_failed_count": event_counts.get("discriminator.failed", 0),
        "invalid_transition_count": event_counts.get("task.invalid_transition", 0),
        "duplicate_success_event_count": duplicate_success_event_count,
        "rework_signal_count": rework_signal_count,
        "dev_replicas_used": dev_replicas,
        "test_replicas_used": test_replicas,
    }
    derived_metrics["stuck_injection_satisfied"] = bool(
        derived_metrics["stuck_injection_requested_count"] >= 1
        and derived_metrics["worker_stuck_count"] >= 1
        and derived_metrics["worker_stuck_recovered_count"] >= 1
        and derived_metrics["worker_stuck_recovery_failed_count"] == 0
    )
    return {
        "tasks_done": done,
        "expected_done": expected_done,
        "fatal_event": fatal,
        "fatal_count": fatal_count,
        "dispatch_by_instance": dict(sorted(dispatch_by_instance.items())),
        "event_counts": dict(sorted(event_counts.items())),
        "derived_metrics": derived_metrics,
    }


def write_iterations(run_dir: Path, row: dict[str, Any]) -> Path:
    path = run_dir / "iterations.tsv"
    header = [
        "run_id",
        "scenario",
        "status",
        "tasks_done",
        "expected_done",
        "returncode",
        "elapsed_seconds",
        "fatal_type",
    ]
    fatal = row.get("fatal_event") or {}
    values = [
        str(row.get("run_id", "")),
        str(row.get("scenario", "")),
        str(row.get("status", "")),
        str(row.get("tasks_done", "")),
        str(row.get("expected_done", "")),
        str(row.get("returncode", "")),
        f"{float(row.get('elapsed_seconds', 0.0)):.1f}",
        str(fatal.get("type") or ""),
    ]
    path.write_text(
        "\t".join(header) + "\n" + "\t".join(values) + "\n",
        encoding="utf-8",
    )
    return path


def write_report(run_dir: Path, row: dict[str, Any]) -> Path:
    fatal = row.get("fatal_event") or {}
    summary = row.get("summary") or {}
    review_gate = row.get("review_gate") or {}
    lines = [
        f"# Autoresearch Run {row.get('run_id', '')}",
        "",
        f"- scenario: `{row.get('scenario', '')}`",
        f"- status: `{row.get('status', '')}`",
        f"- worktree: `{row.get('worktree', '')}`",
        f"- tasks_done: {row.get('tasks_done', 0)} / {row.get('expected_done', 0)}",
        f"- returncode: {row.get('returncode', 0)}",
        f"- elapsed_seconds: {float(row.get('elapsed_seconds', 0.0)):.1f}",
        f"- log: `{row.get('log_path', '')}`",
        "",
        "## Fatal Event",
        "",
    ]
    if fatal:
        lines.extend([
            f"- type: `{fatal.get('type', '')}`",
            f"- task_id: `{fatal.get('task_id') or ''}`",
            f"- actor: `{fatal.get('actor') or ''}`",
            "",
            "```json",
            json.dumps(
                fatal.get("payload") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ])
    else:
        lines.extend(["none", ""])
    if review_gate:
        lines.extend([
            "## Review Gate",
            "",
            f"- mode: `{review_gate.get('mode', '')}`",
            f"- status: `{review_gate.get('status', '')}`",
            f"- triggered: `{review_gate.get('triggered', False)}`",
            f"- route: `{review_gate.get('route', '')}`",
            f"- severity: `{review_gate.get('severity', '')}`",
            f"- reason: {review_gate.get('reason', '')}",
            "",
            "```json",
            json.dumps(
                review_gate.get("artifact_refs") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ])
    lines.extend([
        "## Dispatch",
        "",
        "```json",
        json.dumps(
            summary.get("dispatch_by_instance") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Derived Metrics",
        "",
        "```json",
        json.dumps(
            summary.get("derived_metrics") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Event Counts",
        "",
        "```json",
        json.dumps(
            summary.get("event_counts") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
    ])
    path = run_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_review_gate_error_summary(
    *,
    mode: str,
    run_status: str,
    run_dir: Path,
    error: BaseException,
) -> dict[str, Any]:
    out = run_dir / "review-gate" / "summary.json"
    payload = {
        "schema_version": "autoresearch.review_gate.summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": "error",
        "triggered": False,
        "route": "",
        "reason": str(error),
        "run_status": run_status,
        "artifact_refs": {},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = dict(payload)
    summary["artifact_refs"] = {"summary": str(out)}
    return summary


def _maybe_prepare_review_gate(
    *,
    mode: str,
    run_status: str,
    run_dir: Path,
    worktree: Path,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    try:
        return prepare_review_gate_summary(
            mode=mode,
            run_status=run_status,
            run_dir=run_dir,
            state_dir=worktree / ".zf",
            source_root=worktree,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _write_review_gate_error_summary(
            mode=mode,
            run_status=run_status,
            run_dir=run_dir,
            error=exc,
        )


def upsert_failure_backlog(
    *,
    cfg: AutoresearchRunConfig,
    scenario: AutoresearchScenario,
    row: dict[str, Any],
) -> str:
    state_dir = cfg.backlog_state_dir or (repo_root() / ".zf")
    state_dir.mkdir(parents=True, exist_ok=True)
    store = TaskStore(state_dir / "kanban.json")
    token = hashlib.sha256(
        f"{scenario.name}:{cfg.config_template}:{cfg.worktree}".encode("utf-8")
    ).hexdigest()[:12]
    key = f"autoresearch:{token}"
    title = f"Fix autoresearch failure: {scenario.name}"
    reason = ""
    fatal = row.get("fatal_event") or {}
    if fatal:
        reason = f"fatal={fatal.get('type', '')}, task={fatal.get('task_id') or ''}"
    else:
        reason = f"status={row.get('status', '')}, returncode={row.get('returncode', 0)}"

    existing = next(
        (
            task for task in store.list_all()
            if task.key == key and task.status not in {"done", "cancelled"}
        ),
        None,
    )
    verification = (
        "Run the autoresearch supervisor again and require "
        f"tasks_done >= {scenario.expected_done}, no fatal events, and report.md present."
    )
    if existing is None:
        task = Task(
            key=key,
            title=title,
            status="backlog",
            priority=1,
        )
        task.contract.behavior = (
            "Repair the harness/runtime issue exposed by the autoresearch run. "
            f"Failure reason: {reason}. Report: {row.get('report_path', '')}"
        )
        task.contract.verification = verification
        store.add(task)
        task_id = task.id
        event_type = "task.created"
    else:
        task_id = existing.id
        store.update(
            task_id,
            title=title,
            status="backlog",
            priority=1,
        )
        event_type = "task.updated"

    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type=event_type,
        actor="zf-autoresearch",
        task_id=task_id,
        payload={
            "source": "autoresearch",
            "key": key,
            "scenario": scenario.name,
            "reason": reason,
            "report": str(row.get("report_path", "")),
        },
    ))
    return task_id


def run_autoresearch(cfg: AutoresearchRunConfig) -> AutoresearchRunResult:
    review_gate_mode = normalize_review_gate_mode(cfg.review_gate)
    run_id = cfg.run_id or default_run_id(cfg.scenario_name)
    scenario = resolve_scenario(
        cfg.scenario_name,
        seed_file=cfg.seed_file,
        expected_done=cfg.expected_done,
        timeout_seconds=cfg.timeout_seconds,
    )
    run_dir = (
        cfg.output_dir
        if cfg.output_dir is not None
        else cfg.worktree.resolve() / ".zf" / "autoresearch" / "runs" / run_id
    )
    if not cfg.confirm:
        run_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "dry-run",
            "worktree": str(cfg.worktree.resolve()),
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "summary": {},
            "log_path": "",
        }
        if review_gate_mode != "off":
            row["review_gate"] = {
                "mode": review_gate_mode,
                "status": "skipped",
                "triggered": False,
                "route": "",
                "reason": "dry-run does not inspect runtime failure evidence",
                "artifact_refs": {},
            }
        report_path = write_report(run_dir, row)
        write_iterations(run_dir, {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "dry-run",
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "fatal_event": None,
        })
        return AutoresearchRunResult(
            status="dry-run",
            run_id=run_id,
            worktree=cfg.worktree.resolve(),
            run_dir=run_dir,
            report_path=report_path,
            tasks_done=0,
            expected_done=scenario.expected_done,
            returncode=0,
            review_gate=row.get("review_gate"),
        )

    start = time.time()
    seed_path = prepare_worktree(cfg, scenario=scenario, run_id=run_id, run_dir=run_dir)
    validate_log = run_dir / "validate.log"
    validate = _run(
        ["zf", "validate", "--path", str(cfg.worktree.resolve() / "zf.yaml")],
        cwd=repo_root(),
        log_path=validate_log,
    )
    if validate.returncode != 0:
        elapsed = time.time() - start
        row = {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "validate_failed",
            "worktree": str(cfg.worktree.resolve()),
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": validate.returncode,
            "elapsed_seconds": elapsed,
            "summary": {},
            "log_path": str(validate_log),
            "fatal_event": None,
        }
        review_gate = _maybe_prepare_review_gate(
            mode=review_gate_mode,
            run_status="validate_failed",
            run_dir=run_dir,
            worktree=cfg.worktree.resolve(),
        )
        if review_gate is not None:
            row["review_gate"] = review_gate
        report_path = write_report(run_dir, row)
        row["report_path"] = str(report_path)
        write_iterations(run_dir, row)
        task_id = (
            upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)
            if cfg.backlog_on_failure
            else ""
        )
        return AutoresearchRunResult(
            status="validate_failed",
            run_id=run_id,
            worktree=cfg.worktree.resolve(),
            run_dir=run_dir,
            report_path=report_path,
            tasks_done=0,
            expected_done=scenario.expected_done,
            returncode=validate.returncode,
            backlog_task_id=task_id,
            elapsed_seconds=elapsed,
            review_gate=review_gate,
        )

    log_path = run_dir / "inner-runner.log"
    cmd = build_inner_runner_command(cfg, scenario=scenario, seed_path=seed_path)
    proc = _run_inner_runner(
        cmd,
        cwd=repo_root(),
        log_path=log_path,
        cfg=cfg,
        run_dir=run_dir,
    )
    elapsed = time.time() - start
    summary = summarize_events(cfg.worktree.resolve(), scenario.expected_done)
    tasks_done = int(summary["tasks_done"])
    fatal = summary.get("fatal_event")
    metrics = summary.get("derived_metrics") or {}
    stuck_injection_failed = bool(
        cfg.inject_worker_stuck
        and not metrics.get("stuck_injection_satisfied")
    )
    if (
        proc.returncode == 0
        and tasks_done >= scenario.expected_done
        and fatal is None
        and not stuck_injection_failed
    ):
        status = "passed"
    elif fatal is not None:
        status = "fatal"
    elif stuck_injection_failed:
        status = "failed"
    elif tasks_done < scenario.expected_done:
        status = "incomplete"
    else:
        status = "failed"

    row = {
        "run_id": run_id,
        "scenario": scenario.name,
        "status": status,
        "worktree": str(cfg.worktree.resolve()),
        "tasks_done": tasks_done,
        "expected_done": scenario.expected_done,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "summary": summary,
        "fatal_event": fatal,
        "log_path": str(log_path),
    }
    review_gate = _maybe_prepare_review_gate(
        mode=review_gate_mode,
        run_status=status,
        run_dir=run_dir,
        worktree=cfg.worktree.resolve(),
    )
    if review_gate is not None:
        row["review_gate"] = review_gate
        summary["review_gate"] = review_gate
    events_summary = run_dir / "events-summary.json"
    events_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_path = write_report(run_dir, row)
    row["report_path"] = str(report_path)
    write_iterations(run_dir, row)
    task_id = ""
    if cfg.backlog_on_failure and status != "passed":
        task_id = upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)
    return AutoresearchRunResult(
        status=status,
        run_id=run_id,
        worktree=cfg.worktree.resolve(),
        run_dir=run_dir,
        report_path=report_path,
        tasks_done=tasks_done,
        expected_done=scenario.expected_done,
        returncode=proc.returncode,
        fatal_event=fatal if isinstance(fatal, dict) else None,
        backlog_task_id=task_id,
        elapsed_seconds=elapsed,
        review_gate=review_gate,
    )


def tmux_supervisor_command(
    argv: list[str],
    *,
    worktree: Path,
    session: str,
) -> list[list[str]]:
    marker_env = "ZF_AUTORESEARCH_IN_TMUX=1"
    pythonpath_env = f"PYTHONPATH={shlex.quote(str(repo_root() / 'src'))}"
    quoted = shlex.join(argv + ["--no-tmux"])
    supervisor = (
        f"cd {shlex.quote(str(repo_root()))} && "
        f"{marker_env} {pythonpath_env} {quoted}"
    )
    events = (
        f"while [ ! -f {shlex.quote(str(worktree / '.zf' / 'events.jsonl'))} ]; "
        "do sleep 2; done; "
        f"tail -f {shlex.quote(str(worktree / '.zf' / 'events.jsonl'))}"
    )
    status = (
        f"while [ ! -d {shlex.quote(str(worktree))} ]; do sleep 2; done; "
        f"cd {shlex.quote(str(worktree))}; "
        "while true; do clear; "
        "PYTHONPATH="
        f"{shlex.quote(str(repo_root() / 'src'))} "
        "python3 -m zf.cli.main status --workers 2>/dev/null || true; "
        "sleep 10; done"
    )
    return [
        [
            "tmux", "new-session", "-d", "-s", session,
            "-n", "supervisor", "bash", "-lc", supervisor,
        ],
        [
            "tmux", "new-window", "-t", session,
            "-n", "events", "bash", "-lc", events,
        ],
        [
            "tmux", "new-window", "-t", session,
            "-n", "status", "bash", "-lc", status,
        ],
    ]


def start_tmux_supervisor(
    argv: list[str],
    *,
    worktree: Path,
    session: str,
) -> None:
    for cmd in tmux_supervisor_command(argv, worktree=worktree, session=session):
        subprocess.run(cmd, check=True)
