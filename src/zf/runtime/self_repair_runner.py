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
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.repair_dispatch import (
    DISPATCHED,
    RepairContractError,
    assert_repair_contract_binding,
    build_repair_briefing,
    dispatched_event_payload,
    pending_repair_dispatches,
    repair_branch_name,
    write_repair_contract,
)
from zf.runtime.self_repair_lifecycle import (
    REPAIR_SPAWNED,
    REPAIR_TERMINATED,
    emit_repair_terminal,
    process_pid,
    read_repair_process_result,
    repair_operation_id,
)
from zf.runtime.self_repair_process import LAUNCH_SCHEMA
from zf.runtime.sidecar_refs import sidecar_path

CLOSEOUT_REQUIRED = "autoresearch.repair.closeout.required"
DISPATCH_BLOCKED = "autoresearch.repair.dispatch_blocked"


def _spawn_command(backend: str, *, worktree: Path, briefing: str,
                   model: str = "", effort: str = "") -> list[str]:
    """Repair providers retain their sandbox; host effects have separate gates."""
    if backend == "codex":
        command = ["codex", "exec", "--sandbox", "workspace-write",
                   "-c", 'approval_policy="never"', "-C", str(worktree)]
        if model:
            command += ["--model", model]
        if effort:
            command += ["-c", "model_reasoning_effort=" + json.dumps(effort)]
        return [*command, briefing]
    if backend in {"claude-code", "claude"}:
        command = ["claude", "--permission-mode", "default"]
        if model:
            command += ["--model", model]
        return [*command, "-p", briefing]
    raise ValueError("unsupported self-repair backend")


def _repair_provider_settings(state_dir: Path) -> tuple[str, str, str]:
    from zf.core.config.loader import ConfigError
    from zf.core.config.project_context import resolve_project_context
    try:
        context = resolve_project_context(explicit_state_dir=state_dir, load_config_with_explicit=True)
    except (ConfigError, OSError):
        return "", "", ""
    config = context.config.runtime.run_manager.source_repair
    return config.backend, config.model, config.model_reasoning_effort


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
    spawn_timeout_seconds: int = 1800,
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
    base_commit = _git_stdout(hroot, "rev-parse", "HEAD")
    state_dir = Path(writer.event_log.path).parent
    repair_backend, model, effort = _repair_provider_settings(state_dir) if spawn else ("", "", "")
    backend = repair_backend or backend
    base = Path(tmp_root) if tmp_root else Path(tempfile.gettempdir())
    prepared = 0
    for req in pending:
        branch = repair_branch_name(req, namespace=str(state_dir.resolve()))
        worktree = base / "zf-self-repair" / branch.replace("/", "_")
        try:
            repair_contract, repair_contract_ref = write_repair_contract(
                state_dir,
                req,
                base_commit=base_commit,
                created_by=dispatch_actor,
                source_root=hroot,
            )
        except Exception as exc:
            writer.append(ZfEvent(
                type=DISPATCH_BLOCKED,
                actor=dispatch_actor,
                payload={
                    "fingerprint": req.fingerprint,
                    "attempt": req.attempt,
                    "candidate_id": req.candidate_id,
                    "reason": "repair_contract_freeze_failed",
                    "error": str(exc),
                    "branch": branch,
                    "worktree": str(worktree),
                },
            ))
            continue
        worktree.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "git", "-C", str(hroot), "worktree", "add", "-b", branch,
                str(worktree), "HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
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
        briefing = build_repair_briefing(
            req,
            repair_contract=repair_contract,
            repair_contract_ref=repair_contract_ref,
            state_dir=state_dir,
        )
        from zf.runtime.sidecar_refs import write_sidecar_text
        briefing_ref = write_sidecar_text(state_dir,
            f"artifacts/self-repair/briefings/{repair_contract_ref['sha256']}.md", briefing,
            kind="self_repair_briefing", schema_version="self-repair-briefing.v1",
            created_by=dispatch_actor, required=True)
        briefing_path = sidecar_path(state_dir, briefing_ref["ref"])
        dispatched_event = writer.append(ZfEvent(
            type=DISPATCHED,
            actor=dispatch_actor,
            payload=dispatched_event_payload(
                req,
                branch=branch,
                worktree=str(worktree),
                briefing_path=str(briefing_path),
                base_commit=base_commit,
                repair_contract=repair_contract,
                repair_contract_ref=repair_contract_ref,
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
                writer.append(ZfEvent(
                    type=REPAIR_TERMINATED,
                    actor=dispatch_actor,
                    payload={
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "status": "blocked",
                        "reason": "self_repair_backend_not_configured",
                        "repair_contract_ref": repair_contract_ref,
                        "repair_contract_digest": repair_contract_ref["sha256"],
                    },
                    causation_id=dispatched_event.id,
                ))
                prepared += 1
                continue
            try:
                operation_id = repair_operation_id(
                    req.fingerprint,
                    req.attempt,
                    repair_contract_ref["sha256"],
                )
                result_ref = (
                    f"operator/self-repair/processes/{operation_id}.json"
                )
                launch_ref = write_immutable_json_sidecar(
                    state_dir,
                    {
                        "schema_version": LAUNCH_SCHEMA,
                        "operation_id": operation_id,
                        "backend": backend,
                        "argv": _spawn_command(
                            backend,
                            worktree=worktree,
                            briefing=briefing, model=model, effort=effort,
                        ),
                        "cwd": str(worktree),
                        "timeout_seconds": max(
                            1,
                            int(spawn_timeout_seconds or 1800),
                        ),
                        "repair_contract_digest": repair_contract_ref["sha256"],
                    },
                    root="self-repair/process-launches",
                    kind="self_repair_process_launch",
                    schema_version=LAUNCH_SCHEMA,
                    created_by=dispatch_actor,
                    source_event_id=dispatched_event.id,
                )
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "zf.runtime.self_repair_process",
                        "--launch",
                        str(sidecar_path(state_dir, launch_ref["ref"])),
                        "--result",
                        str(sidecar_path(state_dir, result_ref)),
                    ],
                    cwd=str(hroot),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                writer.append(ZfEvent(
                    type=REPAIR_SPAWNED,
                    actor=dispatch_actor,
                    payload={
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "backend": backend,
                        "operation_id": operation_id,
                        "provider_operation_id": operation_id,
                        "supervisor_pid": process_pid(process),
                        "timeout_seconds": max(
                            1,
                            int(spawn_timeout_seconds or 1800),
                        ),
                        "launch_ref": launch_ref,
                        "result_ref": result_ref,
                        "repair_contract_ref": repair_contract_ref,
                        "repair_contract_digest": repair_contract_ref["sha256"],
                        "worktree": str(worktree),
                    },
                    causation_id=dispatched_event.id,
                ))
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
                writer.append(ZfEvent(
                    type=REPAIR_TERMINATED,
                    actor=dispatch_actor,
                    payload={
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "status": "failed",
                        "reason": "spawn_failed",
                        "error": str(exc),
                        "backend": backend,
                        "repair_contract_ref": repair_contract_ref,
                        "repair_contract_digest": repair_contract_ref["sha256"],
                    },
                    causation_id=dispatched_event.id,
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
    spawned: dict[tuple[str, int], Any] = {}
    terminals: dict[tuple[str, int], Any] = {}
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        key = _repair_event_key(payload)
        event_type = getattr(event, "type", "")
        if event_type == CLOSEOUT_REQUIRED:
            closed.add(key)
        elif event_type == REPAIR_TERMINATED:
            terminals[key] = event
            if str(payload.get("status") or "") in {
                "blocked",
                "failed",
                "no_commit",
                "timeout",
            }:
                closed.add(key)
        elif event_type == REPAIR_SPAWNED:
            spawned[key] = event
        elif event_type == DISPATCHED:
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
        try:
            repair_contract = assert_repair_contract_binding(
                Path(writer.event_log.path).parent,
                payload,
            )
        except RepairContractError as exc:
            writer.append(ZfEvent(
                type=REPAIR_TERMINATED,
                actor="zf-self-repair",
                payload={
                    "fingerprint": key[0],
                    "attempt": key[1],
                    "candidate_id": str(payload.get("candidate_id") or ""),
                    "status": "blocked",
                    "reason": exc.code,
                    "repair_contract_ref": payload.get("repair_contract_ref")
                    if isinstance(payload.get("repair_contract_ref"), dict)
                    else {},
                    "repair_contract_digest": str(
                        payload.get("repair_contract_digest") or ""
                    ),
                },
                causation_id=str(getattr(event, "id", "") or "") or None,
            ))
            continue
        spawned_event = spawned.get(key)
        process_result: dict[str, Any] | None = None
        if spawned_event is not None:
            terminal_event = terminals.get(key)
            terminal_payload = (
                terminal_event.payload
                if terminal_event is not None
                and isinstance(terminal_event.payload, dict)
                else {}
            )
            if str(terminal_payload.get("status") or "") == "completed":
                process_result = dict(
                    terminal_payload.get("process_result") or {}
                )
            else:
                process_result = read_repair_process_result(
                    Path(writer.event_log.path).parent,
                    spawned_event,
                )
                if process_result is None:
                    continue
                process_status = str(process_result.get("status") or "failed")
                if process_status != "completed":
                    emit_repair_terminal(
                        writer,
                        dispatched_event=event,
                        spawned_event=spawned_event,
                        payload=payload,
                        status=process_status,
                        reason=str(
                            process_result.get("reason")
                            or "repair_process_failed"
                        ),
                        process_result=process_result,
                    )
                    continue
        worktree = Path(str(payload.get("worktree") or ""))
        if not worktree.exists():
            if spawned_event is not None:
                emit_repair_terminal(
                    writer,
                    dispatched_event=event,
                    spawned_event=spawned_event,
                    payload=payload,
                    status="failed",
                    reason="repair_worktree_missing",
                    process_result=process_result or {},
                )
            continue
        branch_head = _git_stdout(worktree, "rev-parse", "HEAD")
        base_commit = str(payload.get("base_commit") or "").strip()
        comparison_base = base_commit or root_head
        if branch_head == comparison_base and spawned_event is not None and process_result is not None:
            from zf.runtime.repair_candidate_publish import publish_repair_candidate
            from zf.core.config.loader import ConfigError
            from zf.core.config.project_context import resolve_project_context
            try:
                state = Path(writer.event_log.path).parent
                context = resolve_project_context(explicit_state_dir=state, load_config_with_explicit=True)
                published = publish_repair_candidate(writer=writer, state_dir=state, harness=hroot,
                    root=worktree, payload=payload, contract=repair_contract,
                    policy=context.config.runtime.run_manager.source_repair)
                branch_head = published or branch_head
            except (ConfigError, ValueError, OSError) as exc:
                emit_repair_terminal(writer, dispatched_event=event, spawned_event=spawned_event,
                    payload=payload, status="blocked", reason=f"candidate_publish_blocked:{exc}",
                    process_result=process_result)
                continue
        if not branch_head or branch_head == comparison_base:
            if spawned_event is not None and process_result is not None:
                emit_repair_terminal(
                    writer,
                    dispatched_event=event,
                    spawned_event=spawned_event,
                    payload=payload,
                    status="no_commit",
                    reason="repair_process_exited_without_commit",
                    process_result=process_result,
                )
            continue
        ahead = _git_stdout(
            worktree,
            "rev-list",
            "--count",
            "HEAD",
            f"^{comparison_base}",
        )
        try:
            ahead_count = int(ahead or "0")
        except ValueError:
            ahead_count = 0
        if ahead_count <= 0:
            if spawned_event is not None and process_result is not None:
                emit_repair_terminal(
                    writer,
                    dispatched_event=event,
                    spawned_event=spawned_event,
                    payload=payload,
                    status="no_commit",
                    reason="repair_process_exited_without_commit",
                    process_result=process_result,
                )
            continue
        title = _git_stdout(worktree, "log", "-1", "--format=%s", "HEAD")
        changed_files = _git_lines(
            worktree,
            "diff",
            "--name-only",
            f"{comparison_base}..HEAD",
        )
        risk = _classify_closeout_risk(changed_files, commits_ahead=ahead_count)
        verification_plan = _closeout_verification_plan(
            risk,
            repair_contract=repair_contract,
        )
        restart = _closeout_restart_policy(risk)
        causation_id = str(getattr(event, "id", "") or "")
        if spawned_event is not None and key not in terminals:
            emit_repair_terminal(
                writer,
                dispatched_event=event,
                spawned_event=spawned_event,
                payload=payload,
                status="completed",
                reason="repair_process_completed_with_commit",
                process_result=process_result or {},
            )
        requested_continuation = payload.get("continuation")
        requested_continuation = (
            requested_continuation
            if isinstance(requested_continuation, dict)
            else {}
        )
        # Keep the frozen request identity, including empty slots and its schema.
        # Closeout policy is additive; conflicting request policy still fails the
        # immutable-contract check before any validation or apply action.
        continuation = {
            "schema_version": "self-repair.closeout-continuation.v1",
            "resume_original_workflow": True,
            **requested_continuation,
            "restart_required": restart["restart_required"],
            "restart_strategy": restart["restart_strategy"],
            "safe_boundary": restart["safe_boundary"],
            "state_snapshot_required": restart["state_snapshot_required"],
            "replay_required": restart["replay_required"],
            "resume_strategy": restart["resume_strategy"],
            "blocked_until": "verification_passed_and_apply_decision_recorded",
        }
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
                "repair_commit": branch_head,
                "base_commit": base_commit,
                "source_title": title,
                "commits_ahead": ahead_count,
                "changed_files": changed_files[:80],
                "risk_classification": risk,
                "verification_plan": verification_plan,
                "repair_contract_ref": dict(
                    payload.get("repair_contract_ref") or {}
                ),
                "repair_contract_digest": str(
                    payload.get("repair_contract_digest") or ""
                ),
                "repair_contract_required": True,
                "restart_strategy": restart["restart_strategy"],
                "safe_boundary": restart["safe_boundary"],
                "state_snapshot_required": restart["state_snapshot_required"],
                "replay_required": restart["replay_required"],
                "continuation": continuation,
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


def _closeout_verification_plan(
    risk: dict[str, Any],
    *,
    repair_contract: dict[str, Any],
) -> list[dict[str, str]]:
    categories = set(risk.get("categories") or [])
    plan = [{
        "kind": "diff_integrity",
        "command": "git diff --check",
        "required": "true",
    }]
    for command in repair_contract.get("verification_commands") or []:
        command = str(command or "").strip()
        if command and command not in {row["command"] for row in plan}:
            plan.append({
                "kind": "repair_contract",
                "command": command,
                "required": "true",
            })
    base_commit = str(repair_contract.get("base_commit") or "").strip()
    if base_commit:
        plan.extend([
            {
                "kind": "impact_plan",
                "command": f"python scripts/dev-verify.py plan --base {base_commit}",
                "required": "true",
            },
            {
                "kind": "impact_run",
                "command": f"python scripts/dev-verify.py run --base {base_commit}",
                "required": "true",
            },
        ])
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
