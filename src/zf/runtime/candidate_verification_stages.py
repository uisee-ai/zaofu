"""Select assembly checks without consuming downstream verification evidence."""
from __future__ import annotations

from typing import Any, Iterable

from zf.runtime.verification_commands import (
    task_contract_verification_commands,
    verification_command_required_for_stage,
)


def has_candidate_verification_consumer(config: Any) -> bool:
    """Require a real reader stage reached after the candidate is frozen."""
    readers = {
        str(role.instance_id or role.name)
        for role in config.roles
        if role.role_kind == "reader"
    }
    return any(
        stage.trigger == "candidate.ready"
        and stage.topology == "fanout_reader"
        and bool(stage.roles)
        and set(stage.roles) <= readers
        and stage.aggregate.child_success_event == "verify.child.completed"
        and stage.aggregate.success_event == "test.passed"
        for stage in config.workflow.stages
    )


def candidate_quality_checks(
    config: Any, task_store: Any, tasks: Iterable[Any],
) -> tuple[list[tuple[str, str, int]], str]:
    """Preserve run-owned commands and explicitly defer later acceptance."""
    required = config.workflow.candidate_quality_source == "task_contract_required"
    checks: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, str]] = set()
    missing: list[str] = []
    deferred = False
    for candidate_task in tasks:
        task = task_store.get(candidate_task.task_id)
        commands = task_contract_verification_commands(task.contract) if task else []
        if not commands:
            missing.append(candidate_task.task_id)
            continue
        for command in commands:
            if command["owner"] == "human":
                # A candidate reader cannot supply human approval.
                return [], "task_contract_missing_consumer"
            if command["owner"] == "candidate_verify":
                deferred = True
                continue
            if not verification_command_required_for_stage(
                command, verification_owner="candidate_integration",
                task_id=candidate_task.task_id,
            ):
                continue
            identity = (candidate_task.task_id, command["id"], command["command_digest"])
            if identity not in seen:
                seen.add(identity)
                checks.append((
                    f"task_contract:{candidate_task.task_id}:{command['id']}",
                    command["command"],
                    int(command["timeout_seconds"]),
                ))
    if required and missing:
        return [], "task_contract_missing"
    if deferred and not has_candidate_verification_consumer(config):
        return [], "task_contract_missing_consumer"
    if checks:
        return checks, "task_contract"
    if deferred:
        return [], "task_contract_deferred"
    if required:
        return [], "task_contract_missing"
    for name, gate in config.quality_gates.items():
        if gate.enabled:
            checks.extend(
                (name, str(command).strip(), 300) for command in gate.required_checks
                if str(command).strip()
            )
    return checks, "zf_config_fallback" if checks else "intrinsic_only"
