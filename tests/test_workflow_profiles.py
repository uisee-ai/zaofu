"""V2:flowProfile 形状库 — 一个引用展开完整 refactor 流。"""

from __future__ import annotations

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.workflow_profiles import (
    WorkflowProfileError,
    expand_issue_flow,
    expand_prd_flow,
    expand_workflow_profile,
)


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
    def test_one_reference_expands_full_flow(self, tmp_path):
        cfg = load_config(_flow_yaml(tmp_path))
        # scan/plan 段(profile stages)+ lane 链(G3 物化)全到位
        ids = [s.id for s in cfg.workflow.stages]
        assert ids[:2] == ["flow-scan", "flow-plan"]
        assert ids[2:] == ["flow-lanes-impl", "flow-lanes-review",
                           "flow-lanes-verify", "flow-lanes-final"]
        scan = next(s for s in cfg.workflow.stages if s.id == "flow-scan")
        assert scan.target_ref == ""
        plan = next(s for s in cfg.workflow.stages if s.id == "flow-plan")
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
        assert "flow-verify-bridge" in ids
        assert "flow-module-parity-scan" in ids
        assert "flow-final-judge" in ids
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
        assert pipeline.stage_transition == "per_lane"
        assert pipeline.schema_profile == "refactor-flow/v2"

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
            "issue-lanes-impl",
            "issue-lanes-verify",
            "issue-lanes-final",
        ]
        names = {role.name for role in cfg.roles}
        assert {"issue-triage", "fix-lane-0", "fix-lane-1", "verify-lane-0", "judge-issue"} <= names
        assert cfg.workflow.flow_metadata["flow_kind"] == "issue"
        assert cfg.workflow.flow_metadata["quality_floor"] == "issue-regression"
        assert cfg.workflow.flow_metadata["post_verify_discovery"] == "regression_impact"
        assert cfg.workflow.pipelines[0].stage_transition == "per_lane"
        assert cfg.workflow.pipelines[0].schema_profile == "canonical-dag/v2"

    def test_prd_flow_generates_canonical_build_chain(self, tmp_path):
        path = tmp_path / "zf.yaml"
        path.write_text("""\
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
  version: "1.0"
  project: {name: demo}
""")

        cfg = load_config(path)

        ids = [stage.id for stage in cfg.workflow.stages]
        assert ids == [
            "prd-scan",
            "prd-plan",
            "prd-lanes-impl",
            "prd-lanes-verify",
            "prd-lanes-final",
        ]
        names = {role.name for role in cfg.roles}
        assert {"product-scan", "tech-scan", "planner", "dev-lane-0", "verify-lane-0", "judge-prd"} <= names
        assert cfg.workflow.flow_metadata["flow_kind"] == "prd"
        assert cfg.workflow.flow_metadata["delivery_policy"] == "report_and_demo"
        assert cfg.workflow.flow_metadata["post_verify_discovery"] == "product_completeness"
        assert cfg.workflow.pipelines[0].stage_transition == "per_lane"
        assert cfg.workflow.pipelines[0].schema_profile == "canonical-dag/v2"

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
