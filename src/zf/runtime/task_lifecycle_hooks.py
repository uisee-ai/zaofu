"""ZF-LH-LIFECYCLE-HOOKS-001 — user-configurable task lifecycle hooks (doc 26 §4.3).

zf.yaml gains a ``workflow.task_hooks`` section with after_{create,
start,finish,archive} hook commands. The orchestrator triggers these
hooks at the corresponding lifecycle transitions so users can integrate
zaofu with Linear / Slack / Jira / arbitrary scripts without kernel
changes.

Hooks are best-effort. Non-zero exit code emits ``hook.write_failed``
audit event but does NOT roll back the lifecycle transition (the
transition is already truth in events.jsonl; hooks are downstream
plumbing).
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TaskHookConfig:
    """Lifecycle hook config — declared in zf.yaml under
    workflow.task_hooks.after_*. Empty command list → no-op."""

    after_create: tuple[str, ...] = ()
    after_start: tuple[str, ...] = ()
    after_finish: tuple[str, ...] = ()
    after_archive: tuple[str, ...] = ()


_KNOWN_PHASES: frozenset[str] = frozenset({
    "after_create", "after_start", "after_finish", "after_archive",
})


@dataclass(frozen=True)
class HookExecutionResult:
    """One hook command's outcome."""

    phase: str
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def list_hooks_for_phase(
    config: TaskHookConfig,
    phase: str,
) -> tuple[str, ...]:
    """Return the configured hook commands for a phase, or () for
    unknown phases."""
    if phase not in _KNOWN_PHASES:
        return ()
    return getattr(config, phase, ()) or ()


def _format_command(
    template: str,
    *,
    task_id: str,
    feature_id: str = "",
    role: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    """Substitute placeholders ``{task_id}`` / ``{feature_id}`` /
    ``{role}`` / extra keys in a hook command template."""
    substitutions = {
        "task_id": task_id,
        "feature_id": feature_id,
        "role": role,
    }
    if extra:
        substitutions.update(extra)
    return template.format(**substitutions)


def execute_hooks(
    config: TaskHookConfig,
    phase: str,
    *,
    task_id: str,
    feature_id: str = "",
    role: str = "",
    extra: dict[str, str] | None = None,
    runner=None,
    timeout: int = 30,
) -> list[HookExecutionResult]:
    """Execute all hooks for the given phase. Returns one
    HookExecutionResult per command.

    ``runner`` injects a custom subprocess.run-like callable for
    testing. Default invokes the real subprocess in shell-tokenized
    mode (shlex.split). Errors do not raise; they're captured in
    the result list so the caller can emit audit events.
    """
    commands = list_hooks_for_phase(config, phase)
    if not commands:
        return []

    results: list[HookExecutionResult] = []
    for template in commands:
        cmd = _format_command(
            template,
            task_id=task_id, feature_id=feature_id, role=role,
            extra=extra,
        )
        if runner is not None:
            try:
                rc = runner(cmd)
            except Exception as exc:
                results.append(HookExecutionResult(
                    phase=phase, command=cmd, returncode=255,
                    stdout="", stderr=str(exc),
                ))
                continue
            results.append(HookExecutionResult(
                phase=phase, command=cmd, returncode=int(rc),
                stdout="", stderr="",
            ))
            continue
        try:
            proc = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            results.append(HookExecutionResult(
                phase=phase, command=cmd,
                returncode=proc.returncode,
                stdout=proc.stdout[:2000],
                stderr=proc.stderr[:2000],
            ))
        except subprocess.TimeoutExpired:
            results.append(HookExecutionResult(
                phase=phase, command=cmd, returncode=124,
                stdout="", stderr=f"timeout after {timeout}s",
            ))
        except FileNotFoundError as exc:
            results.append(HookExecutionResult(
                phase=phase, command=cmd, returncode=127,
                stdout="", stderr=str(exc),
            ))
        except Exception as exc:
            results.append(HookExecutionResult(
                phase=phase, command=cmd, returncode=255,
                stdout="", stderr=str(exc),
            ))
    return results
