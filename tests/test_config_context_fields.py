"""Tests for RoleConfig context window threshold fields + YAML/env load."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config, ConfigError
from zf.core.config.schema import RoleConfig


class TestDefaults:
    def test_context_window_default_200k(self):
        role = RoleConfig(name="dev")
        assert role.context_window_tokens == 200_000

    def test_recycle_threshold_default_0_6(self):
        role = RoleConfig(name="dev")
        assert role.recycle_threshold == 0.6
        assert role.context_warning_threshold == 0.6

    def test_context_compact_threshold_default_0_7(self):
        role = RoleConfig(name="dev")
        assert role.context_compact_threshold == 0.7

    def test_recycle_hard_cap_default_0_9(self):
        role = RoleConfig(name="dev")
        assert role.recycle_hard_cap == 0.9
        assert role.context_hard_cap == 0.9


class TestExplicitValues:
    def test_custom_window(self):
        role = RoleConfig(name="dev", context_window_tokens=1_000_000)
        assert role.context_window_tokens == 1_000_000

    def test_custom_threshold(self):
        role = RoleConfig(name="dev", recycle_threshold=0.5)
        assert role.recycle_threshold == 0.5
        assert role.context_warning_threshold == 0.5
        assert role.context_compact_threshold == 0.5

    def test_custom_split_thresholds(self):
        role = RoleConfig(
            name="dev",
            context_warning_threshold=0.55,
            context_compact_threshold=0.75,
            context_hard_cap=0.92,
        )
        assert role.context_warning_threshold == 0.55
        assert role.context_compact_threshold == 0.75
        assert role.context_hard_cap == 0.92
        assert role.recycle_threshold == 0.55
        assert role.recycle_hard_cap == 0.92

    def test_custom_hard_cap(self):
        role = RoleConfig(name="dev", recycle_hard_cap=0.85)
        assert role.recycle_hard_cap == 0.85
        assert role.context_hard_cap == 0.85


class TestValidation:
    def test_threshold_above_hard_cap_rejected(self):
        with pytest.raises((ValueError, AssertionError)):
            RoleConfig(
                name="dev",
                context_warning_threshold=0.6,
                context_compact_threshold=0.95,
                context_hard_cap=0.9,
            )

    def test_negative_threshold_rejected(self):
        with pytest.raises((ValueError, AssertionError)):
            RoleConfig(name="dev", recycle_threshold=-0.1)

    def test_threshold_above_1_rejected(self):
        with pytest.raises((ValueError, AssertionError)):
            RoleConfig(name="dev", recycle_threshold=1.5)

    def test_zero_context_window_rejected(self):
        with pytest.raises((ValueError, AssertionError)):
            RoleConfig(name="dev", context_window_tokens=0)


class TestYamlLoad:
    def test_loader_reads_context_fields(self, tmp_path: Path):
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "session: {tmux_session: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    context_window_tokens: 1000000\n"
            "    context_warning_threshold: 0.5\n"
            "    context_compact_threshold: 0.7\n"
            "    context_hard_cap: 0.85\n"
        )
        cfg = load_config(yml)
        role = cfg.roles[0]
        assert role.context_window_tokens == 1_000_000
        assert role.recycle_threshold == 0.5
        assert role.context_warning_threshold == 0.5
        assert role.context_compact_threshold == 0.7
        assert role.recycle_hard_cap == 0.85
        assert role.context_hard_cap == 0.85

    def test_loader_reads_legacy_context_fields(self, tmp_path: Path):
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "session: {tmux_session: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    recycle_threshold: 0.5\n"
            "    recycle_hard_cap: 0.85\n"
        )
        role = load_config(yml).roles[0]
        assert role.context_warning_threshold == 0.5
        assert role.context_compact_threshold == 0.5
        assert role.context_hard_cap == 0.85

    def test_loader_expands_dotenv_context_thresholds(self, tmp_path: Path):
        (tmp_path / ".env").write_text("ZF_CONTEXT_COMPACT_THRESHOLD=0.72\n")
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    context_compact_threshold: ${ZF_CONTEXT_COMPACT_THRESHOLD:-0.7}\n"
        )
        assert load_config(yml).roles[0].context_compact_threshold == 0.72

    def test_loader_uses_env_default_when_missing(self, tmp_path: Path):
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    context_compact_threshold: ${ZF_CONTEXT_COMPACT_THRESHOLD:-0.73}\n"
        )
        assert load_config(yml).roles[0].context_compact_threshold == 0.73

    def test_loader_rejects_missing_env_without_default(self, tmp_path: Path):
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
            "    context_compact_threshold: ${ZF_CONTEXT_COMPACT_THRESHOLD}\n"
        )
        with pytest.raises(ConfigError, match="ZF_CONTEXT_COMPACT_THRESHOLD"):
            load_config(yml)

    def test_loader_defaults_when_fields_absent(self, tmp_path: Path):
        yml = tmp_path / "zf.yaml"
        yml.write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "session: {tmux_session: t}\n"
            "roles:\n"
            "  - name: dev\n"
            "    backend: mock\n"
        )
        cfg = load_config(yml)
        role = cfg.roles[0]
        assert role.context_window_tokens == 200_000
        assert role.recycle_threshold == 0.6
        assert role.context_warning_threshold == 0.6
        assert role.context_compact_threshold == 0.7
        assert role.recycle_hard_cap == 0.9
        assert role.context_hard_cap == 0.9
