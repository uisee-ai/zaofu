"""Self-repair closeout validation helpers for Run Manager.

The executor intentionally runs a small allowlisted command surface. It proves
that a repair worktree is ready for an operator apply decision without merging
or mutating the main worktree.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

from zf.runtime.repair_process_environment import isolated_repair_environment
from zf.runtime.repair_process_supervisor import RepairProcessCleanupError, run_repair_command


def execute_repair_verification_plan(
    *,
    worktree: str,
    verification_plan: list[Any],
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run an allowlisted self-repair verification plan in the repair worktree."""

    root = Path(str(worktree or "")).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "schema_version": "run-manager.repair-validation-result.v1",
            "ok": False,
            "status": "failed",
            "reason": "worktree_missing",
            "worktree": str(root),
            "results": [],
        }
    if not verification_plan:
        return {
            "schema_version": "run-manager.repair-validation-result.v1",
            "ok": False,
            "status": "failed",
            "reason": "verification_plan_missing",
            "worktree": str(root),
            "results": [],
        }

    with TemporaryDirectory(prefix='zf-repair-verification-') as workspace:
        environment = isolated_repair_environment(workspace)
        return _execute_steps(root, verification_plan, timeout_seconds, environment)


def _execute_steps(root: Path, verification_plan: list[Any], timeout_seconds: int,
                   environment: dict[str, str]) -> dict[str, Any]:
    """Test state is isolated from the resident controller and removed on exit."""
    results: list[dict[str, Any]] = []
    for index, step in enumerate(verification_plan):
        row = step if isinstance(step, dict) else {}
        command = str(row.get("command") or "").strip()
        kind = str(row.get("kind") or f"step-{index}")
        required = str(row.get("required") or "true").lower() != "false"
        if not command:
            result = {
                "kind": kind,
                "command": command,
                "required": required,
                "status": "failed" if required else "skipped",
                "reason": "missing_command",
                "returncode": 1 if required else 0,
            }
            results.append(result)
            if required:
                break
            continue
        if kind == "docs_review" or command.startswith("review "):
            results.append({
                "kind": kind,
                "command": command,
                "required": required,
                "status": "failed" if required else "skipped",
                "reason": "manual_review_evidence_required",
                "returncode": 1 if required else 0,
            })
            if required:
                break
            continue
        invocation = _trusted_invocation(command)
        if invocation is None:
            result = {
                "kind": kind,
                "command": command,
                "required": required,
                "status": "failed" if required else "skipped",
                "reason": "command_not_allowlisted",
                "returncode": 126,
            }
            results.append(result)
            if required:
                break
            continue
        env = environment.copy()
        env.update(invocation["env"])
        try:
            completed = run_repair_command(
                invocation["argv"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = int(completed.returncode)
            result = {
                "kind": kind,
                "command": command,
                "required": required,
                "status": "passed" if returncode == 0 else "failed",
                "reason": "" if returncode == 0 else "command_failed",
                "returncode": returncode,
                "stdout_tail": _tail(completed.stdout),
                "stderr_tail": _tail(completed.stderr),
                "process_cleanup": completed.cleanup_receipt,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "kind": kind,
                "command": command,
                "required": required,
                "status": "failed",
                "reason": "timeout",
                "returncode": 124,
                "stdout_tail": _tail(exc.stdout),
                "stderr_tail": _tail(exc.stderr),
                "process_cleanup": exc.cleanup_receipt,
            }
        except RepairProcessCleanupError as exc:
            result = {"kind": kind, "command": command, "required": required,
                "status": "failed", "reason": f"repair_process_cleanup_unknown:{exc}",
                "returncode": 125, "process_cleanup": {"cleanup": "unknown"}}
        except OSError as exc:
            result = {
                "kind": kind,
                "command": command,
                "required": required,
                "status": "failed",
                "reason": f"spawn_failed:{exc}",
                "returncode": 127,
            }
        results.append(result)
        if result.get("process_cleanup", {}).get("cleanup") == "unknown" or (required and result["status"] != "passed"):
            break

    failed_required = [
        result for result in results
        if result.get("process_cleanup", {}).get("cleanup") == "unknown"
        or (bool(result.get("required", True)) and result.get("status") != "passed")
    ]
    ok = not failed_required
    return {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "reason": "" if ok else str(failed_required[0].get("reason") or "validation_failed"),
        "worktree": str(root),
        "results": results,
    }


def _trusted_invocation(command: str) -> dict[str, Any] | None:
    from zf.runtime.repair_checkout_contract import trusted_pytest_invocation

    pytest_invocation = trusted_pytest_invocation(command)
    if pytest_invocation is not None:
        return pytest_invocation
    if command == "git diff --check":
        return {"argv": ["git", "diff", "--check"], "env": {}}
    if command == "npm --prefix web run build":
        return {"argv": ["npm", "--prefix", "web", "run", "build"], "env": {}}
    prefix = "PYTEST_ADDOPTS=--no-cov "
    env: dict[str, str] = {}
    if command.startswith(prefix):
        env["PYTEST_ADDOPTS"] = "--no-cov"
        command = command[len(prefix):].strip()
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if (
        len(argv) == 5
        and argv[0] in {"python", "python3"}
        and argv[1] == "scripts/dev-verify.py"
        and argv[2] in {"plan", "run"}
        and argv[3] == "--base"
        and re.fullmatch(r"[A-Za-z0-9._/-]+", argv[4])
    ):
        return {"argv": [sys.executable, *argv[1:]], "env": {**env, "PYTHONPATH": "src"}}
    if argv == ["bash", "scripts/dev-premerge-gate.sh"]:
        return {"argv": argv, "env": env}
    if argv == ["bash", "scripts/run-flow-smoke.sh"]:
        return {"argv": argv, "env": env}
    return None


def _tail(value: object, *, limit: int = 2000) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text[-limit:]
