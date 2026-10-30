import json
from types import SimpleNamespace

from tests.test_workflow_budget_guard import _runtime, _start_operation
from zf.cli.main import main
from zf.core.config.schema import ExecutionProfileLimitsConfig
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets
from zf.runtime.workflow_budget_status import operation_budget_status


def test_warning_restart_dedup_components_and_hard_limit(tmp_path):
    runtime, transport = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(token_budget=1000),
    )
    # Previous session usage must not inflate this operation's measurement.
    runtime.cost_tracker.record_usage("dev", 10000, 100, cache_read_tokens=20000)
    operation_id, _ = _start_operation(runtime)
    for index, (fresh, cache, output, level) in enumerate([
        (10, 480, 10, 50), (10, 280, 10, 80), (10, 180, 10, 100),
    ]):
        runtime.cost_tracker.record_usage(
            "dev", fresh, output, cache_read_tokens=cache,
            model="unknown-model", provider="openai", source_event_id=f"usage-{index}",
        )
        before = runtime.cost_tracker.cost_path.read_bytes()
        status = operation_budget_status(runtime.state_dir, runtime.config, operation_id)
        assert runtime.cost_tracker.cost_path.read_bytes() == before
        assert status["warning_level"] == level
        assert status["pricing_status"] == "unpriced"
        measurement = status["measurement"]
        assert measurement["input_tokens"] == (index + 1) * 10
        assert measurement["output_tokens"] == (index + 1) * 10
        assert measurement["total_tokens"] == sum(measurement[key] for key in (
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens", "cache_creation_1h_tokens",
        ))
        before_count = len(runtime.event_log.read_all())
        runtime._run_budget_sweep()
        events = runtime.event_log.read_all()[before_count:]
        expected = "workflow.budget.exceeded" if level == 100 else "workflow.budget.warning"
        assert events[0].type == expected
        # A fresh runtime facade has no in-memory warning cache: ledger dedup
        # must still suppress duplicate notifications and leave budget intact.
        restarted = SimpleNamespace(**runtime.__dict__)
        restarted._now = runtime._now
        assert enforce_active_workflow_budgets(restarted) == []
        assert transport.terminated == (["dev"] if level == 100 else [])


def test_unpriced_cost_headroom_is_unknown(tmp_path):
    runtime, transport = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(
            token_budget=1000, cost_budget_usd=10,
        ),
    )
    operation_id, _ = _start_operation(runtime)
    runtime.cost_tracker.record_usage("dev", 500, 0, model="unknown-model", provider="openai")
    status = operation_budget_status(runtime.state_dir, runtime.config, operation_id)
    assert status["dimensions"]["usd"]["remaining"] is None
    assert status["dimensions"]["usd"]["used"] is None
    assert status["dimensions"]["tokens"]["remaining"] == 500
    assert not transport.terminated


def test_cost_operation_cli_is_read_only_and_rejects_settled_identity(tmp_path, monkeypatch, capsys):
    runtime, _ = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(token_budget=1000),
    )
    operation_id, _ = _start_operation(runtime)
    monkeypatch.setattr("zf.cli.cost.resolve_project_context", lambda: SimpleNamespace(
        state_dir=runtime.state_dir, config=runtime.config, project_root=tmp_path,
    ))
    before = runtime.event_log.read_all()
    assert main(["cost", "--operation", operation_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"]
    assert result["data"]["operation_id"] == operation_id
    assert result["data"]["dimensions"]["tokens"]["remaining"] == 1000
    assert runtime.event_log.read_all() == before
    runtime.event_writer.append(ZfEvent(
        type="workflow.operation.settled", actor="kernel",
        payload={"operation_id": operation_id},
    ))
    for invalid in (operation_id, "missing-operation"):
        assert main(["cost", "--operation", invalid]) == 2
        assert not json.loads(capsys.readouterr().out)["ok"]


def test_budget_warning_is_observation_not_second_recovery_owner():
    from zf.runtime.event_problem_registry import spec_for_event
    spec = spec_for_event("workflow.budget.warning")
    assert spec.action_policy == "informational"
    assert spec.effective_recovery_policy == "none"
    assert not spec.autoresearch_eligible


def test_headroom_does_not_round_up_across_warning_boundary():
    from zf.runtime.workflow_budget_status import budget_headroom
    status = budget_headroom(
        {"token_budget": 10_000_000},
        {"meter_available": True, "total_tokens": 4_999_999},
    )
    assert status["warning_level"] == 0


def test_cost_operation_resolves_configured_state_from_worker_env(tmp_path, monkeypatch, capsys):
    runtime, _ = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(token_budget=1000),
    )
    operation_id, _ = _start_operation(runtime)
    custom_state = tmp_path / "custom-runtime"
    runtime.state_dir.rename(custom_state)
    (tmp_path / "zf.yaml").write_text("version: '1.0'\nproject: {name: budget, state_dir: custom-runtime}\n")
    worker = tmp_path / "worker"
    worker.mkdir()
    monkeypatch.chdir(worker)
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ZF_STATE_DIR", str(custom_state))
    assert main(["cost", "--operation", operation_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["identity"]["state_dir"] == str(custom_state)
    assert result["data"]["dimensions"]["tokens"]["remaining"] == 1000
