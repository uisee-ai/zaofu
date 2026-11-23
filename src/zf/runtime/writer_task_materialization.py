"""Plan-package binding and journaled materialization for writer task maps."""

from __future__ import annotations

from typing import Any


TASK_PIPELINE_ENTRY_STANDARD = "standard"
TASK_PIPELINE_ENTRY_EXTERNAL_GATE = "external_gate"
TASK_PIPELINE_ENTRY_VERIFY_ONLY = "verify_only"


def writer_task_allowed_paths(
    item: dict[str, Any],
    raw: dict[str, Any],
    *,
    fallback: str,
) -> list[str]:
    """Preserve an explicitly empty writer capability instead of widening it."""

    for source, key in (
        # The loader's convenience projection may derive item.allowed_paths
        # from prose scope. Preserve the source contract's explicit [] first.
        (raw, "allowed_paths"),
        (item, "allowed_paths"),
        (raw, "scope"),
    ):
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, list):
            return list(dict.fromkeys(
                str(path).strip() for path in value if str(path).strip()
            ))
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []
    return [str(fallback).strip()] if str(fallback).strip() else []


def writer_task_evidence_contract(raw: dict[str, Any]) -> dict[str, Any]:
    """Preserve declared read scope when projecting an approved task into the TaskStore."""
    nested = raw.get("evidence_contract")
    evidence = dict(nested) if isinstance(nested, dict) else {}
    for key in ("runtime_only", "write_free", "verification_read_paths"):
        if key in raw:
            value = raw[key]
            if key in evidence and evidence[key] != value:
                raise ValueError(f"conflicting task and evidence_contract values for {key}")
            evidence[key] = value
    if raw.get("runtime_only") is True:
        mode = str(raw.get("execution_mode") or evidence.get("task_pipeline_entry_mode")
                   or evidence.get("execution_mode") or "").strip().lower()
        if mode not in {"", "runtime_only", "verify_only"}:
            raise ValueError("runtime_only conflicts with declared pipeline entry mode")
        if raw.get("allowed_paths") or evidence.get("write_free") is False:
            raise ValueError("runtime_only conflicts with declared write scope")
        if not mode and not (evidence.get("write_free") is True and raw.get("allowed_paths") == []):
            raise ValueError("runtime_only needs an explicit entry mode or write_free with empty allowed_paths")
    return evidence


def writer_task_pipeline_entry_mode(
    item: dict[str, Any],
    raw: dict[str, Any],
) -> str:
    """Normalize structured Task Map execution semantics for the scheduler."""

    evidence = writer_task_evidence_contract(raw)
    explicit = str(
        raw.get("execution_mode")
        or item.get("execution_mode")
        or evidence.get("task_pipeline_entry_mode")
        or evidence.get("execution_mode")
        or ""
    ).strip().lower()
    if explicit in {"runtime_only", "verify_only"}:
        return TASK_PIPELINE_ENTRY_VERIFY_ONLY
    if explicit in {"external_gate", "manual_evidence"}:
        return TASK_PIPELINE_ENTRY_EXTERNAL_GATE

    if evidence.get("runtime_only") is True:
        return TASK_PIPELINE_ENTRY_VERIFY_ONLY

    required_manual = evidence.get("required_manual_evidence")
    criteria = raw.get("acceptance_criteria")
    if not isinstance(criteria, list):
        criteria = raw.get("acceptance")
    criteria_rows = criteria if isinstance(criteria, list) else []
    mandatory = [
        criterion
        for criterion in criteria_rows
        if isinstance(criterion, dict)
        and criterion.get("mandatory", True) is not False
    ]
    human_owned = bool(mandatory) and all(
        str(criterion.get("verification_owner") or "").strip().lower() == "human"
        or str(criterion.get("verification_tier") or "").strip().lower()
        == "manual_evidence"
        for criterion in mandatory
    )
    explicitly_write_free = (
        ("allowed_paths" in item and not writer_task_allowed_paths(item, raw, fallback=""))
        or ("allowed_paths" in raw and not writer_task_allowed_paths(item, raw, fallback=""))
    )
    if required_manual and human_owned and explicitly_write_free:
        return TASK_PIPELINE_ENTRY_EXTERNAL_GATE
    return TASK_PIPELINE_ENTRY_STANDARD


def writer_task_contract_source_ref(
    item: dict[str, Any],
    raw: dict[str, Any],
    *,
    fallback: str,
) -> str:
    """Bind successor contracts to their admitted immutable baseline."""

    base_commit = str(item.get("base_commit") or raw.get("base_commit") or "").strip()
    supersedes = item.get("supersedes_task_ids")
    source_refs = item.get("source_refs")
    expected_ref = f"git:{base_commit}" if base_commit else ""
    if (
        expected_ref
        and isinstance(supersedes, list)
        and any(str(task_id).strip() for task_id in supersedes)
        and isinstance(source_refs, list)
        and expected_ref in {str(ref).strip() for ref in source_refs}
    ):
        return expected_ref
    return str(raw.get("source_ref") or fallback or "").strip()


def bind_plan_package_source_refs(
    source_refs: dict[str, str],
    loaded: Any,
) -> None:
    package_id = str(getattr(loaded, "plan_artifact_package_id", "") or "")
    package_ref = str(getattr(loaded, "plan_artifact_package_ref", "") or "")
    package_digest = str(
        getattr(loaded, "plan_artifact_package_digest", "") or ""
    )
    generation = str(getattr(loaded, "task_map_generation", "") or "")
    if package_id:
        source_refs["plan_artifact_package_id"] = package_id
    if package_ref:
        source_refs["plan_artifact_package_ref"] = package_ref
    if package_digest:
        source_refs["plan_artifact_package_digest"] = package_digest
    if generation:
        source_refs["task_map_generation"] = generation


def materialize_writer_tasks(
    runtime: Any,
    tasks: list[Any],
    loaded: Any,
) -> None:
    if not tasks:
        return
    from zf.runtime.task_map_materialization import (
        commit_task_map_materialization,
        prepare_task_map_materialization,
    )

    plan, descriptor = prepare_task_map_materialization(
        state_dir=runtime.state_dir,
        tasks=tasks,
        task_map_ref=loaded.task_map_ref,
        source_index_ref=loaded.source_index_ref,
        package_id=str(
            getattr(loaded, "plan_artifact_package_id", "") or ""
        ),
        package_ref=str(
            getattr(loaded, "plan_artifact_package_ref", "") or ""
        ),
        package_digest=str(
            getattr(loaded, "plan_artifact_package_digest", "") or ""
        ),
        writer=runtime.event_writer,
    )
    commit_task_map_materialization(
        state_dir=runtime.state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=runtime.event_writer,
        project_root=runtime.project_root,
    )


__all__ = [
    "TASK_PIPELINE_ENTRY_EXTERNAL_GATE",
    "TASK_PIPELINE_ENTRY_STANDARD",
    "TASK_PIPELINE_ENTRY_VERIFY_ONLY",
    "bind_plan_package_source_refs",
    "materialize_writer_tasks",
    "writer_task_allowed_paths",
    "writer_task_contract_source_ref",
    "writer_task_evidence_contract",
    "writer_task_pipeline_entry_mode",
]
