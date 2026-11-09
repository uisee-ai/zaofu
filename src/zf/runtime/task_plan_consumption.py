"""Validate task-local Plan port selections against the pinned Task Map."""

from __future__ import annotations

from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def selected_plan_ports(
    *, state_dir: Any, task_id: str, profile: str, evidence: Mapping[str, Any],
    ports: Mapping[str, Mapping[str, Any]], required: list[str],
) -> list[str] | None:
    """Return an approved task selection; absent policy preserves legacy reads.

    Semantic completeness belongs to Planner/Critic. This check binds an
    explicit selection to the immutable Package's exact Task Map entry.
    """
    policy = evidence.get("plan_input_selection")
    if policy is None:
        return None
    if profile not in {"implementation", "task-verify"}:
        return None
    if not isinstance(policy, Mapping) or set(policy) != {
        "schema_version", "implementation", "task-verify",
    } or policy.get("schema_version") != "task-plan-input-selection.v1":
        raise ValueError("invalid task plan input selection")
    required_full = {"task_map", "execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"}
    if not required_full.issubset(required):
        raise ValueError("task selection requires the full canonical matrices in the package")
    task_map = ports.get("task_map")
    if not task_map:
        raise ValueError("task plan input selection requires pinned task_map")
    body = hydrate_sidecar_ref(state_dir, {
        "ref": task_map.get("ref"), "sha256": task_map.get("sha256"),
    }).payload
    items = body.get("tasks") if isinstance(body, Mapping) else None
    matches = [item for item in items or [] if isinstance(item, Mapping)
               and str(item.get("task_id") or item.get("id") or "") == task_id]
    if len(matches) != 1:
        raise ValueError("task selection must match exactly one pinned task")
    source = matches[0].get("evidence_contract")
    if not isinstance(source, Mapping) or source.get("plan_input_selection") != policy:
        raise ValueError("task selection differs from pinned task_map")
    for stage in ("implementation", "task-verify"):
        value = policy.get(stage)
        if not isinstance(value, list) or not value or any(
            not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in value
        ):
            raise ValueError(f"{stage} input selection must be a non-empty port list")
        if len(set(value)) != len(value) or any(name not in required for name in value):
            raise ValueError(f"{stage} input selection has duplicate or unbound ports")
        if "task_execution_input" not in value:
            raise ValueError(f"{stage} input selection requires task_execution_input")
    return list(policy[profile])
