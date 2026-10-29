"""Bounded required-read delivery with separately authorized content aliases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.runtime.artifact_read_ledger import append_artifact_read, read_attempt_artifact


def required_read_output(
    state_dir: Path, *, manifest: dict[str, Any], rows: list[dict[str, Any]],
    identity: dict[str, Any],
) -> str:
    previews = [read_attempt_artifact(
        state_dir, manifest=manifest,
        source_id=row["source_id"], artifact_id=row["artifact_id"],
        json_path=row.get("json_path", "$"), record=False,
        actor=identity["actor"], role=identity["role"],
        provider=identity["provider"], purpose=identity["purpose"],
    ) for row in rows]
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row, preview in zip(rows, previews, strict=True):
        source = {"source_id": row["source_id"], "artifact_id": row["artifact_id"],
                  "json_path": row.get("json_path", "$")}
        key = (preview["read"]["artifact_sha256"], source["json_path"], preview["content"])
        if key in groups:
            groups[key].setdefault("aliases", []).append(source)
        else:
            groups[key] = {**source, "content": preview["content"]}
    output = "\n".join(json.dumps(row, ensure_ascii=False) for row in groups.values())
    if len(output) > 8192:
        raise ValueError("required output exceeds 8192 characters; read each required source with bounded pages")
    # Every alias was independently authenticated and digest/slice checked above.
    # Keep separate source receipts; byte-identical delivery never waives a read.
    for preview in previews:
        append_artifact_read(state_dir, preview["read"])
    return output
