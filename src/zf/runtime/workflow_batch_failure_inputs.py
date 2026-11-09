"""Complete ordered inputs for the four batch failure classification helpers."""
from __future__ import annotations

from zf.core.events.model import ZfEvent


def select_batch_failure_inputs(events: list[ZfEvent]) -> list[ZfEvent]:
    """Select by consumed types and trigger refs, never by age or batch count.

    This view is only for reader ownership, candidate trigger, child failure,
    and candidate authority lookup. Recovery/supersession still uses the full
    ledger. Rebuild on each projection so late evidence remains visible.
    """
    kinds = {"fanout.started", "candidate.ready"}
    trigger_ids: set[str] = set()
    for event in events:
        if event.type not in {"fanout.started", "fanout.aggregate.completed"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "fanout.started":
            trigger = str(body.get("trigger_event_id") or "")
            # Reader ownership uses the raw ref; candidate lookup strips it.
            trigger_ids.update((trigger, trigger.strip()))
        else:
            kinds.add(str(body.get("failure_event") or "").strip())
    return [
        event for event in events
        if event.type in kinds
        or event.type.endswith(".child.failed")
        or event.id in trigger_ids
    ]
