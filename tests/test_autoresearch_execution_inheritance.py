from pathlib import Path
from unittest.mock import patch

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
    from zf.autoresearch.loop import LoopResult
    (tmp_path / "zf.yaml").write_text('''version: "1.0"
project:
  name: test
  state_dir: state
workflow:
  run_limits:
    cost_budget_usd: 40
roles:
  - name: planner
    backend: codex
    model: gpt-6-astra
    model_reasoning_effort: high
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
    assert cfg.role_provider_settings["planner"] == {"backend": "codex", "model": "gpt-6-astra", "model_reasoning_effort": "high"}
