"""Resolve board membership from an admitted Run, its immutable graph and its dispatches."""

from __future__ import annotations

from collections import defaultdict

from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.durable_mailbox_authority import approved_stage_graph, approved_stage_roster, terminal
from zf.runtime.pause_lifecycle import PAUSE_EVENT_TYPES, RESUME_EVENT_TYPES, is_run_dispatch_paused


def board_policy_enabled(config):
    return flow_metadata_for(config, "prd").get("run_board_policy", {}).get("enabled") is True


def legacy_boards(config):
    metadata = getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {}
    boards = metadata.get("run_boards")
    return boards if isinstance(boards, dict) else {}


class BoardPaused(ValueError):
    """A reversible hold on board access and delivery for this Run."""


def board_for_run(state, config, events, operations, run):
    # Stock operator actions have a legacy blank origin. Only accept that origin for
    # pause/resume controls. Worker and integration events cannot reopen the board.
    controls = PAUSE_EVENT_TYPES | RESUME_EVENT_TYPES | {"run.paused", "run.resumed"}
    facts = [e for e in events if e.origin == "kernel" or (not e.origin and e.type in controls)]
    if is_run_dispatch_paused(facts, run):
        raise BoardPaused("board run is paused")
    if run in legacy_boards(config):
        board = legacy_boards(config)[run]
        if (isinstance(board, dict) and board.get("enabled") is True
                and isinstance(board.get("members"), list) and board.get("generation")):
            return board
        raise ValueError("this run has no enabled board with a bound audience")
    if not run or not board_policy_enabled(config):
        raise ValueError("this run has no enabled board with a bound audience")
    kernel = [e for e in events if e.origin == "kernel"]
    scoped = [e for e in kernel if (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    if terminal(scoped, run):
        raise ValueError("board run is terminal")
    invocation = next((e for e in reversed(scoped) if e.type == "workflow.invoke.accepted"
                       and e.payload.get("request_revision") and e.payload.get("workflow_generation")), None)
    if not invocation:
        raise ValueError("board requires an admitted workflow invocation")
    approval = approved_stage_graph(state, kernel, run)
    if not approval:
        raise ValueError("board requires a current approved proposal")
    graph, scope, _approved = approval
    policy = flow_metadata_for(config, "prd")["run_board_policy"]
    if (graph.get("flow_kind") != "prd" or graph.get("run_board_policy") != policy
            or invocation.payload.get("request_id") != scope["request_id"]
            or invocation.payload.get("request_revision") != scope["revision"]
            or invocation.payload.get("workflow_generation") != scope["proposal_digest"]
            or invocation.payload.get("effective_config_digest") != scope["effective_config_digest"]):
        raise ValueError("board requires the invoked proposal, revision and frozen policy")
    # Bind the admission to the exact configuration that was invoked.
    if not any(e.type == "run.admission.admitted" and e.payload.get("request_id") == scope["request_id"]
               and e.payload.get("effective_config_digest") == scope["effective_config_digest"] for e in scoped):
        raise ValueError("board requires the admitted Run")
    # Replaying a duplicate receipt for the same generation must not drop live members.
    sources = [(i, e) for i, e in enumerate(scoped) if e.type == "workflow.invoke.accepted"
               and e.payload.get("workflow_generation") == scope["proposal_digest"]
               and e.payload.get("request_revision") == scope["revision"]]
    floor = sources[0][0]
    generation = ""
    for i, e in enumerate(scoped[floor:], floor):
        p = e.payload
        if (e.type in {"task_map.ready", "task.pipeline.generation.admitted"}
                and p.get("workflow_generation", scope["proposal_digest"]) == scope["proposal_digest"]
                and p.get("task_map_generation") and generation != p["task_map_generation"]):
            floor, generation = i, p["task_map_generation"]

    active = {oid: op for oid, op in operations.items()
              if op.get("workflow_run_id") == run and op.get("status") == "running"
              and not op.get("admitted_call_result_ref") and not op.get("call_result_admitted_event_id")}
    # A current attempt must have a trusted dispatch in this generation. A role name in the
    # palette, a requested operation, or a worker-authored dispatch is insufficient.
    receipts = {}
    for e in scoped[floor:]:
        if e.type not in {"fanout.child.dispatched", "fanout.synth.dispatched", "task.pipeline.stage.dispatched"}:
            continue
        p = e.payload
        oid = p.get("operation_id")
        op = active.get(oid)
        if (not op or p.get("role_instance") != op.get("role_instance")
                or p.get("workflow_generation", scope["proposal_digest"]) != scope["proposal_digest"]
                or (p.get("task_map_generation") and p["task_map_generation"] != generation)):
            continue
        if e.type == "task.pipeline.stage.dispatched":
            # Pipeline's WRC scheduler and transport attempt have distinct IDs; both bind the dispatch and lease.
            if (not op.get("dispatch_id") or not op.get("lease_id") or not generation
                    or any(p.get(k) != op.get(k) for k in ("dispatch_id", "lease_id", "task_id"))
                    or p.get("task_map_generation") != generation):
                continue
        elif p.get("attempt_id") != op.get("active_attempt_id") or p.get("request_hash") != op.get("request_hash"):
            continue
        receipts[oid] = op
    groups = defaultdict(set)
    authorized = set()
    controlled_stages = set()
    if graph.get("stage_teams") or generation:
        for address, ids in approved_stage_roster(state, kernel, active, run, approval):
            stage = address["stage"]
            controlled_stages.add(stage)
            for oid in ids:
                if oid in receipts:
                    groups[stage].add(receipts[oid]["role_instance"])
                    authorized.add(oid)
    # Stages without a team strategy use their fixed, approved graph roles and WRC dispatch.
    for node in graph["nodes"]:
        stage = node.get("stage_id")
        selected = node.get("metadata", {}).get("team_obligations")
        if not node["node_id"].startswith("stage:") or selected or stage in controlled_stages:
            continue
        for oid, op in receipts.items():
            role = op["role_instance"]
            stage_id = op.get("parent_stage_id")
            stages = {stage_id, f"prd-lanes-{stage_id}"} if stage_id in {"impl", "verify"} else {stage_id}
            if stage in stages and role in node.get("roles", []):
                groups[stage].add(role)
                authorized.add(oid)
    return {
        **policy,
        "generation": canonical_json_sha256([run, scope["proposal_digest"], generation]),
        "members": sorted({role for roles in groups.values() for role in roles}),
        "groups": {stage: sorted(roles) for stage, roles in groups.items()},
        "authorized_operations": authorized,
    }


def permits(board, operation):
    return (operation.get("role_instance") in board["members"] and
            ("authorized_operations" not in board or operation.get("operation_id") in board["authorized_operations"]))
