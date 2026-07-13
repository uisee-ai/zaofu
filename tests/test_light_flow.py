"""批D:light 拓扑——profile 编译 / kernel task_map 合成 / 幂等。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.core.config.workflow_profiles import expand_issue_flow, expand_prd_flow
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.light_flow import (
    light_flow_metadata,
    maybe_synthesize_light_task_map,
    synthesize_light_task_map,
)
from zf.runtime.task_map import validate_task_map_payload


def test_light_expansion_shape() -> None:
    out = expand_prd_flow({
        "topology": "light", "prdRef": "docs/prd/x.md",
        "targetRoot": "app", "backend": "codex",
    })
    assert [r["name"] for r in out["roles"]] == ["judge-prd"]
    assert out["stages"] == []  # scan/plan fanout 整段跳过
    assert out["external_triggers"] == ["prd.requested", "task_map.ready"]
    assert out["metadata"]["topology"] == "light"
    assert len(out["pipelines"]) == 1
    assert out["pipelines"][0]["barriers"]["stage_transition"] == "stage_barrier"
    assert "final" not in out["pipelines"][0]["barriers"]


def test_light_pipeline_materializes_candidate_chain(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-light-demo}
spec:
  topology: light
  backend: mock
  prdRef: docs/prd/tiny.md
  targetRoot: app
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

    cfg = load_config(path)

    assert cfg.workflow.pipelines[0].stage_transition == "stage_barrier"
    stages = cfg.workflow.stages
    assert [stage.id for stage in stages] == [
        "prd-lanes-impl",
        "prd-lanes-verify",
        "prd-lanes-final",
    ]
    assert stages[0].trigger == "task_map.ready"
    assert stages[0].aggregate.success_event == "candidate.ready"
    assert stages[1].trigger == "candidate.ready"
    assert stages[1].aggregate.success_event == "test.passed"
    assert stages[2].trigger == "test.passed"
    assert stages[2].aggregate.success_event == "judge.passed"


def test_issue_light_config_loads_target_root_topology(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-light-demo}
spec:
  topology: light
  backend: mock
  issueRef: docs/issues/login-500.md
  targetRoot: app
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

    cfg = load_config(path)

    assert cfg.workflow.flow_metadata["flow_kind"] == "issue"
    assert cfg.workflow.flow_metadata["topology"] == "light"
    assert cfg.workflow.flow_metadata["light_entry_trigger"] == "issue.requested"
    assert cfg.workflow.flow_metadata["target_root"] == "app"
    assert cfg.workflow.stages[0].id == "issue-lanes-impl"
    assert cfg.workflow.stages[0].trigger == "task_map.ready"


def test_default_topology_unchanged() -> None:
    out = expand_prd_flow({"prdRef": "docs/prd/x.md", "targetRoot": "app"})
    assert len(out["stages"]) == 3  # scan/plan/discovery 现状
    assert out["metadata"].get("topology") is None


def test_issue_light_expansion_reuses_single_lane_shape() -> None:
    out = expand_issue_flow({
        "topology": "light",
        "issueRef": "docs/issues/login-500.md",
        "targetRoot": ".",
        "backend": "codex",
    })

    assert [r["name"] for r in out["roles"]] == ["judge-issue"]
    assert out["stages"] == []
    assert out["external_triggers"] == ["issue.requested", "task_map.ready"]
    assert out["metadata"]["topology"] == "light"
    assert out["metadata"]["flow_kind"] == "issue"
    assert out["metadata"]["objective_ref"] == "docs/issues/login-500.md"
    pipeline = out["pipelines"][0]
    assert pipeline["id"] == "issue-lanes"
    assert pipeline["lane_count"] == 1
    assert pipeline["stages"][0]["role_pattern"] == "fix-lane-{lane}"
    assert pipeline["stages"][1]["role_pattern"] == "verify-lane-{lane}"


def test_synthesized_task_map_passes_validation() -> None:
    payload = synthesize_light_task_map(
        pdd_id="default", objective="交付 textstat CLI",
        prd_ref="docs/prd/textstat-prd.md", target_root="app",
    )
    result = validate_task_map_payload(payload)
    assert result.passed, result.errors
    # C1 单源节自带;C2 无系统级命令
    assert payload["shared_conventions"]["test_path_prefix"] == "app/tests"


def test_synthesized_issue_task_map_uses_generic_requirement_text() -> None:
    payload = synthesize_light_task_map(
        pdd_id="issue-default",
        objective="修复登录页 500",
        prd_ref="",
        objective_ref="docs/issues/login-500.md",
        target_root=".",
        flow_kind="issue",
    )
    result = validate_task_map_payload(payload)
    assert result.passed, result.errors
    task = payload["tasks"][0]
    assert payload["shared_conventions"]["test_path_prefix"] == "tests"
    assert task["allowed_paths"][0] == "**"
    assert "issue fix acceptance criteria" in task["description"]
    assert "docs/issues/login-500.md" in task["acceptance_criteria"][0]


def test_synthesized_task_map_preserves_workflow_refs() -> None:
    payload = synthesize_light_task_map(
        pdd_id="default",
        objective="交付 textstat CLI",
        prd_ref="docs/prd/textstat-prd.md",
        target_root="app",
        workflow_refs={
            "workflow_input_manifest_ref": "artifacts/workflow/wf/workflow-input-manifest.json",
            "acceptance_matrix_ref": "artifacts/workflow/wf/acceptance-matrix.json",
            "test_matrix_ref": "artifacts/workflow/wf/test-matrix.json",
            "real_e2e_matrix_ref": "artifacts/workflow/wf/real-e2e-matrix.json",
            "source_refs": {"prd_ref": "docs/prd/textstat-prd.md"},
            "artifact_refs": ["artifacts/workflow/wf/acceptance-matrix.json"],
        },
    )

    task = payload["tasks"][0]
    assert task["workflow_input_manifest_ref"].endswith("workflow-input-manifest.json")
    assert task["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    assert task["test_matrix_ref"].endswith("test-matrix.json")
    assert task["real_e2e_matrix_ref"].endswith("real-e2e-matrix.json")
    assert "referenced acceptance/test/real-e2e matrix" in " ".join(task["acceptance_criteria"])
    assert "workflow_input_manifest_ref" in " ".join(task["verification"])


def _light_config():
    return SimpleNamespace(workflow=SimpleNamespace(flow_metadata={
        "topology": "light", "light_entry_trigger": "prd.requested",
        "prd_ref": "docs/prd/x.md", "target_root": "app",
    }))


def test_entry_trigger_synthesizes_and_emits(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    manifest = tmp_path / "artifacts" / "workflow" / "wf" / "workflow-input-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "acceptance_matrix_ref": str(manifest.parent / "acceptance-matrix.json"),
        "test_matrix_ref": str(manifest.parent / "test-matrix.json"),
        "artifact_refs": [str(manifest.parent / "acceptance-matrix.json")],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    entry = ZfEvent(type="prd.requested", actor="operator",
                    payload={
                        "pdd_id": "default",
                        "objective": "交付 X",
                        "workflow_input_manifest_ref": str(manifest),
                    })
    emitted = maybe_synthesize_light_task_map(
        event=entry, config=_light_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    assert emitted is not None and emitted.type == "task_map.ready"
    assert emitted.payload["source"] == "light_flow_kernel"
    assert emitted.payload["task_map_ref"] == ".zf/artifacts/default/task_map.json"
    assert emitted.payload["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    written = json.loads(
        (state_dir / "artifacts" / "default" / "task_map.json").read_text()
    )
    assert written["tasks"][0]["task_id"] == "DEFAULT-DELIVER-001"
    assert written["tasks"][0]["acceptance_matrix_ref"].endswith("acceptance-matrix.json")


def test_entry_is_idempotent(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    entry = ZfEvent(type="prd.requested", actor="operator",
                    payload={"pdd_id": "default"})
    first = maybe_synthesize_light_task_map(
        event=entry, config=_light_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    second = maybe_synthesize_light_task_map(
        event=entry, config=_light_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=log.read_all(),
    )
    assert first is not None and second is None


def test_non_light_config_is_noop(tmp_path: Path) -> None:
    assert light_flow_metadata(SimpleNamespace(workflow=SimpleNamespace(
        flow_metadata={"topology": ""},
    ))) is None
    assert maybe_synthesize_light_task_map(
        event=ZfEvent(type="prd.requested", actor="op", payload={}),
        config=SimpleNamespace(workflow=SimpleNamespace(flow_metadata={})),
        state_dir=tmp_path, event_writer=None, events=[],
    ) is None


def _light_goal_config():
    cfg = _light_config()
    cfg.goal = SimpleNamespace(enabled=True)
    return cfg


def test_entry_mints_run_goal_when_goal_enabled(tmp_path: Path) -> None:
    """light goal 终态闭环(2026-07-08 第四批):最简配置只开 goal.enabled、
    无人发 run.goal.started → run_id 守卫正确拒发完成事件 → light 没有 goal
    终态。入口合成即补发真 goal(幂等),judge.passed 后可自动闭环。"""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    entry = ZfEvent(type="prd.requested", actor="operator",
                    payload={"pdd_id": "default", "objective": "交付 X"})
    emitted = maybe_synthesize_light_task_map(
        event=entry, config=_light_goal_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    assert emitted is not None
    started = [e for e in log.read_all() if e.type == "run.goal.started"]
    assert len(started) == 1
    payload = started[0].payload
    assert payload["run_id"].startswith("run-light-default-")
    assert payload["objective"] == "交付 X"
    assert payload["source"] == "light_flow_kernel"

    # 幂等:重放入口(带既有事件)不再补发
    again = maybe_synthesize_light_task_map(
        event=entry, config=_light_goal_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=log.read_all(),
    )
    assert again is None
    assert len([
        e for e in log.read_all() if e.type == "run.goal.started"
    ]) == 1


def test_entry_minted_goal_completes_on_judge_passed(tmp_path: Path) -> None:
    """串既有 helper:入口铸的 goal + judge.passed → run.goal.completed
    可发(run_id 非空,不再被 loop.started 兜底守卫拒掉)。"""
    from zf.runtime.run_manager import run_goal_completion_event

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    entry = ZfEvent(type="prd.requested", actor="operator",
                    payload={"pdd_id": "default", "objective": "交付 X"})
    maybe_synthesize_light_task_map(
        event=entry, config=_light_goal_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    judge = ZfEvent(type="judge.passed", actor="zf-cli",
                    payload={"fanout_id": "f", "stage_id": "s",
                             "status": "passed"})
    completion = run_goal_completion_event(
        [*log.read_all(), judge], cause=judge,
    )
    assert completion is not None
    assert completion.type == "run.goal.completed"
    assert completion.payload["run_id"].startswith("run-light-default-")


def test_entry_without_goal_enabled_mints_no_goal(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    entry = ZfEvent(type="prd.requested", actor="operator",
                    payload={"pdd_id": "default"})
    maybe_synthesize_light_task_map(
        event=entry, config=_light_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    assert not [e for e in log.read_all() if e.type == "run.goal.started"]
