"""Bounded required-read delivery with separately authorized content aliases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.artifact_read_ledger import append_artifact_read, read_attempt_artifact


def required_read_output(
    state_dir: Path, *, manifest: dict[str, Any], rows: list[dict[str, Any]],
    identity: dict[str, Any], cursor: str = "", page_size: int = 8192,
) -> str:
    if not 512 <= page_size <= 8192:
        raise ValueError("required page size must be 512..8192 characters")
    previews = [read_attempt_artifact(
        state_dir, manifest=manifest,
        source_id=row["source_id"], artifact_id=row["artifact_id"],
        json_path=row.get("json_path", "$"), record=False,
        actor=identity["actor"], role=identity["role"],
        provider=identity["provider"], purpose=identity["purpose"],
    ) for row in rows]
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row, preview in zip(rows, previews, strict=True):
        expected = row.get("artifact_sha256") or row.get("sha256")
        if expected and expected != preview["read"]["artifact_sha256"]:
            raise ValueError("required artifact digest differs from the pinned policy")
        source = {"source_id": row["source_id"], "artifact_id": row["artifact_id"],
                  "json_path": row.get("json_path", "$")}
        key = (preview["read"]["artifact_sha256"], source["json_path"], preview["content"])
        if key in groups:
            groups[key]["delivery"].setdefault("aliases", []).append(source)
            groups[key]["reads"].append(preview["read"])
        else:
            groups[key] = {"delivery": {**source, "content": preview["content"]},
                           "reads": [preview["read"]]}
    batches = list(groups.values())
    output = "\n".join(json.dumps(group["delivery"], ensure_ascii=False) for group in batches)
    if not cursor and len(output) + 1 <= page_size:
        for preview in previews:
            append_artifact_read(state_dir, preview["read"])
        return output
    # Bind continuation to this attempt, policy, order and immutable content.
    binding = hashlib.sha256(json.dumps(
        [manifest, rows, output], sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    index, start = 0, 0
    if cursor:
        try:
            digest, index_text, start_text = cursor.split(":")
            index, start = int(index_text), int(start_text)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid required-read cursor") from exc
        if digest != binding or not 0 <= index < len(batches) or start < 0:
            raise ValueError("stale or invalid required-read cursor")
    items, receipts = [], []
    next_cursor = ""
    while index < len(batches):
        group = batches[index]
        delivery = group["delivery"]
        content = delivery["content"]
        if start and start >= len(content):
            raise ValueError("required-read cursor is outside the selected content")

        def candidate(end: int) -> tuple[dict[str, Any], str, str]:
            item = {**delivery, "content": content[start:end],
                    "page_start": start, "page_end": end, "page_total": len(content)}
            following = (f"{binding}:{index}:{end}" if end < len(content) else
                         f"{binding}:{index + 1}:0" if index + 1 < len(batches) else "")
            return item, following, _page([*items, item], following)

        end = len(content)
        item, following, rendered = candidate(end)
        if len(rendered) + 1 > page_size:
            low, high = start, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                if len(candidate(middle)[2]) + 1 <= page_size:
                    low = middle
                else:
                    high = middle - 1
            end = low
            if end == start:
                if not items:
                    raise ValueError("required source metadata exceeds page size")
                next_cursor = f"{binding}:{index}:{start}"
                break
            item, following, rendered = candidate(end)
        items.append(item)
        for read in group["reads"]:
            receipts.append({**read, "returned_bytes": len(content[start:end].encode()),
                             "truncated": start > 0 or end < len(content),
                             "page_start": start, "page_end": end, "page_total": len(content),
                             "selected_sha256": hashlib.sha256(content.encode()).hexdigest(),
                             "selected_bytes": len(content.encode())})
        next_cursor = following
        if end < len(content):
            break
        index, start = index + 1, 0
    output = _page(items, next_cursor)
    if len(output) + 1 > page_size:
        raise ValueError("required source metadata exceeds page size")
    # Only delivered ranges count; each independently authorized alias gets a receipt.
    for receipt in receipts:
        append_artifact_read(state_dir, receipt)
    return output


def _page(items: list[dict[str, Any]], cursor: str) -> str:
    return json.dumps({"schema_version": "required-read-page.v1", "items": items,
                       "next_cursor": cursor or None}, ensure_ascii=False, separators=(",", ":"))
