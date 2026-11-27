"""Review the computed initial selection against the frozen sources in the same Critic decision."""

from collections.abc import Mapping
from pathlib import Path

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


READONLY_SCOPE = "readonly_command_scope"
INITIAL_ACCEPTANCE = "initial_mandatory_acceptance"
ACCEPTANCE_EVIDENCE_PATH = f"$.execution_comparisons.{READONLY_SCOPE}.facts.{INITIAL_ACCEPTANCE}"


def review_requirement_source(ref: Mapping) -> bool:
    source = ref.get("source_id")
    return source in {"requirement", "critical-workflow-input"} or str(source or "").startswith(
        "plan-declared-input-"
    )


def readonly_acceptance_summary(requirements: list, *, sample_limit: int) -> dict:
    resolved = 0
    empty, unresolved = [], []
    for task in requirements:
        initial = task.get("initial_readonly_verify")
        if not isinstance(initial, Mapping):
            continue
        selected = initial.get("mandatory_acceptance_ids")
        if not isinstance(selected, list):
            unresolved.append(task["task_id"])
        else:
            resolved += 1
            if not selected:
                empty.append(task["task_id"])

    def sample(ids):
        return [task_id for task_id in ids[:sample_limit] if len(task_id) <= 128]

    return {
        "resolved_acceptance_task_count": resolved,
        "without_mandatory_acceptance_count": len(empty),
        "sample_without_mandatory_acceptance": sample(empty),
        "unresolved_task_count": len(unresolved),
        "sample_unresolved_tasks": sample(unresolved),
    }


def attach_readonly_acceptance_summary(
    state: Path, body: dict, review: Mapping, payload: Mapping, *, source_event_id: str, read_events
) -> None:
    from zf.runtime.plan_review_claims import POLICY

    checks = body.get("execution_comparisons") or {}
    scope = checks.get(READONLY_SCOPE)
    if (
        not scope
        or payload.get("plan_review_evidence_policy") != POLICY
        or payload.get("diagnosis_review")
        or payload.get("review_scope_ref")
        or (body.get("plan_review") or {}).get("mode", "full") != "full"
    ):
        return
    sources = payload.get("input_refs") or []
    if any(
        r.get("artifact_id") in {"scoped_plan_review.json", "unchanged-plan-review.json"} for r in sources
    ):
        return
    if not any(review_requirement_source(r) and r.get("ref") and r.get("sha256") for r in sources):
        return
    summary = readonly_acceptance_summary(
        review.get("verification", {}).get("task_requirements", []), sample_limit=16
    )
    if not summary["without_mandatory_acceptance_count"]:
        return
    policy = {
        "runtime_evidence_path": ACCEPTANCE_EVIDENCE_PATH,
        "instruction": "Cite that exact summary and value. In basis.requirement_evidence annotate each assigned "
        "Owner item from the bound field and indices below (otherwise a frozen requirement citation) with "
        "initial_acceptance_requirement and rationale. Classify its requirement for the empty selections "
        "as requires_nonempty, allows_empty, unrelated or unclear. A requirement for a local mandatory AC "
        "on any empty task is a repair; an authorized local probe may leave final acceptance to Candidate.",
    }
    for ref in sources:
        if ref.get("source_id") != "plan-rework-context" or ref.get("kind") != "plan_rework_context":
            continue
        context = hydrate_sidecar_ref(state, dict(ref)).payload
        if not read_events or not (
            context.get("operator_feedback") or context.get("rework_source") == "plan.rejected"
        ):
            continue
        authority = _current_feedback(read_events(), source_event_id, payload.get("workflow_run_id"), context)
        if authority:
            if "owner_feedback" in policy or "unresolved_owner_decision" in policy:
                raise ValueError("ambiguous assigned Plan feedback")
            if "field" in authority:
                policy["owner_feedback"] = {
                    **{k: ref[k] for k in ("source_id", "artifact_id", "sha256")},
                    **authority,
                }
            else:
                policy["unresolved_owner_decision"] = authority["decision_event_id"]
    body["execution_comparisons"] = {
        **checks,
        READONLY_SCOPE: {
            **scope,
            "facts": {**scope["facts"], INITIAL_ACCEPTANCE: summary},
            "initial_acceptance_review": policy,
        },
    }


def _current_feedback(events, trigger_id, run, context):
    from zf.runtime.candidate_rework_evidence import plan_rejection_feedback
    from zf.runtime.plan_owner_rework_read_history import _owner_request
    from zf.runtime.run_scope import run_aliases
    from zf.runtime.stage_replan_generation import completed_generation_source

    pos = {e.id: i for i, e in enumerate(events)}
    if not run or trigger_id not in pos:
        return {}
    source = events[pos[trigger_id]]
    trigger = source.payload
    if (
        source.origin not in {"", "kernel"}
        or source.actor not in {"zf-cli", "web"}
        or trigger.get("workflow_run_id") != run
        or trigger.get("rework_of") != context.get("rework_of")
    ):
        return {}
    prior = events[: pos[trigger_id] + 1]
    latest = next(
        (
            e
            for e in reversed(prior)
            if e.type == "plan.rejected"
            and e.actor == "operator"
            and e.origin == "kernel"
            and e.payload.get("workflow_run_id") == run
        ),
        None,
    )
    generation = trigger.get("stage_replan_generation")
    receipt = next(
        (
            e
            for e in reversed(events)
            if generation
            and e.type == "runtime.action.completed"
            and e.actor == "web"
            and e.origin in {"", "kernel"}
            and e.causation_id == e.payload.get("stage_trigger_event_id")
            and e.payload.get("stage_replan_generation") == generation
            and e.payload.get("run_id") == run
            and e.payload.get("stage_trigger_event_id") in pos
            and pos[e.payload["stage_trigger_event_id"]] <= pos[trigger_id]
            and e.payload.get("status") == "applied"
            and e.payload.get("action") == "stage-replan-new-generation"
            and not e.payload.get("replayed")
        ),
        None,
    )
    if latest and (not receipt or pos[latest.id] >= pos[receipt.payload["stage_trigger_event_id"]]):
        if context.get("rework_of") != latest.id:
            return {}
        originals = plan_rejection_feedback(latest.payload)
        values = context.get("rework_feedback")
        if (
            originals
            and context.get("rework_source") == trigger.get("rework_source") == "plan.rejected"
            and isinstance(values, list)
            and trigger.get("rework_feedback") == values
            and values[: len(originals)] == originals
        ):
            return {"field": "rework_feedback", "count": len(originals), "decision_event_id": latest.id}
        return {"decision_event_id": latest.id}
    if receipt:
        by_id = {e.id: e for e in events[: pos[receipt.id]]}
        escalation = completed_generation_source(receipt, by_id, run_aliases(prior))
        original = by_id.get(receipt.payload["stage_trigger_event_id"])
        if (
            not original
            or original.actor != "zf-cli"
            or original.origin not in {"", "kernel"}
            or not escalation
            or escalation.id != trigger.get("stage_replan_generation_source_event_id")
        ):
            return {}
        req = _owner_request(by_id.get(original.causation_id), run, trigger.get("operator_authorization_ref"))
        values = req.get("operator_feedback")
        if (
            isinstance(values, list)
            and values
            and trigger.get("operator_authorized") is True
            and original.payload.get("operator_feedback") == trigger.get("operator_feedback") == values
            and context.get("operator_feedback") == values
        ):
            return {
                "field": "operator_feedback",
                "count": len(values),
                "decision_event_id": original.causation_id,
            }
        if values and trigger.get("operator_authorized") is True:
            return {"decision_event_id": original.causation_id}
    return {}


def check_initial_acceptance(row: Mapping, path: str, spec: Mapping, error) -> None:
    policy = spec["initial_acceptance_review"]
    citation = row["runtime_evidence"]
    if (
        citation.get("json_path") != policy["runtime_evidence_path"]
        or citation.get("observed_value") != spec["facts"][INITIAL_ACCEPTANCE]
    ):
        error(
            path + ".runtime_evidence",
            "Cite the exact frozen initial mandatory acceptance summary and its observed_value",
        )
    basis = row.get("basis")
    citations = basis.get("requirement_evidence") if isinstance(basis, Mapping) else None
    if not isinstance(citations, list):
        return  # The enclosing review contract requires this list.
    if policy.get("unresolved_owner_decision") and row.get("disposition") == "no_repair":
        error(path + ".disposition", "The current Owner decision cannot be reconciled to the frozen feedback")
    owner = policy.get("owner_feedback")
    evidence = [
        r
        for r in citations
        if isinstance(r, Mapping) and review_requirement_source(r) and "initial_acceptance_requirement" in r
    ]
    if owner:
        assigned = {f"$.{owner['field']}[{i}]" for i in range(owner["count"])}
        comments = [
            r
            for r in citations
            if isinstance(r, Mapping)
            and all(r.get(k) == owner[k] for k in ("source_id", "artifact_id", "sha256"))
        ]
        evidence.extend(comments)
        if len(comments) != len(assigned) or {r.get("json_path") for r in comments} != assigned:
            error(
                path + ".basis.requirement_evidence",
                "Cite and assess each exact assigned Owner feedback item",
            )
    if not evidence:
        error(
            path + ".basis.requirement_evidence",
            "Assess the frozen requirement for the empty initial selections",
        )
    applicable = set()
    for ref in evidence:
        requirement = ref.get("initial_acceptance_requirement")
        if (
            not isinstance(requirement, str)
            or requirement not in {"requires_nonempty", "allows_empty", "unrelated", "unclear"}
            or not isinstance(ref.get("rationale"), str)
            or not ref["rationale"].strip()
        ):
            error(
                path + ".basis.requirement_evidence",
                "Explain each obligation's applicability to the empty initial selections",
            )
        else:
            applicable.add(requirement)
    if applicable == {"unrelated"}:
        error(
            path + ".basis.requirement_evidence",
            "Cite an applicable frozen requirement or mark its expectation unclear",
        )
    if "requires_nonempty" in applicable and row.get("disposition") != "repair_required":
        error(
            path + ".disposition",
            "The assessed requirement calls for nonempty initial acceptance on an empty selection",
        )
    elif "unclear" in applicable and row.get("disposition") == "no_repair":
        error(
            path + ".disposition",
            "Resolve the unclear initial acceptance obligation or request clarification",
        )
