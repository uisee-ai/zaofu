from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_runtime import (
    admit_runtime_call_result,
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.operation_projection import project_workflow_operation
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
