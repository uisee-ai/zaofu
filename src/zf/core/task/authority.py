"""Mechanical authority identity for canonical Task contract writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from zf.core.task.schema import TaskContract, TaskExecutionBinding, task_contract_identity_payload


# These fields are mutable capsule/evidence projections. They may be merged
# without advancing semantic contract authority; stage currentness still binds
# the independently computed contract/capsule revision where applicable.
CONTRACT_METADATA_FIELDS = frozenset({
    "task_doc_ref",
    "source_doc_ref",
    "progress_doc_ref",
    "evidence_doc_ref",
    "source_revision",
    "contract_revision",
    "capsule_revision",
    "acceptance_evidence",
})

# Legacy readers still consume these compatibility mirrors from
# evidence_contract.  They describe execution identity, not product semantics.
EXECUTION_BINDING_EVIDENCE_FIELDS = frozenset({
    "execution_owner",
    "workflow_request_id",
    "workflow_request_revision",
    "workflow_run_id",
    "workflow_origin_binding_digest",
    "workflow_origin_task_digest",
})


def without_execution_binding_evidence(evidence: object) -> dict:
    if not isinstance(evidence, dict):
        return {}
    return {
        str(key): value
        for key, value in evidence.items()
        if key not in EXECUTION_BINDING_EVIDENCE_FIELDS
    }


def contract_authority_payload(contract: TaskContract) -> dict:
    payload = task_contract_identity_payload(contract)
    for key in CONTRACT_METADATA_FIELDS:
        payload.pop(key, None)
    payload["evidence_contract"] = without_execution_binding_evidence(
        payload.get("evidence_contract")
    )
    return payload


def authority_revision_for(
    *,
    task_id: str,
    contract: TaskContract,
    execution_binding: TaskExecutionBinding,
    sequence: int,
) -> str:
    payload = {
        "task_id": task_id,
        "sequence": int(sequence),
        "contract": contract_authority_payload(contract),
        "execution_binding": asdict(execution_binding),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "authority-r" + hashlib.sha256(encoded).hexdigest()[:24]
__all__ = [
    "CONTRACT_METADATA_FIELDS",
    "EXECUTION_BINDING_EVIDENCE_FIELDS",
    "authority_revision_for",
    "contract_authority_payload",
    "without_execution_binding_evidence",
]
