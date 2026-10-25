"""Pure, version-bound SDLC graph projection. No scheduling or semantic inference."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from zf.runtime.plan_modules import plan_modules
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.goal_coverage_graph import build_goal_coverage_graph
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.sdlc_graph_timeline import build_timeline
from zf.runtime.workflow_trace_fanout import build_fanout_runs

VIEWS = ["lifecycle", "modules", "workflow", "trace", "delivery", "timeline"]
VERDICTS = {"task.done.accepted", "judge.passed", "discriminator.passed",
            "verify.passed", "test.passed", "review.approved", "static_gate.passed"}
DELIVERY = {"ship.completed", "run.delivery.settled", "candidate.integration.completed"}
RUN_TERMINAL = {"run.goal.completed": "done", "run.completed": "done", "run.goal.blocked": "blocked",
                "run.failed": "failed", "run.cancelled": "cancelled", "run.abandoned": "abandoned"}


def activity_event(event) -> dict:
    """Bound transcript excerpts; the ledger keeps the complete event body."""
    result = asdict(event)
    payload = dict(result["payload"])
    for key in ("tool_input", "tool_response", "tool_output", "response"):
        if key in payload:
            text = json.dumps(payload[key], ensure_ascii=False)
            if len(text) > 480:
                payload[key] = text[:480]
                result["excerpted"] = True
    result["payload"] = payload
    return result


def build_sdlc_graph(source: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    diagnostics = list(source["diagnostics"])

    def node(identity, kind, label, *, views=VIEWS, status="unknown", **data):
        if identity not in nodes:
            nodes[identity] = {"id": identity, "kind": kind, "label": str(label),
                               "status": str(status), "views": list(views), "data": data}
        else:
            nodes[identity]["views"] = sorted(set(nodes[identity]["views"]) | set(views))
        return identity

    def edge(start, end, relation, *, views=VIEWS, **data):
        if start == end:
            return
        key = (start, end, relation)
        edges[key] = {"id": f"{start}|{relation}|{end}", "source": start,
                      "target": end, "relation": relation, "views": list(views), **data}

    scope, request, requirement = source["scope"], source["request"], source["requirement"]
    requirement_body = requirement.get("requirement", requirement)
    req = node("request:" + scope["request_id"], "requirement",
               requirement_body.get("objective") or request.get("objective") or scope["request_id"],
               views=["lifecycle", "trace", "modules"], status="confirmed" if request.get("confirmed") else "draft",
               revision=scope["revision"], requirement=requirement)
    run = node("run:" + (scope["run_id"] or scope["request_id"]),
               "run" if scope.get("execution_started") else "preparation",
               scope["run_id"] if scope.get("execution_started") else "Workflow 准备 / 待批准启动",
               views=["lifecycle", "workflow", "trace", "delivery"],
               status="unknown" if scope["historical"] else request.get("status", "draft"),
               **scope)
    edge(req, run, "proposes", views=["lifecycle", "trace"])
    compiled = source["compiled"]
    for item in compiled.get("nodes", []):
        identity = "workflow:" + item["node_id"]
        node(identity, "workflow", item.get("label") or item["node_id"], views=["workflow"],
             status="pending", definition=item, stage_id=item.get("stage_id", ""),
             roles=[r for r in compiled.get("roles", [])
                    if r.get("name") in item.get("roles", []) or r.get("instance_id") in item.get("roles", [])])
    for item in compiled.get("edges", []):
        edge("workflow:" + item["from_node"], "workflow:" + item["to_node"], item.get("kind", "workflow"),
             views=["workflow"], event=item.get("event", ""), condition=item.get("condition", ""))

    version = source["versions"][-1] if source["versions"] else {}
    task_map = version.get("body", {})
    module_projection = plan_modules(task_map)
    diagnostics.extend(module_projection["diagnostics"])
    planned = {str(t.get("task_id") or t.get("id")): t for t in task_map.get("tasks", []) if isinstance(t, dict)}
    tasks = source["tasks"]
    event_rows = source["events"]
    accepted: dict[str, list[str]] = {}
    latest_generation = max((seq for seq, e in event_rows if e.type in {
        "task.pipeline.generation.admitted", "task_map.materialization.committed", "task_map.admitted",
    }), default=0)
    for seq, event in event_rows:
        # Legacy or worker declarations remain visible, but never turn a Task green.
        if seq >= latest_generation and event.origin == "kernel" and event.type == "task.done.accepted":
            accepted.setdefault(str(event.task_id or event.payload.get("task_id") or ""), []).append(event.id)
        if (seq >= latest_generation and event.origin == "kernel" and event.type == "run.goal.completed"
                and event.payload.get("workflow_run_id") == scope["run_id"]
                and version.get("task_map_generation")
                and same_task_map_generation(str(event.payload.get("task_map_generation") or ""),
                                             version["task_map_generation"])):
            for task_id in event.payload.get("completed_task_ids", []):
                accepted.setdefault(str(task_id), []).append(event.id)
    for identity in sorted(set(planned) | set(tasks)):
        task, p = tasks.get(identity), planned.get(identity, {})
        status = task.status if task and not scope["historical"] else "draft" if not task else "unknown"
        if identity in planned and version.get("admitted") is False:
            status = "draft"
        if status == "done" and not accepted.get(identity):
            status = "evidence_missing"
        dependencies = p.get("blocked_by", task.blocked_by if task else [])
        anchor = bool(task and task.contract.evidence_contract.get("workflow_fanout_anchor"))
        node("task:" + identity, "run_anchor" if anchor else "task", p.get("title") or (task.title if task else identity),
             views=["workflow", "trace"] if anchor else VIEWS,
             status=status, task_id=identity, planned=p,
             actual=asdict(task) if task and not scope["historical"] else {},
             plan_version={k: v for k, v in version.items() if k != "body"},
             accepted_event_ids=accepted.get(identity, []), blocked_by=dependencies)
        for dep in dependencies:
            edge("task:" + str(dep), "task:" + identity, "depends_on", views=["modules", "trace", "timeline"])
        for claim in p.get("goal_claim_ids", []):
            edge("claim:" + str(claim), "task:" + identity, "implemented_by", views=["lifecycle", "modules", "trace"])
        if task and task.evidence and not scope["historical"]:
            for commit in dict.fromkeys([task.evidence.commit, *task.evidence.commits]):
                if commit:
                    cid = node("commit:" + commit, "commit", commit[:12], views=["trace", "delivery"],
                               commit=commit, source="TaskStore.evidence")
                    edge("task:" + identity, cid, "implemented_at", views=["trace", "delivery"])
    for claim in task_map.get("goal_claims", []):
        if not isinstance(claim, dict):
            continue  # The existing Goal Coverage adapter handles legacy strings.
        identity = str(claim.get("goal_claim_id") or claim.get("id") or "")
        if identity:
            mapped = any(identity in p.get("goal_claim_ids", []) for p in planned.values())
            cid = node("claim:" + identity, "claim", claim.get("statement") or claim.get("text") or identity,
                       views=["lifecycle", "modules", "trace"], status="mapped" if mapped else "uncovered", claim=claim)
            edge(req, cid, "declares", views=["lifecycle", "modules", "trace"])
    for module in module_projection["modules"]:
        mid = node("module:" + module["module_id"], "module", module["title"],
                   views=["lifecycle", "modules", "trace"], declaration=module)
        parent = "module:" + module["parent_module_id"] if module.get("parent_module_id") else req
        edge(parent, mid, "decomposes", views=["lifecycle", "modules", "trace"])
        for task_id in module.get("task_ids", []):
            edge(mid, "task:" + task_id, "contains", views=["lifecycle", "modules", "trace"])
        for claim in module.get("goal_claim_ids", []):
            edge("claim:" + claim, mid, "realized_by", views=["lifecycle", "modules", "trace"])
        for ref in module.get("design_refs", []):
            aid = node("artifact:" + ref, "design", ref, views=["lifecycle", "modules", "trace"], ref=ref)
            edge(aid, mid, "designs", views=["lifecycle", "modules", "trace"])
    for preview in source["plans"]:
        pid = node("plan:" + preview["plan_id"], "plan", "Plan / " + preview["plan_id"],
                   views=["lifecycle", "trace", "delivery"], status=preview["status"],
                   plan_id=preview["plan_id"], refs=preview.get("refs", {}))
        edge(req, pid, "plans", views=["lifecycle", "trace"])
        edge(pid, run, "guides", views=["lifecycle", "trace"])
        for artifact in preview.get("artifacts", []):
            aid = node("artifact:" + artifact["ref"], "artifact", artifact["name"],
                       views=["lifecycle", "trace", "delivery"], **artifact, plan_id=preview["plan_id"])
            edge(pid, aid, "binds", views=["lifecycle", "trace", "delivery"])
    operations = reduce_workflow_operations([e for _, e in event_rows], workflow_run_id=scope["run_id"])
    operation_events = {event_id: op["operation_id"] for op in operations.values()
                        for event_id in op.get("source_event_ids", [])}
    for _ in range(len(event_rows) + 1):
        before = len(operation_events)
        for _, event in event_rows:
            operation_id = event.payload.get("operation_id") or operation_events.get(event.causation_id)
            if operation_id in operations:
                operation_events.setdefault(event.id, operation_id)
        if len(operation_events) == before:
            break
    fanout_order, fanout_verdicts, fanout_stages = {}, {}, set()
    for seq, event in event_rows:
        identity = str(event.payload.get("fanout_id") or "")
        if identity:
            fanout_order.setdefault(identity, seq)
            if event.type in {"fanout.aggregate.completed", "fanout.synth.completed"} and event.payload.get("status") in {
                "failed", "completed", "cancelled", "superseded",
            }:
                fanout_verdicts[identity] = event.payload["status"]
            elif event.type == "integration.failed":
                fanout_verdicts[identity] = "failed"
    for fanout in sorted(build_fanout_runs(event_rows), key=lambda f: fanout_order[f["fanout_id"]]):
        fanout["status"] = fanout_verdicts.get(fanout["fanout_id"], fanout.get("status", "unknown"))
        fid = "fanout:" + fanout["fanout_id"]
        stage = "workflow:stage:" + str(fanout.get("stage_id") or "").split(":", 1)[0]
        node(fid, "fanout", fanout.get("stage_id") or fanout["fanout_id"],
             views=["workflow", "trace", "timeline"], status=fanout.get("status", "unknown"), fanout=fanout)
        edge(stage if stage in nodes else run, fid, "fanout", views=["workflow", "trace"])
        if stage in nodes:
            fanout_stages.add(stage)
            nodes[stage]["status"] = fanout.get("status", "unknown")
        for child in fanout.get("child_runs", []):
            cid = node(f"{fid}:child:{child['child_id']}", "agent", child.get("worker_id") or child.get("role") or child["child_id"],
                       views=["workflow", "trace", "timeline"], status=child["status"], child=child)
            edge(fid, cid, "delegates", views=["workflow", "trace"])
            if child.get("task_id"):
                edge("task:" + child["task_id"], cid, "execution", views=["workflow", "trace"])
    for operation in operations.values():
        related = [e for _, e in event_rows if e.id in operation.get("source_event_ids", [])]
        operation["requested_at"] = next((e.ts for e in related if e.type == "workflow.operation.requested"), "")
        operation["finished_at"] = operation.get("last_event_at", "") if operation["status"] in {
            "settled", "failed", "cancelled", "superseded", "blocked",
        } else ""
        activity = [e for _, e in event_rows if operation_events.get(e.id) == operation["operation_id"]
                    and e.type.startswith(("agent.", "codex.hook.", "claude.hook.", "provider."))]
        operation["recent_activity"] = [activity_event(e) for e in activity[-10:]]
        configured = next((r for r in compiled.get("roles", []) if operation.get("role_instance")
                           in {r.get("name"), r.get("instance_id")}), {})
        usage = next((e for e in reversed(activity) if e.type == "agent.usage"), None)
        operation["agent_binding"] = {
            "backend": usage.payload.get("backend") if usage else configured.get("backend", ""),
            "model": usage.payload.get("model") if usage else configured.get("model", ""),
            "skills": configured.get("skills", []),
            "source": "provider_usage" if usage else "config_snapshot" if configured else "unknown",
            "source_event_id": usage.id if usage else "",
        }
    stage_operations: dict[str, list[dict]] = {}
    for oid, operation in operations.items():
        op = node("operation:" + oid, "operation", operation.get("role_instance") or oid,
                  views=["workflow", "trace", "timeline"], **operation)
        stage = "workflow:stage:" + operation.get("parent_stage_id", "")
        role = "workflow:role:" + operation.get("role_instance", "")
        parent = stage if stage in nodes else role if role in nodes else run
        stage_operations.setdefault(parent, []).append(operation)
        edge(parent, op, "executes", views=["workflow"])
        tid = "task:" + operation.get("task_id", "")
        if tid in nodes:
            edge(tid, op, "execution", views=["workflow", "trace", "timeline"])
        if operation.get("parent_operation_id"):
            edge("operation:" + operation["parent_operation_id"], op, "delegates", views=["workflow", "trace"])
    for parent, rows in stage_operations.items():
        if parent == run:
            continue
        # A later invocation replaces the same role's stage summary, while all
        # operation nodes and their failure evidence remain inspectable.
        latest_roles = {r.get("role_instance") or r["operation_id"]: r for r in rows}
        states = {r["status"] for r in latest_roles.values()}
        if parent not in fanout_stages:
            nodes[parent]["status"] = next((s for s in ("running", "blocked", "failed", "reserved", "requested", "cancelled", "superseded") if s in states), "settled")
        nodes[parent]["data"]["operation_ids"] = [r["operation_id"] for r in rows]

    timeline = []
    for seq, event in event_rows:
        p = event.payload
        timeline.append({"seq": seq, "id": event.id, "ts": event.ts, "type": event.type,
                         "task_id": event.task_id, "actor": event.actor, "origin": event.origin})
        tid = "task:" + str(event.task_id or p.get("task_id") or "")
        oid = "operation:" + str(p.get("operation_id") or "")
        parent = oid if oid in nodes else tid if tid in nodes else run
        is_gate = event.type in VERDICTS or event.type in RUN_TERMINAL or event.type.endswith((".failed", ".rejected", ".blocked"))
        is_delivery = event.type in DELIVERY or event.type.startswith(("ship.", "candidate.", "release."))
        is_rework = event.type.startswith("task.rework.") or event.type == "task.fix_spawned"
        attempt = str(p.get("active_attempt_id") or p.get("attempt_id") or "")
        if attempt:
            aid = node("attempt:" + attempt, "attempt", attempt, views=["workflow", "trace", "timeline"],
                       status=event.type, source_event_id=event.id, payload=p)
            nodes[aid]["status"] = event.type
            nodes[aid]["data"].setdefault("events", []).append(event.id)
            edge(parent, aid, "attempt", views=["workflow", "trace", "timeline"])
        if not (is_gate or is_delivery or is_rework):
            continue
        if event.type in RUN_TERMINAL and event.origin == "kernel":
            for identity in (run, "workflow:terminal:done"):
                if identity in nodes:
                    nodes[identity]["status"] = RUN_TERMINAL[event.type]
                    nodes[identity]["data"]["terminal_event_id"] = event.id
        status = "accepted" if event.origin == "kernel" and (event.type in VERDICTS or event.type in DELIVERY or event.type in RUN_TERMINAL) else "reported"
        if event.type.endswith((".failed", ".rejected", ".blocked")):
            status = "failed" if event.origin == "kernel" else "reported"
        if event.type in RUN_TERMINAL and event.origin == "kernel":
            terminal_status = RUN_TERMINAL[event.type]
            status = "accepted" if terminal_status == "done" else terminal_status
        if event.type == "candidate.integration.completed" and event.origin == "kernel":
            # Completion records both successful and failed integration attempts.
            status = {"quality_failed": "failed", "conflict": "failed", "stale": "stale"}.get(p.get("status"),
                "accepted" if p.get("status") == "updated" and p.get("quality_status") == "passed" else "unknown")
        eid = node("event:" + event.id, "delivery" if is_delivery else "rework" if is_rework else "evidence",
                   event.type, views=["lifecycle", "trace", "delivery", "timeline"], status=status,
                   event=asdict(event), event_id=event.id, source_seq=seq)
        edge(parent, eid, "produces", views=["lifecycle", "trace", "delivery", "timeline"])
        if event.causation_id:
            edge("event:" + event.causation_id, eid, "causes", views=["trace", "delivery", "timeline"])
        for key in ("commit", "commit_sha", "head_commit", "target_commit"):
            if isinstance(p.get(key), str) and p[key]:
                cid = node("commit:" + p[key], "commit", p[key][:12], views=["trace", "delivery"], commit=p[key])
                edge(eid, cid, "references", views=["trace", "delivery"])
        for key, value in p.items():
            if key.endswith("_ref") and isinstance(value, (str, dict)) and value:
                ref = value if isinstance(value, str) else str(value.get("ref") or "")
                if ref:
                    aid = node("artifact:" + ref, "artifact", key, views=["trace", "delivery"], ref=ref, descriptor=value)
                    edge(eid, aid, "evidence_ref", views=["trace", "delivery"])
    # Reuse the existing admitted-result/current-generation verifier. Do not infer
    # claim closure from Task status or a worker's success declaration.
    goal_coverage = {}
    if task_map.get("goal_claims") or source.get("goal_claim_set"):
        pinned_map = {**task_map, "workflow_run_id": scope["run_id"]}
        for key in ("goal_id", "task_map_generation", "task_map_digest"):
            if version.get(key):
                pinned_map[key] = version[key]
        if source.get("goal_claim_set"):
            pinned_map["goal_id"] = source["goal_claim_set"]["goal_id"]
        try:
            goal_coverage = build_goal_coverage_graph(task_map=pinned_map,
                tasks={} if scope["historical"] else tasks, events=event_rows,
                project_id=scope.get("project_id", ""), feature_id=str(task_map.get("feature_id") or ""),
                task_map_ref=version.get("ref", ""), goal_claim_set=source.get("goal_claim_set"))
        except ValueError as exc:
            diagnostics.append(f"Goal Coverage 不可用：{exc}")
        for item in goal_coverage.get("nodes", []):
            identity, kind = item["node_id"], item["kind"]
            if kind == "task":
                continue
            if kind == "goal_claim":
                node(identity, "claim", item["title"], views=["lifecycle", "modules", "trace", "delivery"])
                nodes[identity]["data"]["coverage"] = item
                nodes[identity]["status"] = item["closure"] if item["closure"] != "unknown" else (
                    "stale" if item["task_verification"] == "stale" else "mapped" if item["task_ids"] else "uncovered")
            else:
                node(identity, kind, item["title"], views=["trace", "delivery"],
                     status=item.get("status", "unknown"), coverage=item)
        for item in goal_coverage.get("edges", []):
            if item["kind"] != "depends_on":
                edge(item["from"], item["to"], item["kind"], views=["trace", "delivery"])
        diagnostics.extend(f"Goal Coverage: {d['code']}: {d.get('stale_reasons') or d.get('message', '')}"
                           for d in goal_coverage.get("diagnostics", []))
    # Missing external integration is a capability, not a manufactured lifecycle node.
    capabilities = {"production": {"status": "unconnected", "reason": "未绑定部署环境与生产健康数据源"},
                    "modules": {"status": module_projection["source"]},
                    "release": {"status": "observed" if any(e.origin == "kernel" and e.type in {
                        "ship.completed", "run.delivery.settled",
                    } for _, e in event_rows) else "unknown"}}
    resolved_edges = [e for e in edges.values() if e["source"] in nodes and e["target"] in nodes]
    missing = [e for e in edges.values() if e["source"] not in nodes or e["target"] not in nodes]
    if missing:
        diagnostics.append(f"{len(missing)} 条引用的目标尚未进入本版本图；未补造节点。")
    analysis = build_timeline(list(operations.values()), list(nodes.values()), resolved_edges)
    return {"schema_version": "sdlc-workbench.v1", "scope": scope, "nodes": list(nodes.values()),
            "edges": resolved_edges, "diagnostics": diagnostics, "unresolved_edges": missing,
            "coverage": {"total_nodes": len(nodes), "included_nodes": len(nodes),
                         "total_edges": len(edges), "included_edges": len(resolved_edges), "truncated": False},
            "capabilities": capabilities, "goal_coverage": goal_coverage, "plans": source["plans"], "timeline": timeline,
            "analysis": analysis, "versions": [{k: v for k, v in row.items() if k != "body"} for row in source["versions"]]}
