"""Projections layer: summaries (moved verbatim from web/server.py)."""
from __future__ import annotations

from dataclasses import asdict
from fastapi import HTTPException
from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.feature.store import FeatureStore
from zf.core.safety import PathGuard
from zf.core.security.redaction import redact_obj
from zf.core.skills.provenance import LOCKFILE_NAME
from zf.core.skills.provenance import read_skill_metadata
from zf.core.skills.provenance import resolve_skill
from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.kanban_projection import workflow_projection
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_archive import RunArchiveError
from zf.runtime.run_archive import validate_run_id
import json
from zf.web.projections.common import _REF_EVENT_KEYS, _payload_collect, _payload_ref, _read_json_file, _resolve_project_root_for_state, _sha256_file


def _safe_snapshot_projection(name: str, default: Any, factory: Any) -> Any:
    try:
        return factory()
    except Exception as exc:
        if isinstance(default, dict):
            return {"projection": name, "error": str(exc)}
        return default


def _provider_health_projection(state_dir: Path) -> dict:
    try:
        from zf.runtime.provider_health import project_provider_health

        return project_provider_health(state_dir)
    except Exception as exc:
        return {
            "schema_version": "provider-health.v1",
            "status": "unknown",
            "providers": [],
            "error": str(exc),
        }


def _safe_task_progress_projection(state_dir: Path, task_id: str) -> dict:
    try:
        from zf.runtime.progress_projection import project_task_progress

        return project_task_progress(state_dir, task_id)
    except Exception as exc:
        return {
            "schema_version": "task-progress.v1",
            "task_id": task_id,
            "current_phase": "",
            "timeline": [],
            "diagnostics": [{"type": "projection_error", "message": str(exc)}],
        }


def _safe_task_capsule_projection(state_dir: Path, task: Task) -> dict:
    try:
        from zf.runtime.task_doc import (
            compute_task_capsule_revisions,
            task_doc_path,
            task_evidence_path,
            task_progress_path,
            task_source_path,
            verify_task_capsule,
        )

        revisions = compute_task_capsule_revisions(task)
        errors = verify_task_capsule(state_dir, task)
        paths = {
            "source": task_source_path(state_dir, task.id),
            "task": task_doc_path(state_dir, task.id),
            "progress": task_progress_path(state_dir, task.id),
            "evidence": task_evidence_path(state_dir, task.id),
        }
        return {
            "schema_version": "task-capsule.v1",
            "task_id": task.id,
            "fresh": not errors,
            "errors": errors,
            "paths": {key: str(value) for key, value in paths.items()},
            "exists": {key: value.exists() for key, value in paths.items()},
            "revisions": revisions,
            "active_dispatch_id": task.active_dispatch_id or "",
        }
    except Exception as exc:
        return {
            "schema_version": "task-capsule.v1",
            "task_id": getattr(task, "id", ""),
            "fresh": False,
            "errors": ["projection_error"],
            "error": str(exc),
        }


def _safe_task_operations_projection(
    state_dir: Path,
    task_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict:
    try:
        from zf.web.projections.operations import task_operations

        return task_operations(state_dir, task_id, config=config)
    except Exception as exc:
        return {
            "schema_version": "task-operations.v1",
            "task_id": task_id,
            "operations": [],
            "error": str(exc),
        }


def _safe_task_run_panel_projection(**kwargs) -> dict:
    task = kwargs.get("task")
    task_id = getattr(task, "id", "") if task is not None else ""
    try:
        from zf.runtime.task_run_panel import project_task_run_panel

        return project_task_run_panel(**kwargs)
    except Exception as exc:
        return {
            "schema_version": "task-run-panel.v1",
            "task_id": task_id,
            "active_operation": None,
            "latest_progress": {},
            "route_summary": {},
            "workdir": {},
            "health": {},
            "counts": {},
            "source_event_ids": [],
            "empty": True,
            "error": str(exc),
        }


def _safe_handoff_summary_projection(
    state_dir: Path,
    task_id: str,
    **kwargs,
) -> dict:
    try:
        from zf.runtime.handoff_summary import project_handoff_summary

        return project_handoff_summary(state_dir, task_id, **kwargs)
    except Exception as exc:
        return {
            "schema_version": "handoff-summary.v1",
            "task_id": task_id,
            "objective": "",
            "current_state": "",
            "current_stage": "",
            "owner": {},
            "completed": [],
            "missing_evidence": [],
            "blockers": [],
            "next_required_event": "",
            "next_required_action": "",
            "do_not_repeat": [],
            "evidence_refs": [],
            "changed_files": [],
            "resume_packet_ref": "",
            "source_event_ids": [],
            "empty": True,
            "error": str(exc),
        }


def _metrics_snapshot_projection(state_dir: Path) -> dict:
    """Kernel 12-metric snapshot (MetricsCollector stays the only computer)."""
    from zf.core.metrics.collector import MetricsCollector

    return MetricsCollector.compute(
        events=EventLog(state_dir / "events.jsonl"),
        tasks=TaskStore(state_dir / "kanban.json"),
        cost=CostTracker(state_dir / "cost.jsonl"),
    ).to_dict()


def _features(state_dir: Path, delivery_features: list[dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    path = state_dir / "feature_list.json"
    if path.exists():
        fs = FeatureStore(path)
        for f in fs.list_all():
            rows.append({
                "id": f.id,
                "title": f.title,
                "status": f.status,
                "priority": getattr(f, "priority", 3),
            })
            seen.add(f.id)
    if delivery_features is None:
        delivery_features = _delivery_features(state_dir)
    for feature in delivery_features:
        fid = str(feature.get("id") or "")
        if fid and fid not in seen:
            rows.append(feature)
            seen.add(fid)
    return rows


def _delivery_features(state_dir: Path) -> list[dict]:
    index = _feature_index(state_dir)
    statuses = _delivery_feature_statuses(state_dir)
    rows: list[dict] = []
    seen: set[str] = set()
    for feature_id, entry in sorted(index.items()):
        if not isinstance(entry, dict):
            continue
        fid = str(entry.get("feature_id") or feature_id or "").strip()
        if not fid:
            continue
        bundle = entry.get("current_bundle")
        if not isinstance(bundle, dict):
            bundle = {}
        rows.append({
            "id": fid,
            "title": _delivery_feature_title(fid, entry, bundle),
            "status": statuses.get(fid, "active"),
            "priority": int(entry.get("priority") or 3),
            "source": "feature-index",
            "current_task_map_ref": str(
                bundle.get("current_task_map_ref")
                or entry.get("current_task_map_ref")
                or ""
            ),
            "current_source_index_ref": str(
                bundle.get("current_source_index_ref")
                or entry.get("current_source_index_ref")
                or ""
            ),
            "bundle_history_count": len(entry.get("bundle_history") or []),
        })
        seen.add(fid)
    rows.extend(_delivery_feature_fallbacks(state_dir, statuses=statuses, seen=seen))
    return rows


def _feature_index(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "refs" / "feature-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _feature_list_ids(state_dir: Path) -> set[str]:
    path = state_dir / "feature_list.json"
    if not path.exists():
        return set()
    try:
        return {str(feature.id) for feature in FeatureStore(path).list_all() if str(feature.id or "").strip()}
    except Exception:
        return set()


def _delivery_feature_fallbacks(
    state_dir: Path,
    *,
    statuses: dict[str, str],
    seen: set[str],
) -> list[dict]:
    """Discover delivery targets when feature-index is absent.

    Refactor/fanout lanes can have rich delivery trace spans without
    ``feature_list.json`` or ``refs/feature-index.json``. This fallback keeps
    the Web Delivery page discoverable without minting feature truth.
    """

    if _feature_list_ids(state_dir):
        return []
    try:
        tasks = TaskStore(state_dir / "kanban.json").list_all_with_archive()
    except Exception:
        tasks = []
    try:
        from zf.web.projections.events import _events_with_seq

        events = list(_events_with_seq(state_dir))
    except Exception:
        events = []
    runtime_statuses = _delivery_runtime_statuses(events)
    candidates: dict[str, dict[str, Any]] = {}

    def add(
        target_id: str,
        *,
        title: str,
        source: str,
        confidence: float,
        task_id: str = "",
        trace_ref: str = "",
        fanout_ref: str = "",
        event_id: str = "",
    ) -> None:
        fid = str(target_id or "").strip()
        if not fid or fid in seen:
            return
        current = candidates.get(fid)
        if current and float(current.get("confidence") or 0) >= confidence:
            return
        candidates[fid] = {
            "id": fid,
            "title": title or fid,
            "status": statuses.get(fid, runtime_statuses.get(fid, "active")),
            "priority": 3,
            "source": f"fallback:{source}",
            "confidence": confidence,
            "trace_ref": trace_ref,
            "fanout_ref": fanout_ref,
            "task_id": task_id,
            "event_id": event_id,
            "degraded": True,
            "reason": "discovered from runtime refs; feature-index is absent",
        }

    for task in tasks:
        feature_id = str(getattr(getattr(task, "contract", None), "feature_id", "") or "").strip()
        if feature_id:
            add(
                feature_id,
                title=getattr(task, "title", "") or feature_id,
                source="task-contract",
                confidence=0.95,
                task_id=getattr(task, "id", ""),
            )

    task_by_id = {getattr(task, "id", ""): task for task in tasks}
    grouped_events: dict[str, list[tuple[int, object]]] = {}
    for seq, event in events:
        task_id = str(getattr(event, "task_id", "") or _payload_ref(getattr(event, "payload", {}) or {}, "task_id") or "")
        if task_id:
            grouped_events.setdefault(task_id, []).append((seq, event))
    for task_id, grouped in grouped_events.items():
        refs = _refs_from_events(grouped, task=task_by_id.get(task_id), state_dir=state_dir)
        title = getattr(task_by_id.get(task_id), "title", "") or task_id
        if refs.get("feature_id"):
            add(str(refs["feature_id"]), title=title, source="event-feature-ref", confidence=0.9, task_id=task_id)
        if refs.get("pdd_id"):
            add(str(refs["pdd_id"]), title=f"Candidate {refs['pdd_id']}", source="candidate-ref", confidence=0.74, task_id=task_id, trace_ref=str(refs.get("pdd_id") or ""))
        if refs.get("fanout_id"):
            add(str(refs["fanout_id"]), title=f"Fanout {refs['fanout_id']}", source="fanout-ref", confidence=0.7, task_id=task_id, fanout_ref=str(refs.get("fanout_id") or ""))
        trace_ref = next((str(getattr(event, "correlation_id", "") or "") for _, event in reversed(grouped) if getattr(event, "correlation_id", "")), "")
        if trace_ref:
            add(trace_ref, title=f"Trace {trace_ref}", source="trace-ref", confidence=0.66, task_id=task_id, trace_ref=trace_ref)

    if not candidates and (tasks or grouped_events):
        project_target = state_dir.parent.name or "delivery"
        add(
            project_target,
            title="Project delivery trace",
            source="project-runtime",
            confidence=0.5,
            trace_ref=project_target,
        )

    rows = sorted(
        candidates.values(),
        key=lambda item: (-float(item.get("confidence") or 0), str(item.get("id") or "")),
    )
    for row in rows:
        seen.add(str(row.get("id") or ""))
    return rows[:12]


def _delivery_runtime_statuses(
    events: list[tuple[int, object]],
) -> dict[str, str]:
    """Derive fallback target state from ordered terminal runtime facts."""

    statuses: dict[str, str] = {}
    fanout_states = {
        "fanout.started": "active",
        "fanout.aggregate.completed": "done",
        "fanout.cancelled": "cancelled",
        "fanout.timed_out": "blocked",
    }
    goal_states = {
        "run.goal.completed": "done",
        "run.goal.blocked": "blocked",
    }
    for _seq, event in events:
        event_type = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", {}) or {}
        fanout_id = str(_payload_ref(payload, "fanout_id") or "").strip()
        if fanout_id and event_type in fanout_states:
            statuses[fanout_id] = fanout_states[event_type]
        if event_type not in goal_states:
            continue
        state = goal_states[event_type]
        for value in (
            _payload_ref(payload, "pdd_id"),
            _payload_ref(payload, "feature_id"),
            _payload_ref(payload, "workflow_run_id"),
            _payload_ref(payload, "run_id"),
            getattr(event, "correlation_id", ""),
        ):
            identity = str(value or "").strip()
            if identity:
                statuses[identity] = state
    return statuses


def _delivery_feature_title(
    feature_id: str,
    entry: dict[str, Any],
    bundle: dict[str, Any],
) -> str:
    title = str(
        entry.get("title")
        or entry.get("name")
        or bundle.get("title")
        or bundle.get("plan_ref")
        or bundle.get("current_task_map_ref")
        or ""
    ).strip()
    return title or feature_id


def _delivery_feature_statuses(state_dir: Path) -> dict[str, str]:
    try:
        tasks = TaskStore(state_dir / "kanban.json").list_all_with_archive()
    except Exception:
        return {}
    by_feature: dict[str, list[str]] = {}
    for task in tasks:
        feature_id = str(getattr(task.contract, "feature_id", "") or "").strip()
        if not feature_id:
            key = str(getattr(task, "key", "") or "")
            if key.startswith("F-") and ":" in key:
                feature_id = key.split(":", 1)[0]
        if not feature_id:
            continue
        by_feature.setdefault(feature_id, []).append(str(task.status or ""))
    out: dict[str, str] = {}
    for feature_id, statuses in by_feature.items():
        if any(status not in {"done", "cancelled"} for status in statuses):
            out[feature_id] = "active"
        elif any(status == "done" for status in statuses):
            out[feature_id] = "done"
        else:
            out[feature_id] = "cancelled"
    return out


def _archive_tasks(state_dir: Path, *, include_active: bool = False) -> list[dict]:
    path = state_dir / "kanban.json"
    if not path.exists():
        return []
    try:
        tasks = TaskStore(path).list_all_with_archive()
    except Exception:
        return []
    rows = []
    for task in tasks:
        if not include_active and task.status not in {"done", "cancelled"}:
            continue
        workflow = workflow_projection(task).to_dict()
        projection = kanban_column_projection(task)
        row = redact_obj(asdict(task))
        row["kanban_column"] = projection.column
        row["kanban_column_label"] = projection.label
        row["kanban_column_reason"] = projection.reason
        row["kanban_column_badges"] = list(projection.badges)
        row["workflow_phase"] = workflow["workflow_phase"]
        row["impl_exit_gate_state"] = workflow["impl_exit_gate_state"]
        row["verify_state"] = workflow["verify_state"]
        row["judge_state"] = workflow["judge_state"]
        row["verify_lanes"] = workflow["verify_lanes"]
        row["workflow_badges"] = workflow["badges"]
        row["workflow_projection"] = workflow
        row["terminal"] = task.status in {"done", "cancelled"}
        row["terminal_outcome"] = (
            "success" if task.status == "done"
            else "cancelled" if task.status == "cancelled"
            else ""
        )
        rows.append(row)
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return rows


def _safe_run_dir(state_dir: Path, run_id: str) -> Path:
    try:
        safe = validate_run_id(run_id)
    except RunArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc
    runs_root = state_dir / "runs"
    try:
        return PathGuard.assert_under(runs_root / safe, runs_root)
    except Exception as exc:
        raise HTTPException(400, "invalid run path") from exc


def _safe_session_segment(value: str) -> str:
    safe = []
    for char in str(value or ""):
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        else:
            safe.append("-")
    result = "".join(safe).strip("-")
    return result or "unknown"


def _skills(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    roles = list(config.roles) if config is not None else []
    pool_path = state_dir / "skills"
    materialize_mode = "copy"
    lock_path = state_dir / LOCKFILE_NAME
    if config is not None:
        raw_pool = Path(config.runtime.skills.pool)
        pool_path = raw_pool if raw_pool.is_absolute() else project_root / raw_pool
        materialize_mode = config.runtime.skills.materialize
        raw_lock = Path(config.runtime.skills.lock_file)
        lock_path = raw_lock if raw_lock.is_absolute() else project_root / raw_lock

    enabled_by_role: dict[str, list[str]] = {}
    for role in roles:
        for skill in role.skills:
            enabled_by_role.setdefault(skill, []).append(role.instance_id)

    pool = []
    pool_sha_by_name: dict[str, str] = {}
    if pool_path.exists():
        for child in sorted(pool_path.iterdir()):
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                metadata = read_skill_metadata(skill_md, expected_name=child.name)
                sha256 = _sha256_file(skill_md)
                warnings = list(metadata.warnings)
            except Exception as exc:
                metadata = None
                sha256 = ""
                warnings = [str(exc)]
            pool_sha_by_name[child.name] = sha256
            pool.append({
                "name": child.name,
                "path": str(child),
                "description": metadata.description if metadata is not None else "",
                "sha256": sha256,
                "enabled_by": enabled_by_role.get(child.name, []),
                "warnings": warnings,
            })

    lock = _read_json_file(lock_path)
    lock_entries = list(lock.get("skills", []) or []) if isinstance(lock, dict) else []
    manifests = []
    hash_warnings = []
    for manifest in sorted((state_dir / "workdirs").glob("*/runtime/skills-manifest.json")):
        data = _read_json_file(manifest)
        if data:
            manifests.append(redact_obj(data))
            for skill in data.get("skills", []) or []:
                if not isinstance(skill, dict):
                    continue
                name = str(skill.get("name") or "")
                manifest_sha = str(skill.get("sha256") or "")
                current_sha = pool_sha_by_name.get(name, "")
                if name and manifest_sha and current_sha and manifest_sha != current_sha:
                    hash_warnings.append({
                        "role": data.get("instance_id") or data.get("role") or "",
                        "skill": name,
                        "status": "materialized_hash_mismatch",
                        "materialized_sha256": manifest_sha,
                        "current_sha256": current_sha,
                    })

    for entry in lock_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        locked_sha = str(entry.get("sha256") or "")
        current_sha = pool_sha_by_name.get(name, "")
        if name and locked_sha and current_sha and locked_sha != current_sha:
            hash_warnings.append({
                "role": entry.get("instance_id") or entry.get("role") or "",
                "skill": name,
                "status": "lock_hash_mismatch",
                "locked_sha256": locked_sha,
                "current_sha256": current_sha,
            })

    loaded = [
        {
            "role": str(entry.get("instance_id") or entry.get("role") or ""),
            "role_type": str(entry.get("role") or ""),
            "task_id": str(entry.get("task_id") or ""),
            "run_id": str(entry.get("run_id") or ""),
            "name": str(entry.get("name") or ""),
            "source": str(entry.get("source_name") or entry.get("source") or ""),
            "status": str(entry.get("status") or ""),
            "materialized_to": str(entry.get("materialized_to") or ""),
            "warnings": list(entry.get("warnings", []) or []),
        }
        for entry in lock_entries
        if isinstance(entry, dict)
    ]

    missing_enabled = []
    if config is not None:
        for role in roles:
            for skill in role.skills:
                resolution = resolve_skill(
                    project_root=project_root,
                    state_dir=state_dir,
                    name=skill,
                    config=config,
                )
                if resolution.path is None:
                    missing_enabled.append({
                        "role": role.instance_id,
                        "skill": skill,
                        "status": "missing",
                    })

    return {
        "pool_path": str(pool_path),
        "materialize": materialize_mode,
        "lock_file": str(lock_path),
        "pool": pool,
        "enabled": [
            {
                "role": role.instance_id,
                "role_name": role.name,
                "backend": role.backend,
                "skills": list(role.skills),
            }
            for role in roles
        ],
        "loaded": redact_obj(loaded),
        "lock": redact_obj(lock_entries),
        "manifests": manifests,
        "warnings": missing_enabled + hash_warnings,
    }


def _safe_token(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in "._-" else "-"
        for ch in value
    ).strip("-") or "unknown"


def _refs_from_events(
    events: list[tuple[int, object]],
    *,
    task: object | None = None,
    state_dir: Path | None = None,
    config: ZfConfig | None = None,
) -> dict:
    refs: dict[str, Any] = {}
    if task is not None:
        evidence = getattr(task, "evidence", None)
        if evidence is not None:
            commit = getattr(evidence, "commit", "")
            if commit:
                refs["commit"] = commit
                refs["source_commit"] = commit
            commits = getattr(evidence, "commits", [])
            if commits:
                refs["commits"] = list(commits)
            files = getattr(evidence, "files_touched", [])
            if files:
                refs["files_touched"] = list(files)

    kv = None
    if state_dir is not None:
        try:
            from zf.web.projections.events import event_ref_kv

            kv = event_ref_kv(state_dir, config=config)
        except Exception:
            kv = None
    for seq, event in events:
        if kv is not None:
            hit = kv.get(seq)
            if hit is not None and hit[0] is event:
                refs.update(hit[1])
                continue
        payload = getattr(event, "payload", {}) or {}
        for key, value in _payload_collect(payload, _REF_EVENT_KEYS).items():
            if value not in (None, ""):
                refs[key] = value
    if "worker_branch" not in refs and "branch" in refs:
        refs["worker_branch"] = refs["branch"]
    if "role_instance" not in refs and "instance_id" in refs:
        refs["role_instance"] = refs["instance_id"]
    return redact_obj(refs)
