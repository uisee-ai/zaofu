"""Acceptance and rolling-smoke command contracts for Plan candidates."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _claim_acceptance_command_errors(task_map: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    claims = {
        str(item.get("goal_claim_id") or item.get("id") or "").strip()
        for item in task_map.get("goal_claims", [])
        if isinstance(item, Mapping) and bool(item.get("mandatory", True))
    }
    command_ids = {
        str(row.get("id") or row.get("command_id") or "").strip(): row
        for task in task_map.get("tasks", [])
        if isinstance(task, Mapping)
        for row in _command_rows(task)
        if str(row.get("id") or row.get("command_id") or "").strip()
    }
    for task in task_map.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "<unknown>")
        task_claims = {
            str(value).strip() for value in task.get("goal_claim_ids", [])
            if str(value).strip()
        }
        criteria = task.get("acceptance_criteria")
        criteria = criteria if isinstance(criteria, list) else []
        if (not claims or task_claims & claims) and not criteria:
            _error(errors, "claim_acceptance_missing", task_id, "claim-owning task has no acceptance criteria")
            continue
        commands = _command_rows(task)
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, Mapping) or not bool(criterion.get("mandatory", True)):
                continue
            path = f"{task_id}.acceptance_criteria[{index}]"
            for field in ("id", "verification_owner", "verification_tier"):
                if not str(criterion.get(field) or "").strip():
                    _error(errors, f"acceptance_{field}_missing", f"{path}.{field}", f"mandatory acceptance criterion requires {field}")
            refs = [
                str(value).strip()
                for value in criterion.get("verification_command_ids", [])
                if str(value).strip()
            ]
            immutable_baseline = (
                str(criterion.get("evidence_mode") or "")
                == "immutable_baseline_only"
            )
            if immutable_baseline:
                if refs:
                    _error(
                        errors,
                        "immutable_baseline_command_refs_present",
                        f"{path}.verification_command_ids",
                        "immutable E2E baseline must be command-free; keep downstream "
                        "runtime audits outside this criterion's command ids",
                    )
                if not _is_pinned_immutable_baseline(criterion):
                    _error(
                        errors,
                        "immutable_baseline_evidence_unpinned",
                        f"{path}.evidence_refs",
                        "immutable E2E baseline requires tier e2e, a pinned git commit, "
                        "and retained artifact evidence",
                    )
            elif not refs:
                _error(errors, "acceptance_command_missing", f"{path}.verification_command_ids", "mandatory acceptance criterion requires command ids")
            for command_id in refs:
                if command_id not in command_ids:
                    _error(errors, "acceptance_command_unknown", f"{path}.verification_command_ids", f"unknown verification command {command_id!r}")
        for command in commands:
            command_id = str(
                command.get("id") or command.get("command_id") or ""
            ).strip()
            if not command_id:
                continue
            for field in ("owner", "tier"):
                if not str(command.get(field) or "").strip():
                    _error(errors, f"command_{field}_missing", f"{task_id}.validation.commands[{command_id}].{field}", f"verification command requires {field}")
    return errors


def _is_pinned_immutable_baseline(criterion: Mapping[str, Any]) -> bool:
    """Allow command-free E2E claims only when immutable evidence is pinned."""

    if (
        str(criterion.get("evidence_mode") or "")
        != "immutable_baseline_only"
        or str(criterion.get("verification_tier") or "") != "e2e"
    ):
        return False
    evidence_refs = {
        str(value).strip()
        for value in criterion.get("evidence_refs", [])
        if str(value).strip()
    }
    pinned_commits = [
        value.removeprefix("git:")
        for value in evidence_refs
        if value.startswith("git:")
    ]
    has_pinned_commit = any(
        len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value.lower())
        for value in pinned_commits
    )
    has_artifact_evidence = any(
        value.startswith("artifacts/") for value in evidence_refs
    )
    return has_pinned_commit and has_artifact_evidence


def _command_rows(task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    validation = task.get("validation")
    values = [
        validation.get("commands") if isinstance(validation, Mapping) else None,
        task.get("verification_commands"),
        task.get("verify_commands"),
    ]
    return [
        row
        for value in values
        if isinstance(value, list)
        for row in value
        if isinstance(row, Mapping)
    ]


def _rolling_smoke_command_errors(
    task_map: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    task_pipeline = (
        metadata.get("task_pipeline")
        if isinstance(metadata, Mapping)
        and isinstance(metadata.get("task_pipeline"), Mapping)
        else {}
    )
    candidate = (
        task_pipeline.get("candidate")
        if isinstance(task_pipeline.get("candidate"), Mapping)
        else {}
    )
    if str(candidate.get("rolling_smoke") or "") != "required":
        return []

    errors: list[dict[str, str]] = []
    for task in task_map.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "<unknown>")
        marked = [
            command
            for command in _command_rows(task)
            if command.get("rolling_smoke") is True
        ]
        if not marked:
            _error(
                errors,
                "rolling_smoke_command_missing",
                f"{task_id}.validation.commands",
                "v4 Task Pipeline requires an explicitly marked rolling-smoke command",
            )
            continue
        for command in marked:
            command_id = str(
                command.get("id")
                or command.get("command_id")
                or "<unknown>"
            )
            field = f"{task_id}.validation.commands[{command_id}]"
            if command.get("deterministic") is not True:
                _error(
                    errors,
                    "rolling_smoke_not_deterministic",
                    f"{field}.deterministic",
                    "rolling-smoke command must be deterministic",
                )
            if command.get("reusable") is not True:
                _error(
                    errors,
                    "rolling_smoke_not_reusable",
                    f"{field}.reusable",
                    "rolling-smoke command must be reusable",
                )
            tier = str(command.get("tier") or "")
            if tier not in {"static", "runtime"}:
                _error(
                    errors,
                    "rolling_smoke_tier_invalid",
                    f"{field}.tier",
                    "rolling-smoke command tier must be static or runtime",
                )
    return errors


def rolling_smoke_command_errors(
    task_map: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Expose the candidate rolling-smoke policy for revised task maps."""

    return _rolling_smoke_command_errors(task_map, metadata=metadata)


def _error(errors: list[dict[str, str]], code: str, field: str, message: str) -> None:
    errors.append({"code": code, "field": field, "message": message})
