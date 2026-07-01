"""Projections layer: fanouts (moved verbatim from web/server.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.security.redaction import redact_obj
from zf.core.task.kanban_projection import kanban_column_projection
import hashlib
import json
from zf.web.projections.common import _first_nonempty, _payload_mentions, _payload_ref, _read_json_file
from zf.web.projections.summaries import _refs_from_events, _safe_token
from zf.web.projections.events import _event_to_dict, _events_with_seq, _stage_summary


def _candidate_detail(
    state_dir: Path,
    pdd_id: str,
    config: ZfConfig | None = None,
) -> dict:
    manifest = _candidate_manifest(state_dir, pdd_id)
    events = [
        (seq, event)
        for seq, event in _events_with_seq(state_dir, config=config)
        if _payload_mentions(getattr(event, "payload", {}), pdd_id)
        or getattr(event, "correlation_id", None) == pdd_id
        or getattr(event, "task_id", None) == pdd_id
    ]
    refs = _refs_from_events(events)
    verify = _stage_summary(events, {"test.", "verify.", "judge."})
    review = _stage_summary(events, {"review."})
    blockers = []
    manifest_status = str(manifest.get("status") or "")
    if manifest_status == "conflict":
        blockers.append("candidate conflict")
    if verify["state"] not in {"passed", "empty"}:
        blockers.append("verify not passed")
    if review["state"] not in {"approved", "empty", "passed"}:
        blockers.append("review not approved")
    included_tasks = manifest.get("included_tasks") if isinstance(manifest, dict) else []
    manifest_task_refs = []
    manifest_tasks = []
    if isinstance(included_tasks, list):
        for item in included_tasks:
            if not isinstance(item, dict):
                continue
            task_id = item.get("task_id")
            task_ref = item.get("task_ref")
            if task_id:
                manifest_tasks.append(str(task_id))
            if task_ref:
                manifest_task_refs.append(str(task_ref))
    candidate_ref = (
        str(manifest.get("branch") or "")
        or refs.get("candidate_ref")
        or refs.get("candidate_branch")
        or (refs.get("branch") if str(refs.get("branch") or "").startswith("candidate/") else "")
        or ""
    )
    return {
        "pdd_id": pdd_id,
        "candidate_ref": candidate_ref,
        "base_main": (
            str(manifest.get("base_commit") or manifest.get("base_ref") or "")
            or refs.get("base_commit")
            or refs.get("base_ref")
            or ""
        ),
        "task_refs": sorted(set(manifest_task_refs) | {
            str(v) for v in refs.values()
            if isinstance(v, str) and v.startswith("task/")
        }),
        "tasks": sorted(set(manifest_tasks) | {
            event.task_id for _, event in events
            if getattr(event, "task_id", None)
        }),
        "verify": verify,
        "review": review,
        "status": manifest_status or ("ready" if not blockers else "observed"),
        "ship_ready": manifest_status == "ready" or (
            not blockers and any(event.type == "candidate.ready" for _, event in events)
        ),
        "blockers": blockers,
        "manifest": redact_obj(manifest),
        "timeline": [_event_to_dict(seq, event) for seq, event in events],
        "empty": not events and not manifest,
    }


def _fanout_detail(
    state_dir: Path,
    fanout_id: str,
    config: ZfConfig | None = None,
) -> dict:
    from zf.web.projections.tasks import _task_index_with_archive  # deferred: import cycle
    manifest = _fanout_manifest(state_dir, fanout_id)
    events = [
        (seq, event)
        for seq, event in _events_with_seq(state_dir, config=config)
        if "fanout" in getattr(event, "type", "")
        or _payload_mentions(getattr(event, "payload", {}), fanout_id)
    ]
    children: dict[str, dict] = {}
    for seq, event in events:
        payload = getattr(event, "payload", {}) or {}
        child = (
            _payload_ref(payload, "child_run")
            or _payload_ref(payload, "child")
            or _payload_ref(payload, "run_id")
        )
        if not child and "child" in getattr(event, "type", ""):
            child = getattr(event, "task_id", None)
        if child:
            item = children.setdefault(str(child), {
                "id": str(child),
                "last_type": "",
                "last_seq": 0,
                "task_id": getattr(event, "task_id", None),
            })
            item["last_type"] = getattr(event, "type", "")
            item["last_seq"] = seq
    manifest_children = []
    task_index = _task_index_with_archive(state_dir)
    if isinstance(manifest.get("children"), list):
        for child in manifest["children"]:
            if isinstance(child, dict):
                manifest_children.append(
                    _fanout_child_projection(child, task_index=task_index)
                )
    projected_children = manifest_children or [
        _fanout_child_projection(child, task_index=task_index)
        for child in children.values()
    ]
    lane_projection = _fanout_lane_projection(
        manifest,
        projected_children,
        config=config,
    )
    progress = _fanout_progress(projected_children, lane_projection=lane_projection)
    aggregate_config = manifest.get("aggregate_config")
    aggregate_config = aggregate_config if isinstance(aggregate_config, dict) else {}
    aggregate = manifest.get("aggregate")
    aggregate = dict(aggregate) if isinstance(aggregate, dict) else {}
    if "mode" not in aggregate:
        aggregate["mode"] = (
            aggregate_config.get("mode")
            or _first_nonempty([
                _payload_ref(getattr(event, "payload", {}), "wait_policy")
                for _, event in reversed(events)
            ])
            or ""
        )
    if "synth_role" not in aggregate:
        aggregate["synth_role"] = aggregate_config.get("synth_role") or ""
    topology = str(manifest.get("topology") or "") or _first_nonempty([
        _payload_ref(getattr(event, "payload", {}), "topology")
        for _, event in reversed(events)
    ]) or ""
    stage_id = str(manifest.get("stage_id") or "") or _first_nonempty([
        _payload_ref(getattr(event, "payload", {}), "stage_id")
        for _, event in reversed(events)
    ]) or ""
    target_ref = str(manifest.get("target_ref") or "") or _first_nonempty([
        _payload_ref(getattr(event, "payload", {}), "target_ref")
        for _, event in reversed(events)
    ]) or ""
    return {
        "fanout_id": fanout_id,
        "trace_id": str(manifest.get("trace_id") or "") or _first_nonempty([
            _payload_ref(getattr(event, "payload", {}), "trace_id")
            for _, event in reversed(events)
        ]) or "",
        "pdd_id": str(manifest.get("pdd_id") or "") or _first_nonempty([
            _payload_ref(getattr(event, "payload", {}), "pdd_id")
            for _, event in reversed(events)
        ]) or "",
        "topology": topology,
        "stage_id": stage_id,
        "target_ref": target_ref,
        "status": str(manifest.get("status") or "observed"),
        "progress": progress,
        "lane_projection": lane_projection,
        "trigger": _fanout_trigger_projection(manifest, events),
        "aggregate_role": _first_nonempty([
            _payload_ref(getattr(event, "payload", {}), "aggregate_role")
            for _, event in reversed(events)
        ]) or aggregate.get("synth_role") or "",
        "wait_policy": _first_nonempty([
            _payload_ref(getattr(event, "payload", {}), "wait_policy")
            for _, event in reversed(events)
        ]) or aggregate.get("mode") or "",
        "children": projected_children,
        "aggregate": redact_obj(aggregate),
        "aggregate_config": redact_obj(aggregate_config),
        "synth": redact_obj(manifest.get("synth") or {}),
        "manifest": redact_obj(manifest),
        "timeline": [_event_to_dict(seq, event) for seq, event in events],
        "empty": not events and not manifest,
    }


def _fanout_child_projection(
    child: dict,
    *,
    task_index: dict[str, object] | None = None,
) -> dict:
    child_id = str(
        child.get("child_id")
        or child.get("id")
        or child.get("child")
        or child.get("run_id")
        or ""
    )
    projected = {
        "id": child_id,
        "child_id": child_id,
        "role_instance": str(child.get("role_instance") or child.get("actor") or ""),
        "status": str(child.get("status") or "observed"),
        "task_id": str(child.get("task_id") or ""),
        "scope": str(child.get("scope") or ""),
        "run_id": str(child.get("run_id") or ""),
        "target_ref": str(child.get("target_ref") or ""),
        "workdir": str(child.get("workdir") or ""),
        "source_branch": str(child.get("source_branch") or ""),
        "source_commit": str(child.get("source_commit") or ""),
        "task_ref": str(child.get("task_ref") or ""),
        "task_map_ref": str(child.get("task_map_ref") or ""),
        "source_index_ref": str(child.get("source_index_ref") or ""),
        "last_type": str(child.get("last_type") or ""),
        "last_seq": int(child.get("last_seq") or 0),
    }
    payload = child.get("payload") if isinstance(child.get("payload"), dict) else {}
    for key in (
        "assignment_strategy",
        "lane_profile",
        "lane_id",
        "stage_slot",
        "affinity_tag",
        "upstream_fanout_id",
        "upstream_child_id",
        "upstream_task_id",
    ):
        value = child.get(key)
        if (value is None or value == "") and isinstance(payload, dict):
            value = payload.get(key)
        projected[key] = str(value or "")
    task_id = str(projected.get("task_id") or "")
    task = (task_index or {}).get(task_id)
    if task is not None:
        projection = kanban_column_projection(task)
        projected["linked_task"] = {
            "task_id": task_id,
            "task_status": str(getattr(task, "status", "") or ""),
            "assigned_to": str(getattr(task, "assigned_to", "") or ""),
            "kanban_column": projection.column,
            "kanban_column_label": projection.label,
            "kanban_column_reason": projection.reason,
            "kanban_column_badges": list(projection.badges),
        }
    for key in (
        "report",
        "recommendation",
        "report_status",
        "reason",
        "evidence",
        "briefing_path",
        "report_path",
        "skills",
        "attempt",
    ):
        if key in child:
            projected[key] = child[key]
    return redact_obj(projected)


def _fanout_progress(
    children: list[dict],
    *,
    lane_projection: dict | None = None,
) -> dict:
    total = len(children)
    terminal = {
        "completed",
        "passed",
        "failed",
        "blocked",
        "cancelled",
        "timed_out",
        "suspended",
    }
    done = sum(
        1
        for child in children
        if str(child.get("status") or "") in terminal
    )
    failed = sum(
        1
        for child in children
        if str(child.get("status") or "") in {"failed", "blocked", "timed_out"}
    )
    progress = {
        "done": done,
        "total": total,
        "failed": failed,
        "pending": max(total - done, 0),
        "percent": round((done / total) * 100, 1) if total else 0.0,
    }
    if lane_projection:
        planned = int(lane_projection.get("planned_lane_count") or 0)
        progress["active_total"] = total
        if planned:
            progress["planned_total"] = planned
        progress["lane_scope"] = str(lane_projection.get("scope") or "")
    return progress


def _fanout_lane_projection(
    manifest: dict,
    children: list[dict],
    *,
    config: ZfConfig | None = None,
) -> dict:
    stage_id = str(manifest.get("stage_id") or "")
    stage = _workflow_stage(config, stage_id)
    assignment = getattr(stage, "assignment", None) if stage is not None else None
    strategy = (
        str(getattr(assignment, "strategy", "") or "")
        or _first_nonempty([_child_str(child, "assignment_strategy") for child in children])
        or ""
    )
    lane_profile = (
        str(getattr(assignment, "lane_profile", "") or "")
        or _first_nonempty([_child_str(child, "lane_profile") for child in children])
        or ""
    )
    stage_slot = (
        str(getattr(assignment, "stage_slot", "") or "")
        or _first_nonempty([_child_str(child, "stage_slot") for child in children])
        or ""
    )
    planned_roles, planned_lane_ids, planned_source = _configured_lane_roles(
        config,
        lane_profile=lane_profile,
        stage_slot=stage_slot,
    )
    if not planned_roles and stage is not None:
        planned_roles = [
            str(role)
            for role in getattr(stage, "roles", []) or []
            if str(role).strip()
        ]
        if planned_roles:
            planned_source = "stage_roles"
    active_roles = sorted({
        _child_str(child, "role_instance")
        for child in children
        if _child_str(child, "role_instance")
    })
    active_lane_ids = sorted({
        _child_str(child, "lane_id")
        for child in children
        if _child_str(child, "lane_id")
    })
    active_task_ids = sorted({
        _child_str(child, "task_id")
        for child in children
        if _child_str(child, "task_id")
    })
    planned_lane_count = len(planned_roles)
    active_lane_count = len(active_lane_ids) or len(active_roles)
    active_child_count = len(children)
    effective_active = active_lane_count or active_child_count
    scope = "observed"
    if active_child_count == 0:
        scope = "empty"
    elif planned_lane_count and effective_active < planned_lane_count:
        scope = (
            "scoped_reverify"
            if stage_slot == "verify" or "verify" in stage_id
            else "scoped"
        )
    elif planned_lane_count and effective_active >= planned_lane_count:
        scope = "full"
    return {
        "strategy": strategy,
        "lane_profile": lane_profile,
        "stage_slot": stage_slot,
        "planned_lane_count": planned_lane_count,
        "active_lane_count": active_lane_count,
        "active_child_count": active_child_count,
        "planned_roles": planned_roles,
        "active_roles": active_roles,
        "planned_lane_ids": planned_lane_ids,
        "active_lane_ids": active_lane_ids,
        "active_task_ids": active_task_ids,
        "scope": scope,
        "is_scoped": scope in {"scoped", "scoped_reverify"},
        "source": planned_source or ("manifest" if children else ""),
    }


def _workflow_stage(config: ZfConfig | None, stage_id: str) -> Any | None:
    if config is None or not stage_id:
        return None
    workflow = getattr(config, "workflow", None)
    for stage in getattr(workflow, "stages", []) or []:
        if str(getattr(stage, "id", "") or "") == stage_id:
            return stage
    return None


def _configured_lane_roles(
    config: ZfConfig | None,
    *,
    lane_profile: str,
    stage_slot: str,
) -> tuple[list[str], list[str], str]:
    if config is None or not lane_profile or not stage_slot:
        return [], [], ""
    workflow = getattr(config, "workflow", None)
    profiles = getattr(workflow, "affinity_lanes", {}) or {}
    profile = profiles.get(lane_profile) if isinstance(profiles, dict) else None
    if profile is None:
        return [], [], ""
    roles: list[str] = []
    lane_ids: list[str] = []
    for lane in getattr(profile, "lanes", []) or []:
        lane_id = str(getattr(lane, "id", "") or "")
        role = str(getattr(lane, stage_slot, "") or "")
        if lane_id:
            lane_ids.append(lane_id)
        if role:
            roles.append(role)
    return roles, lane_ids, "config_affinity_lanes" if roles else ""


def _child_str(child: dict, key: str) -> str:
    value = child.get(key)
    if (value is None or value == "") and isinstance(child.get("payload"), dict):
        value = child["payload"].get(key)
    return str(value or "").strip()


def _fanout_trigger_projection(
    manifest: dict,
    events: list[tuple[int, object]],
) -> dict:
    requested = None
    started = None
    for seq, event in events:
        event_type = getattr(event, "type", "")
        if event_type == "fanout.requested" and requested is None:
            requested = (seq, event)
        if event_type == "fanout.started" and started is None:
            started = (seq, event)

    requested_payload = getattr(requested[1], "payload", {}) if requested else {}
    started_payload = getattr(started[1], "payload", {}) if started else {}
    return {
        "requested_by": (
            _payload_ref(requested_payload, "requested_by")
            if isinstance(requested_payload, dict) else ""
        ) or (getattr(requested[1], "actor", "") if requested else ""),
        "started_by": getattr(started[1], "actor", "") if started else "",
        "triggered_by": (
            str(manifest.get("trigger_event_id") or "")
            or (
                _payload_ref(started_payload, "trigger_event_id")
                if isinstance(started_payload, dict) else ""
            )
            or (
                _payload_ref(requested_payload, "trigger_event_id")
                if isinstance(requested_payload, dict) else ""
            )
            or ""
        ),
        "request_event_id": getattr(requested[1], "id", "") if requested else "",
        "start_event_id": getattr(started[1], "id", "") if started else "",
    }


def _candidates(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    grouped: dict[str, dict] = {}
    for seq, event in _events_with_seq(state_dir, config=config):
        payload = getattr(event, "payload", {}) or {}
        candidate = _first_nonempty([
            _payload_ref(payload, "pdd_id"),
            _payload_ref(payload, "candidate_id"),
            _payload_ref(payload, "candidate_ref"),
            _payload_ref(payload, "candidate_branch"),
            _payload_ref(payload, "branch"),
        ])
        if not candidate and "candidate" not in getattr(event, "type", ""):
            continue
        candidate_id = str(candidate or getattr(event, "correlation_id", None) or "candidate")
        item = grouped.setdefault(candidate_id, {
            "pdd_id": candidate_id,
            "candidate_ref": "",
            "last_seq": 0,
            "last_type": "",
            "tasks": set(),
            "status": "observed",
        })
        item["last_seq"] = seq
        item["last_type"] = getattr(event, "type", "")
        if event.type == "candidate.ready":
            item["status"] = "ready"
        elif event.type == "candidate.conflict":
            item["status"] = "conflict"
        elif event.type == "candidate.updated":
            item["status"] = "updated"
        if getattr(event, "task_id", None):
            item["tasks"].add(event.task_id)
        if candidate_id.startswith("candidate/"):
            item["candidate_ref"] = candidate_id
        candidate_ref = _first_nonempty([
            _payload_ref(payload, "candidate_ref"),
            _payload_ref(payload, "candidate_branch"),
        ])
        if isinstance(candidate_ref, str) and candidate_ref.startswith("candidate/"):
            item["candidate_ref"] = candidate_ref
        branch = _payload_ref(payload, "branch")
        if isinstance(branch, str) and branch.startswith("candidate/"):
            item["candidate_ref"] = branch
        included = payload.get("included_tasks") if isinstance(payload, dict) else None
        if isinstance(included, list):
            for task in included:
                if isinstance(task, dict) and task.get("task_id"):
                    item["tasks"].add(str(task["task_id"]))
    for manifest_path in sorted((state_dir / "candidates").glob("*/manifest.json")):
        manifest = _read_json_file(manifest_path)
        if not isinstance(manifest, dict):
            continue
        pdd_id = str(manifest.get("pdd_id") or manifest_path.parent.name)
        item = grouped.setdefault(pdd_id, {
            "pdd_id": pdd_id,
            "candidate_ref": "",
            "last_seq": 0,
            "last_type": "candidate.manifest",
            "tasks": set(),
            "status": "observed",
        })
        item["candidate_ref"] = str(manifest.get("branch") or item["candidate_ref"])
        item["status"] = str(manifest.get("status") or item["status"])
        included = manifest.get("included_tasks")
        if isinstance(included, list):
            for task in included:
                if isinstance(task, dict) and task.get("task_id"):
                    item["tasks"].add(str(task["task_id"]))
    out = []
    for item in grouped.values():
        out.append({
            **item,
            "tasks": sorted(item["tasks"]),
            "ship_ready": item["status"] in {"ready", "passed", "approved"},
        })
    out.sort(key=lambda x: x["last_seq"], reverse=True)
    return out[:80]


def _candidate_manifest(state_dir: Path, pdd_id: str) -> dict:
    if "/" in pdd_id or "\\" in pdd_id or pdd_id.startswith("."):
        return {}
    manifest_path = state_dir / "candidates" / pdd_id / "manifest.json"
    manifest = _read_json_file(manifest_path)
    return manifest if isinstance(manifest, dict) else {}


def _fanout_manifest(state_dir: Path, fanout_id: str) -> dict:
    if "/" in fanout_id or "\\" in fanout_id or fanout_id.startswith("."):
        return {}
    manifest_path = state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest = _read_json_file(manifest_path)
    return manifest if isinstance(manifest, dict) else {}


def _fanouts(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    from zf.web.projections.tasks import _task_index_with_archive  # deferred: import cycle
    from zf.runtime.fanout import reconcile_fanout_manifest_terminal_state

    grouped: dict[str, dict] = {}
    task_index = _task_index_with_archive(state_dir)
    event_rows = list(_events_with_seq(state_dir, config=config))
    events = [event for _seq, event in event_rows]
    for seq, event in event_rows:
        payload = getattr(event, "payload", {}) or {}
        fanout_id = _first_nonempty([
            _payload_ref(payload, "fanout_id"),
            _payload_ref(payload, "fanout"),
            _payload_ref(payload, "parent_run"),
        ])
        if not fanout_id and "fanout" not in getattr(event, "type", ""):
            continue
        key = str(fanout_id or getattr(event, "correlation_id", None) or "fanout")
        item = grouped.setdefault(key, {
            "fanout_id": key,
            "last_seq": 0,
            "last_type": "",
            "children": set(),
            "tasks": set(),
            "topology": "",
            "stage_id": "",
            "target_ref": "",
            "trace_id": "",
            "pdd_id": "",
            "status": "observed",
        })
        item["last_seq"] = seq
        item["last_type"] = getattr(event, "type", "")
        item["topology"] = item["topology"] or str(_payload_ref(payload, "topology") or "")
        item["stage_id"] = item["stage_id"] or str(_payload_ref(payload, "stage_id") or "")
        item["target_ref"] = item["target_ref"] or str(_payload_ref(payload, "target_ref") or "")
        item["trace_id"] = item["trace_id"] or str(
            _payload_ref(payload, "trace_id") or getattr(event, "correlation_id", "") or ""
        )
        item["pdd_id"] = item["pdd_id"] or str(_payload_ref(payload, "pdd_id") or "")
        if getattr(event, "type", "") == "fanout.requested":
            item["status"] = "requested"
        elif getattr(event, "type", "") == "fanout.started":
            item["status"] = "started"
        elif getattr(event, "type", "") in {
            "fanout.aggregate.completed",
            "fanout.timed_out",
            "fanout.cancelled",
        }:
            item["status"] = str(_payload_ref(payload, "status") or "").strip() or (
                "timed_out" if getattr(event, "type", "") == "fanout.timed_out"
                else "cancelled" if getattr(event, "type", "") == "fanout.cancelled"
                else "completed"
            )
        child = _first_nonempty([
            _payload_ref(payload, "child_id"),
            _payload_ref(payload, "child_run"),
            _payload_ref(payload, "child"),
        ])
        if not child and "child" in getattr(event, "type", ""):
            child = getattr(event, "task_id", None)
        if child:
            item["children"].add(str(child))
        if getattr(event, "task_id", None):
            item["tasks"].add(event.task_id)
    for manifest_path in sorted((state_dir / "fanouts").glob("*/manifest.json")):
        manifest = _read_json_file(manifest_path)
        if not isinstance(manifest, dict):
            continue
        manifest = reconcile_fanout_manifest_terminal_state(dict(manifest), events)
        fanout_id = str(manifest.get("fanout_id") or manifest_path.parent.name)
        item = grouped.setdefault(fanout_id, {
            "fanout_id": fanout_id,
            "last_seq": 0,
            "last_type": "fanout.manifest",
            "children": set(),
            "tasks": set(),
            "topology": "",
            "stage_id": "",
            "target_ref": "",
            "trace_id": "",
            "pdd_id": "",
            "status": "observed",
        })
        item["last_type"] = "fanout.manifest"
        item["topology"] = str(manifest.get("topology") or item["topology"])
        item["stage_id"] = str(manifest.get("stage_id") or item["stage_id"])
        item["target_ref"] = str(manifest.get("target_ref") or item["target_ref"])
        item["trace_id"] = str(manifest.get("trace_id") or item["trace_id"])
        item["pdd_id"] = str(manifest.get("pdd_id") or item["pdd_id"])
        item["status"] = str(manifest.get("status") or item["status"])
        for child in manifest.get("children", []) or []:
            if isinstance(child, dict) and child.get("child_id"):
                item["children"].add(str(child["child_id"]))
                if child.get("task_id"):
                    item["tasks"].add(str(child["task_id"]))
        projected_children = [
            _fanout_child_projection(child, task_index=task_index)
            for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        lane_projection = _fanout_lane_projection(
            manifest,
            projected_children,
            config=config,
        )
        item["lane_projection"] = lane_projection
        item["progress"] = _fanout_progress(
            projected_children,
            lane_projection=lane_projection,
        )
    out = []
    for item in grouped.values():
        progress = item.get("progress")
        if not isinstance(progress, dict):
            progress = {
                "done": 0,
                "total": len(item["children"]),
                "failed": 0,
                "pending": len(item["children"]),
                "percent": 0.0,
            }
        out.append({
            **item,
            "children": sorted(item["children"]),
            "tasks": sorted(item["tasks"]),
            "progress": progress,
            "lane_projection": (
                item.get("lane_projection")
                if isinstance(item.get("lane_projection"), dict)
                else {}
            ),
        })
    out.sort(key=lambda x: x["last_seq"], reverse=True)
    return out[:80]


def _requested_fanout_id(stage_id: str, payload: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8"),
    ).hexdigest()[:12]
    return f"fanout-request-{_safe_token(stage_id)}-{digest}"
