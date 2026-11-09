"""Rebuildable publication navigation, stored with the existing catalog cursor."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_navigation_shards import SHARD_SCHEMA as NAVIGATION_SCHEMA, publish_navigation, query_navigation
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

# Do not index provider transcripts, diagnostic reasoning, or arbitrary logs.
NAVIGABLE_KINDS = frozenset({
    "task_map", "task_contract", "acceptance_matrix", "test_matrix", "verification_result",
    "implementation_result", "research_report", "plan_artifact_package", "goal_claim_set",
    "prd", "execution_spec", "task_execution_input", "task_scoped_plan_input", "source_document",
})


def _key(descriptor: Mapping[str, Any]) -> str:
    return str(descriptor.get("sha256") or "")


def _build(state_dir: Path, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    try:
        # Catalog construction is bounded; larger bodies stay precisely readable
        # through their attempt selections, never silently called fully indexed.
        from zf.runtime.artifact_read_ledger import _encode_selected

        hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
        nav = publish_navigation(state_dir, _encode_selected(hydrated.payload))
        return {**nav, "source_sha256": _key(descriptor), "status": "ready"}
    except (OSError, ValueError, KeyError):
        return {"schema_version": NAVIGATION_SCHEMA, "source_sha256": _key(descriptor),
                "status": "unavailable", "complete": False, "entries": [],
                "reason": "source_missing_invalid_or_over_preview_budget"}


def project_descriptor_navigation(
    conn: sqlite3.Connection, state_dir: Path, descriptor: Mapping[str, Any], source_seq: int,
) -> None:
    """Called inside the catalog's event transaction, before cursor publication."""
    if (not _key(descriptor) or not ({str(descriptor.get("semantic_kind")),
            str(descriptor.get("kind"))} & NAVIGABLE_KINDS)):
        return
    prior = conn.execute("SELECT payload_json FROM artifact_reducer_projection WHERE projection_kind=? AND subject_id=? AND source_snapshot_key=?",
                         ("artifact-navigation", _key(descriptor), NAVIGATION_SCHEMA)).fetchone()
    if prior:
        try:
            cached = json.loads(prior[0])
            if cached.get("status") == "ready" and cached.get("source_sha256") == _key(descriptor):
                return
        except (ValueError, AttributeError):
            pass
    payload = _build(state_dir, descriptor)
    from zf.runtime.artifact_query.store import MAX_REDUCER_PROJECTIONS

    conn.execute("""INSERT OR REPLACE INTO artifact_reducer_projection
        (projection_kind, subject_id, source_snapshot_key, source_seq, reducer_version, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""", ("artifact-navigation", _key(descriptor), NAVIGATION_SCHEMA,
        source_seq, NAVIGATION_SCHEMA, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    conn.execute("""DELETE FROM artifact_reducer_projection WHERE rowid IN (
        SELECT rowid FROM artifact_reducer_projection ORDER BY updated_at DESC, rowid DESC
        LIMIT -1 OFFSET ?)""", (MAX_REDUCER_PROJECTIONS,))


def published_navigation(state_dir: Path, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Caller must authorize the occurrence first. Cached data never grants a read."""
    from zf.runtime.artifact_query.store import get_reducer_projection, set_reducer_projection
    cached = get_reducer_projection(state_dir, projection_kind="artifact-navigation",
        subject_id=_key(descriptor), source_snapshot_key=NAVIGATION_SCHEMA)
    if (cached and cached.get("schema_version") == NAVIGATION_SCHEMA
            and cached.get("source_sha256") == _key(descriptor) and cached.get("status") == "ready"):
        return cached
    payload = _build(state_dir, descriptor)
    set_reducer_projection(state_dir, projection_kind="artifact-navigation", subject_id=_key(descriptor),
        source_snapshot_key=NAVIGATION_SCHEMA, source_seq=0, reducer_version=NAVIGATION_SCHEMA, payload=payload)
    return payload


class ArtifactNavigationMixin:
    def artifact_navigation(self, identity: str, *, context, query: str = "", cursor: str | None = None):
        """Discover published navigation; current worker reads still require a manifest."""
        import base64
        from zf.runtime.call_result_envelope import canonical_json_sha256
        if len(query) > 256:
            raise ValueError("navigation query exceeds 256 characters")
        catalog = self.catalog_show(identity, context=context)
        item = catalog.get("item") or {}
        if not item.get("authorized"):
            return {"status": "unavailable", "items": [], "total": 0, "next_cursor": None}
        locators = item.get("locators") or []
        if not locators or len({row["object_id"] for row in locators}) != 1:
            raise ValueError("ambiguous artifact version; use an exact object or occurrence id")
        locator = locators[0]
        obj = item["object"]
        if not ({obj.get("semantic_kind"), locator.get("storage_kind")} & NAVIGABLE_KINDS):
            return {"status": "unsupported", "items": [], "total": 0, "next_cursor": None}
        descriptor = {"ref": locator["ref"], "sha256": obj["sha256"],
                      "content_type": locator.get("content_type") or "application/json"}
        nav = published_navigation(self.state_dir, descriptor)
        if nav['status'] == 'ready':
            from zf.runtime.artifact_read_ledger import _encode_selected
            def pinned_text():
                return _encode_selected(hydrate_sidecar_ref(self.state_dir, descriptor).payload)
            nav, rows = query_navigation(self.state_dir, nav, query=query, fallback=pinned_text)
        else:
            rows = []
        binding = canonical_json_sha256([identity, obj["sha256"], query, context.actor, context.role,
            context.purpose, NAVIGATION_SCHEMA, [(o["occurrence_id"], o["access_scope"]) for o in item["occurrences"]]])
        start = 0
        if cursor:
            try:
                if len(cursor) > 512:
                    raise ValueError("oversized cursor")
                mark, start = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if mark != binding or type(start) is not int or not 0 <= start < len(rows):
                    raise ValueError("stale cursor")
            except (ValueError, TypeError) as exc:
                raise ValueError("invalid or stale navigation cursor") from exc
        result = {"schema_version": NAVIGATION_SCHEMA, "status": nav["status"], "complete": nav["complete"],
            "source_sha256": obj["sha256"], "ref": locator["ref"], "total": len(rows),
            "body_read_evidence": False, "authorizes_attempt_input": False,
            "projection_state": catalog.get("projection_state"), "source_snapshot": catalog.get("source_snapshot")}
        end = min(len(rows), start + max(1, min(50, context.limit)))
        while True:
            result["items"] = rows[start:end]
            result["next_cursor"] = (base64.urlsafe_b64encode(json.dumps([binding, end]).encode()).decode()
                                     if end < len(rows) else None)
            if len(json.dumps(result, ensure_ascii=False).encode()) + 1 <= 8192:
                return result
            end -= 1
            if end <= start:
                raise ValueError("navigation metadata exceeds output bound")
