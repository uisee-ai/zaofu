"""批D:light 拓扑——profile 编译 / kernel task_map 合成 / 幂等。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.core.config.workflow_profiles import (
    expand_issue_flow,
    expand_prd_flow,
    expand_refactor_flow_v1,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.light_flow import (
    light_flow_entry_triggers,
    light_flow_metadata,
    maybe_synthesize_light_task_map,
    synthesize_light_task_map,
)
from zf.runtime.generic_workflow_fanout import (
    fanout_stage_matches_trigger_event,
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
    assert stages[2].trigger == "flow.goal.closed"
    assert stages[2].aggregate.success_event == "goal.closure.synthesized"


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


def test_issue_default_topology_is_light_single_lane() -> None:
    out = expand_issue_flow({
        "issueRef": "docs/issues/login-500.md",
        "targetRoot": ".",
        "backend": "codex",
    })

    assert out["metadata"]["topology"] == "light"
    assert out["stages"] == []
    assert out["pipelines"][0]["lane_count"] == 1
    assert [
        stage["id"] for stage in out["pipelines"][0]["stages"]
    ] == ["impl", "verify"]
    assert out["pipelines"][0]["final"]["role"] == "judge-issue"
    assert [role["name"] for role in out["roles"]] == ["judge-issue"]
    assert out["entry_stage_id"] == "issue-lanes-impl"


def test_default_topology_unchanged() -> None:
    out = expand_prd_flow({"prdRef": "docs/prd/x.md", "targetRoot": "app"})
    assert len(out["stages"]) == 3  # scan/plan/discovery
    assert out["metadata"].get("topology") is None
    plan = next(stage for stage in out["stages"] if stage["id"] == "prd-plan")
    assert plan["aggregate"]["success_event"] == "task_map.ready"
    assert plan["aggregate"]["synth_role"] == "plan-critic"
    assert [role["name"] for role in out["roles"]].count("plan-critic") == 1
    critic = next(role for role in out["roles"] if role["name"] == "plan-critic")
    assert critic["role_kind"] == "reader"


def test_standard_issue_and_refactor_have_one_plan_critic() -> None:
    issue = expand_issue_flow({
        "topology": "fanout",
        "issueRef": "docs/issues/x.md",
        "targetRoot": ".",
    })
    refactor = expand_refactor_flow_v1({"assembly": "none", "lanes": 1})

    assert next(
        stage for stage in issue["stages"] if stage["id"] == "issue-triage"
    )["aggregate"]["synth_role"] == "plan-critic"
    refactor_plan = next(
        stage for stage in refactor["stages"] if stage["id"] == "flow-plan"
    )
    assert refactor_plan["aggregate"]["synth_role"] == "plan-critic"
    assert refactor_plan["aggregate"]["success_event"] == (
        "zaofu.refactor.plan.ready"
    )


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


def test_multi_kind_light_entry_resolves_by_flow_kind(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-light}
spec: {backend: mock, issueRef: docs/issues/bug.md}
---
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-standard}
spec: {backend: mock, prdRef: docs/prd/feature.md}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: multi-light}
spec:
  version: "1.0"
  project: {name: multi-light}
""")
    config = load_config(path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")

    assert light_flow_entry_triggers(config) == ("issue.requested",)
    emitted = maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="issue.requested",
            actor="zf-cli",
            payload={
                "kind": "issue",
                "pdd_id": "issue-multi",
                "objective": "Fix the regression",
            },
        ),
        config=config,
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
    )

    assert emitted is not None
    assert emitted.payload["flow_kind"] == "issue"
    assert config.workflow.kind_routes["issue"].pattern_id == "issue-lanes-impl"
    assert maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="issue.requested",
            actor="zf-cli",
            payload={
                "kind": "prd",
                "pdd_id": "wrong-kind",
                "objective": "Must not cross Flow routes",
            },
        ),
        config=config,
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=log.read_all(),
    ) is None


def test_synthesized_task_map_passes_validation() -> None:
    payload = synthesize_light_task_map(
        pdd_id="default", objective="交付 textstat CLI",
        prd_ref="docs/prd/textstat-prd.md", target_root="app",
    )
    result = validate_task_map_payload(payload)
    assert result.passed, result.errors
    # C1 单源节自带;C2 无系统级命令
    assert payload["shared_conventions"]["test_path_prefix"] == "app/tests"
    assert payload["required_plan_ports"] == [
        "requirement_spec",
        "goal_claim_set",
        "task_map",
        "planning_result",
    ]


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
    assert payload["required_plan_ports"][0] == "issue_spec"
    assert task["allowed_paths"][0] == "**"
    assert "issue fix acceptance criteria" in task["description"]
    assert "docs/issues/login-500.md" in task["acceptance_criteria"][0]


def test_synthesized_task_map_does_not_bind_unready_workflow_matrices() -> None:
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
    assert "acceptance_matrix_ref" not in task
    assert "test_matrix_ref" not in task
    assert "real_e2e_matrix_ref" not in task
    assert "acceptance_matrix_ref" not in payload
    assert "test_matrix_ref" not in payload
    assert "real_e2e_matrix_ref" not in payload
    assert task["artifact_refs"] == []
    assert payload["artifact_refs"] == []
    assert payload["required_plan_ports"] == [
        "requirement_spec",
        "goal_claim_set",
        "task_map",
        "planning_result",
    ]
    assert "referenced acceptance/test/real-e2e matrix" not in " ".join(
        task["acceptance_criteria"]
    )
    assert "verification" not in task


def test_absolute_target_root_uses_worktree_relative_scope() -> None:
    payload = synthesize_light_task_map(
        pdd_id="issue",
        objective="Fix the regression",
        prd_ref="docs/issue.md",
        target_root="/tmp/project",
        verification_commands=["python3 -m unittest"],
        flow_kind="issue",
    )

    task = payload["tasks"][0]
    assert task["allowed_paths"] == ["**", "README.md"]
    assert payload["shared_conventions"]["test_path_prefix"] == "tests"


def test_prose_verification_guidance_is_not_emitted_as_command() -> None:
    payload = synthesize_light_task_map(
        pdd_id="issue",
        objective="修复 issue",
        prd_ref="artifacts/issue.md",
        target_root=".",
        verification_commands=["执行分诊报告中的验证命令并保存可审计证据。"],
        flow_kind="issue",
    )

    task = payload["tasks"][0]
    assert "verification" not in task
    assert "validation" not in task
    assert "Verification guidance:" in " ".join(task["acceptance_criteria"])
    assert validate_task_map_payload(payload).passed


def test_synthesized_task_map_binds_only_explicit_ready_matrix_ports() -> None:
    payload = synthesize_light_task_map(
        pdd_id="default",
        objective="交付 textstat CLI",
        prd_ref="docs/prd/textstat-prd.md",
        target_root="app",
        workflow_refs={
            "acceptance_matrix_ref": "artifacts/workflow/wf/acceptance-matrix.json",
        },
        ready_plan_ports=["acceptance_matrix"],
    )

    assert payload["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    assert payload["tasks"][0]["acceptance_matrix_ref"].endswith(
        "acceptance-matrix.json"
    )
    assert payload["required_plan_ports"][-1] == "acceptance_matrix"
    assert "ready referenced acceptance/test/real-e2e matrix" in " ".join(
        payload["tasks"][0]["acceptance_criteria"]
    )


def _light_config():
    return SimpleNamespace(workflow=SimpleNamespace(flow_metadata={
        "topology": "light", "light_entry_trigger": "prd.requested",
        "flow_kind": "prd", "prd_ref": "docs/prd/x.md", "target_root": "app",
    }))


def test_entry_trigger_synthesizes_and_emits(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    manifest = tmp_path / "artifacts" / "workflow" / "wf" / "workflow-input-manifest.json"
    manifest.parent.mkdir(parents=True)
    acceptance_matrix = manifest.parent / "acceptance-matrix.json"
    acceptance_matrix.write_text(json.dumps({
        "schema_version": "acceptance-matrix.v1",
        "status": "ready",
        "metadata": {
            "enrichment_contract": {"status": "fulfilled"},
        },
    }), encoding="utf-8")
    test_matrix = manifest.parent / "test-matrix.json"
    test_matrix.write_text(json.dumps({
        "schema_version": "test-matrix.v1",
        "status": "ready",
        "metadata": {
            "enrichment_contract": {"status": "fulfilled"},
        },
        "tests": [{"test_id": "verify", "commands": ["python app/verify.py"]}],
    }), encoding="utf-8")
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
                        "requirement_spec_ref": "artifacts/requirements/r2.json",
                        "requirement_spec_digest": "requirement-r2-sha",
                        "prd_ref": "docs/prd/current.md",
                        "workflow_input_manifest_ref": str(manifest),
                    })
    emitted = maybe_synthesize_light_task_map(
        event=entry, config=_light_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    assert emitted is not None and emitted.type == "task_map.ready"
    assert emitted.payload["source"] == "light_flow_kernel"
    assert emitted.payload["flow_kind"] == "prd"
    assert emitted.payload["task_map_ref"] == "artifacts/default/task_map.json"
    assert emitted.payload["requirement_spec_ref"] == (
        "artifacts/requirements/r2.json"
    )
    assert emitted.payload["requirement_spec_digest"] == "requirement-r2-sha"
    assert emitted.payload["prd_ref"] == "docs/prd/current.md"
    assert emitted.payload["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    written = json.loads(
        (state_dir / "artifacts" / "default" / "task_map.json").read_text()
    )
    assert written["tasks"][0]["task_id"] == "DEFAULT-DELIVER-001"
    assert (
        "Source requirement: artifacts/requirements/r2.json"
        in written["tasks"][0]["description"]
    )
    assert written["tasks"][0]["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    assert written["tasks"][0]["verification"] == "python app/verify.py"
    assert written["tasks"][0]["validation"]["commands"][0]["id"] == (
        "light-verification-1"
    )


def test_entry_inherits_workflow_task_verification(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-ISSUE",
        title="Fix the regression",
        contract=TaskContract(
            verification="python3 -m unittest discover -s tests -v",
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    emitted = maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="prd.requested",
            actor="web",
            task_id="TASK-ISSUE",
            payload={
                "task_id": "TASK-ISSUE",
                "pdd_id": "workflow-issue",
                "objective": "Fix the regression",
            },
        ),
        config=_light_config(),
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
        task_store=store,
    )

    assert emitted is not None
    task_map = json.loads(
        (
            state_dir
            / "artifacts"
            / "workflow-issue"
            / "task_map.json"
        ).read_text()
    )
    task = task_map["tasks"][0]
    assert task["verification"] == (
        "python3 -m unittest discover -s tests -v"
    )
    assert task["validation"]["commands"][0]["command"] == (
        "python3 -m unittest discover -s tests -v"
    )


def test_entry_keeps_draft_matrices_out_of_task_contract(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workflow_dir = tmp_path / "artifacts" / "workflow" / "wf"
    workflow_dir.mkdir(parents=True)
    for name, schema in (
        ("acceptance-matrix.json", "acceptance-matrix.v1"),
        ("test-matrix.json", "test-matrix.v1"),
        ("real-e2e-matrix.json", "real-e2e-matrix.v1"),
    ):
        body = {
            "schema_version": schema,
            "status": "draft",
            "metadata": {
                "enrichment_contract": {"status": "requires_scan_plan_enrichment"},
            },
        }
        if name == "test-matrix.json":
            body["tests"] = [{"commands": ["python draft-only.py"]}]
        (workflow_dir / name).write_text(json.dumps(body), encoding="utf-8")
    manifest = workflow_dir / "workflow-input-manifest.json"
    retained_ref = "docs/prd/product.md"
    portable_task_map_ref = str(workflow_dir / "portable-task-map.json")
    skill_adapter_plan_ref = str(workflow_dir / "skill-adapter-plan.json")
    manifest.write_text(json.dumps({
        "acceptance_matrix_ref": str(workflow_dir / "acceptance-matrix.json"),
        "test_matrix_ref": str(workflow_dir / "test-matrix.json"),
        "real_e2e_matrix_ref": str(workflow_dir / "real-e2e-matrix.json"),
        "task_map_ref": portable_task_map_ref,
        "skill_adapter_plan_ref": skill_adapter_plan_ref,
        "artifact_refs": [
            str(workflow_dir / "acceptance-matrix.json"),
            {"path": "artifacts/workflow/wf/test-matrix.json"},
            {"ref": str(workflow_dir / "real-e2e-matrix.json")},
            portable_task_map_ref,
            {"path": skill_adapter_plan_ref},
            {"path": retained_ref},
        ],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")

    emitted = maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="prd.requested",
            actor="operator",
            payload={
                "pdd_id": "draft",
                "objective": "Deliver from the intake",
                "workflow_input_manifest_ref": str(manifest),
            },
        ),
        config=_light_config(),
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
    )

    assert emitted is not None
    assert emitted.payload["acceptance_matrix_ref"].endswith(
        "acceptance-matrix.json"
    )
    written = json.loads(
        (state_dir / "artifacts" / "draft" / "task_map.json").read_text()
    )
    task = written["tasks"][0]
    assert "acceptance_matrix_ref" not in task
    assert "test_matrix_ref" not in task
    assert "real_e2e_matrix_ref" not in task
    assert task["artifact_refs"] == [{"path": retained_ref}]
    assert written["artifact_refs"] == [{"path": retained_ref}]
    assert "verification" not in task
    assert written["required_plan_ports"] == [
        "requirement_spec",
        "goal_claim_set",
        "task_map",
        "planning_result",
    ]


def test_issue_entry_preserves_typed_stage_identity(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-light-identity}
spec:
  topology: light
  backend: mock
  issueRef: docs/issues/login-500.md
  targetRoot: .
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: issue-light-identity}
spec:
  version: "1.0"
  project: {name: issue-light-identity}
""")
    config = load_config(path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")

    emitted = maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="issue.requested",
            actor="zf-cli",
            payload={
                "pdd_id": "issue-default",
                "objective": "Fix login 500",
            },
        ),
        config=config,
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
    )

    assert emitted is not None
    assert emitted.payload["flow_kind"] == "issue"
    assert fanout_stage_matches_trigger_event(
        config.workflow.stages[0],
        emitted,
    )
    assert not fanout_stage_matches_trigger_event(
        SimpleNamespace(flow_kind="prd"),
        emitted,
    )


def test_entry_uses_config_quality_checks_without_matrix_commands(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = _light_config()
    config.quality_gates = {
        "static": SimpleNamespace(enabled=True, required_checks=["make verify"]),
    }
    log = EventLog(state_dir / "events.jsonl")

    maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="prd.requested",
            actor="operator",
            payload={"pdd_id": "default", "objective": "Deliver X"},
        ),
        config=config,
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
    )

    written = json.loads(
        (state_dir / "artifacts" / "default" / "task_map.json").read_text()
    )
    assert written["tasks"][0]["verification"] == "make verify"
    assert written["tasks"][0]["validation"]["commands"][0]["command"] == (
        "make verify"
    )


def test_entry_preserves_direct_requirement_refs_without_manifest(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")

    emitted = maybe_synthesize_light_task_map(
        event=ZfEvent(
            type="prd.requested",
            actor="operator",
            payload={
                "pdd_id": "direct",
                "workflow_run_id": "run-direct",
                "prd_ref": "docs/prd/direct.md",
                "objective": "Deliver direct requirement",
            },
        ),
        config=_light_config(),
        state_dir=state_dir,
        event_writer=EventWriter(log),
        events=[],
    )

    assert emitted is not None
    assert emitted.payload["workflow_run_id"] == "run-direct"
    assert emitted.payload["prd_ref"] == "docs/prd/direct.md"


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
                    payload={
                        "pdd_id": "default",
                        "workflow_run_id": "run-light-explicit",
                        "objective": "交付 X",
                    })
    emitted = maybe_synthesize_light_task_map(
        event=entry, config=_light_goal_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    assert emitted is not None
    started = [e for e in log.read_all() if e.type == "run.goal.started"]
    assert len(started) == 1
    payload = started[0].payload
    assert payload["run_id"] == "run-light-explicit"
    assert payload["workflow_run_id"] == "run-light-explicit"
    assert started[0].correlation_id == "run-light-explicit"
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
                    payload={
                        "pdd_id": "default",
                        "workflow_run_id": "run-light-explicit",
                        "objective": "交付 X",
                    })
    maybe_synthesize_light_task_map(
        event=entry, config=_light_goal_config(), state_dir=state_dir,
        event_writer=EventWriter(log), events=[],
    )
    judge = ZfEvent(type="judge.passed", actor="zf-cli",
                    correlation_id="run-light-explicit",
                    payload={
                        "workflow_run_id": "run-light-explicit",
                        "fanout_id": "f",
                        "stage_id": "s",
                        "status": "passed",
                    })
    completion = run_goal_completion_event(
        [*log.read_all(), judge], cause=judge,
    )
    assert completion is not None
    assert completion.type == "run.goal.completed"
    assert completion.payload["run_id"] == "run-light-explicit"


def test_entry_allows_distinct_workflow_runs_in_same_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    first = ZfEvent(
        type="prd.requested",
        actor="operator",
        payload={"pdd_id": "first", "workflow_run_id": "run-light-first"},
    )
    second = ZfEvent(
        type="prd.requested",
        actor="operator",
        payload={"pdd_id": "second", "workflow_run_id": "run-light-second"},
    )

    first_ready = maybe_synthesize_light_task_map(
        event=first,
        config=_light_goal_config(),
        state_dir=state_dir,
        event_writer=writer,
        events=[],
    )
    second_ready = maybe_synthesize_light_task_map(
        event=second,
        config=_light_goal_config(),
        state_dir=state_dir,
        event_writer=writer,
        events=log.read_all(),
    )

    assert first_ready is not None
    assert second_ready is not None
    assert second_ready.payload["workflow_run_id"] == "run-light-second"
    started_run_ids = {
        event.payload["run_id"]
        for event in log.read_all()
        if event.type == "run.goal.started"
    }
    assert started_run_ids == {"run-light-first", "run-light-second"}


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
