"""Preserve a document author's claim independently of Critic review scope."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def reviewed_author_profile(
    state_dir: Path, review: Mapping[str, Any], *, plan_ref: str, plan_digest: str = "",
) -> str:
    """Read only the author and Plan pinned by this exact candidate contract."""
    digest = review.get("plan_synth_contract_digest")
    if not digest or review.get("reviewed_plan_candidate_digest") != digest or not plan_ref:
        return ""
    try:
        contract = hydrate_sidecar_ref(state_dir, {
            "ref": review["plan_synth_contract_ref"], "sha256": digest,
        }).payload
        if contract.get("schema_version") != "plan-synth-contract.v1" or any(
            not review.get(key) or contract.get(key) != review[key]
            for key in ("workflow_run_id", "fanout_id", "stage_id", "plan_revision")
        ):
            return ""
        sources = contract["source_bindings"]
        plans = [row for row in sources if row.get("ref") == plan_ref]
        if len(plans) != 1 or (plan_digest and plans[0].get("sha256") != plan_digest):
            return ""
        # Hydration verifies the current body, even when the caller has no digest.
        hydrate_sidecar_ref(state_dir, {**plans[0], "content_type": "text/markdown"})
        claims = []
        for child in contract["child_bindings"]:
            matches = [row for row in sources if all(child.get(key) and row.get(key) == child[key]
                       for key in ("source_id", "artifact_id", "sha256"))]
            if len(matches) != 1:
                return ""
            author = hydrate_sidecar_ref(state_dir, matches[0]).payload
            if (author.get("child_id") == child.get("child_id")
                    and (author.get("plan_artifact_ref") or author.get("plan_ref")) == plan_ref):
                claims.append(str(author.get("plan_content_profile") or ""))
        return claims[0] if len(claims) == 1 and claims[0] in {
            "derived_plan", "derived_plan_full", "summary_only",
        } else ""
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ""


def preview_author_profile(
    state_dir: Path, events: Sequence[Any], source: Any, *, plan_ref: str,
    markdown: str, fallback: str,
) -> str:
    """Read-only repair of a legacy generic label; never upgrade a summary claim."""
    if fallback not in {"", "derived_plan"} or source is None:
        return fallback
    from zf.runtime.plan_output_lineage import reviewed_plan_generation

    def field(event: Any, key: str) -> Any:
        return event.get(key) if isinstance(event, Mapping) else getattr(event, key, None)

    reviews = [event for event in events if field(event, "id") == field(source, "causation_id")
               and field(event, "type") == "fanout.synth.completed"]
    if len(reviews) != 1:
        return fallback
    review = reviews[0]
    try:
        body, current = field(review, "payload"), field(source, "payload")
        generation = reviewed_plan_generation(SimpleNamespace(payload=body))
        if (current.get("workflow_run_id") != body.get("workflow_run_id")
                or current.get("fanout_id") != body.get("fanout_id")
                or current.get("plan_revision") != generation):
            return fallback
        # The admitted semantic delta may explicitly declare an abbreviated body.
        control = hydrate_sidecar_ref(state_dir, body["control_result_ref"]).payload
        if control.get("plan_content_profile") not in (None, "", "derived_plan"):
            return fallback
        return reviewed_author_profile(state_dir, body, plan_ref=plan_ref,
            plan_digest=hashlib.sha256(markdown.encode()).hexdigest()) or fallback
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return fallback
