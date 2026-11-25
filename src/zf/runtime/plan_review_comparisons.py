"""Bound execution comparisons for full Plan reviews; the Critic judges applicability."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def execution_comparisons(
    tasks: list, requirements: list, host_commands: set, *, candidate_stage: bool
) -> dict:
    comparisons = {}
    empty = [
        row["task_id"]
        for row in requirements
        if row.get("standard_with_empty_write_scope")
    ]
    if empty:
        comparisons["standard_with_empty_write_scope"] = {"task_ids": empty}
    selections = {}
    for task, row in zip(tasks, requirements, strict=True):
        validation = task.get("validation")
        raw_commands = (
            validation.get("commands", []) if isinstance(validation, Mapping) else []
        )
        if not isinstance(raw_commands, list):
            continue
        effective = {
            c["command_id"]
            for c in row["commands"]
            if "candidate_verify" in c["required_by_verification_owner"]
            or c["owner"] == "candidate_verify"
        }
        for command in raw_commands:
            if (
                not isinstance(command, Mapping)
                or not isinstance(command.get("id"), str)
                or command["id"] not in effective
            ):
                continue
            text = command.get("command")
            if isinstance(text, str) and text:
                selections.setdefault(text, []).append(
                    {"task_id": row["task_id"], "command_id": command["id"]}
                )
    if (
        (candidate_stage or selections)
        and host_commands
        and host_commands != selections.keys()
    ):
        comparisons["candidate_command_scope"] = {
            "candidate_commands": selections,
            "host_permitted_only": sorted(host_commands - selections.keys()),
            "note": "Host permission is not an obligation. Compare exact Candidate command registrations "
            "to the frozen requirement; a composite invocation and an independent invocation have different receipts.",
        }
    return comparisons


def approval_assessments(
    state: Path, request: Mapping, report: Mapping, *, error
) -> list:
    """Recover required comparisons only from the approved attempt's immutable required summary."""
    if request.get("review_scope_ref"):
        return []  # A scoped review owns only its approved subset.
    summary = next(
        (
            r
            for r in request.get("required_reads", [])
            if r.get("source_id") == "plan-execution-review"
            and r.get("artifact_id") == "plan-execution-review.json"
            and r.get("json_path") == "$"
        ),
        None,
    )
    if not summary:
        return []
    attempt, digest = request["active_attempt_id"], request["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(
        state,
        {
            "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json",
            "sha256": digest,
        },
    ).payload
    if any(
        s.get("artifact_id")
        in {"scoped_plan_review.json", "unchanged-plan-review.json"}
        for s in manifest["sources"]
    ):
        return []
    matches = [
        s
        for s in manifest["sources"]
        if s["source_id"] == summary["source_id"]
        and s.get("sha256") == summary.get("artifact_sha256")
        and s["artifact_id"] == summary["artifact_id"]
    ]
    if len(matches) != 1 or manifest.get("attempt_id") != attempt:
        raise ValueError("Execution review must match the required frozen input")
    body = hydrate_sidecar_ref(state, matches[0]).payload
    checks = body.get("execution_comparisons")
    if not checks or (body.get("plan_review") or {}).get("mode", "full") != "full":
        return []
    submitted = report.get("execution_assessments")
    if (
        not isinstance(submitted, list)
        or len(submitted) != len(checks)
        or any(
            not isinstance(row, dict) or not isinstance(row.get("comparison"), str)
            for row in submitted
        )
        or {row["comparison"] for row in submitted} != checks.keys()
    ):
        error(
            "report.execution_assessments",
            "Assess each frozen execution comparison exactly once: "
            + ", ".join(checks),
        )
        return []
    rows = []
    for i, row in enumerate(submitted):
        path = f"report.execution_assessments[{i}]"
        if not isinstance(row.get("conclusion"), str) or not row["conclusion"].strip():
            error(
                path + ".conclusion",
                "Explain how the current plan satisfies or legitimately excludes this obligation",
            )
        expected = checks[row["comparison"]].get("task_ids")
        if expected is not None and (
            not isinstance(row.get("task_ids"), list)
            or any(not isinstance(t, str) for t in row["task_ids"])
            or sorted(row["task_ids"]) != sorted(expected)
        ):
            error(
                path + ".task_ids",
                "Account for the exact affected Task IDs in the frozen summary",
            )
        basis = row.get("basis")
        authority = (
            basis.get("requirement_evidence", []) if isinstance(basis, Mapping) else []
        )
        if not isinstance(authority, list) or not any(
            isinstance(ref, Mapping)
            and (
                ref.get("source_id") in {"requirement", "critical-workflow-input"}
                or str(ref.get("source_id") or "").startswith("plan-declared-input-")
            )
            for ref in authority
        ):
            error(
                path + ".basis.requirement_evidence",
                "Cite a frozen requirement or its declared input; "
                "a host permission list alone cannot establish an obligation",
            )
        rows.append((path, row))
    return rows
