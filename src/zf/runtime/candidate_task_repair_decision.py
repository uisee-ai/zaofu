"""Compile an admitted OA unchanged-contract repair into the existing gap action."""
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def candidate_task_repair_action(runtime: Any, *, decision: Mapping, directive: Mapping,
                                 expected: Mapping, candidate: Mapping) -> dict:
    repair = directive.get("candidate_repair")
    if (not isinstance(repair, Mapping) or repair.get("classification") != "product_defect"
            or repair.get("contract_changed") is not False or not str(repair.get("rationale") or "").strip()):
        raise ValueError("candidate repair requires an explicit unchanged-contract product diagnosis")
    events = runtime.event_log.read_all()
    # A rejected product result and a blocked/unknown result are not equivalent.
    from zf.runtime.candidate_plan_recovery import candidate_reader_binding

    failure = next(e for e in events if e.id == expected["candidate_failure_id"])
    from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding

    aggregate = aggregate_candidate_binding(runtime.state_dir, events, failure_id=failure.id,
        run_id=decision["identity"]["workflow_run_id"], task_id=expected["target_task_id"])
    if len(aggregate["members"]) > 1 and expected.get("candidate_failure_scope") != "aggregate":
        raise ValueError("candidate repair requires a checkpoint containing all failed Reader outcomes")
    candidate = {**candidate, **{key: aggregate[key] for key in ("members", "unresolved_members", "failed_task_ids")}}
    _, result = candidate_reader_binding(runtime.state_dir, events,
        fanout_id=failure.payload["fanout_id"], operation_id=expected["candidate_operation_id"])
    if result.get("verdict") != "rejected":
        raise ValueError("candidate blocker requires diagnosis or Plan recovery, not product repair")
    if candidate.get("unresolved_members") or any(
        member.get("verdict") != "rejected" or member.get("execution_status") != "completed"
        for member in candidate.get("members", [])
    ):
        raise ValueError("candidate aggregate still contains blocked or unknown outcomes")
    identity = decision["identity"]
    run_id = identity["workflow_run_id"]
    if any(e.origin == "kernel" and e.type in {
        "run.cancelled", "run.failed", "run.completed", "run.goal.cancelled", "run.goal.completed",
    } and (e.payload.get("workflow_run_id") or e.payload.get("run_id") or e.correlation_id) == run_id for e in events):
        raise ValueError("candidate repair cannot revive a terminal Run")
    prior = {e.payload.get("rework_of") for e in events if e.type == "task_map.ready"
        and (e.payload.get("workflow_run_id") or e.correlation_id) == run_id
        and e.payload.get("pdd_id") == candidate["pdd_id"] and e.payload.get("rework_of")
        and e.payload.get("resume_checkpoint_ref") != "oa-candidate-repair-" + identity["operation_id"]}
    if len(prior) >= 2:
        raise ValueError("candidate product rework cap exhausted")
    package = hydrate_plan_artifact_package(runtime.state_dir, {
        "ref": identity["plan_artifact_package_ref"], "sha256": identity["plan_artifact_package_digest"]})
    if package["workflow_run_id"] != run_id or package["task_map_generation"] != identity["task_map_generation"]:
        raise ValueError("candidate repair Plan binding changed")
    ports = [*package["produced"], *package["inherited"]]
    descriptor = next(p for p in ports if p["logical_name"] == "task_map")
    task_map = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    original = {row["task_id"]: row for row in task_map.get("tasks", [])}
    gaps = repair.get("gap_tasks")
    if not isinstance(gaps, list) or not gaps or len(gaps) > 8:
        raise ValueError("candidate repair requires 1..8 explicit canonical gap Tasks")
    affected = set(candidate.get("failed_task_ids") or [])
    replaced = set()
    new_ids = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            raise ValueError("candidate repair gap must be a Task Map row")
        parents = gap.get("supersedes_task_ids")
        if (not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], str)
                or parents[0] not in affected
                or parents[0] not in original or parents[0] in replaced):
            raise ValueError("candidate repair must name one distinct implicated predecessor per Task")
        parent = parents[0]
        successor = gap.get("task_id")
        if not isinstance(successor, str) or not successor or successor in original or successor in new_ids:
            raise ValueError("candidate repair requires a new successor Task id")
        if gap.get("base_commit") != candidate["source_commit"]:
            raise ValueError("candidate repair must start at the frozen failed Candidate")
        # The Agent owns the semantic diagnosis. This narrow route only admits
        # exact copied obligations, with Task identity and Git base rebound.
        baseline = deepcopy(original[parent])
        baseline.update(task_id=successor, base_commit=candidate["source_commit"], supersedes_task_ids=[parent])
        if gap != baseline:
            raise ValueError("candidate repair changed the bound Task row; request scoped Plan revision")
        replaced.add(parent)
        new_ids.add(successor)
    if replaced != affected:
        raise ValueError("candidate repair must account for every implicated Task")
    # Preflight the real adapter before it can publish an amendment. Never
    # silently lose a protected declaration in a legacy gap serialization.
    from zf.runtime.module_gap_plan import build_gap_task_map_amend

    amended = build_gap_task_map_amend(task_map, gap_tasks=gaps,
        supersedes_task_map_ref=descriptor["ref"], successor_base_commit=candidate["source_commit"],
        preserve_task_rows=True)
    generated = {row["task_id"]: row for row in amended["tasks"]}
    from zf.runtime.canonical_gap_rows import canonical_gap_rows

    for gap in canonical_gap_rows(task_map, gaps, target_commit=candidate["source_commit"]):
        for key, value in gap.items():
            if key in {"supersedes_task_ids", "base_commit", "task_id", "wave", "source_refs"}:
                continue
            if generated[gap["task_id"]].get(key) != value:
                raise ValueError("candidate repair adapter cannot preserve declaration: " + key)
    return {
        "candidate_rework_action": "retrigger", "workflow_run_id": run_id, "trace_id": run_id,
        "pdd_id": candidate["pdd_id"], "source_event_id": failure.id, "source_event_type": failure.type,
        "source_commit": candidate["source_commit"], "candidate_base_commit": candidate["source_commit"],
        "candidate_ref": candidate["candidate_ref"], "candidate_event_id": candidate["candidate_event_id"],
        "task_map_ref": descriptor["ref"], "task_map_generation": identity["task_map_generation"],
        "plan_artifact_package_ref": identity["plan_artifact_package_ref"],
        "plan_artifact_package_digest": identity["plan_artifact_package_digest"],
        "source_index_ref": next((p["ref"] for p in ports if p["logical_name"] == "source_index"), ""),
        "rework_attempt": len(prior) + 1, "failed_task_ids": sorted(replaced),
        "checkpoint_id": "oa-candidate-repair-" + identity["operation_id"],
        "rework_feedback": [repair["rationale"], *directive["required_actions"]],
        "rework_summary": {"gap_tasks": deepcopy(gaps), "supersedes_task_ids": sorted(replaced),
                           "preserve_task_rows": True},
    }


def apply_candidate_task_repair(runtime: Any, *, event: Any, decision: Mapping,
                                directive: Mapping, expected: Mapping, candidate: Mapping) -> dict:
    from zf.runtime.control_actions import ControlledActionService

    payload = candidate_task_repair_action(runtime, decision=decision, directive=directive,
        expected=expected, candidate=candidate)
    service = ControlledActionService(runtime.state_dir, runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, actor="zf-cli", source="orchestrator-agent", surface="runtime")
    return service.execute(action="candidate-rework-apply", requested_action="candidate-rework-apply",
        payload=payload, requested=event)
