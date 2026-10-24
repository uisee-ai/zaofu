from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.workflow_route_catalog import workflow_route_catalog
from zf.runtime.workflow_start import WorkflowStartService


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["prd", "issue", "refactor"])
def test_catalog_binds_actual_pipeline_mode(kind, monkeypatch):
    path = ROOT / f"examples/prod/controller/{kind}-task-pipeline-v4-canary.yaml"
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "shadow")
    shadow = workflow_route_catalog(load_config(path))
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "blocking")
    blocking = workflow_route_catalog(load_config(path))

    assert shadow["config_digest"] != blocking["config_digest"]
    route = blocking["routes"][0]
    assert route["flow_topology"] == "fanout"
    assert route["execution_policy"]["task_pipeline_mode"] == "blocking"
    assert route["execution_policy"]["rollout"] == "explicit_canary"


@pytest.mark.parametrize("change", ["permission", "budget", "verification", "approval", "context_scope"])
def test_start_preview_rejects_changed_execution_policy(tmp_path, change):
    config = load_config(ROOT / "zf.yaml")
    TaskStore(tmp_path / "kanban.json").add(Task(
        id="POLICY-TASK",
        title="Research the task",
        contract=TaskContract(behavior="Research", verification="Produce evidence"),
    ))
    service = WorkflowStartService(tmp_path, config, project_root=ROOT)
    preview = service.preview({
        "task_id": "POLICY-TASK",
        "route_id": "research:fixed",
        "objective": "Research workflow boundaries",
    }, require_bindings=False, origin="web")
    assert preview["ok"], preview
    if change == "permission":
        next(r for r in config.roles if r.name == "source_researcher").permission_mode = "restricted"
    elif change == "context_scope":
        role = next(r for r in config.roles if r.name == "source_researcher")
        role.lifecycle = replace(role.lifecycle, mode="on_demand", reader_context_scope="dispatch")
    elif change == "budget":
        config.global_budget_usd = 0.1
    elif change == "verification":
        config.quality_gates["static"].required_checks.append("python -m unittest")
    else:
        config.workflow.plan_approval_enabled = not config.workflow.plan_approval_enabled
    stale = service.preview(preview["payload"], require_bindings=True, origin="web")
    assert not stale["ok"], stale
    assert stale["status"] == "workflow_route_unavailable"
    assert not (tmp_path / "events.jsonl").exists()


def test_catalog_policy_is_stable_and_keeps_compatibility_topology():
    config = load_config(ROOT / "examples/prod/controller/issue-task-pipeline-v4-canary.yaml")
    catalog = workflow_route_catalog(config)
    assert workflow_route_catalog(config) == catalog
    route = catalog["routes"][0]
    assert route["topology"] == "single_lane"
    assert route["flow_topology"] == "fanout"
    assert route["execution_policy"]["digest"].startswith("sha256:")


def test_research_route_does_not_inherit_delivery_metadata():
    config = load_config(ROOT / "zf.yaml")
    config.workflow.flow_metadata["task_pipeline"] = {"mode": "blocking"}
    routes = workflow_route_catalog(config)["routes"]
    research = next(route for route in routes if route["route_id"] == "research:fixed")
    assert research["flow_topology"] == "fanout_reader"
    assert research["execution_policy"]["task_pipeline_mode"] == ""
    assert research["execution_policy"]["rollout"] == "stable_audit"


def test_catalog_ignores_observability_and_ui_changes():
    config = load_config(ROOT / "zf.yaml")
    before = workflow_route_catalog(config)
    config.observability.metrics.enabled = not config.observability.metrics.enabled
    config.runtime.web_terminal.enabled = not config.runtime.web_terminal.enabled
    assert workflow_route_catalog(config) == before
