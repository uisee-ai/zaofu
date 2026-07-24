from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_admission import (
    CallResultAdmissionOutcome,
    dispatch_call_result_correction,
)
from zf.runtime.call_result_runtime import (
    admit_runtime_call_result,
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.operation_projection import project_workflow_operation
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _runtime(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=project_root,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )


def test_selected_call_result_replays_settled_operation_without_redispatch(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    source = runtime.project_root / "inputs" / "context.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"facts": ["one"]}), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    base_payload = {
        "workflow_run_id": "run-1",
        "fanout_id": "F1",
        "child_id": "C1",
        "stage_id": "impl",
        "task_id": "T1",
        "plan_artifact_package_id": "planpkg-current",
        "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
        "plan_artifact_package_digest": "package-sha",
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "contract-sha",
        "artifact_refs": [{
            "source_id": "context",
            "artifact_id": "context",
            "kind": "context",
            "ref": "inputs/context.json",
            "sha256": digest,
        }],
        "required_reads": [{
            "source_id": "context",
            "artifact_id": "context",
            "artifact_sha256": digest,
            "json_path": "$.facts",
        }],
    }
    payload = dict(base_payload)
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_writer_child",
        operation_key="C1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    assert prepared.should_dispatch is True
    assert prepared.output_profile_id == "implementation"
    requested = next(
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    assert request["output_profile_id"] == "implementation"
    assert request["output_profile_revision"] == "1"
    assert request["attempt_domain"] == "task"
    assert request["result_identity"]["plan_artifact_package_id"] == "planpkg-current"
    assert request["result_identity"]["run_contract_digest"] == "contract-sha"
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T1",
        dispatch_id="attempt-1",
    )
    running_projection = project_workflow_operation(
        runtime.state_dir,
        prepared.operation_id,
    )
    assert running_projection["status"] == "running"
    terminal = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="T1",
        payload={
            **payload,
            "target_commit": "abc123",
            "source_commit": "abc123",
            "task_ref": "refs/zf/tasks/T1",
            "summary": "done",
        },
    )
    outcome = admit_runtime_call_result(runtime, terminal, mode="blocking")
    assert outcome.admitted is True

    restarted = _runtime(tmp_path)
    replay_payload = dict(base_payload)
    replay = prepare_call_operation(
        restarted,
        payload=replay_payload,
        operation_type="fanout_writer_child",
        operation_key="C1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    assert replay.ensure_status == "settled"
    assert replay.should_dispatch is False
    assert replay.admitted_call_result_ref == outcome.envelope_ref["ref"]
    projection = project_workflow_operation(runtime.state_dir, replay.operation_id)
    assert projection["status"] == "settled"
    assert projection["blockers"] == []


def test_inherited_operation_identity_is_rederived_per_stage(tmp_path: Path) -> None:
    """ZF-REVIEW-140-B3:verify child 的 payload 派生自上游 impl manifest
    child,继承的 operation_id 曾压过本段派生 → request_hash_divergence 环。
    修复后:同一 payload 流经不同 stage 必须得到不同、且本段稳定的身份。"""
    runtime = _runtime(tmp_path)
    payload = {
        "workflow_run_id": "run-1",
        "fanout_id": "F1",
        "child_id": "C1",
        "stage_id": "impl",
        "task_id": "T1",
    }
    impl = prepare_call_operation(
        runtime, payload=payload, operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F1-impl",
    )
    # payload 现在带着 impl 的 operation_id(manifest 持久化的污染形态)
    assert payload["operation_id"] == impl.operation_id
    verify = prepare_call_operation(
        runtime, payload=payload, operation_type="fanout_reader_child",
        operation_key="verify-lane-0-T1", stage_id="prd-lanes-verify",
        task_id="T1", dispatch_id="run-F2-verify",
    )
    assert verify.operation_id != impl.operation_id, "verify 不得继承 impl 身份"
    # 同 dispatch 重放:身份稳定(replay 语义不变)
    replay = prepare_call_operation(
        runtime, payload=dict(payload), operation_type="fanout_reader_child",
        operation_key="verify-lane-0-T1", stage_id="prd-lanes-verify",
        task_id="T1", dispatch_id="run-F2-verify",
    )
    assert replay.operation_id == verify.operation_id


def test_candidate_verify_operation_requires_current_contract_target_and_self_check(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    descriptors: dict[str, tuple[str, str]] = {}
    for source_id in ("contract", "target", "impl-self-check"):
        ref = f"artifacts/{source_id}.json"
        path = runtime.state_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"source": source_id}), encoding="utf-8")
        descriptors[source_id] = (ref, hashlib.sha256(path.read_bytes()).hexdigest())
    payload = {
        "workflow_run_id": "run-candidate",
        "task_id": "T1",
        "output_profile_id": "candidate-verify",
        "contract_snapshot_ref": descriptors["contract"][0],
        "contract_snapshot_digest": descriptors["contract"][1],
        "target_snapshot_ref": descriptors["target"][0],
        "target_snapshot_digest": descriptors["target"][1],
        "impl_self_check_ref": descriptors["impl-self-check"][0],
        "impl_self_check_digest": descriptors["impl-self-check"][1],
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="candidate-verify-T1",
        stage_id="candidate-verify",
        task_id="T1",
        dispatch_id="attempt-candidate",
    )

    assert prepared.output_profile_id == "candidate-verify"
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    assert {item["source_id"] for item in manifest["sources"]} == {
        "contract",
        "target",
        "impl-self-check",
    }
    assert {item["source_id"] for item in payload["required_reads"]} == {
        "contract",
        "target",
        "impl-self-check",
    }


def _prepared_plan_synth(tmp_path: Path):
    runtime = _runtime(tmp_path)
    report_path = runtime.state_dir / "fanouts" / "F-PLAN" / "children" / "planner" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({
        "status": "passed",
        "recommendation": "approve",
        "task_map": {"tasks": [{"task_id": "T1"}]},
    }), encoding="utf-8")
    manifest = {
        "fanout_id": "F-PLAN",
        "trace_id": "run-plan",
        "workflow_run_id": "run-plan",
        "stage_id": "plan",
        "trigger_event_id": "evt-plan-trigger",
        "target_ref": "main",
    }
    payload = build_plan_synth_call_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        manifest=manifest,
        reports=[{
            "child_id": "planner",
            "report_path": str(report_path),
        }],
        run_id="run-F-PLAN-synth",
        role_instance="plan-critic",
    )
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_synth",
        operation_key="synth@trig:evt-plan-tri",
        stage_id="plan",
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    return runtime, payload, prepared


def _plan_synth_event(payload: dict, *, plan_revision: str = "") -> ZfEvent:
    return ZfEvent(
        type="fanout.synth.completed",
        actor="plan-critic",
        correlation_id="run-plan",
        payload={
            **payload,
            "plan_revision": plan_revision or payload["plan_revision"],
            "status": "completed",
            "recommendation": "approve",
            "summary": "plan ready",
            "report": {
                "status": "passed",
                "recommendation": "approve",
                "summary": "plan ready",
            },
        },
    )


def test_plan_synth_required_reads_repair_then_admit_and_replay(
    tmp_path: Path,
) -> None:
    runtime, payload, prepared = _prepared_plan_synth(tmp_path)
    assert prepared.output_profile_id == "plan-synth"
    assert {row["source_id"] for row in payload["required_reads"]} == {
        "plan-synth-contract",
        "child-result-planner",
    }

    missing = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert missing.repair_requested is True
    assert missing.repair_round == 1

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
        )
    admitted = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert admitted.admitted is True
    duplicate = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert duplicate.admitted is True
    assert duplicate.admitted_event_id == admitted.admitted_event_id

    restarted = _runtime(tmp_path)
    replay_payload = build_plan_synth_call_payload(
        state_dir=restarted.state_dir,
        project_root=restarted.project_root,
        manifest={
            "fanout_id": "F-PLAN",
            "trace_id": "run-plan",
            "workflow_run_id": "run-plan",
            "stage_id": "plan",
            "trigger_event_id": "evt-plan-trigger",
            "target_ref": "main",
        },
        reports=[{
            "child_id": "planner",
            "report_path": str(
                restarted.state_dir
                / "fanouts/F-PLAN/children/planner/report.json"
            ),
        }],
        run_id="run-F-PLAN-synth",
        role_instance="plan-critic",
    )
    replay = prepare_call_operation(
        restarted,
        payload=replay_payload,
        operation_type="fanout_synth",
        operation_key="synth@trig:evt-plan-tri",
        stage_id="plan",
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    assert replay.ensure_status == "settled"
    assert replay.output_profile_id == "plan-synth"
    assert replay.output_profile_revision == "1"


def test_plan_synth_stale_revision_is_superseded(tmp_path: Path) -> None:
    runtime, payload, _prepared = _prepared_plan_synth(tmp_path)
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
        )

    outcome = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload, plan_revision="plan-rstale"),
        mode="blocking",
        dispatch_correction=False,
    )
    assert outcome.status == "superseded"
    assert {issue["code"] for issue in outcome.issues} == {"stale_plan_revision"}
    assert not [
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.call.result.admitted"
    ]


def test_call_result_correction_waits_for_source_turn_stop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.roles = [RoleConfig(
        name="verify",
        instance_id="verify-1",
        backend="codex",
        role_kind="reader",
    )]
    source_event = ZfEvent(
        type="verify.child.failed",
        actor="verify-1",
        task_id="T1",
        payload={"role_instance": "verify-1"},
    )
    runtime.event_log.append(source_event)

    def append_stop(_delay: float) -> None:
        runtime.event_log.append(ZfEvent(
            type="codex.hook.stop",
            actor="verify-1",
        ))

    monkeypatch.setattr(
        "zf.runtime.call_result_admission.time.sleep",
        append_stop,
    )
    sent: list[tuple[str, Path]] = []

    def send_task(role_name, briefing_path, _prompt, _context):  # noqa: ANN001
        assert runtime.event_log.read_all()[-1].type == "codex.hook.stop"
        sent.append((role_name, briefing_path))

    runtime._dispatch_context = lambda **_kwargs: None
    runtime._send_transport_task = send_task
    outcome = CallResultAdmissionOutcome(
        status="repair_pending",
        mode="blocking",
        operation_id="op-1",
        request_hash="request-1",
        repair_round=1,
        correction_ref={"ref": "artifacts/correction.json"},
        correction_dispatch_required=True,
    )

    assert dispatch_call_result_correction(
        runtime,
        source_event=source_event,
        outcome=outcome,
    ) is True
    assert sent[0][0] == "verify-1"
    assert sent[0][1].name == "verify-1-T1-result-correction-1.md"


def test_rework_of_scopes_new_operation(tmp_path: Path) -> None:
    """ZF-REVIEW-140-B3:带 rework_of 的 retrigger 是新 operation(140
    裁决 10:rework 不是 replay),不得与原 operation 撞名。"""
    runtime = _runtime(tmp_path)
    base = {
        "workflow_run_id": "run-1", "fanout_id": "F1",
        "child_id": "C1", "stage_id": "impl", "task_id": "T1",
    }
    first = prepare_call_operation(
        runtime, payload=dict(base), operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F1",
    )
    retrigger = prepare_call_operation(
        runtime, payload={**base, "rework_of": "evt-rej-1"},
        operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F3",
    )
    assert retrigger.operation_id != first.operation_id


def test_generation_scoped_operation_key_per_trigger() -> None:
    """ZF-GEN-SCOPE-01:同触发重放同 id(replay 保持),新触发新代。

    07-17 四次实弹同墙:scan 重发/discovery 重发/final 重发/replan
    task_map 重派 — 同 child 键撞已注册 op → divergence 拒绝。
    键掺 trigger_event_id 后,retrigger 天然新代,replay 语义不变。
    """
    key_a1 = "dev-lane-0-T1@trig:evt-aaaa0000"
    key_a2 = "dev-lane-0-T1@trig:evt-aaaa0000"
    key_b = "dev-lane-0-T1@trig:evt-bbbb1111"
    from zf.runtime.workflow_operation import stable_operation_id

    id_a1 = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_a1, operation_type="fanout_writer_child",
    )
    id_a2 = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_a2, operation_type="fanout_writer_child",
    )
    id_b = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_b, operation_type="fanout_writer_child",
    )
    assert id_a1 == id_a2  # 同触发重放 → replay
    assert id_a1 != id_b   # 新触发 → 新代
