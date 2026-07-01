"""Tests for B-MIXEDBACKEND-01: RoleConfig.backends per-replica backend override.

A role with `replicas > 1` can declare `backends: [claude-code, codex]` so
each replica carries its own backend. Expansion in ZfConfig.__post_init__
then produces `dev-1 backend=claude-code`, `dev-2 backend=codex`.

Mutual-exclusivity with `backend: str` is enforced by the yaml loader
(see tests/test_config_loader.py additions), not in RoleConfig itself —
the dataclass still accepts both for direct instantiation convenience.
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)


class TestRoleConfigBackendsField:
    def test_backends_defaults_to_empty_list(self):
        role = RoleConfig(name="dev")
        assert role.backends == []

    def test_backends_length_must_equal_replicas(self):
        with pytest.raises(ValueError, match="len.backends"):
            RoleConfig(name="dev", replicas=2, backends=["claude-code"])

    def test_backends_with_empty_entry_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            RoleConfig(name="dev", replicas=2, backends=["claude-code", ""])

    def test_empty_backends_list_is_legal(self):
        """The common case: no per-replica override."""
        role = RoleConfig(name="dev", replicas=3, backends=[])
        assert role.backends == []

    def test_backends_len_equal_replicas_accepted(self):
        role = RoleConfig(
            name="dev",
            replicas=2,
            backends=["claude-code", "codex"],
        )
        assert role.backends == ["claude-code", "codex"]


class TestZfConfigMixedBackendsExpansion:
    def test_mixed_backends_expand_per_replica(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    replicas=2,
                    backends=["claude-code", "codex"],
                ),
            ],
        )
        assert len(cfg.roles) == 2
        assert cfg.roles[0].instance_id == "dev-1"
        assert cfg.roles[0].backend == "claude-code"
        assert cfg.roles[1].instance_id == "dev-2"
        assert cfg.roles[1].backend == "codex"
        # After expansion each instance has replicas=1 and no backends list
        assert all(r.replicas == 1 for r in cfg.roles)
        assert all(r.backends == [] for r in cfg.roles)

    def test_no_backends_list_falls_back_to_singular_backend(self):
        """Legacy shape: `backend: claude-code, replicas: 2`."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=2, backend="claude-code")],
        )
        assert len(cfg.roles) == 2
        assert all(r.backend == "claude-code" for r in cfg.roles)

    def test_three_way_mixed_pool(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    replicas=3,
                    backends=["claude-code", "codex", "claude-code"],
                ),
            ],
        )
        assert [r.backend for r in cfg.roles] == [
            "claude-code",
            "codex",
            "claude-code",
        ]
        assert [r.instance_id for r in cfg.roles] == ["dev-1", "dev-2", "dev-3"]
