"""Recover a required-page cursor from evidence of this session's output.

This is an explicit convenience for a worker that can still use the material;
historical tool output does not prove that it survived context compaction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import _parse_ledger
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def resume_position(
    state_dir: Path, *, manifest: dict[str, Any], identity: dict[str, Any],
    bundle_digest: str, groups: list[dict[str, Any]], receipts: list[list[dict[str, Any]]],
    event_log: Any = None,
) -> tuple[int, int]:
    """Return the first gap; unavailable or inconsistent evidence starts over."""
    attempt = str(manifest.get("attempt_id") or "")
    operation = str(identity.get("operation_id") or "")
    if not attempt or not operation or identity.get("provider") != "codex":
        return 0, 0
    try:
        registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", state_dir.parent)
        session = str(registry.get(identity["actor"]) or "")
        if not session:
            return 0, 0
        from zf.cli.artifact_resume_boundary import continuation_boundary, _epoch

        boundary = continuation_boundary(
            event_log=event_log, transcript=registry.get_path(identity["actor"]),
            actor=identity["actor"], session_id=session,
        )
        if boundary is None:
            return 0, 0
        active = active_read_ledger_path(state_dir, attempt)
        paths = sorted(active.parent.glob("read-ledger-*.jsonl"))
        if active.exists():
            paths.append(active)
        rows: list[dict[str, Any]] = []
        for path in paths:
            raw = path.read_bytes()
            if path != active and not path.stem.endswith(hashlib.sha256(raw).hexdigest()[:16]):
                return 0, 0
            rows.extend(_parse_ledger(raw, expected_attempt_id=attempt))
        candidates = [row for row in rows if row.get("operation_id") == operation
                      and row.get("bundle_digest") == bundle_digest
                      and row.get("consumer_actor") == identity["actor"]
                      and row.get("consumer_role") == identity["role"]
                      and row.get("consumer_provider") == identity["provider"]
                      and row.get("consumer_purpose") == identity["purpose"]]
        observed = observe_artifact_delivery(state_dir, candidates)
        observed = [row for row in observed if row.get("provider_delivery_status") == "observed_in_transcript"
                    and row.get("provider_session_id") == session]
        if boundary:
            retained = []
            for row in observed:
                witness = row.get("provider_observation_ref")
                if not isinstance(witness, dict):
                    continue
                try:
                    delivered = hydrate_sidecar_ref(state_dir, witness).payload
                    if _epoch(delivered.get("timestamp")) > boundary:
                        retained.append(row)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            observed = retained
        bodies: dict[str, Any] = {}
        for index, (group, aliases) in enumerate(zip(groups, receipts, strict=True)):
            selection = group.get("selection")
            content = group.get("content", "")  # Legacy direct callers.
            total = selection.chars if selection else len(content)
            digest = selection.digest if selection else hashlib.sha256(content.encode()).hexdigest()
            byte_count = selection.byte_count if selection else len(content.encode())
            ranges_by_alias: list[list[tuple[int, int]]] = []
            for alias in aliases:
                ranges: list[tuple[int, int]] = []
                for row in observed:
                    fields = ("workflow_run_id", "task_id", "attempt_id", "dispatch_id", "source_id",
                              "artifact_id", "artifact_sha256", "json_path", "occurrence_id")
                    if any(row.get(k) != alias.get(k) for k in fields):
                        continue
                    start, end = row.get("page_start"), row.get("page_end")
                    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= total
                            or row.get("page_total") != total or row.get("selected_sha256") != digest
                            or row.get("selected_bytes") != byte_count):
                        continue
                    output_digest = row["output_sha256"]
                    if output_digest not in bodies:
                        bodies[output_digest] = json.loads(hydrate_sidecar_ref(state_dir, row["output_ref"]).payload["body"])
                    page = bodies[output_digest]
                    if (page.get("schema_version") != "required-artifact-page.v2"
                            or page.get("attempt_id") != attempt or page.get("bundle_digest") != bundle_digest
                            or page.get("delivery_id") != row.get("delivery_id")):
                        continue
                    for item in page.get("items", []):
                        sources = [item, *item.get("aliases", [])]
                        if (any(s.get("source_id") == alias["source_id"] and s.get("artifact_id") == alias["artifact_id"]
                                and s.get("json_path") == alias["json_path"]
                                and (not s.get("artifact_sha256") or s["artifact_sha256"] == alias["artifact_sha256"])
                                for s in sources)
                                and item.get("page_start") == start and item.get("page_end") == end
                                and item.get("content") == (selection.read(start, end) if selection else content[start:end])):
                            ranges.append((start, end))
                            break
                ranges_by_alias.append(ranges)
            ends = []
            for ranges in ranges_by_alias:
                end = 0
                for start, stop in sorted(ranges):
                    if start > end:
                        break
                    end = max(end, stop)
                ends.append(end)
            position = min(ends, default=0)
            if position < total:
                return index, position
        return len(groups), 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return 0, 0
