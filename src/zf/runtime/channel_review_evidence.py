"""Preserve Channel review bodies without changing the string-ref event contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def review_evidence_refs(value: object) -> list[str]:
    """Accept legacy strings and explicit ref objects; never silently drop evidence."""
    if not isinstance(value, list):
        raise ValueError("review evidence_refs must be a list")
    refs = []
    for item in value:
        ref = item.get("ref") if isinstance(item, dict) else item
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("each review evidence_refs item requires a non-empty string ref")
        refs.append(ref.strip())
    return list(dict.fromkeys(refs))


def preserve_review(
    state_dir: Path, *, review: dict[str, Any], binding: dict[str, Any],
    actor: str, reply_event_id: str,
) -> dict[str, str]:
    """Persist complete semantic evidence; refs on events remain navigation only."""
    descriptor = write_immutable_json_sidecar(
        state_dir, redact_obj({"binding": binding, "review": review}),
        root="channel-reviews", kind="channel_consensus_review",
        schema_version="channel-consensus-review.v1", created_by=actor,
        source_event_id=reply_event_id,
    )
    return {"review_ref": descriptor["ref"], "review_digest": descriptor["sha256"]}
