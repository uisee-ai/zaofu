"""Hash-bound method bodies for self-contained Channel context packs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import SidecarRefError


def load_channel_skill_guidance(
    refs: list[dict[str, Any]], *, state_dir: Path,
    project_root: Path | None, max_bytes: int = 98304,
) -> list[dict[str, str]]:
    roots = [(state_dir / "runtime-skills").resolve()]
    if project_root is not None:
        roots.append((project_root / "skills").resolve())
    result = []
    remaining = max_bytes
    for item in refs:
        logical = str(item.get("logical_ref") or "")
        path = Path(str(item.get("resolved_path") or "")).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise SidecarRefError("ref_invalid", "channel skill is outside its materialized roots", ref=logical)
        try:
            with path.open("rb") as file:
                raw = file.read(remaining + 1)
        except OSError as exc:
            raise SidecarRefError("ref_missing", "channel skill body is unavailable", ref=logical) from exc
        if len(raw) > remaining:
            raise SidecarRefError("budget_exceeded", "channel skill guidance exceeds the context budget", ref=logical)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(item.get("sha256") or ""):
            raise SidecarRefError("hash_mismatch", "channel skill body differs from its member binding", ref=logical)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SidecarRefError("schema_unsupported", "channel skill body must be UTF-8", ref=logical) from exc
        result.append({"logical_ref": logical, "sha256": digest, "content": content})
        remaining -= len(raw)
    return result
