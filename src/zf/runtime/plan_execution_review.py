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

    descriptor = write_immutable_json_sidecar(
        state_dir, dict(review), root="plan-execution-reviews",
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
    return {
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


def review_execution(
    task_map: Mapping[str, Any], *, ports: Mapping[str, Any],
    policy: Mapping[str, Any], metadata: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe actual wiring and review questions without judging business slices."""
    tasks = [t for t in _rows(task_map.get("tasks")) if isinstance(t, Mapping)]
    graph = _graph(tasks)
    pipeline = metadata.get("task_pipeline") or {}
    per_task = isinstance(pipeline, Mapping) and pipeline.get("mode") == "blocking"
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
    if owners.get("task_verify") and not per_task:
        items.append("task_verify_after_candidate" if candidate_trigger else "verify_wiring_unconfirmed")
    if setup_commands and not policy.get("setup_declared"):
        items.append("setup_not_wired")
    if not policy.get("impl_self_check_required"):
        items.append("self_check_not_required")
    writers = [role for role in policy.get("writer_instances", [])
               if not kind or role.get("flow_kind") in ("", kind)]
    return {
        "schema_version": "plan-execution-review.v1",
        "scope": "mechanical_facts_for_semantic_review",
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
            "write_scope_validation": _write_scope_validation(task_map),
        },
        "verification": {
            "independent_task_verify": per_task,
            "task_pipeline_mode": pipeline.get("mode", "off") if isinstance(pipeline, Mapping) else "off",
            "command_owners": dict(owners),
            "task_requirements": [_task_requirements(task) for task in tasks],
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
            "Task verification ownership does not create a dispatch stage.",
            "Required-by owners use runtime selectors; declared execution_stage is only an annotation.",
            "Setup presence does not prove installation or product execution.",
            "Port byte counts are JSON serialization sizes, not model token usage.",
            "Dependency levels do not prove write independence or predict live worker availability.",
        ],
    }


def _digest(body: Mapping[str, Any]) -> str:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_scope_validation(task_map: Mapping[str, Any]) -> dict[str, Any]:
    from zf.runtime.writer_fanout_admission import validate_writer_task_items, writer_task_items

    try:
        validate_writer_task_items(writer_task_items(dict(task_map)))
    except (RuntimeError, ValueError) as exc:
        return {"status": "failed", "reason": str(exc)}
    return {"status": "passed"}


def _task_requirements(task: Mapping[str, Any]) -> dict[str, Any]:
    from zf.runtime.verification_commands import (
        acceptance_required_for_stage, verification_command_required_for_stage,
    )

    task_id = str(task.get("task_id") or task.get("id") or "")
    owners = ("impl_self_check", "task_verify", "candidate_verify")
    return {
        "task_id": task_id,
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
    queue = deque(key for key, degree in indegree.items() if not degree)
    levels = {key: 1 for key in queue}
    visited = set()
    while queue:
        key = queue.popleft()
        visited.add(key)
        for child in children[key]:
            levels[child] = max(levels.get(child, 1), levels[key] + 1)
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    return {
        "task_count": len(tasks),
        "longest_dependency_chain": max(levels.values(), default=0) if len(visited) == len(nodes) else None,
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

    workflow = getattr(config, "workflow", None)
    work_units = getattr(workflow, "work_units", None)
    split = getattr(work_units, "split_quality", None)
    return {
        "execution": execution_policy(config),
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
