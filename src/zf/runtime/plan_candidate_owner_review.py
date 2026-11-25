"""Mechanical eligibility for a draft requiring independent Plan Critic review.

This admits a reader output, never an executable task map or Plan approval.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.plan_synth_feedback import normalize_plan_synth_owner_decision_items
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
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
            "Blocking choice: candidate_kind=owner_review, owner_decision_items"
            "[{decision_id,question,blocking:true}]. Otherwise candidate_kind=diagnosis_review, "
            "finding_results, exact operator_feedback. Both: execution_status=completed, "
            "result_semantics=artifact_production, recommendation=abstain, summary, "
            "plan_artifact_ref + plan_artifact_sha256, source_index_ref + source_index_sha256. "
            "New work needs separate authority."
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
        for field, message in _draft_artifact_issues(candidate, state_dir, project_root):
            error(field, message)
    return {"schema_version": "plan-candidate-preflight.v1",
            "status": "failed" if errors else "passed", "errors": errors,
            "candidate_kind": "owner_review", "execution_ready": False,
            "summary": "Draft review only; independent Critic must assess the proposed Owner questions."}


def _draft_artifact_issues(
    candidate: Mapping[str, Any], state_dir: Path, project_root: Path, *, bounded: bool = False,
) -> Iterator[tuple[str, str]]:
    """Check the actual Markdown and source-index bytes used in the Critic handoff."""
    for name in ("plan_artifact", "source_index"):
        field = f"{name}_ref"
        ref = candidate.get(field)
        try:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("requires a durable artifact reference")
            path = resolve_artifact_file(ref, state_dir=state_dir, project_root=project_root)
            if bounded and not any(path.resolve().is_relative_to(root.resolve()) for root in (state_dir, project_root)):
                raise ValueError("diagnosis artifact escapes project and state")
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
            yield field, str(exc)


def diagnosis_request_matches(request: Mapping[str, Any], profile_id: str) -> bool:
    """Validate the role and frozen target in the kernel-issued Planner request."""
    validation = request.get("plan_candidate_validation")
    manifest = validation.get("manifest") if isinstance(validation, Mapping) else None
    trigger = manifest.get("trigger_payload") if isinstance(manifest, Mapping) else None
    identity = request.get("result_identity")
    if not bound_candidate_diagnosis(trigger) or not isinstance(identity, Mapping):
        return False
    return (profile_id == request.get("output_profile_id") == "workflow-read"
            and request.get("operation_type") == "fanout_reader_child"
            and request.get("semantic_result_submit_mode") == "blocking"
            and identity.get("attempt_domain") == "plan"
            and identity.get("result_semantics") == "artifact_production"
            and bool(request.get("active_attempt_id"))
            and all(identity.get(k) and identity[k] == request.get(k) for k in
                    ("workflow_run_id", "role_instance", "child_id", "stage_id", "fanout_id"))
            and identity["workflow_run_id"] == trigger["workflow_run_id"]
            and identity["stage_id"] == manifest.get("stage_id")
            and identity["fanout_id"] == manifest.get("fanout_id")
            and identity.get("target_commit") == request.get("target_commit") == trigger["source_commit"]
            and identity.get("plan_artifact_package_digest") == trigger["plan_artifact_package_digest"])


def diagnosis_review_preflight(
    candidates: list[dict[str, Any]], *, trigger: Mapping[str, Any], state_dir: Path, project_root: Path,
) -> dict[str, Any] | None:
    """Admit only the explicitly scoped diagnosis for an independent review."""
    if not any(c.get("candidate_kind") == "diagnosis_review" for c in candidates):
        return None
    issues = []
    if len(candidates) != 1 or not bound_candidate_diagnosis(trigger):
        issues.append(("candidate_kind", "requires one candidate in a pinned zero-Writer OA diagnosis"))
    else:
        candidate = candidates[0]
        try:
            decision = hydrate_sidecar_ref(state_dir, trigger["orchestrator_decision_ref"]).payload
            if (not isinstance(decision, Mapping) or decision.get("schema_version") != "orchestration-decision.v1"
                    or decision.get("decision") != "return_to_plan" or decision.get("execution_status") != "completed"
                    or any(decision.get(k) != trigger[k] for k in ("workflow_run_id", "plan_artifact_package_digest"))):
                raise ValueError("OA decision does not bind this diagnosis")
        except (ValueError, OSError) as exc:
            issues.append(("orchestrator_decision_ref", str(exc)))
        for field, value in (("execution_status", "completed"), ("result_semantics", "artifact_production"),
                             ("recommendation", "abstain"), ("owner_decision_items", [])):
            if candidate.get(field, [] if field == "owner_decision_items" else None) != value:
                issues.append((field, f"diagnosis_review requires {value!r}"))
        if (candidate.get("failure_class") not in (None, "", "none") or not isinstance(candidate.get("summary"), str)
                or not candidate["summary"].strip()):
            issues.append(("summary", "requires a completed diagnosis with an independent summary"))
        for field in ("task_map", "task_map_ref", "tasks", "task_ids", "failed_task_ids", "downstream_task_ids",
                      "plan_ports", "backlog_ref", "output_artifacts", "execution_ready"):
            if candidate.get(field):
                issues.append((field, "diagnosis_review cannot supply dispatchable work or an execution package"))
        feedback = trigger.get("operator_feedback", [])
        if (not isinstance(feedback, list) or not all(isinstance(v, str) and v.strip() for v in feedback)
                or candidate.get("operator_feedback", []) != feedback):
            issues.append(("operator_feedback", "must preserve the exact feedback from this signed diagnosis"))
        for err in workflow_read_finding_issues(candidate.get("findings", [])):
            issues.append((err["field"], err["message"]))
        issues.extend(_diagnosis_finding_issues(candidate, trigger, state_dir))
        issues.extend(_draft_artifact_issues(candidate, state_dir, project_root, bounded=True))
    return {"schema_version": "plan-candidate-preflight.v1", "candidate_kind": "diagnosis_review",
            "execution_ready": False, "status": "failed" if issues else "passed",
            "summary": "Diagnosis artifact for independent Critic review.",
            "errors": [{"code": "diagnosis_review_candidate_invalid", "field": field, "message": msg}
                       for field, msg in issues]}


def _diagnosis_finding_issues(
    candidate: Mapping[str, Any], trigger: Mapping[str, Any], state_dir: Path,
) -> list[tuple[str, str]]:
    summary = trigger.get("rework_summary")
    obligations = summary.get("candidate_obligations") if isinstance(summary, Mapping) else None
    if not isinstance(obligations, list) or not obligations:
        return [("finding_results", "signed diagnosis has no candidate obligations")]
    bound = {}
    for item in obligations:
        if not isinstance(item, Mapping):
            return [("finding_results", "invalid signed candidate obligation")]
        key, ref = item.get("finding_id"), item.get("control_result_ref")
        if (not isinstance(key, str) or not key or key in bound or not isinstance(ref, Mapping)
                or not ref.get("ref") or not ref.get("sha256")):
            return [("finding_results", "requires unique signed findings with bound evidence")]
        try:
            hydrate_sidecar_ref(state_dir, ref)
        except (ValueError, OSError) as exc:
            return [("finding_results", str(exc))]
        bound[key] = ref["ref"]
    rows = candidate.get("finding_results")
    if not isinstance(rows, list) or len(rows) != len(bound):
        return [("finding_results", "address every signed candidate finding exactly once")]
    for row in rows:
        key = row.get("finding_id") if isinstance(row, Mapping) else None
        if (not isinstance(key, str) or key not in bound or row.get("outcome") not in ("supported", "refuted", "unknown")
                or not isinstance(row.get("explanation"), str) or not row["explanation"].strip()
                or row.get("evidence_refs") != [bound[key]]):
            return [("finding_results", "each original finding needs its own outcome, explanation and bound control_result_ref.ref")]
        del bound[key]
    return []
