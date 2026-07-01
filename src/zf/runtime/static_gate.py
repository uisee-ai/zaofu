"""P3/K5 (docs/impl/22-zaofu-canonical-dag.md): static_gate as an
independent DAG stage between ⑤ implement and ⑦ code_review.

Triggered when dev.build.done fires. Runs ``quality_gates.static.required_checks``
sequentially against the dev's working tree, and emits:

  - ``static_gate.passed`` — all checks returned exit_code 0
  - ``static_gate.failed`` — one or more checks failed (with per-check detail)
  - ``static_gate.skipped`` — no checks configured / dag disabled / static gate disabled

Failed gates rely on ``workflow.rework_routing[static_gate.failed]`` to route
back to dev. If unconfigured, the rework router's "dev" fallback applies
(per-project default).

This module DOES NOT replace ``candidates.py:_run_quality_gates`` which
runs the same checks during candidate integration (post-judge). Both
phases run; the static_gate here is the per-task early gate, candidates
is the merged-feature gate. They share commands but execute on different
worktrees.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


_OUTPUT_TRUNCATE = 4000
_DEFAULT_TIMEOUT_SEC = 600


@dataclass(frozen=True)
class StaticCheckResult:
    """Single command result from running a static check."""

    command: str
    exit_code: int
    passed: bool
    output: str
    duration_seconds: float
    gate_name: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "output": self.output,
            "duration_seconds": round(self.duration_seconds, 3),
            "gate_name": self.gate_name,
        }


@dataclass(frozen=True)
class StaticGateResult:
    """Aggregate result of running all configured static checks."""

    passed: bool
    checks: list[StaticCheckResult] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def failed_commands(self) -> list[str]:
        return [c.command for c in self.checks if not c.passed]

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "check_count": len(self.checks),
            "failed_count": sum(1 for c in self.checks if not c.passed),
            "checks": [c.to_dict() for c in self.checks],
            "failed_commands": self.failed_commands,
        }


def run_static_gate(
    *,
    config: Any,
    project_root: Path,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SEC,
) -> StaticGateResult:
    """Run ``config.quality_gates['static'].required_checks`` sequentially.

    Returns a StaticGateResult. Caller decides whether to skip emit based
    on dag.enabled — this function is pure (no event emission).

    Skip semantics:
      - `quality_gates` has no `static` key, or `static.enabled=False`, or
        `static.required_checks` empty → skipped=True, passed=True
        (no-op for backward compat).
    """
    static_cfg = None
    try:
        static_cfg = (config.quality_gates or {}).get("static")
    except Exception:
        static_cfg = None

    if static_cfg is None:
        return StaticGateResult(
            passed=True, skipped=True,
            skip_reason="quality_gates.static not configured",
        )
    if not getattr(static_cfg, "enabled", True):
        return StaticGateResult(
            passed=True, skipped=True,
            skip_reason="quality_gates.static.enabled=False",
        )
    commands = list(getattr(static_cfg, "required_checks", []) or [])
    if not commands:
        return StaticGateResult(
            passed=True, skipped=True,
            skip_reason="quality_gates.static.required_checks empty",
        )

    checks: list[StaticCheckResult] = []
    overall_pass = True
    for command in commands:
        result = _run_one(command, project_root, timeout_seconds)
        checks.append(result)
        if not result.passed:
            overall_pass = False
    return StaticGateResult(passed=overall_pass, checks=checks, skipped=False)


def build_static_gate_event(
    result: StaticGateResult,
    *,
    trigger_event: ZfEvent,
    actor: str = "zf-cli",
) -> ZfEvent:
    """Translate a StaticGateResult into the appropriate event:

      - passed=True, skipped=False → static_gate.passed
      - skipped=True              → static_gate.skipped
      - passed=False              → static_gate.failed
    """
    if result.skipped:
        event_type = "static_gate.skipped"
    elif result.passed:
        event_type = "static_gate.passed"
    else:
        event_type = "static_gate.failed"

    payload = result.to_payload()
    payload["trigger_event_id"] = trigger_event.id
    payload["trigger_event_type"] = trigger_event.type
    # B-NEW-9 (2026-05-17): inherit dispatch_id from trigger so the
    # reconciler's stale-handoff guard (_progress_event_matches_active_dispatch_at)
    # can match this kernel-emitted event against the active task.dispatched.
    # Without this, static_gate.passed has actual_dispatch_id="" → ≠ the
    # latest task.dispatched.dispatch_id → reconciler returns False →
    # never auto-routes static_gate.passed → review. (Cangjie r-next-7
    # blocker.)
    if isinstance(trigger_event.payload, dict):
        trigger_dispatch_id = trigger_event.payload.get("dispatch_id")
        if trigger_dispatch_id:
            payload["dispatch_id"] = trigger_dispatch_id
    return ZfEvent(
        type=event_type,
        actor=actor,
        task_id=trigger_event.task_id,
        payload=payload,
        causation_id=trigger_event.id,
        correlation_id=trigger_event.correlation_id,
    )


def is_static_gate_enabled_for_event(event: ZfEvent, config: Any) -> bool:
    """Return True if static_gate should run on this event.

    Conditions:
      - event.type == "dev.build.done"
      - workflow.dag.enabled is True (opt-in for backward compat)
      - quality_gates.static is configured

    This is the kernel hook gate so we don't redundantly run static checks
    on projects that haven't opted into the workflow.dag pipeline.
    """
    if event.type != "dev.build.done":
        return False
    try:
        dag = getattr(getattr(config, "workflow", None), "dag", None)
    except Exception:
        dag = None
    if dag is None or not getattr(dag, "enabled", False):
        return False
    try:
        static_cfg = (config.quality_gates or {}).get("static")
    except Exception:
        return False
    if static_cfg is None:
        return False
    return bool(getattr(static_cfg, "enabled", True))


def _run_one(
    command: str,
    cwd: Path,
    timeout_seconds: int,
) -> StaticCheckResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_gate_env(cwd),
        )
        output = (
            (proc.stdout or "")
            + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
        )
        return StaticCheckResult(
            command=command,
            exit_code=proc.returncode,
            passed=proc.returncode == 0,
            output=output[:_OUTPUT_TRUNCATE],
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired:
        return StaticCheckResult(
            command=command,
            exit_code=124,  # timeout convention
            passed=False,
            output=f"static_gate: command timed out after {timeout_seconds}s",
            duration_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        return StaticCheckResult(
            command=command,
            exit_code=1,
            passed=False,
            output=f"static_gate: invocation error — {type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
        )


def _gate_env(cwd: Path) -> dict[str, str]:
    """Run gate checks against the selected checkout, not the caller checkout."""
    env = os.environ.copy()
    cwd = cwd.resolve()
    project_src = cwd / "src"
    if project_src.exists():
        inherited = env.get("PYTHONPATH", "")
        parts = [
            part
            for part in inherited.split(os.pathsep)
            if part and Path(part).resolve() != project_src.resolve()
        ]
        env["PYTHONPATH"] = os.pathsep.join([str(project_src), *parts])
    else:
        env.pop("PYTHONPATH", None)
    env["ZF_PROJECT_ROOT"] = str(cwd)
    inherited_venv = env.get("VIRTUAL_ENV")
    workspace_venv = (cwd / ".venv").resolve()
    if inherited_venv and Path(inherited_venv).resolve() != workspace_venv:
        env.pop("VIRTUAL_ENV", None)
    return env
