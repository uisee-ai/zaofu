"""Admit truthful input execution failures without granting a semantic verdict."""
from collections.abc import Mapping
from typing import Any


def incomplete_task_input_failure(schema: str, result: Mapping[str, Any]) -> bool:
    if schema not in {"implementation-result.v1", "verification-result.v1"}:
        return False
    return (
        result.get("execution_status") == "failed"
        and result.get("verdict") in {"blocked", "abstained"}
        and result.get("failure_class") == "reader_execution_failure"
        and bool(str(result.get("summary") or "").strip())
        and isinstance(result.get("evidence_refs"), list)
        and any(isinstance(ref, str) and ref.strip() for ref in result["evidence_refs"])
    )
