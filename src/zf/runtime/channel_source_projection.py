"""Lossless prompt views and audit references for checkpointed synthesis."""
from __future__ import annotations

import hashlib
import json
from typing import Any

PIPELINE_VERSION = "checkpointed.v1"


def checkpointed_synthesis(refs: dict[str, Any]) -> bool:
    return bool(refs.get("synthesis_request_id") and refs.get("synthesis_pipeline") == PIPELINE_VERSION)


def intern_source_contents(pack: dict[str, Any]) -> dict[str, Any]:
    """Retain every source identity, sending each identical body only once."""
    documents = pack.get("semantic_source_documents")
    if not isinstance(documents, list):
        return pack
    bodies: dict[str, Any] = {}
    projected = []
    for row in documents:
        if not isinstance(row, dict) or "content" not in row:
            projected.append(row)
            continue
        content = row["content"]
        raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        bodies[digest] = content
        projected.append({**{key: value for key, value in row.items() if key != "content"}, "content_ref": digest})
    return {**pack, "semantic_source_documents": projected, "semantic_source_contents": bodies,
            "semantic_source_encoding": "Each semantic_source_document.content_ref resolves to its COMPLETE body in semantic_source_contents. Every original source identity and digest is retained; identical bodies are read once and apply to all matching identities."}
