from dataclasses import replace

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state, _manifest
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.reader_respawn_continuation import (
    reader_dispatch_call_identity,
    transport_retry_input_attempt,
)


@pytest.mark.parametrize("replace_provider", [True, False])
def test_transport_retry_uses_its_original_artifact_attempt(tmp_path, monkeypatch, replace_provider):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path, semantic_submit=True)
    original = next(e for e in log.read_all() if e.type == "fanout.child.dispatched")
    original_call = reader_dispatch_call_identity(original.payload)
    attempt = original_call["attempt_id"]
    assert "The new transport dispatch" not in transport.sent[0][2]
    child["payload"]["semantic_result_submit_mode"] = "blocking"
    monkeypatch.setattr(runtime, "_ensure_fanout_role_dispatchable", lambda **kwargs: True)
    role = config.roles[0]
    if replace_provider:
        role.backend = "codex"
    decision = ZfEvent(type="repair.action.approved", actor="zf-cli", payload={})
    assert runtime._retry_fanout_child(
        manifest=_manifest(state, original.payload["fanout_id"]), child=child,
        previous_dispatch=original, attempt=3, provider_session_replaced=replace_provider,
        recovery_decision_event=decision,
    )

    retry = [e for e in log.read_all() if e.type == "fanout.child.dispatched"][-1]
    new_dispatch = retry.payload["run_id"]
    assert new_dispatch != attempt
    assert original_call == reader_dispatch_call_identity(retry.payload)
    assert retry.payload["retry_of_run_id"] == original.payload["run_id"]
    prompt = transport.sent[-1][2]
    assert f"The new transport dispatch is `{new_dispatch}`" in prompt
    assert f"artifact recover --attempt {attempt}`" in prompt
    assert f"artifact recover --attempt {new_dispatch}" not in prompt
    assert ("--required --resume" in prompt) == replace_provider
    assert load_attempt_source_manifest(state, attempt)["attempt_id"] == attempt
    with pytest.raises(ValueError, match="attempt source manifest not found"):
        load_attempt_source_manifest(state, new_dispatch)


@pytest.mark.parametrize("fault", ["operation", "request", "attempt", "manifest", "changed_bytes", "origin"])
def test_retry_guidance_never_names_an_unbound_input(tmp_path, fault):
    state, log, _, runtime, _, _ = _durable_reader_state(tmp_path)
    original = next(e for e in log.read_all() if e.type == "fanout.child.dispatched")
    payload = dict(original.payload)
    nested = dict(payload["payload"])
    payload["payload"] = nested
    operation = reader_dispatch_call_identity(payload)["operation_id"]
    if fault in {"operation", "request", "attempt"}:
        nested[{"operation": "operation_id", "request": "request_hash", "attempt": "attempt_id"}[fault]] = "other"
    elif fault == "manifest":
        descriptor = dict(nested["attempt_source_manifest"], sha256="f" * 64)
        payload["attempt_source_manifest"] = nested["attempt_source_manifest"] = descriptor
    elif fault == "changed_bytes":
        path = state / nested["attempt_source_manifest"]["ref"]
        path.write_bytes(path.read_bytes() + b" ")
    dispatch = replace(original, payload=payload, origin="agent" if fault == "origin" else original.origin)
    assert not transport_retry_input_attempt(
        runtime, dispatch, operation_id=operation, role=payload["role_instance"],
    )
