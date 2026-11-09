"""A lost reader cannot reset a role already handed to another dispatch."""

from zf.core.events import ZfEvent


def another_reader_owns_role(events: list[ZfEvent], dispatch: ZfEvent) -> bool:
    role = str(dispatch.payload.get("role_instance") or "")
    if not role:
        return False
    owner: tuple[str, str, str] | None = None
    expected = (str(dispatch.payload.get("fanout_id") or ""),
                str(dispatch.payload.get("child_id") or "synth"),
                str(dispatch.payload.get("run_id") or ""))
    for event in events:
        p = event.payload
        if event.type in {"fanout.child.dispatched", "fanout.synth.dispatched"}:
            if p.get("role_instance") == role:
                owner = (str(p.get("fanout_id") or ""), str(p.get("child_id") or "synth"),
                         str(p.get("run_id") or ""))
        elif owner and event.type in {"fanout.child.completed", "fanout.child.failed", "fanout.synth.completed"}:
            if (p.get("fanout_id"), p.get("child_id") or "synth") == owner[:2]:
                if not p.get("run_id") or p["run_id"] == owner[2]:
                    owner = None
        elif owner and event.type == "fanout.cancelled" and p.get("fanout_id") == owner[0]:
            owner = None
    return owner is not None and owner != expected
