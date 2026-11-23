"""Late native identity must come from exact admitted output, never role latest."""
import hashlib
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_call_result_admission import _persisted_verification_payload, _runtime
from zf.cli.artifact_required_read import prepare_required_read
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import emit_artifact_output
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
from zf.runtime.context_delivery import (
    attach_context_sections, build_context_delivery_envelope, build_execution_binding,
    latest_delivery_receipt, write_context_delivery_receipt,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def fixture(state, source_body='{"requirement":"complete original source"}', *, text_format=False):
    admission, operations = _runtime(state)
    source = state / "artifacts/input.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(source_body)
    required = [{"source_id": "workflow-input", "artifact_id": "input", "json_path": "$",
                 "artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                 "allow_truncated": False}]
    manifest = attach_context_sections(build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id="attempt-1", dispatch_id="dispatch-1",
        sources=[{"source_id": "workflow-input", "artifact_id": "input",
                  "ref": "artifacts/input.json", "sha256": required[0]["artifact_sha256"]}],
    ), output_profile_id="task-verify", explicit_required_reads=required)
    manifest_ref = write_attempt_source_manifest(state, manifest)
    binding = build_execution_binding(source_manifest=manifest, role_instance="verify-1", provider_backend="codex")
    delivery, delivery_ref = build_context_delivery_envelope(
        state, source_manifest=manifest, source_manifest_descriptor=manifest_ref,
        workflow_run_id="run-1", operation_id="op-1", attempt_id="attempt-1",
        dispatch_id="dispatch-1", role_instance="verify-1", provider_session_id="",
        execution_binding=binding,
    )
    receipt = write_context_delivery_receipt(state, envelope=delivery, envelope_descriptor=delivery_ref)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1", operation_id="op-1", operation_type="agent",
        request={"prompt": "verify"}, task_id="T1",
    )
    operations.mark_started(workflow_run_id="run-1", operation_id="op-1",
        request_hash=ensured.request_hash, task_id="T1", role_instance="verify-1",
        active_attempt_id="attempt-1", dispatch_id="dispatch-1",
        context_delivery_receipt_ref=receipt, context_delivery_envelope_ref=delivery_ref)
    output, rows = prepare_required_read(state, manifest=manifest, rows=required,
        identity={"actor": "verify-1", "role": "verify", "provider": "codex",
                  "purpose": "task-verify", "operation_id": "op-1"})
    if text_format:
        from zf.runtime.artifact_text_page import render_text_page

        output = render_text_page(json.loads(output))
    emit_artifact_output(state, output, rows, io.StringIO())
    sid = str(uuid4())
    transcript = state / "native.jsonl"
    registry = RoleSessionRegistry(state / "role_sessions.yaml", state.parent)
    registry.bind_codex_session("verify-1", sid, session_path=transcript)
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": sid}},
        {"type": "response_item", "timestamp": "2099-01-01T00:00:00+00:00",
         "payload": {"type": "custom_tool_call_output", "call_id": "call-1", "output": output}},
    ]))
    payload = _persisted_verification_payload(state)
    payload["dispatch_id"] = "dispatch-1"
    event = ZfEvent(type="verify.child.completed", actor="verify-1", task_id="T1", payload=payload)
    return SimpleNamespace(state=state, admission=admission, operations=operations,
        manifest=manifest, manifest_ref=manifest_ref, binding=binding, receipt=receipt,
        delivery_ref=delivery_ref, sid=sid, registry=registry, transcript=transcript, event=event,
        policy={"attempt_id": "attempt-1", "required_reads": required},
        operation={"workflow_run_id": "run-1", "operation_id": "op-1", "request_hash": ensured.request_hash})


def admit(f):
    return f.admission.report_legacy_result(f.event, mode="blocking",
        operation=f.operation, input_policy=f.policy)


def next_delivery(f, sid=None):
    previous = latest_delivery_receipt(f.operations.event_log.read_all(),
        workflow_run_id="run-1", role_instance="verify-1")
    return build_context_delivery_envelope(f.state, source_manifest=f.manifest,
        source_manifest_descriptor=f.manifest_ref, workflow_run_id="run-1",
        operation_id="op-2", attempt_id="attempt-2", dispatch_id="dispatch-2",
        role_instance="verify-1", provider_session_id=sid or f.sid,
        execution_binding=f.binding, previous_receipt_descriptor=previous)[0]


@pytest.mark.parametrize("text_format", [False, True])
def test_real_admission_binds_late_session_without_changing_original_receipt(tmp_path, text_format):
    f = fixture(tmp_path, text_format=text_format)
    before = (tmp_path / f.receipt["ref"]).read_bytes()
    result = admit(f)
    assert result.admitted, result.issues
    admitted = next(e for e in f.operations.event_log.read_all()
                    if e.type == "workflow.call.result.admitted")
    assert admitted.payload.get("context_delivery_session_observation_ref")
    assert (tmp_path / f.receipt["ref"]).read_bytes() == before
    # Rotation of the registry cannot rewrite sealed history.
    f.registry.clear("verify-1")
    envelope = next_delivery(f)
    assert envelope["previous_state"] == "known"
    assert {s["delivery"] for s in envelope["sections"]} == {"full"}
    assert {s["shadow_delivery"] for s in envelope["sections"]} == {"unchanged"}
    assert next_delivery(f, str(uuid4()))["previous_state"] == "incompatible"
    assert admit(f).admitted  # Replay does not append another admission.
    assert sum(e.type == "workflow.call.result.admitted" for e in f.operations.event_log.read_all()) == 1


def sealed_rows(f):
    from zf.runtime.artifact_read_ledger import seal_read_ledger
    ref = seal_read_ledger(f.state, "attempt-1")
    return [json.loads(line) for line in hydrate_sidecar_ref(f.state, ref).payload.splitlines()]


def replace_ledger(f, rows):
    from zf.runtime.sidecar_refs import build_sidecar_ref_descriptor
    raw = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    path = f.state / "artifacts/fixture-ledger.jsonl"
    path.write_bytes(raw)
    f.event.payload["read_ledger_ref"] = build_sidecar_ref_descriptor(
        kind="artifact_read_ledger", ref="artifacts/fixture-ledger.jsonl",
        sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw),
        content_type="application/x-ndjson", schema_version="artifact-read-ledger.v1",
        created_by="test",
    )


def write_json(f, body, kind="test-evidence"):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    return write_immutable_json_sidecar(f.state, body, root="fixture-evidence",
        kind=kind, schema_version=body["schema_version"], created_by="test")


@pytest.mark.parametrize("fault", [
    "operation", "attempt", "dispatch", "role", "run", "task", "occurrence", "unknown",
    "missing_witness", "corrupt_witness", "witness_flag", "empty_session", "wrong_session",
    "mixed_sessions", "witness_call", "witness_time", "witness_partial",
    "output_attempt", "output_delivery", "output_bundle", "output_source", "output_partial",
])
def test_bad_or_ambiguous_evidence_never_binds_a_native_session(tmp_path, fault):
    f = fixture(tmp_path)
    rows = sealed_rows(f)
    row = rows[0]
    fields = {"operation": "operation_id", "attempt": "attempt_id", "dispatch": "dispatch_id",
              "role": "consumer_actor", "run": "workflow_run_id", "task": "task_id",
              "occurrence": "occurrence_id"}
    if fault in fields:
        row[fields[fault]] = "wrong"
    elif fault == "unknown":
        row["provider_delivery_status"] = "unknown"
    elif fault == "missing_witness":
        row.pop("provider_observation_ref")
    elif fault == "corrupt_witness":
        row["provider_observation_ref"]["sha256"] = "0" * 64
    elif fault.startswith("output_"):
        output = hydrate_sidecar_ref(tmp_path, row["output_ref"]).payload
        page = json.loads(output["body"])
        if fault == "output_attempt": page["attempt_id"] = "wrong"
        elif fault == "output_delivery": page["delivery_id"] = "wrong"
        elif fault == "output_bundle": page["bundle_digest"] = "wrong"
        elif fault == "output_source": page["items"][0]["source_id"] = "wrong"
        elif fault == "output_partial": page["items"][0]["content"] = "cut"
        output["body"] = json.dumps(page)
        row["output_ref"] = write_json(f, output)
        row["output_sha256"] = hashlib.sha256(output["body"].encode()).hexdigest()
    else:
        observation = hydrate_sidecar_ref(tmp_path, row["provider_observation_ref"]).payload
        if fault == "witness_flag": observation["schema_version"] = "fake"
        elif fault == "empty_session": observation["session_id"] = row["provider_session_id"] = ""
        elif fault == "wrong_session": observation["session_id"] = str(uuid4())
        elif fault == "witness_call": observation["call_id"] = "wrong"
        elif fault == "witness_time": observation["timestamp"] = "2000-01-01T00:00:00+00:00"
        elif fault == "witness_partial":
            observation["output"] = observation["raw_output"] = "{}"
        elif fault == "mixed_sessions":
            row = dict(row)
            rows.append(row)
            observation["session_id"] = row["provider_session_id"] = str(uuid4())
        row["provider_observation_ref"] = write_json(f, observation)
    replace_ledger(f, rows)
    outcome = admit(f)
    events = f.operations.event_log.read_all()
    admissions = [e for e in events if e.type == "workflow.call.result.admitted"]
    if fault == "attempt":
        assert not outcome.admitted  # Existing input gate still rejects old attempt rows.
        assert not admissions
    else:
        assert outcome.admitted, outcome.issues
        assert admissions[0].payload["context_delivery_session_observation_status"] == "unavailable"
        assert not admissions[0].payload.get("context_delivery_session_observation_ref")
    assert next_delivery(f)["previous_state"] == "incompatible"


def add_manual_page(f, *, observed=True):
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    result = read_attempt_artifact(f.state, manifest=f.manifest,
        source_id="workflow-input", artifact_id="input", page_start=0, page_size=8192,
        actor="verify-1", role="verify", provider="codex", purpose="task-verify", record=False)
    row = result["read"]
    row["operation_id"] = "op-1"
    page = {"content": result["content"], **{k: row[k] for k in ("page_start", "page_end", "page_total")},
            "next_page_start": None}
    body = json.dumps(page, ensure_ascii=False)
    emit_artifact_output(f.state, body, [row], io.StringIO())
    if observed:
        # Same shape produced by the actual manual-page CLI, without pretending
        # that an attempt or delivery nonce exists in this output.
        with f.transcript.open("a") as stream:
            stream.write("\n" + json.dumps({"type": "response_item", "timestamp": "2099-01-01T00:00:00+00:00",
                "payload": {"type": "custom_tool_call_output", "call_id": "manual-call", "output": body}}))


@pytest.mark.parametrize("variant", ["mixed", "manual_only", "manual_unknown", "manual_other_session"])
def test_real_manual_page_shape_needs_strict_v2_anchor_and_one_observed_session(tmp_path, variant):
    from zf.runtime.artifact_read_ledger import active_read_ledger_path
    f = fixture(tmp_path)
    if variant == "manual_only":
        active_read_ledger_path(tmp_path, "attempt-1").write_text("")
    add_manual_page(f, observed=variant != "manual_unknown")
    if variant == "manual_other_session":
        rows = sealed_rows(f)
        row = rows[-1]
        observation = hydrate_sidecar_ref(tmp_path, row["provider_observation_ref"]).payload
        observation["session_id"] = row["provider_session_id"] = str(uuid4())
        row["provider_observation_ref"] = write_json(f, observation)
        replace_ledger(f, rows)
    assert admit(f).admitted
    admitted = next(e for e in f.operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    if variant == "mixed":
        assert admitted.payload.get("context_delivery_session_observation_ref")
        assert next_delivery(f)["previous_state"] == "known"
    else:
        assert not admitted.payload.get("context_delivery_session_observation_ref")
        assert next_delivery(f)["previous_state"] == "incompatible"



def add_raw_read(f, *, observed=True, body_override=None):
    from zf.runtime.artifact_read_ledger import read_attempt_artifact

    result = read_attempt_artifact(f.state, manifest=f.manifest,
        source_id="workflow-input", artifact_id="input",
        actor="verify-1", role="verify", provider="codex", purpose="task-verify", record=False)
    row = result["read"]
    row["operation_id"] = "op-1"
    # The non-paged CLI emits selected text directly, without a page envelope.
    body = result["content"] if body_override is None else body_override
    row["returned_bytes"] = len(body.encode())
    emit_artifact_output(f.state, body, [row], io.StringIO())
    if observed:
        with f.transcript.open("a") as stream:
            stream.write("\n" + json.dumps({"type": "response_item", "timestamp": "2099-01-01T00:00:00+00:00",
                "payload": {"type": "custom_tool_call_output", "call_id": "raw-call", "output": body}}))


@pytest.mark.parametrize("source_body", [
    '{"schema_version":"workflow.input_manifest.v1","requirement":"完整输入"}',
    '"JSON string selected as raw text"',
    '# Original requirement\n完整 Markdown 输入\n',
])
def test_real_admission_accepts_full_raw_output_alongside_v2_and_manual_pages(tmp_path, source_body):
    f = fixture(tmp_path, source_body)
    original = (tmp_path / f.receipt["ref"]).read_bytes()
    add_manual_page(f)
    add_raw_read(f)
    assert admit(f).admitted
    admitted = next(e for e in f.operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    assert admitted.payload.get("context_delivery_session_observation_ref"), admitted.payload
    proof = hydrate_sidecar_ref(tmp_path, admitted.payload["context_delivery_session_observation_ref"]).payload
    assert proof["current_context_availability"] == "unknown"
    assert (tmp_path / f.receipt["ref"]).read_bytes() == original
    envelope = next_delivery(f)
    assert envelope["previous_state"] == "known"
    assert {s["delivery"] for s in envelope["sections"]} == {"full"}
    assert {s["shadow_delivery"] for s in envelope["sections"]} == {"unchanged"}
    assert admit(f).admitted
    assert sum(e.type == "workflow.call.result.admitted" for e in f.operations.event_log.read_all()) == 1


@pytest.mark.parametrize("fault", [
    "raw_only", "unknown", "different_session", "wrong_body", "output_digest",
    "witness_partial", "source_digest", "partial_page_metadata", "limited_read",
])
def test_raw_output_never_weakens_anchor_source_or_observation_checks(tmp_path, fault):
    from zf.runtime.artifact_read_ledger import active_read_ledger_path

    f = fixture(tmp_path, '{"schema_version":"required-artifact-page.v2","attempt_id":"attempt-1"}'
                if fault == "raw_only" else '{"requirement":"complete original source"}')
    if fault == "raw_only":
        active_read_ledger_path(tmp_path, "attempt-1").write_text("")
    add_raw_read(f, observed=fault != "unknown",
                 body_override='{"requirement":"different source"}' if fault == "wrong_body" else None)
    if fault not in {"raw_only", "unknown", "wrong_body"}:
        rows = sealed_rows(f)
        row = rows[-1]
        if fault in {"different_session", "witness_partial"}:
            witness = hydrate_sidecar_ref(tmp_path, row["provider_observation_ref"]).payload
            if fault == "different_session":
                witness["session_id"] = row["provider_session_id"] = str(uuid4())
            else:
                witness["output"] = witness["raw_output"] = "incomplete"
            row["provider_observation_ref"] = write_json(f, witness)
        elif fault == "output_digest":
            row["output_sha256"] = "0" * 64
        elif fault == "source_digest":
            row["artifact_sha256"] = "0" * 64
        elif fault == "partial_page_metadata":
            row["page_end"] = row["returned_bytes"]
        else:
            row["max_chars"] = 1
        replace_ledger(f, rows)
    assert admit(f).admitted  # Optional association cannot invalidate business admission.
    admitted = next(e for e in f.operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    assert admitted.payload["context_delivery_session_observation_status"] == "unavailable"
    assert not admitted.payload.get("context_delivery_session_observation_ref")
    assert next_delivery(f)["previous_state"] == "incompatible"


@pytest.mark.parametrize("fault", ["missing", "broken_digest", "other_receipt", "other_identity", "other_session"])
def test_consumer_rechecks_observation_and_does_not_use_stale_known_history(tmp_path, fault):
    f = fixture(tmp_path)
    assert admit(f).admitted
    previous = latest_delivery_receipt(f.operations.event_log.read_all(),
        workflow_run_id="run-1", role_instance="verify-1")
    ref = previous["session_observation_ref"]
    proof = hydrate_sidecar_ref(tmp_path, ref).payload
    if fault == "missing":
        (tmp_path / ref["ref"]).unlink()
    elif fault == "broken_digest":
        ref["sha256"] = "0" * 64
    else:
        if fault == "other_receipt": proof["original_receipt_ref"] = f.delivery_ref
        elif fault == "other_identity": proof["identity"]["dispatch_id"] = "other"
        elif fault == "other_session": proof["provider_session_id"] = str(uuid4())
        previous["session_observation_ref"] = write_json(f, proof)
    envelope, _ = build_context_delivery_envelope(
        tmp_path, source_manifest=f.manifest, source_manifest_descriptor=f.manifest_ref,
        workflow_run_id="run-1", operation_id="op-2", attempt_id="attempt-2",
        dispatch_id="dispatch-2", role_instance="verify-1", provider_session_id=f.sid,
        execution_binding=f.binding, previous_receipt_descriptor=previous)
    assert envelope["previous_state"] == "unknown"
    assert {s["delivery"] for s in envelope["sections"]} == {"full"}


@pytest.mark.parametrize("new_receipt", [None, {}, "malformed"])
def test_latest_unknown_operation_never_borrows_old_success(tmp_path, new_receipt):
    f = fixture(tmp_path)
    assert admit(f).admitted
    f.operations.event_writer.append(ZfEvent(type="workflow.operation.started", payload={
        "workflow_run_id": "run-1", "operation_id": "op-new", "request_hash": "hash-new",
        "role_instance": "verify-1", "active_attempt_id": "attempt-new",
        "dispatch_id": "dispatch-new", "context_delivery_receipt_ref": new_receipt,
    }))
    assert latest_delivery_receipt(f.operations.event_log.read_all(),
        workflow_run_id="run-1", role_instance="verify-1") == {}
    assert next_delivery(f)["previous_state"] == "absent"


def test_late_old_admission_cannot_override_new_operation(tmp_path):
    f = fixture(tmp_path)
    assert admit(f).admitted
    old = next(e for e in f.operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    f.operations.event_writer.append(ZfEvent(type="workflow.operation.started", payload={
        "workflow_run_id": "run-1", "operation_id": "op-new", "request_hash": "hash-new",
        "role_instance": "verify-1", "context_delivery_receipt_ref": f.receipt,
    }))
    f.operations.event_writer.append(ZfEvent(type=old.type, payload=old.payload))
    assert "session_observation_ref" not in latest_delivery_receipt(
        f.operations.event_log.read_all(), workflow_run_id="run-1", role_instance="verify-1")
    assert next_delivery(f)["previous_state"] == "incompatible"


def test_observation_write_failure_is_diagnostic_and_result_replay_stays_idempotent(tmp_path, monkeypatch):
    from zf.runtime import context_session_observation
    f = fixture(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("fixture no space")
    monkeypatch.setattr(context_session_observation, "write_immutable_json_sidecar", fail)
    assert admit(f).admitted
    events = f.operations.event_log.read_all()
    event = next(e for e in events if e.type == "workflow.call.result.admitted")
    assert event.payload["context_delivery_session_observation_reason"] == "OSError"
    assert not event.payload.get("context_delivery_session_observation_ref")
    assert admit(f).admitted
    assert sum(e.type == "workflow.call.result.admitted" for e in f.operations.event_log.read_all()) == 1
    assert next_delivery(f)["previous_state"] == "incompatible"


def test_existing_explicit_session_is_never_overwritten_by_observation(tmp_path):
    f = fixture(tmp_path)
    # A receipt which explicitly bound S2 is not an empty-ID candidate.
    envelope = hydrate_sidecar_ref(tmp_path, f.delivery_ref).payload
    envelope["provider_session_id"] = str(uuid4())
    replacement = write_json(f, envelope)
    receipt = write_context_delivery_receipt(tmp_path, envelope=envelope, envelope_descriptor=replacement)
    f.operations.event_writer.append(ZfEvent(type="workflow.operation.started", payload={
        **f.operation, "role_instance": "verify-1", "active_attempt_id": "attempt-1",
        "dispatch_id": "dispatch-1", "context_delivery_receipt_ref": receipt,
    }))
    assert admit(f).admitted
    event = next(e for e in f.operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    assert not event.payload.get("context_delivery_session_observation_ref")
    assert next_delivery(f)["previous_state"] == "incompatible"


def test_admitted_observation_cannot_bridge_changed_execution_binding(tmp_path):
    f = fixture(tmp_path)
    assert admit(f).admitted
    f.binding = build_execution_binding(
        source_manifest={**f.manifest, "target_commit": "different-target"},
        role_instance="verify-1", provider_backend="codex")
    assert next_delivery(f)["previous_state"] == "incompatible"


def test_invalid_business_result_does_not_publish_observation(tmp_path):
    f = fixture(tmp_path)
    f.event.payload["verification_result"]["task_id"] = "wrong-task"
    assert not admit(f).admitted
    assert not list((tmp_path / "artifacts/context-delivery/session-observations").glob("*.json"))
    assert not any(e.type == "workflow.call.result.admitted" for e in f.operations.event_log.read_all())


def test_optional_event_scan_failure_does_not_block_valid_admission(tmp_path, monkeypatch):
    import inspect
    f = fixture(tmp_path)
    original = f.admission.event_log.read_all

    def fail_only_optional_scan(*args, **kwargs):
        frame = inspect.currentframe().f_back
        while frame:
            if (frame.f_code.co_name == "report_legacy_result"
                    and frame.f_locals.get("session_observation") == {}):
                raise OSError("fixture optional event scan unavailable")
            frame = frame.f_back
        return original(*args, **kwargs)

    monkeypatch.setattr(f.admission.event_log, "read_all", fail_only_optional_scan)
    assert admit(f).admitted
    admissions = [e for e in original() if e.type == "workflow.call.result.admitted"]
    assert len(admissions) == 1
    assert admissions[0].payload["context_delivery_session_observation_reason"] == "OSError"
    assert not admissions[0].payload.get("context_delivery_session_observation_ref")
    assert admit(f).admitted
    assert len([e for e in original() if e.type == "workflow.call.result.admitted"]) == 1
