"""Bounded required-read pages with separately authorized content aliases."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.artifact_read_ledger import append_artifact_read, read_attempt_artifact
from zf.runtime.call_result_envelope import canonical_json_sha256

PAGE_CHARS = 8192
PAGE_SCHEMA = "required-artifact-page.v1"


def required_read_output(
    state_dir: Path, *, manifest: dict[str, Any], rows: list[dict[str, Any]],
    identity: dict[str, Any], cursor: str | None = None,
) -> str:
    # Authenticate every alias and validate every digest before returning any
    # content or recording consumption, including on continuation pages.
    previews = [read_attempt_artifact(
        state_dir, manifest=manifest,
        source_id=row["source_id"], artifact_id=row["artifact_id"],
        json_path=row.get("json_path", "$"), record=False,
        actor=identity["actor"], role=identity["role"],
        provider=identity["provider"], purpose=identity["purpose"],
    ) for row in rows]
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    receipts: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row, preview in zip(rows, previews, strict=True):
        source = {"source_id": row["source_id"], "artifact_id": row["artifact_id"],
                  "json_path": row.get("json_path", "$")}
        key = (preview["read"]["artifact_sha256"], source["json_path"], preview["content"])
        if key in groups:
            groups[key].setdefault("aliases", []).append(source)
        else:
            groups[key] = {**source, "content": preview["content"]}
        receipts.setdefault(key, []).append(preview["read"])
    output = "\n".join(json.dumps(row, ensure_ascii=False) for row in groups.values())
    if cursor is None and len(output) <= PAGE_CHARS:
        # Keep the small-bundle CLI format compatible with existing consumers.
        for preview in previews:
            append_artifact_read(state_dir, preview["read"])
        return output
    bundle_digest = canonical_json_sha256({
        "manifest": manifest, "requirements": rows, "groups": list(groups.values()),
    })
    index, start = _position(cursor, bundle_digest, list(groups.values()))
    key = list(groups)[index]
    group = groups[key]
    content = group["content"]
    selected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    def render(end: int) -> str:
        position = (index, end) if end < len(content) else (index + 1, 0)
        continuation = (
            _cursor(bundle_digest, *position) if position[0] < len(groups) else None
        )
        return json.dumps({
            "schema_version": PAGE_SCHEMA,
            "attempt_id": str(manifest.get("attempt_id") or ""),
            "bundle_digest": bundle_digest,
            "item": {**group, "content": content[start:end],
                     "page_start": start, "page_end": end, "page_total": len(content),
                     "selected_sha256": selected_sha256},
            "next_cursor": continuation,
            "complete": continuation is None,
        }, ensure_ascii=False)

    # Bound serialized output, including escaped text, aliases and the cursor.
    low, high = start, min(len(content), start + PAGE_CHARS)
    while low < high:
        middle = (low + high + 1) // 2
        if len(render(middle)) <= PAGE_CHARS:
            low = middle
        else:
            high = middle - 1
    end = low
    output = render(end)
    if len(output) > PAGE_CHARS or (end == start and content):
        raise ValueError("required page metadata exceeds output bound")
    for receipt in receipts[key]:
        append_artifact_read(state_dir, {
            **receipt,
            "returned_bytes": len(content[start:end].encode("utf-8")),
            "truncated": start > 0 or end < len(content),
            "page_start": start, "page_end": end, "page_total": len(content),
            "selected_sha256": selected_sha256,
            "selected_bytes": len(content.encode("utf-8")),
        })
    return output


def _cursor(bundle_digest: str, index: int, offset: int) -> str:
    value = json.dumps([bundle_digest, index, offset], separators=(",", ":"))
    return base64.urlsafe_b64encode(value.encode()).decode()


def _position(
    cursor: str | None, bundle_digest: str, groups: list[dict[str, Any]],
) -> tuple[int, int]:
    index, offset = 0, 0
    if cursor is not None:
        try:
            if len(cursor) > 512:
                raise ValueError("oversized cursor")
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(value, list) or len(value) != 3 or value[0] != bundle_digest:
                raise ValueError("stale cursor")
            index, offset = value[1:]
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid or stale required-read cursor") from exc
    if (
        type(index) is not int or type(offset) is not int
        or not 0 <= index < len(groups) or offset < 0
        or offset >= max(1, len(groups[index]["content"]))
    ):
        raise ValueError("required-read cursor is outside the selected bundle")
    return index, offset
