"""Attempt-bound navigation projection. Listing never produces read evidence."""
from __future__ import annotations

import base64
import json
import re
import shlex
from pathlib import Path
from typing import Any

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.call_result_envelope import canonical_json_sha256

INDEX_SCHEMA = "attempt-input-index.v1"
INDEX_BYTES = 8192


def load_attempt_source_manifest(state_dir: Path, attempt_id: str) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(attempt_id)).strip("-._") or "attempt"
    root = state_dir / "artifacts" / "attempts" / safe / "source-manifests"
    manifests: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and str(value.get("attempt_id") or "") == str(attempt_id):
            manifests.append(value)
    if not manifests:
        raise ValueError(f"attempt source manifest not found: {attempt_id}")
    canonical = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in manifests
    }
    if len(canonical) != 1:
        raise ValueError(f"attempt has divergent source manifests: {attempt_id}")
    return manifests[0]



def input_index(
    *, manifest: dict[str, Any], required: list[dict[str, Any]] | None,
    identity: dict[str, str], reads: list[dict[str, Any]],
    query: str = "", cursor: str | None = None, limit: int = 10,
) -> dict[str, Any]:
    """Literal locator lookup; no global search, body loading or ACL union."""
    if not 1 <= limit <= 50 or len(query) > 256:
        raise ValueError("input index requires limit 1..50 and query at most 256 characters")
    from zf.runtime.artifact_read_ledger import required_read_satisfied
    from zf.runtime.cli_command import zf_cli_cmd

    attempt = str(manifest.get("attempt_id") or "")
    items = []
    for source in manifest.get("sources", []):
        if not artifact_access_allowed(source.get("access_scope"),
                actor=identity.get("actor", ""), role=identity.get("role", ""), purpose=identity.get("purpose", "")):
            continue
        allowed = source.get("allowed_paths") or ["$"]
        requirements = [row for row in (required or []) if row.get("source_id") == source["source_id"]
                        and row.get("artifact_id") == source["artifact_id"]
                        and (not row.get("artifact_sha256") or row["artifact_sha256"] == source["sha256"])]
        paths = list(dict.fromkeys([*allowed, *[row.get("json_path", "$") for row in requirements]]))
        for path in paths:
            if "$" not in allowed and path not in allowed:
                continue
            locator = f"{source['source_id']} {source['artifact_id']} {path} {source['ref']}"
            if query.casefold() not in locator.casefold():
                continue
            snapshot = next((s for s in source.get("read_selections", []) if s.get("json_path") == path), {})
            policy = next((row for row in requirements if row.get("json_path", "$") == path), None)
            occurrence_reads = [r for r in reads if r.get("status") == "read"
                and r.get("attempt_id") == attempt and r.get("source_id") == source["source_id"]
                and r.get("artifact_id") == source["artifact_id"] and r.get("artifact_sha256") == source["sha256"]
                and r.get("occurrence_id") == source.get("occurrence_id") and r.get("json_path", "$") == path]
            args = ["artifact", "read", "--attempt", attempt, "--source", source["source_id"],
                    "--artifact", source["artifact_id"], "--json-path", path]
            items.append({
                "source_id": source["source_id"], "artifact_id": source["artifact_id"],
                "occurrence_id": source.get("occurrence_id", ""), "ref": source["ref"],
                "sha256": source["sha256"], "json_path": path,
                "required": policy is not None if required is not None else None,
                "reason": "required_by_attempt_policy" if policy is not None else "available_in_attempt_scope",
                "read_status": (("recorded" if required_read_satisfied(occurrence_reads, policy) else "missing")
                                if policy else "optional" if required is not None else "unknown"),
                "source_bytes": snapshot.get("source_bytes"), "selected_bytes": snapshot.get("selected_bytes"),
                "selected_sha256": snapshot.get("selected_sha256"),
                "snapshot_status": "pinned" if snapshot else "legacy",
                "read_command": f"{zf_cli_cmd()} {shlex.join(args)}", "cost": read_cost(occurrence_reads),
            })
    binding = canonical_json_sha256({"manifest": manifest, "requirements": required,
                                    "identity": identity, "query": query})
    start = 0
    if cursor:
        try:
            if len(cursor) > 512:
                raise ValueError("oversized cursor")
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(value, list) or len(value) != 2 or value[0] != binding:
                raise ValueError("stale cursor")
            start = value[1]
            if type(start) is not int or not 0 <= start < len(items):
                raise ValueError("invalid cursor offset")
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid or stale input-index cursor") from exc
    result = {
        "schema_version": INDEX_SCHEMA, "attempt_id": attempt,
        "task_id": str(manifest.get("task_id") or ""),
        "read_purpose": str(manifest.get("read_purpose") or ""),
        "manifest_sha256": canonical_json_sha256(manifest),
        "scope": "authorized_attempt_inputs", "query_mode": "literal_locator",
        "total": len(items), "items": [], "next_cursor": None,
        "coverage": "manifest_only", "body_read_evidence": False,
        "required_policy_status": "available" if required is not None else "unknown",
        "provider_tokens": None, "provider_cache_tokens": None,
    }
    end = min(len(items), start + limit)
    while True:
        result["items"] = items[start:end]
        result["next_cursor"] = (base64.urlsafe_b64encode(json.dumps([binding, end]).encode()).decode()
                                 if end < len(items) else None)
        if len(json.dumps(result, ensure_ascii=False).encode()) + 1 <= INDEX_BYTES:
            return result
        end -= 1
        if end <= start:
            raise ValueError("input index metadata exceeds output bound")


def read_cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Observed production bytes, never estimated tokens or current memory.

    Exact-range duplicates are a conservative lower bound: partially overlapping
    pages are not called duplicates without reading their Unicode bodies.
    """
    produced = repeated = 0
    ranges: set[tuple[Any, ...]] = set()
    deliveries: set[tuple[Any, ...]] = set()
    for row in rows:
        delivery = (row.get("delivery_id"), row.get("output_sha256"), row.get("page_start"),
                    row.get("page_end"), row.get("read_at"))
        if delivery in deliveries:
            continue
        deliveries.add(delivery)
        count = max(0, int(row.get("returned_bytes") or 0))
        produced += count
        key = (row.get("selected_sha256"), row.get("page_start"), row.get("page_end"),
               row.get("max_items"), row.get("max_chars"), count)
        if key in ranges:
            repeated += count
        ranges.add(key)
    return {"produced_body_bytes": produced, "repeated_exact_range_bytes": repeated,
            "read_count": len(deliveries), "provider_delivery": "unknown",
            "current_memory": "unknown"}
