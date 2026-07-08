"""G3(doc 88 P1 切片 1):lane_pipeline → canonical stages 物化。"""

from __future__ import annotations

import pytest
import yaml

from zf.core.config.loader import load_config


def _pipelines_only_yaml(tmp_path, *, hand_stage=False, lanes=2):
    raw = {
        "id": "demo", "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "task_source": {"task_map_ref": "${task_map_ref}"},
        "affinity_key": "affinity_tag",
        "lane_count": lanes,
        "assembly": {"task": "DEMO-ASM-001"},
        "lane_role_template": {"backend": "mock"},
        "stages": [
            {"id": "impl", "role_pattern": "dev-lane-{lane}",
             "terminal": {"success": "dev.build.done", "failure": "dev.failed"},
             "on_failure": {"rework_to": "impl", "feedback_artifact": "required"}},
            {"id": "review",
             "on_failure": {"rework_to": "impl", "feedback_artifact": "required"}},
            {"id": "verify",
             "on_failure": {"rework_to": "impl", "feedback_artifact": "required"}},
        ],
        "final": {"when": "all_tasks_verified", "role": "judge-x",
                  "success": "judge.passed", "failure": "judge.failed"},
    }
    body = {
        "version": "1.0", "project": {"name": "t"},
        "roles": [{"name": "judge-x", "backend": "mock",
                   "instance_id": "judge-x", "role_kind": "reader"}],
        "workflow": {"pipelines": [raw]},
    }
    if hand_stage:
        body["workflow"]["stages"] = [{
            "id": "hand-impl", "trigger": "task_map.ready",
            "topology": "fanout_writer_scoped",
            "roles": ["dev-lane-0", "dev-lane-1"][:lanes],
            "source": {"task_map": "${task_map_ref}"},
            "aggregate": {"mode": "candidate_integration",
                          "success_event": "candidate.ready",
                          "failure_event": "integration.failed"},
        }]
    p = tmp_path / "zf.yaml"
    p.write_text(yaml.dump(body))
    return p


class TestMaterialization:
    def test_pipelines_only_materializes_candidate_chain(self, tmp_path):
        cfg = load_config(_pipelines_only_yaml(tmp_path))
        stages = cfg.workflow.stages
        assert [s.id for s in stages] == [
            "demo-impl", "demo-review", "demo-verify", "demo-final",
        ]
        impl = stages[0]
        assert impl.topology == "fanout_writer_scoped"
        assert impl.trigger == "task_map.ready"
        assert impl.aggregate.mode == "candidate_integration"
        assert impl.aggregate.success_event == "candidate.ready"
        assert impl.on_fail.event == "dev.failed"
        assert impl.on_fail.restart_stage == "demo-impl"
        assert impl.on_fail.target_affinity == "same_lane"
        assert impl.on_fail.max_attempts == 2
        assert impl.on_fail.feedback_artifact == "required"
        assert impl.on_fail.emit == "impl.rework.requested"
        review = stages[1]
        assert review.trigger == "candidate.ready"
        assert review.aggregate.success_event == "review.approved"
        assert review.aggregate.child_success_event == "review.child.completed"
        assert review.on_fail.event == "review.child.failed"
        assert review.on_fail.restart_stage == "demo-impl"
        assert review.on_fail.target_affinity == "same_lane"
        assert "dev.failed" not in cfg.workflow.rework_routing
        assert "review.child.failed" not in cfg.workflow.rework_routing
        verify = stages[2]
        assert verify.trigger == "review.approved"
        assert verify.aggregate.success_event == "test.passed"
        final = stages[3]
        assert final.trigger == "test.passed"
        assert final.aggregate.success_event == "judge.passed"

    def test_pipeline_final_can_wait_for_custom_post_verify_stage(self, tmp_path):
        path = _pipelines_only_yaml(tmp_path)
        data = yaml.safe_load(path.read_text())
        data["workflow"]["pipelines"][0]["final"]["trigger"] = (
            "flow.discovery.completed"
        )
        path.write_text(yaml.dump(data))

        cfg = load_config(path)

        final = next(
            stage for stage in cfg.workflow.stages if stage.id == "demo-final"
        )
        assert final.trigger == "flow.discovery.completed"

    def test_materialized_graph_compiles_zero_stop(self, tmp_path):
        from zf.core.workflow.graph import compile_workflow_graph
        cfg = load_config(_pipelines_only_yaml(tmp_path))
        graph = compile_workflow_graph(cfg)
        stops = [d for d in graph.diagnostics if d.get("severity") == "STOP"]
        assert stops == []

    def test_affinity_profile_materialized(self, tmp_path):
        cfg = load_config(_pipelines_only_yaml(tmp_path))
        profiles = cfg.workflow.affinity_lanes
        assert "demo-slot" in profiles
        lanes = profiles["demo-slot"].lanes
        assert len(lanes) == 2
        assert lanes[0].impl == "dev-lane-0"
        assert lanes[1].review == "review-lane-1"

    def test_hand_stage_overlap_skips_and_warns(self, tmp_path, capsys):
        cfg = load_config(_pipelines_only_yaml(tmp_path, hand_stage=True))
        err = capsys.readouterr().err
        assert "dual representation" in err or "同一 trigger" in err
        assert [s.id for s in cfg.workflow.stages] == ["hand-impl"]

    def test_template_less_pipeline_stays_inspect_only(self, tmp_path):
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump({
            "version": "1.0", "project": {"name": "t"},
            "workflow": {"pipelines": [{
                "id": "p", "kind": "lane_pipeline", "trigger": "t",
                "affinity_key": "affinity_tag", "lane_count": 1,
                "assembly": "none", "stages": [{"id": "impl"}],
            }]},
        }))
        cfg = load_config(p)  # 角色不齐 → 不物化,不报错(inspect-only 行为)
        assert cfg.workflow.stages == []


class TestAdmissionWiring:
    def _spec(self):
        from zf.core.workflow.lane_pipeline import parse_lane_pipeline
        return parse_lane_pipeline({
            "id": "demo", "kind": "lane_pipeline", "trigger": "task_map.ready",
            "affinity_key": "affinity_tag", "lane_count": 1,
            "assembly": {"task": "DEMO-ASM-001"},
            "stages": [{"id": "impl"}],
        })

    def test_load_writer_task_map_rejects_missing_assembly(self, tmp_path):
        import json
        from zf.core.events.model import ZfEvent
        from zf.runtime.writer_fanout_admission import load_writer_task_map

        ref = tmp_path / "task_map.json"
        ref.write_text(json.dumps({"tasks": [
            {"task_id": "T-1", "title": "x", "allowed_paths": ["pkg/a/**"],
             "affinity_tag": "lane0"},
        ]}))
        event = ZfEvent(
            type="task_map.ready", actor="kernel",
            payload={"task_map_ref": str(ref), "pdd_id": "F-1"},
        )
        with pytest.raises(ValueError, match="assembly task"):
            load_writer_task_map(
                stage=None, event=event, pdd_id="F-1",
                state_dir=tmp_path, project_root=tmp_path,
                pipeline_spec=self._spec(),
            )

    def test_no_pipeline_spec_keeps_legacy_behavior(self, tmp_path):
        import json
        from zf.core.events.model import ZfEvent
        from zf.runtime.writer_fanout_admission import load_writer_task_map

        ref = tmp_path / "task_map.json"
        ref.write_text(json.dumps({"tasks": [
            {"task_id": "T-1", "title": "x", "allowed_paths": ["pkg/a/**"],
             "affinity_tag": "lane0"},
        ]}))
        event = ZfEvent(
            type="task_map.ready", actor="kernel",
            payload={"task_map_ref": str(ref), "pdd_id": "F-1"},
        )
        loaded = load_writer_task_map(
            stage=None, event=event, pdd_id="F-1",
            state_dir=tmp_path, project_root=tmp_path,
        )
        assert len(loaded.task_items) == 1
