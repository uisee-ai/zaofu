"""Exercise a real, run-bound resident launch through CLI and child config materialization."""

import argparse
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.autoresearch.diagnosis_resource_policy import (
    apply_diagnostic_resource_policy,
    resident_resource_policy,
    loop_resource_settings,
)
from zf.autoresearch.loop import LoopResult
from zf.autoresearch.loop_iteration import run_iteration
from zf.autoresearch.orchestrator import (
    DEFAULT_CONTROLLER_TEMPLATE,
    _apply_autoresearch_controller_overrides,
    _load_config_template,
)
from zf.autoresearch.resident_commands import _command_for_request
from zf.cli.autoresearch import register
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_json


def bind(tmp_path, monkeypatch, *, sample_cap=0, cost_cap=0):
    monkeypatch.chdir(tmp_path)
    for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "zf.yaml").write_text("project: {name: demo, state_dir: runtime}\nroles: []\n")
    parent = load_config(tmp_path / "zf.yaml")
    state = tmp_path / "runtime"
    limits = dict(max_usage_samples=sample_cap, cost_budget_usd=cost_cap, timeout_seconds=0, token_budget=0)
    run = {"cost_budget_usd": 0, "timeout_seconds": 0, "token_budget": 0}
    cfg = {
        "global_budget_usd": None,
        "workflow": {
            "run_limits": run,
            "execution_profiles": {
                "bounded-direct-v1": {"limits": limits},
            },
        },
        "roles": [{"name": "planner", "budget_usd": cost_cap or None}],
    }
    snap = write_sidecar_json(
        state,
        "artifacts/pinned.json",
        {"schema_version": "effective-config-snapshot.v1", "config": cfg},
        kind="effective_config_snapshot",
        schema_version="effective-config-snapshot.v1",
        created_by="test",
    )
    writer = EventWriter(EventLog(state / "events.jsonl"))
    invoke = writer.append(
        ZfEvent(
            type="workflow.invoke.requested",
            actor="web",
            payload={
                "workflow_run_id": "parent-run",
                "effective_config_ref": snap,
            },
        )
    )
    writer.append(
        ZfEvent(
            type="run.admission.admitted",
            origin="kernel",
            payload={
                "run_id": "parent-run",
                "source_event_id": invoke.id,
                "run_limits": run,
                "effective_config_digest": snap["sha256"],
            },
        )
    )
    request = {
        "scenarios": ["incident-reproduction"],
        "mode": "debug",
        "loop_request_id": "ar",
        "workflow_run_id": "parent-run",
        "run_id": "parent-run",
    }
    writer.append(
        ZfEvent(type="autoresearch.loop.requested", actor="zf-autoresearch", origin="kernel", payload=request)
    )
    writer.append(
        ZfEvent(
            type="autoresearch.loop.started",
            actor="zf-autoresearch-resident",
            payload={"loop_request_id": "ar", "worktree": str(tmp_path / "child")},
        )
    )
    argv = _command_for_request(
        request, state_dir=state, worktree=tmp_path / "child", output_root=tmp_path / "out"
    )
    return parent, state, snap, writer, argv[argv.index("autoresearch") :]


def test_real_resident_command_replaces_only_template_metering(tmp_path, monkeypatch):
    _, state, _, _, argv = bind(tmp_path, monkeypatch)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(argv)
    template = _load_config_template(Path(__file__).resolve().parents[1] / DEFAULT_CONTROLLER_TEMPLATE)
    limits = template["workflow"]["execution_profiles"]["bounded-direct-v1"]["limits"]
    assert limits["max_usage_samples"] == 60 and limits["cost_budget_usd"] == 8
    assert template["roles"][0]["execution"]["profile_allowlist"] == ["bounded-direct-v1"]

    def materialize(child):
        _apply_autoresearch_controller_overrides(template, cfg=child, run_id="inner")
        assert limits == {
            "max_children": 0,
            "max_depth": 0,
            "timeout_seconds": 0,
            "max_usage_samples": 0,
            "token_budget": 0,
            "cost_budget_usd": 0,
        }
        assert (
            template["global_budget_usd"] is None
            and template["workflow"]["run_limits"]["cost_budget_usd"] == 0
        )
        assert template["roles"][0]["execution"]["profile_allowlist"] == ["bounded-direct-v1"]
        assert not template["runtime"]["autoresearch_resident"]["enabled"]
        raise StopIteration("isolated child config verified before launch")

    def loop(cfg, **kwargs):
        assert cfg.max_iterations == 1 and cfg.parent_state_dir == state
        with (
            patch("zf.autoresearch.loop_iteration.run_autoresearch", side_effect=materialize),
            patch("zf.autoresearch.loop_iteration.cleanup_autoresearch_run"),
            pytest.raises(StopIteration),
        ):
            run_iteration(cfg=cfg, scenario="incident-reproduction", run_id="inner")
        return LoopResult(
            iterations=0, final_status="done", journal_path=tmp_path / "j", report_path=tmp_path / "r"
        )

    with patch("zf.cli.autoresearch.run_loop", side_effect=loop):
        assert args.func(args) == 0


def test_restricted_parent_and_explicit_canary_intersect_without_changing_permissions(tmp_path, monkeypatch):
    parent, state, _, writer, argv = bind(tmp_path, monkeypatch, sample_cap=100, cost_cap=4)
    writer.append(
        ZfEvent(
            type="run.goal.updated",
            actor="operator",
            origin="kernel",
            payload={"run_id": "parent-run", "run_limits_patch": {"token_budget": 12345}},
        )
    )
    seed = argv[argv.index("--seed-text") + 1]
    resources = resident_resource_policy(
        state, parent, seed, worktree=tmp_path / "child", explicit_template=True
    )
    assert resources.run["token_budget"] == 12345
    for explicit, samples in ((False, 100), (True, 60)):
        template = _load_config_template(Path(__file__).resolve().parents[1] / DEFAULT_CONTROLLER_TEMPLATE)
        limits = template["workflow"]["execution_profiles"]["bounded-direct-v1"]["limits"]
        limits["max_children"], limits["max_depth"] = 2, 1
        apply_diagnostic_resource_policy(template, replace(resources, explicit_template=explicit), 3)
        assert (
            limits["max_usage_samples"],
            limits["cost_budget_usd"],
            limits["max_children"],
            limits["max_depth"],
        ) == (samples, 4, 2, 1)
        assert template["workflow"]["run_limits"]["cost_budget_usd"] == 3
        assert next(r for r in template["roles"] if r["name"] == "planner")["budget_usd"] == 4


def test_zero_global_or_role_budget_is_a_hard_spending_limit(tmp_path, monkeypatch):
    parent, state, _, _, argv = bind(tmp_path, monkeypatch)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(argv)
    resources, _ = loop_resource_settings(args, state, parent)
    zero_spend = replace(resources, global_usd=0, roles={"planner": {"budget_usd": 0}})
    with patch("zf.autoresearch.diagnosis_resource_policy.resident_resource_policy", return_value=zero_spend):
        with pytest.raises(ValueError, match="no monetary spend"):
            loop_resource_settings(args, state, parent)
    template = _load_config_template(Path(__file__).resolve().parents[1] / DEFAULT_CONTROLLER_TEMPLATE)
    apply_diagnostic_resource_policy(template, zero_spend, None)
    assert template["global_budget_usd"] == 0 and template["global_budget_usd"] is not None
    assert next(role for role in template["roles"] if role["name"] == "planner")["budget_usd"] == 0


def test_foreign_or_damaged_authority_cannot_remove_template_ceiling(tmp_path, monkeypatch):
    import json

    parent, state, snap, _, argv = bind(tmp_path, monkeypatch)
    seed = json.loads(argv[argv.index("--seed-text") + 1])
    assert (
        resident_resource_policy(
            state,
            parent,
            json.dumps({**seed, "diagnostic_request_id": "foreign"}),
            worktree=tmp_path / "child",
            explicit_template=False,
        )
        is None
    )
    with pytest.raises(ValueError, match="exact worktree"):
        resident_resource_policy(
            state, parent, json.dumps(seed), worktree=tmp_path / "other", explicit_template=False
        )
    (state / snap["ref"]).write_text("{}")
    with pytest.raises(ValueError, match="digest|hash|byte"):
        resident_resource_policy(
            state, parent, json.dumps(seed), worktree=tmp_path / "child", explicit_template=False
        )
