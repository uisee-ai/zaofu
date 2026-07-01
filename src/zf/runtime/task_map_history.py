"""task-map re-plan version chain (doc 69 §14.10, slice S-k).

Pure projection over ``artifact.manifest.published`` events: extracts the
task_map-kind artifact refs and reconstructs the version chain so a delivery
trace can show "the plan was re-cut: task-map v1 → v2". The runtime ledger
(``task_refs.py``) already tracks ``supersedes`` when it writes the index; here
we re-derive the same chain purely from events for the read-only delivery-trace
projection (守 I1/I2: read events, never re-judge, never write truth).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_manifest import normalize_artifact_kind

EventSlice = Sequence[tuple[int, ZfEvent]]

_PUBLISH_TYPE = "artifact.manifest.published"
_DEAD_STATUSES = {"superseded", "rejected"}


def build_task_map_history(
    events: EventSlice = (), *, feature_id: str = "",
) -> list[dict[str, Any]]:
    """Return the task_map artifact version chain (publish order, oldest→newest).

    Each entry: ``{artifact_id, version, status, ref, supersedes, reason,
    event_id, source_event_id, published_at, superseded, is_current}``. Empty
    when no task_map artifact was ever published for this feature.
    """
    entries: list[dict[str, Any]] = []
    for _seq, event in events:
        if event.type != _PUBLISH_TYPE:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if feature_id and not _matches_feature(event, payload, feature_id):
            continue
        refs = payload.get("artifact_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            if normalize_artifact_kind(str(ref.get("kind") or "")) != "task_map":
                continue
            entries.append({
                "artifact_id": str(ref.get("artifact_id") or ""),
                "version": _as_int(ref.get("version")),
                "status": str(ref.get("status") or "accepted"),
                "ref": str(ref.get("path") or ""),
                "supersedes": str(ref.get("supersedes") or ""),
                "reason": str(ref.get("summary") or ""),
                "event_id": str(event.id or ""),
                "source_event_id": str(ref.get("source_event_id") or ""),
                "published_at": str(event.ts or ""),
            })

    if not entries:
        return []

    superseded_ids = {e["supersedes"] for e in entries if e["supersedes"]}
    current_idx = _current_index(entries, superseded_ids)
    for idx, entry in enumerate(entries):
        chained = bool(entry["artifact_id"]) and entry["artifact_id"] in superseded_ids
        entry["superseded"] = (
            entry["status"] in _DEAD_STATUSES or chained or idx != current_idx
        )
        entry["is_current"] = idx == current_idx
    return entries


def _current_index(entries: list[dict[str, Any]], superseded_ids: set[str]) -> int:
    """Latest live entry = highest (version, publish order) not marked dead.

    Raw worker manifests often omit version/artifact_id; then every entry has
    version 0 and the last-published one wins by publish order.
    """
    best = -1
    best_key: tuple[int, int] | None = None
    for idx, e in enumerate(entries):
        if e["status"] in _DEAD_STATUSES:
            continue
        if e["artifact_id"] and e["artifact_id"] in superseded_ids:
            continue
        key = (e["version"], idx)
        if best_key is None or key > best_key:
            best_key, best = key, idx
    return best if best >= 0 else len(entries) - 1


def _matches_feature(event: ZfEvent, payload: dict[str, Any], feature_id: str) -> bool:
    return feature_id in (
        str(getattr(event, "feature_id", "") or ""),
        str(payload.get("feature_id") or ""),
        str(event.task_id or ""),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
