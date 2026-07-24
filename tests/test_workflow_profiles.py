"""V2:flowProfile 形状库 — 一个引用展开完整 refactor 流。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.flow import draft_multi_kind_project_spec
from zf.core.config.loader import ConfigError, load_config
from zf.core.events.model import ZfEvent
from zf.core.verification.event_schema import EventSchemaRegistry
from zf.core.config.workflow_profiles import (
    WorkflowProfileError,
    expand_issue_flow,
    expand_prd_flow,
    expand_workflow_profile,
)
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin


def _flow_yaml(tmp_path, *, extra_spec="", body_extra=""):
    text = f"""\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {{name: demo-flow}}
spec:
  flowProfile: refactor-flow/v1
  lanes: 2
  assembly: {{task: DEMO-ASM-001}}
  budgets: {{maxReworkAttempts: 2, traceBudget: 4}}
  laneRoleTemplate: {{backend: mock}}
{extra_spec}---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {{name: demo}}
spec:
  version: "1.0"
  project: {{name: demo}}
{body_extra}"""
    p = tmp_path / "zf.yaml"
    p.write_text(text)
    return p


class TestExpansion:
    def test_multi_kind_flow_namespaces_roles_routes_and_dispatch(
        self, tmp_path, capsys,
    ):
        path = tmp_path / "zf.yaml"
        docs = draft_multi_kind_project_spec(
            backend="mock",
            project_name="multi-demo",
            project_root=tmp_path,
        )
        path.write_text(
            yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        cfg = load_config(path)
        assert "breaking overrides" not in capsys.readouterr().err
        assert len({role.name for role in cfg.roles}) == len(cfg.roles)
        assert {role.backend for role in cfg.roles} == {"mock"}
        assert set(cfg.workflow.kind_routes) >= {"issue", "prd", "feat", "refactor"}
        assert set(cfg.workflow.flow_metadata_by_kind) == {"issue", "prd", "refactor"}
        assert cfg.verification.event_schema.mode == "blocking"
        assert cfg.verification.report_evidence_gate == "fail_closed"
        assert set(cfg.workflow.dag.event_schemas_by_kind) == {
            "issue", "prd", "refactor",
        }

        registry = EventSchemaRegistry.from_config(cfg)
        assert registry.validate(ZfEvent(
            type="task_map.ready",
            actor="test",
            payload={"flow_kind": "issue", "task_map_ref": "task-map.json"},
        )) == []
        refactor_violations = registry.validate(ZfEvent(
            type="task_map.ready",
            actor="test",
            payload={"flow_kind": "refactor", "task_map_ref": "task-map.json"},
        ))
        assert {
            violation.field_path for violation in refactor_violations
        } >= {
            "payload.pdd_id",
            "payload.trace_id",
            "payload.source_commit",
            "payload.candidate_base_commit",
        }

        impl_stages = [
            stage for stage in cfg.workflow.stages
            if stage.trigger == "task_map.ready"
        ]
        assert {stage.flow_kind for stage in impl_stages} == {
            "issue", "prd", "refactor",
        }
        issue_event = ZfEvent(
            type="task_map.ready",
            actor="test",
            payload={"flow_kind": "issue"},
        )
        assert [
            stage.flow_kind for stage in impl_stages
            if FanoutCoordinationMixin._fanout_stage_matches_trigger_event(
                stage, issue_event,
            )
        ] == ["issue"]

    def test_one_reference_expands_full_flow(self, tmp_path):
        cfg = load_config(_flow_yaml(tmp_path))
        # scan/plan 段(profile stages)+ lane 链(G3 物化)全到位
        ids = [s.id for s in cfg.workflow.stages]
        assert ids[:2] == ["flow-scan", "flow-plan"]
        assert ids[2:] == ["flow-lanes-impl", "flow-lanes-review",
                           "flow-lanes-verify", "flow-lanes-final"]
        scan = next(s for s in cfg.workflow.stages if s.id == "flow-scan")
        assert scan.target_ref == ""
        assert any(
            "initial refactor scan" in item
            for item in scan.criteria.instructions
        )
        plan = next(s for s in cfg.workflow.stages if s.id == "flow-plan")
        assert plan.aggregate.synth_role == "plan-critic"
        assert any(
            "Synthesize the scan reports" in item
            for item in plan.criteria.instructions
        )

        assert any(
            "schema_version` exactly `task-map.v1" in item
            for item in plan.criteria.instructions
        )
        contract = plan.children[0].payload["refactor_contract"]
        assert contract["assembly_policy"] == "declared_task"
        assert contract["assembly_task_id"] == "DEMO-ASM-001"
        # roles:3 scan + synth + judge + 2 lanes × 3 stages 生成
        names = {r.name for r in cfg.roles}
        assert {"scan-contract", "scan-runtime", "scan-verification",
                "refactor-plan-synth", "judge-refactor",
                "dev-lane-0", "dev-lane-1", "review-lane-0",
                "verify-lane-1"} <= names
        # schema 契约一并引用
        assert len(cfg.workflow.dag.event_schemas) == 22
        # graph 编译零 STOP
        from zf.core.workflow.graph import compile_workflow_graph
        stops = [d for d in compile_workflow_graph(cfg).diagnostics
                 if d.get("severity") == "STOP"]
        assert stops == []

    def test_kind_routes_validate_stage_ids(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
version: "1.0"
project: {name: demo}
roles:
  - name: reader
    backend: mock
    role_kind: reader
workflow:
  kind_routes:
    issue:
      pattern_id: missing-stage
  stages:
    - id: issue-triage
      trigger: issue.requested
      topology: fanout_reader
      roles: [reader]
""", encoding="utf-8")

        with pytest.raises(ConfigError, match="unknown workflow stage"):
            load_config(path)

    def test_kind_routes_validate_alias_targets(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
version: "1.0"
project: {name: demo}
roles:
  - name: reader
    backend: mock
    role_kind: reader
workflow:
  kind_routes:
    feat:
      alias: prd
  stages:
    - id: prd-scan
      trigger: prd.requested
      topology: fanout_reader
      roles: [reader]
""", encoding="utf-8")

        with pytest.raises(ConfigError, match="alias references missing route"):
            load_config(path)

    def test_assembly_required(self, tmp_path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            "apiVersion: zaofu.dev/v1\nkind: RefactorFlow\n"
            "spec: {flowProfile: refactor-flow/v1, lanes: 1}\n"
            "---\napiVersion: zaofu.dev/v1\nkind: ZfConfig\n"
            "spec: {version: '1.0', project: {name: t}}\n"
        )
        with pytest.raises(ConfigError, match="assembly is required"):
            load_config(p)

    def test_unknown_profile_and_params_fail_closed(self):
        with pytest.raises(WorkflowProfileError, match="unknown flow profile"):
            expand_workflow_profile({"flowProfile": "refactor-flow/v99",
                                     "assembly": "none"})
        with pytest.raises(WorkflowProfileError, match="unknown param"):
            expand_workflow_profile({"flowProfile": "refactor-flow/v1",
                                     "assembly": "none", "lanesCount": 3})

    def test_three_source_guard_hand_stage_wins(self, tmp_path, capsys):
        body_extra = (
            "  workflow:\n"
            "    stages:\n"
            "    - id: my-scan\n"
            "      trigger: refactor.scan.requested\n"
            "      topology: fanout_reader\n"
            "      roles: [scan-contract]\n"
            "      aggregate: {mode: wait_for_all,\n"
            "                  success_event: zaofu.refactor.review.ready,\n"
            "                  failure_event: zaofu.refactor.plan.blocked}\n"
        )
        cfg = load_config(_flow_yaml(tmp_path, body_extra=body_extra))
        err = capsys.readouterr().err
        assert "三源守门" in err or "hand-written stage already covers" in err
        ids = [s.id for s in cfg.workflow.stages]
        assert "my-scan" in ids and "flow-scan" not in ids
        assert "flow-plan" in ids  # 未撞 trigger 的 profile stage 照常

    def test_hand_role_wins_over_profile_role(self, tmp_path):
        body_extra = (
            "  roles:\n"
            "  - {name: judge-refactor, backend: mock,\n"
            "     instance_id: judge-refactor, role_kind: reader,\n"
            "     stuck_threshold_seconds: 1234}\n"
        )
        cfg = load_config(_flow_yaml(tmp_path, body_extra=body_extra))
        judge = next(r for r in cfg.roles if r.name == "judge-refactor")
        assert judge.stuck_threshold_seconds == 1234  # 手写最高

    def test_scan_children_instruction_refs(self, tmp_path):
        extra = (
            "  scan:\n"
            "    children:\n"
            "    - {id: scan-contract, instructionRef: skills/scan-c.md}\n"
            "    - {id: scan-runtime, instructionRef: skills/scan-r.md}\n"
        )
        cfg = load_config(_flow_yaml(tmp_path, extra_spec=extra))
        scan = next(s for s in cfg.workflow.stages if s.id == "flow-scan")
        payloads = [c.payload for c in scan.children]
        assert payloads[0]["instruction_ref"] == "skills/scan-c.md"

    def test_v3_generates_goal_loop_without_review_stage(self, tmp_path):
        text = """\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 2
  assembly: {task: DEMO-ASM-001}
  laneRoleTemplate: {backend: mock}
  gapLoop: enabled
  verifyRescan: module_parity
  completionThreshold: close_p0_p1
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
"""
        path = tmp_path / "zf.yaml"
        path.write_text(text)
        cfg = load_config(path)
        ids = [s.id for s in cfg.workflow.stages]
        assert "flow-lanes-review" not in ids
        assert "flow-lanes-impl" in ids
        assert "flow-lanes-verify" in ids
        assert "flow-lanes-final" not in ids
        assert "flow-verify-bridge" in ids
        assert "flow-module-parity-scan" in ids
        assert "flow-final-judge" in ids
        assert ids.count("flow-final-judge") == 1
        verify_bridge = next(
            s for s in cfg.workflow.stages if s.id == "flow-verify-bridge"
        )
        assert any(
            "evidence_refs" in instruction
            for instruction in verify_bridge.criteria.instructions
        )
        scan = next(s for s in cfg.workflow.stages if s.id == "flow-scan")
        assert scan.target_ref == ""
        plan = next(s for s in cfg.workflow.stages if s.id == "flow-plan")
        assert plan.children
        contract = plan.children[0].payload["refactor_contract"]
        assert contract["schema_version"] == "refactor-plan-contract.v1"
        assert contract["assembly_policy"] == "declared_task"
        assert contract["assembly_task_id"] == "DEMO-ASM-001"
        assert contract["lane_count"] == 2
        pipeline = cfg.workflow.pipelines[0]
        assert pipeline.stage_transition == "stage_barrier"
        assert pipeline.schema_profile == "refactor-flow/v5"

    def test_v3_target_ref_is_explicit_and_not_objective_ref(self, tmp_path):
        text = """\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: none
  objectiveRef: docs/objective.md
  targetRef: HEAD
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
"""
        path = tmp_path / "zf.yaml"
        path.write_text(text)
        cfg = load_config(path)

        scan = next(s for s in cfg.workflow.stages if s.id == "flow-scan")
        assert scan.target_ref == "HEAD"
        plan = next(s for s in cfg.workflow.stages if s.id == "flow-plan")
        contract = plan.children[0].payload["refactor_contract"]
        assert contract["assembly_policy"] == "none"
        assert contract["assembly_task_id"] == ""
        assert contract["lane_count"] == 1
        assert cfg.workflow.flow_metadata["objective_ref"] == "docs/objective.md"

    def test_v3_unknown_param_fails_closed(self):
        with pytest.raises(WorkflowProfileError, match="unknown param"):
            expand_workflow_profile({
                "flowProfile": "refactor-flow/v3",
                "assembly": "none",
                "randomGapSetting": True,
            })

    def test_v3_role_defaults_and_skill_bundles(self, tmp_path):
        text = """\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: {task: DEMO-ASM-001}
  roleDefaults:
    backend: mock
    permission_mode: bypass
    stuck_threshold_seconds: 777
    spawn_ready_timeout_seconds: 88
  roleSkillBundles:
    scan-contract: [contract-scan]
    refactor-plan-synth: [plan-synth]
    impl: [impl-skill]
    verify: [verify-skill]
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
"""
        path = tmp_path / "zf.yaml"
        path.write_text(text)

        cfg = load_config(path)

        scan = next(role for role in cfg.roles if role.name == "scan-contract")
        dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
        verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
        assert scan.backend == "mock"
        assert scan.stuck_threshold_seconds == 777
        assert scan.spawn_ready_timeout_seconds == 88
        assert scan.skills == ["contract-scan"]
        assert dev.skills == ["impl-skill"]
        assert verify.skills == ["verify-skill"]
        assert cfg.workflow.flow_metadata["flow_kind"] == "refactor"
        assert cfg.workflow.flow_metadata["gap_loop"] == "enabled"
        assert cfg.workflow.flow_metadata["post_verify_discovery"] == "module_parity"

    def test_flow_role_defaults_normalize_and_reach_lane_roles(self):
        defaults = {
            "permissionMode": "bypass",
            "stuckThresholdSeconds": 901,
            "spawnReadyTimeoutSeconds": 241,
        }
        expansions = [
            expand_issue_flow({"roleDefaults": defaults}),
            expand_prd_flow({"roleDefaults": defaults}),
            expand_workflow_profile({
                "flowProfile": "refactor-flow/v3",
                "assembly": "none",
                "roleDefaults": defaults,
            }),
        ]

        for expansion in expansions:
            assert all(
                role["stuck_threshold_seconds"] == 901
                and role["spawn_ready_timeout_seconds"] == 241
                for role in expansion["roles"]
            )
            template = expansion["pipelines"][0]["lane_role_template"]
            assert template["permission_mode"] == "bypass"
            assert template["stuck_threshold_seconds"] == 901
            assert template["spawn_ready_timeout_seconds"] == 241

    def test_flow_role_defaults_reject_unknown_camel_case(self):
        with pytest.raises(WorkflowProfileError, match="unknown camelCase key"):
            expand_prd_flow({"roleDefaults": {"stuckTimeoutSeconds": 900}})

    def test_config_profile_flow_defaults_merge_refactor_role_skill_bundles(self, tmp_path):
        text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: refactor-defaults/v1}
spec:
  flow_defaults:
    refactor:
      roleSkillBundles:
        impl: [using-agent-skills, test-driven-development]
        verify: [code-review-and-quality]
---
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: {task: DEMO-ASM-001}
  laneRoleTemplate: {backend: mock}
  roleSkillBundles:
    impl: [zf-harness-done-contract]
    verify: []
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  uses: [refactor-defaults/v1]
  version: "1.0"
  project: {name: demo}
"""
        path = tmp_path / "zf.yaml"
        path.write_text(text)

        cfg = load_config(path)

        dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
        verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
        assert dev.skills == [
            "using-agent-skills",
            "test-driven-development",
            "zf-harness-done-contract",
        ]
        assert verify.skills == []

    def test_verify_gap_producer_default(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: none
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")
        refactor = load_config(path)
        metadata = refactor.workflow.flow_metadata
        assert metadata["post_verify_discovery"] == "module_parity"
        assert any(stage.id == "flow-module-parity-scan" for stage in refactor.workflow.stages)

        issue = expand_issue_flow({"entryTrigger": "issue.requested"})
        prd = expand_prd_flow({"entryTrigger": "prd.requested"})

        assert issue["metadata"]["post_verify_discovery"] == "regression_impact"
        assert issue["pipelines"][0]["lane_count"] == 1
        assert prd["metadata"]["post_verify_discovery"] == "product_completeness"

    def test_issue_flow_generates_canonical_bugfix_chain(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 2
  backend: mock
  issueRef: backlogs/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

        cfg = load_config(path)

        ids = [stage.id for stage in cfg.workflow.stages]
        assert ids == [
            "issue-triage",
            "issue-post-verify-discovery",
            "issue-lanes-impl",
            "issue-lanes-verify",
            "issue-lanes-final",
        ]
        assert next(stage for stage in cfg.workflow.stages if stage.id == "issue-triage").target_ref == "HEAD"
        assert next(
            stage for stage in cfg.workflow.stages if stage.id == "issue-triage"
        ).aggregate.synth_role == "plan-critic"
        discovery = next(
            stage for stage in cfg.workflow.stages
            if stage.id == "issue-post-verify-discovery"
        )
        final = next(
            stage for stage in cfg.workflow.stages
            if stage.id == "issue-lanes-final"
        )
        assert discovery.trigger == "flow.discovery.requested"
        assert final.trigger == "flow.goal.closed"
        assert final.aggregate.success_event == "goal.closure.synthesized"
        assert final.aggregate.failure_event == "goal.closure.synthesis.failed"
        assert any(
            "issue triage" in item
            for item in cfg.workflow.stages[0].criteria.instructions
        )
        assert any(
            "schema_version` exactly `task-map.v1" in item
            for item in cfg.workflow.stages[0].criteria.instructions
        )
        assert any(
            "after implementation verification" in item
            for item in discovery.criteria.instructions
        )
        impl = next(
            stage for stage in cfg.workflow.stages
            if stage.id == "issue-lanes-impl"
        )
        assert impl.synthesize_canonical_tasks is True
        names = {role.name for role in cfg.roles}
        assert {"issue-triage", "plan-critic", "fix-lane-0", "fix-lane-1", "verify-lane-0", "judge-issue"} <= names
        assert cfg.workflow.flow_metadata["flow_kind"] == "issue"
        assert cfg.workflow.flow_metadata["quality_floor"] == "issue-regression"
        assert cfg.workflow.flow_metadata["post_verify_discovery"] == "regression_impact"
        assert cfg.workflow.pipelines[0].stage_transition == "stage_barrier"
        assert cfg.workflow.pipelines[0].schema_profile == "canonical-dag/v8"
        assert cfg.goal.enabled is True

    def test_prd_flow_generates_canonical_build_chain(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: prd-defaults/v1}
spec:
  flow_defaults:
    prd:
      roleSkillBundles:
        scan: [spec-driven-development]
        planner: [planning-and-task-breakdown]
        impl: [test-driven-development, zf-harness-done-contract]
        verify: [zf-harness-verification-checklist]
        judge-prd: [shipping-and-launch]
---
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-demo}
spec:
  lanes: 1
  backend: mock
  prdRef: docs/prd.md
  targetRoot: app
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  uses: [prd-defaults/v1]
  version: "1.0"
  project: {name: demo}
""")

        cfg = load_config(path)

        ids = [stage.id for stage in cfg.workflow.stages]
        assert ids == [
            "prd-scan",
            "prd-plan",
            "prd-post-verify-discovery",
            "prd-lanes-impl",
            "prd-lanes-verify",
            "prd-lanes-final",
        ]
        assert next(stage for stage in cfg.workflow.stages if stage.id == "prd-scan").target_ref == "HEAD"
        assert next(
            stage for stage in cfg.workflow.stages if stage.id == "prd-plan"
        ).aggregate.synth_role == "plan-critic"
        discovery = next(
            stage for stage in cfg.workflow.stages
            if stage.id == "prd-post-verify-discovery"
        )
        final = next(
            stage for stage in cfg.workflow.stages
            if stage.id == "prd-lanes-final"
        )
        assert discovery.trigger == "flow.discovery.requested"
        assert final.trigger == "flow.goal.closed"
        assert final.aggregate.success_event == "goal.closure.synthesized"
        assert final.aggregate.failure_event == "goal.closure.synthesis.failed"
        scan = next(stage for stage in cfg.workflow.stages if stage.id == "prd-scan")
        assert any(
            "initial PRD scan" in item
            for item in scan.criteria.instructions
        )
        plan = next(stage for stage in cfg.workflow.stages if stage.id == "prd-plan")
        assert any(
            "machine-readable task_map" in item
            for item in plan.criteria.instructions
        )
        assert any(
            "schema_version` exactly `task-map.v1" in item
            for item in plan.criteria.instructions
        )
        names = {role.name for role in cfg.roles}
        assert {"product-scan", "tech-scan", "planner", "dev-lane-0", "verify-lane-0", "judge-prd"} <= names
        product_scan = next(role for role in cfg.roles if role.name == "product-scan")
        planner = next(role for role in cfg.roles if role.name == "planner")
        dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
        verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
        judge = next(role for role in cfg.roles if role.name == "judge-prd")
        assert "spec-driven-development" in product_scan.skills
        assert "planning-and-task-breakdown" in planner.skills
        assert "test-driven-development" in dev.skills
        assert "zf-harness-done-contract" in dev.skills
        assert "zf-harness-verification-checklist" in verify.skills
        assert "shipping-and-launch" in judge.skills
        assert cfg.workflow.flow_metadata["flow_kind"] == "prd"
        assert cfg.workflow.flow_metadata["delivery_policy"] == "report_and_demo"
        assert cfg.workflow.flow_metadata["post_verify_discovery"] == "product_completeness"
        assert cfg.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"] == {}
        assert cfg.workflow.pipelines[0].stage_transition == "stage_barrier"
        assert cfg.workflow.pipelines[0].schema_profile == "canonical-dag/v8"
        assert cfg.goal.enabled is True

    def test_prd_flow_pins_semantic_submit_profile_modes(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-demo}
spec:
  topology: light
  backend: mock
  semanticSubmitProfiles:
    thin-judge-goal-closure: blocking
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

        cfg = load_config(path)

        assert cfg.workflow.flow_metadata["result_protocol"] == {
            "mode": "blocking",
            "semantic_submit_profiles": {
                "thin-judge-goal-closure": "blocking",
            },
        }

    def test_prd_flow_rejects_invalid_semantic_submit_mode(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
spec:
  topology: light
  semanticSubmitProfiles:
    thin-judge-goal-closure: permissive
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec: {project: {name: demo}}
""")

        with pytest.raises(ConfigError, match="mode must be shadow or blocking"):
            load_config(path)

    def test_prd_flow_role_skill_bundles_override_defaults(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-demo}
spec:
  lanes: 1
  backend: mock
  roleSkillBundles:
    impl: [custom-impl]
    verify: [custom-verify]
    judge-prd: []
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

        cfg = load_config(path)

        dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
        verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
        judge = next(role for role in cfg.roles if role.name == "judge-prd")
        assert dev.skills == ["custom-impl"]
        assert verify.skills == ["custom-verify"]
        assert judge.skills == []

    # 2026-07-08 agent-skills 退役:controller bundle 只允许仓内
    # skills/(zf-*)与 yoke/ 名。外部基线名回流即红。
    _RETIRED_AGENT_SKILLS = {
        "using-agent-skills",
        "test-driven-development",
        "incremental-implementation",
        "debugging-and-error-recovery",
        "source-driven-development",
        "code-review-and-quality",
        "spec-driven-development",
        "context-engineering",
        "planning-and-task-breakdown",
        "shipping-and-launch",
        "code-simplification",
    }

    def test_prod_controller_flows_enable_writer_workdirs(self):
        root = Path(__file__).parent.parent
        for relative in (
            "examples/prod/controller/prd-fanout-v3.yaml",
            "examples/prod/controller/issue-fanout-v3.yaml",
            "examples/prod/controller/refactor-lane-v3.yaml",
        ):
            cfg = load_config(root / relative)
            assert cfg.runtime.workdirs.enabled is True, relative
            assert cfg.runtime.workdirs.mode == "worktree", relative
            assert all(
                role.permission_mode == "bypass"
                and role.stuck_threshold_seconds == 900
                and role.spawn_ready_timeout_seconds == 240
                for role in cfg.roles
            ), relative
            assert any(
                source.name == "zaofu-skills"
                and source.path == "../../../skills"
                for source in cfg.skill_sources
            ), relative
            assert not any(
                source.name == "agent-skills" for source in cfg.skill_sources
            ), f"{relative}: agent-skills 外部基线已退役,不应再声明为 source"
            for role in cfg.roles:
                leaked = self._RETIRED_AGENT_SKILLS & set(role.skills or [])
                assert not leaked, (
                    f"{relative}:{role.name} 仍引用已退役 agent-skills 名 {sorted(leaked)}"
                )
            if relative == "examples/prod/controller/issue-fanout-v3.yaml":
                triage = next(role for role in cfg.roles if role.name == "issue-triage")
                fix = next(role for role in cfg.roles if role.name == "fix-lane-0")
                verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
                assert "zf-issue-plan-synth" in triage.skills
                assert "debugging-triage" in triage.skills
                assert "zf-yoke-dev-worker-role-context" in fix.skills
                assert "zf-harness-done-contract" not in fix.skills
                assert "zf-verify-gap-producer-contract" not in verify.skills
                assert "zf-yoke-test-evaluator-role-context" in verify.skills
            if relative == "examples/prod/controller/prd-fanout-v3.yaml":
                scan = next(role for role in cfg.roles if role.name == "product-scan")
                planner = next(role for role in cfg.roles if role.name == "planner")
                dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
                verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
                assert "zf-prd-plan-synth" in scan.skills
                assert "zf-yoke-planner-role-context" in scan.skills
                assert "grill" not in scan.skills
                assert "source-verification" in scan.skills
                assert "context-hygiene" in scan.skills
                assert "zf-yoke-planner-role-context" in planner.skills
                assert "zf-plan-task-map-contract" not in planner.skills
                assert "zf-yoke-dev-worker-role-context" in dev.skills
                assert "zf-verify-gap-producer-contract" not in verify.skills
            if relative == "examples/prod/controller/refactor-lane-v3.yaml":
                scan = next(role for role in cfg.roles if role.name == "scan-contract")
                plan = next(role for role in cfg.roles if role.name == "refactor-plan-synth")
                dev = next(role for role in cfg.roles if role.name == "dev-lane-0")
                verify = next(role for role in cfg.roles if role.name == "verify-lane-0")
                module = next(role for role in cfg.roles if role.name == "module-parity-scan")
                assert "zf-refactor-plan-synth" in scan.skills
                assert "source-verification" in scan.skills
                assert "zf-plan-task-map-contract" not in plan.skills
                assert "zf-yoke-planner-role-context" in plan.skills
                assert "zf-yoke-dev-worker-role-context" in dev.skills
                assert "zf-harness-done-contract" not in dev.skills
                assert "zf-verify-rescan-replan" not in verify.skills
                assert "zf-verify-gap-producer-contract" not in verify.skills
                assert "zf-yoke-test-evaluator-role-context" in verify.skills
                assert "zf-yoke-test-evaluator-role-context" in module.skills
                assert "verify-review" not in module.skills
                assert "zf-verify-rescan-replan" in module.skills
                assert "zf-goal-closure-replan-contract" in module.skills
                assert "zf-verify-gap-producer-contract" not in module.skills
                assert "zf-provider-contract-parity" not in module.skills

    def test_issue_prd_flow_unknown_params_fail_closed(self, tmp_path):
        issue = tmp_path / "issue.yaml"
        issue.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
spec: {lanes: 1, surprise: true}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec: {project: {name: demo}}
""")
        with pytest.raises(ConfigError, match="IssueFlow: unknown param"):
            load_config(issue)

        prd = tmp_path / "prd.yaml"
        prd.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
spec: {lanes: 1, surprise: true}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec: {project: {name: demo}}
""")
        with pytest.raises(ConfigError, match="PrdFlow: unknown param"):
            load_config(prd)

    def test_product_flow_accepts_and_validates_artifact_package_mode(self, tmp_path):
        config = tmp_path / "prd.yaml"
        config.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
spec: {topology: light, lanes: 1, artifactPackageMode: blocking}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec: {project: {name: demo}}
""")
        loaded = load_config(config)
        assert loaded.workflow.flow_metadata["artifact_package"]["mode"] == "blocking"

        config.write_text(config.read_text().replace("blocking", "surprise"))
        with pytest.raises(ConfigError, match="artifactPackageMode"):
            load_config(config)
