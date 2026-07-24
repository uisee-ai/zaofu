"""Canonical provider backend identities shared by config-producing surfaces."""

from __future__ import annotations


_CANONICAL_ALIASES = {
    "claude": "claude-code",
    "claude-code-headless": "claude-headless",
    "claude_headless": "claude-headless",
    "codex-app-server": "codex-headless",
    "codex_headless": "codex-headless",
}


def canonical_backend_id(value: object) -> str:
    """Return the runtime adapter id for a user- or catalog-facing backend."""

    backend = str(value or "").strip().lower()
    return _CANONICAL_ALIASES.get(backend, backend)


def catalog_backend_id(value: object) -> str:
    """Return the provider-family id used by the finite flow catalog."""

    backend = canonical_backend_id(value)
    if backend in {"claude-code", "claude-headless"}:
        return "claude"
    if backend in {"codex", "codex-headless"}:
        return "codex"
    return backend


__all__ = ["canonical_backend_id", "catalog_backend_id"]
