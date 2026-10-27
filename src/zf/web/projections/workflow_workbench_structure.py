"""A partial compiled Workflow canvas without Plan or node-detail hydration."""
from __future__ import annotations

from zf.core.task.store import TaskStore
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.web.projections.workflow_workbench_sources import scope_events


def read_structure_sources(context, detail: dict, *, as_of: int, all_events: list) -> dict:
    """Use the same explicit run scope while leaving artifact-backed views unopened."""
    request = detail["result"]
    request_id = str(request["request_id"])
    run_id = str(request.get("run_id") or detail.get("links", {}).get("run_id") or "")
    source_seq = min(as_of, len(all_events)) if as_of else len(all_events)
    events = all_events[:source_seq]
    tasks = TaskStore(context.state_dir / "kanban.json").list_all_with_archive()
    generations = task_pipeline_generation_contexts(events)
    scoped = scope_events(events, request_id=request_id, run_id=run_id, tasks=tasks,
                          generations=generations)
    task_ids = {str(e.task_id or e.payload.get("task_id") or "") for _, e in scoped}
    task_ids.update(key for key, value in generations.items()
                    if run_id and value.get("workflow_run_id") == run_id)
    scoped_tasks = {t.id: t for t in tasks if t.id in task_ids and (
        not t.execution_binding.workflow_run_id or t.execution_binding.workflow_run_id == run_id)}
    compiled = detail.get("artifacts", {}).get("effective_config", {}).get("compiled_graph", {})
    diagnostics = [] if compiled else ["此配置快照尚未包含编译图；重新准备 Proposal 后可显示结构。"]
    return {"scope": {"project_id": request.get("project_id", ""), "request_id": request_id,
                      "revision": request.get("revision"), "run_id": run_id,
                      "proposal_digest": request.get("proposal_digest", ""),
                      "source_seq": source_seq, "head_seq": len(all_events), "historical": bool(as_of),
                      "execution_started": any(e.type == "workflow.submit.accepted" for _, e in scoped)
                      if as_of else bool(detail.get("lifecycle", {}).get("submitted"))},
            "request": request, "requirement": detail.get("requirement", {}),
            "compiled": compiled, "events": scoped, "tasks": scoped_tasks,
            "goal_claim_set": None, "plans": [], "versions": [], "diagnostics": diagnostics}


def build_structure(source: dict, *, generation: str) -> dict:
    """Only compiled nodes are displayed; other graph views require the full graph."""
    graph = build_sdlc_graph(source)
    nodes = []
    for item in graph["nodes"]:
        if item["kind"] != "workflow":
            continue
        definition = item["data"].get("definition") or {}
        nodes.append({**item, "data": {"definition": {"type": definition.get("type", "")}}})
    ids = {node["id"] for node in nodes}
    edges = [edge for edge in graph["edges"] if edge["source"] in ids and edge["target"] in ids]
    return {"schema_version": "sdlc-workbench-structure.v1", "partial": True,
            "generation": generation, "scope": graph["scope"], "nodes": nodes, "edges": edges,
            "diagnostics": source["diagnostics"]}


def build_structure_node(source: dict, *, node_id: str) -> dict | None:
    """A selected compiled node's exact details, without loading any Plans."""
    graph = build_sdlc_graph(source)
    node = next((item for item in graph["nodes"] if item["id"] == node_id and item["kind"] == "workflow"), None)
    if node is None:
        return None
    return {"schema_version": "sdlc-workbench-structure-node.v1", "scope": graph["scope"], "node": node}
