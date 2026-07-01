"""Read-only provider/backend capability projection."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from zf.runtime.backend import BackendCapabilities, get_adapter


ADAPTER_BACKENDS = ("claude-code", "codex", "mock", "python")
HEADLESS_BACKENDS = ("deterministic", "claude-headless", "codex-headless")


def project_provider_capabilities(
    *,
    config: Any | None = None,
    operator_backends: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project static provider capabilities plus runtime availability hints."""

    now = now or datetime.now(timezone.utc)
    operator_index = {
        str(item.get("id") or ""): item
        for item in operator_backends or []
        if isinstance(item, dict) and item.get("id")
    }
    role_index = _roles_by_backend(config)
    providers = [
        _with_runtime_hints(
            provider_capability_for_backend(backend),
            operator_index.get(backend),
            role_index.get(backend, []),
        )
        for backend in (*HEADLESS_BACKENDS, *ADAPTER_BACKENDS)
    ]
    providers.sort(key=lambda row: (
        _surface_order(str(row.get("surface") or "")),
        str(row.get("backend") or ""),
    ))
    return {
        "schema_version": "provider-capabilities.v1",
        "generated_at": now.isoformat(),
        "summary": {
            "providers": len(providers),
            "native_resume": sum(1 for row in providers if row.get("native_resume")),
            "streaming": sum(1 for row in providers if row.get("streaming")),
            "interrupt": sum(1 for row in providers if row.get("interrupt")),
            "cost": sum(1 for row in providers if row.get("cost")),
            "test_mode": sum(1 for row in providers if row.get("test_mode")),
        },
        "providers": providers,
    }


def provider_capability_for_backend(backend: str) -> dict[str, Any]:
    backend = _canonical_backend(backend)
    if backend in ADAPTER_BACKENDS:
        caps = get_adapter(backend).capabilities
        return _adapter_capability(backend, caps)
    if backend == "deterministic":
        return _static_capability(
            backend,
            label="deterministic",
            family="deterministic",
            surface="builtin",
            source="builtin",
            test_mode=True,
            available=True,
        )
    if backend in {"claude-headless", "codex-headless"}:
        family = "claude" if backend.startswith("claude") else "codex"
        return _static_capability(
            backend,
            label="Claude headless" if family == "claude" else "Codex headless",
            family=family,
            surface="headless",
            source="headless",
            native_resume=True,
            streaming=True,
            stream_json=True,
            cancel=True,
            interrupt=True,
            tools=True,
            cost=True,
            context_usage=True,
            confidence="configured_provider",
        )
    return _static_capability(
        backend or "unknown",
        label=backend or "unknown",
        family="unknown",
        surface="unknown",
        source="unknown",
        confidence="unknown",
    )


def _adapter_capability(backend: str, caps: BackendCapabilities) -> dict[str, Any]:
    family = "claude" if backend == "claude-code" else "codex" if backend == "codex" else "mock"
    test_mode = backend in {"mock", "python"}
    return {
        **_static_capability(
            backend,
            label={
                "claude-code": "Claude Code",
                "codex": "Codex",
                "mock": "Mock",
                "python": "Python/mock",
            }.get(backend, backend),
            family=family,
            surface="mock" if test_mode else "terminal",
            source="adapter",
            native_resume=caps.native_resume,
            streaming=caps.stream_json,
            stream_json=caps.stream_json,
            tools=not test_mode,
            cost=bool(caps.context_usage_reader and not test_mode),
            context_usage=caps.context_usage_reader,
            test_mode=test_mode,
            confidence="adapter_static",
        ),
        "per_turn_hook": caps.per_turn_hook,
        "session_start_hook": caps.session_start_hook,
        "hook_review_required": caps.hook_review_required,
        "nested_agent_disable": caps.nested_agent_disable,
        "adapter_capabilities": asdict(caps),
    }


def _static_capability(
    backend: str,
    *,
    label: str,
    family: str,
    surface: str,
    source: str,
    native_resume: bool = False,
    streaming: bool = False,
    stream_json: bool = False,
    cancel: bool = False,
    interrupt: bool = False,
    tools: bool = False,
    cost: bool = False,
    context_usage: bool = False,
    test_mode: bool = False,
    available: bool | None = None,
    confidence: str = "conservative",
) -> dict[str, Any]:
    return {
        "backend": backend,
        "provider": backend,
        "label": label,
        "family": family,
        "surface": surface,
        "source": source,
        "available": available,
        "availability": (
            "available" if available is True
            else "unavailable" if available is False
            else "not_checked"
        ),
        "native_resume": native_resume,
        "resume": native_resume,
        "streaming": streaming,
        "stream_json": stream_json,
        "cancel": cancel,
        "interrupt": interrupt,
        "tools": tools,
        "cost": cost,
        "context_usage": context_usage,
        "context": "provider_usage" if context_usage else "unknown",
        "workdir": "project",
        "test_mode": test_mode,
        "confidence": confidence,
        "roles": [],
    }


def _with_runtime_hints(
    row: dict[str, Any],
    operator_backend: dict[str, Any] | None,
    roles: list[str],
) -> dict[str, Any]:
    out = dict(row)
    if operator_backend is not None:
        available = bool(operator_backend.get("available"))
        out["available"] = available
        out["availability"] = "available" if available else "missing_command"
        out["default"] = bool(operator_backend.get("default"))
        out["source"] = str(operator_backend.get("source") or out.get("source") or "")
    out["roles"] = roles
    out["role_count"] = len(roles)
    return out


def _roles_by_backend(config: Any | None) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for role in getattr(config, "roles", []) or []:
        backend = _canonical_backend(getattr(role, "backend", ""))
        if not backend:
            continue
        roles.setdefault(backend, []).append(str(getattr(role, "instance_id", "") or getattr(role, "name", "")))
    for values in roles.values():
        values.sort()
    return roles


def _canonical_backend(backend: str) -> str:
    value = str(backend or "").strip()
    return {
        "claude": "claude-code",
        "claude-code-headless": "claude-headless",
        "claude_headless": "claude-headless",
        "codex-app-server": "codex-headless",
        "codex_headless": "codex-headless",
    }.get(value, value)


def _surface_order(surface: str) -> int:
    return {
        "headless": 0,
        "terminal": 1,
        "builtin": 2,
        "mock": 3,
    }.get(surface, 9)


__all__ = [
    "project_provider_capabilities",
    "provider_capability_for_backend",
]
