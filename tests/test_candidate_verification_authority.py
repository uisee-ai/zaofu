from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.candidate_verification_authority import (
    CandidateVerificationAuthorityError,
    prepare_candidate_verification_authority,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    hydrate_task_contract_snapshot,
    write_target_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.task_pipeline_briefing import verification_result_template


def _runtime(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    log = EventLog(state_dir / "events.jsonl")
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="FLOW-ANCHOR",
        title="workflow anchor",
        status="in_progress",
    ))
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=project_root,
        event_log=log,
        event_writer=EventWriter(log),
        task_store=task_store,
        config=SimpleNamespace(
            roles=[],
            workflow=SimpleNamespace(
                flow_metadata={
                    "flow_kind": "issue",
                    "artifact_package_mode": "shadow",
                    "result_protocol": {
                        "mode": "blocking",
                        "semantic_submit_profiles": {
                            "candidate-verify": "blocking",
                        },
                    },
                },
            ),
        ),
    )


def _seed_frozen_candidate(runtime: SimpleNamespace, tier: str = "task_non_smoke") -> dict[str, str]:
    run_id = "run-candidate-authority"
    generation = "generation-1"
    child_task_id = "TASK-1"
    child_commit = "b" * 40
    candidate_head = "c" * 40
    command = "pytest -q tests/test_pulse.py"
    command_digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    contract = {
        "schema_version": "task-contract-snapshot.v1",
        "workflow_run_id": run_id,
        "task_id": child_task_id,
        "contract_revision": "contract-child-1",
        "task_map_generation": generation,
        "base_commit": "a" * 40,
        "task_ref": f"task/{child_task_id}",
        "plan_artifact_package_id": "",
        "plan_artifact_package_ref": "",
        "plan_artifact_package_digest": "",
        "title": "child",
        "behavior": "implement pulse status",
        "allowed_paths": ["src/pulse.py", "tests/test_pulse.py"],
        "protected_paths": [".zf/**"],
        "acceptance_criteria": [{
            "acceptance_id": "AC-1",
            "statement": "pulse status is filterable",
            "text": "pulse status is filterable",
            "mandatory": True,
            "verification_owner": "task_verify",
            "verification_tier": tier,
            "verification_command_ids": ["cmd-1"],
        }],
        "verification_command": command,
        "verification_commands": [{
            "command_id": "cmd-1",
            "command": command,
            "command_digest": command_digest,
            "acceptance_ids": ["AC-1"],
            "owner": "task_verify",
            "tier": tier,
            "producer_paths": ["artifacts/probe.json"],
            "deterministic": True,
            "reusable": False,
            "timeout_seconds": 60,
        }],
        "verification_tiers": [tier],
        "required_source_outputs": [],
        "required_contract_tests": ["tests/test_pulse.py"],
        "source_refs": {},
        "evidence_contract": {},
        "source_ref": "",
        "source_index_ref": "",
        "product_contract_ref": "",
        "risk_class": "low",
        "integration_admission_profile": "standard",
    }
    contract_descriptor = write_task_contract_snapshot(runtime.state_dir, contract)
    target_descriptor = write_target_snapshot(
        runtime.state_dir,
        build_target_snapshot(
            contract_descriptor,
            target_commit=child_commit,
            contract_snapshot=contract,
        ),
    )
    runtime.event_log.append(ZfEvent(
        id="evt-task-ref",
        type="task.ref.updated",
        task_id=child_task_id,
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "task_id": child_task_id,
            "source_commit": child_commit,
        },
    ))
    runtime.event_log.append(ZfEvent(
        id="evt-task-verify",
        type="task.pipeline.verify.completed",
        task_id=child_task_id,
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "parent_task_id": "FLOW-ANCHOR",
            "task_id": child_task_id,
            "task_map_generation": generation,
            "target_commit": child_commit,
            "contract_snapshot_ref": contract_descriptor["ref"],
            "contract_snapshot_digest": contract_descriptor["sha256"],
            "target_snapshot_ref": target_descriptor["ref"],
            "target_snapshot_digest": target_descriptor["sha256"],
        },
    ))
    freeze_receipt = {
        "schema_version": "candidate-freeze-receipt.v1",
        "freeze_id": "freeze-1",
        "workflow_run_id": run_id,
        "task_map_generation": generation,
        "candidate_generation": "candidate-generation-1",
        "candidate_base_commit": "a" * 40,
        "candidate_head": candidate_head,
        "candidate_head_commit": candidate_head,
        "candidate_ref": "refs/heads/candidate/demo",
        "integration_ledger_digest": "d" * 64,
        "completed_task_ids": [child_task_id],
        "task_ids": [child_task_id],
        "status": "frozen",
    }
    freeze_descriptor = write_immutable_json_sidecar(
        runtime.state_dir,
        freeze_receipt,
        root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt",
        schema_version="candidate-freeze-receipt.v1",
        created_by="test",
    )
    runtime.event_log.append(ZfEvent(
        id="evt-candidate-ready",
        type="candidate.ready",
        correlation_id=run_id,
        payload={
            **freeze_receipt,
            "target_commit": candidate_head,
            "freeze_receipt_ref": freeze_descriptor,
            "freeze_receipt_digest": freeze_descriptor["sha256"],
        },
    ))
    return {
        "run_id": run_id,
        "generation": generation,
        "child_task_id": child_task_id,
        "candidate_head": candidate_head,
        "command": command,
    }


@pytest.mark.parametrize("tier", ["task_non_smoke", "real_e2e"])
def test_candidate_verify_operation_pins_aggregate_contract_and_target(
    tmp_path: Path, tier: str,
) -> None:
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime, tier)
    payload = {
        "workflow_run_id": seeded["run_id"],
        "task_id": "FLOW-ANCHOR",
        "fanout_id": "fanout-candidate-verify",
        "child_id": "verify-lane-0",
        "role_instance": "verify-lane-0",
        "stage_id": "issue-lanes-verify",
        "output_profile_id": "candidate-verify",
        "artifact_package_mode": "shadow",
        "target_commit": seeded["candidate_head"],
        "canonical_success_event": "test.passed",
        "canonical_failure_event": "test.failed",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="verify-lane-0",
        stage_id="issue-lanes-verify",
        task_id="FLOW-ANCHOR",
        dispatch_id="dispatch-candidate-verify",
        causation_id="evt-candidate-ready",
    )

    assert prepared.output_profile_id == "candidate-verify"
    assert payload["verification_owner"] == "candidate_verify"
    assert payload["verification_tier"] == "integration"
    aggregate = hydrate_task_contract_snapshot(
        runtime.state_dir,
        {
            "ref": payload["contract_snapshot_ref"],
            "sha256": payload["contract_snapshot_digest"],
        },
    )
    assert aggregate["authority_scope"] == "candidate"
    assert aggregate["task_id"] == "FLOW-ANCHOR"
    assert aggregate["completed_task_ids"] == [seeded["child_task_id"]]
    assert aggregate["verification_commands"][0]["command"] == seeded["command"]
    assert aggregate["verification_commands"][0]["tier"] == tier
    assert aggregate["verification_commands"][0]["producer_paths"] == ["artifacts/probe.json"]
    assert aggregate["acceptance_criteria"][0]["verification_tier"] == tier
    template = verification_result_template(aggregate)
    assert template["execution_status"] == "completed"
    assert template["verification_owner"] == "candidate_verify"
    assert template["verification_tier"] == "integration"

    requested = next(
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    identity = request["result_identity"]
    assert identity["contract_snapshot_ref"] == payload["contract_snapshot_ref"]
    assert identity["target_snapshot_ref"] == payload["target_snapshot_ref"]
    assert identity["target_commit"] == seeded["candidate_head"]
    assert {row["source_id"] for row in request["required_reads"]} >= {
        "contract",
        "target",
        "candidate-freeze",
    }
    assert "impl-self-check" not in {
        row["source_id"] for row in request["required_reads"]
    }


def test_candidate_verify_authority_pins_triggering_freeze(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    original = next(
        event
        for event in runtime.event_log.read_all()
        if event.id == "evt-candidate-ready"
    )
    original_body = dict(original.payload)
    newer_receipt = {
        key: value
        for key, value in original_body.items()
        if key not in {
            "target_commit",
            "freeze_receipt_ref",
            "freeze_receipt_digest",
        }
    }
    newer_receipt.update({
        "freeze_id": "freeze-2",
        "candidate_generation": "candidate-generation-2",
    })
    newer_descriptor = write_immutable_json_sidecar(
        runtime.state_dir,
        newer_receipt,
        root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt",
        schema_version="candidate-freeze-receipt.v1",
        created_by="test",
    )
    runtime.event_log.append(ZfEvent(
        id="evt-candidate-ready-newer",
        type="candidate.ready",
        correlation_id=seeded["run_id"],
        payload={
            **newer_receipt,
            "target_commit": seeded["candidate_head"],
            "freeze_receipt_ref": newer_descriptor,
            "freeze_receipt_digest": newer_descriptor["sha256"],
        },
    ))
    runtime.event_log.append(ZfEvent(
        id="evt-candidate-ready-recovery",
        type="candidate.ready",
        correlation_id=seeded["run_id"],
        payload={
            "workflow_run_id": seeded["run_id"],
            "candidate_head_commit": seeded["candidate_head"],
            "candidate_base_commit": "a" * 40,
            "completed_task_ids": [seeded["child_task_id"]],
            "candidate_snapshot_event_id": "evt-candidate-ready",
            "freeze_id": "freeze-1",
            "source": "workflow_resume_batch",
        },
    ))
    payload = {
        "workflow_run_id": seeded["run_id"],
        "task_id": "FLOW-ANCHOR",
        "target_commit": seeded["candidate_head"],
        "trigger_payload": {
            "freeze_id": "freeze-1",
            "candidate_snapshot_event_id": "evt-candidate-ready-recovery",
        },
    }

    prepare_candidate_verification_authority(
        runtime,
        payload=payload,
        workflow_run_id=seeded["run_id"],
        task_id="FLOW-ANCHOR",
    )

    assert payload["candidate_snapshot_event_id"] == "evt-candidate-ready"
    target = hydrate_sidecar_ref(
        runtime.state_dir,
        {
            "ref": payload["target_snapshot_ref"],
            "sha256": payload["target_snapshot_digest"],
        },
    ).payload
    assert target["candidate_event_id"] == "evt-candidate-ready"


def test_candidate_verify_fails_before_dispatch_without_current_task_verify(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    events = [
        event for event in runtime.event_log.read_all()
        if event.type != "task.pipeline.verify.completed"
    ]
    runtime.event_log.path.write_text(
        "".join(event.to_json() + "\n" for event in events),
        encoding="utf-8",
    )

    with pytest.raises(
        CandidateVerificationAuthorityError,
        match="lacks current admitted Verify evidence",
    ):
        prepare_candidate_verification_authority(
            runtime,
            payload={"target_commit": seeded["candidate_head"]},
            workflow_run_id=seeded["run_id"],
            task_id="FLOW-ANCHOR",
        )


def test_candidate_verify_semantic_submit_uses_pinned_candidate_authority(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    runtime.event_log.append(ZfEvent(
        id="evt-candidate-recovery-wrapper",
        type="candidate.ready",
        correlation_id=seeded["run_id"],
        payload={
            "workflow_run_id": seeded["run_id"],
            "trace_id": seeded["run_id"],
            "pdd_id": "FLOW-ANCHOR",
            "feature_id": "FLOW-ANCHOR",
            "candidate_ref": "refs/heads/candidate/demo",
            "candidate_base_commit": "a" * 40,
            "candidate_head_commit": seeded["candidate_head"],
            "completed_task_ids": [seeded["child_task_id"]],
            "candidate_snapshot_event_id": "evt-candidate-ready",
            "source": "workflow_resume_batch",
            "resume_checkpoint_ref": "wfres-candidate-retry",
        },
    ))
    token = provision_role_submit_credential(
        runtime.state_dir,
        "verify-lane-0",
    ).read_text().strip()
    payload = {
        "workflow_run_id": seeded["run_id"],
        "task_id": "FLOW-ANCHOR",
        "fanout_id": "fanout-candidate-verify",
        "child_id": "verify-lane-0",
        "role_instance": "verify-lane-0",
        "stage_id": "issue-lanes-verify",
        "output_profile_id": "candidate-verify",
        "artifact_package_mode": "shadow",
        "target_commit": seeded["candidate_head"],
        "canonical_success_event": "test.passed",
        "canonical_failure_event": "test.failed",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="verify-lane-0",
        stage_id="issue-lanes-verify",
        task_id="FLOW-ANCHOR",
        dispatch_id="dispatch-candidate-verify",
        causation_id="evt-candidate-ready",
    )
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    for requirement in payload["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            source_id=requirement["source_id"],
            artifact_id=requirement["artifact_id"],
            json_path=requirement["json_path"],
        )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="FLOW-ANCHOR",
        dispatch_id="dispatch-candidate-verify",
        causation_id="evt-candidate-ready",
    )
    aggregate = hydrate_task_contract_snapshot(
        runtime.state_dir,
        {
            "ref": payload["contract_snapshot_ref"],
            "sha256": payload["contract_snapshot_digest"],
        },
    )
    semantic = verification_result_template(aggregate)
    semantic.update({
        "summary": "candidate commands and acceptance criteria passed",
        "evidence_refs": ["event:test-candidate-verify"],
    })
    for probe in semantic["probe_receipts"]:
        probe["target_commit"] = seeded["candidate_head"]
        probe["evidence_refs"] = ["event:test-candidate-command"]
    for result in semantic["requirement_results"]:
        result["evidence_refs"] = ["event:test-candidate-acceptance"]

    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=semantic,
        role_instance="verify-lane-0",
        credential=token,
    )

    assert submitted.admitted_event_id
    admitted = [
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.call.result.admitted"
    ]
    assert admitted
