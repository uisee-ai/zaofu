"""Digest-pinned full source bodies for recovery, never summary substitutes."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, sidecar_path


def pin_source_versions(state_dir: Path, sources: list[dict[str, Any]]) -> None:
    for source in sources:
        raw = sidecar_path(state_dir, source["ref"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("source changed before version pinning")
        source["recovery_ref"] = write_immutable_json_sidecar(
            state_dir, {"schema_version": "artifact-source-version.v1",
                        "sha256": source["sha256"], "body_base64": base64.b64encode(raw).decode()},
            root="source-versions", kind="artifact_source_version",
            schema_version="artifact-source-version.v1", created_by="artifact-read",
        )


def read_source_version(state_dir: Path, source: dict[str, Any], *, path: Path) -> tuple[bytes, str]:
    """Prefer the full authoritative ref; recover only the exact pinned version.

    A broken recovery copy cannot prevent a valid original read. A changed
    original cannot silently become the content of an older attempt.
    """
    expected = source.get("sha256")
    try:
        raw = path.read_bytes()
        if not expected or hashlib.sha256(raw).hexdigest() == expected:
            return raw, "authoritative_ref"
    except OSError:
        pass
    descriptor = source.get("recovery_ref")
    if isinstance(descriptor, dict):
        payload = hydrate_sidecar_ref(state_dir, descriptor).payload
        raw = base64.b64decode(payload["body_base64"], validate=True)
        if payload.get("sha256") == expected and hashlib.sha256(raw).hexdigest() == expected:
            return raw, "pinned_full_version"
    raise ValueError(f"artifact ref missing or artifact hash mismatch: {source['ref']}")
