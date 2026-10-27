"""Exact, disposable Request/Run graph generations in read_model.sqlite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable

from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.runtime.workflow_requests import load_workflow_request, workflow_request_path
from zf.web.projections import read_model
from zf.web.projections.projection_failures import collect_projection_failures
from zf.web.projections.source_cache import fingerprint, task_paths
from zf.web.projections.workflow_workbench_sources import read_workbench_sources


class WorkbenchSourceChanged(RuntimeError):
    """No stable generation was available; do not serve mixed source state."""


def _paths(context, dependencies: set[str], request_id: str = "") -> list[Path]:
    state = context.state_dir
    paths = task_paths(state) + [context.project_root / "zf.yaml"]
    # Include directory identities and missing roots, so additions/deletions and
    # sidecar-only repairs invalidate a previously incomplete projection.
    if request_id:
        request_path = workflow_request_path(state, request_id)
        paths.append(request_path)
        directories = [state / "artifacts" / "workflow" / request_path.stem]
    else:
        directories = [state / name for name in ("artifacts", "refs", "workflow-requests")]
    for directory in directories:
        paths.extend([directory, *directory.rglob("*")])
    return paths + [Path(path) for path in dependencies]


def _references(value, context) -> set[str]:
    """Track declared external refs as well as all resolver fallback locations.

    Do not hydrate new files here: the existing readers own permission, digest
    and size checks. Only data used by artifact readers supplies dependencies.
    """
    paths: set[str] = set()
    seen: set[str] = set()
    roots = (context.state_dir.resolve(), context.project_root.resolve())

    def visit(item, key=""):
        if isinstance(item, dict):
            for name, child in item.items():
                visit(child, name)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and item and (
            key in {"ref", "refs", "path"} or key.endswith(("_ref", "_refs", "_path"))
        ):
            if item in seen:
                return
            seen.add(item)
            ref = Path(item).expanduser()
            candidates = [ref] if ref.is_absolute() else [root / ref for root in roots]
            if ref.parts and ref.parts[0] == ".zf":
                candidates.append(roots[0].joinpath(*ref.parts[1:]))
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                except (OSError, ValueError):
                    continue
                if any(resolved.is_relative_to(root) for root in roots):
                    # Keep the unresolved path too: retargeting a symlink is a
                    # source change even if the previous destination survives.
                    paths.update((str(candidate), str(resolved)))

    visit(value)
    return paths


def read_workbench_projection(context, project_id: str, request_id: str, as_of: int,
                              detail: Callable, *, if_none_match: str | None = None) -> tuple[dict, str] | JSONResponse:
    request = load_workflow_request(context.state_dir, request_id)
    if not request:
        return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
    identity = json.dumps([str(context.project_root.resolve()), project_id, request_id,
                           request.get("run_id"), request.get("revision"),
                           request.get("proposal_digest"), as_of], sort_keys=True)
    key = "workflow-workbench.v2:" + hashlib.sha256(identity.encode()).hexdigest()
    dependencies = _references(request, context)

    def version() -> str:
        return fingerprint(context.state_dir, _paths(context, dependencies, request_id), context.config)

    def tag(current):
        return '"' + hashlib.sha256((key + current).encode()).hexdigest() + '"'

    def receipt(current):
        # Conditional reads must not deserialize a multi-MB graph just to
        # discover that the browser already has this exact source generation.
        try:
            read_model.set_cached_projection(context.state_dir, key + ":source", kind="workflow-workbench-source",
                source_seq=0, payload={"fingerprint": current, "dependencies": sorted(dependencies)})
        except (sqlite3.Error, OSError):
            pass

    def cached_read():
        if if_none_match:
            source = read_model.get_cached_projection(context.state_dir, key + ":source")
            if source and isinstance(source.get("dependencies"), list) and all(
                isinstance(path, str) for path in source["dependencies"]
            ):
                dependencies.update(source["dependencies"])
                current = version()
                if source.get("fingerprint") == current and if_none_match == tag(current) and current == version():
                    return {}, if_none_match
        cached = read_model.get_cached_projection(context.state_dir, key)
        if not cached or not isinstance(cached.get("value"), dict):
            return None
        if not isinstance(cached.get("dependencies"), list) or not all(
            isinstance(path, str) for path in cached["dependencies"]
        ):
            return None
        dependencies.update(cached.get("dependencies") or [])
        current = version()
        if cached.get("fingerprint") == current and current == version():
            receipt(current)
            return cached["value"], tag(current)
        return None

    cached = cached_read()
    if cached is not None:
        return cached
    lock = context.state_dir / "projections" / ("workbench-" + key.split(":")[1])
    with locked_path(lock):
        cached = cached_read()
        if cached is not None:
            return cached
        # A newly discovered external ref needs a before/after check on a
        # subsequent build; metadata observed only after reading is insufficient.
        events, event_version = None, ""
        for _attempt in range(3):
            before = version()
            with collect_projection_failures() as failures:
                current_events = fingerprint(context.state_dir, [], context.config)
                if events is None or event_version != current_events:
                    events = event_log_from_project(context.state_dir, config=context.config).read_all()
                    event_version = current_events
                response = detail(project_id, request_id, events=events)
                if isinstance(response, JSONResponse):
                    if response.status_code != 200:
                        return response
                    body = json.loads(response.body)
                else:
                    body = response
                inputs = read_workbench_sources(context, body, as_of=as_of, all_events=events)
            # Ordinary tool/trace events carry many opaque refs which the graph
            # never hydrates. Follow only the semantic readers' inputs/outputs.
            artifact_events = [event.payload for _, event in inputs["events"] if event.type in {
                "plan.approval.requested", "approval.requested", "task_map.ready",
                "replan.requested", "goal.claim_set.pinned", "task.pipeline.generation.admitted",
                "task_map.materialization.committed", "task_map.admitted",
            }]
            discovered = _references([body, inputs["plans"], inputs["versions"],
                                      inputs["goal_claim_set"], artifact_events], context)
            added = discovered - dependencies
            dependencies.update(discovered)
            if failures or added or before != version():
                continue
            with collect_projection_failures() as graph_failures:
                value = redact_obj(build_sdlc_graph(inputs), memoize=True)
            if graph_failures or before != version():
                continue
            try:
                read_model.set_cached_projection(context.state_dir, key, kind="workflow-workbench",
                    source_seq=value["scope"]["head_seq"], payload={
                        "fingerprint": before, "dependencies": sorted(dependencies), "value": value,
                    })
            except (sqlite3.Error, OSError):
                pass  # Canonical reads remain usable when the disposable DB fails.
            receipt(before)
            return value, tag(before)
    raise WorkbenchSourceChanged()
