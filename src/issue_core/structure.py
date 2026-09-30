"""Validated Structure Agent boundary with an explicit Limited Draft fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import IssueUnderstandingAgent


STRUCTURED_FIELDS = (
    "title", "summary", "bug_description", "reproduction_steps", "actual_behavior",
    "expected_behavior", "attachment_context", "additional_context", "environment",
    "project_version",
)


class StructureOutputError(ValueError):
    pass


class TransientStructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructureResult:
    proposal: Mapping[str, Any]
    understanding_status: str
    reason_code: str = ""
    attempts: int = 0


def validate_structure_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StructureOutputError("structure output must be an object")
    missing = set(STRUCTURED_FIELDS) - set(value)
    if missing:
        raise StructureOutputError(f"structure output is missing fields: {sorted(missing)}")
    proposal: dict[str, Any] = {}
    for name in STRUCTURED_FIELDS:
        if name == "environment":
            raw = value.get(name)
            if not isinstance(raw, Mapping) or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in raw.items()
            ):
                raise StructureOutputError("environment must be a string map")
            proposal[name] = dict(raw)
            continue
        raw = value.get(name)
        if not isinstance(raw, str):
            raise StructureOutputError(f"{name} must be a string")
        limit = 240 if name == "title" else (120 if name == "project_version" else 8000)
        if len(raw) > limit:
            raise StructureOutputError(f"{name} exceeds its contract")
        proposal[name] = raw
    if not proposal["title"].strip():
        raise StructureOutputError("structured title must not be empty")
    provenance = value.get("field_provenance")
    if not isinstance(provenance, Mapping):
        raise StructureOutputError("structure output requires field_provenance")
    proposal["understanding"] = {
        "status": "completed",
        "mode": "agent",
        "field_provenance": dict(provenance),
        "missing_fields": [
            name for name in STRUCTURED_FIELDS
            if name != "environment" and not str(proposal[name]).strip()
        ],
    }
    return proposal


def limited_draft_proposal(
    description: str,
    *,
    environment: Mapping[str, str],
    project_version: str,
    explicit_title: str = "",
    local_timestamp: str,
    reason_code: str,
) -> dict[str, Any]:
    raw = str(description or "").strip()
    title = str(explicit_title or "").strip()[:240]
    if not title:
        title = f"Issue report — {local_timestamp}"[:240]
    empty_fields = (
        "reproduction_steps", "actual_behavior", "expected_behavior",
        "attachment_context", "additional_context",
    )
    return {
        "title": title,
        "summary": raw[:8000],
        "bug_description": raw[:8000],
        **{name: "" for name in empty_fields},
        "environment": {str(key): str(value) for key, value in environment.items()},
        "project_version": str(project_version or "")[:120],
        "understanding": {
            "status": "fallback",
            "mode": "limited_draft",
            "reason_code": reason_code,
            "missing_fields": list(empty_fields),
            "field_provenance": {
                "title": {
                    "source": "reporter" if explicit_title else "temporary",
                    "confidence": 1.0 if explicit_title else 0.0,
                },
                "bug_description": {"source": "raw_report", "confidence": 1.0},
                "summary": {"source": "raw_report", "confidence": 1.0},
                "environment": {"source": "host", "confidence": 1.0},
                "project_version": {"source": "host", "confidence": 1.0},
                **{
                    name: {"source": "unknown", "confidence": 0.0}
                    for name in empty_fields
                },
            },
        },
    }


def structure_report(
    agent: IssueUnderstandingAgent | None,
    description: str,
    *,
    environment: Mapping[str, str],
    project_version: str,
    explicit_title: str = "",
    local_timestamp: str,
) -> StructureResult:
    if agent is None:
        return StructureResult(
            limited_draft_proposal(
                description,
                environment=environment,
                project_version=project_version,
                explicit_title=explicit_title,
                local_timestamp=local_timestamp,
                reason_code="structure_agent_unavailable",
            ),
            understanding_status="fallback",
            reason_code="structure_agent_unavailable",
        )
    transient_failures = 0
    schema_failures = 0
    attempts = 0
    while transient_failures <= 2 and schema_failures <= 1:
        attempts += 1
        try:
            output = agent.understand(
                description,
                environment={str(key): str(value) for key, value in environment.items()},
                project_version=project_version,
            )
            proposal = validate_structure_proposal(output)
            proposal["understanding"]["attempts"] = attempts
            return StructureResult(proposal, "completed", attempts=attempts)
        except TransientStructureError:
            transient_failures += 1
            if transient_failures > 2:
                reason = "structure_agent_transient_exhausted"
                break
        except (StructureOutputError, TypeError, KeyError, ValueError):
            schema_failures += 1
            if schema_failures > 1:
                reason = "structure_agent_schema_invalid"
                break
        except Exception:
            reason = "structure_agent_unavailable"
            break
    return StructureResult(
        limited_draft_proposal(
            description,
            environment=environment,
            project_version=project_version,
            explicit_title=explicit_title,
            local_timestamp=local_timestamp,
            reason_code=reason,
        ),
        understanding_status="fallback",
        reason_code=reason,
        attempts=attempts,
    )


__all__ = [
    "STRUCTURED_FIELDS", "StructureOutputError", "StructureResult",
    "TransientStructureError", "limited_draft_proposal", "structure_report",
    "validate_structure_proposal",
]
