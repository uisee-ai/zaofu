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

from dataclasses import dataclass
from pathlib import Path

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


def run_preflight_checks(config) -> list[CheckResult]:
    """Run all dispatch-readiness checks against a loaded config."""
    return [
        _check_dispatch_prompt_signature(),
        _check_dispatch_chain_imports(),
        _check_role_backends(config),
    ]


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
            backend = str(backend or "").strip()
            if not backend:
                continue
            try:
                get_adapter(backend)
            except Exception:
                bad.add(f"{getattr(role, 'name', '?')}:{backend}")
    if bad:
        return CheckResult(name, False, "unknown backend(s): " + ", ".join(sorted(bad)))
    return CheckResult(name, True, f"all backends resolvable across {len(roles)} roles")
