"""Bounded required-read pages with separately authorized content aliases."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from zf.runtime.artifact_read_snapshot import open_selection
from zf.runtime.call_result_envelope import canonical_json_sha256

PAGE_BYTES = 8192
PAGE_SCHEMA = "required-artifact-page.v2"


def prepare_required_read(
    state_dir: Path, *, manifest: dict[str, Any], rows: list[dict[str, Any]],
    identity: dict[str, Any], cursor: str | None = None, page_bytes: int = PAGE_BYTES,
    resume: bool = False, event_log: Any = None,
) -> tuple[str, list[dict[str, Any]]]:
    if not 1024 <= page_bytes <= PAGE_BYTES:
        raise ValueError("--page-bytes must be between 1024 and 8192")
    # Authorize every occurrence before exposing metadata or content. New
    # manifests carry immutable selection digests; legacy inputs retain full reads.
    previews = [open_selection(state_dir, manifest=manifest, row=row, identity=identity)
                for row in rows]
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    receipts: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    content_keys: dict[tuple[str, int, int], tuple[str, str, str]] = {}
    for row, preview in zip(rows, previews, strict=True):
        expected = row.get("artifact_sha256") or row.get("sha256")
        if expected and expected != preview.receipt["artifact_sha256"]:
            raise ValueError("required artifact digest differs from the pinned policy")
        source = {"source_id": row["source_id"], "artifact_id": row["artifact_id"],
                  "artifact_sha256": preview.receipt["artifact_sha256"],
                  "json_path": row.get("json_path", "$")}
        # The emitted contract is selected text. Only byte-identical selected
        # bodies may share it; authorization and receipts remain per occurrence.
        content_key = (preview.digest, preview.chars, preview.byte_count)
        # Retain the original group key when no new overlap is found, so
        # compatible in-flight bundle digests and cursors remain valid.
        key = content_keys.setdefault(content_key, (
            preview.receipt["artifact_sha256"], source["json_path"], preview.digest,
        ))
        if key in groups:
            groups[key].setdefault("aliases", []).append(source)
        else:
            groups[key] = {**source, "selection": preview}
        receipts.setdefault(key, []).append(preview.receipt)
    # Small bundles use the same identity envelope as continuation pages. A
    # fresh delivery id prevents an earlier read of identical text witnessing
    # this read, even when both belong to the same resumed provider session.
    delivery_id = str(uuid4())
    bundle_digest = canonical_json_sha256({
        "manifest": manifest, "requirements": rows, "selections": list(groups),
        "identity": identity,
    })
    if resume:
        from zf.cli.artifact_required_resume import resume_position

        index, start = resume_position(
            state_dir, manifest=manifest, identity=identity,
            bundle_digest=bundle_digest, groups=list(groups.values()),
            receipts=list(receipts.values()), event_log=event_log,
        )
    else:
        index, start = _position(cursor, bundle_digest, list(groups.values())) if groups or cursor else (0, 0)
    items: list[dict[str, Any]] = []
    emitted: list[dict[str, Any]] = []
    keys = list(groups)

    def render(extra: dict[str, Any] | None, position: tuple[int, int]) -> str:
        continuation = _cursor(bundle_digest, *position) if position[0] < len(groups) else None
        return json.dumps({
            "schema_version": PAGE_SCHEMA,
            "attempt_id": str(manifest.get("attempt_id") or ""),
            "delivery_id": delivery_id,
            "bundle_digest": bundle_digest,
            "items": [*items, *([extra] if extra is not None else [])],
            "next_cursor": continuation,
            "complete": continuation is None,
        }, ensure_ascii=False)

    # Fill a page with complete small items and at most one partial tail. This
    # avoids a provider round trip per tiny source without dropping any body.
    while index < len(keys):
        key = keys[index]
        group = groups[key]
        selection = group["selection"]
        digest = selection.digest
        public = {k: v for k, v in group.items() if k != "selection"}

        def item(end: int) -> dict[str, Any]:
            return {**public, "content": selection.read(start, end),
                    "page_start": start, "page_end": end, "page_total": selection.chars,
                    "selected_sha256": digest}

        def position(end: int) -> tuple[int, int]:
            return (index, end) if end < selection.chars else (index + 1, 0)

        end = min(selection.chars, start + page_bytes)
        if len(render(item(end), position(end)).encode("utf-8")) + 1 > page_bytes:
            low, high = start, end
            while low < high:
                middle = (low + high + 1) // 2
                if len(render(item(middle), position(middle)).encode("utf-8")) + 1 <= page_bytes:
                    low = middle
                else:
                    high = middle - 1
            end = low
            if end == start:
                if items:
                    break  # Preserve room for the continuation identity.
                raise ValueError("required page metadata exceeds output bound")
        items.append(item(end))
        for receipt in receipts[key]:
            emitted.append({
                **receipt, "delivery_id": delivery_id,
                "recovery_mode": selection.receipt["recovery_mode"],
                "operation_id": identity.get("operation_id", ""),
                "bundle_digest": bundle_digest,
                "returned_bytes": len(selection.read(start, end).encode("utf-8")),
                "truncated": start > 0 or end < selection.chars,
                "page_start": start, "page_end": end, "page_total": selection.chars,
                "selected_sha256": digest, "selected_bytes": selection.byte_count,
            })
        index, start = position(end)
        if start:
            break
    output = render(None, (index, start))
    if len(output.encode("utf-8")) + 1 > page_bytes:
        raise ValueError("required page metadata exceeds output bound")
    return output, emitted


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
        or offset >= max(1, groups[index]["selection"].chars)
    ):
        raise ValueError("required-read cursor is outside the selected bundle")
    return index, offset
