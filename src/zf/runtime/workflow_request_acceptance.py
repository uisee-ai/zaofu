"""Canonical Task input inheritance for Task-bound Workflow requests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.task.authority import without_execution_binding_evidence
from zf.runtime.task_contract_snapshot import criterion_text


TASK_INPUT_CONTRACT_SCHEMA_VERSION = "task-workflow-input-contract.v1"
TASK_INPUT_BINDING_SCHEMA_VERSION = "task-workflow-input-binding.v1"
_CONTROL_PARAMETER_KEYS = frozenset({"request_id", "request_revision"})


class TaskWorkflowInputCoverageError(ValueError):
    """Raised when a Task-bound Workflow loses canonical contract inputs."""


def inherit_task_acceptance(
    parameters: dict[str, Any],
    workflow_task: Any | None,
) -> dict[str, Any]:
    if workflow_task is None or "acceptance" in parameters:
        return parameters
    acceptance = [
        criterion_text(item)
        for item in workflow_task.contract.acceptance_criteria
    ]
    acceptance = [item for item in acceptance if item]
    if acceptance:
        parameters["acceptance"] = acceptance
    return parameters


def bind_task_workflow_inputs(
    parameters: dict[str, Any],
    workflow_task: Any,
    *,
    task_contract_digest: str,
    prior_binding: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Compile Task defaults, explicit overrides, and immutable input identity."""

    input_contract = build_task_workflow_input_contract(
        workflow_task,
        task_contract_digest=task_contract_digest,
    )
    input_contract_digest = task_workflow_input_contract_digest(input_contract)
    evidence_digest = _digest(input_contract["evidence_contract"])
    source_refs = {
        key: value
        for key, value in {
            "task_source_ref": input_contract["source_ref"],
            "task_spec_ref": input_contract["spec_ref"],
            "task_source_index_ref": input_contract["source_index_ref"],
            "task_product_contract_ref": input_contract["product_contract_ref"],
            "task_contract_digest": task_contract_digest,
            "task_evidence_contract_digest": evidence_digest,
        }.items()
        if str(value or "").strip()
    }
    source_refs.update(_channel_prd_source_refs(input_contract))
    artifact_refs = list(dict.fromkeys([
        *([input_contract["source_ref"]] if input_contract["source_ref"] else []),
        *([input_contract["spec_ref"]] if input_contract["spec_ref"] else []),
        *input_contract["handoff_artifacts"],
    ]))
    acceptance = [
        criterion_text(item)
        for item in input_contract["acceptance_criteria"]
    ]
    acceptance = [item for item in acceptance if item]
    if not acceptance and input_contract["acceptance"]:
        acceptance = [input_contract["acceptance"]]
    constraints = [
        *input_contract["exclusions"],
        *input_contract["explicit_non_goals"],
        *input_contract["unknowns"],
    ]
    defaults: dict[str, Any] = {
        **(
            {"source_ref": input_contract["source_ref"]}
            if input_contract["source_ref"]
            else {}
        ),
        **({"source_refs": source_refs} if source_refs else {}),
        **({"artifact_refs": artifact_refs} if artifact_refs else {}),
        **({"acceptance": acceptance} if acceptance else {}),
        **({"scope": input_contract["scope"]} if input_contract["scope"] else {}),
        **({"constraints": constraints} if constraints else {}),
    }

    semantic_parameters = {
        str(key): value
        for key, value in parameters.items()
        if key not in _CONTROL_PARAMETER_KEYS
    }
    if prior_binding is not None:
        if str(prior_binding.get("task_contract_digest") or "") != str(
            task_contract_digest or ""
        ):
            raise ValueError("task input binding is stale")
        if str(prior_binding.get("task_input_contract_digest") or "") != (
            input_contract_digest
        ):
            raise ValueError("task input contract is stale")
        override_fields = _strings(prior_binding.get("override_fields"))
        overrides = {
            field: semantic_parameters[field]
            for field in override_fields
            if field in semantic_parameters
        }
        if set(overrides) != set(override_fields):
            raise ValueError("task input override binding is incomplete")
    else:
        overrides = {
            key: value
            for key, value in semantic_parameters.items()
            if value not in (None, "", [], {})
        }

    effective = dict(defaults)
    effective.update(overrides)
    for key in _CONTROL_PARAMETER_KEYS:
        value = parameters.get(key)
        if value not in (None, "", [], {}):
            effective[key] = value
    if prior_binding is not None and str(
        prior_binding.get("effective_parameter_digest") or ""
    ) != _digest(effective):
        raise ValueError("task input parameter binding is stale")
    if prior_binding is not None:
        supplied_effective = {
            key: value
            for key, value in parameters.items()
            if value not in (None, "", [], {})
        }
        if supplied_effective != effective:
            raise ValueError("task input parameters do not match the approved preview")
    binding = {
        "schema_version": TASK_INPUT_BINDING_SCHEMA_VERSION,
        "task_id": str(workflow_task.id or ""),
        "task_contract_digest": str(task_contract_digest or ""),
        "task_input_contract_digest": input_contract_digest,
        "task_evidence_contract_digest": evidence_digest,
        "inherited_fields": sorted(set(defaults) - set(overrides)),
        "override_fields": sorted(overrides),
        "effective_parameter_digest": _digest(effective),
        "source_ref_count": len(source_refs),
        "artifact_ref_count": len(artifact_refs),
        "acceptance_count": len(acceptance),
    }
    return effective, binding, input_contract, overrides


def build_task_workflow_input_contract(
    workflow_task: Any,
    *,
    task_contract_digest: str,
) -> dict[str, Any]:
    contract = workflow_task.contract
    evidence_contract = without_execution_binding_evidence(
        contract.evidence_contract
    )
    return {
        "schema_version": TASK_INPUT_CONTRACT_SCHEMA_VERSION,
        "task_id": str(workflow_task.id or ""),
        "task_contract_digest": str(task_contract_digest or ""),
        "source_ref": str(contract.source_ref or ""),
        "spec_ref": str(contract.spec_ref or ""),
        "source_index_ref": str(contract.source_index_ref or ""),
        "product_contract_ref": str(contract.product_contract_ref or ""),
        "handoff_artifacts": _strings(contract.handoff_artifacts),
        "acceptance_criteria": list(contract.acceptance_criteria or []),
        "acceptance": str(contract.acceptance or ""),
        "scope": _strings(contract.scope),
        "exclusions": _strings(contract.exclusions),
        "explicit_non_goals": _strings(contract.explicit_non_goals),
        "unknowns": _strings(contract.unknowns),
        "evidence_contract": evidence_contract,
    }


def assert_task_workflow_input_coverage(
    *,
    binding: Mapping[str, Any],
    input_contract: Mapping[str, Any],
    source_ref: str,
    source_refs: Mapping[str, Any],
    artifact_refs: list[Any] | tuple[Any, ...],
    acceptance: list[str] | tuple[str, ...],
    constraints: list[str] | tuple[str, ...],
    scope: list[str] | tuple[str, ...],
    project_root: Path,
    reference_roots: tuple[Path, ...] = (),
) -> None:
    errors: list[str] = []
    if str(binding.get("schema_version") or "") != TASK_INPUT_BINDING_SCHEMA_VERSION:
        errors.append("binding schema is missing")
    if str(input_contract.get("schema_version") or "") != TASK_INPUT_CONTRACT_SCHEMA_VERSION:
        errors.append("input contract schema is missing")
    if str(binding.get("task_id") or "") != str(input_contract.get("task_id") or ""):
        errors.append("task identity mismatch")
    if str(binding.get("task_contract_digest") or "") != str(
        input_contract.get("task_contract_digest") or ""
    ):
        errors.append("Task contract digest mismatch")
    if str(binding.get("task_input_contract_digest") or "") != (
        task_workflow_input_contract_digest(dict(input_contract))
    ):
        errors.append("Task input contract digest mismatch")
    evidence_contract = input_contract.get("evidence_contract")
    evidence_contract = (
        dict(evidence_contract) if isinstance(evidence_contract, Mapping) else {}
    )
    if str(binding.get("task_evidence_contract_digest") or "") != _digest(
        evidence_contract
    ):
        errors.append("Task evidence contract digest mismatch")

    overrides = set(_strings(binding.get("override_fields")))
    canonical_source = str(input_contract.get("source_ref") or "")
    if (
        canonical_source
        and "source_ref" not in overrides
        and not _same_project_ref(
            source_ref,
            canonical_source,
            project_root,
            reference_roots=reference_roots,
        )
    ):
        errors.append("source_ref coverage is missing")
    expected_source_refs = {
        key: value
        for key, value in {
            "task_source_ref": canonical_source,
            "task_spec_ref": str(input_contract.get("spec_ref") or ""),
            "task_source_index_ref": str(input_contract.get("source_index_ref") or ""),
            "task_product_contract_ref": str(
                input_contract.get("product_contract_ref") or ""
            ),
            "task_contract_digest": str(binding.get("task_contract_digest") or ""),
            "task_evidence_contract_digest": str(
                binding.get("task_evidence_contract_digest") or ""
            ),
        }.items()
        if str(value or "").strip()
    }
    expected_source_refs.update(_channel_prd_source_refs(input_contract))
    if "source_refs" not in overrides:
        for key, value in expected_source_refs.items():
            if str(source_refs.get(key) or "") != str(value):
                errors.append(f"source_refs.{key} coverage is missing")

    expected_artifacts = {
        value
        for value in (
            canonical_source,
            str(input_contract.get("spec_ref") or ""),
            *_strings(input_contract.get("handoff_artifacts")),
        )
        if value
    }
    actual_artifacts = {_artifact_ref(item) for item in artifact_refs}
    if (
        "artifact_refs" not in overrides
        and not expected_artifacts.issubset(actual_artifacts)
    ):
        errors.append("artifact_refs coverage is missing")

    expected_acceptance = [
        criterion_text(item)
        for item in input_contract.get("acceptance_criteria") or []
    ]
    expected_acceptance = [item for item in expected_acceptance if item]
    if not expected_acceptance and str(input_contract.get("acceptance") or ""):
        expected_acceptance = [str(input_contract["acceptance"])]
    if (
        "acceptance" not in overrides
        and list(acceptance) != expected_acceptance
    ):
        errors.append("acceptance coverage is missing")
    expected_constraints = [
        *_strings(input_contract.get("exclusions")),
        *_strings(input_contract.get("explicit_non_goals")),
        *_strings(input_contract.get("unknowns")),
    ]
    if (
        "constraints" not in overrides
        and list(constraints) != expected_constraints
    ):
        errors.append("constraints coverage is missing")
    if "scope" not in overrides and list(scope) != _strings(input_contract.get("scope")):
        errors.append("scope coverage is missing")
    if errors:
        raise TaskWorkflowInputCoverageError("; ".join(errors))


def _channel_prd_source_refs(input_contract: Mapping[str, Any]) -> dict[str, str]:
    """Project immutable Channel PRD lineage into a workflow input manifest."""

    evidence = input_contract.get("evidence_contract")
    evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
    channel_id = str(evidence.get("channel_id") or "").strip()
    if not channel_id:
        return {}
    return {
        key: value
        for key, value in {
            "channel_id": channel_id,
            "channel_thread_id": str(evidence.get("thread_id") or "").strip(),
            "channel_prd_ref": str(input_contract.get("source_ref") or "").strip(),
            "channel_prd_digest": str(
                evidence.get("channel_prd_digest")
                or evidence.get("source_digest")
                or ""
            ).strip(),
            "channel_prd_revision": str(evidence.get("prd_revision") or "").strip(),
            "channel_prd_readiness_ref": str(
                evidence.get("readiness_ref") or ""
            ).strip(),
            "channel_prd_readiness_digest": str(
                evidence.get("readiness_digest") or ""
            ).strip(),
        }.items()
        if value
    }


def _artifact_ref(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("ref") or value.get("path") or value.get("uri") or "")
    return str(value or "").strip()


def _same_project_ref(
    actual: str,
    expected: str,
    project_root: Path,
    *,
    reference_roots: tuple[Path, ...] = (),
) -> bool:
    if str(actual or "").strip() == str(expected or "").strip():
        return True
    try:
        roots = (Path(project_root), *reference_roots)
        actual_path = Path(actual).expanduser()
        expected_path = Path(expected).expanduser()
        if actual_path.is_absolute():
            actual_candidates = (actual_path.resolve(),)
        else:
            actual_candidates = tuple(
                (root.expanduser().resolve() / actual_path).resolve()
                for root in roots
            )
        if expected_path.is_absolute():
            expected_candidates = (expected_path.resolve(),)
        else:
            expected_candidates = tuple(
                (root.expanduser().resolve() / expected_path).resolve()
                for root in roots
            )
        return bool(set(actual_candidates).intersection(expected_candidates))
    except (OSError, RuntimeError, ValueError):
        return False


def task_workflow_input_contract_text(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def task_workflow_input_contract_digest(value: Mapping[str, Any]) -> str:
    body = task_workflow_input_contract_text(value)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = [
    "TASK_INPUT_BINDING_SCHEMA_VERSION",
    "TASK_INPUT_CONTRACT_SCHEMA_VERSION",
    "TaskWorkflowInputCoverageError",
    "assert_task_workflow_input_coverage",
    "bind_task_workflow_inputs",
    "build_task_workflow_input_contract",
    "inherit_task_acceptance",
    "task_workflow_input_contract_digest",
    "task_workflow_input_contract_text",
]
