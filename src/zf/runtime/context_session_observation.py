"""Bind a late native session to historical, admitted context delivery.

This evidence never authorizes an attempt or claims retained model context.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from zf.runtime.artifact_output_match import complete_output_observed, observable_outputs
from zf.runtime.artifact_read_ledger_validation import _parse_ledger, validate_required_reads
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

SCHEMA = "context-session-observation.v1"
REF_FIELD = "context_delivery_session_observation_ref"
_IDENTITY = ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id", "role_instance")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _load(state: Path, ref: Mapping[str, Any]) -> Any:
    _require(bool(ref.get("ref") and ref.get("sha256")), "unbound_ref")
    return hydrate_sidecar_ref(state, dict(ref)).payload


def _source_ref(body: Mapping[str, Any], stem: str) -> dict[str, str]:
    return {"ref": str(body.get(f"{stem}_ref") or ""),
            "sha256": str(body.get(f"{stem}_digest") or "")}


def _validate(state: Path, proof: Mapping[str, Any], receipt_ref: Mapping[str, Any]) -> str:
    _require(proof.get("schema_version") == SCHEMA, "observation_schema")
    original = proof.get("original_receipt_ref") or {}
    _require(all(original.get(k) == receipt_ref.get(k) for k in ("ref", "sha256")),
             "receipt_binding")
    receipt = _load(state, original)
    _require(isinstance(receipt, dict) and receipt.get("schema_version") == "context-delivery-receipt.v1",
             "receipt_schema")
    _require(not receipt.get("provider_session_id"), "session_already_bound")
    identity = proof.get("identity") or {}
    _require(all(identity.get(k) and identity[k] == receipt.get(k) for k in _IDENTITY),
             "receipt_identity")
    _require(bool(identity.get("request_hash")), "request_identity")
    envelope = _load(state, _source_ref(receipt, "delivery_envelope"))
    _require(envelope.get("schema_version") == "context-delivery-envelope.v1", "envelope_schema")
    _require(all(envelope.get(k) == receipt.get(k) for k in (*_IDENTITY,
        "source_manifest_ref", "source_manifest_digest", "execution_binding_digest",
        "provider_session_id", "renderer_version")), "envelope_binding")
    manifest = _load(state, _source_ref(receipt, "source_manifest"))
    _require(all(manifest.get(k) == identity.get(k)
                 for k in ("workflow_run_id", "attempt_id", "dispatch_id")), "manifest_identity")
    _require(manifest.get("task_id", "") == identity.get("task_id", ""), "task_identity")
    policy = proof.get("input_policy") or {}
    _require(policy.get("attempt_id") == identity["attempt_id"] and policy.get("required_reads"),
             "policy_identity")
    ledger_ref = proof.get("read_ledger_ref") or {}
    # Use the existing full-coverage/identity gate in addition to witness checks.
    _require(not validate_required_reads(state, policy=policy, ledger_descriptor=ledger_ref),
             "ledger_validation")
    ledger = _load(state, ledger_ref)
    _require(isinstance(ledger, str), "ledger_schema")
    rows = _parse_ledger(ledger.encode(), expected_attempt_id=identity["attempt_id"])
    _require(bool(rows), "no_observation")
    sources = {(s["source_id"], s["artifact_id"]): s for s in manifest["sources"]}
    sessions: set[str] = set()
    identity_anchors = 0
    cache: dict[tuple[str, str], Any] = {}

    def load(ref: Mapping[str, Any]) -> Any:
        key = (str(ref.get("ref") or ""), str(ref.get("sha256") or ""))
        if key not in cache:
            cache[key] = _load(state, ref)
        return cache[key]

    for row in rows:
        _require(all(row.get(k) == identity[k] for k in
                     ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id")),
                 "read_identity")
        _require(row.get("task_id", "") == identity.get("task_id", "")
                 and row.get("consumer_actor") == identity["role_instance"], "read_consumer")
        _require(row.get("consumer_provider") == "codex"
                 and row.get("production_status") == "stdout_flushed"
                 and row.get("provider_delivery_status") == "observed_in_transcript",
                 "delivery_unknown")
        source = sources.get((row.get("source_id"), row.get("artifact_id")), {})
        _require(bool(source) and all(row.get(a) == source.get(b) for a, b in (
            ("artifact_ref", "ref"), ("artifact_sha256", "sha256"), ("occurrence_id", "occurrence_id"))),
            "source_occurrence")
        _require("$" in source["allowed_paths"] or row.get("json_path") in source["allowed_paths"],
                 "source_selection")
        output = load(row.get("output_ref") or {})
        _require(output.get("schema_version") == "artifact-output.v1", "output_schema")
        body = output.get("body")
        _require(isinstance(body, str) and hashlib.sha256(body.encode()).hexdigest() == row.get("output_sha256"),
                 "output_digest")
        page = json.loads(body)
        if page.get("schema_version") == "required-artifact-page.v2":
            _require(page.get("attempt_id") == identity["attempt_id"]
                 and row.get("delivery_id") and page.get("delivery_id") == row["delivery_id"]
                 and row.get("bundle_digest") and page.get("bundle_digest") == row["bundle_digest"],
                 "output_identity")
            matches = []
            for item in page.get("items", []):
                aliases = [item, *item.get("aliases", [])]
                if any(all(alias.get(k) == row.get(k) for k in
                           ("source_id", "artifact_id", "artifact_sha256", "json_path")) for alias in aliases):
                    matches.append(item)
            _require(len(matches) == 1, "output_source")
            item = matches[0]
            _require(item.get("selected_sha256") == row.get("selected_sha256"), "output_selection")
            identity_anchors += 1
        else:
            # The manual --page-start path has no attempt/delivery nonce in its
            # output. Check it for consistency, but never use it as the anchor.
            _require(set(page) == {"content", "page_start", "page_end", "page_total", "next_page_start"}
                     and not row.get("delivery_id") and not row.get("bundle_digest"), "manual_page_schema")
            item = page
            _require(item["next_page_start"] == (row["page_end"] if row["page_end"] < row["page_total"] else None),
                     "manual_page_cursor")
        _require(all(item.get(k) == row.get(k) for k in
                     ("page_start", "page_end", "page_total")),
                 "output_page")
        _require(isinstance(item.get("content"), str)
                 and all(type(row.get(k)) is int for k in ("page_start", "page_end", "page_total"))
                 and 0 <= row["page_start"] < row["page_end"] <= row["page_total"]
                 and len(item["content"]) == row["page_end"] - row["page_start"]
                 and len(item["content"].encode()) == row.get("returned_bytes"), "output_body")
        observed = load(row.get("provider_observation_ref") or {})
        _require(observed.get("schema_version") == "provider-output-observation.v1",
                 "witness_schema")
        sid = str(observed.get("session_id") or "")
        _require(bool(sid) and str(UUID(sid)) == sid
                 and sid == row.get("provider_session_id")
                 and observed.get("call_id") and observed["call_id"] == row.get("provider_call_id"),
                 "witness_identity")
        _require(datetime.fromisoformat(observed["timestamp"]) >= datetime.fromisoformat(row["read_at"]),
                 "witness_time")
        _require(observed.get("output") in observable_outputs(observed.get("raw_output", observed.get("output")))
                 and complete_output_observed(body, observed["output"]), "witness_body")
        sessions.add(sid)
    _require(identity_anchors > 0, "no_identity_bound_output")
    _require(len(sessions) == 1, "mixed_sessions")
    sid = next(iter(sessions))
    if proof.get("provider_session_id"):
        _require(proof["provider_session_id"] == sid, "observation_session")
    return sid


def prepare_session_observation(
    state: Path, *, event_log: Any, result_identity: Mapping[str, Any],
    ledger_ref: Mapping[str, Any], policy: Mapping[str, Any], source_event_id: str,
) -> dict[str, Any]:
    """Called only at the successful admission edge; failure is diagnostic."""
    identity = {k: str(result_identity.get(k) or "") for k in
                ("workflow_run_id", "operation_id", "request_hash", "attempt_id", "dispatch_id", "task_id")}
    identity["role_instance"] = str(result_identity.get("producer_role") or "")
    try:
        # The optional event scan belongs inside the same non-fatal boundary as
        # witness hydration/writing, after business admission checks have passed.
        started = next((e for e in reversed(event_log.read_all()) if e.type == "workflow.operation.started"
                        and e.payload.get("operation_id") == identity["operation_id"]
                        and e.payload.get("request_hash") == identity["request_hash"]), None)
        if started is None:
            return {}
        start = started.payload
        receipt_ref = start.get("context_delivery_receipt_ref")
        if not isinstance(receipt_ref, dict) or not receipt_ref:
            return {}
        receipt = _load(state, receipt_ref)
        if receipt.get("provider_session_id"):
            return {}
        _require(all(identity[k] and identity[k] == start.get(a) for k, a in (
            ("workflow_run_id", "workflow_run_id"), ("role_instance", "role_instance"),
            ("attempt_id", "active_attempt_id"), ("dispatch_id", "dispatch_id"))), "started_identity")
        proof = {"schema_version": SCHEMA, "identity": identity, "original_receipt_ref": receipt_ref,
                 "read_ledger_ref": dict(ledger_ref), "input_policy": dict(policy),
                 "started_event_id": started.id, "result_event_id": source_event_id,
                 "observation_scope": "historical_tool_output", "current_context_availability": "unknown"}
        proof["provider_session_id"] = _validate(state, proof, receipt_ref)
        descriptor = write_immutable_json_sidecar(
            state, proof, root="context-delivery/session-observations", kind="context_session_observation",
            schema_version=SCHEMA, created_by="call-result-admission", source_event_id=source_event_id,
        )
        return {REF_FIELD: descriptor, "context_delivery_session_observation_status": "observed"}
    except Exception as exc:
        # Optional historical association must not redispatch an admitted result.
        reason = str(exc) if type(exc) is ValueError else getattr(exc, "code", type(exc).__name__)
        return {"context_delivery_session_observation_status": "unavailable",
                "context_delivery_session_observation_reason": reason[:120]}


def observed_session_id(state: Path, *, receipt_ref: Mapping[str, Any],
                        observation_ref: Mapping[str, Any]) -> str:
    """Revalidate immutable evidence; never query a current role/session registry."""
    return _validate(state, _load(state, observation_ref), receipt_ref)
