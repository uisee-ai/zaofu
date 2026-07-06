"""doc 90 A5:envelope camelCase → canonical snake_case 归一化。"""

from __future__ import annotations

import pytest

from zf.core.config.kind_envelope import (
    KindEnvelopeError,
    normalize_lane_pipeline_external,
)
from zf.core.workflow.lane_pipeline import parse_lane_pipeline


def _external_spec(**overrides) -> dict:
    spec = {
        "id": "cj-min-refactor",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "taskSource": {"taskMapRef": ".zf/artifacts/F-1/task_map.json"},
        "affinityKey": "affinity_tag",
        "lanes": 5,
        "overflow": "none",
        "reworkDefaults": {"maxAttempts": 2, "traceBudget": 6},
        "requireArtifactDigests": True,
        "assembly": {"task": "CJMIN-ASSEMBLY-001"},
        "stages": [
            {"id": "impl",
             "terminal": {"success": "dev.build.done", "failure": "dev.failed"}},
            {"id": "review",
             "rework": {"to": "impl", "feedbackArtifact": "required"}},
            {"id": "verify",
             "rework": {"to": "impl", "feedbackArtifact": "required"}},
        ],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-refactor",
            "success": "judge.passed",
            "failure": "judge.failed",
        },
        "laneRoleTemplate": {
            "backend": "codex",
            "stuckThresholdSeconds": 900,
            "skillsByStage": {"impl": ["code-refactoring"]},
        },
        "schemaProfile": "refactor-flow/v1",
    }
    spec.update(overrides)
    return spec


class TestRoundTrip:
    def test_doc90_section6_shape_round_trips_into_canonical_parser(self):
        canonical = normalize_lane_pipeline_external(_external_spec())
        spec = parse_lane_pipeline(canonical)
        assert spec.pipeline_id == "cj-min-refactor"
        assert spec.task_map_ref == ".zf/artifacts/F-1/task_map.json"
        assert spec.affinity_key == "affinity_tag"
        assert spec.lane_count == 5
        assert spec.max_rework_attempts == 2
        assert spec.trace_budget == 6
        assert spec.require_artifact_digests is True
        assert spec.assembly_task == "CJMIN-ASSEMBLY-001"
        # 显式 terminal 逃生门保留;缺省走 A3 约定
        assert spec.stages[0].success_event == "dev.build.done"
        assert spec.stages[1].success_event == "review.child.completed"
        assert spec.stages[1].rework_to == "impl"
        assert spec.stages[1].feedback_artifact == "required"
        assert spec.lane_role_template.backend == "codex"
        assert spec.lane_role_template.stuck_threshold_seconds == 900
        assert spec.lane_role_template.skills_by_stage["impl"] == (
            "code-refactoring",
        )
        assert spec.schema_profile == "refactor-flow/v1"

    def test_snake_case_passthrough_untouched(self):
        canonical_in = {
            "id": "p", "kind": "lane_pipeline", "trigger": "t",
            "affinity_key": "affinity_tag", "lane_count": 2,
            "assembly": "none",
            "stages": [{"id": "impl"}],
        }
        out = normalize_lane_pipeline_external(dict(canonical_in))
        assert out["affinity_key"] == "affinity_tag"
        assert out["lane_count"] == 2
        spec = parse_lane_pipeline(out)
        assert spec.lane_count == 2

    def test_barriers_stage_transition_camelcase_normalizes(self):
        canonical = normalize_lane_pipeline_external(_external_spec(
            barriers={
                "stageTransition": "per_lane",
                "final": "all_lanes_verified",
            },
        ))
        assert canonical["barriers"] == {
            "stage_transition": "per_lane",
            "final": "all_lanes_verified",
        }
        spec = parse_lane_pipeline(canonical)
        assert spec.stage_transition == "per_lane"
        assert spec.final_barrier == "all_lanes_verified"


class TestFailClosed:
    def test_unknown_camelcase_key_rejected_not_dropped(self):
        with pytest.raises(KindEnvelopeError, match="lanesCount"):
            normalize_lane_pipeline_external(_external_spec(lanesCount=4))

    def test_unknown_nested_camelcase_rejected(self):
        spec = _external_spec()
        spec["laneRoleTemplate"]["stuckThresholdSecond"] = 1  # 拼错
        with pytest.raises(KindEnvelopeError, match="stuckThresholdSecond"):
            normalize_lane_pipeline_external(spec)

    def test_unknown_barrier_camelcase_rejected(self):
        spec = _external_spec(
            barriers={"stageTransitions": "per_lane"},  # 拼错
        )
        with pytest.raises(KindEnvelopeError, match="stageTransitions"):
            normalize_lane_pipeline_external(spec)

    def test_canonical_discipline_not_relaxed(self):
        # 归一化产物进 canonical parser:未知 snake 键仍 fail-closed
        canonical = normalize_lane_pipeline_external(_external_spec())
        canonical["bogus_key"] = 1
        from zf.core.workflow.lane_pipeline import LanePipelineSpecError
        with pytest.raises(LanePipelineSpecError, match="unknown key"):
            parse_lane_pipeline(canonical)

    def test_rework_alias_nested_typo_rejected(self):
        spec = _external_spec()
        spec["stages"][1]["rework"] = {"too": "impl"}  # 'to' 拼错且无大写
        canonical = normalize_lane_pipeline_external(spec)
        # 小写拼错落到 canonical 层拒(on_failure 未知键)
        from zf.core.workflow.lane_pipeline import LanePipelineSpecError
        with pytest.raises(LanePipelineSpecError, match="unknown key"):
            parse_lane_pipeline(canonical)


class TestEnvelopeStream:
    """doc 90 B1:`---` 多文档 kind 路由 → 单一 ZfConfig。"""

    def _envelope_yaml(self, tmp_path):
        text = """\
apiVersion: zaofu.dev/v1
kind: LanePipeline
metadata: {name: cj-min-refactor}
spec:
  trigger: task_map.ready
  taskSource: {taskMapRef: ".zf/artifacts/F-1/task_map.json"}
  affinityKey: affinity_tag
  lanes: 2
  assembly: none
  stages:
  - {id: impl, rolePattern: "dev-lane-{lane}"}
  - id: review
    rework: {to: impl, feedbackArtifact: required}
  laneRoleTemplate: {backend: codex}
  schemaProfile: refactor-flow/v1
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: cj-min}
spec:
  version: "1.0"
  project: {name: cj-min}
  roles:
  - {name: judge-refactor, backend: mock, instance_id: judge-refactor, role_kind: reader}
"""
        p = tmp_path / "zf.yaml"
        p.write_text(text)
        return p

    def test_multi_doc_routes_into_single_config(self, tmp_path):
        from zf.core.config.loader import load_config
        cfg = load_config(self._envelope_yaml(tmp_path))
        assert len(cfg.workflow.pipelines) == 1
        spec = cfg.workflow.pipelines[0]
        assert spec.pipeline_id == "cj-min-refactor"  # metadata.name 缺省
        assert spec.lane_count == 2
        # 生成 role + profile schema 全链生效
        names = {r.name for r in cfg.roles}
        assert {"dev-lane-0", "dev-lane-1", "review-lane-0",
                "review-lane-1", "judge-refactor"} <= names
        assert len(cfg.workflow.dag.event_schemas) == 22

    def test_equivalent_to_single_doc_form(self, tmp_path):
        import yaml
        from zf.core.config.loader import load_config
        cfg_env = load_config(self._envelope_yaml(tmp_path))
        single = {
            "version": "1.0",
            "project": {"name": "cj-min"},
            "roles": [{"name": "judge-refactor", "backend": "mock",
                       "instance_id": "judge-refactor",
                       "role_kind": "reader"}],
            "workflow": {"pipelines": [{
                "id": "cj-min-refactor", "kind": "lane_pipeline",
                "trigger": "task_map.ready",
                "task_source": {"task_map_ref": ".zf/artifacts/F-1/task_map.json"},
                "affinity_key": "affinity_tag", "lane_count": 2,
                "assembly": "none",
                "stages": [
                    {"id": "impl", "role_pattern": "dev-lane-{lane}"},
                    {"id": "review",
                     "on_failure": {"rework_to": "impl",
                                    "feedback_artifact": "required"}},
                ],
                "lane_role_template": {"backend": "codex"},
                "schema_profile": "refactor-flow/v1",
            }]},
        }
        p2 = tmp_path / "single.yaml"
        p2.write_text(yaml.dump(single))
        cfg_single = load_config(p2)
        assert (
            sorted((r.name, r.role_kind, tuple(r.publishes))
                   for r in cfg_env.roles)
            == sorted((r.name, r.role_kind, tuple(r.publishes))
                      for r in cfg_single.roles)
        )
        assert cfg_env.workflow.dag.event_schemas == (
            cfg_single.workflow.dag.event_schemas
        )

    def test_schema_profile_kind_registers_local_profile(self, tmp_path):
        from zf.core.config.loader import load_config
        text = """\
apiVersion: zaofu.dev/v1
kind: SchemaProfile
metadata: {name: my-flow/v1}
spec:
  events:
    my.event.done: {required: [task_id, status]}
---
apiVersion: zaofu.dev/v1
kind: LanePipeline
metadata: {name: p}
spec:
  trigger: t
  affinityKey: affinity_tag
  lanes: 1
  assembly: none
  stages: [{id: impl}]
  schemaProfile: my-flow/v1
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: t}
spec:
  version: "1.0"
  project: {name: t}
"""
        p = tmp_path / "zf.yaml"
        p.write_text(text)
        cfg = load_config(p)
        assert cfg.workflow.dag.event_schemas == {
            "my.event.done": {"required": ["task_id", "status"]},
        }

    def test_unknown_kind_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError, load_config
        p = tmp_path / "zf.yaml"
        p.write_text(
            "apiVersion: zaofu.dev/v1\nkind: Deployment\nspec: {}\n---\n"
            "apiVersion: zaofu.dev/v1\nkind: ZfConfig\n"
            "spec: {version: '1.0', project: {name: t}}\n"
        )
        with pytest.raises(ConfigError, match="unknown kind 'Deployment'"):
            load_config(p)

    def test_unknown_api_version_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError, load_config
        p = tmp_path / "zf.yaml"
        p.write_text(
            "apiVersion: zaofu.dev/v2\nkind: ZfConfig\n"
            "spec: {version: '1.0', project: {name: t}}\n"
        )
        with pytest.raises(ConfigError, match="apiVersion"):
            load_config(p)

    def test_two_zfconfig_docs_fail_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError, load_config
        doc = ("apiVersion: zaofu.dev/v1\nkind: ZfConfig\n"
               "spec: {version: '1.0', project: {name: t}}\n")
        p = tmp_path / "zf.yaml"
        p.write_text(doc + "---\n" + doc)
        with pytest.raises(ConfigError, match="exactly one"):
            load_config(p)

    def test_legacy_single_doc_is_implicit_zfconfig(self, tmp_path):
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text("version: '1.0'\nproject: {name: legacy}\n")
        cfg = load_config(p)
        assert cfg.project.name == "legacy"
