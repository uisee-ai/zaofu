"""V2:flowProfile 形状库 — 一个引用展开完整 refactor 流。"""

from __future__ import annotations

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.workflow_profiles import (
    WorkflowProfileError,
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
