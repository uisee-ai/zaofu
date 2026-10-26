"""Pin the configured route's graph, using the same explicit kind guard as dispatch."""

from copy import deepcopy

from zf.core.workflow.graph import WorkflowGraphCompiler


def compile_proposal_graph(config, family: str) -> dict:
    kind = family.lower()
    if kind in {"issueflow", "prdflow", "refactorflow"}:
        kind = kind.removesuffix("flow")
    scoped = deepcopy(config)
    if kind in {"issue", "prd", "refactor", "workflow"}:
        scoped.roles = [role for role in scoped.roles if not role.flow_kind or role.flow_kind == kind]
        scoped.workflow.stages = [stage for stage in scoped.workflow.stages
                                  if not stage.flow_kind or stage.flow_kind == kind]
        role_ids = {role.name for role in scoped.roles} | {role.instance_id for role in scoped.roles}
        scoped.workflow.rework_routing = {event: role for event, role in scoped.workflow.rework_routing.items()
                                          if role in role_ids}
    graph = WorkflowGraphCompiler().compile(scoped).to_dict()
    known = {node["node_id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        for key in ("from_node", "to_node"):
            identity = edge[key]
            if identity not in known and identity.startswith("event:"):
                graph["nodes"].append({"node_id": identity, "stage_id": "", "type": "external_event",
                                       "label": identity.removeprefix("event:"), "roles": [], "metadata": {}})
                known.add(identity)
    return {**graph, "flow_kind": kind,
            "roles": [{"name": role.name, "instance_id": role.instance_id, "backend": role.backend,
                       "model": role.model, "skills": role.skills} for role in scoped.roles]}
