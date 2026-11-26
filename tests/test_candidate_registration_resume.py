"""An owner can replay untouched Reader registration without reopening completed product work."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests.test_run_admission import _config
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.candidate_registration_resume import registration_preview
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import write_run_contract_snapshot


@pytest.fixture
def registration(tmp_path):
    rt = _runtime(tmp_path)
    run, task, generation, stage = "run-1", "FLOW-ANCHOR", "map-1", "candidate-review"
    repo = rt.project_root
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=f@localhost",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
    )
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/heads/candidate/demo", head],
        check=True,
    )
    writer = EventWriter(rt.event_log, default_origin="kernel")

    def emit(kind, body, *, actor="zf-cli", cause=None):
        return writer.emit(
            kind,
            actor=actor,
            correlation_id=run,
            causation_id=cause,
            payload={"workflow_run_id": run, **body},
        )

    emit("run.admission.admitted", {"task_id": task}, actor="orchestrator")
    emit("workflow.invoke.requested", {"task_id": task})
    snapshot = write_run_contract_snapshot(rt.state_dir, {"schema_version": "run-contract.v1"})
    package = write_plan_artifact_package(
        rt.state_dir,
        build_plan_artifact_package(
            workflow_run_id=run,
            flow_kind="issue",
            producer_stage_id="plan",
            run_contract=snapshot,
            plan_revision="1",
            task_map_generation=generation,
            produced=[],
            required_ports=[],
        ),
    )
    emit(
        "plan.artifact_package.admitted",
        {"package_ref": package["ref"], "package_digest": package["sha256"]},
    )
    emit(
        "task.pipeline.generation.admitted",
        {"generation_id": "g1", "task_map_generation": generation},
    )
    _seed_frozen_candidate(
        rt,
        run_id=run,
        generation=generation,
        candidate_head=head,
        package_fields={
            "generation_id": "g1",
            "pdd_id": task,
            "fanout_id": "plan-batch",
            "plan_artifact_package_id": package["package_id"],
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    )
    (native,) = [e for e in rt.event_log.read_all() if e.type == "candidate.ready"]
    body = native.payload
    rows = [
        {
            "role_instance": role,
            "child_id": role,
            "target_ref": body["candidate_ref"],
            "payload": {
                "task_id": task,
                "workflow_run_id": run,
                "trigger_payload": body,
                "output_profile_id": "candidate-verify",
                "target_commit": head,
            },
        }
        for role in ("reader-1", "reader-2")
    ]
    common = {
        "task_id": task,
        "stage_id": stage,
        "fanout_id": "review-batch",
        "trigger_event_id": native.id,
    }
    start = emit("fanout.started", {**common, "expected_children": rows}, cause=native.id)
    failed = emit(
        "fanout.cancelled",
        {
            **common,
            **{
                k: body[k]
                for k in (
                    "freeze_receipt_digest",
                    "freeze_id",
                    "candidate_head_commit",
                    "generation_id",
                    "task_map_generation",
                    "plan_artifact_package_digest",
                )
            },
            "candidate_snapshot_event_id": native.id,
            "failure_class": "candidate_authority_invalid",
            "failure_kind": "infra",
            "failure_scope": "candidate",
            "recovery_owner": "run_manager",
        },
        cause=start.id,
    )
    service = ControlledActionService(rt.state_dir, writer, config=_config(), project_root=repo, actor="operator")
    return service, failed, native, emit


def submit(service, failed, payload, **overrides):
    requested = service.writer.emit(
        "control.action.requested",
        actor="operator",
        causation_id=failed.id,
        correlation_id=payload["workflow_run_id"],
        payload=payload,
    )
    return service.execute(
        action="workflow-batch-resume",
        requested_action="recovery-test",
        payload={**payload, **overrides},
        requested=requested,
    )


def test_concurrent_controlled_registration_retries_one_freeze_once(registration, monkeypatch):
    from zf.runtime import candidate_registration_resume as recovery

    checked = []
    original = recovery.prepare_candidate_verification_authority

    def observe(runtime, **kwargs):
        checked.append(kwargs["payload"]["role_instance"])
        return original(runtime, **kwargs)

    monkeypatch.setattr(recovery, "prepare_candidate_verification_authority", observe)
    service, failure, native, _ = registration
    before = service.writer.event_log.read_all()
    preview = registration_preview(service, failure.id)
    assert service.writer.event_log.read_all() == before
    assert preview["reader_roles"] == ["reader-1", "reader-2"]
    # A resident Run Manager is optional; the same mechanical preflight applies.
    payload = {**preview, "approval_ref": "owner:one-exact-retry"}
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(service, failure, payload), range(2)))
    assert checked == ["reader-1", "reader-2"]
    assert sum(row["ok"] for row in results) == 1, results
    (replay,) = [e for e in service.writer.event_log.read_all()[len(before) :] if e.type == "candidate.ready"]
    assert replay.payload["candidate_snapshot_event_id"] == native.id
    assert replay.payload["registration_failure_event_id"] == failure.id
    assert replay.payload["freeze_receipt_digest"] == native.payload["freeze_receipt_digest"]
    assert replay.payload["registration_authorization_event_id"]


@pytest.mark.parametrize(
    "fault",
    [
        "registered",
        "dispatched",
        "business",
        "owner",
        "closed",
        "new_generation",
        "new_freeze",
        "native_file",
        "moved_git",
        "foreign_token",
        "unbound_actor",
        "bad_preflight",
    ],
)
def test_controlled_registration_refuses_unsafe_retry(registration, fault):
    service, failed, native, emit = registration
    payload = {**registration_preview(service, failed.id), "approval_ref": "owner:test"}
    fa = {"fanout_id": failed.payload["fanout_id"]}
    if fault == "registered":
        emit(
            "workflow.operation.requested",
            {"parent_stage_id": failed.payload["stage_id"]},
            cause=failed.causation_id,
        )
    elif fault == "dispatched":
        emit("fanout.child.dispatched", fa)
    elif fault == "business":
        emit("test.failed", {"failure_kind": "product"})
    elif fault == "owner":
        emit(
            "human.escalate",
            {
                "question": "Approve changing the product?",
                "decision_token": "owner-1",
                "blocking_scope": "run",
            },
        )
    elif fault == "closed":
        emit("run.cancelled", {"run_id": payload["workflow_run_id"]})
    elif fault == "new_generation":
        emit("task.pipeline.generation.admitted", {"generation_id": "different"})
    elif fault == "new_freeze":
        service.writer.append(replace(native, id="evt-later-freeze"))
    elif fault == "moved_git":
        subprocess.run(
            [
                "git",
                "-C",
                str(service.project_root),
                "update-ref",
                "-d",
                native.payload["candidate_ref"],
            ],
            check=True,
        )
    elif fault == "native_file":
        (service.state_dir / native.payload["freeze_receipt_ref"]["ref"]).write_text("{}")
    elif fault == "unbound_actor":
        service.actor = "run-manager"
    elif fault == "bad_preflight":
        verify = next(e for e in service.writer.event_log.read_all() if e.type == "task.pipeline.verify.completed")
        (service.state_dir / verify.payload["contract_snapshot_ref"]).write_text("{}")
    overrides = {"approval_ref": "not-the-requested-approval"} if fault == "foreign_token" else {}
    result = submit(service, failed, payload, **overrides)
    assert not result["ok"], result
    assert not any(
        e.type == "candidate.ready" and e.payload.get("registration_authorization_event_id")
        for e in service.writer.event_log.read_all()
    )
