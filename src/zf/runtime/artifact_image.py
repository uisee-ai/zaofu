"""Deliver authorized original PNG bytes to a visual tool when mounting is unavailable."""
from __future__ import annotations

import base64
from pathlib import Path

from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.artifact_input_index import binary_input
from zf.runtime.artifact_read_ledger import _find_source
from zf.runtime.artifact_source_versions import read_source_version
from zf.runtime.sidecar_refs import sidecar_path

# Multiple of three so that consecutive base64 fields concatenate without padding.
_PAGE_BYTES = 6000


def image_page(state: Path, manifest: dict, identity: dict, source_id: str,
               artifact_id: str, page: int) -> dict:
    source = _find_source(manifest, source_id=source_id, artifact_id=artifact_id)
    require_artifact_access(source.get("access_scope"), actor=identity["actor"],
                            role=identity["role"], purpose=identity["purpose"])
    if (page < 0 or "$" not in (source.get("allowed_paths") or ["$"])
            or not binary_input(source) or Path(source["ref"]).suffix.lower() != ".png"
            or not source.get("sha256")):
        raise ValueError("image pages require a whole, digest-pinned PNG input and nonnegative page")
    body, _ = read_source_version(state, source, path=sidecar_path(state, source["ref"]))
    start = page * _PAGE_BYTES
    if not body.startswith(b"\x89PNG\r\n\x1a\n") or start >= len(body):
        raise ValueError("invalid PNG signature or image page outside the pinned input")
    end = min(start + _PAGE_BYTES, len(body))
    return {"mime_type": "image/png", "sha256": source["sha256"], "byte_count": len(body),
            "page": page, "next_page": page + 1 if end < len(body) else None,
            "data_base64": base64.b64encode(body[start:end]).decode("ascii")}
