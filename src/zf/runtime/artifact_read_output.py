"""Invocation-local presentation of already authorized artifact reads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import _manifest_ref_path


class RequiredReadOutput:
    """Keep per-source receipts while rendering shared selected bodies once."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self._content_ids: dict[tuple[str, str, str, int, int, str], str] = {}

    def render(self, result: Mapping[str, Any]) -> dict[str, Any]:
        read = dict(result["read"])
        row = {
            "schema_version": "artifact-required-read-output.v1",
            "source_id": read["source_id"],
            "artifact_id": read["artifact_id"],
            "json_path": read["json_path"],
            # Match append_artifact_read's canonical JSON, excluding newline.
            "read_receipt": {
                "attempt_id": str(read.get("attempt_id") or ""),
                "occurrence_id": str(read.get("occurrence_id") or ""),
                "row_sha256": hashlib.sha256(json.dumps(
                    read, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
            },
            "content": result["content"],
        }
        digest = str(read.get("artifact_sha256") or "")
        if (
            read.get("status") != "read"
            or read.get("truncated")
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            return row
        # Every source has already passed the normal read path, including its
        # access scope, selector and on-disk digest. This is not a read cache.
        key = (
            str(_manifest_ref_path(self.state_dir, read["artifact_ref"]).resolve()),
            digest,
            read["json_path"],
            int(read.get("max_items") or 0),
            int(read.get("max_chars") or 0),
            hashlib.sha256(result["content"].encode("utf-8")).hexdigest(),
        )
        content_id = self._content_ids.get(key)
        if content_id is not None:
            row.pop("content")
            row["content_ref"] = content_id
        else:
            content_id = f"content-{len(self._content_ids) + 1}"
            self._content_ids[key] = content_id
            row["content_id"] = content_id
        return row
