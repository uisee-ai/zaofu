"""Retire a probe repair checkpoint only after authenticated revalidation."""
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.host_probe_diagnosis import bound_container_probe_receipt, probe_obligation


def probe_repair_revalidated(
    state_dir: Path, events: list[ZfEvent], config: Any, item: dict[str, Any],
) -> bool:
    """A read projection: no product verdict, new execution, or state mutation."""
    policy = getattr(getattr(config, "runtime", None), "host_verification", None)
    continuation = item.get("continuation") or {}
    case_id = continuation.get("recovery_case_id")
    keys = ("workflow_run_id", "task_id", "task_map_generation", "target_commit")
    if (not policy or not policy.enabled or not case_id
            or continuation.get("safe_resume_action") != "agent_recommendation"
            or any(not continuation.get(key) for key in keys)):
        return False
    merged = next((i for i in range(len(events) - 1, -1, -1)
        if events[i].origin == "kernel"
        and events[i].type == "run.manager.repair.merge.merged"
        and events[i].payload.get("queue_id") == item.get("queue_id")
        and events[i].payload.get("continuation") == continuation), None)
    if merged is None:
        return False
    sources = set()
    for event in events[:merged]:
        p = event.payload
        if (event.origin == "kernel" and event.type == "run.manager.action.planned"
                and p.get("recovery_case_id") == case_id
                and all(p.get(key) == continuation[key] for key in keys)):
            sources.update(p.get("failure_event_ids") or [])
    prior: dict[str, ZfEvent] = {}
    failures: list[dict] = []
    latest = None
    for index, event in enumerate(events):
        receipt = bound_container_probe_receipt(state_dir, event, prior, policy)
        prior[event.id] = event
        if receipt is None:
            continue
        if not all((receipt.get(key) if key == "target_commit" else event.payload.get(key))
                   == continuation[key] for key in keys):
            continue
        if index < merged and event.id in sources and receipt["exit_code"] != 0:
            failures.append(receipt)
        if (index > merged and len(failures) == 1
                and probe_obligation(receipt) == probe_obligation(failures[0])):
            latest = receipt
    return len(failures) == 1 and latest is not None and latest["exit_code"] == 0
