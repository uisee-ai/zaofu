"""Projections layer: workflow_graph (moved verbatim from web/server.py)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_event
from zf.core.task.store import TaskStore


_GRAPH_CACHE_KIND = "workflow_graph.v2"
# Serve a cached graph up to this many events behind truth (background refresh
# catches up). Tuned to absorb live append storms without lying for long.
_GRAPH_CACHE_MAX_LAG_EVENTS = 2000

_GRAPH_REFRESH_JOBS: dict[str, object] = {}
_GRAPH_REFRESH_LOCK = __import__("threading").Lock()


def _spawn_graph_refresh(state_dir: Path, *, config: "ZfConfig | None", cache_key: str) -> None:
    import threading

    key = f"{state_dir.resolve()}::{cache_key}"
    with _GRAPH_REFRESH_LOCK:
        existing = _GRAPH_REFRESH_JOBS.get(key)
        if existing is not None and getattr(existing, "is_alive", lambda: False)():
            return

        def _job() -> None:
            try:
                _workflow_graph(state_dir, config=config, force_recompute=True)
            except Exception:
                pass
            finally:
                with _GRAPH_REFRESH_LOCK:
                    if _GRAPH_REFRESH_JOBS.get(key) is threading.current_thread():
                        _GRAPH_REFRESH_JOBS.pop(key, None)

        thread = threading.Thread(target=_job, name="zf-graph-refresh", daemon=True)
        _GRAPH_REFRESH_JOBS[key] = thread
        thread.start()
_ACTION_DECISION_EVENT_TYPES = {
    "workflow.dispatch.requested",
    "workflow.gate.requested",
    "task.rework.requested",
    "static_gate.passed",
    "static_gate.failed",
    "static_gate.skipped",
}
_OVERLAY_EVENT_PREFIXES = ("fanout.", "run.")


def _workflow_judge_configured(config: ZfConfig | None) -> bool:
    if config is None:
        return False
    for role in getattr(config, "roles", []) or []:
        publishes = set(getattr(role, "publishes", []) or [])
        if "judge.passed" in publishes or "judge.failed" in publishes:
            return True
        if str(getattr(role, "name", "") or "").strip().lower() == "judge":
            return True
    return False


def _workflow_terminal_success_event(config: ZfConfig | None) -> str:
    if config is None:
        return ""
    published = {
        event_type
        for role in getattr(config, "roles", []) or []
        for event_type in (getattr(role, "publishes", []) or [])
    }
    for event_type in ("judge.passed", "verify.passed", "test.passed", "review.approved"):
        if event_type in published:
            return event_type
    return ""


def _workflow_stage(config: ZfConfig | None, stage_id: str) -> Any:
    if config is None:
        return None
    for stage in getattr(config.workflow, "stages", []) or []:
        if getattr(stage, "id", "") == stage_id:
            return stage
    return None


def _role_outcome_aggregates(
    role_refs_by_node: dict[str, set[str]],
    tasks: list[Any],
    events: list[Any],
    state_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Aggregate per-role outcome metrics for the config-level experiment
    heatmap (design 101 §2 layer-2 + §8 A). Process columns: pass_rate /
    rework_count / cost_usd. Deterministic quality columns (no LLM judge):
    scope_violation_rate / discriminator_catch_rate. Fields are null when
    there is no data so the graph overlay degrades to "—" instead of
    crashing. Semantic quality stays gated on LH-2.5 LLM-judge."""
    # task_id -> node_id, for attributing per-task events to a role node.
    task_node: dict[str, str] = {}
    for node_id, refs in role_refs_by_node.items():
        for t in tasks:
            if str(getattr(t, "assigned_to", "") or "") in refs:
                task_node[str(getattr(t, "id", ""))] = node_id
    rework_by_task: dict[str, int] = {}
    scope_by_node: dict[str, int] = {}
    disc_pass_by_node: dict[str, int] = {}
    disc_fail_by_node: dict[str, int] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        tid = str(
            getattr(event, "task_id", "")
            or _event_payload(event).get("task_id", "")
            or ""
        )
        if not tid:
            continue
        if etype == "task.rework.requested":
            rework_by_task[tid] = rework_by_task.get(tid, 0) + 1
        node = task_node.get(tid)
        if node is None:
            continue
        if etype == "scope.violation":
            scope_by_node[node] = scope_by_node.get(node, 0) + 1
        elif etype == "discriminator.passed":
            disc_pass_by_node[node] = disc_pass_by_node.get(node, 0) + 1
        elif etype == "discriminator.failed":
            disc_fail_by_node[node] = disc_fail_by_node.get(node, 0) + 1
    cost_by_role: dict[str, float] = {}
    try:
        from zf.core.cost.tracker import CostTracker

        for role_name, summary in CostTracker(
            state_dir / "cost.jsonl"
        ).per_role_totals().items():
            cost_by_role[str(role_name)] = float(getattr(summary, "total_usd", 0.0) or 0.0)
    except Exception:
        cost_by_role = {}
    agg: dict[str, dict[str, Any]] = {}
    for node_id, refs in role_refs_by_node.items():
        owned = [t for t in tasks if str(getattr(t, "assigned_to", "") or "") in refs]
        done = [t for t in owned if str(getattr(t, "status", "")) == "done"]
        rework_total = sum(rework_by_task.get(getattr(t, "id", ""), 0) for t in owned)
        if done:
            clean = sum(
                1 for t in done if rework_by_task.get(getattr(t, "id", ""), 0) == 0
            )
            pass_rate: float | None = round(clean / len(done), 3)
        else:
            pass_rate = None
        cost_usd: float | None = None
        for ref in refs:
            if ref in cost_by_role:
                cost_usd = round(cost_by_role[ref], 4)
                break
        # deterministic quality columns (design 101 §8 A)
        scope_violation_rate: float | None = (
            round(scope_by_node.get(node_id, 0) / len(owned), 3) if owned else None
        )
        dp = disc_pass_by_node.get(node_id, 0)
        df = disc_fail_by_node.get(node_id, 0)
        discriminator_catch_rate: float | None = (
            round(df / (dp + df), 3) if (dp + df) else None
        )
        # representative task to drill into (design 101 §8 B): prefer a
        # reworked task (the failure we want to inspect), else any owned.
        drill_task_id: str | None = next(
            (
                str(getattr(t, "id", ""))
                for t in owned
                if rework_by_task.get(getattr(t, "id", ""), 0) > 0
            ),
            (str(getattr(owned[0], "id", "")) if owned else None),
        )
        agg[node_id] = {
            "pass_rate": pass_rate,
            "rework_count": rework_total,
            "cost_usd": cost_usd,
            "scope_violation_rate": scope_violation_rate,
            "discriminator_catch_rate": discriminator_catch_rate,
            "drill_task_id": drill_task_id,
        }
    return agg


def _workflow_graph(state_dir: Path, *, config: ZfConfig | None = None, force_recompute: bool = False) -> dict[str, Any]:
    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    roles = list(getattr(config, "roles", []) or []) if config is not None else []
    try:
        tasks = TaskStore(state_dir / "kanban.json").list_all()
    except Exception:
        tasks = []
    active_tasks = [
        task for task in tasks
        if getattr(task, "status", "") not in {"done", "cancelled"}
    ]
    role_node_by_ref: dict[str, str] = {}
    role_refs_by_node: dict[str, set[str]] = {}
    for role in roles:
        name = str(getattr(role, "name", "") or "")
        instance_id = str(getattr(role, "instance_id", "") or name)
        if not instance_id and not name:
            continue
        node_id = f"role:{instance_id or name}"
        refs = {value for value in (name, instance_id) if value}
        for value in refs:
            role_node_by_ref[value] = node_id
        role_refs_by_node[node_id] = refs
    task_ids_by_role_node: dict[str, list[str]] = {}
    for node_id, refs in role_refs_by_node.items():
        task_ids_by_role_node[node_id] = [
            task.id for task in active_tasks
            if str(getattr(task, "assigned_to", "") or "") in refs
        ][:20]
    orchestrator_node = next(
        (
            node_id for node_id, refs in role_refs_by_node.items()
            if "orchestrator" in refs
        ),
        "",
    )
    stage_nodes = [
        {
            "id": str(getattr(stage, "id", "")),
            "label": str(getattr(stage, "name", "") or getattr(stage, "id", "")),
            "kind": "stage",
            "pattern_id": str(getattr(stage, "id", "")),
            "topology": str(getattr(stage, "topology", "") or ""),
            "backend": str(getattr(stage, "backend", "") or ""),
            "trigger": str(getattr(stage, "trigger", "") or ""),
            "roles": list(getattr(stage, "roles", []) or []),
            "target_ref": str(getattr(stage, "target_ref", "") or ""),
            "barrier": {
                "mode": str(getattr(getattr(stage, "aggregate", None), "mode", "") or "wait_for_all"),
                "success_event": str(getattr(getattr(stage, "aggregate", None), "success_event", "") or ""),
                "failure_event": str(getattr(getattr(stage, "aggregate", None), "failure_event", "") or ""),
                "required_children": [
                    str(child)
                    for child in (
                        list(getattr(stage, "roles", []) or [])
                        or [
                            getattr(child, "role_instance", "") or getattr(child, "role", "")
                            for child in list(getattr(stage, "children", []) or [])
                        ]
                    )
                    if str(child)
                ],
            },
        }
        for stage in stages
        if str(getattr(stage, "id", ""))
    ]
    role_nodes = [
        {
            "id": f"role:{getattr(role, 'instance_id', '') or getattr(role, 'name', '')}",
            "label": str(getattr(role, "name", "") or getattr(role, "instance_id", "")),
            "kind": "role",
            "backend": str(getattr(role, "backend", "") or ""),
            "instance_id": str(getattr(role, "instance_id", "") or ""),
            "role_kind": str(getattr(role, "role_kind", "") or ""),
            "skills": list(getattr(role, "skills", []) or []),
            "plugins": list(getattr(role, "plugins", []) or []),
            "agent": str(getattr(role, "agent", "") or ""),
            "wip_count": len(task_ids_by_role_node.get(
                f"role:{getattr(role, 'instance_id', '') or getattr(role, 'name', '')}",
                [],
            )),
            "task_ids": task_ids_by_role_node.get(
                f"role:{getattr(role, 'instance_id', '') or getattr(role, 'name', '')}",
                [],
            ),
        }
        for role in roles
        if str(getattr(role, "instance_id", "") or getattr(role, "name", ""))
    ]
    aggregate_nodes = [
        {
            "id": f"aggregate:{getattr(stage, 'id', '')}",
            "label": f"{getattr(stage, 'id', '')} aggregate",
            "kind": "aggregate",
            "mode": str(getattr(getattr(stage, "aggregate", None), "mode", "") or "wait_for_all"),
            "topology": str(getattr(stage, "topology", "") or ""),
        }
        for stage in stages
        if str(getattr(stage, "id", ""))
        and str(getattr(stage, "topology", "") or "").startswith("fanout")
    ]
    edges: list[dict[str, Any]] = []
    for index, stage in enumerate(stage_nodes[:-1]):
        edges.append({
            "from": stage["id"],
            "to": stage_nodes[index + 1]["id"],
            "kind": "workflow",
        })
    if orchestrator_node and stage_nodes:
        edges.append({
            "from": orchestrator_node,
            "to": stage_nodes[0]["id"],
            "kind": "orchestrates",
        })
    for stage in stages:
        stage_id = str(getattr(stage, "id", "") or "")
        if not stage_id:
            continue
        topology = str(getattr(stage, "topology", "") or "")
        aggregate_id = f"aggregate:{stage_id}"
        role_refs = list(getattr(stage, "roles", []) or [])
        children = list(getattr(stage, "children", []) or [])
        for role_ref in role_refs:
            role_node = role_node_by_ref.get(str(role_ref))
            if role_node:
                edges.append({
                    "from": stage_id,
                    "to": role_node,
                    "kind": "stage_role",
                })
        if topology.startswith("fanout"):
            edges.append({
                "from": stage_id,
                "to": aggregate_id,
                "kind": "fanout_aggregate",
            })
            for child in children:
                child_ref = (
                    str(getattr(child, "role_instance", "") or "")
                    or str(getattr(child, "role", "") or "")
                )
                role_node = role_node_by_ref.get(child_ref)
                if role_node:
                    edges.append({
                        "from": stage_id,
                        "to": role_node,
                        "kind": "fanout_child",
                        "scope": str(getattr(child, "scope", "") or ""),
                    })
                    edges.append({
                        "from": role_node,
                        "to": aggregate_id,
                        "kind": "fanout_return",
                    })
            if orchestrator_node:
                edges.append({
                    "from": aggregate_id,
                    "to": orchestrator_node,
                    "kind": "return_to_orchestrator",
                })
    if orchestrator_node and stage_nodes and not any(edge["kind"] == "return_to_orchestrator" for edge in edges):
        edges.append({
            "from": stage_nodes[-1]["id"],
            "to": orchestrator_node,
            "kind": "return_to_orchestrator",
        })
    if not stage_nodes and role_nodes:
        publishers_by_event: dict[str, list[str]] = {}
        for role in roles:
            role_ref = str(getattr(role, "instance_id", "") or getattr(role, "name", "") or "")
            role_node = role_node_by_ref.get(role_ref)
            if not role_node:
                continue
            for event_type in list(getattr(role, "publishes", []) or []):
                publishers_by_event.setdefault(str(event_type), []).append(role_node)

        seen_edges: set[tuple[str, str, str, str]] = set()

        def add_role_edge(
            source: str,
            target: str,
            kind: str,
            trigger_event: str = "",
        ) -> None:
            if not source or not target or source == target:
                return
            key = (source, target, kind, trigger_event)
            if key in seen_edges:
                return
            seen_edges.add(key)
            edge: dict[str, Any] = {
                "from": source,
                "to": target,
                "kind": kind,
            }
            if trigger_event:
                edge["trigger_event"] = trigger_event
            edges.append(edge)

        for role in roles:
            role_ref = str(getattr(role, "instance_id", "") or getattr(role, "name", "") or "")
            target = role_node_by_ref.get(role_ref, "")
            for trigger in list(getattr(role, "triggers", []) or []):
                trigger_type = str(trigger)
                if trigger_type == "task.assigned" and orchestrator_node:
                    add_role_edge(orchestrator_node, target, "assign", trigger_type)
                for source in publishers_by_event.get(trigger_type, []):
                    if trigger_type == "task.assigned" and source == orchestrator_node:
                        continue
                    add_role_edge(source, target, "trigger", trigger_type)
    compiled_graph_projection: dict[str, Any] = {}
    workflow_node_runs: dict[str, Any] = {}
    compiled_graph: Any = None
    if config is not None:
        try:
            from zf.core.workflow.graph import compile_workflow_graph

            compiled_graph = compile_workflow_graph(config)
            compiled_graph_projection = compiled_graph.to_dict()
        except Exception as exc:
            compiled_graph_projection = {
                "schema_version": "workflow-graph.v1",
                "diagnostics": [{
                    "kind": "workflow_graph_compile_failed",
                    "message": str(exc),
                }],
            }
            compiled_graph = None

    projection_status: dict[str, Any] = {}
    source_seq = 0
    cache_key = ""
    try:
        from zf.web.projections import read_model

        source_seq = read_model.current_projected_seq(state_dir, config=config)
        projection_status = read_model.projection_status(state_dir)
        cache_key = _workflow_graph_cache_key(
            config=config,
            active_tasks=active_tasks,
            stages=stages,
            roles=roles,
        )
        cached = None if force_recompute else read_model.get_cached_projection(
            state_dir,
            cache_key,
        )
        if cached is not None:
            cached_seq = int((cached.get("projection_cache") or {}).get("source_seq") or 0)
            lag = max(0, int(source_seq) - cached_seq)
            if lag == 0:
                return cached
            if lag <= _GRAPH_CACHE_MAX_LAG_EVENTS:
                # Serve-stale-with-lag: on a live project every append used to
                # miss this cache (exact-seq test) and rewrite the multi-MB
                # graph row per request — the F0-A collapse. Serve the cached
                # graph, surface the lag, refresh once in the background.
                projection = cached.setdefault("projection", {})
                projection["projection_lag"] = lag
                projection["stale"] = True
                _spawn_graph_refresh(state_dir, config=config, cache_key=cache_key)
                return cached
    except Exception:
        projection_status = {"projection_state": "unavailable"}
        source_seq = 0
        cache_key = ""

    events = _workflow_graph_events(
        state_dir,
        config=config,
        compiled_graph=compiled_graph,
    )
    fanout_events = [
        event for event in events
        if str(event.type).startswith("fanout.") or str(_event_payload(event).get("fanout_id", ""))
    ][-80:]
    run_events = [
        event for event in events
        if str(_event_payload(event).get("run_id", "")) or str(event.type).startswith("run.")
    ][-80:]
    # design 101 §2 layer-2 + §8 A: per-role aggregate outcome heatmap.
    # The graph-scoped event set is filtered to topology event types, so
    # hydrate the outcome/quality event types explicitly for the aggregate.
    try:
        from zf.web.projections import read_model

        _outcome_events = read_model.hydrate_events(
            state_dir,
            types=[
                "task.rework.requested",
                "scope.violation",
                "discriminator.passed",
                "discriminator.failed",
            ],
            config=config,
        )
    except Exception:
        _outcome_events = events
    try:
        _role_agg = _role_outcome_aggregates(
            role_refs_by_node, tasks, _outcome_events, state_dir
        )
    except Exception:
        _role_agg = {}
    for _node in role_nodes:
        _node.update(
            _role_agg.get(
                _node["id"],
                {
                    "pass_rate": None,
                    "rework_count": 0,
                    "cost_usd": None,
                    "scope_violation_rate": None,
                    "discriminator_catch_rate": None,
                    "drill_task_id": None,
                },
            )
        )
    if compiled_graph is not None:
        try:
            from zf.runtime.workflow_node_projection import (
                build_workflow_node_projection,
            )

            workflow_node_runs = build_workflow_node_projection(
                graph=compiled_graph,
                events=events,
                tasks=active_tasks,
            )
        except Exception as exc:
            compiled_graph_projection = {
                "schema_version": "workflow-graph.v1",
                "diagnostics": [{
                    "kind": "workflow_graph_compile_failed",
                    "message": str(exc),
                }],
            }
            workflow_node_runs = {}
    result = {
        "nodes": stage_nodes + role_nodes + aggregate_nodes,
        "edges": edges,
        "compiled_graph": compiled_graph_projection,
        "workflow_node_runs": workflow_node_runs,
        "overlays": {
            "fanouts": [asdict(redact_event(event)) for event in fanout_events],
            "runs": [asdict(redact_event(event)) for event in run_events],
        },
        "counts": {
            "stages": len(stage_nodes),
            "roles": len(role_nodes),
            "aggregates": len(aggregate_nodes),
            "fanout_events": len(fanout_events),
            "run_events": len(run_events),
            "active_tasks": len(active_tasks),
        },
        "projection": {
            "source": "read_model.sqlite",
            "source_seq": source_seq,
            "projection_state": str(projection_status.get("projection_state", "unknown")),
            "projection_lag": projection_status.get("projection_lag"),
            "cache_key": cache_key,
        },
    }
    if cache_key and source_seq:
        try:
            from zf.web.projections import read_model

            read_model.set_cached_projection(
                state_dir,
                cache_key,
                kind=_GRAPH_CACHE_KIND,
                source_seq=source_seq,
                payload=result,
            )
        except Exception:
            pass
    return result


def _workflow_graph_events(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    compiled_graph: Any,
) -> list[ZfEvent]:
    try:
        from zf.web.projections import read_model

        event_types = _workflow_graph_event_types(compiled_graph)
        # slim=True: graph topology only reads event.type + fanout_id/run_id
        # (both in the slim keep-set), so skip the per-event raw file read that
        # made this projection take 40-50s on large logs.
        return read_model.hydrate_events(
            state_dir,
            types=sorted(event_types),
            type_prefixes=_OVERLAY_EVENT_PREFIXES,
            config=config,
            slim=True,
        )
    except Exception:
        return []


def _workflow_graph_event_types(compiled_graph: Any) -> set[str]:
    event_types = set(_ACTION_DECISION_EVENT_TYPES)
    nodes = list(getattr(compiled_graph, "nodes", []) or [])
    for node in nodes:
        for attr in ("trigger", "success_event", "failure_event", "skipped_event"):
            event_types.update(_split_event_types(str(getattr(node, attr, "") or "")))
    terminal_policy = getattr(compiled_graph, "terminal_policy", None)
    event_types.update(str(event) for event in getattr(terminal_policy, "success_events", []) or [])
    event_types.update(str(event) for event in getattr(terminal_policy, "failure_events", []) or [])
    return {event for event in event_types if event}


def _split_event_types(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _event_payload(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return payload


def _workflow_graph_cache_key(
    *,
    config: ZfConfig | None,
    active_tasks: list[Any],
    stages: list[Any],
    roles: list[Any],
) -> str:
    payload = {
        "kind": _GRAPH_CACHE_KIND,
        "config": _workflow_config_signature(config, stages=stages, roles=roles),
        "tasks": [
            {
                "id": str(getattr(task, "id", "") or ""),
                "status": str(getattr(task, "status", "") or ""),
                "assigned_to": str(getattr(task, "assigned_to", "") or ""),
                "active_dispatch_id": str(getattr(task, "active_dispatch_id", "") or ""),
            }
            for task in active_tasks
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{_GRAPH_CACHE_KIND}:{digest}"


def _workflow_config_signature(
    config: ZfConfig | None,
    *,
    stages: list[Any],
    roles: list[Any],
) -> dict[str, Any]:
    if config is None:
        return {"configured": False}
    return {
        "configured": True,
        "stages": [
            {
                "id": str(getattr(stage, "id", "") or ""),
                "trigger": str(getattr(stage, "trigger", "") or ""),
                "topology": str(getattr(stage, "topology", "") or ""),
                "roles": list(getattr(stage, "roles", []) or []),
                "target_ref": str(getattr(stage, "target_ref", "") or ""),
                "aggregate": {
                    "mode": str(getattr(getattr(stage, "aggregate", None), "mode", "") or ""),
                    "success_event": str(getattr(getattr(stage, "aggregate", None), "success_event", "") or ""),
                    "failure_event": str(getattr(getattr(stage, "aggregate", None), "failure_event", "") or ""),
                },
            }
            for stage in stages
        ],
        "roles": [
            {
                "name": str(getattr(role, "name", "") or ""),
                "instance_id": str(getattr(role, "instance_id", "") or ""),
                "backend": str(getattr(role, "backend", "") or ""),
                "role_kind": str(getattr(role, "role_kind", "") or ""),
                "triggers": list(getattr(role, "triggers", []) or []),
                "publishes": list(getattr(role, "publishes", []) or []),
            }
            for role in roles
        ],
    }
