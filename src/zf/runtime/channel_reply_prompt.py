"""Provider-facing response prompts and deterministic Channel test replies."""

from __future__ import annotations

import json
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER


def fake_channel_reply_text(
    member: dict[str, Any],
    message: dict[str, Any],
    *,
    semantic_source_digests: object = None,
    contribution_refs: object = None,
    contribution_digests: object = None,
) -> str:
    """Return a deterministic reply that still exercises the typed contract."""
    member_id = str(member.get("member_id") or "agent")
    text = str(message.get("text") or "").strip()
    if len(text) > 220:
        text = text[:217] + "..."
    summary = str(
        redact_obj(f"{member_id} received the channel request: {text}")
    )
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    if refs.get("cross_review_request_id"):
        contract = {
            "channel_cross_review": {
                "summary": summary,
                "outcome": "unresolved",
                "answer": "",
                "findings": [],
                "contradictions": [],
                "risks": [],
                "source_refs": [],
                "evidence_refs": [],
                "consumed_message_digests": _string_list(
                    semantic_source_digests
                ),
            },
        }
    elif refs.get("consensus_review_id"):
        contract = {
            "channel_consensus_review": {
                "verdict": "signed",
                "summary": summary,
                "artifact_digest": str(refs.get("artifact_digest") or ""),
                "evidence_refs": [],
            },
        }
    elif refs.get("question_dedup_request_id"):
        contract = {
            "channel_question_dedup": {
                "ledger_digest": str(
                    refs.get("question_ledger_digest") or ""
                ),
                "groups": [],
            },
        }
    elif refs.get("synthesis_request_id"):
        contract = {
            "channel_synthesis": {
                "decision": "proceed",
                "summary": summary,
                "decisions": ["Preserve the requested product behavior."],
                "prd_markdown": "# Deterministic test PRD\n\n## Behavior\nPreserve README.md. This is a mock contract, not a product quality evaluation.\n\n## Acceptance\nAC-MOCK-01: README.md remains present.\n",
                "requirement_coverage": [{"id": "REQ-MOCK-01", "source_refs": [str(message.get("message_id") or "mock")],
                                          "section": "Behavior", "acceptance_ids": ["AC-MOCK-01"], "status": "covered"}],
                "decision_log": [],
                "assumptions": [],
                "out_of_scope": [],
                "acceptance_criteria": [{
                    "id": "AC-MOCK-01",
                    "criterion": "README.md remains present.",
                    "verification_command_ids": ["VC-MOCK-01"],
                    "producer_paths": ["README.md"],
                }],
                "verification_commands": [{
                    "id": "VC-MOCK-01",
                    "command": "test -f README.md",
                    "acceptance_ids": ["AC-MOCK-01"],
                    "owner": "verify",
                    "tier": "runtime",
                    "deterministic": True,
                    "reusable": True,
                    "timeout_seconds": 30,
                    "producer_paths": ["README.md"],
                }],
                "open_questions": [],
                "risks": [],
                "readiness": {
                    "verdict": "ready",
                    "implementation_start": True,
                    "gaps": [],
                    "risks": [],
                    "evidence_refs": [],
                    "reason": "deterministic test contract is complete",
                },
                "recommended_workflow": {},
                "classification": {},
                "dissent": [],
                "source_refs": [],
                "evidence_refs": [],
                "consumed_contribution_refs": _string_list(
                    contribution_refs
                ),
                "consumed_contribution_digests": _string_list(
                    contribution_digests
                ),
                "consumed_message_digests": _string_list(
                    semantic_source_digests
                ),
                "confidence": "deterministic-test",
            },
        }
    else:
        contract = {
            "channel_contribution": {
                "summary": summary,
                "questions": [],
                "freeze": True,
            },
        }
    if "channel_synthesis" in contract and refs.get("prd_source_file"):
        contract["channel_synthesis"].pop("prd_markdown", None)
        contract["channel_synthesis"]["prd_file"] = {
            key: refs["prd_source_file"][key] for key in ("ref", "sha256")
        }
    return (
        summary
        + "\n"
        + CHANNEL_CONTRACT_MARKER
        + "\n"
        + json.dumps(contract, ensure_ascii=True)
    )


def _contract_response_instruction(contract: str) -> str:
    return (
        "First write a concise user-facing Markdown response with the useful "
        "answer. Never expose the machine contract as prose. Then, on its own "
        "line, write the exact marker below without quotes or code fences:\n"
        f"{CHANNEL_CONTRACT_MARKER}\n"
        "On the next line, write "
        f"{contract} The marker and JSON must be the final content; do not "
        "wrap either in a Markdown fence and do not write prose after them."
    )


def channel_reply_response_contract(
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
) -> str:
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    if refs.get("cross_review_request_id"):
        return _contract_response_instruction(
            "one JSON object named channel_cross_review containing "
            "summary, answer, findings, contradictions, risks, source_refs, "
            "evidence_refs, consumed_message_digests, and outcome (resolved|needs_owner|unresolved). "
            "Use resolved only for an evidence-settled fact with no remaining interpretation conflict. "
            "A non-empty answer or evidence ref alone does not resolve ambiguity. If a product choice "
            "is needed, use needs_owner plus an atomic owner_question and recommended_answer; the "
            "runtime keeps the same question open for the Owner. These two fields must be non-empty strings "
            "directly inside channel_cross_review, not only in prose, findings, or questions. "
            "On a retry, inspect this review in context_pack.cross_review_index and correct its rejection reason. "
            "Use unresolved for missing evidence. Copy every required "
            "message digest from semantic_source_manifest only after reading its "
            "complete semantic_source_document. Facts may be settled only with "
            "evidence_refs."
        )
    if refs.get("consensus_review_id"):
        artifact_ref = str(refs.get("artifact_ref") or "")
        artifact_digest = str(refs.get("artifact_digest") or "")
        target_binding = ""
        if artifact_ref and artifact_digest:
            target_binding = (
                " The canonical review target is artifact_ref="
                f"{json.dumps(artifact_ref)} with artifact_digest="
                f"{json.dumps(artifact_digest)}. The response artifact_digest "
                "MUST equal that canonical digest exactly; do not substitute a "
                "spec_digest, Markdown digest, contract digest, or evidence digest."
            )
        feedback_instruction = ""
        if refs.get("evaluation_feedback_id"):
            feedback_instruction = (
                " Read the complete evaluation_report and evaluation_input_manifest in semantic_source_documents. "
                "Add finding_dispositions: one item for EVERY zero-based index in report.findings, with "
                "finding_index, disposition=revision_request|owner_decision|evidence_needed|out_of_scope|"
                "not_reproduced|suggestion, and non-empty reason with source/PRD evidence. "
                "Independently verify findings, do not convert suggestions or test-operation instructions into "
                "product scope. Use blocked + revision_request for evidenced document defects whose behavior "
                "is already determined; use owner_decision only for actual unresolved product choices. "
                "For missing evidence say what is missing, do not invent a passing result. "
                "Signed means no material PRD blocker remains; preserve all finding dispositions, including "
                "rejected findings. If report verdict is not PASS but findings is empty, also inspect matrix "
                "failures and coverage gaps and explain their disposition in summary. "
            )
        return _contract_response_instruction(
            "one JSON object named channel_consensus_review containing verdict "
            "signed|blocked, "
            "summary, artifact_digest, evidence_refs, and for blocked verdict "
            "blocker_question, blocker_kind=revision_request|owner_decision, "
            "plus optional blocker_question_id. Use revision_request when the "
            "source already settles the product behavior and the draft needs "
            "correction; use owner_decision only for an unresolved product choice. "
            "Read the exact "
            "source requirement and synthesis artifact supplied in context_pack.semantic_source_documents "
            "before producing this object. Independently check source-to-PRD coverage, factual accuracy, "
            "internal consistency, role-specific risks, and executable acceptance criteria. Cite source "
            "and PRD sections in evidence_refs and summarize omissions; digest equality alone is not quality. "
            + target_binding + feedback_instruction
        )
    if refs.get("question_dedup_request_id"):
        return _contract_response_instruction(
            "one JSON object named channel_question_dedup. "
            "It must contain the exact ledger_digest from the context and "
            "groups. Each group contains canonical_question_id, "
            "merge_question_ids, and reason. It may also contain "
            "question_updates and bounded cross_review_requests. Every "
            "cross_review_requests item must contain question_id, non-empty target_member_ids "
            "(an array of real member IDs), and prompt; optional evidence_refs is an array. "
            "canonical_question_id and merge_question_id MUST identify a "
            "currently open ledger item. Never use a resolved or merged "
            "question as a canonical target, and never merge across question "
            "statuses. If a resolved item is semantically similar to an open "
            "one, preserve the resolved decision unchanged and either keep the "
            "open item separate or use that open item as the canonical target. "
            "When question_updates is present, it must be a JSON array whose "
            "items use question_id (not canonical_question_id) to identify "
            "one currently open ledger item, plus only the fields being "
            "updated; do not emit updates for merged or resolved items. "
            "surviving fact must target a real channel member and have an "
            "evidence-bound cross review; owner/operator aliases are only "
            "valid for owner decisions. Repair any rejection reason carried "
            "by the request refs. Do not emit merge or cross-review events."
        )
    # Adaptive members preserve the originating synthesis IDs for audit
    # lineage, but their turn is still a normal contribution turn.
    if refs.get("adaptive_next_round_id"):
        return _channel_contribution_response_contract(adaptive_next_pass=True)
    if refs.get("synthesis_request_id"):
        selected_source = ("Selected immutable prd_source_file: " + json.dumps(refs["prd_source_file"]) + ". "
                           if refs.get("prd_source_file") else "")
        return _contract_response_instruction(
            selected_source + "one JSON object named channel_synthesis containing "
            "title, decision, summary, prd_markdown, requirement_coverage, decision_log, decisions, assumptions, out_of_scope, "
            "acceptance_criteria, verification_commands, open_questions, risks, "
            "recommended_workflow, source_refs, evidence_refs, "
            "source-consumption evidence (source_attestation OR all three consumed_* lists), "
            "classification, dissent, confidence, readiness, and next_round. "
            "Your deliverable now is the complete standalone PRD, not a decision draft, "
            "outline, or recommendation that a later workflow write the PRD. Concision applies "
            "to the chat summary, never to omission of required artifact content. "
            "A read_only agent can publish through this structured reply: Runtime writes the artifact. "
            "Do not request filesystem write permission merely to publish a PRD. "
            "If request refs include prd_source_file, its complete verified body is supplied as "
            "kind=channel_prd_source in context_pack.semantic_source_documents. Read that body completely; "
            "do not call tools to reopen the file. "
            "You may return prd_file:{ref,sha256} matching it exactly instead of retyping the body; "
            "the remaining contract, source coverage, independent review and Owner gates still apply. "
            "A local file link or success summary alone is not a published PRD. "
            "Provide exactly one of prd_file, prd_markdown or prd_composition. For a long existing PRD, "
            "prefer prd_composition to preserve its complete source without retyping it: "
            "{source_message_id, source_message_digest, start_anchor, end_anchor, "
            "edits:[{old,new}], appendix_markdown}. Select one requirement/owner_input document "
            "from semantic_source_documents and copy its message_id and message_body_digest exactly. "
            "The two literal anchors must each occur exactly once; the retained body includes "
            "both anchors. Select the complete product document, excluding conversation wrappers. "
            "Each edit.old must occur exactly once in that retained original body; edits cannot "
            "overlap. Use edits:[] when retaining the original rules. Put all reconciled "
            "decisions, test assumptions, dissent disposition and additional acceptance in "
            "appendix_markdown; make rule precedence explicit and edit contradictory clauses. "
            "Runtime resolves this recipe only against the immutable source for this request, "
            "then publishes the complete expanded Markdown through the ordinary PRD contract. "
            "You still own semantic completeness; source reuse is not quality approval. "
            "prd_markdown, when used instead, is the complete standalone PRD Markdown. "
            "Build a source-driven requirement inventory. requirement_coverage is a non-empty array "
            "of objects with id, source_refs, section, acceptance_ids, and status (covered, deferred, or gap). "
            "Every deferred or gap item must also contain a non-empty reason. "
            "Match each source requirement to a real PRD section and acceptance; state exclusions explicitly. "
            "decision_log contains question/decision, rationale, source_refs, and dissent disposition. "
            "Never equate matching digests or merged questions with semantic completeness. "
            "recommended_workflow and classification must each be JSON "
            "objects; use {} when no structured value applies. readiness must "
            "be an object containing verdict (ready, needs_owner, or "
            "needs_multi_lens), implementation_start (boolean), gaps, risks, "
            "evidence_refs, and reason. Set implementation_start=true only "
            "when verdict=ready, open_questions and gaps are empty, acceptance "
            "criteria are complete, and verification_commands contains at "
            "least one pure executable shell command. Every mandatory "
            "acceptance criterion must have a declared real evidence method; "
            "summary must describe only durable product behavior and must not "
            "include transient sign-off, Owner confirmation, or execution "
            "authorization status. Pending Owner/consensus confirmation is not a product readiness gap; "
            "neither are the later create-task and workflow-start approvals. "
            "Record pending approvals as recommended workflow steps, not as needs_owner gaps. "
            "Use needs_owner only for unresolved product decisions. When inputs are ready "
            "and the Owner permits implementation after those approvals, use ready with "
            "implementation_start=true; this never grants or bypasses either approval. "
            "Preserve implementation_start=false for an explicit planning-only restriction. Every "
            "final PRD must include at least one durable item in decisions; "
            "the short decision field is not a substitute for the decision list. "
            "acceptance criterion must have a stable id. Every verification "
            "command must have a stable id, acceptance_ids matching those "
            "criterion ids, and producer_paths; do not rename acceptance_ids "
            "to covers. Commands must also declare owner, tier, deterministic, "
            "reusable, and timeout_seconds instead of relying on downstream "
            "defaults. Prefer source_attestation after reading all required sources: "
            "{context_pack_id, context_pack_sha256, all_required_sources_read:true}. "
            "Copy the ID and SHA256 exactly from context_pack. This explicitly declares "
            "that you read every required semantic source, structured contribution, and "
            "completed cross-review in that immutable pack. It replaces all three lists: "
            "omit consumed_message_digests, consumed_contribution_refs, and "
            "consumed_contribution_digests when using source_attestation. Runtime expands "
            "only the bound pack's identities and still checks source currentness. "
            "Alternatively, consumed_contribution_refs and digests must cover every "
            "structured contribution and completed cross-review artifact in the "
            "context pack exactly. "
            "browser viewport, pointer, network, storage, refresh, or screenshot "
            "criteria require a repo-root executable Docker Playwright command, "
            "not only unit/build commands. Missing future screenshots or traces "
            "before implementation is not a readiness gap when a runnable command "
            "and producer paths can be planned; a missing or forbidden runner is. "
            "next_round is an object. Omit it or use {\"action\":\"finalize\"} "
            "when this synthesis should proceed to the existing Owner/consensus "
            "path. Use action=continue only when a selective next pass will "
            "materially resolve an evidence-backed conflict without asking the "
            "Owner. A continue object requires non-empty reason, objective, and "
            "unique target_member_ids selected only from the current discussion "
            "participants; it must not add roles, permissions, skills, providers, or "
            "workflow work. Never use continue when open_questions is non-empty. "
            "Read every semantic_source_document. If using the explicit consumed_* lists "
            "instead of source_attestation, copy every required digest from "
            "semantic_source_manifest into consumed_message_digests; never invent a digest. All plural fields must "
            "be JSON arrays. Keep the preceding Markdown concise."
        )
    thread_id = str(request.get("thread_id") or "main")
    sessions = channel.get("discussions")
    candidate_session = (
        sessions.get(thread_id) if isinstance(sessions, dict) else None
    )
    session = candidate_session if isinstance(candidate_session, dict) else {}
    state = str(session.get("state") or "")
    phase1_trigger_message_id = str(
        session.get("adaptive_next_round_message_id")
        or session.get("requirement_message_id")
        or ""
    )
    is_current_blind_reply = (
        state == "phase1_blind"
        and phase1_trigger_message_id == str(request.get("message_id") or "")
    )
    if is_current_blind_reply or state == "phase2_relay" or (
        request.get("target_member_id") and request.get("target_member_id") == channel.get("leader_member_id")
    ):
        return _channel_contribution_response_contract()
    return ""


def _channel_contribution_response_contract(
    *,
    adaptive_next_pass: bool = False,
) -> str:
    adaptive_boundary = (
        " This is a selective next-pass contribution, not a Synthesizer turn. "
        "Return exactly one channel_contribution contract and exactly one "
        "contract marker. Do not emit channel_synthesis, next_round, consensus, "
        "or a PRD."
        if adaptive_next_pass
        else ""
    )
    return _contract_response_instruction(
        "one JSON object named channel_contribution containing "
        "summary, questions, findings, contradictions, risks, source_refs, evidence_refs, freeze, and "
        "optional member_invitations (only the current leader may propose them). "
        "Each invitation uses member_id, an existing profile_id from channel_profile_catalog, "
        "reason, source_refs, and required_reviewer boolean. It is a proposal awaiting "
        "controlled approval, not a completed invitation. If there is no skill gap omit it. "
        "questions is a list of explicit clarification questions, "
        "where each question may carry kind, depends_on, priority, "
        "why_it_matters, recommended_answer, and target_member_id. Each "
        "question kind MUST be exactly one of fact|owner_decision|tradeoff|"
        "clarification and priority MUST be exactly one of p0|p1|p2|p3; "
        "do not use aliases such as critical, high, medium, or low. "
        "Question target rule: owner_decision, tradeoff, and clarification "
        "questions may target the human owner only; omit target_member_id or "
        "write owner, never operator or owner:operator. A fact question is an "
        "agent-to-agent evidence request and MUST target exactly one real "
        "member_id from the current Channel roster, never owner, operator, or "
        "an invented alias. If no current member owns the missing fact, do not "
        "emit it as a question: record it instead as a finding, risk, or "
        "explicit assumption/gate. "
        "the answer space is enumerable, a question may also carry "
        "options (two or three mutually exclusive objects with id, "
        "label, description, and recommended; put the single recommended "
        "option first) plus allow_other. Leave options absent for a "
        "genuinely free-form answer; "
        "findings, contradictions, risks, source_refs, evidence_refs, "
        "and freeze=true when your contribution is complete. Freeze means "
        "this blind pass is finished and you have no additional questions or "
        "evidence to add; it does not mean the question list is empty. You "
        "may return questions and freeze=true together so they enter relay "
        "and dedup. Use freeze=false only when your answer is incomplete or "
        "you still need another question/evidence pass. This blind "
        "phase precedes canonical synthesis: do not open a blocking "
        "question that requires the PRD/artifact/version/digest this "
        "discussion will create later. Review the current requirement "
        "and context digest instead; record missing future output as a "
        "finding or assumption."
        + adaptive_boundary
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ))


__all__ = [
    "channel_reply_response_contract",
    "fake_channel_reply_text",
]
