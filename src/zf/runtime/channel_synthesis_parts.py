"""Agent-authored portions of the existing complete synthesis contract."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from zf.runtime.prd_quality_prompt import PRD_AUTHOR_GUIDANCE


@dataclass(frozen=True)
class SynthesisPart:
    name: str
    title: str
    arrays: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    @property
    def fields(self) -> set[str]:
        return set((*self.arrays, *self.objects, *self.strings, *self.optional))


PARTS = (
    SynthesisPart("document", "核对材料与整理文稿",
                  arrays=("assumptions", "out_of_scope", "non_functional_requirements", "success_metrics"),
                  objects=("document_control",), strings=("title", "summary", "spec_format"),
                  optional=("prd_file", "prd_composition", "prd_markdown")),
    SynthesisPart("requirements", "保存需求与验收映射",
                  arrays=("requirement_coverage", "user_stories", "edge_cases", "key_entities", "acceptance_criteria")),
    SynthesisPart("decisions", "整理已确认决定",
                  arrays=("decision_log", "decisions", "dissent", "risks", "open_questions")),
    SynthesisPart("implementation", "补齐开发交接",
                  arrays=("verification_commands", "prd_quality_checklist"), objects=("implementation_companion",)),
    SynthesisPart("finalize", "复核并组装草案",
                  arrays=("source_refs", "evidence_refs"),
                  objects=("classification", "readiness", "recommended_workflow", "next_round", "permission_audit_contract"),
                  strings=("quality_contract",),
                  optional=("confidence", "decision", "source_attestation", "consumed_message_digests",
                            "consumed_contribution_refs", "consumed_contribution_digests",
                            "canonical_cross_review_refs", "supporting_contribution_refs")),
)

GUIDANCE = {
    "document": (
        "Read the complete required source pack, including all Owner decisions and dissent. "
        "semantic_source_archived contains provenance of historical GENERATED contracts, not new requirements; "
        "do not regenerate those failed drafts. Identical content_ref values resolve to the same complete body. "
        "Use exactly one body: prd_file:{ref,sha256} for the verified selected complete file; "
        "prd_composition:{source_ref,source_digest,edits:[{old,new}],appendix_markdown} for exact edits to that file; "
        "otherwise standalone complete prd_markdown. Never output the selected file again when it can be reused. "
        "Use spec_format=prd-spec.v1 and document_control with revision/status/owner/profile (lite or full). "
        "The document needs standard product, user stories, flow, named atomic requirements/scenarios, rules, "
        "edge cases, entities, NFR, success metrics, acceptance, risks and traceability sections. "
        "Each scenario has GIVEN/WHEN/THEN and an Acceptance ID. Each NFR and metric has a unique id and "
        "non-empty statement describing its measurable requirement/outcome; name alone is not a statement. "
        "Success metrics also declare baseline, target, "
        "measurement_method, owner, measurement_timing and status; unknown sourced values stay explicit TBD. "
        "Summary describes durable product behavior, not approval status. No invented implementation evidence."
    ),
    "requirements": (
        "Derive a source-driven inventory and map actual requirements to the selected document. "
        "requirement_coverage rows: unique id, source_refs, direct evidence_refs, exact section heading, "
        "acceptance_ids, status=covered|deferred|gap; deferred/gap need reason. Research rows also need "
        "adopted_conclusion and applicability_limit. acceptance_criteria have stable id and observable criterion. "
        "user_stories have unique id,title,priority,persona,value,independent_test,acceptance_ids. "
        "edge_cases name trigger, expected behavior and acceptance_ids. key_entities have id,name,purpose,lifecycle,constraints. "
        "Preserve exact chosen behavior, numbers, status codes, ordering and fallback; do not weaken Owner decisions. "
        "Reference existing sections and stable IDs; do not duplicate the full PRD inside rows."
    ),
    "decisions": (
        "Carry every Owner decision from the complete question_ledger, including deferred resolutions. "
        "decision_log entries preserve question_id, resolved_event_id, exact chosen answer/behavior, rationale, "
        "source_refs, impact, dissent disposition and reopening_condition. Preserve the actual IDs. "
        "Do not restate entire discussions; keep each record focused while retaining the full chosen behavior. "
        "decisions must contain durable product decisions. dissent is an array, not an envelope object. "
        "Deferred is not permission, exclusion or approval. Keep factual investigation with members; only "
        "genuine currently-needed product decisions become open_questions, each with why_it_matters, "
        "recommended_answer and concrete alternatives. Future tests and execution approvals are not missing product decisions."
    ),
    "implementation": (
        "Use the saved acceptance IDs exactly. verification_commands entries have unique id, command (pure "
        "executable shell), acceptance_ids, producer_paths, owner, tier, deterministic, reusable, timeout_seconds. "
        "Browser criteria require a repo-root executable Docker Playwright command and artifact paths. "
        "Future test files are planned deliverables, not evidence already executed. " + PRD_AUTHOR_GUIDANCE
    ),
    "finalize": (
        "Read and reconcile ALL saved parts against this complete immutable source pack. "
        "Do not silently change saved business parts; if a contradiction remains, report a precise issue. "
        "Return source_attestation:{context_pack_id,context_pack_sha256,all_required_sources_read:true} "
        "ONLY after reading the required sources. Copy this CURRENT pack identity/hash exactly. "
        "Omit the three consumed_* lists when attesting; runtime expands original identities. "
        "quality_contract=channel-prd-quality.v1. classification declares source_read_status=complete and "
        "artifact_readability_status=verified only when justified. permission_audit_contract includes "
        "deny_by_default=true,event_fields,query_fields,retention_policy,owner. "
        "readiness has verdict=ready|needs_owner|needs_multi_lens,implementation_start,gaps,risks,evidence_refs,reason. "
        "Preserve explicit planning-only implementation_start=false. Future independent reviews, Owner confirmation "
        "and Task/Workflow approvals remain mandatory controlled steps, not unknown product decisions. "
        "recommended_workflow and classification are objects. next_round={action:finalize} unless an evidence-backed "
        "selective pass is necessary. Do not approve, create tasks or start workflows. "
        "Return a compact readable completion explanation as public prose, never raw contract prose."
    ),
}


def part_error(spec: SynthesisPart, fields: Any) -> str:
    if not isinstance(fields, dict):
        return "fields must be an object"
    extra = fields.keys() - spec.fields
    if extra:
        return f"fields belong to another part: {', '.join(sorted(extra))}"
    for names, expected in ((spec.arrays, list), (spec.objects, dict), (spec.strings, str)):
        for name in names:
            if not isinstance(fields.get(name), expected):
                return f"{name} must be {expected.__name__}"
    if spec.name == "document":
        bodies = fields.keys() & {"prd_markdown", "prd_file", "prd_composition"}
        if len(bodies) != 1:
            return "exactly one complete document body form is required"
        body = fields[next(iter(bodies))]
        if not body or not isinstance(body, str if "prd_markdown" in bodies else dict):
            return "document body is unavailable"
        if fields.get("spec_format") == "prd-spec.v1":
            from zf.runtime.channel_prd_document import validate_prd_measurables
            error = validate_prd_measurables(fields)
            if error:
                return error
    if spec.name == "finalize" and not (
        isinstance(fields.get("source_attestation"), dict)
        or all(isinstance(fields.get(key), list) for key in (
            "consumed_message_digests", "consumed_contribution_refs", "consumed_contribution_digests"))
    ):
        return "explicit source-consumption evidence is required"
    return ""


def part_prompt(spec: SynthesisPart, *, initial_prompt: str, saved: dict[str, dict], first_turn: bool,
                context_id: str, context_digest: str, error: str = "") -> str:
    # The old monolithic response instruction is replaced, not layered on top.
    context = initial_prompt.split("\nResponse contract (authoritative for this reply):", 1)[0] if first_turn else (
        "Continue this exact PRD generation with its bound complete source pack. No new material is introduced."
    )
    return context + "\n\nCURRENT RESPONSE CONTRACT: checkpointed synthesis\n" + (
        f"This turn produces ONLY part {spec.name!r} ({spec.title}). The runtime saves each complete part; "
        "do not restart completed parts or output channel_synthesis. Never repeat a complete contract after a "
        "transport retry; finish only the current part. Use a short human-readable public progress/completion "
        "sentence, then on its own line <!-- zf:channel-contract -->, then one valid JSON object "
        f'{{"channel_synthesis_part":{{"part":"{spec.name}","fields":{{...}}}}}}. '
        "The marker/JSON must be last; close every string/object. This is a candidate contribution, not a published PRD. "
        f"Required array fields: {spec.arrays}; object fields: {spec.objects}; string fields: {spec.strings}; "
        f"allowed optional fields: {spec.optional}. Only these fields belong in fields.\n"
        + GUIDANCE[spec.name]
        + f"\nCurrent source identity: context_pack_id={context_id}; context_pack_sha256={context_digest}.\n"
        + "Saved complete parts (authoritative candidate dependencies, not approval; never re-emit them):\n"
        + json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
        + (f"\nThe previous attempt for THIS part was rejected: {error}. Correct only this part.\n" if error else "")
    )
