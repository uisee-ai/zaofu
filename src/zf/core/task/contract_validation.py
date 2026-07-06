"""Strict task contract validation used by CLI and dispatch preflight."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.task.schema import Task, VALID_VERIFICATION_TIERS
from zf.core.task.store import TaskStore


def validate_task_contract(
    task: Task,
    *,
    config: ZfConfig,
    project_root: Path,
) -> list[str]:
    errors: list[str] = []
    contract = task.contract
    prefix = f"{task.id}: contract"

    if not str(contract.behavior or "").strip():
        errors.append(f"{prefix}.behavior is required")
    if not str(contract.verification or "").strip():
        errors.append(f"{prefix}.verification is required")
    if not contract.verification_tiers:
        errors.append(f"{prefix}.verification_tiers must not be empty")
    for tier in contract.verification_tiers:
        if tier not in VALID_VERIFICATION_TIERS:
            errors.append(f"{prefix}.verification_tiers contains invalid tier {tier!r}")

    role_names = {role.name for role in config.roles}
    instance_ids = {role.instance_id for role in config.roles}
    if contract.owner_instance and contract.owner_instance not in instance_ids:
        errors.append(
            f"{prefix}.owner_instance {contract.owner_instance!r} does not match a role instance"
        )
    if (
        contract.owner_role
        and contract.owner_role not in role_names
        and not _allows_semantic_owner_role(task, config)
    ):
        errors.append(
            f"{prefix}.owner_role {contract.owner_role!r} does not match a role"
        )
    if not contract.owner_role and not contract.owner_instance and not task.assigned_to:
        errors.append(f"{prefix}.owner_role or owner_instance is required")

    if contract.rework_to and contract.rework_to not in role_names:
        errors.append(f"{prefix}.rework_to {contract.rework_to!r} does not match a role")

    if _task_requires_source_precedence(task, config) and not _has_source_precedence_ref(task):
        errors.append(
            f"{prefix}.product_contract_ref or spec_skip_reason is required "
            "for product-impacting tasks; use spec_ref/source_ref when the "
            "source contract is an existing plan/spec artifact"
        )

    for field_name, values in (
        ("scope", contract.scope),
        ("exclusions", contract.exclusions),
        ("shared_files", contract.shared_files),
        ("exclusive_files", contract.exclusive_files),
        ("handoff_artifacts", contract.handoff_artifacts),
    ):
        for value in values or []:
            reason = _invalid_contract_path_reason(str(value), project_root)
            if reason:
                errors.append(f"{prefix}.{field_name} {value!r}: {reason}")

    if set(contract.shared_files or []) & set(contract.exclusive_files or []):
        errors.append(f"{prefix}.shared_files overlaps exclusive_files")

    # P2/K4 (docs/impl/22-zaofu-canonical-dag.md): 6 required_backlog_refs
    # preflight when workflow.dag enforces it.
    errors.extend(validate_backlog_refs(task, config=config))

    return errors


def validate_backlog_refs(
    task: Task,
    *,
    config: ZfConfig,
) -> list[str]:
    """P2/K4: verify that the task contract carries the 6 required_backlog_refs
    declared by ``workflow.dag.required_backlog_refs``.

    Only enforced when BOTH:
      - ``workflow.dag.dev_requires_orchestrator_backlog: true``, AND
      - the task is heading to a writer role (owner_role/owner_instance ==
        a writer like ``dev``), since reader roles (arch/critic/review/test/
        judge) consume upstream events directly and don't need the synthesis.

    Empty / whitespace-only ref values count as missing.

    The 6 refs (canonical list, see docs/impl/22 §4.2.1):
      spec_ref, plan_ref, tdd_ref, critic_event_id, critic_gate_ref,
      evidence_contract
    """
    dag = getattr(config.workflow, "dag", None)
    if dag is None or not dag.dev_requires_orchestrator_backlog:
        return []  # enforcement disabled by config

    required = list(dag.required_backlog_refs or [])
    if not required:
        # Config flag is on but no fields configured — that's a no-op,
        # not an error. Treat as disabled.
        return []

    # Only enforce for writer-targeted tasks. arch/critic/review/test/judge
    # are readers; their tasks are produced before backlog synthesis happens.
    if not _task_targets_writer_role(task, config):
        return []

    contract = task.contract
    if contract is None:
        return [
            f"{task.id}: contract.{ref} is required (workflow.dag.required_backlog_refs)"
            for ref in required
        ]

    errors: list[str] = []
    for ref_name in required:
        value = getattr(contract, ref_name, None)
        if not _is_ref_present(value):
            errors.append(
                f"{task.id}: contract.{ref_name} is required "
                f"(workflow.dag.required_backlog_refs); orchestrator must "
                f"populate it at stage ④ backlog before dispatching dev"
            )
    return errors


def _task_targets_writer_role(task: Task, config: ZfConfig) -> bool:
    """Return True if the task is heading to a writer role (one that
    actually changes the codebase). Readers (arch/critic/review/test/judge)
    don't need 6-ref backlog synthesis upstream of them."""
    target_role_name = (
        _role_name_from_role_or_instance(task.contract.owner_role, config)
        or _role_name_from_instance(task.contract.owner_instance, config)
        or _role_name_from_instance(task.assigned_to, config)
        or (task.assigned_to if _is_role_name(task.assigned_to, config) else "")
    )
    if not target_role_name:
        return False
    for role in config.roles:
        if role.name == target_role_name:
            return getattr(role, "role_kind", "") == "writer"
    return False


def _role_name_from_instance(instance_id: str, config: ZfConfig) -> str:
    if not instance_id:
        return ""
    for role in config.roles:
        if role.instance_id == instance_id:
            return role.name
    return ""


def _role_name_from_role_or_instance(value: str, config: ZfConfig) -> str:
    if not value:
        return ""
    for role in config.roles:
        if role.name == value:
            return role.name
    return _role_name_from_instance(value, config)


def _is_role_name(value: str | None, config: ZfConfig) -> bool:
    if not value:
        return False
    return any(role.name == value for role in config.roles)


def _allows_semantic_owner_role(task: Task, config: ZfConfig) -> bool:
    """Compatibility for refactor task_map semantic owner labels.

    Older refactor task maps used values such as ``dev-core`` in
    ``owner_role`` to describe the module owner, not the runtime role.name.
    When task lineage proves the source is a refactor task_map and dispatch has
    a concrete runtime assignee/instance, keep the task admissible while newer
    imports preserve that semantic value under ``evidence_contract``.
    """
    contract = task.contract
    evidence = contract.evidence_contract or {}
    if not isinstance(evidence, dict):
        return False
    if str(evidence.get("source") or "").strip() != "refactor_task_map":
        return False
    return bool(
        _role_name_from_instance(contract.owner_instance, config)
        or _role_name_from_instance(task.assigned_to or "", config)
        or any(getattr(role, "role_kind", "") == "writer" for role in config.roles)
    )


def _is_ref_present(value: object) -> bool:
    """A ref counts as present if non-empty string or non-empty dict.
    Whitespace-only strings count as empty."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return len(value) > 0
    return bool(value)


def _task_requires_source_precedence(task: Task, config: ZfConfig) -> bool:
    contract = task.contract
    if contract is None:
        return False
    if _task_targets_writer_role(task, config):
        return True
    return bool(contract.scope or contract.affected_files)


def _has_source_precedence_ref(task: Task) -> bool:
    contract = task.contract
    if contract is None:
        return False
    for field_name in (
        "product_contract_ref",
        "spec_skip_reason",
        "spec_ref",
        "source_ref",
        "source_key",
        "source_task_id",
        "source_backlog_task_id",
    ):
        if _is_ref_present(getattr(contract, field_name, "")):
            return True
    return False


def validate_runtime_contracts(
    *,
    config: ZfConfig,
    project_root: Path,
    state_dir: Path,
) -> list[str]:
    errors: list[str] = []
    if config.verification.contract.quality_required:
        if not _has_enabled_quality_checks(config):
            errors.append(
                "verification.contract.quality_required requires at least one "
                "enabled quality_gates.*.required_checks entry"
            )

    store = TaskStore(state_dir / "kanban.json")
    for task in store.list_all():
        if task.status in {"done", "cancelled"}:
            continue
        errors.extend(validate_task_contract(
            task,
            config=config,
            project_root=project_root,
        ))
    return errors


def _has_enabled_quality_checks(config: ZfConfig) -> bool:
    for gate in config.quality_gates.values():
        if not getattr(gate, "enabled", True):
            continue
        if getattr(gate, "required_checks", []) or []:
            return True
    return False


def _invalid_contract_path_reason(value: str, project_root: Path) -> str:
    if not value:
        return "empty path"
    path = Path(value)
    if path.is_absolute():
        return "absolute paths are not allowed"
    if any(part == ".." for part in path.parts):
        return "parent traversal is not allowed"
    if path.parts and path.parts[0] in {".zf", ".git"}:
        return "runtime/internal paths are not allowed"
    try:
        (project_root / path).resolve().relative_to(project_root.resolve())
    except ValueError:
        return "path escapes project root"
    return ""
