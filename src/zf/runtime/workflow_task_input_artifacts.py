"""Validate and persist canonical Task inputs for Workflow intake."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.workflow_request_acceptance import (
    assert_task_workflow_input_coverage,
    task_workflow_input_contract_digest,
    task_workflow_input_contract_text,
)


@dataclass(frozen=True)
class MaterializedTaskInput:
    source_refs: dict[str, str]
    contract_ref: str
    contract_digest: str


def materialize_task_workflow_input(
    *,
    workflow_dir: Path,
    project_root: Path,
    source_ref: str,
    source_refs: Mapping[str, str],
    artifact_refs: Sequence[Any],
    acceptance: Sequence[str],
    constraints: Sequence[str],
    scope: Sequence[str],
    binding: Mapping[str, Any],
    input_contract: Mapping[str, Any],
    reference_roots: tuple[Path, ...] = (),
) -> MaterializedTaskInput:
    normalized_source_refs = dict(source_refs)
    if binding or input_contract:
        assert_task_workflow_input_coverage(
            binding=binding,
            input_contract=input_contract,
            source_ref=source_ref,
            source_refs=normalized_source_refs,
            artifact_refs=list(artifact_refs),
            acceptance=list(acceptance),
            constraints=list(constraints),
            scope=list(scope),
            project_root=project_root,
            reference_roots=reference_roots,
        )
    if not input_contract:
        return MaterializedTaskInput(normalized_source_refs, "", "")

    contract_path = workflow_dir / "task-input-contract.json"
    contract_digest = task_workflow_input_contract_digest(input_contract)
    atomic_write_text(
        contract_path,
        task_workflow_input_contract_text(input_contract),
    )
    normalized_source_refs.update({
        "task_input_contract_ref": str(contract_path),
        "task_input_contract_digest": contract_digest,
    })
    return MaterializedTaskInput(
        normalized_source_refs,
        str(contract_path),
        contract_digest,
    )


def dedupe_artifact_refs(values: Sequence[Any]) -> list[Any]:
    out: list[Any] = []
    identities: set[str] = set()
    for value in values:
        normalized = (
            dict(value)
            if isinstance(value, Mapping)
            else str(value or "").strip()
        )
        if normalized in ("", {}):
            continue
        identity = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity in identities:
            continue
        identities.add(identity)
        out.append(normalized)
    return out


__all__ = [
    "MaterializedTaskInput",
    "dedupe_artifact_refs",
    "materialize_task_workflow_input",
]
