"""Execution facts for Plan authors/reviewers; never a semantic approval gate."""

from __future__ import annotations

from collections import Counter, deque
import hashlib
import json
from typing import Any, Mapping
from pathlib import Path


def attach_execution_review(
    *, state_dir: Path, payload: dict[str, Any], review: Mapping[str, Any],
    source_event_id: str,
) -> None:
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    from zf.runtime.plan_execution_delivery import execution_review_delivery

    body, details = execution_review_delivery(review)
    for item in details:
        detail = write_immutable_json_sidecar(
            state_dir, item["body"], root=item["root"], kind=item["kind"],
            schema_version=item["schema_version"], created_by="plan-preflight",
            source_event_id=source_event_id,
        )
        payload.setdefault("input_refs", []).append({
            **detail, "source_id": item["source_id"],
            "artifact_id": item["source_id"] + ".json", "allowed_paths": ["$"],
        })
    descriptor = write_immutable_json_sidecar(
        state_dir, body, root="plan-execution-reviews",
        kind="plan_execution_review", schema_version="plan-execution-review.v1",
        created_by="plan-preflight", source_event_id=source_event_id,
    )
    payload.setdefault("input_refs", []).append({
        **descriptor, "source_id": "plan-execution-review",
        "artifact_id": "plan-execution-review.json", "allowed_paths": ["$"],
    })


def execution_policy(config: Any) -> dict[str, Any]:
    from zf.runtime.workflow_route_policy import effective_execution_policy_digest

    workflow = getattr(config, "workflow", None)
    host = getattr(getattr(config, "runtime", None), "host_verification", None)
    return {
        "host_verification_commands": list(host.commands) if host and host.enabled else [],
        "effective_policy_digest": effective_execution_policy_digest(config),
        "setup_declared": bool(str(getattr(getattr(config, "project", None),
                                         "setup_script", "") or "").strip()),
        "impl_self_check_required": bool(getattr(workflow, "impl_self_check_required", False)),
        "stages": [
            {"id": stage.id, "trigger": stage.trigger, "topology": stage.topology,
             "roles": list(getattr(stage, "roles", []) or [])}
            for stage in getattr(workflow, "stages", [])
        ],
        "writer_instances": [
            {"instance_id": role.instance_id, "flow_kind": role.flow_kind,
             "default_profile": role.execution.default_profile,
             "profile_allowlist": list(role.execution.profile_allowlist)}
            for role in getattr(config, "roles", []) if role.role_kind == "writer"
        ],
    }


def planning_execution_context(config: Any, *, flow_kind: str = "") -> list[str]:
    """Expose compiled facts before the Planner authors its Task Map."""
    from zf.core.workflow.flow_metadata import flow_metadata_for

    if config is None:
        return ["## Compiled planning execution facts",
                "Configuration unavailable; execution policy and approval requirements are unknown.", ""]
    policy = execution_policy(config)
    metadata = flow_metadata_for(config, flow_kind)
    policy.update({
        "plan_review": metadata.get("plan_review", {}),
        "plan_approval_required": bool(config.workflow.plan_approval_enabled),
        "task_pipeline": metadata.get("task_pipeline", {}),
        "rolling_smoke_policy": _rolling_smoke_policy(),
        "flow_kind": flow_kind,
        "authorizes_execution": False,
    })
    return [
        "## Compiled planning execution facts",
        "These are facts from this runtime's loaded config, not approval or evidence "
        "that future stages have run. Use them to assign verification ownership. "
        "After authoring the Task Map, perform the execution-readiness review. "
        "Keep future gates pending; do not try to observe Plan approval before submitting a Plan.",
        "```json", json.dumps(policy, ensure_ascii=False, indent=2), "```", "",
    ]


def review_execution(
    task_map: Mapping[str, Any], *, ports: Mapping[str, Any],
    policy: Mapping[str, Any], metadata: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
    usage_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe actual wiring and review questions without judging business slices."""
    tasks = [t for t in _rows(task_map.get("tasks")) if isinstance(t, Mapping)]
    graph = _graph(tasks)
    pipeline = metadata.get("task_pipeline") or {}
    per_task = isinstance(pipeline, Mapping) and pipeline.get("mode") == "blocking"
    write_scope, writer_items = _review_writer_items(task_map)
    commands = [c for t in tasks for c in _rows(_object(t.get("validation")).get("commands"))
                if isinstance(c, Mapping)]
    owners = Counter(str(c.get("owner") or "impl_self_check") for c in commands)
    conventions = task_map.get("shared_conventions") or {}
    setup_commands = _rows(conventions.get("setup_commands")) if isinstance(conventions, Mapping) else []
    sizes = {name: len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
             for name, body in ports.items()}
    items = []
    kind = str(metadata.get("flow_kind") or "")
    stages = [s for s in policy.get("stages", [])
              if not kind or str(s.get("id") or "").startswith(kind + "-")]
    candidate_trigger = any(s.get("trigger") == "candidate.ready" for s in stages)
    host_commands = set(policy.get("host_verification_commands") or ())
    unregistered = [
        {"task_id": task.get("task_id") or task.get("id"), "command_id": cmd.get("id"),
         "command": cmd["command"]}
        for task in tasks for cmd in _rows(_object(task.get("validation")).get("commands"))
        if isinstance(cmd, Mapping) and cmd.get("owner") == "candidate_verify"
        and isinstance(cmd.get("command"), str) and host_commands and cmd["command"] not in host_commands
    ]
    if unregistered:
        items.append("candidate_commands_outside_host_registry")
    if owners.get("task_verify") and not per_task:
        items.append("task_verify_after_candidate" if candidate_trigger else "verify_wiring_unconfirmed")
    if setup_commands and not policy.get("setup_declared"):
        items.append("setup_not_wired")
    if not policy.get("impl_self_check_required"):
        items.append("self_check_not_required")
    requirements = [
        _task_requirements(task, writer_items.get(str(task.get("task_id") or task.get("id") or ""))
                           if per_task else None) for task in tasks
    ]
    if any(row.get("standard_with_empty_write_scope") for row in requirements):
        items.append("standard_with_empty_write_scope")
    # An optional Candidate probe can legitimately lack a mandatory AC. Report the
    # exact declarations to reviewers without turning them into a product verdict.
    mandatory_refs = {
        cid for task in tasks for ac in _rows(task.get("acceptance_criteria") or task.get("acceptance"))
        if isinstance(ac, Mapping) and ac.get("mandatory", True) is not False
        and ac.get("verification_owner") == "candidate_verify"
        for cid in _rows(ac.get("verification_command_ids")) + _rows(ac.get("command_ids"))
        if isinstance(cid, str)
    }
    for task, row in zip(tasks, requirements, strict=True):
        unbound = [
            c["id"] for c in _rows(_object(task.get("validation")).get("commands"))
            if isinstance(c, Mapping) and isinstance(c.get("id"), str) and c["id"]
            and c.get("owner") == "candidate_verify" and c.get("acceptance_ids") == []
            and not c.get("acceptance_id")
            and c["id"] not in mandatory_refs
        ]
        if unbound:
            row["unbound_candidate_command_ids"] = unbound
    if any(row.get("unbound_candidate_command_ids") for row in requirements):
        items.append("candidate_commands_without_mandatory_binding")
    writers = [role for role in policy.get("writer_instances", [])
               if not kind or role.get("flow_kind") in ("", kind)]
    from zf.runtime.plan_review_comparisons import execution_comparisons

    comparisons = execution_comparisons(tasks, requirements, host_commands, candidate_stage=candidate_trigger)
    return {
        "schema_version": "plan-execution-review.v1",
        **({"execution_comparisons": comparisons} if comparisons else {}),
        "scope": "mechanical_facts_for_semantic_review",
        "plan_review": metadata.get("plan_review", {}),
        "usage_observations": dict(usage_facts or {"available": False}),
        "input_binding": {
            "task_map_digest": _digest(task_map),
            "effective_policy_digest": str(policy.get("effective_policy_digest") or ""),
            **{key: (identity or {}).get(key, "") for key in (
                "workflow_run_id", "request_revision", "plan_revision", "task_map_generation",
                "workflow_generation", "requirement_digest", "prd_digest",
            )},
        },
        "graph": graph,
        "capacity": {
            "configured_writer_instances": writers,
            "configured_writer_count": len(writers),
            "dependency_level_peak_width": max(graph["level_widths"].values(), default=0),
            "write_scope_validation": write_scope,
        },
        "verification": {
            "candidate_commands_outside_host_registry": unregistered,
            "independent_task_verify": per_task,
            "task_pipeline_mode": pipeline.get("mode", "off") if isinstance(pipeline, Mapping) else "off",
            "command_owners": dict(owners),
            "rolling_smoke_policy": _rolling_smoke_policy(),
            "task_requirements": requirements,
            "stages": stages,
            "impl_self_check_required": bool(policy.get("impl_self_check_required")),
        },
        "environment": {
            "planned_setup_command_count": len(setup_commands),
            "setup_declared": bool(policy.get("setup_declared")),
        },
        "inputs": {"serialized_port_bytes": sizes,
                   "total_serialized_port_bytes": sum(sizes.values())},
        "review_items": items,
        "limitations": [
            "Counts do not establish slice quality or elapsed execution time.",
            ("The blocking Task Pipeline dispatches independent Task Verify when its task is ready; "
             "Candidate Verify begins after candidate.ready." if per_task and candidate_trigger else
             "Check the compiled Task Pipeline and stage triggers for actual Verify dispatch."),
            "Required-by owners use runtime selectors; declared execution_stage is only an annotation.",
            "Setup presence does not prove installation or product execution.",
            "Port byte counts are JSON serialization sizes, not model token usage.",
            "Dependency levels do not prove write independence or predict live worker availability.",
        ],
    }


def _digest(body: Mapping[str, Any]) -> str:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _review_writer_items(task_map: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict]]:
    from zf.runtime.writer_fanout_admission import validate_writer_task_items, writer_task_items

    try:
        items = writer_task_items(dict(task_map))
        validate_writer_task_items(items)
    except (RuntimeError, ValueError) as exc:
        return {"status": "failed", "reason": str(exc)}, {}
    return {"status": "passed"}, {item["task_id"]: item for item in items}


def _initial_readonly_verify(item: dict) -> dict:
    from zf.runtime.task_contract_snapshot import (
        normalize_acceptance_criteria, snapshot_verification_route,
    )
    from zf.runtime.verification_commands import (
        acceptance_required_for_stage, task_map_verification_commands,
        verification_command_required_for_stage,
    )
    from zf.runtime.writer_task_materialization import writer_task_pipeline_entry_mode

    raw = item["raw_task"]
    mode = writer_task_pipeline_entry_mode(item, raw)
    if mode == "standard" and raw.get("allowed_paths") == []:
        return {"standard_with_empty_write_scope": True}
    if mode != "verify_only":
        return {}
    task_id = item["task_id"]
    criteria = raw.get("acceptance_criteria") or raw.get("acceptance") or []
    if not criteria or not all(isinstance(c, dict) and (c.get("id") or c.get("acceptance_id"))
                               for c in criteria):
        return {"initial_readonly_verify": {"status": "requires_dispatch_contract"}}
    try:
        criteria = normalize_acceptance_criteria(criteria, task_id=task_id, contract_revision="",
                                                verification_tiers=item["verification_tiers"])
    except ValueError:
        return {"initial_readonly_verify": {"status": "requires_dispatch_contract"}}
    commands = task_map_verification_commands(raw)
    from zf.runtime.frozen_audit_scope import bind_frozen_audit_scope
    from zf.runtime.writer_task_materialization import writer_task_evidence_contract

    try:
        bind_frozen_audit_scope(writer_task_evidence_contract(raw), criteria, commands,
                                task_id=task_id, write_paths=raw.get("allowed_paths"))
    except ValueError as exc:
        return {"initial_readonly_verify": {"status": "invalid", "reason": str(exc)}}
    owner, _ = snapshot_verification_route(criteria, commands=commands, task_id=task_id)
    baseline = raw.get("immutable_baseline") or _object(raw.get("evidence_contract")).get("immutable_baseline", "")
    return {"initial_readonly_verify": {
        "verification_owner": owner,
        "declared_baseline": baseline,
        **({"frozen_candidate_acceptance_ids": [c["acceptance_id"] for c in criteria
                                               if c.get("frozen_candidate_only")]}
           if any(c.get("frozen_candidate_only") for c in criteria) else {}),
        "mandatory_acceptance_ids": [c["acceptance_id"] for c in criteria if c["mandatory"]
                                     and acceptance_required_for_stage(c, verification_owner=owner)],
        "command_ids": [c["id"] for c in commands if verification_command_required_for_stage(
            c, verification_owner=owner, task_id=task_id)],
    }}


def _task_requirements(task: Mapping[str, Any], writer_item: dict | None) -> dict[str, Any]:
    from zf.runtime.verification_commands import (
        acceptance_required_for_stage, verification_command_required_for_stage,
    )

    task_id = str(task.get("task_id") or task.get("id") or "")
    owners = ("impl_self_check", "task_verify", "candidate_verify")
    return {
        "task_id": task_id,
        **(_initial_readonly_verify(writer_item) if writer_item else {}),
        "rolling_smoke_command_ids": [
            str(command.get("id") or "")
            for command in _rows(_object(task.get("validation")).get("commands"))
            if isinstance(command, Mapping) and command.get("rolling_smoke") is True
        ],
        "commands": [
            {
                "command_id": str(command.get("id") or ""),
                "owner": str(command.get("owner") or "impl_self_check"),
                "producer_task_id": str(command.get("producer_task_id") or ""),
                "declared_execution_stage": str(command.get("execution_stage") or ""),
                "required_by_verification_owner": [
                    owner for owner in owners if verification_command_required_for_stage(
                        command, verification_owner=owner, task_id=task_id,
                    )
                ],
            }
            for command in _rows(_object(task.get("validation")).get("commands"))
            if isinstance(command, Mapping)
        ],
        "acceptance_criteria": [
            {
                "acceptance_id": str(criterion.get("id") or ""),
                "verification_owner": str(criterion.get("verification_owner") or "task_verify"),
                "declared_execution_stage": str(criterion.get("execution_stage") or ""),
                "command_ids": _rows(criterion.get("verification_command_ids")),
                "required_by_verification_owner": [
                    owner for owner in owners if acceptance_required_for_stage(
                        criterion, verification_owner=owner,
                    )
                ],
            }
            for criterion in _rows(task.get("acceptance_criteria"))
            if isinstance(criterion, Mapping)
        ],
    }


def _rolling_smoke_policy() -> dict[str, Any]:
    """Facts of candidate_incremental._run_rolling_smoke, not build inference."""
    return {
        "selector": "rolling_smoke == true",
        "order": "command_registry_order",
        "execution_workspace": "candidate_worktree",
        "implicit_unselected_commands": False,
        "writer_commands_replayed": False,
    }


def _graph(tasks: list[Mapping[str, Any]]) -> dict[str, Any]:
    nodes = {str(t.get("task_id") or t.get("id") or ""): t for t in tasks}
    dependencies = {
        key: set(str(d) for d in _rows(task.get("blocked_by", task.get("depends_on", []))))
        for key, task in nodes.items()
    }
    missing = sorted({d for ds in dependencies.values() for d in ds if d not in nodes})
    indegree = {key: len(ds) for key, ds in dependencies.items()}
    children: dict[str, list[str]] = {key: [] for key in nodes}
    for key, ds in dependencies.items():
        for dep in ds:
            if dep in children:
                children[dep].append(key)
    for dependents in children.values():
        dependents.sort()
    queue = deque(sorted(key for key, degree in indegree.items() if not degree))
    levels = {key: 1 for key in queue}
    predecessor: dict[str, str] = {}
    visited = set()
    while queue:
        key = queue.popleft()
        visited.add(key)
        for child in children[key]:
            depth = levels[key] + 1
            if (depth > levels.get(child, 1)
                    or (depth == levels.get(child) and key < predecessor[child])):
                levels[child] = depth
                predecessor[child] = key
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    complete = len(visited) == len(nodes)
    longest_path: list[str] | None = [] if complete else None
    if complete and visited:
        # Equal-length chains use task IDs for deterministic diagnostics, not priority.
        cursor = min(visited, key=lambda key: (-levels[key], key))
        while True:
            longest_path.append(cursor)
            if cursor not in predecessor:
                break
            cursor = predecessor[cursor]
        longest_path.reverse()
    tasks_by_level: dict[int, list[str]] = {}
    for key in sorted(visited):
        tasks_by_level.setdefault(levels[key], []).append(key)
    return {
        "task_count": len(tasks),
        "longest_dependency_chain": max(levels.values(), default=0) if complete else None,
        "longest_dependency_path": longest_path,
        "tasks_by_level": dict(sorted(tasks_by_level.items())),
        "direct_dependencies": {key: sorted(dependencies[key]) for key in sorted(nodes)},
        "direct_dependents": {key: children[key] for key in sorted(nodes)},
        "level_widths": dict(sorted(Counter(levels[k] for k in visited).items())),
        "missing_dependencies": missing,
        "unresolved_task_ids": sorted(set(nodes) - visited),
    }


def _rows(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def plan_candidate_writer_policy(config: Any) -> dict[str, Any]:
    """Snapshot the exact mechanical writer policy used after Plan."""

    from zf.runtime.candidate_verification_stages import has_candidate_verification_consumer
    from zf.runtime.plan_candidate_role_preflight import writer_role_policy

    workflow = getattr(config, "workflow", None)
    work_units = getattr(workflow, "work_units", None)
    split = getattr(work_units, "split_quality", None)
    compiled = execution_policy(config)
    return {
        "frozen_candidate_reader": has_candidate_verification_consumer(config),
        "host_verification_commands": compiled["host_verification_commands"],
        "harness_profile": str(getattr(workflow, "harness_profile", "") or ""),
        "role_closure": writer_role_policy(config),
        "execution": compiled,
        "candidate_quality_source": str(
            getattr(workflow, "candidate_quality_source", "auto") or "auto"
        ),
        "work_units": {
            "enabled": bool(getattr(work_units, "enabled", False)),
            "split_quality": {
                "mode": str(getattr(split, "mode", "warning") or "warning"),
                "max_scope_files": int(
                    getattr(split, "max_scope_files", 0) or 0
                ),
                "max_acceptance_criteria": int(
                    getattr(split, "max_acceptance_criteria", 0) or 0
                ),
                "require_validation_surface": bool(
                    getattr(split, "require_validation_surface", True)
                ),
            },
        },
    }


def plan_review_briefing(config: Any, *, flow_kind: str = "") -> list[str]:
    from zf.core.workflow.flow_metadata import flow_metadata_for

    policy = flow_metadata_for(config, flow_kind).get("plan_review") or {}
    if not policy:
        return []
    return [
        "Configured independent Plan review: " + json.dumps(policy, ensure_ascii=False),
        "Review selection is from the approved configuration, not the author's self-assessment. "
        "For targeted mode, check the named questions and affected dependencies/shared rules; "
        "state actual reviewed and unreviewed scope in summary with current artifact refs/digests "
        "and opening/scenario excerpts. Expand review if evidence reveals a wider conflict. "
        "Consume the pinned required reads, then retrieve supplementary port contents needed to answer "
        "each focus and its affected dependencies. Record actual evidence and unreviewed scope. "
        "First targeted review needs no historical approval; historical incremental reuse still needs "
        "its bound evidence. Targeted focus is not proof of whole-document approval. "
        "Do not rewrite the Planner's artifacts.",
    ]
