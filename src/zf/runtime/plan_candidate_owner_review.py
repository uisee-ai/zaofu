"""Mechanical eligibility for a draft requiring independent Plan Critic review.

This admits a reader output, never an executable task map or Plan approval.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.plan_synth_feedback import normalize_plan_synth_owner_decision_items
from zf.runtime.task_map import resolve_artifact_file, validate_source_index_payload
from zf.runtime.workflow_read_result import workflow_read_finding_issues


def bound_candidate_diagnosis(trigger: object) -> bool:
    """Recognize the existing typed OA scope in kernel-pinned Plan validation."""
    if not isinstance(trigger, Mapping):
        return False
    decision = trigger.get("orchestrator_decision_ref")
    return bool(trigger.get("resume_scope") == "candidate_diagnosis_only"
                and trigger.get("replan_classification") == "oa_semantic_return_to_plan"
                and all(trigger.get(key) for key in ("workflow_run_id", "candidate_event_id", "rework_of",
                                                     "pdd_id", "source_commit", "plan_artifact_package_digest"))
                and isinstance(decision, Mapping) and decision.get("ref") and decision.get("sha256")
                and not any(trigger.get(key) for key in ("task_ids", "failed_task_ids", "downstream_task_ids")))


def diagnosis_only_writer_error(task_count: int) -> dict[str, str]:
    """Explain a nonempty Plan that has no authorized Writer selection."""
    return {
        "code": "candidate_diagnosis_no_writer_authority",
        "field": "task_map.tasks",
        "message": (
            f"candidate_diagnosis_only: submitted tasks={task_count}, authorized Writers=0. "
            "Record independent findings with sources. For a genuine blocking Owner choice submit a "
            "Critic draft: candidate_kind=owner_review, execution_status=completed, "
            "result_semantics=artifact_production, recommendation=abstain or blocked, summary, "
            "plan_artifact_ref + plan_artifact_sha256, source_index_ref + source_index_sha256, "
            "owner_decision_items[{decision_id,question,blocking:true}]. New work/re-verification needs separate authority."
        ),
    }


def owner_review_preflight(
    candidates: list[dict[str, Any]], *, state_dir: Path, project_root: Path,
) -> dict[str, Any] | None:
    """Only an explicit typed declaration selects the draft review route."""
    if not any(row.get("candidate_kind") == "owner_review" for row in candidates):
        return None
    errors: list[dict[str, str]] = []

    def error(field: str, message: str) -> None:
        errors.append({"code": "owner_review_candidate_invalid", "field": field, "message": message})

    if len(candidates) != 1:
        error("reports", "Owner review requires exactly one candidate")
    for candidate in candidates:
        for field, expected in (("candidate_kind", "owner_review"),
                                ("execution_status", "completed"),
                                ("result_semantics", "artifact_production")):
            if candidate.get(field) != expected:
                error(field, f"requires {expected!r}")
        if candidate.get("recommendation") not in {"abstain", "blocked"}:
            error("recommendation", "requires abstain or blocked")
        if candidate.get("failure_class") not in (None, "", "none"):
            error("failure_class", "execution failures cannot use draft review")
        if not str(candidate.get("summary") or "").strip():
            error("summary", "requires a reason for review")
        errors.extend(workflow_read_finding_issues(candidate.get("findings", [])))
        raw = candidate.get("owner_decision_items")
        valid_shape = isinstance(raw, list) and all(
            isinstance(item, Mapping)
            and isinstance(item.get("options", []), list)
            and isinstance(item.get("evidence_refs", []), list)
            for item in raw
        )
        items = normalize_plan_synth_owner_decision_items(raw) if valid_shape else []
        if not items or not isinstance(raw, list) or len(items) != len(raw):
            error("owner_decision_items", "requires typed questions with IDs and list-valued options/evidence_refs")
        if not any(item["blocking"] for item in items):
            error("owner_decision_items", "requires a blocking question")
        if len({item["decision_id"] for item in items}) != len(items):
            error("owner_decision_items", "question IDs must be unique")
        for name in ("plan_artifact", "source_index"):
            field = f"{name}_ref"
            ref = candidate.get(field)
            try:
                if not isinstance(ref, str) or not ref.strip():
                    raise ValueError("requires a durable artifact reference")
                path = resolve_artifact_file(ref, state_dir=state_dir, project_root=project_root)
                data = read_plan_port_bytes(path, name)
                if hashlib.sha256(data).hexdigest() != candidate.get(f"{name}_sha256"):
                    raise ValueError("artifact digest mismatch")
                if name == "plan_artifact":
                    if path.suffix.lower() != ".md" or not data.decode("utf-8").strip():
                        raise ValueError("requires a nonempty Markdown draft")
                else:
                    body = json.loads(data)
                    if not isinstance(body, dict):
                        raise ValueError("requires a source index object")
                    validation = validate_source_index_payload(body)
                    if not validation.passed:
                        raise ValueError("; ".join(validation.errors))
            except (OSError, ValueError, UnicodeError) as exc:
                error(field, str(exc))
    return {"schema_version": "plan-candidate-preflight.v1",
            "status": "failed" if errors else "passed", "errors": errors,
            "candidate_kind": "owner_review", "execution_ready": False,
            "summary": "Draft review only; independent Critic must assess the proposed Owner questions."}
