"""A sprint — loader nudges users away from implicit bypass mode.

Background: RoleConfig.permission_mode defaults to "bypass" which makes
the agent run with --dangerously-skip-permissions. This is fine for
trusted local development but not what a new user reading docs and
copying a snippet would expect. Loader emits a per-role warning when:

  - permission_mode is NOT explicitly set in the yaml dict
  - the resolved value is "bypass"
  - backend is claude-code or codex (not mock/python)

Adding `permission_mode: bypass` explicitly silences the warning —
that's the acknowledgment path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config


def _yaml(tmp_path: Path, role_data: dict) -> Path:
    cfg = {
        "version": "1.0",
        "project": {"name": "t"},
        "roles": [role_data],
    }
    p = tmp_path / "zf.yaml"
    p.write_text(yaml.dump(cfg))
    return p


class TestImplicitBypassWarning:
    def test_implicit_bypass_warns(self, tmp_path: Path, capsys):
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "claude-code",
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode: bypass" in err
        assert "dev" in err
        assert "least privilege" in err

    def test_explicit_bypass_silences_warning(self, tmp_path: Path, capsys):
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "claude-code",
            "permission_mode": "bypass",
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode: bypass" not in err

    def test_allowlist_no_warning(self, tmp_path: Path, capsys):
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "claude-code",
            "permission_mode": "allowlist",
            "allowed_tools": ["Read"],
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode" not in err

    def test_mock_backend_no_warning(self, tmp_path: Path, capsys):
        # Mock/python backends never spawn a real agent; warning is irrelevant.
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "mock",
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode" not in err

    def test_python_backend_no_warning(self, tmp_path: Path, capsys):
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "python",
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode" not in err

    def test_codex_backend_warns_too(self, tmp_path: Path, capsys):
        # codex also runs with --dangerously-bypass-approvals-and-sandbox
        # by default, same security posture as claude-code bypass.
        load_config(_yaml(tmp_path, {
            "name": "dev", "backend": "codex",
        }))
        err = capsys.readouterr().err
        assert "implicit permission_mode: bypass" in err

    def test_warning_is_per_role(self, tmp_path: Path, capsys):
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": [
                {"name": "dev", "backend": "claude-code"},
                {"name": "test", "backend": "claude-code"},
                {"name": "review", "backend": "claude-code",
                 "permission_mode": "bypass"},  # acknowledged
            ],
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        load_config(p)
        err = capsys.readouterr().err
        # dev + test warned, review acknowledged
        assert err.count("implicit permission_mode") == 2
        assert "'dev'" in err
        assert "'test'" in err
        assert "'review'" not in err


class TestLoadingShippedExamplesIsClean:
    """Sanity check: every example yaml under examples/ should load
    without producing the implicit-bypass warning. If a future preset
    adds a role without explicit permission_mode, this catches it."""

    @pytest.mark.parametrize("example", [
        "minimal.yaml", "code-assist.yaml", "design-first.yaml",
        "safe-team.yaml", "zf.yaml",
    ])
    def test_example_loads_without_implicit_bypass_warning(
        self, example: str, capsys
    ):
        repo_root = Path(__file__).parent.parent
        p = repo_root / "examples" / example
        load_config(p)
        err = capsys.readouterr().err
        assert "implicit permission_mode" not in err, (
            f"{example} produced an implicit-bypass warning — "
            f"add `permission_mode: bypass` (or allowlist) explicitly to "
            f"every role:\n{err}"
        )
