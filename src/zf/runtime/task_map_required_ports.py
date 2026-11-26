"""Mechanical validation for Task Map plan artifact port declarations."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

from zf.runtime.plan_artifact_ports import canonical_plan_port_name


_PLAN_PORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def required_plan_port_errors(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["required_plan_ports must be a list when present"]
    if not value:
        return ["required_plan_ports must not be empty when present"]

    errors: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"required_plan_ports[{index}] must be a non-empty string"
            )
            continue
        normalized = canonical_plan_port_name(item)
        if not _PLAN_PORT_NAME.fullmatch(normalized):
            errors.append(
                f"required_plan_ports[{index}] has an invalid logical name"
            )
            continue
        if normalized in seen:
            duplicates.add(normalized)
        seen.add(normalized)
    if duplicates:
        errors.append(
            "required_plan_ports contains duplicate canonical names: "
            + ", ".join(sorted(duplicates))
        )
    return errors


_MATRICES = {"execution_spec", "acceptance_matrix", "test_matrix", "real_e2e_matrix"}
_SCOPED = {"execution_spec", "task_execution_input"}
_STAGES = ("implementation", "task-verify")


def _names(values: Iterable[str]) -> str:
    return ", ".join(sorted(values)) or "(none)"


def _selection_shape(policy: Any) -> Iterator[tuple[str, str, str]]:
    """Yield relative field, reason and guidance, including independent stage errors."""
    if (not isinstance(policy, dict)
            or set(policy) != {"schema_version", *_STAGES}
            or policy.get("schema_version") != "task-plan-input-selection.v1"):
        yield "", "schema", ("has invalid schema; expected exactly schema_version, implementation, "
                             "task-verify with schema_version=task-plan-input-selection.v1")
        return
    for stage in _STAGES:
        values = policy[stage]
        if (not isinstance(values, list) or not values or
                any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in values)):
            yield f".{stage}", "port_list", 'must be a non-empty port list, e.g. ["execution_spec"]'
            continue
        seen = set()
        duplicates = set()
        for port in values:
            if port in seen:
                duplicates.add(port)
            seen.add(port)
        if duplicates:
            yield f".{stage}", "duplicate", f"contains duplicate ports: {_names(duplicates)}; include each name once"
        if not _SCOPED.intersection(values):
            yield f".{stage}", "scope_missing", ('requires a task-scoped input: execution_spec or task_execution_input; '
                                                'e.g. ["execution_spec"] with a task-scoped-plan-input.v1 body')


def plan_input_selection_shape_errors(policy: Any, *, prefix: str) -> list[str]:
    return [f"{prefix}{suffix} {detail}" for suffix, _, detail in _selection_shape(policy)]


def plan_input_declaration_errors(
    payload: dict[str, Any], *, available_ports: Iterable[str] | None = None,
    require_selection: bool = False, diagnostics: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Validate explicit Task Map declarations; a produced port alone does not select it."""
    def record(field: str, code: str, detail: str, *, task: tuple[int, str] | None = None) -> None:
        prefix = f"{task[1]}." if task else ""
        text = f"{prefix}{field} {detail}"
        errors.append(text)
        if diagnostics is not None:
            path = f"task_map.tasks[{task[0]}].{field}" if task else f"task_map.{field}"
            diagnostics[text] = {
                "code": f"task_plan_inputs_{code}", "field": path,
                "message": f"{task[1]}: {detail}" if task else detail,
            }

    declared = payload.get("required_plan_ports")
    errors = required_plan_port_errors(declared)
    available = {canonical_plan_port_name(x) for x in declared if isinstance(x, str)} if isinstance(declared, list) else set()
    bound = set(available_ports) if available_ports is not None else None
    missing_matrix_reported = False
    tasks = payload.get("tasks")
    for index, task in enumerate(tasks if isinstance(tasks, list) else []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "")
        row = (index, task_id)
        evidence = task.get("evidence_contract")
        evidence = evidence if isinstance(evidence, dict) else {}
        local = evidence.get("required_plan_ports")
        errors.extend(
            f"{task_id}.evidence_contract.{error}"
            for error in (required_plan_port_errors(local) if local not in (None, "", []) else [])
        )
        local_ports = {canonical_plan_port_name(x) for x in local if isinstance(x, str)} if isinstance(local, list) else set()
        if bound is not None and (missing := local_ports - bound):
            record("evidence_contract.required_plan_ports", "unbound",
                   f"contains unbound ports: {_names(missing)}; publish their plan_ports descriptors; "
                   f"available package ports: {_names(bound)}", task=row)
        policy = evidence.get("plan_input_selection")
        field = "evidence_contract.plan_input_selection"
        if policy is None:
            if require_selection:
                record(field, "required", "is required by task-scoped-v1", task=row)
            continue
        if not missing_matrix_reported and (missing := _MATRICES - available):
            record("required_plan_ports", "matrices_missing",
                   f"requires complete plan matrix ports; missing from task_map.required_plan_ports: {_names(missing)}; "
                   f"declared: {_names(available)}. Root example: "
                   + json.dumps({"required_plan_ports": sorted(_MATRICES)}))
            missing_matrix_reported = True
        for suffix, code, detail in _selection_shape(policy):
            record(field + suffix, code, detail, task=row)
        if not isinstance(policy, dict):
            continue
        selectable = available | local_ports
        for stage in _STAGES:
            values = policy.get(stage)
            if not isinstance(values, list):
                continue
            unknown = {v for v in values if isinstance(v, str) and v.strip()} - selectable
            if unknown:
                record(f"{field}.{stage}", "undeclared",
                       f"contains unbound ports: {_names(unknown)}. Declare shared names in task_map.required_plan_ports "
                       f"or task-only names in this task's evidence_contract.required_plan_ports; "
                       f"selectable declarations: {_names(selectable)}.", task=row)
    return errors


def selected_input_body_errors(payload: dict[str, Any], ports: dict[str, Any]) -> list[str]:
    """Reject a claimed scoped input with an unscoped body before approval."""
    from zf.runtime.task_scoped_plan_inputs import SCHEMA_VERSION

    errors = []
    for task in payload.get("tasks", []) if isinstance(payload.get("tasks"), list) else []:
        evidence = task.get("evidence_contract") if isinstance(task, dict) else None
        policy = evidence.get("plan_input_selection") if isinstance(evidence, dict) else None
        if not isinstance(policy, dict):
            continue  # Shape errors belong to plan_input_declaration_errors.
        for stage in ("implementation", "task-verify"):
            names = policy.get(stage)
            for name in names if isinstance(names, list) else []:
                if not isinstance(name, str) or name not in {"execution_spec", "task_execution_input"}:
                    continue
                body = ports.get(name)
                if not isinstance(body, dict) or body.get("schema_version") != SCHEMA_VERSION:
                    errors.append(f"{task.get('task_id') or task.get('id')}.{stage}: selected {name} must be task scoped")
    return errors


__all__ = ["plan_input_declaration_errors", "plan_input_selection_shape_errors",
           "required_plan_port_errors", "selected_input_body_errors"]
