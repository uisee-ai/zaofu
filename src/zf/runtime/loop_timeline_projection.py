"""Identity-bound, read-only Loop timeline facts.

Legacy event-only records are retained as unpaired signals, never promoted to
TaskAttempts merely because their event type ends in ``.failed``.
"""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent

_TERMINALS = {
    "task.attempt.succeeded", "task.attempt.failed",
    "task.attempt.deadlettered", "task.attempt.superseded",
}
_SIGNAL_PREFIXES = ("task.attempt.", "runtime.action.", "web.action.", "worker.")


def build_loop_timeline(events: list[tuple[int, ZfEvent]]) -> dict[str, Any]:
    """Match only explicit TaskAttempt ids; retain other events as signals."""
    attempts: dict[tuple[str, str], dict[str, Any]] = {}
    signals: list[dict[str, Any]] = []
    for seq, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        attempt_id = str(payload.get("attempt_id") or "")
        kind = event.type
        if not task_id:
            continue
        key = (task_id, attempt_id)
        trusted = event.origin in {"", "kernel"}
        if kind == "task.attempt.started" and attempt_id and trusted:
            attempts.setdefault(key, {
                "attempt_id": attempt_id, "task_id": task_id,
                "stage_id": str(payload.get("stage_id") or ""),
                "started_ts": event.ts,
                "role": str(payload.get("instance_id") or payload.get("role_instance") or payload.get("role") or ""),
                "terminal": None, "open": True, "counted": True,
            })
        elif kind in _TERMINALS and attempt_id and key in attempts and trusted:
            attempt = attempts[key]
            if attempt["terminal"] is None:
                attempt["terminal"] = {"type": kind, "ts": event.ts, "seq": seq,
                                       "reason": str(payload.get("reason") or "")}
                attempt["open"] = False
                attempt["counted"] = kind != "task.attempt.superseded"
            else:
                signals.append({"task_id": task_id, "type": kind, "ts": event.ts, "seq": seq,
                                "reason": "additional terminal observation"})
        elif kind.startswith(_SIGNAL_PREFIXES) and kind != "task.attempt.heartbeat":
            signals.append({"task_id": task_id, "type": kind, "ts": event.ts, "seq": seq,
                            "reason": "unpaired or auxiliary event"})

    tasks: dict[str, dict[str, Any]] = {}
    for item in attempts.values():
        task = tasks.setdefault(item["task_id"], {"id": item["task_id"], "attempts": [], "signals": [],
                                                    "source": "identity-bound events"})
        task["attempts"].append(item)
    for item in signals:
        task = tasks.setdefault(item["task_id"], {"id": item["task_id"], "attempts": [], "signals": [],
                                                    "source": "identity-bound events"})
        task["signals"].append(item)
    for task in tasks.values():
        task["counted"] = sum(bool(a["counted"]) for a in task["attempts"])
        task["fails"] = sum((a["terminal"] or {}).get("type") in {"task.attempt.failed", "task.attempt.deadlettered"}
                            for a in task["attempts"])
    baseline = None
    for seq, event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type != "task_map.admitted" or event.origin != "kernel":
            continue
        ref = str(payload.get("task_map_ref") or "")
        digest = str(payload.get("task_map_digest") or "")
        ids = payload.get("task_ids")
        valid_digest = len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower())
        if ref and valid_digest and isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids):
            baseline = {"event_id": event.id, "seq": seq, "task_map_ref": ref,
                        "task_map_digest": digest, "task_ids": list(dict.fromkeys(ids))}
        break
    return {"tasks": sorted(tasks.values(), key=lambda t: t["id"]),
            "attempt_count": len(attempts),
            "failed_attempt_count": sum(task["fails"] for task in tasks.values()),
            "signal_count": len(signals),
            "source": "identity-bound events", "coverage": "observed_only",
            "baseline": baseline}
