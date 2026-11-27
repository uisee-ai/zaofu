"""Bound repeated, byte-equivalent Plan bodies without summarizing their meaning."""
from __future__ import annotations

import hashlib
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


def _context_fields(body):
    # Leave future owner instructions / waiver fields on their original full read.
    fields = (*CONTEXT_FIELDS, *(k for k in ("operator_feedback", "read_equivalents") if k in body))
    return fields if set(body) == {*fields, "previous_plan_candidate_refs"} else ()


def has_scoped_rework_context(request):
    paths = [r.get("json_path") for r in request.get("required_reads", [])
             if r.get("source_id") == "plan-rework-context"]
    return any(paths == ["$." + k for k in (*CONTEXT_FIELDS, *extra)] for extra in (
        (), ("operator_feedback",), ("read_equivalents",), ("operator_feedback", "read_equivalents")))


def restore_replayed_rework_reads(payload, previous_request, attempt_id, profile):
    if (profile not in {"workflow-read", "plan-synth"} or not previous_request
            or previous_request.get("active_attempt_id") != attempt_id
            or payload.get("required_reads") != previous_request.get("required_reads")
            or not has_scoped_rework_context(previous_request)):
        return
    # A returned full projection is not a new declaration. Retain the exact
    # charter rows (order and constraints) from the already signed request.
    declared = [previous_request["required_reads"][i]
                for i in previous_request.get("plan_rework_explicit_read_indexes", [])]
    if declared:
        payload["required_reads"] = declared
    else:
        payload.pop("required_reads", None)


def _proved_partition(state_dir, candidate, desc, rows, sources, bodies):
    from zf.runtime.artifact_text_codec import encode_selected
    from zf.runtime.plan_input_read_selection import _read

    scope = candidate.get("access_scope")
    if (candidate.get("kind") != "plan_rework_candidate_partition"
            or not isinstance(scope, dict) or set(scope) != {"read_equivalents"}
            or any(candidate.get(k) != desc[k] for k in _DESCRIPTOR_KEYS - _REBOUND_KEYS
                   - {"kind", "allowed_paths", "access_scope", "read_selections"})):
        return False
    allowed = candidate.get("allowed_paths", [])
    aliases = scope["read_equivalents"]
    if not isinstance(aliases, list) or not aliases or not allowed or "$" in allowed:
        return False
    identity = (candidate["source_id"], candidate["artifact_id"], candidate["sha256"])
    if rows.get(identity, set()) != set(allowed):
        return False
    # The kernel checks structural equivalence against pinned bytes. It never
    # exposes the partitioned root or adds either of the original agent grants.
    body = _read(state_dir, candidate)
    if not isinstance(body, dict) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", k) for k in body):
        return False
    selections = candidate.get("read_selections")
    if (not isinstance(selections, list) or [s["json_path"] for s in selections] != allowed
            or any(s.get("source_sha256") != candidate["sha256"]
                   or s.get("selected_sha256") != hashlib.sha256(encode_selected(body[s["json_path"][2:]]).encode()).hexdigest()
                   for s in selections)):
        return False
    remaining = {"$." + k for k in body} - set(allowed)
    if len(remaining) != len(aliases) or len(body) != len(remaining) + len(allowed):
        return False
    for alias in aliases:
        if not isinstance(alias, dict) or set(alias) != {
            "json_path", "source_id", "source_sha256", "source_json_path", "content_sha256"
        }:
            return False
        path, target_path = alias["json_path"], alias["source_json_path"]
        if path not in remaining or not isinstance(target_path, str) or not re.fullmatch(r"\$\.[A-Za-z0-9_-]+", target_path):
            return False
        remaining.remove(path)
        targets = [k for k in sources if k[0] == alias["source_id"] and k[2] == alias["source_sha256"]]
        if len(targets) != 1:
            return False
        target = targets[0]
        # Require a separately mandatory, fully granted authenticated donor.
        if (not target[0].startswith("previous-plan-candidate-") or sources[target].get("access_scope")
                or sources[target].get("allowed_paths") != ["$"] or "$" not in rows.get(target, ())
                or not isinstance(bodies.get(target), dict) or target_path[2:] not in bodies[target]):
            return False
        value = body[path[2:]]
        if (encode_selected(value) != encode_selected(bodies[target][target_path[2:]])
                or canonical_json_sha256(value) != alias["content_sha256"]):
            return False
    return not remaining


def rework_context_paths(manifest, sources, bodies, required, lookup_failure, previous_request,
                         *, state_dir=None, read_events=None):
    """Return selectors only when all prior and current data/authority bindings agree."""
    if lookup_failure is None or previous_request is not None and not has_scoped_rework_context(previous_request):
        return ()
    contexts = [(k, s) for k, s in sources.items() if k[0] == "plan-rework-context"]
    if len(contexts) != 1:
        return ()
    rows = {}
    for row in required:
        if not any(row.get(k) for k in ("max_items", "max_chars", "allow_truncated")):
            key = row.get("source_id"), row.get("artifact_id"), row.get("artifact_sha256")
            rows.setdefault(key, set()).add(row.get("json_path", "$"))
    key, source = contexts[0]
    body = bodies.get(key)
    fields = _context_fields(body) if isinstance(body, dict) else ()
    if (not fields or "$" not in rows.get(key, ()) or source.get("kind") != "plan_rework_context"
            or source.get("allowed_paths") != ["$"] or source.get("access_scope")
            or body["schema_version"] != "plan-rework-context.v1" or not isinstance(body["rework_of"], str)
            or not body["rework_of"] or type(body["rework_attempt"]) is not int or body["rework_attempt"] <= 0
            or not isinstance(body["rework_feedback"], list) or not body["rework_feedback"]):
        return ()
    descriptors = body["previous_plan_candidate_refs"]
    if not isinstance(descriptors, list) or not descriptors or not manifest.get("workflow_run_id"):
        return ()
    if "operator_feedback" in body:
        from zf.runtime.plan_owner_rework_read_history import owner_reviewed_history
        if not state_dir or not read_events or not isinstance(body["operator_feedback"], list) or not body["operator_feedback"]:
            return ()
        try:
            if not owner_reviewed_history(state_dir, manifest, body, read_events()):
                return ()
        except (KeyError, ValueError, TypeError, OSError):
            return ()
    else:
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

    partitions = []
    for i, desc in enumerate(descriptors, 1):
        if (not isinstance(desc, dict) or set(desc) != _DESCRIPTOR_KEYS
                or any(not isinstance(desc[k], str) or not desc[k]
                       for k in ("ref", "sha256", "artifact_id", "source_id", "source_event_id", "occurrence_id"))):
            return ()
        candidate_key = (f"previous-plan-candidate-{i}", desc["artifact_id"], desc["sha256"])
        candidate = previous.get(candidate_key)
        if (not candidate or desc["allowed_paths"] != ["$"] or desc["access_scope"]
                or not desc["recovery_ref"] or not desc["read_selections"]
                or candidate.get("schema_version") not in ("", desc["schema_version"])
                or candidate.get("source_event_id") != manifest.get("source_event_id", "")):
            return ()
        if candidate.get("kind") == "plan_rework_candidate_partition":
            try:
                if not state_dir or not _proved_partition(state_dir, candidate, desc, rows, previous, bodies):
                    return ()
            except (KeyError, ValueError, TypeError, OSError):
                return ()
            partitions.append({"source_id": candidate_key[0], "sha256": candidate_key[2],
                               "read_equivalents": candidate["access_scope"]["read_equivalents"]})
        elif (candidate_key not in bodies or "$" not in rows.get(candidate_key, ())
                or any(candidate.get(k) != desc[k] for k in _DESCRIPTOR_KEYS - _REBOUND_KEYS)):
            return ()
        rebound = build_attempt_source_manifest(
            workflow_run_id=manifest["workflow_run_id"], task_id=manifest.get("task_id", ""),
            attempt_id=manifest.get("attempt_id", ""), dispatch_id=manifest.get("dispatch_id", ""),
            sources=[{**candidate, "occurrence_id": ""}],
        )["sources"]
        if not rebound or rebound[0]["occurrence_id"] != candidate.get("occurrence_id"):
            return ()
    if (partitions or "read_equivalents" in body) and (not partitions or body.get("read_equivalents") != partitions):
        return ()
    return tuple("$." + key for key in fields)
