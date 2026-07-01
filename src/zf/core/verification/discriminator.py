"""Discriminator framework — multi-D verification before task done.

Sprint H MVP: introduces the abstraction + 2 concrete D classes
(ContractD, FunctionalD). 5 other D types from `06-verification.md`
(Adversarial / Structural / Behavioral / Process / Semantic) are
deferred to Sprint H+1; the framework is designed so each can plug in
as a new BaseDiscriminator subclass without touching the runner or
orchestrator wiring.

Core invariant: AND closure — DiscriminatorReport.passed is True only
if every result is passed. A single failing D blocks task → done.
"""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.task.schema import Task, VALID_VERIFICATION_TIERS
from zf.core.verification.architecture_rules import parse_rules
from zf.core.verification.evidence import command_evidence
from zf.core.verification.promoted_rules import PromotedRulesStore
from zf.core.verification.validation import (
    coerce_validation_spec,
    evaluate_validation_spec,
)


def _verification_command_env(workspace: Path | None = None) -> dict[str, str]:
    """Return an environment suitable for non-interactive gate commands.

    systemd/tmux watcher processes often start with a stripped PATH that omits
    nvm/Volta-managed Node tools. Contract/quality gates are still project
    commands, so make common user tool locations visible without requiring each
    project to hard-code absolute pnpm paths.
    """
    env = os.environ.copy()
    if workspace is not None:
        _prefer_workspace_pythonpath(env, workspace)
    home = Path.home()
    candidates: list[Path] = [
        home / ".local" / "bin",
        home / ".volta" / "bin",
        home / ".bun" / "bin",
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        node_versions = sorted(
            (path for path in nvm_root.iterdir() if path.is_dir()),
            reverse=True,
        )
        candidates.extend(path / "bin" for path in node_versions)
        for path in node_versions:
            candidates.extend(_codex_vendor_tool_paths(
                path / "lib" / "node_modules",
            ))

    existing = [
        item for item in env.get("PATH", "").split(os.pathsep) if item
    ]
    merged: list[str] = []
    for path in [str(candidate) for candidate in candidates if candidate.exists()] + existing:
        if path not in merged:
            merged.append(path)
    env["PATH"] = os.pathsep.join(merged)
    return env


def _prefer_workspace_pythonpath(env: dict[str, str], workspace: Path) -> None:
    workspace = workspace.resolve()
    project_src = workspace / "src"
    if not project_src.exists():
        env.pop("PYTHONPATH", None)
        env["ZF_PROJECT_ROOT"] = str(workspace)
        _drop_foreign_virtualenv(env, workspace)
        return

    project_src_resolved = project_src.resolve()
    inherited = env.get("PYTHONPATH", "")
    inherited_parts = []
    for part in inherited.split(os.pathsep):
        if not part:
            continue
        if Path(part).resolve() == project_src_resolved:
            continue
        inherited_parts.append(part)
    env["PYTHONPATH"] = os.pathsep.join([str(project_src), *inherited_parts])
    env["ZF_PROJECT_ROOT"] = str(workspace)
    _drop_foreign_virtualenv(env, workspace)


def _drop_foreign_virtualenv(env: dict[str, str], workspace: Path) -> None:
    inherited = env.get("VIRTUAL_ENV")
    if not inherited:
        return
    workspace_venv = (workspace / ".venv").resolve()
    if Path(inherited).resolve() != workspace_venv:
        env.pop("VIRTUAL_ENV", None)


def _provision_web_node_modules(workspace: Path) -> dict[str, str]:
    """Best-effort Web dependency provision for git worktree verification.

    Git worktrees do not include ignored ``web/node_modules``. Contract
    verification often runs ``npm --prefix web ...`` inside task refs, so reuse
    the already-installed dependencies from the source checkout when available.
    """
    web_dir = workspace / "web"
    if (
        not (web_dir / "package.json").exists()
        or not (web_dir / "package-lock.json").exists()
    ):
        return {"status": "skipped", "reason": "no_web_package"}

    target = web_dir / "node_modules"
    target_tsc = target / ".bin" / "tsc"
    if target_tsc.exists():
        return {"status": "present", "target": str(target)}

    source_roots: list[Path] = []
    for raw in (os.environ.get("ZF_PROJECT_ROOT", ""), os.getcwd()):
        if not raw:
            continue
        try:
            candidate = Path(raw).resolve()
        except OSError:
            continue
        if candidate == workspace.resolve() or candidate in source_roots:
            continue
        source_roots.append(candidate)

    for source_root in source_roots:
        source = source_root / "web" / "node_modules"
        source_tsc = source / ".bin" / "tsc"
        if not source_tsc.exists():
            continue
        if target.exists() and not target.is_symlink():
            return {
                "status": "blocked",
                "reason": "target_node_modules_exists_without_tsc",
                "target": str(target),
            }
        try:
            if target.is_symlink():
                target.unlink()
            target.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            return {
                "status": "blocked",
                "reason": type(exc).__name__,
                "target": str(target),
                "source": str(source),
            }
        return {
            "status": "linked",
            "target": str(target),
            "source": str(source),
        }

    return {
        "status": "missing",
        "reason": "source_node_modules_missing",
        "target": str(target),
    }


def _verification_needs_web_python_extra(command: str) -> bool:
    normalized = " ".join((command or "").split())
    return any(
        marker in normalized
        for marker in (
            "zf web",
            "full_stack_validation.py",
            "/api/actions/",
            "chat-orchestrator",
        )
    )


def _provision_web_python_extra(
    workspace: Path,
    *,
    command: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Prepare optional Python Web deps for isolated worktree verification."""
    if not _verification_needs_web_python_extra(command):
        return {"status": "skipped", "reason": "command_not_web"}
    pyproject = workspace / "pyproject.toml"
    if not pyproject.exists():
        return {"status": "skipped", "reason": "no_pyproject"}
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "blocked", "reason": type(exc).__name__}
    if "web =" not in text:
        return {"status": "skipped", "reason": "no_web_extra"}

    probe = [
        "uv",
        "run",
        "--extra",
        "web",
        "python",
        "-c",
        "import fastapi, uvicorn, websockets",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            env=_verification_command_env(workspace),
            timeout=timeout_seconds,
        )
        payload = command_evidence(
            command=" ".join(probe),
            exit_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_evidence(
            command=" ".join(probe),
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
    except Exception as exc:
        payload = command_evidence(
            command=" ".join(probe),
            exit_code=None,
            error=str(exc),
        )
    payload["status"] = "ready" if payload.get("passed") else "failed"
    payload["duration_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _codex_vendor_tool_paths(node_modules_root: Path) -> list[Path]:
    """Return tool directories bundled with the globally installed Codex CLI."""
    codex_node_modules = (
        node_modules_root
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
    )
    if not codex_node_modules.exists():
        return []
    return sorted(
        (
            path
            for path in codex_node_modules.glob("codex-*/vendor/*/path")
            if path.is_dir()
        ),
        reverse=True,
    )


def _verification_syntax_check(command: str) -> dict[str, Any] | None:
    """Pre-flight shell-syntax check on the verification command.

    Runs ``sh -n -c "<command>"`` (POSIX "noexec"): parses the command
    without executing any builtin or external program. Catches operator
    pitfalls like an unbalanced ``)`` or unterminated quotes BEFORE the
    discriminator launches a real run that would otherwise fail with rc=2
    and trigger a useless rework round through dev.

    Returns:
      None when the command is syntactically valid (or trivially short)
      A dict ``{"passed": False, "stderr": ..., "exit_code": ...}`` when
        the syntax check failed, so the caller can short-circuit with a
        discriminator failure tagged as a CONTRACT bug, not a worker bug.
    """
    if not command or not command.strip():
        return None
    try:
        result = subprocess.run(
            ["sh", "-n", "-c", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        # sh -n shouldn't ever block; treat as inconclusive (pass through)
        return None
    except (OSError, subprocess.SubprocessError):
        # No /bin/sh on PATH? Pass through — would have caught at real run.
        return None
    if result.returncode == 0:
        return None
    return {
        "passed": False,
        "exit_code": result.returncode,
        "stderr": (result.stderr or "").strip()[:400],
        "command_preview": command[:200],
    }


def _verification_preflight(
    *,
    command: str,
    workspace: Path,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    if "pnpm" not in command:
        return None
    if not (workspace / "pnpm-lock.yaml").is_file():
        return None
    if (workspace / "node_modules").exists():
        return None
    started = time.monotonic()
    try:
        result = subprocess.run(
            "pnpm install --frozen-lockfile",
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            env=_verification_command_env(workspace),
            timeout=timeout_seconds,
        )
        payload = command_evidence(
            command="pnpm install --frozen-lockfile",
            exit_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_evidence(
            command="pnpm install --frozen-lockfile",
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
    except Exception as exc:
        payload = command_evidence(
            command="pnpm install --frozen-lockfile",
            exit_code=None,
            error=str(exc),
        )
    payload["duration_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _latest_expected_red_evidence(
    task_id: str,
    *,
    command: str,
    returncode: int,
    event_log: EventLog,
) -> dict[str, Any]:
    """Return gate evidence proving a non-zero command is expected RED.

    ContractD normally treats non-zero verification as a hard failure. RED-test
    tasks are different: the verification command is expected to fail until a
    later implementation task makes it green. To keep this fail-closed, accept a
    non-zero command only when a prior gate success event explicitly recorded
    matching RED_expected evidence for the same task.
    """
    if returncode == 0:
        return {}
    command = str(command or "").strip()
    if not command:
        return {}
    try:
        events = event_log.read_all()
    except Exception:
        return {}
    for event in reversed(events):
        if event.task_id != task_id:
            continue
        if event.type not in {"judge.passed", "test.passed", "review.approved"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for check in _iter_gate_checks(payload):
            if str(check.get("status") or "") != "RED_expected":
                continue
            try:
                exit_code = int(check.get("exit_code"))
            except (TypeError, ValueError):
                continue
            if exit_code != returncode:
                continue
            check_command = str(check.get("command") or "").strip()
            if check_command and check_command != command:
                continue
            return {
                "source_event_id": event.id,
                "source_event_type": event.type,
                "check": check,
            }
    return {}


def _split_mixed_verification(command: str) -> tuple[str, str]:
    """Split a shell gate from a prose evidence clause.

    Agent-authored contracts sometimes append a human verification clause after
    a valid shell command, e.g. `pnpm exec tsc ... && run a scope-local runtime
    probe ...`. The shell can execute the first half, but `run ...` is prose
    unless a project defines a command named `run`. Keep this intentionally
    narrow so normal compound shell commands still execute as written.
    """
    raw = str(command or "").strip()
    marker = "&& run "
    idx = raw.find(marker)
    if idx > 0:
        shell_command = raw[:idx].strip()
        prose_tail = raw[idx + len("&& "):].strip()
        if shell_command and prose_tail:
            return shell_command, prose_tail

    # Full-width semicolon is not shell syntax; agents often use it to append
    # Chinese manual-evidence instructions after an executable gate.
    if "；" in raw:
        shell_command, prose_tail = (part.strip() for part in raw.split("；", 1))
        if shell_command and prose_tail:
            return shell_command, prose_tail

    return raw, ""


def _prose_tail_requires_terminal_evidence(prose_tail: str) -> bool:
    tail = str(prose_tail or "").strip().lower()
    if "probe" not in tail:
        return False
    return tail.startswith("run ") or "runtime" in tail or "scope-local" in tail


def _expected_red_declared(contract: Any, validation_spec: dict[str, Any]) -> bool:
    expected = str(validation_spec.get("expected_result") or "").strip().lower()
    if expected in {"red", "expected_red", "red_expected", "failure_expected"}:
        return True
    try:
        expected_exit = int(validation_spec.get("expected_exit_code"))
    except (TypeError, ValueError):
        expected_exit = 0
    if expected_exit != 0:
        return True
    evidence_contract = getattr(contract, "evidence_contract", {}) if contract else {}
    if isinstance(evidence_contract, dict):
        value = evidence_contract.get("expected_red")
        if value is True:
            return True
        if str(value or "").strip().lower() in {"true", "yes", "1", "red"}:
            return True
    return False


def _contract_declared_paths(contract: object | None) -> list[str]:
    if contract is None:
        return []
    out: list[str] = []
    for attr in ("scope", "affected_files", "shared_files", "exclusive_files"):
        value = getattr(contract, attr, None)
        if isinstance(value, list):
            out.extend(str(item).strip() for item in value if str(item).strip())
    return out


def _is_docs_only_manual_evidence_contract(
    contract: object | None,
    requested_tiers: set[str],
) -> bool:
    if "manual_evidence" not in requested_tiers:
        return False
    paths = _contract_declared_paths(contract)
    if not paths:
        return False
    return all(
        path.startswith("docs/") and path.lower().endswith((".md", ".mdx"))
        for path in paths
    )


def _gate_check_exit_code(check: dict[str, Any]) -> int | None:
    try:
        return int(check.get("exit_code"))
    except (TypeError, ValueError):
        return None


def _latest_prose_tail_evidence(
    task_id: str,
    *,
    prose_tail: str,
    event_log: EventLog,
) -> dict[str, Any]:
    """Return terminal evidence for a non-shell verification tail.

    This is deliberately limited to the runtime-probe pattern produced by the
    Cangjie artifact-builder tasks. A prose clause is accepted only if a prior
    terminal gate for the same task recorded a successful runtime check whose
    command or summary names a probe.
    """
    tail = str(prose_tail or "").strip().lower()
    if "probe" not in tail:
        return {}
    try:
        events = event_log.read_all()
    except Exception:
        return {}
    for event in reversed(events):
        if event.task_id != task_id:
            continue
        if event.type not in {"judge.passed", "test.passed", "review.approved"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for check in _iter_gate_checks(payload):
            if _gate_check_exit_code(check) != 0:
                continue
            tier = str(check.get("tier") or "").strip().lower()
            if tier != "runtime":
                continue
            text = " ".join(
                str(check.get(key) or "")
                for key in ("command", "summary", "status")
            ).lower()
            if "probe" not in text:
                continue
            return {
                "source_event_id": event.id,
                "source_event_type": event.type,
                "check": check,
            }
    return {}


def _iter_gate_checks(value: Any):
    if isinstance(value, dict):
        if "exit_code" in value and (
            "command" in value or "status" in value or "tier" in value
        ):
            yield value
        for child in value.values():
            yield from _iter_gate_checks(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_gate_checks(child)


@dataclass
class DiscriminatorResult:
    """One D's outcome on one task."""
    d_name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class DiscriminatorReport:
    """Aggregated outcome of running a runner's full D list."""
    passed: bool   # AND closure: all d_results are passed
    d_results: list[DiscriminatorResult]


class BaseDiscriminator(ABC):
    """Abstract base for verification discriminators."""

    name: str = ""

    @abstractmethod
    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        """Evaluate the task against this D's criteria."""


class DiscriminatorRunner:
    """Run a list of discriminators with AND closure semantics.

    Exceptions inside an individual D are caught and turned into a
    failed result so a buggy D doesn't crash the dispatch loop.
    """

    def __init__(self, discriminators: list[BaseDiscriminator]) -> None:
        self.discriminators = list(discriminators)

    def run(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorReport:
        results: list[DiscriminatorResult] = []
        for d in self.discriminators:
            try:
                result = d.evaluate(task, workspace, event_log)
            except Exception as e:
                result = DiscriminatorResult(
                    d_name=d.name or d.__class__.__name__,
                    passed=False,
                    evidence={"error": str(e), "type": type(e).__name__},
                    reason=f"discriminator raised {type(e).__name__}: {e}",
                )
            results.append(result)
        return DiscriminatorReport(
            passed=all(r.passed for r in results),
            d_results=results,
        )


# -- Concrete discriminators (Sprint H MVP) --


class ContractD(BaseDiscriminator):
    """Verify the task's contract has the minimum evidence to count
    as 'done': behavior set, verification set, verification command
    runs cleanly.

    Empty contract (no behavior, no verification) is treated as PASS
    — backward compat with existing tasks that didn't fill these.
    Only filled contracts are held to the standard.
    """

    name = "ContractD"

    DEFAULT_TIMEOUT_SECONDS = 300

    def __init__(self, *, require_contract: bool = False) -> None:
        self.require_contract = require_contract

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        contract = task.contract
        behavior_set = bool(contract and contract.behavior)
        validation_spec = coerce_validation_spec(
            getattr(contract, "validation", {}) if contract else {},
        )
        validation_set = bool(validation_spec)
        verification_set = bool(contract and (contract.verification or validation_set))
        verification_tiers = list(getattr(contract, "verification_tiers", []) or [])
        unknown_tiers = sorted(set(verification_tiers) - VALID_VERIFICATION_TIERS)
        if unknown_tiers:
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={
                    "verification_tiers": verification_tiers,
                    "unknown_tiers": unknown_tiers,
                    "valid_tiers": sorted(VALID_VERIFICATION_TIERS),
                },
                reason=f"unknown verification tiers: {unknown_tiers}",
            )

        # Backward compat: truly empty contract = pass. A tier declaration is
        # contract intent, so it must also carry behavior + verification.
        if (
            not behavior_set
            and not verification_set
            and not verification_tiers
            and not validation_set
        ):
            if self.require_contract:
                return DiscriminatorResult(
                    d_name=self.name, passed=False,
                    evidence={
                        "contract_empty": True,
                        "require_contract": True,
                    },
                    reason="task contract is required but empty",
                )
            return DiscriminatorResult(
                d_name=self.name, passed=True,
                evidence={"contract_empty": True},
                reason="no contract evidence required",
            )

        if not behavior_set:
            return DiscriminatorResult(
                d_name=self.name, passed=False,
                evidence={"behavior_set": False},
                reason="contract.behavior is empty but verification is set",
            )
        if not verification_set:
            return DiscriminatorResult(
                d_name=self.name, passed=False,
                evidence={"verification_set": False},
                reason="contract.verification is empty",
            )

        verification_command = str(contract.verification or "").strip()
        if validation_set and str(validation_spec.get("kind") or "") == "command":
            verification_command = (
                verification_command
                or str(validation_spec.get("command") or "").strip()
            )
            if verification_command:
                validation_spec = {
                    **validation_spec,
                    "command": verification_command,
                }
        shell_command, prose_tail = _split_mixed_verification(verification_command)
        expected_red_declared = _expected_red_declared(contract, validation_spec)

        validation_result = None
        if validation_set:
            validation_result = evaluate_validation_spec(
                validation_spec,
                workspace=workspace,
            )
            if not validation_result.passed:
                return DiscriminatorResult(
                    d_name=self.name,
                    passed=False,
                    evidence={
                        "behavior_set": True,
                        "verification_set": bool(verification_command),
                        "validation": validation_result.evidence,
                        "validation_kind": validation_spec.get("kind", ""),
                        "validation_passed": False,
                    },
                    reason=validation_result.reason,
                )
            if not shell_command:
                return DiscriminatorResult(
                    d_name=self.name,
                    passed=True,
                    evidence={
                        "behavior_set": True,
                        "verification_set": False,
                        "validation": validation_result.evidence,
                        "validation_kind": validation_spec.get("kind", ""),
                        "validation_passed": True,
                        "verification_passed": None,
                    },
                    reason="structured validation passed",
                )

        # P2 #3 (backlog 2026-05-14): catch contract-side shell syntax
        # errors (unbalanced ), unterminated quotes, etc.) before we
        # exec, so operator pitfalls don't ride through as worker faults.
        syntax_check = _verification_syntax_check(shell_command)
        if syntax_check is not None:
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={
                    "behavior_set": True,
                    "verification_set": True,
                    "verification_tiers": verification_tiers,
                    "validation": (
                        validation_result.evidence if validation_result else {}
                    ),
                    "validation_kind": validation_spec.get("kind", ""),
                    "verification_syntax_check": syntax_check,
                    "contract_syntax_invalid": True,
                    "verification_passed": False,
                },
                reason=(
                    f"contract.verification has shell syntax error "
                    f"(sh -n exit={syntax_check['exit_code']}): "
                    f"{syntax_check['stderr']}"
                ),
            )

        preflight = _verification_preflight(
            command=shell_command,
            workspace=workspace,
            timeout_seconds=self.DEFAULT_TIMEOUT_SECONDS,
        )
        if preflight is not None and not preflight.get("passed"):
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={
                    "behavior_set": True,
                    "verification_set": True,
                    "verification_tiers": verification_tiers,
                    "validation": (
                        validation_result.evidence if validation_result else {}
                    ),
                    "validation_kind": validation_spec.get("kind", ""),
                    "verification_preflight": preflight,
                    "verification_passed": False,
                },
                reason="verification preflight failed",
            )

        web_python_dependencies = _provision_web_python_extra(
            workspace,
            command=shell_command,
            timeout_seconds=self.DEFAULT_TIMEOUT_SECONDS,
        )
        web_dependencies = _provision_web_node_modules(workspace)

        # Run verification command
        try:
            result = subprocess.run(
                shell_command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(workspace),
                env=_verification_command_env(workspace),
                timeout=self.DEFAULT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return DiscriminatorResult(
                d_name=self.name, passed=False,
                evidence={
                    "verification_passed": False,
                    "timeout_seconds": self.DEFAULT_TIMEOUT_SECONDS,
                    "web_python_dependencies": web_python_dependencies,
                    "web_dependencies": web_dependencies,
                    "validation": (
                        validation_result.evidence if validation_result else {}
                    ),
                    "validation_kind": validation_spec.get("kind", ""),
                },
                reason="verification command timed out",
            )
        except Exception as e:
            return DiscriminatorResult(
                d_name=self.name, passed=False,
                evidence={
                    "error": str(e),
                    "web_python_dependencies": web_python_dependencies,
                    "web_dependencies": web_dependencies,
                    "validation": (
                        validation_result.evidence if validation_result else {}
                    ),
                    "validation_kind": validation_spec.get("kind", ""),
                },
                reason=f"verification command crashed: {e}",
            )

        expected_red = _latest_expected_red_evidence(
            task.id,
            command=shell_command,
            returncode=result.returncode,
            event_log=event_log,
        )
        prose_tail_evidence: dict[str, Any] = {}
        prose_tail_requires_evidence = _prose_tail_requires_terminal_evidence(
            prose_tail,
        )
        prose_tail_missing = False
        if result.returncode == 0 and prose_tail_requires_evidence:
            prose_tail_evidence = _latest_prose_tail_evidence(
                task.id,
                prose_tail=prose_tail,
                event_log=event_log,
            )
            prose_tail_missing = not bool(prose_tail_evidence)
        passed = (result.returncode == 0 and not prose_tail_missing) or bool(expected_red)
        reason = ""
        if not passed:
            if prose_tail_missing:
                reason = "verification prose clause lacks terminal evidence"
            elif expected_red_declared and result.returncode != 0:
                reason = "expected-red verification lacks matching terminal evidence"
            else:
                reason = f"verification command failed (rc={result.returncode})"
        return DiscriminatorResult(
            d_name=self.name,
            passed=passed,
            evidence={
                "behavior_set": True,
                "verification_set": True,
                "verification_tiers": verification_tiers,
                "validation": (
                    validation_result.evidence if validation_result else {}
                ),
                "validation_kind": validation_spec.get("kind", ""),
                "validation_passed": (
                    validation_result.passed if validation_result else None
                ),
                "verification_preflight": preflight,
                "web_python_dependencies": web_python_dependencies,
                "web_dependencies": web_dependencies,
                "verification_passed": result.returncode == 0,
                "verification_command": verification_command,
                "verification_shell_command": shell_command,
                "verification_prose_tail": prose_tail,
                "verification_prose_tail_requires_evidence": (
                    prose_tail_requires_evidence
                ),
                "verification_prose_tail_evidence": prose_tail_evidence,
                "verification_expected_red": bool(expected_red),
                "verification_expected_red_declared": expected_red_declared,
                "expected_red_evidence": expected_red,
                "verification_returncode": result.returncode,
                "verification_stdout_tail": (result.stdout or "")[-500:],
                "verification_stderr_tail": (result.stderr or "")[-500:],
            },
            reason=reason,
        )


class ContractQualityD(BaseDiscriminator):
    """Fail closed on generic Sprint Contracts in strict harness presets.

    ContractD answers "does the declared verification execute?". This
    discriminator answers "is the declared contract specific enough to be
    meaningful?". It intentionally uses conservative heuristics so legacy
    projects can keep it disabled while harness presets can opt in.
    """

    name = "ContractQualityD"

    _GENERIC_ACCEPTANCE = {
        "",
        "exit_code=0",
        "tests pass",
        "test pass",
        "all tests pass",
        "code complete",
        "implementation complete",
        "done",
        "works",
        "no errors",
    }
    _TRIVIAL_VERIFICATION = {"", "true", ":", "echo ok", "printf ok"}
    _GENERIC_BEHAVIOR = {
        "x",
        "b",
        "do task",
        "fix bug",
        "implement feature",
        "make it work",
        "update code",
    }

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        contract = task.contract
        if contract is None:
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={"contract_missing": True},
                reason="task contract is missing",
            )

        issues: list[str] = []
        behavior = _norm(getattr(contract, "behavior", ""))
        verification = _norm(getattr(contract, "verification", ""))
        acceptance = _norm(getattr(contract, "acceptance", ""))
        scope = [str(item).strip() for item in getattr(contract, "scope", []) or []]

        if len(behavior) < 12 or behavior in self._GENERIC_BEHAVIOR:
            issues.append("contract.behavior is too generic")
        if acceptance in self._GENERIC_ACCEPTANCE:
            issues.append("contract.acceptance is too generic")
        if verification in self._TRIVIAL_VERIFICATION:
            issues.append("contract.verification is trivial")
        if not scope or scope in (["."], ["./"], ["*"], ["**/*"]):
            issues.append("contract.scope is missing or repo-wide")

        # A practical specificity check: behavior should mention at least two
        # word-like tokens so "login works" passes but "x" does not.
        behavior_tokens = [part for part in behavior.replace("_", " ").split() if part]
        if len(behavior_tokens) < 2:
            issues.append("contract.behavior lacks object/action detail")

        if issues:
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={
                    "issues": issues,
                    "behavior": getattr(contract, "behavior", ""),
                    "acceptance": getattr(contract, "acceptance", ""),
                    "verification": getattr(contract, "verification", ""),
                    "scope": scope,
                },
                reason="; ".join(issues),
            )
        return DiscriminatorResult(
            d_name=self.name,
            passed=True,
            evidence={
                "behavior": getattr(contract, "behavior", ""),
                "acceptance": getattr(contract, "acceptance", ""),
                "verification": getattr(contract, "verification", ""),
                "scope": scope,
            },
        )


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


class FunctionalD(BaseDiscriminator):
    """Wrap config.quality_gates and run every enabled gate's
    required_checks list. AND closure: all gates pass → passed.

    No gates configured → vacuous truth (passed=True). This keeps
    the discriminator backward-compatible with projects that don't
    define quality_gates yet.
    """

    name = "FunctionalD"

    DEFAULT_TIMEOUT_SECONDS = 300

    def __init__(
        self,
        quality_gates: dict[str, Any] | None = None,
    ) -> None:
        self.quality_gates = quality_gates or {}

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        gates_run: list[str] = []
        gates_passed: list[str] = []
        gates_failed: list[str] = []
        gates_skipped_by_tier: list[str] = []
        gates_skipped_by_profile: list[str] = []
        failure_details: dict[str, list[str]] = {}
        gate_checks: dict[str, list[dict[str, Any]]] = {}
        contract = getattr(task, "contract", None)
        requested_tiers = {
            str(tier).strip()
            for tier in (getattr(contract, "verification_tiers", []) or [])
            if str(tier).strip()
        }
        docs_only_manual_evidence = _is_docs_only_manual_evidence_contract(
            contract,
            requested_tiers,
        )

        for gate_name, gate_cfg in self.quality_gates.items():
            enabled = getattr(gate_cfg, "enabled", True)
            if not enabled:
                continue
            checks = getattr(gate_cfg, "required_checks", []) or []
            if not checks:
                continue
            if docs_only_manual_evidence and gate_name != "manual_evidence":
                gates_skipped_by_profile.append(gate_name)
                continue
            if requested_tiers and gate_name not in requested_tiers:
                gates_skipped_by_tier.append(gate_name)
                continue
            gates_run.append(gate_name)
            all_ok = True
            for cmd in checks:
                started = time.monotonic()
                try:
                    r = subprocess.run(
                        cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        cwd=str(workspace),
                        env=_verification_command_env(workspace),
                        timeout=self.DEFAULT_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as e:
                    all_ok = False
                    gate_checks.setdefault(gate_name, []).append({
                        **command_evidence(
                            command=cmd,
                            exit_code=None,
                            stdout=e.stdout or "",
                            stderr=e.stderr or "",
                            timed_out=True,
                        ),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    })
                    failure_details.setdefault(gate_name, []).append(
                        f"{cmd}: timed out after {self.DEFAULT_TIMEOUT_SECONDS}s"
                    )
                    continue
                except Exception as e:
                    all_ok = False
                    gate_checks.setdefault(gate_name, []).append({
                        **command_evidence(
                            command=cmd,
                            exit_code=None,
                            error=str(e),
                        ),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    })
                    failure_details.setdefault(gate_name, []).append(
                        f"{cmd}: {e}"
                    )
                    continue
                check_payload = command_evidence(
                    command=cmd,
                    exit_code=r.returncode,
                    stdout=r.stdout or "",
                    stderr=r.stderr or "",
                )
                check_payload["duration_ms"] = int(
                    (time.monotonic() - started) * 1000
                )
                gate_checks.setdefault(gate_name, []).append(check_payload)
                if r.returncode != 0:
                    all_ok = False
                    failure_details.setdefault(gate_name, []).append(cmd)
            (gates_passed if all_ok else gates_failed).append(gate_name)

        passed = len(gates_failed) == 0
        return DiscriminatorResult(
            d_name=self.name,
            passed=passed,
            evidence={
                "gates_run": gates_run,
                "gates_passed": gates_passed,
                "gates_failed": gates_failed,
                "gates_skipped_by_tier": gates_skipped_by_tier,
                "gates_skipped_by_profile": gates_skipped_by_profile,
                "failure_details": failure_details,
                "gate_checks": gate_checks,
            },
            reason="" if passed else f"failed gates: {gates_failed}",
        )


class ArchitectureRulesD(BaseDiscriminator):
    """Run ARCHITECTURE_RULES.md checks as terminal discriminators."""

    name = "ArchitectureRulesD"

    DEFAULT_TIMEOUT_SECONDS = 300

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        rules = parse_rules(workspace / "ARCHITECTURE_RULES.md")
        checks: list[dict[str, Any]] = []
        failed: list[str] = []
        for rule in rules:
            payload = _run_command_evidence(
                rule.check,
                cwd=workspace,
                timeout_seconds=self.DEFAULT_TIMEOUT_SECONDS,
            )
            payload["rule"] = rule.name
            payload["fix"] = rule.fix
            payload["why"] = rule.why
            checks.append(payload)
            if not payload["passed"]:
                failed.append(rule.name)
        return DiscriminatorResult(
            d_name=self.name,
            passed=not failed,
            evidence={
                "rules_file": str(workspace / "ARCHITECTURE_RULES.md"),
                "rules_run": [rule.name for rule in rules],
                "rules_failed": failed,
                "checks": checks,
            },
            reason="" if not failed else f"failed architecture rules: {failed}",
        )


class PromotedRulesD(BaseDiscriminator):
    """Run review-promoted rules from runtime state as terminal gates."""

    name = "PromotedRulesD"

    DEFAULT_TIMEOUT_SECONDS = 300

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        rules = PromotedRulesStore(event_log.path.parent / "promoted_rules.jsonl").list()
        checks: list[dict[str, Any]] = []
        failed: list[str] = []
        for rule in rules:
            payload = _run_command_evidence(
                rule.rule,
                cwd=workspace,
                timeout_seconds=self.DEFAULT_TIMEOUT_SECONDS,
            )
            payload["category"] = rule.category
            payload["fix_hint"] = rule.fix_hint
            checks.append(payload)
            if not payload["passed"]:
                failed.append(rule.category)
        return DiscriminatorResult(
            d_name=self.name,
            passed=not failed,
            evidence={
                "rules_file": str(event_log.path.parent / "promoted_rules.jsonl"),
                "rules_run": [rule.category for rule in rules],
                "rules_failed": failed,
                "checks": checks,
            },
            reason="" if not failed else f"failed promoted rules: {failed}",
        )


def _run_command_evidence(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=_verification_command_env(cwd),
            timeout=timeout_seconds,
        )
        payload = command_evidence(
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_evidence(
            command=command,
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
    except Exception as exc:
        payload = command_evidence(
            command=command,
            exit_code=None,
            error=str(exc),
        )
    payload["duration_ms"] = int((time.monotonic() - started) * 1000)
    return payload
