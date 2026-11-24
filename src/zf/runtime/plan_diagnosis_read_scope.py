"""Authenticate scoped prior Plan citations without changing signed manifests."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def previous_candidate_slices(
    state_dir: Path | None, manifest: Mapping[str, Any], profile: str,
) -> set[str]:
    sources = [s for s in manifest.get("sources", []) if isinstance(s, Mapping)]
    partial = [s for s in sources if s.get("source_id", "").startswith("previous-plan-candidate-")
               and s.get("allowed_paths") not in (None, [], ["$"])
               and s.get("kind") != "plan_rework_candidate_partition"]
    if not state_dir or not partial or profile not in {"workflow-read", "plan-synth"}:
        return set()
    # Required paths are keyed by source ID. A second occurrence with that ID
    # cannot consume a selector granted to another artifact, digest or scope.
    ids = [s.get("source_id") for s in sources]
    if any(ids.count(s["source_id"]) != 1 for s in partial):
        raise ValueError("ambiguous previous Plan citation source ID")
    events = EventLog(state_dir / "events.jsonl").read_all()
    run, attempt = manifest.get("workflow_run_id"), manifest.get("attempt_id")
    issued = [e for e in events if e.type == "workflow.operation.requested" and e.origin == "kernel"
              and e.payload.get("workflow_run_id") == run and e.payload.get("active_attempt_id") == attempt]
    if issued:
        # Existing attempts retain their exact issued policy, even for an older
        # release. New attempts cannot inherit this grant from a predecessor.
        if len(issued) != 1:
            raise ValueError("ambiguous prior Plan read policy for attempt")
        request = hydrate_sidecar_ref(state_dir, issued[0].payload["request_ref"]).payload["request"]
        if request["output_profile_id"] != profile:
            return set()
        from zf.runtime.artifact_read_ledger import _safe_component

        digest = request["source_manifest_digest"]
        frozen = hydrate_sidecar_ref(state_dir, {
            "ref": f"artifacts/attempts/{_safe_component(attempt)}/source-manifests/{digest}.json",
            "sha256": digest,
        }).payload
        old = {s["source_id"]: s for s in frozen["sources"]}
        keys = ("ref", "sha256", "kind", "artifact_id", "allowed_paths", "access_scope", "occurrence_id")
        return {s["source_id"] for s in partial if s["source_id"] in old
                and all(s.get(k) == old[s["source_id"]].get(k) for k in keys)
                and {row["json_path"] for row in request.get("required_reads", [])
                     if row.get("source_id") == s["source_id"] and row.get("artifact_sha256") == s["sha256"]}
                == set(s["allowed_paths"])}
    authority = manifest.get("handoff_authority_contract") or {}
    if (profile != "workflow-read" or authority.get("attempt_domain") != "plan" or not run
            or not manifest.get("plan_artifact_package_digest") or not manifest.get("task_map_generation")):
        return set()
    contexts = [s for s in sources if s.get("source_id") == "plan-rework-context"
                and s.get("kind") == "plan_rework_context"]
    if len(contexts) != 1:
        return set()
    context = hydrate_sidecar_ref(state_dir, contexts[0]).payload
    if context.get("resume_scope") != "candidate_diagnosis_only":
        return set()
    selected = context.get("previous_plan_candidate_refs")
    if not isinstance(selected, list) or not selected:
        return set()
    markers = [e for e in events if e.type == "orchestrator.replan_requested" and e.origin == "kernel"
               and e.actor == "zf-cli" and e.causation_id and e.payload.get("workflow_run_id") == run
               and e.payload.get("classification") == "oa_semantic_return_to_plan"
               and e.payload.get("plan_artifact_package_digest") == manifest.get("plan_artifact_package_digest")
               and e.payload.get("task_map_generation") == manifest.get("task_map_generation")
               and all(context.get(k) and context[k] == e.payload.get(k) for k in (
                   "resume_scope", "rework_of", "rework_attempt", "orchestrator_decision_ref",
                   "previous_plan_candidate_refs"))]
    if len(markers) != 1:
        return set()
    marker = markers[0]
    if not any(e.origin == "kernel" and e.type == "orchestrator.semantic.decision.admitted"
               and e.causation_id == marker.causation_id and e.payload.get("workflow_run_id") == run
               and e.payload.get("decision_ref") == context["orchestrator_decision_ref"]
               and e.payload.get("checkpoint") == "semantic_failure" and e.payload.get("checkpoint_policy") == "blocking"
               and e.payload.get("decision") == "return_to_plan" and e.payload.get("status") == "admitted"
               for e in events[:events.index(marker)]):
        return set()
    refs = {f"previous-plan-candidate-{i}": ref for i, ref in enumerate(selected, 1)
            if isinstance(ref, Mapping)}
    return {s["source_id"] for s in partial
            if (ref := refs.get(s["source_id"])) and ref.get("ref") == s["ref"]
            and (ref.get("artifact_id") or Path(ref["ref"]).name) == s["artifact_id"]
            and (ref.get("sha256") or ref.get("digest")) == s["sha256"]
            and ref.get("allowed_paths") == s["allowed_paths"]
            and (ref.get("access_scope") or {}) == (s.get("access_scope") or {})}
