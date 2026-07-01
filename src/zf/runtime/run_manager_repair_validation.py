"""Self-repair closeout validation helpers for Run Manager.

The executor intentionally runs a small allowlisted command surface. It proves
that a repair worktree is ready for an operator apply decision without merging
or mutating the main worktree.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


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
                "status": "passed",
                "reason": "manual_review_instruction_recorded",
                "returncode": 0,
            })
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
        env = os.environ.copy()
        env.update(invocation["env"])
        try:
            completed = subprocess.run(
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
            }
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
        if required and result["status"] != "passed":
            break

    failed_required = [
        result for result in results
        if bool(result.get("required", True))
        and result.get("status") != "passed"
        and str(result.get("reason") or "") != "manual_review_instruction_recorded"
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
    if command == "git diff --check":
        return {"argv": ["git", "diff", "--check"], "env": {}}
    if command == "npm --prefix web run build":
        return {"argv": ["npm", "--prefix", "web", "run", "build"], "env": {}}
    prefix = "PYTEST_ADDOPTS=--no-cov "
    env: dict[str, str] = {}
    if command.startswith(prefix):
        env["PYTEST_ADDOPTS"] = "--no-cov"
        command = command[len(prefix):].strip()
    argv = shlex.split(command)
    if len(argv) >= 4 and argv[:3] == ["uv", "run", "pytest"]:
        return {"argv": argv, "env": env}
    return None


def _tail(value: object, *, limit: int = 2000) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text[-limit:]
