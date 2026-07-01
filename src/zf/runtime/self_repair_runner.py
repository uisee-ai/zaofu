"""Authorized self-repair dispatch consumer — runnable from the CLI *and* the
watcher tick (doc 80 P3 activation / R20-B2).

R20 reproduced the dead-end live: autoresearch emitted
``autoresearch.repair.dispatch_requested`` but nothing ran the consumer
(operator-manual ``zf self-repair run`` only), so the stall never self-healed —
"detect→backlog→fix" stopped at the fix step. This module is the shared consumer
(CLI + tick): for each pending dispatch_requested (no matching dispatched),
prepare an isolated ZAOFU worktree + a ``zf-self-repair`` briefing, emit
``autoresearch.repair.dispatched``, and — with ``spawn`` — launch a headless
agent to run the tracked playbook (backlog → fix → verify → done; it never
auto-merges, leaves red work blocked + escalated).

Gated upstream by ``ZF_AUTORESEARCH_AUTO_REPAIR=authorized`` + a per-fingerprint
cap (``repair_authorization``). Idempotent + events-derived: a dispatched already
present → not pending → no re-dispatch (doc 80 invariant 4).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.repair_dispatch import (
    DISPATCHED,
    build_repair_briefing,
    dispatched_event_payload,
    pending_repair_dispatches,
    repair_branch_name,
)

CLOSEOUT_REQUIRED = "autoresearch.repair.closeout.required"
DISPATCH_BLOCKED = "autoresearch.repair.dispatch_blocked"


def _spawn_command(backend: str, *, worktree: Path, briefing: str) -> list[str]:
    """Command line for the supported unattended self-repair backends."""
    if backend == "codex":
        return [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(worktree),
            briefing,
        ]
    if backend in {"claude-code", "claude"}:
        return ["claude", "--dangerously-skip-permissions", "-p", briefing]
    return [backend, "--dangerously-skip-permissions", "-p", briefing]


def harness_root(arg: str | None = None) -> Path:
    """The ZaoFu repo root (where the self-repair worktree is branched). The
    repair targets the harness's own code (src/zf), so it runs in zaofu — not the
    project the orchestrator is driving."""
    if arg:
        return Path(arg).resolve()
    import zf

    # zf package lives at <root>/src/zf
    return Path(zf.__file__).resolve().parents[2]


def dispatch_pending_self_repairs(
    events,
    writer,
    *,
    root: str | None = None,
    spawn: bool = False,
    backend: str = "",
    tmp_root: str | None = None,
    request_types: tuple[str, ...] = ("autoresearch.repair.dispatch_requested",),
    dispatch_actor: str = "zf-self-repair",
) -> int:
    """Consume every pending self-repair dispatch → worktree + dispatched (+ spawn).

    Returns the number of dispatches prepared. Best-effort per request: a failed
    ``git worktree add`` skips that one (the rest still dispatch) — never raises
    into the tick loop.
    """
    pending = pending_repair_dispatches(events, request_types=request_types)
    if not pending:
        return 0
    hroot = harness_root(root)
    base = Path(tmp_root) if tmp_root else Path(tempfile.gettempdir())
    prepared = 0
    for req in pending:
        branch = repair_branch_name(req)
        worktree = base / "zf-self-repair" / branch.replace("/", "_")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(hroot), "worktree", "add", "-B", branch, str(worktree), "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 and "already exists" not in (result.stderr or ""):
            writer.append(ZfEvent(
                type=DISPATCH_BLOCKED,
                actor=dispatch_actor,
                payload={
                    "fingerprint": req.fingerprint,
                    "attempt": req.attempt,
                    "candidate_id": req.candidate_id,
                    "reason": "worktree_add_failed",
                    "stderr": result.stderr or "",
                    "branch": branch,
                    "worktree": str(worktree),
                },
            ))
            continue
        briefing = build_repair_briefing(req)
        briefing_path = worktree / ".self-repair-briefing.md"
        try:
            briefing_path.write_text(briefing, encoding="utf-8")
        except OSError:
            pass
        writer.append(ZfEvent(
            type=DISPATCHED,
            actor=dispatch_actor,
            payload=dispatched_event_payload(
                req, branch=branch, worktree=str(worktree), briefing_path=str(briefing_path),
            ),
        ))
        if spawn:
            backend = str(backend or "").strip()
            if not backend:
                writer.append(ZfEvent(
                    type=DISPATCH_BLOCKED,
                    actor=dispatch_actor,
                    payload={
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "reason": "self_repair_backend_not_configured",
                        "branch": branch,
                        "worktree": str(worktree),
                    },
                ))
                prepared += 1
                continue
            try:
                subprocess.Popen(
                    _spawn_command(backend, worktree=worktree, briefing=briefing),
                    cwd=str(worktree),
                )
            except Exception as exc:
                # the worktree + briefing + dispatched event are ready regardless;
                # an operator can run the zf-self-repair skill in the worktree.
                writer.append(ZfEvent(
                    type=DISPATCH_BLOCKED,
                    actor=dispatch_actor,
                    payload={
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "reason": "spawn_failed",
                        "error": str(exc),
                        "backend": backend,
                        "branch": branch,
                        "worktree": str(worktree),
                    },
                ))
        prepared += 1
    return prepared


def _repair_event_key(payload: dict) -> tuple[str, int]:
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    return str(payload.get("fingerprint") or ""), attempt


def emit_self_repair_closeouts(
    events,
    writer,
    *,
    root: str | None = None,
) -> int:
    """Emit closeout-required events for isolated repair branches with commits.

    The watcher must not auto-merge self-repair branches into the running
    harness. This bridge closes the visibility gap: once a dispatched repair
    worktree has commits ahead of the harness HEAD, operators and supervisor
    projections get a deterministic "ready for merge/restart decision" event.
    """
    dispatched = []
    closed: set[tuple[str, int]] = set()
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        key = _repair_event_key(payload)
        if getattr(event, "type", "") == CLOSEOUT_REQUIRED:
            closed.add(key)
        elif getattr(event, "type", "") == DISPATCHED:
            dispatched.append(event)
    if not dispatched:
        return 0
    hroot = harness_root(root)
    root_head = _git_stdout(hroot, "rev-parse", "HEAD")
    if not root_head:
        return 0
    emitted = 0
    for event in dispatched:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        key = _repair_event_key(payload)
        if key in closed:
            continue
        worktree = Path(str(payload.get("worktree") or ""))
        if not worktree.exists():
            continue
        branch_head = _git_stdout(worktree, "rev-parse", "HEAD")
        if not branch_head or branch_head == root_head:
            continue
        ahead = _git_stdout(worktree, "rev-list", "--count", "HEAD", f"^{root_head}")
        try:
            ahead_count = int(ahead or "0")
        except ValueError:
            ahead_count = 0
        if ahead_count <= 0:
            continue
        title = _git_stdout(worktree, "log", "-1", "--format=%s", "HEAD")
        changed_files = _git_lines(worktree, "diff", "--name-only", f"{root_head}..HEAD")
        risk = _classify_closeout_risk(changed_files, commits_ahead=ahead_count)
        verification_plan = _closeout_verification_plan(risk)
        restart = _closeout_restart_policy(risk)
        causation_id = str(getattr(event, "id", "") or "")
        writer.append(ZfEvent(
            type=CLOSEOUT_REQUIRED,
            actor="zf-self-repair",
            payload={
                "fingerprint": key[0],
                "attempt": key[1],
                "candidate_id": str(payload.get("candidate_id") or ""),
                "branch": str(payload.get("branch") or ""),
                "worktree": str(worktree),
                "source_commit": branch_head,
                "source_title": title,
                "commits_ahead": ahead_count,
                "changed_files": changed_files[:80],
                "risk_classification": risk,
                "verification_plan": verification_plan,
                "restart_strategy": restart["restart_strategy"],
                "safe_boundary": restart["safe_boundary"],
                "state_snapshot_required": restart["state_snapshot_required"],
                "replay_required": restart["replay_required"],
                "continuation": {
                    "schema_version": "self-repair.closeout-continuation.v1",
                    "restart_required": restart["restart_required"],
                    "restart_strategy": restart["restart_strategy"],
                    "safe_boundary": restart["safe_boundary"],
                    "state_snapshot_required": restart["state_snapshot_required"],
                    "replay_required": restart["replay_required"],
                    "resume_original_workflow": True,
                    "resume_strategy": restart["resume_strategy"],
                    "blocked_until": "verification_passed_and_apply_decision_recorded",
                },
                "action": "operator_merge_or_cherry_pick_then_restart_decision",
                "restart_required": restart["restart_required"],
                "auto_merge": False,
                "dispatched_event_id": str(getattr(event, "id", "") or ""),
            },
            causation_id=causation_id or None,
        ))
        emitted += 1
    return emitted


def _closeout_restart_policy(risk: dict[str, Any]) -> dict[str, Any]:
    risk_level = str(risk.get("risk") or "unknown")
    if risk_level == "low":
        strategy = "next_run"
        safe_boundary = "terminal_or_next_run"
        snapshot_required = False
        replay_required = False
        resume_strategy = "apply_for_next_run_without_runtime_restart"
    else:
        strategy = "control_plane_restart_preserve_run_manager"
        safe_boundary = "terminal_or_operator_approved_checkpoint"
        snapshot_required = True
        replay_required = True
        resume_strategy = (
            "snapshot_replay_then_preserve_run_manager_control_plane_restart"
        )
    return {
        "schema_version": "self-repair.restart-policy.v1",
        "restart_required": False,
        "restart_strategy": strategy,
        "safe_boundary": safe_boundary,
        "state_snapshot_required": snapshot_required,
        "replay_required": replay_required,
        "resume_strategy": resume_strategy,
    }


def _classify_closeout_risk(
    changed_files: list[str],
    *,
    commits_ahead: int,
) -> dict[str, Any]:
    files = [str(path) for path in changed_files if str(path).strip()]
    categories: set[str] = set()
    for path in files:
        if path.startswith(("docs/", "reports/", "ideas/", "tasks/", "backlogs/")) or path.endswith(".md"):
            categories.add("docs")
        elif path.startswith("tests/"):
            categories.add("tests")
        elif path.startswith(("web/", "src/zf/web/")):
            categories.add("web")
        elif path.startswith("src/zf/core/config/") or path in {"zf.yaml", "pyproject.toml", "uv.lock"}:
            categories.add("config")
        elif path.startswith("src/zf/runtime/"):
            categories.add("runtime")
        elif path.startswith("src/"):
            categories.add("source")
        else:
            categories.add("other")
    if not files:
        risk = "unknown"
    elif categories <= {"docs"} and commits_ahead <= 2:
        risk = "low"
    elif categories <= {"tests"} and commits_ahead <= 2:
        risk = "low"
    elif "runtime" in categories or "config" in categories:
        risk = "medium"
    else:
        risk = "medium"
    if len(files) >= 30 or commits_ahead >= 5 or "other" in categories:
        risk = "high"
    return {
        "schema_version": "self-repair.closeout-risk.v1",
        "risk": risk,
        "categories": sorted(categories),
        "changed_file_count": len(files),
        "commits_ahead": commits_ahead,
        "controlled_apply_allowed": risk == "low",
        "human_approval_required": risk != "low",
    }


def _closeout_verification_plan(risk: dict[str, Any]) -> list[dict[str, str]]:
    categories = set(risk.get("categories") or [])
    plan = [{
        "kind": "diff_integrity",
        "command": "git diff --check",
        "required": "true",
    }]
    if categories.intersection({"runtime", "source", "config", "tests"}):
        plan.append({
            "kind": "focused_pytest",
            "command": "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_run_manager.py tests/test_self_repair_runner.py tests/test_tick_services.py -q",
            "required": "true",
        })
    if "config" in categories:
        plan.append({
            "kind": "config_pytest",
            "command": "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_config_loader.py -q",
            "required": "true",
        })
    if "web" in categories:
        plan.append({
            "kind": "web_build_or_test",
            "command": "npm --prefix web run build",
            "required": "true",
        })
    if categories <= {"docs"}:
        plan.append({
            "kind": "docs_review",
            "command": "review changed markdown for task status / index references",
            "required": "true",
        })
    return plan


def _git_stdout(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    stdout = result.stdout
    if not isinstance(stdout, str):
        return ""
    return stdout.strip()


def _git_lines(cwd: Path, *args: str) -> list[str]:
    text = _git_stdout(cwd, *args)
    return [line.strip() for line in text.splitlines() if line.strip()]
