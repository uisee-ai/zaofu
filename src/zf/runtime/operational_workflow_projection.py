"""Generation-aware operational workflow read model and rebuild consumer."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text


SCHEMA_VERSION = "operational-workflow-projection.v1"


def build_operational_workflow_projection(
    events: Iterable[ZfEvent],
) -> dict[str, Any]:
    event_list = list(events)
    rows: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(event_list, start=1):
        payload = event.payload if isinstance(event.payload, dict) else {}
        run_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        )
        stage_id = str(
            payload.get("stage_id")
            or payload.get("stage_slot")
            or ""
        )
        fanout_id = str(payload.get("fanout_id") or "")
        generation = str(
            payload.get("task_map_generation")
            or payload.get("generation")
            or ""
        )
        if not any((run_id, stage_id, fanout_id, generation)):
            continue
        key = "\0".join((run_id, stage_id, fanout_id, generation))
        row = rows.setdefault(key, {
            "workflow_run_id": run_id,
            "stage_id": stage_id,
            "fanout_id": fanout_id,
            "task_map_generation": generation,
            "status": "observed",
            "task_ids": [],
            "evidence_refs": [],
        })
        if event.type == "fanout.started":
            row["status"] = "running"
        elif event.type in {"fanout.aggregate.completed", "candidate.ready"}:
            row["status"] = str(payload.get("status") or "completed")
        elif event.type in {"fanout.cancelled", "fanout.timed_out"}:
            row["status"] = event.type.rsplit(".", 1)[-1]
        task_id = str(event.task_id or payload.get("task_id") or "")
        if task_id and task_id not in row["task_ids"]:
            row["task_ids"].append(task_id)
        for ref in payload.get("evidence_refs") or []:
            value = str(ref or "")
            if value and value not in row["evidence_refs"]:
                row["evidence_refs"].append(value)
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        row["cursor"] = index
    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "cursor": len(event_list),
        "last_event_id": event_list[-1].id if event_list else "",
        "freshness": {
            "status": "ready",
            "event_count": len(event_list),
        },
        "rows": sorted(
            rows.values(),
            key=lambda item: (
                item["workflow_run_id"],
                item["stage_id"],
                item["fanout_id"],
                item["task_map_generation"],
            ),
        ),
    }


def write_operational_workflow_projection(
    state_dir: Path,
    projection: dict[str, Any],
) -> Path:
    path = Path(state_dir) / "projections" / "operational-workflow.json"
    atomic_write_text(
        path,
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def consume_projection_rebuild_requests(
    *,
    state_dir: Path,
    events: list[ZfEvent],
    writer: EventWriter,
) -> int:
    settled = {
        str((event.payload or {}).get("request_event_id") or "")
        for event in events
        if event.type in {
            "projection.rebuild.completed",
            "projection.rebuild.failed",
        }
    }
    consumed = 0
    for request in events:
        if request.type != "projection.rebuild.requested" or request.id in settled:
            continue
        payload = request.payload if isinstance(request.payload, dict) else {}
        requested = str(payload.get("projection") or "")
        if requested not in {
            "",
            "operational",
            "operational-workflow",
            "run_manager",
            "stage_spine",
        }:
            continue
        try:
            projection = build_operational_workflow_projection(events)
            path = write_operational_workflow_projection(state_dir, projection)
            writer.append(ZfEvent(
                type="projection.rebuild.completed",
                actor="run-manager",
                causation_id=request.id,
                correlation_id=request.correlation_id,
                payload={
                    "request_event_id": request.id,
                    "projection": requested or "operational-workflow",
                    "projection_ref": str(path),
                    "cursor": int(projection["cursor"]),
                    "last_event_id": str(projection["last_event_id"]),
                },
            ))
        except Exception as exc:
            writer.append(ZfEvent(
                type="projection.rebuild.failed",
                actor="run-manager",
                causation_id=request.id,
                correlation_id=request.correlation_id,
                payload={
                    "request_event_id": request.id,
                    "projection": requested or "operational-workflow",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            ))
        consumed += 1
    return consumed


__all__ = [
    "SCHEMA_VERSION",
    "build_operational_workflow_projection",
    "consume_projection_rebuild_requests",
    "write_operational_workflow_projection",
]
