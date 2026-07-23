"""Delivery-trace resolver — load runtime truth from disk, then project.

The pure builders (``delivery_trace`` / ``execution_graph`` / ``drift_report``)
take already-read inputs. This module is the read-only loading layer (doc 65
§7.1): resolve feature_id, load kanban tasks + events + the accepted task-map,
then compose. It performs **no writes** — only ``EventLog.read_all`` and
``TaskStore`` archive-aware reads (so the projection-boundary invariant holds: a
``zf trace delivery`` call never mutates ``.zf/``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.factory import event_log_from_project
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.delivery_trace import build_delivery_trace
from zf.runtime.delivery_run_trace import build_delivery_run_projection
from zf.runtime.drift_report import build_drift_report
from zf.runtime.execution_graph import build_execution_graph
from zf.runtime.goal_coverage_graph import (
    build_goal_coverage_graph,
    degraded_goal_coverage_graph,
)
from zf.runtime.workflow_trace import build_workflow_trace

_IDEA_TYPES = ("feature.created", "user.message")


def resolve_delivery_trace(
    *,
    state_dir: Path,
    config: Any,
    generated_at: str,
    project_id: str = "",
    feature_id: str = "",
    task_id: str = "",
    task_map_ref: str = "",
) -> dict[str, Any]:
    """Load kanban + events + accepted task-map and build delivery-trace.v1."""
    inp = _load_feature_inputs(
        state_dir, config=config, feature_id=feature_id, task_id=task_id,
        task_map_ref=task_map_ref,
    )
    graph = build_execution_graph(
        task_map=inp["task_map"], tasks=inp["tasks"], events=inp["events"],
        feature_id=inp["feature_id"], task_map_ref=inp["ref"],
    )
    if inp["diagnostics"]:
        graph["diagnostics"] = list(graph.get("diagnostics", [])) + inp["diagnostics"]
    drift = build_drift_report(graph=graph, events=inp["events"])
    trace = build_delivery_trace(
        feature_id=inp["feature_id"], generated_at=generated_at, tasks=inp["tasks"],
        task_map=inp["task_map"], idea=_idea(inp["all_events"], feature_id=inp["feature_id"]),
        plan=_plan(inp["tasks"]), events=inp["events"],
        project_id=project_id, task_map_ref=inp["ref"], drift_report=drift,
    )
    workflow_trace = build_workflow_trace(
        config=config,
        events=inp["events"],
        tasks=inp["tasks"],
        feature_id=inp["feature_id"],
        task_map_ref=inp["ref"],
        project_id=project_id,
    )
    trace["workflow_trace"] = workflow_trace
    trace.update(build_delivery_run_projection(
        config=config,
        events=inp["events"],
        tasks=inp["tasks"],
        workflow_trace=workflow_trace,
        execution_graph=trace.get("execution_graph", {}),
        autoresearch_cycles=trace.get("autoresearch_cycles", []),
    ))
    try:
        trace["goal_coverage_graph"] = build_goal_coverage_graph(
            task_map=inp["task_map"],
            tasks=inp["tasks"],
            events=inp["events"],
            project_id=project_id,
            feature_id=inp["feature_id"],
            task_map_ref=inp["ref"],
        )
    except Exception as exc:
        # A read projection may degrade, but must never block Delivery Trace or
        # alter canonical task/closure state.
        trace["goal_coverage_graph"] = degraded_goal_coverage_graph(
            project_id=project_id,
            feature_id=inp["feature_id"],
            reason=f"{type(exc).__name__}: {exc}",
        )
    if inp["diagnostics"]:
        trace["diagnostics"] = list(trace.get("diagnostics", [])) + inp["diagnostics"]
    if inp["bundle"]:
        trace["current_bundle"] = inp["bundle"]
        trace["current_task_map_ref"] = inp["bundle"].get("current_task_map_ref", "")
    return trace


def resolve_delivery_report(
    *, state_dir: Path, config: Any, generated_at: str,
    project_id: str = "", feature_id: str = "", task_id: str = "", task_map_ref: str = "",
) -> dict[str, Any]:
    """Build delivery-report.v1 (delivery-trace + post-mortem) for a feature."""
    from zf.runtime.delivery_report import build_delivery_report
    inp = _load_feature_inputs(state_dir, config=config, feature_id=feature_id,
                               task_id=task_id, task_map_ref=task_map_ref)
    trace = resolve_delivery_trace(
        state_dir=state_dir, config=config, generated_at=generated_at,
        project_id=project_id, feature_id=feature_id, task_id=task_id,
        task_map_ref=task_map_ref,
    )
    return build_delivery_report(trace=trace, events=inp["events"], generated_at=generated_at)


def resolve_execution_graph(
    *, state_dir: Path, config: Any, feature_id: str = "",
    task_id: str = "", task_map_ref: str = "",
) -> dict[str, Any]:
    """Load + build the full execution-graph.v1 (with schema_version)."""
    inp = _load_feature_inputs(state_dir, config=config, feature_id=feature_id,
                               task_id=task_id, task_map_ref=task_map_ref)
    graph = build_execution_graph(
        task_map=inp["task_map"], tasks=inp["tasks"], events=inp["events"],
        feature_id=inp["feature_id"], task_map_ref=inp["ref"],
    )
    if inp["diagnostics"]:
        graph["diagnostics"] = list(graph.get("diagnostics", [])) + inp["diagnostics"]
    return graph


def resolve_drift_report(
    *, state_dir: Path, config: Any, feature_id: str = "",
    task_id: str = "", task_map_ref: str = "",
) -> dict[str, Any]:
    """Load + build the full drift-report.v1 (with schema_version)."""
    inp = _load_feature_inputs(state_dir, config=config, feature_id=feature_id,
                               task_id=task_id, task_map_ref=task_map_ref)
    graph = build_execution_graph(
        task_map=inp["task_map"], tasks=inp["tasks"], events=inp["events"],
        feature_id=inp["feature_id"], task_map_ref=inp["ref"],
    )
    if inp["diagnostics"]:
        graph["diagnostics"] = list(graph.get("diagnostics", [])) + inp["diagnostics"]
    return build_drift_report(graph=graph, events=inp["events"])


def _load_feature_inputs(
    state_dir: Path, *, config: Any, feature_id: str, task_id: str, task_map_ref: str,
) -> dict[str, Any]:
    """Shared read-only loader: events + kanban tasks + accepted task-map."""
    event_log = event_log_from_project(state_dir, config=config)
    all_events = event_log.read_all()
    events = list(enumerate(all_events))

    all_tasks = TaskStore(state_dir / "kanban.json").list_all_with_archive()
    # feature_id resolution (doc 65 §20.2): explicit > task_id's contract.
    if not feature_id.strip() and task_id:
        task = next((t for t in all_tasks if t.id == task_id), None)
        if task is not None:
            feature_id = task.contract.feature_id

    tasks = _tasks_for_feature(all_tasks, feature_id=feature_id, task_id=task_id)
    ref, task_map, diagnostics, bundle = _resolve_task_map(
        state_dir, feature_id=feature_id, task_map_ref=task_map_ref,
    )
    return {
        "feature_id": feature_id, "tasks": tasks, "task_map": task_map,
        "ref": ref, "events": events, "all_events": all_events,
        "diagnostics": diagnostics, "bundle": bundle,
    }


def _tasks_for_feature(
    all_tasks: list[Task], *, feature_id: str, task_id: str,
) -> dict[str, Task]:
    if feature_id.strip():
        return {t.id: t for t in all_tasks if t.contract.feature_id == feature_id}
    if task_id:
        return {t.id: t for t in all_tasks if t.id == task_id}
    return {t.id: t for t in all_tasks}


def _resolve_task_map(
    state_dir: Path, *, feature_id: str, task_map_ref: str,
) -> tuple[str, dict[str, Any] | None, list[dict[str, str]], dict[str, Any]]:
    """Locate the accepted task-map. P0: explicit ref, else artifacts/<feature>/.

    Richer artifact-manifest-event resolution (doc 65 §7.1 fallbacks) is P2.
    """
    diagnostics: list[dict[str, str]] = []
    bundle: dict[str, Any] = {}
    candidates: list[Path] = []
    if task_map_ref.strip():
        candidates.extend(_candidate_paths(state_dir, task_map_ref))
    if feature_id.strip():
        bundle = _feature_current_bundle(state_dir, feature_id)
        bundle_ref = str(bundle.get("current_task_map_ref") or "").strip()
        if bundle_ref:
            bundle_candidates = _candidate_paths(state_dir, bundle_ref)
            candidates.extend(bundle_candidates)
            if not any(path.exists() for path in bundle_candidates):
                diagnostics.append({
                    "kind": "task_map_ref_unreadable",
                    "message": "feature current bundle task_map_ref is not readable",
                    "task_map_ref": bundle_ref,
                    "attempted_paths": ", ".join(str(path) for path in bundle_candidates),
                })
    if feature_id.strip():
        base = state_dir / "artifacts" / feature_id
        candidates.extend([base / "task_map.json", base / "task-map.json"])
        versioned = sorted(
            [
                path for path in base.glob("v*/task_map.json")
                if path.is_file()
            ],
            key=lambda path: _version_sort_key(path.parent.name),
            reverse=True,
        )
        candidates.extend(versioned)
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                return (task_map_ref or str(path)), data, diagnostics, bundle
    return task_map_ref, None, diagnostics, bundle


def _feature_current_bundle(state_dir: Path, feature_id: str) -> dict[str, Any]:
    path = state_dir / "refs" / "feature-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    entry = data.get(feature_id)
    if not isinstance(entry, dict):
        return {}
    bundle = entry.get("current_bundle")
    if isinstance(bundle, dict):
        return dict(bundle)
    legacy_ref = str(entry.get("current_task_map_ref") or "").strip()
    if legacy_ref:
        return {
            "schema_version": "feature-delivery-bundle.v1",
            "feature_id": feature_id,
            "current_task_map_ref": legacy_ref,
            "current_source_index_ref": str(entry.get("current_source_index_ref") or ""),
            "current_coverage_report_ref": str(entry.get("current_coverage_report_ref") or ""),
        }
    return {}


def _candidate_paths(state_dir: Path, ref: str) -> list[Path]:
    raw = str(ref or "").split("#", 1)[0].strip()
    if not raw:
        return []
    path = Path(raw)
    if path.is_absolute():
        return [path]
    candidates = [
        state_dir / raw,
        state_dir.parent / raw,
        Path.cwd() / raw,
    ]
    if path.parts:
        first = path.parts[0]
        if first in {state_dir.name, ".zf"} and len(path.parts) > 1:
            candidates.append(state_dir / Path(*path.parts[1:]))
        if first == "artifacts":
            candidates.append(state_dir / path)
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _version_sort_key(name: str) -> tuple[int, str]:
    text = str(name or "")
    if text.startswith("v"):
        try:
            return int(text[1:]), text
        except ValueError:
            pass
    return 0, text


def _idea(all_events: list, *, feature_id: str) -> dict[str, Any]:
    for event in all_events:
        if event.type not in _IDEA_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if feature_id and feature_id not in (
            str(payload.get("feature_id") or ""), str(event.task_id or ""),
        ):
            continue
        return {
            "event_id": event.id,
            "summary": str(payload.get("summary") or payload.get("message") or "")[:240],
            "source": event.type,
        }
    return {}


def _plan(tasks: dict[str, Task]) -> dict[str, Any]:
    """Best-effort plan refs from a representative task contract."""
    for task in tasks.values():
        c = task.contract
        if c.spec_ref or c.plan_ref or c.critic_event_id:
            return {
                "status": "accepted" if c.critic_event_id else "unknown",
                "spec_ref": c.spec_ref,
                "plan_ref": c.plan_ref,
                "tdd_ref": c.tdd_ref,
                "critic_event_id": c.critic_event_id,
            }
    return {}
