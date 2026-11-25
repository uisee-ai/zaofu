"""Actual dispatched Reader methods, old WRC previews and scoped autonomous retry."""
from dataclasses import replace

import pytest

from tests.test_candidate_host_consumption import evidence
from tests.test_reader_settled_failure_rebuild import failed_reader
from tests.test_run_manager_reader_rebuild import consume
from zf.runtime import candidate_host_navigation as navigation
from zf.runtime.candidate_reader_method import PROOF, SNAPSHOT, reader_method_change
from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.fixture
def worker(evidence, tmp_path, monkeypatch):
    # Model the preceding release showing the authenticated receipt but omitting its media navigation.
    choose = navigation.host_navigation_entries
    with monkeypatch.context() as previous:
        previous.setattr(navigation, "host_navigation_entries", lambda *a, **kw: [
            {**r, "media_sources": []} for r in choose(*a, **kw)])
        state, log, transport, runtime, scope, failure, op = failed_reader(tmp_path,
            frozen_inputs=evidence[:2], pending_sibling=True, failure_class="verifier_execution_failure")
    dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched" and e.payload["child_id"] == "verify-1")
    return state, log, transport, runtime, scope, failure, op, dispatch


def diagnosis(worker):
    state, log, _, _, scope, failure, _, _ = worker
    return diagnosis_reader_rebuild(state, log.read_all(), {"request_kind":"recovery_diagnosis",
        "workflow_run_id": scope["workflow_run_id"], "source_event_ids":[failure.id]})


def test_resident_retries_only_with_changed_bound_method(worker):
    state, log, transport, runtime, scope, _, op, dispatch = worker
    old = hydrate_sidecar_ref(state, dispatch.payload[SNAPSHOT]).payload
    assert old["entries"] and not old["entries"][0]["media_sources"]
    assert old["binding"]["request_hash"] == op["request_hash"]
    before = len(log.read_all())
    card = diagnosis(worker)
    change = card["method_change"]
    assert change["status"] == "improved" and len(change["added_routes"]) == 1
    assert change["added_routes"][0]["source_id"].startswith("candidate-host-media-")
    assert change["preview_entries"][0]["approval_ref"] == old["entries"][0]["approval_ref"]
    assert len(log.read_all()) == before  # A preview is neither delivery nor authority.
    assert change["failure_event_id"] == worker[5].id
    for method in ("forged", None):
        action = {**card["recommendation"], "action_payload":dict(scope)}
        if method:
            action["action_payload"][PROOF] = method
        runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload=action)
        consume(runtime)
        assert not any(e.type == "repair.action.requested" for e in log.read_all())
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager",
                              payload=card["recommendation"])
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager",
                              payload=card["recommendation"])
    consume(runtime)
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == 1
    sent = len(transport.sent)
    runtime.run_once(events=intents)
    runtime.run_once(events=intents)
    assert len(transport.sent) == sent + 1
    latest = next(e for e in reversed(log.read_all()) if e.type == "fanout.child.dispatched")
    new = hydrate_sidecar_ref(state, latest.payload[SNAPSHOT]).payload
    assert latest.payload["child_id"] == "verify-1"
    assert new["binding"]["request_hash"] != old["binding"]["request_hash"]
    assert new["entries"] == change["preview_entries"]
    assert hydrate_sidecar_ref(state, dispatch.payload[SNAPSHOT]).payload == old


def test_unchanged_method_and_existing_owner_gate_do_not_authorize_retry(worker, monkeypatch):
    _, log, _, runtime, scope, _, _, _ = worker
    choose = navigation.host_navigation_entries
    with monkeypatch.context() as previous:
        previous.setattr(navigation, "host_navigation_entries", lambda *a, **kw:[
            {**row, "media_sources":[]} for row in choose(*a, **kw)])
        card = diagnosis(worker)
        assert card["method_change"]["status"] == "unchanged" and "recommendation" not in card
        runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager",
            payload={"recommended_route":"controlled_action", "action":"child-rebuild", "action_payload":scope})
        consume(runtime)
        assert not any(e.type == "repair.action.requested" for e in log.read_all())
    card = diagnosis(worker)
    runtime.event_writer.emit("human.escalate", payload={"workflow_run_id":scope["workflow_run_id"],
        "blocking_scope":"run", "checkpoint_id":"owner-review", "reason":"await owner"})
    assert "recommendation" not in diagnosis(worker)
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload=card["recommendation"])
    consume(runtime)
    assert not any(e.type == "repair.action.requested" for e in log.read_all())


@pytest.mark.parametrize("fault", ["legacy", "tampered", "target", "access", "missing-media"])
def test_method_comparison_preserves_unknown(worker, fault, monkeypatch):
    state, _, _, _, _, _, op, dispatch = worker
    expected = {"unknown", "unchanged"} if fault == "missing-media" else {"unknown"}
    payload = dict(dispatch.payload)
    if fault == "legacy":
        payload.pop(SNAPSHOT)
    elif fault == "tampered":
        payload[SNAPSHOT] = {**payload[SNAPSHOT], "sha256":"0"*64}
    elif fault == "target":
        payload["payload"] = {**payload["payload"], "target_commit":"other"}
    elif fault == "missing-media":
        source = payload["payload"]
        manifest = hydrate_sidecar_ref(state, source["attempt_source_manifest"]).payload
        media = next(row for row in manifest["sources"] if row["source_id"].startswith("candidate-host-media-"))
        (state/media["ref"]).unlink()
    else:
        monkeypatch.setattr(navigation, "artifact_access_allowed", lambda *a, **k: False)
    assert reader_method_change(state, replace(dispatch, payload=payload), op,
                                failure_event_id=worker[5].id)["status"] in expected


def test_restoring_exact_index_changes_method_without_a_release(evidence, tmp_path, monkeypatch):
    choose = navigation.host_navigation_entries
    saved = {}

    def storage_fault(state, manifest, request, *, role_name):
        if request.get("role_instance") == "verify-1":
            media = next(row for row in manifest["sources"] if row["source_id"].startswith("candidate-host-media-"))
            path = state / media["ref"]
            if path.exists():
                saved.setdefault(path, path.read_bytes())
                path.unlink()
        return choose(state, manifest, request, role_name=role_name)

    with monkeypatch.context() as injection:
        injection.setattr(navigation, "host_navigation_entries", storage_fault)
        state, log, transport, runtime, scope, failure, op = failed_reader(tmp_path, frozen_inputs=evidence[:2],
            pending_sibling=True, failure_class="verifier_execution_failure")
    dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched" and e.payload["child_id"] == "verify-1")
    prior = hydrate_sidecar_ref(state, dispatch.payload[SNAPSHOT]).payload
    assert len(saved) == 1 and prior["entries"] and not prior["entries"][0]["media_sources"]
    for path, contents in saved.items():
        path.write_bytes(contents)
    card = diagnosis((state, log, transport, runtime, scope, failure, op, dispatch))
    assert card["method_change"]["status"] == "improved"
    assert card["method_change"]["binding"]["request_hash"] == op["request_hash"]
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload=card["recommendation"])
    consume(runtime)
    assert len([e for e in log.read_all() if e.type == "repair.action.requested"]) == 1


def test_queued_method_proof_is_rechecked_before_new_dispatch(worker):
    state, log, transport, runtime, _, _, _, dispatch = worker
    card = diagnosis(worker)
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload=card["recommendation"])
    consume(runtime)
    intent = next(e for e in log.read_all() if e.type == "repair.action.requested")
    assert intent.payload[PROOF] == card["method_change"][PROOF]
    manifest = hydrate_sidecar_ref(state, dispatch.payload["payload"]["attempt_source_manifest"]).payload
    media = next(row for row in manifest["sources"]
                 if row["source_id"] == card["method_change"]["added_routes"][0]["source_id"])
    (state / media["ref"]).unlink()
    sent = len(transport.sent)
    runtime.run_once(events=[intent])
    assert len(transport.sent) == sent
    rejection = next(e for e in log.read_all() if e.type == "repair.action.rejected")
    assert "candidate_reader_method_change_unproven" in rejection.payload["reason"]
