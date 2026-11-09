"""Preserve bounded diagnostic sidecar descriptors without hydrating them."""
from __future__ import annotations

from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import SidecarRefError, safe_sidecar_ref


def valid_diagnostic_sidecar(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("ref_schema_version") != "sidecar-ref.v1":
        return False
    digest = value.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest.lower())
    ):
        return False
    try:
        safe_sidecar_ref(str(value.get("ref") or ""))
    except SidecarRefError:
        return False
    return True


def diagnostic_sidecar_refs(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: redact_obj(dict(value))
        for key in ("context_ref", "read_set_ref")
        if valid_diagnostic_sidecar(value := source.get(key))
    }
