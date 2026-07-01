"""Tests for the static preset evaluator (doc 68 S4)."""

from __future__ import annotations

from zf.core.config.schema import (
    RoleConfig, WorkflowConfig, WorkflowDagConfig, WorkflowStageConfig, ZfConfig,
)
from zf.core.config.preset_eval import evaluate_preset


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def _clean_config() -> ZfConfig:
    return ZfConfig(
        roles=[
            RoleConfig(name="orchestrator",
                       triggers=["dev.blocked", "clarification.needed", "worker.stuck", "dev.build.done"]),
            RoleConfig(name="dev", triggers=["task.dispatched"], publishes=["dev.build.done"],
                       context_warning_threshold=0.5,
                       context_compact_threshold=0.7,
                       context_hard_cap=0.9),
            RoleConfig(name="review", triggers=["dev.build.done"], publishes=["review.approved"]),
        ],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(stage_order=["intake", "dev", "review", "done"])),
    )


def test_clean_config_ok():
    report = evaluate_preset(_clean_config())
    assert report["schema_version"] == "preset-eval.v1"
    assert report["ok"] is True
    assert report["summary"]["fail"] == 0


def test_stage_references_undeclared_role_fail():
    cfg = ZfConfig(
        roles=[RoleConfig(name="dev")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(id="review-wave", roles=["dev", "ghost-reviewer"]),
        ]),
    )
    report = evaluate_preset(cfg)
    chk = _by_name(report)["stage_roles_exist[review-wave]"]
    assert chk["status"] == "FAIL"
    assert "ghost-reviewer" in chk["detail"]
    assert report["ok"] is False


def test_orchestrator_missing_exception_triggers_warn():
    cfg = ZfConfig(roles=[
        RoleConfig(name="orchestrator", triggers=["dev.build.done"]),  # misses dev.blocked etc.
    ])
    report = evaluate_preset(cfg)
    chk = _by_name(report)["orchestrator.exception_triggers"]
    assert chk["status"] == "WARN"
    assert "dev.blocked" in chk["detail"]


def test_orchestrator_empty_triggers_pass():
    cfg = ZfConfig(roles=[RoleConfig(name="orchestrator", triggers=[])])
    report = evaluate_preset(cfg)
    assert _by_name(report)["orchestrator.exception_triggers"]["status"] == "PASS"


def test_stage_order_non_terminal_warn():
    cfg = ZfConfig(workflow=WorkflowConfig(
        dag=WorkflowDagConfig(stage_order=["intake", "dev", "review"])))
    report = evaluate_preset(cfg)
    assert _by_name(report)["stage_order.terminal"]["status"] == "WARN"


def test_worker_unreachable_warn():
    cfg = ZfConfig(roles=[
        RoleConfig(name="orchestrator", triggers=[]),
        RoleConfig(name="ghost"),  # no triggers, no publishes
    ])
    report = evaluate_preset(cfg)
    chk = _by_name(report)["worker_reachable[ghost]"]
    assert chk["status"] == "WARN"
    assert report["ok"] is True  # WARN does not fail the preset
