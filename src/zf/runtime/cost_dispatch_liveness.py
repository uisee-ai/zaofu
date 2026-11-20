"""Select cost-monitoring dispatches without reviving closed Reader attempts."""
from collections.abc import Sequence

from zf.core.events.model import ZfEvent


def last_open_cost_dispatch(events: Sequence[ZfEvent]) -> ZfEvent | None:
    closed: set[tuple[str, ...]] = set()
    fields = ("workflow_run_id", "fanout_id", "child_id", "run_id")
    for event in reversed(events):
        body = event.payload or {}
        identity = tuple(str(body.get(key) or "") for key in fields)
        if (event.origin == "kernel" and all(identity)
                and event.type in {"fanout.child.completed", "fanout.child.failed"}):
            closed.add(identity)
        elif event.type in {"task.dispatched", "fanout.child.dispatched"}:
            if event.type == "fanout.child.dispatched" and identity in closed:
                continue
            return event
    return None
