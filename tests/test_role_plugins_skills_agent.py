"""P-Y2 — RoleConfig.plugins / skills / agent fields.

These three optional fields let yaml configs attach Claude-specific
extensions to a role. Codex backends ignore them with a stderr warning
at load time (P-Y3).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig, ZfConfig, ProjectConfig
from zf.runtime.backend import ClaudeCodeAdapter, CodexAdapter
from zf.runtime.injection import generate_role_instructions


class TestRoleConfigDefaults:
    def test_plugins_skills_agent_default_empty(self):
        r = RoleConfig(name="dev")
        assert r.plugins == []
        assert r.skills == []
        assert r.agent == ""


def _yaml_with_role(tmp_path: Path, role_data: dict) -> Path:
    cfg = {
        "version": "1.0",
        "project": {"name": "t"},
        "roles": [{"name": "dev", "backend": "claude-code", **role_data}],
    }
    p = tmp_path / "zf.yaml"
    p.write_text(yaml.dump(cfg))
    return p


class TestLoaderParsesNewFields:
    def test_plugins_loaded_as_list(self, tmp_path: Path):
        cfg = load_config(_yaml_with_role(tmp_path, {
            "plugins": ["~/.claude/plugins/a", "~/.claude/plugins/b"],
        }))
        assert cfg.roles[0].plugins == [
            "~/.claude/plugins/a", "~/.claude/plugins/b"
        ]

    def test_skills_loaded_as_list(self, tmp_path: Path):
        cfg = load_config(_yaml_with_role(tmp_path, {
            "skills": ["five-axis-review", "tdd"],
        }))
        assert cfg.roles[0].skills == ["five-axis-review", "tdd"]

    def test_agent_loaded_as_string(self, tmp_path: Path):
        cfg = load_config(_yaml_with_role(tmp_path, {
            "agent": "review-specialist",
        }))
        assert cfg.roles[0].agent == "review-specialist"

    def test_missing_fields_default_to_empty(self, tmp_path: Path):
        cfg = load_config(_yaml_with_role(tmp_path, {}))
        assert cfg.roles[0].plugins == []
        assert cfg.roles[0].skills == []
        assert cfg.roles[0].agent == ""


class TestCodexUnsupportedFieldsWarning:
    def test_codex_with_plugins_warns_to_stderr(self, tmp_path: Path, capsys):
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{
                "name": "dev", "backend": "codex",
                "plugins": ["/tmp/some-plugin"],
            }],
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        load_config(p)
        err = capsys.readouterr().err
        assert "codex does not support" in err
        assert "plugins" in err

    def test_codex_with_agent_warns(self, tmp_path: Path, capsys):
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{
                "name": "dev", "backend": "codex",
                "agent": "code-reviewer",
            }],
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        load_config(p)
        err = capsys.readouterr().err
        assert "codex does not support" in err
        assert "agent" in err

    def test_codex_with_skills_does_not_warn(self, tmp_path: Path, capsys):
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{
                "name": "dev", "backend": "codex",
                "skills": ["any-skill"],
            }],
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        load_config(p)
        err = capsys.readouterr().err
        assert "codex does not support" not in err

    def test_claude_with_plugins_no_warning(self, tmp_path: Path, capsys):
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [{
                "name": "dev", "backend": "claude-code",
                "plugins": ["/tmp/x"], "agent": "y",
            }],
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        load_config(p)
        err = capsys.readouterr().err
        assert "does not support" not in err


class TestClaudeCodeAdapterRendering:
    def test_plugins_render_as_repeated_plugin_dir(self):
        role = RoleConfig(
            name="dev", backend="claude-code", permission_mode="bypass",
            plugins=["/path/a", "/path/b"],
        )
        cmd = ClaudeCodeAdapter().build_command(role)
        assert cmd.count("--plugin-dir") == 2
        a = cmd.index("--plugin-dir")
        assert cmd[a + 1] == "/path/a"
        b = cmd.index("--plugin-dir", a + 2)
        assert cmd[b + 1] == "/path/b"

    def test_agent_renders_as_agent_flag(self):
        role = RoleConfig(
            name="dev", backend="claude-code", permission_mode="bypass",
            agent="my-agent",
        )
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--agent" in cmd
        assert cmd[cmd.index("--agent") + 1] == "my-agent"

    def test_no_plugins_no_flag(self):
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--plugin-dir" not in cmd
        assert "--agent" not in cmd


class TestCodexAdapterIgnores:
    def test_codex_argv_does_not_include_plugin_or_agent(self):
        role = RoleConfig(
            name="dev", backend="codex",
            plugins=["/tmp/x"], agent="y", skills=["s"],
        )
        cmd = CodexAdapter().build_command(role)
        assert "--plugin-dir" not in cmd
        assert "--agent" not in cmd


class TestSkillsInInstructions:
    def test_skills_appear_in_role_instructions(self):
        config = ZfConfig(project=ProjectConfig(name="proj"))
        role = RoleConfig(
            name="review", backend="claude-code",
            skills=["five-axis-review", "tdd"],
        )
        out = generate_role_instructions(config, role)
        # 7b40f1f split "## Available Skills" into auto-injected vs
        # load-on-demand sections; bare names land in the latter.
        assert "## Load-On-Demand Skills" in out
        assert "/five-axis-review" in out
        assert "/tdd" in out

    def test_no_skills_no_section(self):
        config = ZfConfig(project=ProjectConfig(name="proj"))
        role = RoleConfig(name="dev", backend="claude-code")
        out = generate_role_instructions(config, role)
        assert "## Auto-Injected Skills" not in out
        assert "## Load-On-Demand Skills" not in out
