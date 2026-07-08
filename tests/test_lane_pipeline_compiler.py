"""doc 88 P0(0327):LanePipelineSpec inspect-only compiler。

Hermes/cj-min 样例闭合编译 + 6 类 fail-closed STOP + loader 往返 +
runtime 不接线证明。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.workflow.lane_pipeline import (
    LanePipelineSpecError,
    compile_lane_pipeline,
    lane_pipeline_inspection,
    parse_lane_pipeline,
)

_REPO = Path(__file__).resolve().parent.parent


def _hermes_raw(**overrides) -> dict:
    raw = {
        "id": "cj-min-refactor",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "task_source": {"task_map_ref": ".zf/artifacts/F-1/task_map.json"},
        "affinity_key": "affinity_tag",
        "lane_count": 4,
        "overflow": "first_released_lane",
        "max_rework_attempts": 2,
        "require_artifact_digests": True,
        "stages": [
            {
                "id": "impl",
                "role_pattern": "dev-lane-{lane}",
                "terminal": {
                    "success": "dev.child.completed",
                    "failure": "dev.child.failed",
                },
            },
            {
                "id": "review",
                "role_pattern": "review-lane-{lane}",
                "terminal": {
                    "success": "review.child.completed",
                    "failure": "review.child.failed",
                },
                "on_failure": {"rework_to": "impl", "feedback_artifact": "required"},
            },
            {
                "id": "verify",
                "role_pattern": "verify-lane-{lane}",
                "terminal": {
                    "success": "verify.child.completed",
                    "failure": "verify.child.failed",
                },
                "on_failure": {"rework_to": "impl", "feedback_artifact": "required"},
            },
        ],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-refactor",
            "success": "judge.passed",
            "failure": "judge.failed",
        },
        # A6: greenfield 无主地带显式归属(缺失 = inspect STOP)
        "assembly": {"task": "CJMIN-ASSEMBLY-001"},
    }
    raw.update(overrides)
    return raw


def _hermes_roles() -> list[RoleConfig]:
    roles = []
    for stage in ("dev", "review", "verify"):
        for lane in range(4):
            roles.append(RoleConfig(
                name=f"{stage}-lane-{lane}", backend="mock",
                instance_id=f"{stage}-lane-{lane}",
            ))
    roles.append(RoleConfig(
        name="judge-refactor", backend="mock", instance_id="judge-refactor",
    ))
    return roles


class TestHermesSampleCompiles:
    def test_sample_pipeline_has_zero_stops(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, diags = compile_lane_pipeline(spec, _hermes_roles())
        stops = [d for d in diags if d["severity"] == "STOP"]
        assert stops == [], f"Hermes sample must compile closed: {stops}"
        # G6 要害:历史 STOP 类在编译产物里不可能出现
        assert not any(
            "invalid_rework_target" in d["kind"] or "missing_rework_route" in d["kind"]
            for d in diags
        )

    def test_contract_carries_rev1_semantics(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        assert contract["lane_release_on"] == ["verified", "blocked", "superseded"]
        assert contract["attempt_binding"] == "unique_derivation_or_stale"
        assert contract["trace_budget"] == 6  # 2 attempts × 3 stages(G1 加性封顶)
        assert contract["final_gate"]["blocked_tasks_block_final"] is True
        assert contract["recovery_owner"] == "doc87-reconciler"

    def test_same_lane_handoff_binding(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        review = next(s for s in contract["stages"] if s["stage_id"] == "review")
        assert review["role_selector"]["lanes"]["lane3"] == "review-lane-3"
        assert review["failure_target"] == "impl"
        assert review["next_stage"] == "verify"


class TestStageTransitionBarriers:
    def test_default_stage_barrier_contract_preserves_existing_shape(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, diags = compile_lane_pipeline(spec, _hermes_roles())
        assert [d for d in diags if d["severity"] == "STOP"] == []
        assert spec.stage_transition == "stage_barrier"
        assert spec.final_barrier == ""
        assert contract["stage_transition"] == "stage_barrier"
        assert contract["handoff_contract"] == {
            "mode": "stage_barrier",
            "emitter": "stage_aggregate",
        }
        impl = next(s for s in contract["stages"] if s["stage_id"] == "impl")
        assert impl["transition_scope"] == "stage_barrier"
        assert "handoff_success_event" not in impl

    def test_per_lane_contract_declares_kernel_handoff_events(self):
        spec = parse_lane_pipeline(_hermes_raw(
            barriers={
                "stage_transition": "per_lane",
                "final": "all_lanes_verified",
            },
        ))
        contract, diags = compile_lane_pipeline(spec, _hermes_roles())
        assert [d for d in diags if d["severity"] == "STOP"] == []
        assert spec.stage_transition == "per_lane"
        assert spec.final_barrier == "all_lanes_verified"
        assert contract["stage_transition"] == "per_lane"
        assert contract["final_gate"]["barrier"] == "all_lanes_verified"
        handoff = contract["handoff_contract"]
        assert handoff["mode"] == "per_lane"
        assert handoff["emitter"] == "kernel"
        assert handoff["events"] == {
            "success": "lane.stage.completed",
            "failure": "lane.stage.failed",
        }
        assert handoff["identity_fields"] == [
            "pipeline_id", "task_id", "attempt_id", "stage_slot", "lane_id",
        ]
        assert handoff["dispatch"]["scope"] == "same_lane"
        assert handoff["currentness_gate"]["requires_handoff_ref"] is True
        impl = next(s for s in contract["stages"] if s["stage_id"] == "impl")
        assert impl["transition_scope"] == "per_lane"
        assert impl["handoff_success_event"] == "lane.stage.completed"

    def test_per_lane_defaults_final_barrier(self):
        spec = parse_lane_pipeline(_hermes_raw(
            barriers={"stage_transition": "per_lane"},
        ))
        assert spec.final_barrier == "all_lanes_verified"

    def test_unknown_barrier_key_fails_at_parse(self):
        with pytest.raises(LanePipelineSpecError, match="unknown key"):
            parse_lane_pipeline(_hermes_raw(
                barriers={"stage_transiton": "per_lane"},
            ))

    def test_invalid_stage_transition_fails_closed_at_compile(self):
        spec = parse_lane_pipeline(_hermes_raw(
            barriers={"stage_transition": "stream_everything"},
        ))
        _, diags = compile_lane_pipeline(spec, _hermes_roles())
        assert "lane_pipeline_invalid_stage_transition" in {
            d["kind"] for d in diags if d["severity"] == "STOP"
        }


class TestFailClosedStops:
    def _stops(self, raw, roles=None):
        spec = parse_lane_pipeline(raw)
        _, diags = compile_lane_pipeline(spec, roles or _hermes_roles())
        return {d["kind"] for d in diags if d["severity"] == "STOP"}

    def test_role_pattern_expansion_missing_role(self):
        roles = [r for r in _hermes_roles() if r.instance_id != "review-lane-2"]
        assert "lane_pipeline_role_missing" in self._stops(_hermes_raw(), roles)

    def test_missing_terminal_defaults_by_convention(self):
        # A3:缺省 terminal 由约定铸造 {stage}.child.completed/failed,
        # 不再 STOP(STOP 降级为不可达安全网);显式 terminal 为逃生门。
        raw = _hermes_raw()
        raw["stages"][0]["terminal"] = {"failure": "dev.child.failed"}
        spec = parse_lane_pipeline(raw)
        assert spec.stages[0].success_event == "impl.child.completed"
        assert spec.stages[0].failure_event == "dev.child.failed"  # 显式保留
        assert "lane_pipeline_missing_terminal" not in self._stops(raw)

    def test_failure_without_route_not_entry(self):
        raw = _hermes_raw()
        del raw["stages"][1]["on_failure"]
        assert "lane_pipeline_missing_rework_route" in self._stops(raw)

    def test_invalid_rework_target(self):
        raw = _hermes_raw()
        raw["stages"][1]["on_failure"]["rework_to"] = "nonexistent"
        assert "lane_pipeline_invalid_rework_target" in self._stops(raw)

    def test_missing_affinity_key(self):
        assert "lane_pipeline_missing_affinity_key" in self._stops(
            _hermes_raw(affinity_key=""),
        )

    def test_artifact_gate_without_digest_source(self):
        assert "lane_pipeline_missing_digest_source" in self._stops(
            _hermes_raw(task_source={}),
        )

    def test_unclear_overflow(self):
        assert "lane_pipeline_unclear_overflow" in self._stops(
            _hermes_raw(overflow="whatever"),
        )

    def test_unknown_key_fails_at_parse(self):
        with pytest.raises(LanePipelineSpecError, match="unknown key"):
            parse_lane_pipeline(_hermes_raw(lane_cout=4))


class TestLoaderRoundTrip:
    def test_yaml_pipelines_parse_into_config(self, tmp_path: Path):
        import yaml
        from zf.core.config.loader import load_config

        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [
                {"name": f"dev-lane-{i}", "backend": "mock",
                 "instance_id": f"dev-lane-{i}"} for i in range(2)
            ],
            "workflow": {"pipelines": [{
                "id": "p1", "kind": "lane_pipeline",
                "trigger": "task_map.ready",
                "affinity_key": "affinity_tag",
                "lane_count": 2,
                "stages": [{
                    "id": "impl", "role_pattern": "dev-lane-{lane}",
                    "terminal": {"success": "dev.child.completed"},
                }],
            }]},
        }))
        cfg = load_config(cfg_path)
        assert len(cfg.workflow.pipelines) == 1
        assert cfg.workflow.pipelines[0].pipeline_id == "p1"

    def test_spec_error_becomes_config_error(self, tmp_path: Path):
        import yaml
        from zf.core.config.loader import ConfigError, load_config

        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({
            "version": "1.0",
            "project": {"name": "t"},
            "workflow": {"pipelines": [{
                "id": "p1", "kind": "lane_pipeline", "bogus_key": 1,
            }]},
        }))
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(cfg_path)

    def test_inspection_report_carries_contracts_and_stops(self, tmp_path: Path):
        from zf.core.workflow.inspection import build_workflow_inspection_report

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=_hermes_roles(),
        )
        config.workflow.pipelines = [parse_lane_pipeline(_hermes_raw())]
        report = build_workflow_inspection_report(
            config, project_root=tmp_path, state_dir=tmp_path / ".zf",
        )
        assert report["lane_pipelines"]
        assert report["lane_pipelines"][0]["pipeline_id"] == "cj-min-refactor"
        assert not [
            d for d in report["diagnostics"]
            if d.get("severity") == "STOP" and "lane_pipeline" in str(d.get("kind"))
        ]


class TestInspectOnlyBoundary:
    def test_runtime_touches_lane_pipeline_only_at_admission(self):
        """G3(doc 88 P1 切片 1)边界:admission 内容校验站点是唯一获准
        的 runtime 引用——writer_fanout_admission(validate 调用)与
        orchestrator(trigger→spec 查找)。其余 runtime 模块仍禁;
        物化器 lane_pipeline_materialize 仍不得被 runtime 导入
        (stages 经 canonical loader 流入,不建第二 scheduler)。"""
        pattern = re.compile(r"lane_pipeline")
        # K1 切片 4:_lane_pipeline_for_trigger/_validate_writer_task_items
        # verbatim 迁居 writer_fanout_data.py(引用本体搬家,G3 边界实质
        # 不变 —— 仍只有 admission 查找与校验两类引用)。
        # P3:fanout 协调方法 verbatim 迁居 orchestrator_fanout.py
        # (FanoutCoordinationMixin),trigger→spec 查找引用随之搬家。
        allowed = {
            "writer_fanout_admission.py", "orchestrator.py",
            "writer_fanout_data.py", "orchestrator_fanout.py",
            # Read-only event diagnostics may name lane_pipeline producers or
            # sources; they do not import the materializer or mutate workflow.
            "event_contracts.py", "event_problem_registry.py",
        }
        offenders = []
        for path in (_REPO / "src/zf/runtime").glob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                if path.name not in allowed:
                    offenders.append(path.name)
        assert offenders == [], (
            f"lane_pipeline referenced by runtime modules beyond the G3 "
            f"admission allowlist: {offenders}"
        )
        admission_src = (
            _REPO / "src/zf/runtime/writer_fanout_admission.py"
        ).read_text(encoding="utf-8")
        assert "validate_lane_pipeline_admission" in admission_src
        for path in (_REPO / "src/zf/runtime").glob("*.py"):
            assert "lane_pipeline_materialize" not in path.read_text(
                encoding="utf-8", errors="replace",
            ), f"{path.name} imports the materializer (second-scheduler risk)"


class TestLaneRoleTemplate:
    """doc 90 A1:laneRoleTemplate 生成 + topology 锁定。"""

    def _raw_with_template(self, **tpl_overrides):
        raw = _hermes_raw()
        tpl = {
            "backend": "codex",
            "stuck_threshold_seconds": 900,
            "skills_by_stage": {
                "impl": ["code-refactoring", "python-to-typescript-migration"],
                "review": ["requesting-code-review"],
                "verify": ["test-driven-development"],
            },
        }
        tpl.update(tpl_overrides)
        raw["lane_role_template"] = tpl
        return raw

    def _load(self, raw, roles_yaml=None, tmp_path=None):
        import yaml
        from zf.core.config.loader import load_config

        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({
            "version": "1.0",
            "project": {"name": "t"},
            "roles": roles_yaml or [
                {"name": "judge-refactor", "backend": "mock",
                 "instance_id": "judge-refactor", "role_kind": "reader"},
            ],
            "workflow": {"pipelines": [raw]},
        }))
        return load_config(cfg_path)

    def test_generates_lane_roles_with_topology(self, tmp_path):
        cfg = self._load(self._raw_with_template(), tmp_path=tmp_path)
        names = {r.name for r in cfg.roles}
        for stage in ("dev", "review", "verify"):
            for lane in range(4):
                assert f"{stage}-lane-{lane}" in names
        dev0 = next(r for r in cfg.roles if r.name == "dev-lane-0")
        assert dev0.role_kind == "writer"          # 首 stage → writer
        assert dev0.instance_id == "dev-lane-0"
        assert dev0.backend == "codex"
        assert dev0.stuck_threshold_seconds == 900
        assert dev0.skills == ["code-refactoring", "python-to-typescript-migration"]
        assert dev0.publishes == ["dev.child.completed", "dev.child.failed"]
        assert dev0.stages == ["impl"]
        rev1 = next(r for r in cfg.roles if r.name == "review-lane-1")
        assert rev1.role_kind == "reader"
        assert rev1.skills == ["requesting-code-review"]
        # meta 落在 workflow.pipelines_role_meta
        metas = cfg.workflow.pipelines_role_meta
        assert len(metas) == 12
        assert all(m.source == "generated" for m in metas)

    def test_generated_roles_satisfy_compile_role_check(self, tmp_path):
        cfg = self._load(self._raw_with_template(), tmp_path=tmp_path)
        from zf.core.workflow.inspection import build_workflow_inspection_report
        report = build_workflow_inspection_report(
            cfg, project_root=tmp_path, state_dir=tmp_path / ".zf",
        )
        stops = [
            d for d in report["diagnostics"]
            if d.get("severity") == "STOP"
            and "lane_pipeline" in str(d.get("kind"))
        ]
        assert stops == []
        gen = report["lane_pipelines"][0]["generated_roles"]
        assert len(gen) == 12

    def test_whitelist_override_applies(self, tmp_path):
        roles = [
            {"name": "judge-refactor", "backend": "mock",
             "instance_id": "judge-refactor", "role_kind": "reader"},
            {"name": "dev-lane-2", "backend": "mock",
             "instance_id": "dev-lane-2", "model": "o4-mini",
             "budget_usd": 50.0},
        ]
        cfg = self._load(self._raw_with_template(), roles_yaml=roles,
                         tmp_path=tmp_path)
        dev2 = next(r for r in cfg.roles if r.name == "dev-lane-2")
        assert dev2.backend == "mock"        # 白名单覆盖
        assert dev2.model == "o4-mini"
        assert dev2.budget_usd == 50.0
        assert dev2.role_kind == "writer"    # topology 仍由生成层
        assert dev2.publishes == ["dev.child.completed", "dev.child.failed"]
        meta = next(
            m for m in cfg.workflow.pipelines_role_meta
            if m.name == "dev-lane-2"
        )
        assert meta.source == "generated+override"
        assert "backend" in meta.overridden_fields

    def test_topology_override_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError
        roles = [
            {"name": "judge-refactor", "backend": "mock",
             "instance_id": "judge-refactor", "role_kind": "reader"},
            {"name": "dev-lane-0", "backend": "mock",
             "instance_id": "dev-lane-0",
             "publishes": ["my.custom.event"]},
        ]
        with pytest.raises(ConfigError, match="locked topology"):
            self._load(self._raw_with_template(), roles_yaml=roles,
                       tmp_path=tmp_path)

    def test_replicas_pool_conflict_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError
        roles = [
            {"name": "judge-refactor", "backend": "mock",
             "instance_id": "judge-refactor", "role_kind": "reader"},
            {"name": "review-lane-0", "backend": "mock", "replicas": 2},
        ]
        # replica 展开使 instance_id 与 lane 名错位 → 先命中 topology 锁;
        # 直写 replicas 命中 pool 检查 —— 两者都是 fail-closed,断言任一。
        with pytest.raises(ConfigError, match="locked topology|replicas/autoscale"):
            self._load(self._raw_with_template(), roles_yaml=roles,
                       tmp_path=tmp_path)

    def test_unknown_template_key_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError
        with pytest.raises(ConfigError, match="unknown key"):
            self._load(self._raw_with_template(skils_by_stage={}),
                       tmp_path=tmp_path)


class TestSchemaProfile:
    """doc 90 A2:schemaProfile 库 + 三层 merge + override 分级。"""

    def _yaml(self, tmp_path, *, profile="refactor-flow/v1", overrides=None,
              local=None, harness="baseline"):
        import yaml
        raw = _hermes_raw()
        raw["schema_profile"] = profile
        if overrides:
            raw["schema_overrides"] = overrides
        body = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{"name": "judge-refactor", "backend": "mock",
                       "instance_id": "judge-refactor",
                       "role_kind": "reader"}],
            "workflow": {
                "harness_profile": harness,
                "pipelines": [raw],
            },
        }
        if local:
            body["workflow"]["dag"] = {"event_schemas": local}
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(body))
        return p

    def test_profile_resolves_into_effective_schemas(self, tmp_path):
        from zf.core.config.loader import load_config
        cfg = load_config(self._yaml(tmp_path))
        schemas = cfg.workflow.dag.event_schemas
        assert "judge.passed" in schemas
        assert "fanout_id" in schemas["judge.passed"]["required"]
        assert len(schemas) == 22
        assert cfg.workflow.pipelines_schema_sources["judge.passed"] == "profile"

    def test_v2_profile_adds_lane_stage_handoff_events(self, tmp_path):
        from zf.core.config.loader import load_config
        cfg = load_config(self._yaml(tmp_path, profile="refactor-flow/v2"))
        schemas = cfg.workflow.dag.event_schemas
        assert len(schemas) == 24
        assert "lane.stage.completed" in schemas
        assert "lane.stage.failed" in schemas
        assert "handoff_ref" in schemas["lane.stage.completed"]["required"]
        assert "failure_target" in schemas["lane.stage.failed"]["required"]
        assert (
            cfg.workflow.pipelines_schema_sources["lane.stage.completed"]
            == "profile"
        )

    def test_unknown_profile_fails_closed(self, tmp_path):
        from zf.core.config.loader import ConfigError, load_config
        with pytest.raises(ConfigError, match="unknown schema profile"):
            load_config(self._yaml(tmp_path, profile="refactor-flow/v99"))

    def test_three_layer_precedence(self, tmp_path):
        from zf.core.config.loader import load_config
        cfg = load_config(self._yaml(
            tmp_path,
            overrides={"judge.passed": {
                "required": ["fanout_id", "stage_id", "status",
                             "target_ref", "extra_from_override"],
            }},
            local={"judge.failed": {
                "required": ["fanout_id", "stage_id", "status",
                             "target_ref", "extra_from_local"],
            }},
        ))
        schemas = cfg.workflow.dag.event_schemas
        assert "extra_from_override" in schemas["judge.passed"]["required"]
        assert "extra_from_local" in schemas["judge.failed"]["required"]
        src = cfg.workflow.pipelines_schema_sources
        assert src["judge.passed"] == "override"
        assert src["judge.failed"] == "local"

    def test_breaking_override_warns_on_baseline(self, tmp_path, capsys):
        from zf.core.config.loader import load_config
        load_config(self._yaml(
            tmp_path,
            overrides={"judge.passed": {"required": ["fanout_id"]}},  # 放宽
        ))
        assert "breaking" in capsys.readouterr().err

    def test_breaking_override_stops_on_strict(self, tmp_path):
        from zf.core.config.loader import ConfigError, load_config
        with pytest.raises(ConfigError, match="breaking"):
            load_config(self._yaml(
                tmp_path, harness="strict",
                overrides={"judge.passed": {"required": ["fanout_id"]}},
            ))

    def test_additive_override_is_quiet_on_strict(self, tmp_path):
        from zf.core.config.loader import load_config
        cfg = load_config(self._yaml(
            tmp_path, harness="strict",
            overrides={"judge.passed": {
                "required": ["fanout_id", "stage_id", "status",
                             "target_ref", "more"],
            }},
        ))
        assert "more" in cfg.workflow.dag.event_schemas["judge.passed"]["required"]


class TestConventionDerivation:
    """doc 90 A3:terminal 约定 + affinity/rework 派生 + kernel-swept 豁免。"""

    def test_no_terminal_block_fully_defaults(self):
        raw = _hermes_raw()
        del raw["stages"][2]["terminal"]
        spec = parse_lane_pipeline(raw)
        assert spec.stages[2].success_event == "verify.child.completed"
        assert spec.stages[2].failure_event == "verify.child.failed"

    def test_affinity_lanes_derivation_matches_handwritten_shape(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        lanes = contract["affinity_lanes"]
        assert lanes["affinity_key"] == "affinity_tag"
        assert len(lanes["lanes"]) == 4
        lane3 = next(l for l in lanes["lanes"] if l["id"] == "lane3")
        # == cj-min 手写 affinity_lanes 表的逐项形状
        assert lane3 == {
            "id": "lane3",
            "impl": "dev-lane-3",
            "review": "review-lane-3",
            "verify": "verify-lane-3",
        }

    def test_rework_routing_derived_with_kernel_swept_exemption(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        routing = contract["rework_routing"]
        # lane 级 failure → same_lane impl
        assert routing["review.child.failed"] == "dev-lane-{lane}@same_lane"
        assert routing["verify.child.failed"] == "dev-lane-{lane}@same_lane"
        # kernel-swept(candidate 级)一律不铸 route(d9379b8 对齐):
        from zf.core.workflow.topology import KERNEL_SWEPT_FAILURE_EVENTS
        assert "judge.failed" in KERNEL_SWEPT_FAILURE_EVENTS  # 前提自检
        assert "judge.failed" not in routing
        for swept in KERNEL_SWEPT_FAILURE_EVENTS:
            assert swept not in routing

    def test_final_failure_never_routes_categorically(self):
        # G2 范畴化:final failure 是 stage 级(candidate 级)——无论叫什么
        # 名字都归 kernel candidate-rework sweep(doc 88 M7),不铸 agent
        # 路由。旧行为按点名集放行自定义名,正是点名化的盲区。
        raw = _hermes_raw()
        raw["final"]["failure"] = "refactor.final.rejected"  # 自定义名
        spec = parse_lane_pipeline(raw)
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        assert "refactor.final.rejected" not in contract["rework_routing"]
        assert "judge.failed" not in contract["rework_routing"]

    def test_inspect_has_no_comma_join_stops_for_paired_publishes(self, tmp_path):
        # 生成 role publishes 成对(success+failure);d9379b8 后 inspect
        # 不得再产 comma-join 形态的 route STOP。
        import yaml
        from zf.core.config.loader import load_config
        from zf.core.workflow.inspection import build_workflow_inspection_report

        raw = _hermes_raw()
        raw["lane_role_template"] = {"backend": "codex"}
        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{"name": "judge-refactor", "backend": "mock",
                       "instance_id": "judge-refactor",
                       "role_kind": "reader"}],
            "workflow": {"pipelines": [raw]},
        }))
        cfg = load_config(cfg_path)
        report = build_workflow_inspection_report(
            cfg, project_root=tmp_path, state_dir=tmp_path / ".zf",
        )
        comma_stops = [
            d for d in report["diagnostics"]
            if d.get("severity") == "STOP" and "," in str(d.get("event", ""))
        ]
        assert comma_stops == []


class TestAssemblyOwnerGate:
    """doc 90 A6 / doc 88 §3.2:根/组装 owner 门(R21/R24 无主地带)。"""

    def test_missing_assembly_decl_is_stop(self):
        raw = _hermes_raw()
        del raw["assembly"]
        spec = parse_lane_pipeline(raw)
        _, diags = compile_lane_pipeline(spec, _hermes_roles())
        assert any(
            d["kind"] == "lane_pipeline_missing_assembly_decl"
            for d in diags
        )

    def test_assembly_none_is_explicit_acceptance(self):
        spec = parse_lane_pipeline(_hermes_raw(assembly="none"))
        contract, diags = compile_lane_pipeline(spec, _hermes_roles())
        assert not any(
            d["kind"] == "lane_pipeline_missing_assembly_decl"
            for d in diags
        )
        assert contract["assembly"] == "none"

    def test_assembly_task_lands_in_contract(self):
        spec = parse_lane_pipeline(_hermes_raw())
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        assert contract["assembly"] == {"task": "CJMIN-ASSEMBLY-001"}

    def test_bogus_assembly_shapes_fail_parse(self):
        with pytest.raises(LanePipelineSpecError, match="assembly"):
            parse_lane_pipeline(_hermes_raw(assembly="whatever"))
        with pytest.raises(LanePipelineSpecError, match="non-empty task id"):
            parse_lane_pipeline(_hermes_raw(assembly={"task": ""}))

    # ---- admission 内容校验(纯函数;P1 接 task_map.ready ingest) ----

    def _items(self, *, with_assembly=True, with_root=True):
        items = [
            {"task_id": "CJMIN-GATEWAY-001",
             "allowed_paths": ["packages/gateway/**"]},
            {"task_id": "CJMIN-PI-001",
             "allowed_paths": ["packages/pi-core/**"]},
        ]
        if with_assembly:
            paths = ["packages/assembly/**"]
            if with_root:
                paths += ["package.json", "tsconfig.json"]
            items.append({
                "task_id": "CJMIN-ASSEMBLY-001",
                "allowed_paths": paths,
            })
        return items

    def test_admission_passes_with_assembly_and_root_owner(self):
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw())
        assert validate_lane_pipeline_admission(spec, self._items()) == []

    def test_admission_rejects_missing_assembly_task(self):
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw())
        problems = validate_lane_pipeline_admission(
            spec, self._items(with_assembly=False),
        )
        assert any("not present in the task_map" in p for p in problems)

    def test_admission_rejects_unowned_root(self):
        # R21 失败形状:根 package.json/tsconfig 无主 → 根 tsc -b 永不执行。
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw())
        problems = validate_lane_pipeline_admission(
            spec, self._items(with_root=False),
        )
        assert any("workspace-root" in p for p in problems)

    def test_admission_allows_single_assembly_slice_without_root_owner(self):
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw(
            lane_count=1,
            assembly={"task": "TINYCALC-ASM-001"},
        ))
        items = [{
            "task_id": "TINYCALC-ASM-001",
            "root_owner_class": "assembly",
            "allowed_paths": [
                "src/tinycalc/calculator.py",
                "src/tinycalc/__init__.py",
                "tests/test_calculator.py",
            ],
        }]

        assert validate_lane_pipeline_admission(spec, items) == []

    def test_admission_none_skips_assembly_but_checks_root(self):
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw(assembly="none"))
        # 无 assembly task,但有人拥有根 → 只剩 root 检查 → 通过
        items = self._items(with_assembly=False)
        items[0]["allowed_paths"] = ["packages/gateway/**", "package.json"]
        assert validate_lane_pipeline_admission(spec, items) == []
        # 根也无主 → 仍拒
        problems = validate_lane_pipeline_admission(
            spec, self._items(with_assembly=False),
        )
        assert any("workspace-root" in p for p in problems)

    def test_admission_refactor_contract_none_allows_leaf_only_refactor(self):
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        spec = parse_lane_pipeline(_hermes_raw(assembly="none"))
        items = [
            {
                "task_id": "REFACTOR-PRICING-CHAR",
                "allowed_paths": ["tests/test_pricing.py"],
            },
            {
                "task_id": "REFACTOR-PRICING-HELPERS",
                "allowed_paths": ["src/orders/pricing.py"],
            },
        ]
        task_map = {
            "refactor_contract": {
                "assembly": "none",
                "assembly_policy": "none",
            },
            "tasks": items,
        }

        assert validate_lane_pipeline_admission(
            spec,
            items,
            task_map_payload=task_map,
        ) == []


class TestInstructionRefs:
    """doc 90 §6.1(0722):repo artifact 引用,非 truth。"""

    def _diags(self, refs, tmp_path, harness="baseline"):
        from zf.core.workflow.lane_pipeline import (
            instruction_ref_diagnostics,
        )
        spec = parse_lane_pipeline(_hermes_raw(instruction_refs=refs))
        return instruction_ref_diagnostics(
            spec, project_root=tmp_path, state_dir=tmp_path / ".zf",
            harness_profile=harness,
        )

    def test_valid_ref_passes_and_lands_in_contract(self, tmp_path):
        (tmp_path / "skills").mkdir()
        (tmp_path / "skills" / "scan.md").write_text("x")
        diags = self._diags({"scan": "skills/scan.md"}, tmp_path)
        assert diags == []
        spec = parse_lane_pipeline(
            _hermes_raw(instruction_refs={"scan": "skills/scan.md"}),
        )
        contract, _ = compile_lane_pipeline(spec, _hermes_roles())
        assert contract["instruction_refs"] == {"scan": "skills/scan.md"}

    def test_escape_paths_stop(self, tmp_path):
        for bad in ("/etc/passwd", "../outside.md"):
            diags = self._diags({"x": bad}, tmp_path)
            assert any(
                d["kind"] == "lane_pipeline_instruction_ref_escape"
                for d in diags
            ), bad

    def test_runtime_state_paths_stop(self, tmp_path):
        diags = self._diags({"x": ".zf/briefings/a.md"}, tmp_path)
        assert any(
            d["kind"] == "lane_pipeline_instruction_ref_runtime_state"
            for d in diags
        )

    def test_missing_ref_warn_baseline_stop_strict(self, tmp_path):
        warn = self._diags({"x": "skills/none.md"}, tmp_path)
        assert [d["severity"] for d in warn] == ["WARN"]
        stop = self._diags({"x": "skills/none.md"}, tmp_path, harness="strict")
        assert [d["severity"] for d in stop] == ["STOP"]


class TestTemplateDeclarativeExtensions:
    """真实 hermes 文件暴露的两个声明位(topology 仍归生成层)。"""

    def test_publishes_extra_and_role_stage_labels(self, tmp_path):
        raw = self._raw() if hasattr(self, "_raw") else _hermes_raw()
        raw["lane_role_template"] = {
            "backend": "codex",
            "publishes_extra_by_stage": {"impl": ["dev.blocked"]},
            "role_stages_by_stage": {"impl": ["implement"]},
        }
        import yaml
        from zf.core.config.loader import load_config
        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "roles": [{"name": "judge-refactor", "backend": "mock",
                       "instance_id": "judge-refactor",
                       "role_kind": "reader"}],
            "workflow": {"pipelines": [raw]},
        }))
        cfg = load_config(cfg_path)
        dev0 = next(r for r in cfg.roles if r.name == "dev-lane-0")
        assert "dev.blocked" in dev0.publishes
        assert dev0.stages == ["implement"]
        rev0 = next(r for r in cfg.roles if r.name == "review-lane-0")
        assert rev0.stages == ["review"]  # 未声明 → 缺省 stage_id


class TestDagLevelSchemaProfile:
    """doc 90 增补:workflow.dag.schema_profile 顶层引用(无 lane_pipeline)。"""

    def test_profile_resolves_without_pipeline(self, tmp_path):
        import yaml
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "workflow": {"dag": {"schema_profile": "refactor-flow/v1"}},
        }))
        cfg = load_config(p)
        assert len(cfg.workflow.dag.event_schemas) == 22
        assert cfg.workflow.pipelines_schema_sources["judge.passed"] == "profile"

    def test_local_escape_hatch_still_wins(self, tmp_path):
        import yaml
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "workflow": {"dag": {
                "schema_profile": "refactor-flow/v1",
                "event_schemas": {"judge.passed": {"required": [
                    "fanout_id", "stage_id", "status", "target_ref", "extra"]}},
            }},
        }))
        cfg = load_config(p)
        assert "extra" in cfg.workflow.dag.event_schemas["judge.passed"]["required"]


class TestCanonicalDagProfile:
    """G1:第二本藏书 — 通用 stage 语法契约。"""

    def test_resolves_and_coexists(self):
        from zf.core.config.schema_profiles import resolve_schema_profile
        generic = resolve_schema_profile("canonical-dag/v1")
        refactor = resolve_schema_profile("refactor-flow/v1")
        assert "judge.passed" in generic and "judge.passed" in refactor
        # 通用基线比 refactor 专用契约宽(required 是子集)
        assert set(generic["judge.passed"]["required"]) < set(
            refactor["judge.passed"]["required"]
        )

    def test_non_refactor_project_gets_enforcement_by_reference(self, tmp_path):
        import yaml
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "anything"},
            "workflow": {"dag": {"schema_profile": "canonical-dag/v1"}},
        }))
        cfg = load_config(p)
        schemas = cfg.workflow.dag.event_schemas
        assert "fanout_id" in schemas["review.rejected"]["required"]
        assert len(schemas) == 16


class TestDerivedKernelSwept:
    """G2:swept 集从图派生(去点名化),兼容不收窄。"""

    def test_derived_superset_of_frozen_base(self):
        from zf.core.workflow.topology import (
            KERNEL_SWEPT_FAILURE_EVENTS,
            derive_kernel_swept_events,
        )
        assert derive_kernel_swept_events([]) >= KERNEL_SWEPT_FAILURE_EVENTS

    def test_custom_stage_failure_captured(self):
        from zf.core.workflow.topology import derive_kernel_swept_events
        stages = [{"id": "impl", "aggregate": {
            "mode": "candidate_integration",
            "success_event": "my.candidate.ready",
            "failure_event": "my.integration.broke",   # 自定义名
        }}]
        derived = derive_kernel_swept_events(stages)
        assert "my.integration.broke" in derived
        assert "judge.failed" in derived  # 基线仍在

    def test_inspect_exempts_custom_stage_failure(self, tmp_path):
        # 自定义事件名项目自动获得与 canonical 词汇同等的 sweep 豁免:
        # missing_rework_route 不再对 stage 级自定义失败 STOP/WARN。
        import yaml
        from zf.core.config.loader import load_config
        from zf.core.workflow.graph import compile_workflow_graph
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "roles": [{"name": "w", "backend": "mock", "instance_id": "w",
                       "role_kind": "writer"}],
            "workflow": {"stages": [{
                "id": "impl", "trigger": "go",
                "topology": "fanout_writer_scoped",
                "roles": ["w"], "source": {"task_map": "${task_map_ref}"},
                "aggregate": {"mode": "candidate_integration",
                              "success_event": "my.candidate.ready",
                              "failure_event": "my.integration.broke"},
            }]},
        }))
        graph = compile_workflow_graph(load_config(p))
        missing = [
            d for d in graph.diagnostics
            if d.get("kind") == "missing_rework_route"
            and d.get("event") == "my.integration.broke"
        ]
        assert missing == []


class TestPayloadProvenanceContract:
    """1404:聚合铸造 payload 来源契约(v3 sim 实测缺口的正解)。"""

    def test_synth_owed_gaps_named_explicitly(self):
        from zf.core.config.schema_profiles import (
            resolve_schema_profile,
            synth_owed_gaps,
        )
        schemas = resolve_schema_profile("refactor-flow/v1")
        payload = {  # sim 场景:synth 给了 task_map/plan,漏了 audit
            "fanout_id": "f1", "stage_id": "s", "status": "completed",
            "plan_artifact_ref": "x.md", "task_map_ref": "tm.json",
        }
        gaps = synth_owed_gaps("zaofu.refactor.plan.ready", payload, schemas)
        assert gaps == ["scan_quality_audit_ref"]

    def test_no_gap_when_synth_pays_in_full(self):
        from zf.core.config.schema_profiles import (
            resolve_schema_profile,
            synth_owed_gaps,
        )
        schemas = resolve_schema_profile("refactor-flow/v1")
        payload = {"plan_artifact_ref": "a", "scan_quality_audit_ref": "b",
                   "task_map_ref": "c"}
        assert synth_owed_gaps(
            "zaofu.refactor.plan.ready", payload, schemas) == []

    def test_kernel_owned_fields_not_in_scope(self):
        from zf.core.config.schema_profiles import (
            resolve_schema_profile,
            synth_owed_gaps,
        )
        schemas = resolve_schema_profile("refactor-flow/v1")
        # artifact_gate/artifact_refs 是 kernel 注入,缺失不算 synth 欠账
        gaps = synth_owed_gaps("zaofu.refactor.plan.ready", {
            "plan_artifact_ref": "a", "scan_quality_audit_ref": "b",
            "task_map_ref": "c"}, schemas)
        assert "artifact_gate" not in gaps and gaps == []

    def test_field_sources_survive_merge(self, tmp_path):
        import yaml
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "workflow": {"dag": {"schema_profile": "refactor-flow/v1"}},
        }))
        cfg = load_config(p)
        rule = cfg.workflow.dag.event_schemas["zaofu.refactor.plan.ready"]
        assert rule["field_sources"]["scan_quality_audit_ref"] == "synth"

    def test_events_without_annotation_never_gap(self):
        from zf.core.config.schema_profiles import synth_owed_gaps
        assert synth_owed_gaps("judge.passed", {}, {"judge.passed": {
            "required": ["fanout_id"]}}) == []


# --- B-R28-07 (R27 ISSUE-002 / R29): admission match-by-role + 子目录 scaffold ---

def _admit(items):
    from zf.core.workflow.lane_pipeline import (
        parse_lane_pipeline, validate_lane_pipeline_admission,
    )
    spec = parse_lane_pipeline(_hermes_raw())  # 声明 assembly: {task: CJMIN-ASSEMBLY-001}
    return validate_lane_pipeline_admission(spec, items)


def test_admission_accepts_role_assembly_named_differently():
    """字面 id 缺,但有任务 root_owner_class=assembly(synth 角色制)→ 放行。"""
    items = [
        {"task_id": "CJMIN-PI-CORE-001", "root_owner_class": "assembly",
         "allowed_paths": ["cj-min/package.json", "cj-min/tsconfig.json",
                           "cj-min/packages/pi-core/**"]},
        {"task_id": "CJMIN-STATE-001", "root_owner_class": "slice",
         "allowed_paths": ["cj-min/packages/state-config/src/**"]},
    ]
    assert _admit(items) == []  # R29 真实形态:既无字面 id 也是子目录 scaffold


def test_admission_accepts_subdir_scaffolding():
    """scaffold 持在子目录(cj-min/package.json)而非 repo-root bare → 仍算有主。"""
    items = [
        {"task_id": "CJMIN-ASSEMBLY-001",  # 字面匹配,只考 clause 2
         "allowed_paths": ["cj-min/package.json", "cj-min/pnpm-workspace.yaml"]},
        {"task_id": "CJMIN-X-001", "allowed_paths": ["cj-min/packages/x/**"]},
    ]
    assert _admit(items) == []


def test_admission_accepts_python_pyproject_scaffolding():
    """PRD greenfield: app/pyproject.toml is the package scaffold owner."""
    from zf.core.workflow.lane_pipeline import (
        parse_lane_pipeline, validate_lane_pipeline_admission,
    )

    spec = parse_lane_pipeline({
        "id": "prd-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 2,
        "assembly": "none",
        "stages": [{"id": "impl"}, {"id": "verify"}],
        "final": {"when": "all_tasks_verified", "role": "judge"},
    })
    items = [
        {
            "task_id": "PDD-TEXTSTAT-SCAFFOLD-001",
            "allowed_paths": ["app/pyproject.toml", "app/textstat/__init__.py"],
        },
        {
            "task_id": "PDD-TEXTSTAT-CORE-002",
            "allowed_paths": ["app/textstat/stats.py", "app/tests/test_stats.py"],
        },
        {
            "task_id": "PDD-TEXTSTAT-CLI-003",
            "allowed_paths": ["app/textstat/cli.py", "app/tests/test_cli.py"],
        },
    ]

    assert validate_lane_pipeline_admission(spec, items) == []


def test_admission_still_rejects_truly_unowned_scaffold():
    """保护不破:既无字面/角色 assembly,也无任何 scaffold owner → 仍两拒。"""
    items = [
        {"task_id": "CJMIN-X-001", "root_owner_class": "slice",
         "allowed_paths": ["cj-min/packages/x/src/**"]},
    ]
    problems = _admit(items)
    assert any("not present" in p for p in problems)      # clause 1
    assert any("workspace-root" in p for p in problems)   # clause 2
