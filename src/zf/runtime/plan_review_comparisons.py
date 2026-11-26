"""Bound execution comparisons for full Plan reviews; the Critic judges applicability."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from zf.runtime.plan_readonly_acceptance_review import check_initial_acceptance, review_requirement_source
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def execution_comparisons(
    tasks: list, requirements: list, host_commands: set, *, candidate_stage: bool, dependencies: Mapping
) -> dict:
    from zf.runtime.task_pipeline_reconciler import STANDARD_INITIAL_STAGE, VERIFY_ONLY_INITIAL_STAGE

    comparisons = {}
    entries = {r["task_id"]: r["initial_readonly_verify"] for r in requirements
               if "initial_readonly_verify" in r}
    if entries:
        comparisons["readonly_pipeline_entry"] = {
            "task_ids": list(entries), "before_rework": True,
            "runtime_evidence_fields": ["entry_mode", "first_stage_on_admission"],
            "facts": {"entries": [
                {"task_id": key, "entry_mode": "verify_only", "first_stage_on_admission": VERIFY_ONLY_INITIAL_STAGE,
                 **{k: entry[k] for k in ("status", "reason") if k in entry}}
                for key, entry in entries.items()
            ]},
        }
        comparisons["readonly_command_scope"] = {
            "task_ids": list(entries), "before_rework": True,
            "facts": {"entries": [
                {"task_id": key, **{k: entry[k] for k in ("status", "reason", "verification_owner", "command_ids")
                                    if k in entry}} for key, entry in entries.items()
            ]},
        }
        if dependencies.keys() - entries.keys():
            comparisons["cross_task_ordering"] = {
                "task_ids": list(dependencies), "before_rework": True,
                "facts": {"tasks": [{"task_id": key, "blocked_by": value} for key, value in dependencies.items()]},
            }
    empty = []
    selections = {}
    for task, row in zip(tasks, requirements, strict=True):
        if row.get("standard_with_empty_write_scope"):
            evidence = task.get("evidence_contract")
            declarations = {k: evidence[k] for k in ("task_pipeline_entry_mode", "execution_mode")
                            if isinstance(evidence, Mapping) and k in evidence}
            empty.append({
                "task_id": row["task_id"], "declared_execution_mode": task.get("execution_mode"),
                **({"evidence_contract": declarations} if declarations else {}),
                "entry_mode": "standard", "first_stage_on_admission": STANDARD_INITIAL_STAGE,
                "allowed_paths": task["allowed_paths"],
                "declared_command_ids": [c["command_id"] for c in row["commands"]],
            })
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
    if empty:
        comparisons["standard_with_empty_write_scope"] = {
            "task_ids": [entry["task_id"] for entry in empty], "before_rework": True,
            "runtime_evidence_fields": ["entry_mode", "first_stage_on_admission"],
            "facts": {"entries": empty},
        }
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


def execution_assessments(
    state: Path, request: Mapping, report: Mapping, *, rejected: bool, error
) -> list:
    """Bind dispositions to the independent Critic's current frozen summary before committing a verdict."""
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
    required = {key for key, spec in checks.items() if not rejected or spec.get("before_rework")}
    if not required:
        return []
    fixes = report.get("fix_items") or []
    rework_checks = {key for key, spec in checks.items() if spec.get("before_rework")}
    if rework_checks and isinstance(fixes, list):
        for index, fix in enumerate(fixes):
            comparison = fix.get("comparison") if isinstance(fix, Mapping) else None
            if not isinstance(comparison, str) or comparison not in rework_checks | {"other"}:
                error(f"report.fix_items[{index}].comparison",
                      "Name the disputed comparison, or use other for a separate evidenced obligation")
    submitted = report.get("execution_assessments")
    if (
        not isinstance(submitted, list)
        or any(
            not isinstance(row, dict) or not isinstance(row.get("comparison"), str)
            for row in submitted
        )
        or len({row["comparison"] for row in submitted}) != len(submitted)
        or not required <= {row["comparison"] for row in submitted} <= checks.keys()
    ):
        error(
            "report.execution_assessments",
            "Assess each frozen execution comparison exactly once: "
            + ", ".join(sorted(required)),
        )
        return []
    rows = []
    for i, row in enumerate(submitted):
        path = f"report.execution_assessments[{i}]"
        if not isinstance(row.get("conclusion"), str) or not row["conclusion"].strip():
            error(
                path + ".conclusion",
                "Explain the evidence and conclusion for this obligation",
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
        if checks[row["comparison"]].get("before_rework"):
            _check_review_disposition(row, path, report, matches[0], checks[row["comparison"]], rejected=rejected, error=error)
        elif rework_checks and "disposition" in row:
            error(path + ".disposition", "Explicit repair dispositions belong to comparisons marked before_rework")
        basis = row.get("basis")
        authority = (
            basis.get("requirement_evidence", []) if isinstance(basis, Mapping) else []
        )
        if not isinstance(authority, list) or not any(
            isinstance(ref, Mapping)
            and review_requirement_source(ref)
            for ref in authority
        ):
            error(
                path + ".basis.requirement_evidence",
                "Cite a frozen requirement or its declared input; "
                "a host permission list alone cannot establish an obligation",
            )
        rows.append((path, row))
    return rows


def _check_review_disposition(row, path, report, source, spec, *, rejected, error):
    comparison = row["comparison"]
    disposition = row.get("disposition")
    if disposition not in ("no_repair", "repair_required", "needs_clarification") or (
        not rejected and disposition != "no_repair"
    ):
        error(path + ".disposition", "Declare no_repair, repair_required or needs_clarification; resolve blockers before approval")
    fixes = report.get("fix_items") or []
    linked = [f for f in fixes if isinstance(f, Mapping) and f.get("comparison") == comparison
              ] if isinstance(fixes, list) else []
    for fix in linked:
        targets = [fix["task_id"]] if fix.get("task_id") else fix.get("task_ids")
        if (not isinstance(targets, list) or not targets or not isinstance(row.get("task_ids"), list)
                or any(not isinstance(t, str) or t not in row["task_ids"] for t in targets)):
            error(path + ".task_ids", "A linked repair must name affected task_id or task_ids in this comparison")
    if bool(linked) != (disposition == "repair_required"):
        error(path + ".disposition", "Bind only a confirmed repair to fix_items using its comparison key")
    questions = report.get("owner_decision_items") or []
    pending = isinstance(questions, list) and any(
        isinstance(question, Mapping) and question.get("decision_id") and question.get("question")
        and question.get("blocking") is not False and question.get("comparison") == comparison
        for question in questions
    )
    if pending != (disposition == "needs_clarification"):
        error(path + ".disposition", "Bind missing authority or evidence to a blocking owner_decision_item for this comparison")
    citation = row.get("runtime_evidence")
    prefix = f"$.execution_comparisons.{comparison}.facts"
    selector = citation.get("json_path") if isinstance(citation, Mapping) else None
    if (not isinstance(citation, Mapping) or any(citation.get(k) != source[k]
            for k in ("source_id", "artifact_id", "sha256")) or not isinstance(selector, str)
            or not selector.startswith((prefix + ".", prefix + "["))):
        error(path + ".runtime_evidence", "Cite a specific field under the frozen comparison facts")
    elif "initial_acceptance_review" in spec:
        check_initial_acceptance(row, path, spec, error)
    elif fields := spec.get("runtime_evidence_fields"):
        if not any(selector == f"{prefix}.entries[{index}].{field}"
                   for index in range(len(spec["facts"]["entries"])) for field in fields):
            error(path + ".runtime_evidence",
                  f"Cite the computed dispatch field under {prefix}.entries[i]: " + ", ".join(fields))
