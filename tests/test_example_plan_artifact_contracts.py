from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config


PLAN_STAGE_REQUIRED_KEYS = {
    "examples/workflow-product-fanout-standard-codex.yaml": {
        "product-plan-authoring": {"plan_artifact_ref", "artifact_refs"},
        "product-task-map": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
    "examples/workflow-refactor-standard-codex.yaml": {
        "refactor-plan": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
    "examples/hermes-codex.yaml": {
        "cj-min-refactor-plan": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
    "examples/hermes-mixed.yaml": {
        "cj-min-refactor-scan": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
    "examples/star-refactor-planning-reader.yaml": {
        "refactor-planning-scan": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
    "examples/star-zaofu-refactor-review.yaml": {
        "zaofu-refactor-plan-synthesis": {"plan_artifact_ref", "task_map_ref", "artifact_refs"},
    },
}


DAG_PLAN_REF_EXAMPLES = [
    "examples/workflow-product-standard-codex.yaml",
    "examples/zf-codex.yaml",
    "examples/zf-full-claude.yaml",
    "examples/zf-full-codex.yaml",
    "examples/zf-mixed.yaml",
    "examples/zf-standard-codex.yaml",
    "examples/zf-strict-codex.yaml",
]


def test_plan_stage_examples_require_durable_plan_artifacts() -> None:
    for raw_path, stages in PLAN_STAGE_REQUIRED_KEYS.items():
        cfg = load_config(Path(raw_path))
        by_id = {stage.id: stage for stage in cfg.workflow.stages}
        for stage_id, expected in stages.items():
            assert stage_id in by_id, f"{raw_path} missing stage {stage_id}"
            stage = by_id[stage_id]
            required = set(stage.criteria.output.required_keys)
            payload_text = " ".join(
                str(child.payload)
                for child in stage.children
            )
            missing = {
                key for key in expected
                if key not in required and key not in payload_text
            }
            assert not missing, f"{raw_path}:{stage_id} missing {sorted(missing)}"


def test_standard_zf_examples_keep_plan_ref_in_dag_contract() -> None:
    for raw_path in DAG_PLAN_REF_EXAMPLES:
        cfg = load_config(Path(raw_path))
        required_refs = set(cfg.workflow.dag.required_backlog_refs)
        assert "plan_ref" in required_refs, raw_path
