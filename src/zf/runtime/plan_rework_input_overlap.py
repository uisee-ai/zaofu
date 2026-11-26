"""Bound repeated, byte-equivalent Plan bodies without summarizing their meaning."""
from __future__ import annotations

import json
import re

from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def partition_repeated_candidate_bodies(state_dir, sources):
    """Only new rework manifests change; immutable original artifacts stay intact.

    Keep the first full body and every distinct field. Later large identical
    fields refer to that first required source. Partial grants and non-JSON
    artifacts keep their original scope. This is equality, not relevance.
    """
    known = {}
    result = []
    overlaps = []
    for source in sources:
        if (not source.get("source_id", "").startswith("previous-plan-candidate-")
                or source.get("allowed_paths") != ["$"]):
            result.append(source)
            continue
        try:
            body = hydrate_sidecar_ref(state_dir, source).payload
        except (ValueError, OSError):
            result.append(source)
            continue
        if not isinstance(body, dict) or not all(re.fullmatch(r"[A-Za-z0-9_-]+", k) for k in body):
            result.append(source)
            continue
        aliases, distinct = [], []
        for key, value in body.items():
            path = f"$.{key}"
            # Small scalars/metadata remain visible even when repeated.
            signature = (canonical_json_sha256(value)
                         if isinstance(value, (dict, list)) and len(json.dumps(value).encode()) >= 1024 else "")
            previous = known.get(signature) if signature else None
            if previous is not None and previous["body"] == value:
                aliases.append({"json_path": path, "source_id": previous["source_id"],
                                "source_sha256": previous["sha256"],
                                "source_json_path": previous["json_path"], "content_sha256": signature})
            else:
                distinct.append(path)
                if signature:
                    known[signature] = {"body": value, "source_id": source["source_id"],
                                        "sha256": source["sha256"], "json_path": path}
        if aliases and distinct:
            source = {**source, "kind": "plan_rework_candidate_partition",
                      "allowed_paths": distinct,
                      "access_scope": {**source.get("access_scope", {}), "read_equivalents": aliases}}
            overlaps.append({"source_id": source["source_id"], "sha256": source["sha256"],
                             "read_equivalents": aliases})
        result.append(source)
    # Expose the navigation in the existing mandatory rework context, not only
    # in manifest metadata that a worker's input index might omit.
    if overlaps:
        for index, source in enumerate(result):
            if source.get("source_id") == "plan-rework-context":
                body = hydrate_sidecar_ref(state_dir, source).payload
                descriptor = write_immutable_json_sidecar(state_dir, {**body, "read_equivalents": overlaps},
                    root="plan-synth/rework-context", kind="plan_rework_context",
                    schema_version="plan-rework-context.v1", created_by="plan-rework-input-overlap")
                result[index] = {**source, **descriptor}
    return result


# v1 repeats the prior manifest descriptors inside an otherwise semantic
# feedback document. Only this complete, known shape has a lossless shortcut.
CONTEXT_FIELDS = ("schema_version", "rework_of", "rework_attempt", "rework_feedback")
_DESCRIPTOR_KEYS = {
    "ref", "sha256", "artifact_id", "kind", "allowed_paths", "access_scope", "retention",
    "read_selections", "recovery_ref", "source_id", "source_event_id", "occurrence_id", "schema_version",
}
# The current attempt rebinds these four metadata fields. Their *original*
# values are still authenticated against the failure and readable at context $.
_REBOUND_KEYS = {"source_id", "source_event_id", "occurrence_id", "schema_version"}


def has_scoped_rework_context(request):
    rows = [r for r in request.get("required_reads", []) if r.get("source_id") == "plan-rework-context"]
    return [r.get("json_path") for r in rows] == ["$." + key for key in CONTEXT_FIELDS]


def rework_context_paths(manifest, sources, bodies, required, lookup_failure, previous_request):
    """Return selectors only when the prior failure and current full-body grants agree."""
    if lookup_failure is None or previous_request is not None and not has_scoped_rework_context(previous_request):
        return ()
    contexts = [(k, s) for k, s in sources.items() if k[0] == "plan-rework-context"]
    if len(contexts) != 1:
        return ()
    full_rows = {(r.get("source_id"), r.get("artifact_id"), r.get("artifact_sha256")) for r in required
                 if r.get("json_path", "$") == "$"
                 and not any(r.get(k) for k in ("max_items", "max_chars", "allow_truncated"))}
    key, source = contexts[0]
    body = bodies.get(key)
    if (key not in full_rows or source.get("kind") != "plan_rework_context"
            or source.get("allowed_paths") != ["$"] or source.get("access_scope")
            or not isinstance(body, dict) or set(body) != {*CONTEXT_FIELDS, "previous_plan_candidate_refs"}
            or body["schema_version"] != "plan-rework-context.v1" or not isinstance(body["rework_of"], str)
            or not body["rework_of"] or type(body["rework_attempt"]) is not int or body["rework_attempt"] <= 0
            or not isinstance(body["rework_feedback"], list) or not body["rework_feedback"]):
        return ()
    descriptors = body["previous_plan_candidate_refs"]
    if not isinstance(descriptors, list) or not descriptors or not manifest.get("workflow_run_id"):
        return ()
    failure = lookup_failure(body["rework_of"])
    if (failure is None or failure.id != body["rework_of"] or failure.origin != "kernel"
            or failure.type != "prd.plan.failed" or failure.payload.get("status") != "failed"
            or failure.payload.get("workflow_run_id") != manifest["workflow_run_id"]
            or failure.payload.get("previous_plan_candidate_refs") != descriptors):
        return ()

    previous = {k: s for k, s in sources.items() if k[0].startswith("previous-plan-candidate-")}
    if len(previous) != len(descriptors):
        return ()
    from zf.runtime.artifact_read_ledger import build_attempt_source_manifest

    for i, desc in enumerate(descriptors, 1):
        if (not isinstance(desc, dict) or set(desc) != _DESCRIPTOR_KEYS
                or any(not isinstance(desc[k], str) or not desc[k]
                       for k in ("ref", "sha256", "artifact_id", "source_id", "source_event_id", "occurrence_id"))):
            return ()
        candidate_key = (f"previous-plan-candidate-{i}", desc["artifact_id"], desc["sha256"])
        candidate = previous.get(candidate_key)
        if (not candidate or candidate_key not in full_rows or candidate_key not in bodies
                or desc["allowed_paths"] != ["$"] or desc["access_scope"]
                or not desc["recovery_ref"] or not desc["read_selections"]
                or candidate.get("schema_version") not in ("", desc["schema_version"])
                or candidate.get("source_event_id") != manifest.get("source_event_id", "")
                or any(candidate.get(k) != desc[k] for k in _DESCRIPTOR_KEYS - _REBOUND_KEYS)):
            return ()
        rebound = build_attempt_source_manifest(
            workflow_run_id=manifest["workflow_run_id"], task_id=manifest.get("task_id", ""),
            attempt_id=manifest.get("attempt_id", ""), dispatch_id=manifest.get("dispatch_id", ""),
            sources=[{**candidate, "occurrence_id": ""}],
        )["sources"]
        if not rebound or rebound[0]["occurrence_id"] != candidate.get("occurrence_id"):
            return ()
    return tuple("$." + key for key in CONTEXT_FIELDS)
