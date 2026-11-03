"""Read a sealed prior-review ledger without hydrating the entire sidecar."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger_validation import _parse_ledger_lines
from zf.runtime.sidecar_refs import normalize_sidecar_ref_descriptor, sidecar_path

# A receipt contains metadata and sidecar references, never the artifact body.
# Bound a malformed individual record while allowing any number of records.
_MAX_RECEIPT_BYTES = 1024 * 1024


def prior_observed_rows(state_dir: Path, descriptor: Mapping[str, Any], *,
                        attempt_id: str, session_id: str, actor: str) -> list[dict[str, Any]]:
    ref = normalize_sidecar_ref_descriptor(dict(descriptor))
    if (ref.get("kind") != "artifact_read_ledger" or not ref.get("sha256")
            or not isinstance(ref.get("byte_count"), int) or ref["byte_count"] <= 0):
        raise ValueError("invalid prior ledger descriptor")
    path = sidecar_path(state_dir, ref["ref"])
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while line := stream.readline(_MAX_RECEIPT_BYTES + 1):
            if len(line) > _MAX_RECEIPT_BYTES:
                raise ValueError("prior ledger receipt is too large")
            size += len(line)
            if size > ref["byte_count"]:
                raise ValueError("prior ledger length differs")
            digest.update(line)
            parsed = _parse_ledger_lines([line.decode("utf-8")], expected_attempt_id=attempt_id)
            for row in parsed:
                if (row.get("provider_delivery_status") == "observed_in_transcript"
                        and row.get("provider_session_id") == session_id
                        and row.get("consumer_provider") == "codex"
                        and row.get("consumer_actor") == actor):
                    rows.append(row)
    if size != ref["byte_count"] or digest.hexdigest() != ref["sha256"]:
        raise ValueError("prior ledger digest or length differs")
    return rows
