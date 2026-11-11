"""Mechanical eligibility for a draft requiring independent Plan Critic review.

This admits a reader output, never an executable task map or Plan approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.plan_synth_feedback import normalize_plan_synth_owner_decision_items
from zf.runtime.task_map import resolve_artifact_file, validate_source_index_payload
from zf.runtime.workflow_read_result import workflow_read_finding_issues


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
        items = normalize_plan_synth_owner_decision_items(raw)
        if not items or not isinstance(raw, list) or len(items) != len(raw):
            error("owner_decision_items", "requires typed questions with IDs")
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
