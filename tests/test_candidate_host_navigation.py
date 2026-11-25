"""Reader-visible routes must come from the active request, without consuming its evidence."""

from copy import deepcopy
import json

import pytest

from tests.test_candidate_host_consumption import evidence, prepare
from zf.runtime.artifact_read_ledger import render_attempt_source_briefing
from zf.runtime.candidate_host_navigation import render_host_navigation
from zf.runtime.reader_submission_guidance import verifier_failure_schema_guidance
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_new_consumers_receive_their_own_bound_read_path(evidence):
    runtime, seeded, source_op, _, receipt_ref, completed = evidence
    original = hydrate_sidecar_ref(runtime.state_dir, receipt_ref).payload
    old = None
    for role in ("consumer-one", "consumer-two"):
        prepared, payload = prepare(runtime, seeded, role)
        before = list(runtime.event_log.read_all())
        section = render_attempt_source_briefing(payload, state_dir=runtime.state_dir, role_name=role)
        for expected in (
            original["command_id"],
            original["command"],
            original["approval_ref"],
            original["execution_origin"],
            "receipt_id=" + completed.id,
            "--source-id candidate-host-original-receipt-" + completed.id,
            "candidate-host-media-" + completed.id,
        ):
            assert expected in section
        assert prepared.attempt_id in section and (old is None or old not in section)
        assert not any(
            e.type in {"artifact.input.observed", "workflow.call.result.admitted"}
            for e in runtime.event_log.read_all()[len(before) :]
        )
        old = prepared.attempt_id
    guide = verifier_failure_schema_guidance(owner="candidate_verify")
    assert guide.index("candidate-host-execution") < guide.index("Runner unavailable before evaluation")
    assert "obstruction: first match" not in verifier_failure_schema_guidance(owner="task_verify")


@pytest.mark.parametrize(
    "fault",
    ["operation", "role", "attempt", "target", "index-access", "receipt-access", "partial", "tampered"],
)
def test_mismatched_or_unauthorized_receipts_are_not_exposed(evidence, fault):
    runtime, seeded, _, _, _, completed = evidence
    _, payload = prepare(runtime, seeded, "consumer")
    state = runtime.state_dir
    mf = hydrate_sidecar_ref(
        state,
        {
            "ref": payload["attempt_source_manifest_ref"],
            "sha256": payload["attempt_source_manifest_digest"],
        },
    ).payload
    mf = deepcopy(mf)
    payload = deepcopy(payload)
    if fault in {"operation", "role", "attempt", "target"}:
        key = {
            "operation": "operation_id",
            "role": "role_instance",
            "attempt": "attempt_id",
            "target": "target_commit",
        }[fault]
        payload[key] = "mismatch"
    else:
        sid = (
            "candidate-host-execution"
            if fault == "index-access"
            else f"candidate-host-original-receipt-{completed.id}"
        )
        source = next(s for s in mf["sources"] if s["source_id"] == sid)
        if fault == "tampered":
            source["sha256"] = "f" * 64
        elif fault == "partial":
            source["allowed_paths"] = ["$.status"]
        else:
            source["access_scope"] = {"roles": ["other"]}
    assert "receipt_id=" + completed.id not in render_host_navigation(
        state, mf, payload, role_name="consumer"
    )


def test_new_attempt_for_same_reader_has_fresh_locator_and_preserves_original_authorization(evidence):
    from zf.runtime.call_result_runtime import prepare_call_operation

    runtime, seeded, _, _, receipt, completed = evidence
    first, _ = prepare(runtime, seeded, "consumer")
    retry_payload = {
        "workflow_run_id": seeded["run_id"],
        "task_id": "FLOW-ANCHOR",
        "fanout_id": "fanout-candidate",
        "child_id": "consumer",
        "role_instance": "consumer",
        "stage_id": "issue-lanes-verify",
        "output_profile_id": "candidate-verify",
        "artifact_package_mode": "shadow",
        "target_commit": seeded["candidate_head"],
        "canonical_success_event": "test.passed",
        "canonical_failure_event": "test.failed",
        "retry_of_operation_id": first.operation_id,
        "parent_operation_id": first.operation_id,
    }
    op = prepare_call_operation(
        runtime,
        payload=retry_payload,
        operation_type="fanout_reader_child",
        operation_key="consumer-2",
        stage_id="issue-lanes-verify",
        task_id="FLOW-ANCHOR",
        dispatch_id="attempt-new",
        causation_id="evt-candidate-ready",
    )
    section = render_attempt_source_briefing(
        retry_payload, state_dir=runtime.state_dir, role_name="consumer"
    )
    wanted = hydrate_sidecar_ref(runtime.state_dir, receipt).payload
    assert (first.operation_id, first.attempt_id) != (op.operation_id, op.attempt_id)
    assert "receipt_id=" + completed.id in section and "approval_ref=" + wanted["approval_ref"] in section
    assert (
        "--attempt attempt-new --source-id" in section
        and "--attempt " + first.attempt_id + " " not in section
    )


def test_mounted_resume_keeps_recoverable_command_and_receipt_locators(evidence):
    from zf.runtime.artifact_workdir_view import AttemptFiles
    from zf.runtime.artifact_input_index import input_index

    runtime, seeded, _, _, receipt, completed = evidence
    op, payload = prepare(runtime, seeded, "consumer")
    mf = hydrate_sidecar_ref(
        runtime.state_dir,
        {
            "ref": payload["attempt_source_manifest_ref"],
            "sha256": payload["attempt_source_manifest_digest"],
        },
    ).payload
    identity = {
        "actor": "consumer",
        "role": "consumer",
        "purpose": "candidate-verify",
        "operation_id": op.operation_id,
        "attempt_id": op.attempt_id,
        "workflow_run_id": seeded["run_id"],
        "task_id": "FLOW-ANCHOR",
    }
    view = AttemptFiles(runtime.state_dir, mf, identity, payload["required_reads"], lambda: {})
    resume = json.loads(view.read("RESUME.md")[0].decode().split("\n", 2)[2])
    command_id = hydrate_sidecar_ref(runtime.state_dir, receipt).payload["command_id"]
    assert {"command_id": command_id, "receipt_id": completed.id} in resume["host_evidence"][
        "available_receipt_locators"
    ]
    index = input_index(
        manifest=mf,
        identity=identity,
        required=payload["required_reads"],
        reads=[],
        source_id="candidate-host-original-receipt-" + completed.id,
        state_dir=runtime.state_dir,
    )
    assert any("artifact read --attempt " + op.attempt_id in i["read_command"] for i in index["sources"])
