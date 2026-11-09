"""Plan package currentness for task, run, and scoped OA call results."""

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.task.store import TaskStore
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    current_task_contract_identity,
)


def plan_package_currentness_issues(
    state_dir: Path,
    event_log: EventLog,
    envelope: Mapping[str, Any],
    operation: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    control_result = (
        envelope.get("control_result")
        if isinstance(envelope.get("control_result"), Mapping)
        else {}
    )
    identity = (
        envelope.get("identity")
        if isinstance(envelope.get("identity"), Mapping)
        else {}
    )
    if (
        str(control_result.get("schema_version") or "")
        == "plan-synthesis-result.v1"
        or str(identity.get("attempt_domain") or "") == "plan"
    ):
        # Plan-domain calls produce the replacement candidate/package.  A
        # prior package is an input to rework, not the output authority to
        # which the replacement result must remain bound.
        return []
    workflow_run_id = str(identity.get("workflow_run_id") or "")
    if not workflow_run_id:
        return []
    from zf.runtime.plan_artifact_package import (
        admitted_plan_artifact_package_for_generation,
        reduce_plan_artifact_packages,
    )

    events = event_log.read_all()
    reduced = reduce_plan_artifact_packages(
        events,
        workflow_run_id=workflow_run_id,
    )
    current = reduced.get("current")
    operation = operation or {}
    task_bound = (
        str(identity.get("attempt_domain") or "") == "task"
        or str(operation.get("operation_type") or "") == "task-stage"
    )
    task_generation_current = True
    task_id = str(identity.get("task_id") or "")
    if task_bound and task_id:
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        if task is not None:
            try:
                task_generation = str(
                    current_task_contract_identity(task).get(
                        "task_map_generation"
                    )
                    or ""
                )
            except TaskContractSnapshotError:
                task_generation = ""
            result_generation = str(
                identity.get("task_map_generation") or ""
            )
            task_generation_current = (
                not task_generation
                or same_task_map_generation(
                    result_generation,
                    task_generation,
                )
            )
    if task_bound and task_generation_current:
        generation_package = admitted_plan_artifact_package_for_generation(
            reduced,
            task_map_generation=str(
                identity.get("task_map_generation") or ""
            ),
        )
        if generation_package:
            current = generation_package
    from zf.runtime.orchestrator_agent_plan_binding import operation_checkpoint_plan_package

    scoped = operation_checkpoint_plan_package(
        state_dir, events, operation,
    )
    if scoped is not None:
        if not scoped:
            return [_currentness_issue(
                "control_result.plan_artifact_package_ref",
                "stale_plan_artifact_package", "current target Task package is missing",
            )]
        current = scoped
    if not isinstance(current, Mapping):
        return []
    actual_digest = str(identity.get("plan_artifact_package_digest") or "")
    blocking = str(current.get("mode") or "") == "blocking"
    if not actual_digest and not blocking:
        return []
    issues: list[dict[str, str]] = []
    for field, event_field in (
        ("plan_artifact_package_id", "package_id"),
        ("plan_artifact_package_ref", "package_ref"),
        ("plan_artifact_package_digest", "package_digest"),
    ):
        expected = str(current.get(event_field) or "")
        actual = str(identity.get(field) or "")
        if expected and actual != expected:
            issues.append(_currentness_issue(
                f"control_result.{field}",
                "stale_plan_artifact_package",
                f"current package expects {expected}, got {actual or '<missing>'}",
            ))
    return issues


def _currentness_issue(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}
