"""Evidence for a resident deciding whether the same frozen Reader can use a changed host route."""
import hashlib
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.call_result_envelope import canonical_json_bytes, write_immutable_json_sidecar
from zf.runtime import candidate_host_navigation as navigation
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations

SNAPSHOT = "candidate_reader_method_ref"
PROOF = "reader_method_digest"
_SCHEMA = "candidate-reader-host-navigation.v1"


def _digest(value: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _identity(payload: dict, dispatch: dict) -> dict:
    names = ("operation_id", "request_hash", "attempt_id", "workflow_run_id", "task_id",
             "fanout_id", "child_id", "role_instance", "target_commit", "contract_snapshot_digest",
             "target_snapshot_digest", "attempt_source_manifest_digest")
    if any(not payload.get(key) for key in names):
        raise ValueError("incomplete Reader binding")
    if any(dispatch.get(k) != payload[k] for k in ("operation_id", "role_instance", "fanout_id", "child_id")):
        raise ValueError("transport and Reader identities differ")
    return {**{key: payload[key] for key in names}, "transport_run_id": dispatch["run_id"]}


def _manifest(state: Path, payload: dict) -> dict:
    manifest = hydrate_sidecar_ref(state, {"ref": payload["attempt_source_manifest_ref"],
        "sha256": payload["attempt_source_manifest_digest"]}).payload
    if any(manifest.get(k) != payload[k] for k in ("workflow_run_id", "task_id", "attempt_id")):
        raise ValueError("source manifest identity mismatch")
    return manifest


def _delivered_section(briefing: str) -> str:
    lines = briefing.splitlines()
    sections = [i for i, line in enumerate(lines) if line.startswith(navigation.HOST_NAVIGATION_TITLE)]
    if not sections:
        return ""
    if len(sections) != 1:
        raise ValueError("ambiguous dispatched host navigation")
    start = sections[0]
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    return "\n".join(lines[start:end])


def record_delivered_method(state: Path, dispatch: dict, briefing: Path, *, role: str) -> None:
    """Attach only to the kernel's successful transport occurrence, never a provider read receipt."""
    request = dispatch.get("payload") or {}
    if request.get("output_profile_id") != "candidate-verify" or not request.get("candidate_host_evidence_ref"):
        return
    try:
        binding = _identity(request, dispatch)
        manifest = _manifest(state, request)
        entries = navigation.host_navigation_entries(state, manifest, request, role_name=role)
        section = navigation.render_navigation_entries(binding["attempt_id"], entries)
        raw = briefing.read_bytes()
        text = raw.decode("utf-8")
        if (request["attempt_source_manifest_ref"] not in text or binding["attempt_id"] not in text
                or section.strip() != _delivered_section(text)):
            raise ValueError("candidate host route does not match the dispatched briefing")
        packet = {"schema_version": _SCHEMA, "binding": binding, "role": role, "purpose": "candidate-verify",
                  "briefing_sha256": hashlib.sha256(raw).hexdigest(), "entries": _receipt_locators(entries)}
        dispatch[SNAPSHOT] = write_immutable_json_sidecar(state, packet, root="candidate-reader-methods",
            kind="candidate_reader_method", schema_version=_SCHEMA, created_by="kernel")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        # Keep the normal transport snapshot even when the additional method witness cannot be established.
        dispatch["candidate_reader_method_error"] = str(exc)


def _receipt_locators(entries: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key != "command"} for row in entries]


def _source_rows(routes: set[tuple[str, ...]]) -> list[dict]:
    return [{"command_id": command, "receipt_id": receipt, "source_id": source, "sha256": sha}
            for command, receipt, source, sha in sorted(routes)]


def _routes(entries: list[dict]) -> set[tuple[str, ...]]:
    routes = set()
    for row in entries:
        base = (row["command_id"], row["receipt_id"], row["receipt_source"], row["receipt_sha256"])
        routes.add(base)
        routes.update((row["command_id"], row["receipt_id"], media["source_id"], media["sha256"])
                      for media in row["media_sources"])
    return routes


def reader_method_change(state: Path, dispatch: ZfEvent, operation: dict, *, failure_event_id: str) -> dict:
    """Rerender only the old signed WRC. Newly granted permissions cannot justify a retry of it."""
    source = dispatch.payload.get("payload") or {}
    if operation.get("semantic_verdict") != "abstained":
        return {}
    try:
        envelope = hydrate_sidecar_ref(state, operation["request_ref"]).payload
        request = envelope["request"]
        if request.get("output_profile_id") != "candidate-verify" or not request.get("candidate_host_evidence_ref"):
            return {}
        binding = _identity(source, dispatch.payload)
        attempt = binding["attempt_id"]
        if (operation_request_hash(envelope) != operation["request_hash"]
                or operation["request_hash"] != binding["request_hash"]
                or envelope["operation_id"] != binding["operation_id"]
                or operation["operation_id"] != envelope["operation_id"]
                or request["active_attempt_id"] != attempt or operation["active_attempt_id"] != attempt
                or request["source_manifest_digest"] != binding["attempt_source_manifest_digest"]
                or any(request.get(key) != binding[key] for key in (
                    "workflow_run_id", "task_id", "fanout_id", "child_id", "role_instance",
                    "target_commit", "contract_snapshot_digest", "target_snapshot_digest"))):
            raise ValueError("dispatched Reader and signed WRC do not match")
        descriptor = dispatch.payload.get(SNAPSHOT)
        if not descriptor or not descriptor.get("sha256"):
            raise ValueError("original delivered method snapshot missing")
        old = hydrate_sidecar_ref(state, descriptor).payload
        if old.get("binding") != binding or old.get("schema_version") != _SCHEMA or not old.get("role"):
            raise ValueError("original delivered method belongs to another Reader")
        manifest = _manifest(state, source)
        host = request["candidate_host_evidence_ref"]
        index = next((row for row in manifest["sources"] if row["source_id"] == "candidate-host-execution"), {})
        if (not index or (index.get("ref"), index.get("sha256")) != (host["ref"], host["sha256"])
                or "$" not in index.get("allowed_paths", []) or not artifact_access_allowed(
                    index.get("access_scope"), actor=binding["role_instance"], role=old["role"], purpose="candidate-verify")):
            raise ValueError("original host index unavailable to this Reader")
        verify_sidecar_ref(state, index)
        preview = _receipt_locators(navigation.host_navigation_entries(state, manifest, {
            **request, "operation_id": envelope["operation_id"], "attempt_id": attempt,
        }, role_name=old["role"]))
        before, after = _routes(old["entries"]), _routes(preview)
        by_source = {row["source_id"]: row for row in manifest["sources"]}
        for _, _, source_id, source_sha in after:
            row = by_source[source_id]
            if row["sha256"] != source_sha:
                raise ValueError("host navigation source digest mismatch")
            verify_sidecar_ref(state, row)
        evidence = {"failure_event_id": failure_event_id, "dispatch_event_id": dispatch.id, "binding": binding, "delivered_method_ref": descriptor,
                    "role": old["role"], "purpose": old["purpose"], "preview_entries": preview,
                    "added_routes": _source_rows(after - before), "lost_routes": _source_rows(before - after)}
        if not preview:
            return {**evidence, "status": "unknown", "reason": "no authorized original host receipt is accessible"}
        if before - after:
            return {**evidence, "status": "unknown", "reason": "previously offered routes are unavailable"}
        status = "improved" if after - before else "unchanged"
        # Reordering equivalent locators is not a new reason to summon the resident.
        proof = _digest({key: value for key, value in evidence.items() if key != "preview_entries"})
        return {**evidence, "status": status, PROOF: proof}
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        return {"status": "unknown", "dispatch_event_id": dispatch.id, "failure_event_id": failure_event_id, "reason": str(exc)}


def validate_resident_reader_method(state: Path, events: list[ZfEvent], action: dict,
                                    *, consuming_recommendation: bool = True) -> None:
    """Recompute method proof at recommendation consumption, independent of a resident's assertions."""
    op_id = action.get("target_operation_id")
    if not op_id:
        return
    dispatch = next((e for e in reversed(events) if e.origin == "kernel" and e.type == "fanout.child.dispatched"
                     and e.payload.get("fanout_id") == action.get("fanout_id")
                     and e.payload.get("child_id") == action.get("fanout_child_id")
                     and e.payload.get("operation_id") == op_id), None)
    if not dispatch:
        if action.get(PROOF):
            raise ValueError("candidate_reader_method_dispatch_missing")
        return  # Exact dispatch/rebuild admission rejects stale or unidentified actions.
    change = reader_method_change(state, dispatch, reduce_workflow_operations(events).get(op_id, {}),
                                  failure_event_id=action.get("source_event_id") or "")
    if action.get(PROOF) and not change:
        raise ValueError("candidate_reader_method_binding_missing")
    if change and (change["status"] != "improved" or action.get(PROOF) != change.get(PROOF)):
        raise ValueError("candidate_reader_method_change_unproven:" + change["status"])
    if consuming_recommendation and change and any(e.type == "repair.action.requested" and e.origin == "kernel"
            and e.payload.get("recovery_action") == "child-rebuild"
            and e.payload.get("source_event_id") == change["failure_event_id"]
            and e.payload.get("target_operation_id") == op_id for e in events):
        raise ValueError("candidate_reader_method_rebuild_already_requested")
