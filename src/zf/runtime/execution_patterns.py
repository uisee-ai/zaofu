"""Execution pattern projection derived from zf.yaml and fanout events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class ExecutionPattern:
    pattern_id: str
    kind: str
    source: dict[str, str]
    trigger: str = ""
    target_ref: str = ""
    roles: list[str] = field(default_factory=list)
    children: list[dict[str, Any]] = field(default_factory=list)
    barrier: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "kind": self.kind,
            "source": dict(self.source),
            "trigger": self.trigger,
            "target_ref": self.target_ref,
            "roles": list(self.roles),
            "children": list(self.children),
            "barrier": dict(self.barrier),
        }


def project_execution_patterns(
    config: ZfConfig | None,
    *,
    state_dir: Path | None = None,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    """Return a rebuildable read model of callable execution patterns."""

    patterns = _patterns_from_config(config)
    active_runs = _active_pattern_runs(state_dir, events) if state_dir else []
    return {
        "schema_version": "execution-patterns.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patterns": [pattern.to_dict() for pattern in patterns],
        "runs": active_runs,
        "counts": {
            "patterns": len(patterns),
            "active_runs": len([
                run for run in active_runs
                if run.get("status") not in {"completed", "failed", "timed_out", "cancelled"}
            ]),
        },
    }


def resolve_execution_pattern(
    config: ZfConfig | None,
    pattern_id: str,
) -> ExecutionPattern | None:
    for pattern in _patterns_from_config(config):
        if pattern.pattern_id == pattern_id:
            return pattern
    return None


def _patterns_from_config(config: ZfConfig | None) -> list[ExecutionPattern]:
    if config is None:
        return []
    patterns: list[ExecutionPattern] = []
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        stage_id = str(getattr(stage, "id", "") or "")
        topology = str(getattr(stage, "topology", "") or "")
        if not stage_id or not topology:
            continue
        aggregate = getattr(stage, "aggregate", None)
        children = _stage_children(stage)
        role_refs = [str(role) for role in list(getattr(stage, "roles", []) or [])]
        required_children = [
            str(child.get("child_id") or child.get("role_instance") or child.get("role") or "")
            for child in children
        ] or role_refs
        patterns.append(ExecutionPattern(
            pattern_id=stage_id,
            kind=topology,
            source={"kind": "zf.yaml", "path": "workflow.stages", "stage_id": stage_id},
            trigger=str(getattr(stage, "trigger", "") or ""),
            target_ref=str(getattr(stage, "target_ref", "") or ""),
            roles=role_refs,
            children=children,
            barrier={
                "mode": str(getattr(aggregate, "mode", "") or "wait_for_all"),
                "required_children": [child for child in required_children if child],
                "success_event": str(getattr(aggregate, "success_event", "") or ""),
                "failure_event": str(getattr(aggregate, "failure_event", "") or ""),
                "synth_role": str(getattr(aggregate, "synth_role", "") or ""),
                "timeout_seconds": int(getattr(stage, "timeout_seconds", 0) or 0),
                "max_retries": int(getattr(aggregate, "max_retries", 0) or 0),
            },
        ))
    return patterns


def _stage_children(stage: object) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    raw_children = list(getattr(stage, "children", []) or [])
    if not raw_children:
        for role in list(getattr(stage, "roles", []) or []):
            role_ref = str(role)
            children.append({
                "child_id": role_ref,
                "role_instance": role_ref,
                "role": role_ref,
                "scope": "",
                "expected_output": "",
            })
        return children
    for raw in raw_children:
        role_instance = str(
            getattr(raw, "role_instance", "") or getattr(raw, "role", "") or ""
        )
        role = str(getattr(raw, "role", "") or role_instance)
        payload = getattr(raw, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        scope = str(getattr(raw, "scope", "") or "")
        child_id = str(payload.get("child_id") or role_instance or role)
        children.append({
            "child_id": child_id,
            "role_instance": role_instance,
            "role": role,
            "scope": scope,
            "task_id": str(getattr(raw, "task_id", "") or ""),
            "expected_output": str(payload.get("expected_output") or ""),
            "payload": dict(payload),
        })
    return children


def _active_pattern_runs(
    state_dir: Path,
    events: list[ZfEvent] | None,
) -> list[dict[str, Any]]:
    event_list = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    runs: dict[str, dict[str, Any]] = {}
    for event in event_list:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id or not event.type.startswith("fanout."):
            continue
        run = runs.setdefault(fanout_id, {
            "run_id": fanout_id,
            "pattern_id": str(payload.get("stage_id") or ""),
            "kind": str(payload.get("topology") or ""),
            "status": "observed",
            "last_event_id": event.id,
            "last_event_type": event.type,
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "children": set(),
        })
        run["last_event_id"] = event.id
        run["last_event_type"] = event.type
        if event.type == "fanout.started":
            run["status"] = "started"
        elif event.type == "fanout.aggregate.completed":
            run["status"] = str(payload.get("status") or "completed")
        elif event.type == "fanout.timed_out":
            run["status"] = "timed_out"
        elif event.type == "fanout.cancelled":
            run["status"] = "cancelled"
        child_id = str(payload.get("child_id") or "")
        if child_id:
            run["children"].add(child_id)
    out: list[dict[str, Any]] = []
    for run in runs.values():
        item = dict(run)
        item["children"] = sorted(run["children"])
        out.append(item)
    out.sort(key=lambda item: str(item.get("run_id") or ""))
    return out
