"""View-scoped ``delivery-trace.v2`` projections.

The legacy Delivery projection intentionally composes every historical view.
This module is the explicit v2 resource contract: each view loads canonical
inputs once and invokes only the builders required by that screen.  Returned
sections keep the existing root-level Delivery names so the Web client does
not need a second adapter model.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.delivery_projection_common import event_status, payload, status_kind
from zf.runtime.delivery_task_flow import build_task_flow
from zf.runtime.delivery_trace import (
    _node_counts,
    _ship_readiness,
    _task_map_count,
    _trace_status,
)
from zf.runtime.delivery_trace_resolve import (
    _hydrate_goal_projection_events,
    _resolve_task_map,
    _tasks_for_feature,
)
from zf.runtime.drift_report import build_drift_report
from zf.runtime.execution_graph import _evidence_by_task, build_execution_graph
from zf.runtime.goal_claim_set import hydrate_pinned_goal_claim_set
from zf.runtime.goal_coverage_graph import (
    build_goal_coverage_graph,
    degraded_goal_coverage_graph,
)
from zf.runtime.run_chain import build_run_chain
from zf.runtime.task_lifecycle_trace import build_task_lifecycle
from zf.web.projections.delivery_view_graph import _compact_goal_coverage
from zf.web.projections.delivery_view_lifecycle import (
    _compact_task_lifecycle,
    _run_groups_from_lifecycle,
)
from zf.web.projections.delivery_view_runs import (
    as_list,
    compact_run_chain,
    compact_task_flow,
)
from zf.web.projections.delivery_view_wire import (
    budget_fields,
    exact_ids,
    wire_id,
    wire_task_id,
)
from zf.web.projections.delivery_view_work import (
    compact_work_projection,
    scope_work_goal_graph,
)
from zf.web.projections.events import _events_with_seq
from zf.web.projections.trace_identity import event_trace_id, wire_trace_id


DeliveryView = Literal["overview", "runs", "graph", "work"]
ScopeToken = tuple[str, str]

_MAX_ATTENTION_ITEMS = 8
_MAX_ATTENTION_KINDS = 12
_MAX_TRACE_REFS = 8
_MAX_TRACE_REF_EVENT_IDS = 4
_MAX_TRACE_REF_TASK_IDS = 4
_MAX_REFRESH_TASK_IDS = 64
_MAX_TASK_FLOW_STAGES = 8
_MAX_TASK_FLOW_TASK_IDS = 16
_MAX_TEXT_CHARS = 120


@dataclass
class _Inputs:
    as_of_seq: int
    binding: dict[str, str]
    diagnostics: list[dict[str, Any]]
    events: list[tuple[int, ZfEvent]]
    feature_id: str
    last_event_id: str
    project_id: str
    ref: str
    scoped_events: list[tuple[int, ZfEvent]]
    task_map: dict[str, Any] | None
    tasks: dict[str, Task]


def build_delivery_view(
    *,
    state_dir: Path,
    config: Any,
    generated_at: str,
    project_id: str,
    feature_id: str,
    view: DeliveryView,
    as_of_seq: int = 0,
    goal_id: str = "",
) -> dict[str, Any]:
    """Build one bounded, root-level Delivery v2 view."""

    if view not in {"overview", "runs", "graph", "work"}:
        raise ValueError(f"unsupported Delivery view {view!r}")
    if goal_id and view != "work":
        raise ValueError("goal_id is only supported by the Work view")
    inputs = _load_inputs(
        state_dir=state_dir,
        config=config,
        project_id=project_id,
        feature_id=feature_id,
        as_of_seq=as_of_seq,
        hydrate_goal_results=view in {"graph", "work"},
    )
    if view == "overview":
        result = _overview_view(inputs)
    elif view == "runs":
        result = _runs_view(inputs, config=config)
    elif view == "graph":
        result = _graph_view(inputs, state_dir=state_dir)
    else:
        result = _work_view(inputs, state_dir=state_dir, goal_id=goal_id)
    trace_projection = (
        _canonical_trace_projection(inputs.scoped_events)
        if view == "runs"
        else {}
    )
    return redact_obj({
        "schema_version": "delivery-trace.v2",
        "view": view,
        "generated_at": generated_at,
        "project_id": project_id,
        "feature_id": feature_id,
        "as_of_seq": inputs.as_of_seq,
        "as_of_event_id": inputs.last_event_id,
        "refresh_scope": _refresh_scope(inputs.tasks),
        "task_map": _task_map_summary(inputs),
        **_common_hero(inputs.tasks, view=view),
        "run_summary": _run_summary(inputs),
        **result,
        **trace_projection,
    })


def _refresh_scope(tasks: dict[str, Task]) -> dict[str, Any]:
    """Expose bounded exact task membership for live refresh selection."""

    task_ids, omitted = exact_ids(tasks, limit=_MAX_REFRESH_TASK_IDS)
    total = len(task_ids) + omitted
    return {
        "task_ids": task_ids,
        **budget_fields("task_ids", total=total, included=len(task_ids)),
    }


def _load_inputs(
    *,
    state_dir: Path,
    config: Any,
    project_id: str,
    feature_id: str,
    as_of_seq: int,
    hydrate_goal_results: bool,
) -> _Inputs:
    raw_events = [
        (int(seq), event)
        for seq, event in _events_with_seq(state_dir, config=config)
        if isinstance(event, ZfEvent)
        and (not as_of_seq or int(seq) <= as_of_seq)
    ]
    effective_seq = int(raw_events[-1][0]) if raw_events else int(as_of_seq or 0)
    last_event_id = str(raw_events[-1][1].id or "") if raw_events else ""
    all_events = [event for _seq, event in raw_events]
    all_tasks = TaskStore(state_dir / "kanban.json").list_all_with_archive()
    tasks = _tasks_for_feature(all_tasks, feature_id=feature_id, task_id="")
    ref, task_map, diagnostics, _bundle, binding = _resolve_task_map(
        state_dir,
        feature_id=feature_id,
        task_map_ref="",
        events=all_events,
    )
    projected_events = raw_events
    if hydrate_goal_results and raw_events:
        hydrated, result_diagnostics = _hydrate_goal_projection_events(
            state_dir,
            all_events,
        )
        projected_events = [
            (raw_events[index][0], event)
            for index, (_ignored_seq, event) in enumerate(hydrated)
        ]
        diagnostics.extend(result_diagnostics)
    if task_map is None:
        diagnostics.append({
            "kind": "task_map_missing",
            "message": "no accepted task-map; using canonical task state only",
        })
    provisional = _Inputs(
        as_of_seq=effective_seq,
        binding=binding,
        diagnostics=list(diagnostics),
        events=projected_events,
        feature_id=feature_id,
        last_event_id=last_event_id,
        project_id=project_id,
        ref=ref,
        scoped_events=[],
        task_map=task_map,
        tasks=tasks,
    )
    provisional.scoped_events = _scope_delivery_events(provisional)
    return provisional


def _overview_view(inputs: _Inputs) -> dict[str, Any]:
    graph, drift, counts, ship, diagnostics = _graph_context(inputs, summary_only=True)
    del graph
    return {
        "status": _trace_status(counts),
        "execution_graph": {
            "task_count": counts["total"],
            "done_count": counts["done"],
            "in_progress_count": counts["in_progress"],
            "blocked_count": counts["blocked"],
            "waiting_count": counts["waiting"],
            "nodes": [],
            "edges": [],
            "waves": [],
            "summary_only": True,
        },
        "drift_report": {
            "status": str(drift.get("status") or "ok"),
            "summary": dict(drift.get("summary") or {}),
            "items": [],
            "summary_only": True,
        },
        "ship": _compact_ship(ship),
        **_attention(
            diagnostics,
            drift_items=list(drift.get("items") or []),
            tasks=inputs.tasks,
        ),
    }


def _graph_view(inputs: _Inputs, *, state_dir: Path) -> dict[str, Any]:
    graph, drift, counts, ship, _diagnostics = _graph_context(inputs)
    goal_graph = _goal_coverage(inputs, state_dir=state_dir)
    graph_nodes = list(graph.get("nodes") or [])
    graph_edges = list(graph.get("edges") or [])
    drift_items = list(drift.get("items") or [])
    selected_drift_items = drift_items[:24]
    return {
        "status": _trace_status(counts),
        "execution_graph": {
            "schema_version": "execution-graph.v2",
            "task_count": counts["total"],
            "done_count": counts["done"],
            "in_progress_count": counts["in_progress"],
            "blocked_count": counts["blocked"],
            "waiting_count": counts["waiting"],
            "nodes": [],
            "edges": [],
            "waves": [],
            "summary_only": True,
            **budget_fields("nodes", total=len(graph_nodes), included=0),
            **budget_fields("edges", total=len(graph_edges), included=0),
        },
        "goal_coverage_graph": _compact_goal_coverage(goal_graph),
        "drift_report": {
            "status": str(drift.get("status") or "ok"),
            "summary": dict(drift.get("summary") or {}),
            "items": [_compact_drift_item(item) for item in selected_drift_items],
            **budget_fields(
                "items",
                total=len(drift_items),
                included=len(selected_drift_items),
            ),
            "truncated": len(drift_items) > len(selected_drift_items),
        },
        "ship": _compact_ship(ship),
    }


def _runs_view(inputs: _Inputs, *, config: Any) -> dict[str, Any]:
    counts = _task_counts(inputs.tasks)
    raw_task_flow = build_task_flow(
        config=config,
        events=inputs.scoped_events,
        tasks=inputs.tasks,
        workflow_trace={},
        execution_graph={},
    )
    dag = getattr(getattr(config, "workflow", None), "dag", None)
    raw_run_chain = build_run_chain(
        inputs.scoped_events,
        stage_order=list(getattr(dag, "stage_order", []) or []),
    )
    lifecycle = _compact_task_lifecycle(
        build_task_lifecycle(inputs.scoped_events),
        allowed_task_ids=set(inputs.tasks),
        task_statuses={task_id: task.status for task_id, task in inputs.tasks.items()},
        preferred_task_ids=_run_visible_task_ids(raw_run_chain, raw_task_flow),
    )
    task_flow = compact_task_flow(raw_task_flow)
    run_groups = _run_groups_from_lifecycle(lifecycle, inputs.tasks)
    return {
        "status": _trace_status(counts),
        "task_flow": task_flow,
        "run_groups": run_groups,
        "run_chain": compact_run_chain(raw_run_chain),
        "task_lifecycle": lifecycle,
    }


def _work_view(
    inputs: _Inputs,
    *,
    state_dir: Path,
    goal_id: str = "",
) -> dict[str, Any]:
    raw_goal_graph = _goal_coverage(inputs, state_dir=state_dir)
    source_goal_count = sum(
        node.get("kind") == "goal"
        for node in list(raw_goal_graph.get("nodes") or [])
        if isinstance(node, dict)
    )
    goal_matched = True
    if goal_id:
        raw_goal_graph, goal_matched = scope_work_goal_graph(
            raw_goal_graph,
            goal_id=goal_id,
        )
    scoped_claim_count = sum(
        node.get("kind") == "goal_claim"
        for node in list(raw_goal_graph.get("nodes") or [])
        if isinstance(node, dict)
    )
    goal_graph = _compact_goal_coverage(raw_goal_graph)
    execution_graph = build_execution_graph(
        task_map=inputs.task_map,
        tasks=inputs.tasks,
        events=inputs.events,
        feature_id=inputs.feature_id,
        task_map_ref=inputs.ref,
    )
    work = compact_work_projection(
        execution_graph=execution_graph,
        goal_graph=raw_goal_graph,
        compact_goal_graph=goal_graph,
        evidence_by_task=_evidence_by_task(inputs.events),
        lifecycle=build_task_lifecycle(inputs.scoped_events),
        tasks=inputs.tasks,
        # With one Goal the feature's still-unmapped canonical tasks belong in
        # that selected delivery scope.  Multi-Goal and missing selections
        # fail closed to tasks with an explicit selected-Goal Claim link.
        goal_scoped=bool(goal_id) and (not goal_matched or source_goal_count > 1),
    )
    result = {
        "status": _trace_status(_task_counts(inputs.tasks)),
        "goal_coverage_graph": goal_graph,
        **work,
    }
    if goal_id:
        scope_goal_id, scope_goal_id_opaque = wire_id(goal_id, namespace="goal")
        result["work_scope"] = {
            "goal_id": scope_goal_id,
            "goal_id_opaque": scope_goal_id_opaque,
            "matched": goal_matched,
            "claim_count": scoped_claim_count,
            "task_count": int(
                (work.get("execution_graph") or {}).get("task_count") or 0
            ),
        }
    return result


def _run_visible_task_ids(
    run_chain: dict[str, Any],
    task_flow: dict[str, Any],
) -> list[str]:
    visible: list[str] = []

    def add(value: Any) -> None:
        task_id = str(value or "")
        if task_id and task_id not in visible and len(visible) < _MAX_TASK_FLOW_TASK_IDS:
            visible.append(task_id)

    for stage in list(run_chain.get("stages") or [])[:_MAX_TASK_FLOW_STAGES]:
        for task_id in as_list(stage.get("task_ids")):
            add(task_id)
    for stage in list(task_flow.get("stages") or [])[:_MAX_TASK_FLOW_STAGES]:
        for task_id in as_list(stage.get("task_ids")):
            add(task_id)
        for task in list(stage.get("tasks") or []):
            if isinstance(task, dict):
                add(task.get("task_id"))
    return visible


def _graph_context(
    inputs: _Inputs, *, summary_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    graph = build_execution_graph(
        task_map=inputs.task_map,
        tasks=inputs.tasks,
        events=inputs.events,
        feature_id=inputs.feature_id,
        task_map_ref=inputs.ref,
        summary_only=summary_only,
    )
    diagnostics = [*list(graph.get("diagnostics") or []), *inputs.diagnostics]
    graph = {**graph, "diagnostics": diagnostics}
    drift = build_drift_report(graph=graph, events=inputs.events)
    counts = _node_counts(list(graph.get("nodes") or []))
    ship = _ship_readiness(
        list(graph.get("nodes") or []),
        counts,
        drift,
        inputs.events,
        inputs.feature_id,
    )
    return graph, drift, counts, ship, diagnostics


def _goal_coverage(inputs: _Inputs, *, state_dir: Path) -> dict[str, Any]:
    try:
        task_map = inputs.task_map or {}
        workflow_run_id = str(
            inputs.binding.get("workflow_run_id")
            or task_map.get("workflow_run_id")
            or task_map.get("run_id")
            or ""
        )
        goal_id = str(
            inputs.binding.get("goal_id")
            or inputs.feature_id
            or task_map.get("goal_id")
            or task_map.get("feature_id")
            or ""
        )
        generation = str(
            inputs.binding.get("task_map_generation")
            or task_map.get("task_map_generation")
            or ""
        )
        pinned = hydrate_pinned_goal_claim_set(
            state_dir=state_dir,
            events=[event for _seq, event in inputs.events],
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
            task_map_generation=generation,
        )
        projection_task_map = dict(task_map)
        if workflow_run_id:
            projection_task_map["workflow_run_id"] = workflow_run_id
        if goal_id:
            projection_task_map["goal_id"] = goal_id
        if generation:
            projection_task_map["task_map_generation"] = generation
        return build_goal_coverage_graph(
            task_map=projection_task_map or None,
            tasks=inputs.tasks,
            events=inputs.events,
            project_id=inputs.project_id,
            feature_id=inputs.feature_id,
            task_map_ref=inputs.ref,
            goal_claim_set=pinned,
        )
    except Exception as exc:
        return degraded_goal_coverage_graph(
            project_id=inputs.project_id,
            feature_id=inputs.feature_id,
            reason=f"{type(exc).__name__}: {exc}",
        )


def _scope_delivery_events(inputs: _Inputs) -> list[tuple[int, ZfEvent]]:
    task_ids = set(inputs.tasks)
    feature_id = inputs.feature_id
    task_map_refs = {str(inputs.ref or "")}
    if isinstance(inputs.task_map, dict):
        task_map_refs.update(
            str(inputs.task_map.get(key) or "")
            for key in ("task_map_ref", "source_ref")
        )
    by_causation: dict[str, list[int]] = defaultdict(list)
    by_event_id: dict[str, list[int]] = defaultdict(list)
    by_token: dict[ScopeToken, list[int]] = defaultdict(list)
    events_by_seq: dict[int, ZfEvent] = {}
    tokens_by_seq: dict[int, set[ScopeToken]] = {}
    for seq, event in inputs.events:
        events_by_seq[seq] = event
        tokens = _event_scope_tokens(event)
        tokens_by_seq[seq] = tokens
        if event.causation_id:
            by_causation[str(event.causation_id)].append(seq)
        if event.id:
            by_event_id[str(event.id)].append(seq)
        for token in tokens:
            by_token[token].append(seq)

    # Selection is directional: directly scoped events and their descendants
    # may expand downward, while causation ancestors are evidence-only and
    # must not fan back out through unrelated siblings.
    ancestor_mode = 0
    scoped_mode = 1
    selected_modes: dict[int, int] = {}
    pending: deque[tuple[int, int]] = deque()

    def select(seq: int, mode: int) -> None:
        previous = selected_modes.get(seq, -1)
        if mode <= previous:
            return
        selected_modes[seq] = mode
        pending.append((seq, mode))

    for seq, event in inputs.events:
        data = payload(event)
        event_task_id = str(event.task_id or data.get("task_id") or "")
        feature_values = {
            str(data.get(key) or "")
            for key in ("feature_id", "pdd_id", "goal_id")
        }
        ref_values = {
            str(data.get(key) or "")
            for key in ("task_map_ref", "new_task_map_ref", "old_task_map_ref")
        }
        if (
            event_task_id in task_ids
            or (feature_id and feature_id in feature_values)
            or bool((ref_values - {""}) & (task_map_refs - {""}))
        ):
            select(seq, scoped_mode)

    # Indexed causation/identity closure.  Each index bucket is consumed once,
    # so a large event log remains linear in events plus matched edges.
    consumed_ancestor_refs: set[str] = set()
    consumed_descendant_refs: set[str] = set()
    consumed_tokens: set[ScopeToken] = set()

    while pending:
        seq, mode = pending.popleft()
        if selected_modes.get(seq) != mode:
            continue
        event = events_by_seq[seq]
        parent_ref = str(event.causation_id or "")
        if parent_ref and parent_ref not in consumed_ancestor_refs:
            consumed_ancestor_refs.add(parent_ref)
            for parent_seq in by_event_id.get(parent_ref, ()):
                select(parent_seq, ancestor_mode)

        event_id = str(event.id or "")
        if (
            mode == scoped_mode
            and event_id
            and event_id not in consumed_descendant_refs
        ):
            consumed_descendant_refs.add(event_id)
            for child_seq in by_causation.get(event_id, ()):
                select(child_seq, scoped_mode)

        for token in tokens_by_seq[seq]:
            # Ancestors are evidence-only.  Expanding any of their identities
            # would authorize unrelated causation siblings to enter scope.
            if mode == ancestor_mode:
                continue
            if token in consumed_tokens:
                continue
            consumed_tokens.add(token)
            for related_seq in by_token.get(token, ()):
                select(related_seq, scoped_mode)
    return [
        (seq, event)
        for seq, event in inputs.events
        if seq in selected_modes
    ]


def _event_scope_tokens(event: ZfEvent) -> set[ScopeToken]:
    data = payload(event)
    tokens = {
        ("trace", str(event.correlation_id or "")),
        ("trace", str(data.get("trace_id") or "")),
        ("fanout", str(data.get("fanout_id") or "")),
        ("run", str(data.get("run_id") or "")),
        ("run", str(data.get("dispatch_id") or "")),
        ("workflow_run", str(data.get("workflow_run_id") or "")),
    }
    return {(kind, value) for kind, value in tokens if value}


def _canonical_trace_projection(
    events: list[tuple[int, ZfEvent]],
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[int, ZfEvent]]] = defaultdict(list)
    for seq, event in events:
        trace_id = event_trace_id(event)
        if trace_id and event.id:
            grouped[trace_id].append((seq, event))
    ordered = sorted(
        grouped.items(),
        key=lambda item: item[1][-1][0],
        reverse=True,
    )
    refs: list[dict[str, Any]] = []
    for canonical_id, pairs in ordered[:_MAX_TRACE_REFS]:
        wire_id, opaque = wire_trace_id(canonical_id)
        event_ids = list(dict.fromkeys(
            str(event.id) for _seq, event in pairs if event.id
        ))
        raw_task_ids = list(dict.fromkeys(
            str(event.task_id or "")
            for _seq, event in pairs
            if str(event.task_id or "")
        ))
        source_event_ids, _source_event_ids_omitted = exact_ids(
            event_ids[-_MAX_TRACE_REF_EVENT_IDS:],
            limit=_MAX_TRACE_REF_EVENT_IDS,
        )
        selected_task_ids = raw_task_ids[:_MAX_TRACE_REF_TASK_IDS]
        task_ids = [wire_task_id(task_id)[0] for task_id in selected_task_ids]
        refs.append({
            "trace_id": wire_id,
            "trace_id_opaque": opaque,
            "membership": "trace-v2-source-event",
            "event_count": len(pairs),
            "source_event_ids": source_event_ids,
            **budget_fields(
                "source_event_ids",
                total=len(event_ids),
                included=len(source_event_ids),
            ),
            "task_ids": task_ids,
            "task_ids_opaque": sum(
                1 for task_id in selected_task_ids if wire_task_id(task_id)[1]
            ),
            **budget_fields(
                "task_ids",
                total=len(raw_task_ids),
                included=len(task_ids),
            ),
            "last_seq": pairs[-1][0],
        })
    return {
        "canonical_trace_refs": refs,
        **budget_fields(
            "canonical_trace_refs",
            total=len(ordered),
            included=len(refs),
        ),
    }


def _task_map_summary(inputs: _Inputs) -> dict[str, Any]:
    raw_tasks = list((inputs.task_map or {}).get("tasks") or [])
    waves = {
        int(item.get("wave") or 0)
        for item in raw_tasks
        if isinstance(item, dict)
    }
    return {
        "status": "accepted" if inputs.task_map else "missing",
        "task_map_ref": _text(inputs.ref),
        "task_count": _task_map_count(inputs.task_map),
        "wave_count": len(waves),
    }


def _task_counts(tasks: dict[str, Task]) -> dict[str, int]:
    kinds = Counter(status_kind(task.status) for task in tasks.values())
    return {
        "total": len(tasks),
        "done": kinds["done"],
        "in_progress": kinds["running"],
        "blocked": kinds["blocked"] + kinds["failed"],
        "waiting": kinds["pending"],
    }


def _common_hero(tasks: dict[str, Task], *, view: DeliveryView) -> dict[str, Any]:
    """Cheap canonical-task facts used when a view skips Graph computation."""

    counts = _task_counts(tasks)
    return {
        "status": _trace_status(counts),
        "execution_graph": {
            "task_count": counts["total"],
            "done_count": counts["done"],
            "in_progress_count": counts["in_progress"],
            "blocked_count": counts["blocked"],
            "waiting_count": counts["waiting"],
            "nodes": [],
            "edges": [],
            "waves": [],
            "summary_only": True,
            "basis": "canonical_task_state",
        },
        "drift_report": {
            "status": "not_evaluated",
            "summary": {},
            "items": [],
            "summary_only": True,
        },
        "ship": {
            "status": "not_evaluated",
            "readiness": "ship gates are not evaluated in this view",
            "shipped": False,
            "ship_status": "not_evaluated",
            "merge_ref": "",
            "candidate_status": "",
            "required_tasks": counts["total"],
            "done_tasks": counts["done"],
            "missing_evidence": [],
            "release_blockers": [],
            "summary_only": True,
            "basis": f"not_computed_for_{view}_view",
        },
    }


def _run_summary(inputs: _Inputs) -> dict[str, Any]:
    """Fold explicit run identities without constructing run/span bodies."""

    runs: dict[ScopeToken, dict[str, Any]] = {}
    terminal_statuses = {"done", "failed", "blocked"}
    for seq, event in inputs.scoped_events:
        data = payload(event)
        run_key = next((
            (kind, value)
            for kind, value in (
                ("run", str(data.get("dispatch_id") or "").strip()),
                ("run", str(data.get("run_id") or "").strip()),
                ("fanout", str(data.get("fanout_id") or "").strip()),
                ("workflow_run", str(data.get("workflow_run_id") or "").strip()),
            )
            if value
        ), None)
        if run_key is None:
            continue
        task_id = str(event.task_id or data.get("task_id") or "")
        task = inputs.tasks.get(task_id)
        label = str(
            data.get("label")
            or data.get("stage_id")
            or (task.title if task is not None else "")
            or run_key[1]
        )
        previous = runs.get(run_key)
        previous_status = str((previous or {}).get("status") or "pending")
        candidate_status = status_kind(event_status(event))
        if previous_status in terminal_statuses:
            run_status = previous_status
        elif candidate_status != "pending":
            run_status = candidate_status
        else:
            run_status = previous_status
        runs[run_key] = {
            "seq": seq,
            "label": _text(label),
            "status": run_status,
        }
    values = list(runs.values())
    counts = Counter(str(item["status"]) for item in values)
    latest = max(values, key=lambda item: int(item["seq"]), default=None)
    latest_label = ""
    if latest is not None:
        latest_label = f"{latest['label']} · {latest['status']}"
    return {
        "total": len(values),
        "completed": counts["done"],
        "running": counts["running"],
        "failed": counts["failed"] + counts["blocked"],
        "latest_label": latest_label,
    }


def _attention(
    diagnostics: list[dict[str, Any]],
    *,
    drift_items: list[dict[str, Any]] | None = None,
    tasks: dict[str, Task] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        rows.append({
            "kind": _text(item.get("kind") or item.get("code") or "projection_degraded", 80),
            "message": _text(item.get("message") or "projection degraded"),
            "task_id": _text(item.get("task_id"), 120),
        })
    for item in drift_items or []:
        if str(item.get("severity") or "") not in {"error", "warning"}:
            continue
        rows.append({
            "kind": _text(item.get("kind") or "drift", 80),
            "message": _text(item.get("message") or "delivery drift"),
            "task_id": _text(item.get("task_id"), 120),
        })
    for task in (tasks or {}).values():
        if status_kind(task.status) not in {"blocked", "failed"}:
            continue
        rows.append({
            "kind": "task_blocked",
            "message": _text(task.title or "task requires attention"),
            "task_id": _text(task.id, 120),
        })
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["kind"], row["message"])
        current = grouped.setdefault(key, {
            "kind": row["kind"],
            "message": row["message"],
            "count": 0,
            "task_ids": [],
        })
        current["count"] += 1
        task_id = row["task_id"]
        if task_id and task_id not in current["task_ids"] and len(current["task_ids"]) < 8:
            current["task_ids"].append(task_id)
    items = list(grouped.values())
    kind_counts = Counter(row["kind"] for row in rows)
    compact_items = [
        {
            "label": _text(item["message"]),
            "meta": _text(
                " · ".join(filter(None, [
                    str(item["kind"]),
                    ", ".join(item["task_ids"]),
                ])),
            ),
            "tone": "err" if any(
                token in str(item["kind"]).lower()
                for token in ("fail", "block", "missing", "reject", "error")
            ) else "warn",
            "count": int(item["count"]),
        }
        for item in items[:_MAX_ATTENTION_ITEMS]
    ]
    return {
        "attention": compact_items,
        "attention_summary": {
            "total_count": len(rows),
            "truncated": len(items) > _MAX_ATTENTION_ITEMS,
            "by_kind": [
            {"kind": kind, "count": count}
            for kind, count in kind_counts.most_common(_MAX_ATTENTION_KINDS)
            ],
            "by_kind_truncated": len(kind_counts) > _MAX_ATTENTION_KINDS,
        },
    }


def _compact_ship(ship: dict[str, Any]) -> dict[str, Any]:
    missing = list(ship.get("missing_evidence") or [])
    blockers = list(ship.get("release_blockers") or [])
    selected_missing = missing[:24]
    selected_blockers = blockers[:12]
    return {
        "status": _text(ship.get("status"), 80),
        "readiness": _text(ship.get("readiness"), 80),
        "shipped": bool(ship.get("shipped")),
        "ship_status": _text(ship.get("ship_status"), 80),
        "merge_ref": _text(ship.get("merge_ref"), 200),
        "candidate_status": _text(ship.get("candidate_status"), 80),
        "required_tasks": int(ship.get("required_tasks") or 0),
        "done_tasks": int(ship.get("done_tasks") or 0),
        "missing_evidence": [
            {
                "task_id": wire_task_id(item.get("task_id"))[0],
                "status": _text(item.get("status"), 80),
            }
            for item in selected_missing
        ],
        **budget_fields(
            "missing_evidence",
            total=len(missing),
            included=len(selected_missing),
        ),
        "release_blockers": [
            {
                "kind": _text(item.get("kind"), 120),
                "severity": _text(item.get("severity"), 80),
                "evidence_event_id": _text(item.get("evidence_event_id"), 160),
            }
            for item in selected_blockers
        ],
        **budget_fields(
            "release_blockers",
            total=len(blockers),
            included=len(selected_blockers),
        ),
    }


def _compact_drift_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _text(item.get("kind"), 100),
        "severity": _text(item.get("severity"), 80),
        "task_id": wire_task_id(item.get("task_id"))[0],
        "message": _text(item.get("message")),
    }


def _text(value: Any, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


__all__ = ["DeliveryView", "build_delivery_view"]
