"""Pre-flight dispatch-readiness checks (doc 78 W4).

Run BEFORE a real harness launch to catch the bug classes that silently brick
an entire long-horizon run — the kind that cost ~45min/round to discover live:

- **dispatch-prompt signature drift** (0f7d623): a dispatch site passed
  ``prompt_kind="fanout_child"`` that ``build_task_prompt`` did not accept →
  TypeError at every fanout dispatch → scan never produced a task_map and the
  whole run stalled at 0 dispatched. A pre-launch call reproduces the TypeError
  in milliseconds.
- **broken dispatch-chain imports**: an import error anywhere in the dispatch
  chain bricks every dispatch.
- **unknown role backend**: a typo'd backend fails only when the role is first
  spawned, mid-run.

Pure checks (config in → results out) so they stay unit-testable; the CLI
wrapper (`zf preflight`) is thin. This is a STATIC readiness preflight; a full
mock-backend pipeline e2e (dispatch→aggregate→transition) is a future extension.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from zf.core.config.backend_identity import canonical_backend_id

# prompt_kinds actually passed by dispatch sites (orchestrator.py 2834/3329/4272
# + normal dispatch). The signature must accept all of them.
_DISPATCH_PROMPT_KINDS = ("task", "fanout_child", "fanout_synth")

_DISPATCH_CHAIN_MODULES = (
    "zf.runtime.injection",
    "zf.runtime.orchestrator",
    "zf.runtime.candidate_rework",
    "zf.runtime.writer_fanout_admission",
    "zf.runtime.rework_triage",
    "zf.runtime.wake_patterns",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def run_preflight_checks(
    config,
    *,
    check_provider_auth: bool = False,
) -> list[CheckResult]:
    """Run dispatch checks and, when requested, local provider readiness."""

    results = [
        _check_dispatch_prompt_signature(),
        _check_dispatch_chain_imports(),
        _check_role_backends(config),
    ]
    if check_provider_auth:
        results.append(_check_provider_auth_readiness(config))
    return results


def preflight_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)


def _check_dispatch_prompt_signature() -> CheckResult:
    name = "dispatch_prompt_signature"
    try:
        from zf.runtime.injection import build_task_prompt
    except Exception as exc:  # import failure surfaces as its own line below
        return CheckResult(name, False, f"cannot import build_task_prompt: {exc}")
    probe = Path("/tmp/zf-preflight/role/briefings/probe.md")
    for kind in _DISPATCH_PROMPT_KINDS:
        try:
            out = build_task_prompt("preflight-role", probe, prompt_kind=kind)
        except TypeError as exc:
            return CheckResult(name, False, f"signature drift on prompt_kind={kind!r}: {exc}")
        except Exception as exc:
            return CheckResult(name, False, f"{type(exc).__name__} on prompt_kind={kind!r}: {exc}")
        if not isinstance(out, str) or not out.strip():
            return CheckResult(name, False, f"empty prompt for prompt_kind={kind!r}")
    return CheckResult(name, True, "build_task_prompt accepts " + "/".join(_DISPATCH_PROMPT_KINDS))


def _check_dispatch_chain_imports() -> CheckResult:
    name = "dispatch_chain_imports"
    import importlib

    broken: list[str] = []
    for module in _DISPATCH_CHAIN_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            broken.append(f"{module}: {type(exc).__name__}: {exc}")
    if broken:
        return CheckResult(name, False, "; ".join(broken))
    return CheckResult(name, True, f"{len(_DISPATCH_CHAIN_MODULES)} dispatch modules import cleanly")


def _check_role_backends(config) -> CheckResult:
    name = "role_backends_known"
    try:
        from zf.runtime.backend import get_adapter
    except Exception as exc:
        return CheckResult(name, False, f"cannot import backend adapter registry: {exc}")
    bad: set[str] = set()
    roles = list(getattr(config, "roles", []) or [])
    for role in roles:
        candidates = [getattr(role, "backend", "") or "", *(getattr(role, "backends", []) or [])]
        for backend in candidates:
            backend = canonical_backend_id(backend)
            if not backend:
                continue
            try:
                get_adapter(backend)
            except Exception:
                bad.add(f"{getattr(role, 'name', '?')}:{backend}")
    if bad:
        return CheckResult(name, False, "unknown backend(s): " + ", ".join(sorted(bad)))
    return CheckResult(name, True, f"all backends resolvable across {len(roles)} roles")


def _check_provider_auth_readiness(config) -> CheckResult:
    """Probe local CLI authentication without starting a model turn."""

    name = "provider_auth_readiness"
    backends = sorted(_configured_provider_backends(config))
    real_backends = [
        backend for backend in backends
        if backend not in {"", "mock", "python", "deterministic"}
    ]
    if not real_backends:
        return CheckResult(name, True, "no real provider backend configured")

    failures: list[str] = []
    ready: list[str] = []
    for backend in real_backends:
        ok, detail = _probe_provider_auth(backend)
        if ok:
            ready.append(backend)
        else:
            failures.append(f"{backend}: {detail}")
    if failures:
        return CheckResult(name, False, "; ".join(failures))
    return CheckResult(name, True, "ready: " + ", ".join(ready))


def _configured_provider_backends(config) -> set[str]:
    backends: set[str] = set()

    def add(value: object) -> None:
        if value:
            backends.add(canonical_backend_id(value))

    for role in list(getattr(config, "roles", []) or []):
        values = [
            getattr(role, "backend", "") or "",
            *(getattr(role, "backends", []) or []),
        ]
        for value in values:
            add(value)

    runtime = getattr(config, "runtime", None)
    run_manager = getattr(runtime, "run_manager", None)
    add(getattr(run_manager, "backend", "") if run_manager is not None else "")
    reflect = getattr(run_manager, "reflect", None)
    add(getattr(reflect, "backend", "") if reflect is not None else "")
    source_repair = getattr(run_manager, "source_repair", None)
    add(getattr(source_repair, "backend", "") if source_repair is not None else "")

    resident = getattr(runtime, "autoresearch_resident", None)
    add(getattr(resident, "self_repair_backend", "") if resident is not None else "")
    autoresearch = getattr(config, "autoresearch", None)
    trigger_policy = getattr(autoresearch, "trigger_policy", None)
    add(
        getattr(trigger_policy, "self_repair_backend", "")
        if trigger_policy is not None else ""
    )
    return backends


def _probe_provider_auth(backend: str) -> tuple[bool, str]:
    backend = canonical_backend_id(backend)
    probe_backend = {
        "claude-headless": "claude-code",
        "codex-headless": "codex",
    }.get(backend, backend)
    commands = {
        "claude-code": (["claude", "auth", "status"], "`claude /login`"),
        "codex": (["codex", "login", "status"], "`codex login`"),
    }
    command_and_hint = commands.get(probe_backend)
    if command_and_hint is None:
        return False, "no bounded auth probe is registered"
    command, login_hint = command_and_hint
    if shutil.which(command[0]) is None:
        return False, f"command missing: {command[0]!r}"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"auth status timed out; authenticate with {login_hint}"
    except OSError as exc:
        return False, f"auth status failed: {exc}"

    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    if probe_backend == "claude-code":
        logged_in = False
        if result.stdout.strip():
            try:
                payload = json.loads(result.stdout)
            except (TypeError, ValueError):
                payload = {}
            logged_in = bool(payload.get("loggedIn"))
        if result.returncode == 0 and logged_in:
            return True, "authenticated"
    elif result.returncode == 0 and "logged in" in output.lower():
        return True, "authenticated"
    detail = output.splitlines()[0] if output else "not authenticated"
    return False, f"{detail}; authenticate with {login_hint}"
