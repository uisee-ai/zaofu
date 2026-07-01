"""Types for replan contract evaluation artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReplanContractCheck:
    name: str
    passed: bool
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplanContractEvalResult:
    schema_version: str = "replan-contract-eval.v1"
    eval_id: str = ""
    profile: str = "baseline"
    decision: str = "revise"
    old_task_map_ref: str = ""
    new_task_map_ref: str = ""
    expected_current_task_map_ref: str = ""
    trigger_event_id: str = ""
    idempotency_key: str = ""
    checks: list[ReplanContractCheck] = field(default_factory=list)
    contract_delta: dict[str, Any] = field(default_factory=dict)
    required_fixes: list[str] = field(default_factory=list)
    refs: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [check.to_dict() for check in self.checks]
        return data


__all__ = [
    "ReplanContractCheck",
    "ReplanContractEvalResult",
]
