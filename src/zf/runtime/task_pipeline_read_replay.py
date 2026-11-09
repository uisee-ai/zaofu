"""Prove read-policy strengthening without changing an admitted task contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from zf.core.events.model import ZfEvent


def read_policy_compatibility(
    previous: Mapping[str, Any], current: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Accept only complete, same-digest coverage of every previously required path.

    Root reads can cover former field reads; identical-content aliases can share
    coverage. Additional required context is allowed, but changing an existing
    source's content or weakening a read is not. All non-read request fields must
    still pass the caller's ordinary semantic hash comparison.
    """
    old = previous.get("required_reads")
    new = current.get("required_reads")
    if not isinstance(old, list) or not old or not isinstance(new, list) or not new:
        return None
    for row in [*old, *new]:
        if (
            not isinstance(row, Mapping)
            or len(str(row.get("artifact_sha256") or "")) != 64
            or not str(row.get("source_id") or "")
            or not str(row.get("json_path") or "").startswith("$")
            or row.get("allow_truncated") is not False
            or row.get("max_chars", 0) != 0
            or row.get("max_items", 0) != 0
            or not isinstance(row.get("min_returned_bytes"), int)
            or row["min_returned_bytes"] < 1
        ):
            return None
    old_sources = {row["source_id"] for row in old}
    new_sources = {row["source_id"] for row in new}
    for source in old_sources & new_sources:
        if {r["artifact_sha256"] for r in old if r["source_id"] == source} != {
            r["artifact_sha256"] for r in new if r["source_id"] == source
        }:
            return None
    coverage = []
    for required in old:
        path = required["json_path"]
        covering = next((row for row in new if (
            row["artifact_sha256"] == required["artifact_sha256"]
            and row["min_returned_bytes"] >= required["min_returned_bytes"]
            and (row["json_path"] == "$" or path == row["json_path"]
                 or path.startswith(row["json_path"] + ".")
                 or path.startswith(row["json_path"] + "["))
        )), None)
        if covering is None:
            return None
        coverage.append({"previous": dict(required), "current": dict(covering)})
    return {"rule": "same-content-read-coverage.v1", "coverage": coverage}


def mark_restart_compatibility_probes(
    operations: Iterable[Mapping[str, Any]], events: Sequence[ZfEvent],
) -> list[Mapping[str, Any]]:
    """Offer a pre-send recheck after restart; never change canonical status.

    A failed probe emits a new blocked occurrence after loop.started and thus
    cannot poll forever. ensure_operation must still prove compatibility before
    sending anything. Other blocked reasons never gain this eligibility.
    """
    positions = {event.id: index for index, event in enumerate(events)}
    restart = next((event for event in reversed(events)
                    if event.type == "loop.started" and event.origin == "kernel"), None)
    if restart is None:
        return list(operations)
    return [{**operation, "compatibility_probe_restart_event_id": restart.id}
            if (operation.get("status") == "blocked"
                and operation.get("task_pipeline_stage")
                and operation.get("reason") == "request_hash_compatibility_failed"
                and operation.get("last_event_id") in positions
                and positions[operation["last_event_id"]] < positions[restart.id])
            else operation for operation in operations]
