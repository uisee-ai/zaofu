from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_control_actions_recovery import _service
from tests.test_plan_rework_draft import rework as rework
from tests.test_plan_synth_recovery_refs import _resume_case
from zf.core.events import ZfEvent
from zf.runtime.plan_rework_signed_rejection import signed_rejection_source


def _kernel(kind, **payload):
    return ZfEvent(
        type=kind,
        origin="kernel",
        actor="zf-cli",
        payload={"workflow_run_id": "run", **payload},
    )


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    service = _service(tmp_path)
    events, _, candidate, control = _resume_case(service.state_dir, monkeypatch)
    dispatch, admission, synth, failure, _ = events
    control.update(execution_status="completed", verdict="rejected", failure_class="")
    origin = _kernel("invoice.scan.finished", stage_id="invoice-scan")
    formal = {
        "task_map_generation": "invoice-version-2",
        "plan_artifact_package_digest": "a" * 64,
    }
    origin.payload.update(formal)
    stage = "prd-plan"
    old_start = _kernel(
        "fanout.started",
        fanout_id="fanout",
        stage_id=stage,
        trigger_event_id=origin.id,
        aggregate={
            "synth_role": "independent-accountant",
            "failure_event": "invoice.plan.failed",
        },
    )
    old_start.causation_id = origin.id
    binding = {
        "request_hash": "f" * 64,
        "role_instance": "independent-accountant",
        "stage_id": stage,
        "workflow_run_id": "run",
    }
    dispatch.origin = "kernel"
    dispatch.actor = "zf-cli"
    dispatch.payload.update(binding)
    synth.payload.update(binding)
    synth.actor = "independent-accountant"
    admission.origin = "kernel"
    admission.actor = "zf-cli"
    admission.payload.update(
        admission_status="admitted",
        semantic_verdict="rejected",
        attempt_id="attempt",
        request_hash=binding["request_hash"],
    )
    failure.origin = "kernel"
    failure.actor = "zf-cli"
    failure.type = "invoice.plan.failed"
    failure.payload.update(
        formal,
        stage_id=stage,
        fanout_id="fanout",
        status="failed",
        previous_plan_candidate_refs=[candidate],
    )
    for e in (origin, old_start, dispatch, admission, synth, failure):
        service.writer.append(e)
    return service, failure, synth, origin, candidate


def _recipient(case, *, owner=False, explicit=True):
    service, failure, synth, origin, candidate = case
    log = service.writer.event_log
    stage = failure.payload["stage_id"]
    if owner:
        escalation = service.writer.append(
            _kernel(
                "human.escalate",
                source_event_id=failure.id,
                failure_class="stage_replan_cap_exhausted",
                allowed_actions=["start_new_generation"],
            )
        )
        body = {
            "workflow_run_id": "run",
            "escalation_event_id": escalation.id,
            "approval_ref": "operator:invoice-review",
        }
        if explicit:
            body["resume_candidate_synth_event_id"] = synth.id
        requested = service.writer.append(
            ZfEvent(
                type="web.action.requested",
                actor="web",
                payload={
                    "action": "stage-replan-new-generation",
                    "source_session_id": "",
                    "request": body,
                },
            )
        )
        config = SimpleNamespace(
            id=stage,
            attempt_domain="invoice-plan",
            topology="fanout_reader",
            trigger=origin.type,
            failure_event="",
            aggregate=SimpleNamespace(failure_event=failure.type),
        )
        service.config = SimpleNamespace(workflow=SimpleNamespace(stages=[config]))
        service.actor = "web"
        result = service.execute(
            action="stage-replan-new-generation",
            requested_action="stage-replan-new-generation",
            payload=body,
            requested=requested,
        )
        assert result["ok"], result
        trigger = next(
            e for e in log.read_all() if e.id == result["stage_trigger_event_id"]
        )
    else:
        trigger = _kernel(
            origin.type,
            **{
                k: origin.payload[k]
                for k in (
                    "stage_id",
                    "task_map_generation",
                    "plan_artifact_package_digest",
                )
            },
            rework_of=failure.id,
            previous_plan_candidate_refs=[candidate],
        )
        trigger.causation_id = failure.id
        service.writer.append(trigger)
    start = _kernel(
        "fanout.started",
        stage_id=stage,
        fanout_id="invoice-next",
        trigger_event_id=trigger.id,
        aggregate={"failure_event": failure.type},
    )
    start.causation_id = trigger.id
    service.writer.append(start)
    events = log.read_all()
    by_id = {e.id: e for e in events}
    return events, dict(
        start=by_id[start.id],
        trigger=by_id[trigger.id],
        scope=deepcopy(trigger.payload),
        request={
            "role_instance": "invoice-author",
            "stage_id": stage,
            "workflow_run_id": "run",
            "fanout_id": "invoice-next",
        },
    )


@pytest.mark.parametrize("owner", [False, True])
def test_rejected_author_inputs_are_bound_to_the_admitted_independent_review(
    review_case, owner
):
    service, failure, synth, _, candidate = review_case
    events, options = _recipient(review_case, owner=owner)
    producer, review, rejection, refs, proof = signed_rejection_source(
        service.state_dir, events, **options
    )
    assert (
        producer.payload["fanout_id"] == "fanout"
        and review.id == synth.id
        and rejection.id == failure.id
    )
    assert refs == [candidate] and refs[0]["allowed_paths"] == ["$.approved"]
    assert proof["critic_admission_event_id"] == "admission"
    assert ("generation_approval_event_id" in proof) == owner


@pytest.mark.parametrize(
    "mutation",
    [
        "unadmitted",
        "wrong_outcome",
        "same_role",
        "other_operation",
        "worker_dispatch",
        "other_baseline",
        "altered_sources",
        "superseded",
        "other_generation",
        "forged_retry",
    ],
)
def test_ambiguous_rejection_or_recipient_cannot_supply_an_edit_base(
    review_case, mutation
):
    service, _, _, _, _ = review_case
    events, options = _recipient(review_case)
    admission = next(e for e in events if e.type == "workflow.call.result.admitted")
    if mutation == "unadmitted":
        admission.origin = "worker"
    elif mutation == "wrong_outcome":
        admission.payload["semantic_verdict"] = "passed"
    elif mutation == "same_role":
        options["request"]["role_instance"] = "independent-accountant"
    elif mutation == "other_operation":
        admission.payload["request_hash"] = "0" * 64
    elif mutation == "worker_dispatch":
        next(e for e in events if e.type == "fanout.synth.dispatched").origin = "worker"
    elif mutation == "other_baseline":
        options["scope"]["plan_artifact_package_digest"] = "b" * 64
    elif mutation == "altered_sources":
        options["scope"]["previous_plan_candidate_refs"][0]["allowed_paths"] = ["$"]
    elif mutation == "superseded":
        events.insert(events.index(options["trigger"]), _kernel("task_map.ready"))
    elif mutation == "other_generation":
        options["scope"]["stage_replan_generation"] = options["trigger"].payload[
            "stage_replan_generation"
        ] = "new"
    elif mutation == "forged_retry":
        options["trigger"].origin = "worker"
    with pytest.raises(ValueError):
        signed_rejection_source(service.state_dir, events, **options)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_checkpoint",
        "agent_request",
        "wrong_escalation",
        "other_receipt",
        "receipt_missing",
    ],
)
def test_owner_generation_requires_the_exact_applied_human_checkpoint(
    review_case, mutation
):
    service, _, _, _, _ = review_case
    events, options = _recipient(
        review_case, owner=True, explicit=(mutation != "no_checkpoint")
    )
    request = next(e for e in events if e.type == "web.action.requested")
    receipt = next(e for e in events if e.type == "runtime.action.completed")
    if mutation == "agent_request":
        request.payload["source_session_id"] = "agent"
    elif mutation == "wrong_escalation":
        request.payload["request"]["escalation_event_id"] = "other"
    elif mutation == "other_receipt":
        receipt.payload["source_failure_event_id"] = "older"
    elif mutation == "receipt_missing":
        events.remove(receipt)
    with pytest.raises(ValueError):
        signed_rejection_source(service.state_dir, events, **options)


def test_mock_signed_rejection_exports_draft_through_stock_cli(
    rework, tmp_path, monkeypatch, capsys
):
    """Use immutable Critic controls and signed operation registration, then the real CLI."""
    import json
    import shutil

    from tests.test_plan_rework_draft import issue
    from zf.cli import main
    from zf.core.events import EventLog, EventWriter
    from zf.core.safety.path_guard import write_workdir_owner_marker
    from zf.runtime.artifact_read_capability import (
        bind_attempt_artifact_read_capability,
        provision_role_artifact_read_credential,
    )
    from zf.runtime.artifact_read_ledger import write_attempt_source_manifest
    from zf.runtime.call_result_envelope import (
        normalize_call_result_envelope,
        write_immutable_json_sidecar,
    )

    source, _, request, manifest, payload, physical, author, child = rework
    state = tmp_path / "signed-state"
    shutil.copytree(source.state_dir / "artifacts", state / "artifacts")
    logger = EventLog(state / "events.jsonl")
    writer = EventWriter(logger, default_origin="kernel")
    prefix = source.event_log.read_all()
    for prior in prefix[: prefix.index(child) + 1]:
        if prior.type == "fanout.started":
            prior.payload["aggregate"] = {"synth_role": "accountant"}
        writer.append(prior)
    run = request["workflow_run_id"]
    stage = request["stage_id"]
    prior_scope = payload["plan_candidate_validation"]["manifest"]["trigger_payload"]
    formal = {
        k: prior_scope[k]
        for k in (
            "stage_replan_generation",
            "task_map_generation",
            "plan_artifact_package_digest",
        )
    }

    def pin(body, schema):
        return write_immutable_json_sidecar(
            state,
            body,
            root="signed-critic",
            schema_version=schema,
            kind=schema,
            created_by="mock-accountant",
        )

    def emit(kind, body, cause=None, *, actor="zf-cli", origin="kernel"):
        return writer.append(
            ZfEvent(
                type=kind,
                actor=actor,
                origin=origin,
                causation_id=cause,
                payload={"workflow_run_id": run, "stage_id": stage, **body},
            )
        )

    refs = deepcopy(prior_scope["previous_plan_candidate_refs"])
    for i, r in enumerate(refs):
        r["source_id"] = (
            "child-invoice-author-control-result"
            if r["kind"] == "call_control_result"
            else f"child-artifact-invoice-author-{i + 1}"
        )
        r["artifact_id"] = f"input-{i + 1}"
    report = {
        **pin(child.payload["report"], "invoice-report.v1"),
        "source_id": "child-result-invoice-author",
        "artifact_id": "report",
        "allowed_paths": ["$"],
    }
    refs.insert(0, report)
    identity = {
        "fanout_id": "invoices-r3",
        "stage_id": stage,
        "workflow_run_id": run,
        "plan_revision": "invoice-v3",
        "operation_id": "signed-check",
        "attempt_id": "check-1",
        "run_id": "check-1",
        "request_hash": "e" * 64,
        "role_instance": "accountant",
    }
    bindings = [
        {k: r[k] for k in ("source_id", "artifact_id", "ref", "sha256")} for r in refs
    ]
    contract = pin(
        {
            **identity,
            "schema_version": "plan-synth-contract.v1",
            "child_bindings": [{"child_id": "invoice-author", **bindings[0]}],
            "source_bindings": bindings,
        },
        "plan-synth-contract.v1",
    )
    identity.update(
        plan_synth_contract_ref=contract["ref"],
        plan_synth_contract_digest=contract["sha256"],
        reviewed_plan_candidate_digest=contract["sha256"],
    )
    review_inputs = pin(
        {
            "schema_version": "attempt-source-manifest.v1",
            "sources": refs,
            "workflow_run_id": run,
            "dispatch_id": "check-1",
            "attempt_id": "check-1",
        },
        "attempt-source-manifest.v1",
    )
    dispatch = emit(
        "fanout.synth.dispatched",
        {**identity, "attempt_source_manifest": review_inputs},
    )
    control = pin(
        {**identity, "execution_status": "completed", "verdict": "rejected"},
        "plan-synthesis-result.v1",
    )
    result = emit(
        "fanout.synth.completed",
        {**identity, "control_result_ref": control},
        dispatch.id,
        actor="accountant",
        origin="worker",
    )
    envelope = normalize_call_result_envelope(
        source_payload=identity,
        control_result=control,
        execution_status="completed",
        workflow_run_id=run,
        operation_id=identity["operation_id"],
        request_hash=identity["request_hash"],
        source_event_id=result.id,
        source_event_type=result.type,
        actor="accountant",
    )
    accept = emit(
        "workflow.call.result.admitted",
        {
            **identity,
            "source_event_id": result.id,
            "admission_status": "admitted",
            "semantic_verdict": "rejected",
            "control_result_ref": control,
            "envelope_ref": pin(envelope, envelope["schema_version"]),
        },
    )
    fail = emit(
        "invoice.plan.failed",
        {
            **formal,
            "fanout_id": "invoices-r3",
            "status": "failed",
            "previous_plan_candidate_refs": refs,
        },
        result.id,
    )
    retry = emit(
        "invoice.sources.ready",
        {**formal, "rework_of": fail.id, "previous_plan_candidate_refs": refs},
        fail.id,
    )
    start = emit(
        "fanout.started",
        {
            "fanout_id": request["fanout_id"],
            "trigger_event_id": retry.id,
            "aggregate": {"failure_event": fail.type},
            "expected_children": [
                {"child_id": request["child_id"], "role_instance": "planner"}
            ],
        },
        retry.id,
    )
    manifest.update(
        schema_version="attempt-source-manifest.v1",
        task_id="invoice-draft",
        dispatch_id=manifest["attempt_id"],
        source_event_id=start.id,
        sources=[
            {**r, "source_id": f"previous-plan-candidate-{i}"}
            for i, r in enumerate(refs, 1)
        ],
    )
    payload = {
        "plan_candidate_validation": {"manifest": {"trigger_payload": retry.payload}}
    }
    issued = issue(
        (
            SimpleNamespace(state_dir=state, event_log=logger),
            writer,
            request,
            manifest,
            payload,
        )
    )
    assert "ref" in issued, issued
    write_attempt_source_manifest(state, manifest)
    role = "planner"
    token = provision_role_artifact_read_credential(
        state, role, role_name=role, provider="codex"
    )
    bind_attempt_artifact_read_capability(
        state,
        operation_id="op-b",
        attempt_id=manifest["attempt_id"],
        role_instance=role,
        manifest=manifest,
    )
    monkeypatch.delenv("ZF_ARTIFACT_READ_TOKEN", raising=False)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", role)
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token))
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject: {name: invoices, state_dir: signed-state}\n'
        "roles: [{name: planner, backend: mock}]\nruntime: {workdirs: {enabled: true, root: signed-state/roles, mode: dry-run}}\n"
    )
    role_dir = state / "roles" / role
    write_workdir_owner_marker(
        role_dir, project_name="invoices", instance_id=role, project_root=tmp_path
    )
    workdir = role_dir / "project"
    workdir.mkdir()
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(workdir)
    assert (
        main(
            [
                "plan",
                "--state-dir",
                str(state),
                "draft-baseline",
                "--workspace",
                "--from-rework",
                "--attempt",
                manifest["attempt_id"],
                "--port",
                "task_map",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["editable_candidate"]["critic_admission_event_id"] == accept.id
    assert summary["editable_candidate"]["producer_admission_event_id"] == author.id
    draft = workdir / summary["draft_dir"]
    assert (draft / "task_map.json").read_bytes() == physical["task_map"][1]
    assert (draft / "plan.md").read_bytes() == physical["plan.md"][1]
