"""Admit truthful input/runner failures without granting a semantic verdict."""
from collections.abc import Mapping
from typing import Any


def incomplete_task_input_failure(schema: str, result: Mapping[str, Any]) -> bool:
    if schema not in {"implementation-result.v1", "verification-result.v1"}:
        return False
    failure_classes = {"reader_execution_failure"}
    if schema == "verification-result.v1":
        # Reporting an unavailable verifier does not evaluate product claims.
        # The result still passes identity, evidence and supplied-report checks.
        failure_classes.add("verifier_execution_failure")
    return (
        result.get("execution_status") == "failed"
        and result.get("verdict") in {"blocked", "abstained"}
        and result.get("failure_class") in failure_classes
        and bool(str(result.get("summary") or "").strip())
        and isinstance(result.get("evidence_refs"), list)
        and any(isinstance(ref, str) and ref.strip() for ref in result["evidence_refs"])
    )
