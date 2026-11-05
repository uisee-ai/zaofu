"""Keep admitted plan identity and its source references in one generation."""

from collections.abc import Mapping, Sequence
from typing import Any

from zf.core.events.model import ZfEvent


def bind_admitted_plan_source_refs(
    payload: dict[str, Any],
    current: Mapping[str, Any],
    events: Sequence[ZfEvent | Mapping[str, Any]],
) -> None:
    source_id = current.get("source_event_id")
    if not source_id:
        return
    for event in events:
        event_id = event.get("id") if isinstance(event, Mapping) else event.id
        if event_id != source_id:
            continue
        source = event.get("payload", {}) if isinstance(event, Mapping) else event.payload
        for ref_key, digest_key in (
            ("task_map_ref", "task_map_digest"),
            ("source_index_ref", "source_index_digest"),
        ):
            if ref_key in source:
                payload[ref_key] = source[ref_key]
                payload.pop(digest_key, None)
                if source.get(digest_key):
                    payload[digest_key] = source[digest_key]
        return
