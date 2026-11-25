"""On-demand independent review of terminal attempts, outside the workflow loop."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.segments import iter_event_records
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import canonical_json_sha256 as digest
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.skill_attempt_evaluation import IDENTITY, EvaluationHistory
from zf.runtime.skill_evaluation_projection import SkillEvaluationObserver
from zf.runtime.skill_review_model import (
    MAX_INPUT_BYTES,
    OPTIONS,
    OUTPUT_TOKENS,
    blinded_input,
    model_identity,
    review_once,
    review_prompt_digest,
)
from zf.runtime.skill_semantic_review import REVIEW_EVENT, REVIEWER, RULE, review_packet

POLICY = "task-verify-stratified.v1"


def select_cards(cards, sample_percent, explicit=False):
    groups = {"released": [], "withheld": []}
    selected = []
    for card in sorted(cards, key=lambda c: digest([c[k] for k in IDENTITY])):
        outcome = card["task_outcome"]
        if card["critical_failures"]:
            selected.append((card, "critical_evidence_conflict"))
        elif explicit:
            selected.append((card, "operator_selected"))
        elif outcome in {"passed", "rejected", "blocked", "abstained"}:
            groups["released" if outcome == "passed" else "withheld"].append(card)
    limits = {k: (len(rows) * sample_percent + 99) // 100 for k, rows in groups.items()}
    for i in range(max(limits.values())):
        for group, rows in groups.items():
            if i < limits[group]:
                selected.append((rows[i], "sample_" + group))
    return selected


def review_batch(
    state: Path,
    root: Path,
    config,
    *,
    run_id="",
    attempt_id="",
    model,
    max_calls=3,
    sample_percent=10,
    timeout=90,
):
    if not run_id and not attempt_id:
        raise ValueError("a run or attempt is required")
    if os.environ.get("ZF_ROLE_INSTANCE"):
        raise ValueError("run independent review from an operator context")
    if not 0 <= max_calls <= 100 or not 0 <= sample_percent <= 100 or not 1 <= timeout <= 600:
        raise ValueError("invalid review budget, timeout or sample percentage")
    policy = {
        "version": POLICY,
        "sample_percent": sample_percent,
        "max_calls_per_batch": max_calls,
        "timeout_seconds_per_call": timeout,
        "max_input_bytes": MAX_INPUT_BYTES,
        "max_output_tokens": OUTPUT_TOKENS,
    }
    rows, calls = [], 0
    # Only competing review requests share this lock. Business dispatch and the observer do not.
    with locked_path(state / "projections" / "skill-review-batch", timeout_seconds=1):
        history = EvaluationHistory(state, root)
        for record in iter_event_records(state, config=config, strict_io=True):
            history.consume(record.event)
        keys = [
            key
            for key in history.terminal
            if (not run_id or key[0] == run_id) and (not attempt_id or key[2] == attempt_id)
        ]
        if attempt_id and len(keys) != 1:
            raise ValueError("select one terminal Task Verify attempt")
        writer = EventWriter(event_log_from_project(state, config=config))
        identity = None
        identity_error = None
        for card, trigger in select_cards(
            (history.mechanical_card(k) for k in keys), sample_percent, bool(attempt_id)
        ):
            key = tuple(card[k] for k in IDENTITY)
            packet = review_packet(history, key, card)
            packet_digest = digest(packet)
            decision = {
                "schema_version": "skill-independent-review.v1",
                "reviewer_role": REVIEWER,
                "rule": RULE,
                "input_digest": packet_digest,
                "base_digest": packet["base_digest"],
                "identity": list(key),
                "policy": policy,
                "trigger": trigger,
                "requested_model": model,
                "status": "pending",
            }
            match = history.matching_review(key, card, packet)
            previous = match[1] if match else None
            if calls >= max_calls:
                decision["pending_reason"] = "budget_exhausted"
            elif not packet["obligations"] or not card["evidence"]["control_result"]:
                decision["pending_reason"] = "missing_frozen_input"
            elif card["task_outcome"] not in {"passed", "blocked", "rejected", "abstained"}:
                decision["pending_reason"] = "missing_business_verdict"
            elif len(json.dumps(blinded_input(packet), ensure_ascii=False).encode()) > MAX_INPUT_BYTES:
                decision["pending_reason"] = "input_budget_exhausted"
            elif card["role"] == REVIEWER:
                decision["pending_reason"] = "reviewer_conflict"
            elif identity_error:
                decision["pending_reason"] = identity_error
            else:
                try:
                    identity = identity or model_identity(model)
                    if (
                        previous
                        and previous.get("status") in {"reviewed", "abstained"}
                        and previous.get("reviewer") == identity
                        and previous.get("rule") == RULE
                        and previous.get("prompt_digest") == review_prompt_digest(packet)
                        and previous.get("inference_options") == OPTIONS
                    ):
                        rows.append({"attempt_id": key[2], "status": previous["status"], "reused": True})
                        continue
                    calls += 1
                    decision.update(review_once(packet, identity, timeout))
                    decision["status"] = decision["decision"]["status"]
                except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as exc:
                    decision["pending_reason"] = (
                        "reviewer_unavailable"
                        if isinstance(exc, (OSError, http.client.HTTPException))
                        else "invalid_or_unavailable_review"
                    )
                    if identity is None:
                        identity_error = decision["pending_reason"]
            # Budget/unavailability cannot erase a completed evaluation of these frozen inputs.
            if previous and decision["status"] == "pending" and previous["status"] != "pending":
                rows.append(
                    {
                        "attempt_id": key[2],
                        "status": "pending",
                        "pending_reason": decision["pending_reason"],
                        "retained_status": previous["status"],
                    }
                )
                continue
            if previous == decision:
                rows.append({"attempt_id": key[2], "status": previous["status"], "reused": True})
                continue
            packet_ref = write_immutable_json_sidecar(
                state,
                packet,
                root="skill-reviews/inputs",
                kind="skill_review_input",
                schema_version="skill-review-input.v1",
                created_by=REVIEWER,
            )
            ref = write_immutable_json_sidecar(
                state,
                decision,
                root="skill-reviews/decisions",
                kind="skill_independent_review",
                schema_version=decision["schema_version"],
                created_by=REVIEWER,
            )
            writer.append(
                ZfEvent(
                    type=REVIEW_EVENT,
                    origin="kernel",
                    actor=REVIEWER,
                    task_id=card["task_id"],
                    causation_id=card["terminal_event_id"],
                    correlation_id=key[0],
                    payload={**dict(zip(IDENTITY, key)), "input_ref": packet_ref, "review_ref": ref},
                )
            )
            rows.append(
                {
                    "attempt_id": key[2],
                    "trigger": trigger,
                    "status": decision["status"],
                    "pending_reason": decision.get("pending_reason"),
                    "review_ref": ref["ref"],
                }
            )
    if rows:
        SkillEvaluationObserver(state, root, config).poll()
    return {"policy": policy, "calls": calls, "reviews": rows}
