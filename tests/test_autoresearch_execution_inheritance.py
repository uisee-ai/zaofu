from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zf.autoresearch.orchestrator import AutoresearchRunConfig, _apply_autoresearch_controller_overrides
from zf.cli.autoresearch import register


def test_diagnostic_model_inheritance_preserves_backend_and_permissions(tmp_path: Path):
    data = {"roles": [
        {"name": "planner", "backend": "codex", "model": "template", "allowed_tools": ["Read"]},
        {"name": "verify", "backend": "mock", "model": "mock-model"},
        {"name": "other", "backend": "codex", "model": "template"},
    ]}
    cfg = AutoresearchRunConfig(worktree=tmp_path, role_provider_settings={
        "planner": {"backend": "codex", "model": "gpt-6-astra", "model_reasoning_effort": "high", "allowed_tools": ["*"]},
        "verify": {"backend": "codex", "model": "gpt-6.1-terra"},
    })
    _apply_autoresearch_controller_overrides(data, cfg=cfg, run_id="diagnosis")
    assert data["roles"][0] == {"name": "planner", "backend": "codex", "model": "gpt-6-astra", "model_reasoning_effort": "high", "allowed_tools": ["Read"]}
    assert data["roles"][1]["model"] == "mock-model"
    assert data["roles"][2]["model"] == "template"


def test_loop_cli_caps_diagnostic_budget_and_carries_model_selection(tmp_path, monkeypatch):
    import argparse
    from zf.autoresearch.loop_iteration import run_iteration
    from zf.autoresearch.loop import LoopResult
    (tmp_path / "zf.yaml").write_text('''version: "1.0"
project:
  name: test
  state_dir: state
workflow:
  run_limits:
    cost_budget_usd: 40
runtime:
  run_manager:
    backend: codex
    resident_agent:
      enabled: true
      instance_id: parent-rm
      model: gpt-6-luna
      model_reasoning_effort: high
roles:
  - name: planner
    backend: codex
    model: gpt-6-astra
    model_reasoning_effort: high
    provider_session:
      sandbox: workspace-write
''')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZF_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ZF_STATE_DIR", raising=False)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["autoresearch", "loop", "--scenarios", "incident-reproduction", "--worktree", str(tmp_path / "experiment")])
    captured = {}
    def run(cfg, **kwargs):
        captured["cfg"] = cfg
        return LoopResult(iterations=0, final_status="done", journal_path=tmp_path / "j", report_path=tmp_path / "r")
    with patch("zf.cli.autoresearch.run_loop", run):
        assert args.func(args) == 0
    cfg = captured["cfg"]
    assert cfg.budget_usd == 40
    assert cfg.role_provider_settings["planner"] == {"backend": "codex", "model": "gpt-6-astra", "model_reasoning_effort": "high", "sandbox": "workspace-write"}

    child_config = {"runtime": {"run_manager": {"backend": "codex", "resident_agent": {
        "enabled": False, "instance_id": "child-rm", "model": "", "model_reasoning_effort": "",
    }}}}

    def capture_child(child_cfg):
        _apply_autoresearch_controller_overrides(child_config, cfg=child_cfg, run_id="test")
        return SimpleNamespace(worktree=tmp_path, status="test", tasks_done=0, expected_done=0,
                               fatal_event="", report_path=tmp_path / "report", review_gate={})

    with patch("zf.autoresearch.loop_iteration.run_autoresearch", side_effect=capture_child), \
         patch("zf.autoresearch.loop_iteration.cleanup_autoresearch_run"):
        run_iteration(cfg=cfg, scenario="incident-reproduction", run_id="test")
    resident = child_config["runtime"]["run_manager"]["resident_agent"]
    assert resident == {"enabled": False, "instance_id": "child-rm", "model": "gpt-6-luna",
                        "model_reasoning_effort": "high"}


@pytest.mark.parametrize(("backend", "model", "effort", "expected"), [
    ("codex", "gpt-6-luna", "", ("gpt-6-luna", "medium")),
    ("codex", "", "high", ("template", "high")),
    ("codex", "", "", ("template", "medium")),
    ("claude-code", "gpt-6-luna", "high", ("template", "medium")),
])
def test_resident_inherits_only_matching_nonempty_provider_fields(tmp_path, backend, model, effort, expected):
    from zf.autoresearch.orchestrator import RESIDENT_PROVIDER_KEY

    resident = {"enabled": False, "model": "template", "model_reasoning_effort": "medium", "transport": "tmux"}
    data = {"runtime": {"run_manager": {"backend": "codex", "resident_agent": resident}}}
    selection = {"backend": backend, "model": model, "model_reasoning_effort": effort,
                 "enabled": True, "transport": "other"}
    cfg = AutoresearchRunConfig(worktree=tmp_path, role_provider_settings={RESIDENT_PROVIDER_KEY: selection})
    _apply_autoresearch_controller_overrides(data, cfg=cfg, run_id="test")
    assert resident == {"enabled": False, "model": expected[0], "model_reasoning_effort": expected[1],
                        "transport": "tmux"}


@pytest.mark.parametrize("session", [None, {"native_delegation": False}])
def test_diagnostic_inherits_parent_sandbox_without_extra_tools(tmp_path, session):
    data = {"roles": [
        {"name": "planner", "backend": "codex", "allowed_tools": ["Read"],
         "provider_session": session},
        {"name": "verify", "backend": "mock"},
    ]}
    cfg = AutoresearchRunConfig(worktree=tmp_path, role_provider_settings={
        "planner": {"backend": "codex", "sandbox": "workspace-write"},
        "verify": {"backend": "codex", "sandbox": "workspace-write"},
    })
    _apply_autoresearch_controller_overrides(data, cfg=cfg, run_id="diagnosis")
    assert data["roles"][0]["provider_session"] == {
        **(session or {}), "sandbox": "workspace-write"}
    assert data["roles"][0]["allowed_tools"] == ["Read"]
    assert "provider_session" not in data["roles"][1]


@pytest.mark.parametrize(("explicit", "reflect_enabled", "reflect_backend", "expected_backend", "expected_model"), [
    ("", False, "", "codex", "gpt-6-luna"),
    ("", True, "claude-code", "claude-code", ""),
    ("claude", False, "", "claude-code", ""),
    ("codex", True, "claude-code", "codex", "gpt-6-luna"),
])
def test_loop_cli_uses_scoped_reflection_provider(tmp_path, monkeypatch, explicit, reflect_enabled,
                                                  reflect_backend, expected_backend, expected_model):
    import argparse
    import subprocess
    from zf.autoresearch.loop import EvalSnapshot

    (tmp_path / "zf.yaml").write_text(f'''version: "1.0"
project:
  name: test
  state_dir: state
runtime:
  run_manager:
    backend: codex
    resident_agent:
      enabled: false
      model: gpt-6-luna
      model_reasoning_effort: medium
    reflect:
      enabled: {str(reflect_enabled).lower()}
      backend: "{reflect_backend}"
      timeout_seconds: 47
''')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZF_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ZF_STATE_DIR", raising=False)
    monkeypatch.setenv("ZF_AUTORESEARCH_REFLECT_BACKEND", "claude-code")
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    argv = ["autoresearch", "loop", "--scenarios", "incident-reproduction", "--worktree", str(tmp_path / "child"),
            "--max-iterations", "1", "--fix-wait-strategy", "none", "--output-dir", str(tmp_path / "report")]
    if explicit:
        argv.extend(["--reflect-backend", explicit])
    args = parser.parse_args(argv)
    result = {"status": "passed", "tasks_done": 1, "expected_done": 1}
    with patch("zf.cli.autoresearch._real_autoresearch_fn", return_value=result), patch(
        "zf.cli.autoresearch._real_eval_collector", return_value=EvalSnapshot(1, 0, 0, 0.0, 0, 0, 1),
    ), patch(
        "zf.integrations.controller.helper.run_provider",
        return_value=subprocess.CompletedProcess([], 0, stdout='{"verdict":"best_so_far"}', stderr=""),
    ) as provider:
        args.func(args)
    provider.assert_called_once()
    assert (tmp_path / "report" / "journal.jsonl").is_file()
    command, = provider.call_args.args
    invocation = provider.call_args.kwargs
    assert invocation["backend"] == expected_backend
    assert invocation.get("model", "") == expected_model
    if expected_model:
        assert command[command.index("--model") + 1] == expected_model
        assert 'model_reasoning_effort="medium"' in command
    else:
        assert "--model" not in command
    if reflect_enabled and expected_backend == reflect_backend:
        assert invocation["timeout"] == 47
