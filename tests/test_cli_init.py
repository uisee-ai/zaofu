"""Tests for zf init command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.agents_md import extract_managed_block, render_canonical_block
from zf.core.config.loader import load_config
from zf.integrations.feishu.routing import resolve_feishu_route
from zf.runtime.channel_projection import project_channels


def test_init_creates_zf_directory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Create a minimal zf.yaml
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    result = main(["init"])
    assert result == 0
    zf_dir = tmp_path / ".zf"
    assert zf_dir.is_dir()
    assert (zf_dir / "events.jsonl").exists()
    assert (zf_dir / "session.yaml").exists()
    assert (zf_dir / "kanban.json").exists()
    assert (zf_dir / "memory").is_dir()
    assert (zf_dir / "logs").is_dir()


def test_init_notes_flag_appends_to_claude_md(tmp_path: Path, monkeypatch):
    """`zf init --notes` writes operator notes into CLAUDE.md — same shared
    path as Web New Project 'description' (CLI/Web 入口不对称已抹平)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')

    result = main(["init", "--notes", "团队约定:只用 pnpm;src/ 下不放测试。"])

    assert result == 0
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "团队约定:只用 pnpm" in claude_md


def test_init_creates_project_instruction_docs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')

    result = main(["init"])

    assert result == 0
    agents_md = tmp_path / "AGENTS.md"
    claude_md = tmp_path / "CLAUDE.md"
    assert agents_md.exists()
    assert claude_md.exists()
    assert extract_managed_block(agents_md.read_text(encoding="utf-8")) == (
        render_canonical_block().rstrip("\n")
    )
    agents_text = agents_md.read_text(encoding="utf-8")
    assert "## Harness Health Signals" in agents_text
    assert "zf validate --instructions" in agents_text
    assert "zf update agents-md --check" in agents_text
    assert "verification evidence" in agents_text
    assert "handoff" in agents_text
    assert "AGENTS.md" in claude_md.read_text(encoding="utf-8")


def test_init_preserves_existing_instruction_docs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    (tmp_path / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Local Rules\n- keep this line\n",
        encoding="utf-8",
    )
    existing_claude = "# Existing\n\nDo not replace this file.\n"
    (tmp_path / "CLAUDE.md").write_text(existing_claude, encoding="utf-8")

    result = main(["init"])

    assert result == 0
    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "- keep this line" in agents_text
    assert extract_managed_block(agents_text) == render_canonical_block().rstrip("\n")
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == existing_claude


def test_init_can_skip_project_instruction_docs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')

    result = main(["init", "--skip-instruction-docs"])

    assert result == 0
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_init_scaffolds_feishu_default_channel_route_when_inbound_enabled(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  feishu_inbound:\n"
        "    enabled: true\n"
    )

    result = main(["init"])

    assert result == 0
    feishu_yaml = tmp_path / "feishu.yaml"
    assert feishu_yaml.exists()
    text = feishu_yaml.read_text(encoding="utf-8")
    assert '"${ZF_FEISHU_INBOUND_CHAT_ID:-*}"' in text
    assert "ZF_LEADER_FEISHU_OPENID" in text
    assert "FEISHU_KANBAN" in text
    assert "FEISHU_RUNM" in text
    assert "zaofu" in text
    assert "zaofu-leader" in text
    assert "ZF_PM_FEISHU_OPENID" in text
    assert "ZF_PM_FEISHU_INBOUND_CHAT_ID" in text
    monkeypatch.setenv("ZF_LEADER_FEISHU_OPENID", "ou_leader")
    cfg = load_config(tmp_path / "zf.yaml")
    route = resolve_feishu_route(cfg, "any-chat")
    assert route is not None
    assert route.target == "channel"
    assert route.channel_id == "zaofu"
    assert route.default_member == "zaofu-leader"
    user = cfg.integrations.feishu_identity.users["ou_leader"]
    assert user.operator == "zaofu-leader"
    assert user.level == "approver"

    projection = project_channels(tmp_path / ".zf")
    channel = next(
        item for item in projection["channels"]
        if item["channel_id"] == "zaofu"
    )
    member = next(
        item for item in channel["members"]
        if item["member_id"] == "zaofu-leader"
    )
    assert member["permission_profile"] == "project_writer"
    assert member["is_default_responder"] is True
    assert channel["discussion"]["mode"] == "leader_delegation"


def test_init_feishu_template_binds_product_manager_to_kanban_agent(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  feishu_inbound:\n"
        "    enabled: true\n"
    )
    assert main(["init"]) == 0

    monkeypatch.setenv("ZF_PM_FEISHU_OPENID", "ou_pm")
    monkeypatch.setenv("ZF_PM_FEISHU_INBOUND_CHAT_ID", "oc_pm")
    cfg = load_config(tmp_path / "zf.yaml")

    route = resolve_feishu_route(cfg, "oc_pm")
    assert route is not None
    assert route.target == "kanban_agent"
    assert route.default_member == "zf-product-manager"
    user = cfg.integrations.feishu_identity.users["ou_pm"]
    assert user.operator == "zf-product-manager"
    assert user.level == "operator"


def test_init_feishu_template_prefers_app_id_routes_for_multi_bot_group(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  feishu_inbound:\n"
        "    enabled: true\n"
    )
    monkeypatch.setenv("FEISHU_KANBAN", "cli_kanban")
    monkeypatch.setenv("FEISHU_RUNM", "cli_runm")
    assert main(["init"]) == 0

    cfg = load_config(tmp_path / "zf.yaml")

    kanban = resolve_feishu_route(cfg, "oc_shared", app_id="cli_kanban")
    assert kanban is not None
    assert kanban.target == "kanban_agent"
    assert kanban.default_member == "zf-product-manager"

    run_manager = resolve_feishu_route(cfg, "oc_shared", app_id="cli_runm")
    assert run_manager is not None
    assert run_manager.target == "run_manager"
    assert run_manager.default_member == "run-manager"

    fallback = resolve_feishu_route(cfg, "oc_shared")
    assert fallback is not None
    assert fallback.target == "channel"
    assert fallback.channel_id == "zaofu"


def test_init_does_not_scaffold_feishu_route_by_default(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')

    result = main(["init"])

    assert result == 0
    assert not (tmp_path / "feishu.yaml").exists()


def test_init_kanban_is_valid_json(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    data = json.loads((tmp_path / ".zf" / "kanban.json").read_text())
    assert isinstance(data, list)


def test_init_emits_session_started_event(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    events_text = (tmp_path / ".zf" / "events.jsonl").read_text()
    assert "session.started" in events_text


def test_init_double_init_errors(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    result = main(["init"])
    assert result != 0  # should error on double init


def test_init_force_reinitializes(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    result = main(["init", "--force"])
    assert result == 0


def test_init_without_zf_yaml_warns(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = main(["init"])
    # Should still succeed (creates with defaults) but warn
    assert result == 0


def test_init_uses_project_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )

    result = main(["init"])

    assert result == 0
    state_dir = tmp_path / "runtime-state"
    assert state_dir.is_dir()
    assert (state_dir / "events.jsonl").exists()
    assert (state_dir / "kanban.json").exists()
    assert not (tmp_path / ".zf").exists()


def test_init_explicit_state_dir_overrides_project_config(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )

    result = main(["init", "--state-dir", "override-state"])

    assert result == 0
    assert (tmp_path / "override-state" / "events.jsonl").exists()
    assert not (tmp_path / "runtime-state").exists()


def test_init_installs_pre_commit_hook_in_git_repo(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    assert main(["init"]) == 0

    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert hook.stat().st_mode & 0o111  # executable
    assert "运行时真相" in hook.read_text(encoding="utf-8")


def test_init_preserves_existing_pre_commit_hook(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")

    assert main(["init"]) == 0

    assert (hooks / "pre-commit").read_text() == "#!/bin/sh\necho mine\n"


def test_init_no_git_hooks_flag_skips_install(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    assert main(["init", "--no-git-hooks"]) == 0

    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_init_env_check_failure_exits_nonzero(tmp_path: Path, monkeypatch):
    from zf.runtime.env_preflight import EnvCheck

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    monkeypatch.setattr(
        "zf.runtime.env_preflight.check_hook_command",
        lambda cmd: EnvCheck("hook_command", False, True, "hook 命令 exit 1: shim broken"),
    )

    assert main(["init", "--env-check"]) == 1


def test_init_setup_contract_hint_for_node_project(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-lock.yaml").write_text("")

    assert main(["init"]) == 0

    out = capsys.readouterr().out
    assert "project.scripts.setup" in out
    assert "setup: pnpm install" in out


def test_init_setup_hint_absent_when_declared(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "  scripts:\n"
        "    setup: pnpm install\n"
    )
    (tmp_path / "package.json").write_text("{}")

    assert main(["init"]) == 0

    assert "建议在 zf.yaml 加" not in capsys.readouterr().out
