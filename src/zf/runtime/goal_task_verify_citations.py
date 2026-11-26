"""Resolve optional Task Verify corroboration in the current Judge's read set."""

from collections.abc import Mapping
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.artifact_access import ArtifactAccessError, require_artifact_access
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import validate_required_reads
from zf.runtime.goal_task_verify_evidence import judge_task_verify_evidence
from zf.runtime.input_delivery_contract import pinned_delivery_policy
from zf.runtime.input_delivery_preflight import preflight_required_reads
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _dispatch_manifest(events, result, current):
    closure = ("workflow_run_id", "closure_fact_ref", "closure_fact_digest", "contract_snapshot_digest")
    if any(not result.get(key) or current.get(key) != result[key] for key in closure):
        return None
    descriptor = None
    for event in events:
        if event.type != "fanout.child.dispatched" or event.origin != "kernel":
            continue
        caller = event.payload
        if not isinstance(caller, dict) or any(
            not result.get(k) or caller.get(k) != result[k] for k in ("operation_id", "dispatch_id", "attempt_id")
        ):
            continue
        request = caller.get("payload") or {}
        if (not isinstance(request, dict) or request.get("output_profile_id") != "thin-judge-goal-closure"
                or any(not result.get(k) or request.get(k) != result[k] for k in (*closure, "request_hash", "role_instance"))):
            return None
        manifest = request.get("attempt_source_manifest")
        if not isinstance(manifest, dict) or not manifest.get("ref") or not manifest.get("sha256"):
            return None
        if descriptor is not None and descriptor != manifest:
            return None
        descriptor = manifest
    return descriptor


def readable_task_citations(
    state: Path, events: list[ZfEvent], current: Mapping, result: Mapping, cited: set[str],
    *, ledger_descriptor: Mapping | None,
) -> set[str]:
    """Authorize exact cited envelopes via current membership, manifest and full body reads."""
    if not cited or not current.get("has_task_verify_evidence"):
        return set()
    descriptor = _dispatch_manifest(events, result, current)
    if not descriptor:
        return set()
    try:
        manifest = hydrate_sidecar_ref(state, descriptor).payload
        if not isinstance(manifest, dict) or any(
            manifest.get(k) != result[k] for k in ("workflow_run_id", "attempt_id", "dispatch_id", "task_id")
        ):
            return set()
        fact = hydrate_sidecar_ref(state, {"ref": current["closure_fact_ref"], "sha256": current["closure_fact_digest"]}).payload
        nominated = [r for r in fact["task_verify_evidence"] if r["envelope"]["ref"] in cited]
        if not nominated:
            return set()
        fresh = judge_task_verify_evidence(
            state, events, batch=current["judge_batch_admissions"], run=current["workflow_run_id"],
            generation=current["task_map_generation"], target=current["candidate_head_commit"],
            task_ids={r["task_id"] for r in nominated},
        )
        grants = set()
        for source in manifest["sources"]:
            if not isinstance(source, dict) or "$" not in source.get("allowed_paths", []):
                continue
            try:
                require_artifact_access(source.get("access_scope"), actor=result["role_instance"],
                                        purpose=manifest.get("read_purpose") or "")
            except ArtifactAccessError:
                continue
            grants.add((source["source_id"], source["artifact_id"], source["ref"], source["sha256"]))
        envelope_grants = {(ref, digest) for _, _, ref, digest in grants}
        consumer = {key: result[key] for key in ("workflow_run_id", "task_id", "operation_id", "dispatch_id", "attempt_id")}
        refs, requirements = [], []
        for row in nominated:
            envelope, body, source = row["envelope"], row["control_result"], row["source_id"]
            if (row not in fresh or (envelope["ref"], envelope["sha256"]) not in envelope_grants
                    or (source, source, body["ref"], body["sha256"]) not in grants):
                continue
            refs.append(envelope["ref"])
            requirements.append({
                "source_id": source, "artifact_id": source, "artifact_sha256": body["sha256"],
                "json_path": "$", "min_returned_bytes": 1,
                "consumer_binding": {**consumer, "consumer_actor": result["role_instance"], "artifact_ref": body["ref"]},
            })
        if not refs:
            return set()
        policy = pinned_delivery_policy(state, events, result, fallback=None)
        if not policy or policy.get("attempt_id") != result["attempt_id"]:
            return set()
        policy = {**policy, "required_reads": requirements}
        if ledger_descriptor is None:
            issues = preflight_required_reads(state, policy)
        else:
            digest = ledger_descriptor.get("sha256") or ""
            attempt_dir = active_read_ledger_path(state, result["attempt_id"]).parent.relative_to(state)
            if not digest or ledger_descriptor.get("ref") != str(attempt_dir / f"read-ledger-{digest[:16]}.jsonl"):
                return set()
            issues = validate_required_reads(state, policy=policy, ledger_descriptor=ledger_descriptor)
        by_field = {f"required_reads[{i}]": ref for i, ref in enumerate(refs)}
        failures = {issue["field"] for issue in issues}
        if not failures <= by_field.keys():
            return set()
        return {ref for field, ref in by_field.items() if field not in failures}
    except (KeyError, TypeError, ValueError, OSError):
        return set()
