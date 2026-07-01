"""Tests for G-INST-1 / G-INST-2: RoleConfig instance_id + replicas.

Multi-instance support starts at the config schema. Every existing
`RoleConfig.name` continues to work unchanged; new `instance_id` and
`replicas` fields open the door to N copies of a role_type without
breaking backward compatibility.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.config.loader import load_config


class TestRoleConfigInstanceIdField:
    def test_instance_id_defaults_to_name(self):
        role = RoleConfig(name="dev")
        assert role.instance_id == "dev"

    def test_explicit_instance_id_overrides_default(self):
        role = RoleConfig(name="dev", instance_id="dev-primary")
        assert role.instance_id == "dev-primary"
        assert role.name == "dev"  # name field unchanged

    def test_instance_id_empty_string_falls_back_to_name(self):
        """Defensive: if someone sets instance_id="" explicitly, still
        treat it as 'use name'."""
        role = RoleConfig(name="review", instance_id="")
        assert role.instance_id == "review"


class TestRoleConfigReplicasField:
    def test_replicas_defaults_to_1(self):
        role = RoleConfig(name="dev")
        assert role.replicas == 1

    def test_explicit_replicas(self):
        role = RoleConfig(name="dev", replicas=3)
        assert role.replicas == 3

    def test_replicas_below_1_rejected(self):
        with pytest.raises((ValueError, AssertionError)):
            RoleConfig(name="dev", replicas=0)


class TestZfConfigReplicasExpansion:
    def test_replicas_1_yields_single_instance(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=1)],
        )
        assert len(cfg.roles) == 1
        assert cfg.roles[0].instance_id == "dev"
        assert cfg.roles[0].name == "dev"

    def test_replicas_3_expands_to_3_instances(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=3)],
        )
        assert len(cfg.roles) == 3
        names = [r.name for r in cfg.roles]
        instance_ids = [r.instance_id for r in cfg.roles]
        assert names == ["dev", "dev", "dev"]
        assert instance_ids == ["dev-1", "dev-2", "dev-3"]
        # Every instance should have replicas=1 after expansion (avoid re-expanding)
        assert all(r.replicas == 1 for r in cfg.roles)

    def test_mixed_roles_expand_independently(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="arch", replicas=1),
                RoleConfig(name="dev", replicas=2),
                RoleConfig(name="review", replicas=1),
            ],
        )
        instance_ids = [r.instance_id for r in cfg.roles]
        assert instance_ids == ["arch", "dev-1", "dev-2", "review"]

    def test_explicit_instance_id_on_single_replica_preserved(self):
        """If user writes instance_id explicitly + replicas=1, keep it."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", instance_id="dev-primary")],
        )
        assert cfg.roles[0].instance_id == "dev-primary"

    def test_expansion_is_idempotent(self):
        """Running __post_init__ twice (e.g. on a re-loaded config)
        must not re-explode already-expanded instances."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=2)],
        )
        first = [(r.name, r.instance_id) for r in cfg.roles]
        cfg.__post_init__()  # simulate reloading
        second = [(r.name, r.instance_id) for r in cfg.roles]
        assert first == second


class TestReplicaExpansionPreservesAllFields:
    """P0-REPLICA-FIELDS-01: replica expansion must preserve every
    RoleConfig field except the 4 that explicitly change per replica.

    Pre-fix the expansion was a hand-written whitelist that silently
    dropped 8 runtime-consumed fields (plugins / skills / agent /
    max_rework_attempts / orphan_warning_seconds /
    orphan_escalate_seconds / drain_hold_seconds / per-replica budget),
    causing dev-1 / dev-2 to behave differently from the YAML the user
    actually wrote. Those fields feed dispatch / lifecycle / backend
    args, so the silent drop mutated team behavior.
    """

    def _expand(self, role: RoleConfig) -> list[RoleConfig]:
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[role],
        )
        return cfg.roles

    def test_plugins_skills_agent_preserved(self):
        role = RoleConfig(
            name="dev",
            replicas=2,
            plugins=["dir-a", "dir-b"],
            skills=["sk-1", "sk-2"],
            agent="my-agent",
        )
        instances = self._expand(role)
        assert [r.instance_id for r in instances] == ["dev-1", "dev-2"]
        for r in instances:
            assert r.plugins == ["dir-a", "dir-b"]
            assert r.skills == ["sk-1", "sk-2"]
            assert r.agent == "my-agent"

    def test_lifecycle_timeouts_preserved(self):
        role = RoleConfig(
            name="dev",
            replicas=2,
            max_rework_attempts=5,
            orphan_warning_seconds=120.0,
            orphan_escalate_seconds=240.0,
            drain_hold_seconds=60.0,
        )
        for r in self._expand(role):
            assert r.max_rework_attempts == 5
            assert r.orphan_warning_seconds == 120.0
            assert r.orphan_escalate_seconds == 240.0
            assert r.drain_hold_seconds == 60.0

    def test_loader_preserves_lifecycle_timeouts_through_replica_expansion(
        self,
        tmp_path: Path,
    ):
        path = tmp_path / "zf.yaml"
        path.write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: test\n"
            "roles:\n"
            "  - name: dev\n"
            "    replicas: 2\n"
            "    max_rework_attempts: 9\n"
            "    orphan_warning_seconds: 12\n"
            "    orphan_escalate_seconds: 34\n"
            "    drain_hold_seconds: 56\n"
        )

        cfg = load_config(path)

        assert [r.instance_id for r in cfg.roles] == ["dev-1", "dev-2"]
        for role in cfg.roles:
            assert role.max_rework_attempts == 9
            assert role.orphan_warning_seconds == 12.0
            assert role.orphan_escalate_seconds == 34.0
            assert role.drain_hold_seconds == 56.0

    def test_backends_list_maps_to_per_instance(self):
        role = RoleConfig(
            name="dev",
            replicas=2,
            backends=["claude-code", "codex"],
        )
        instances = self._expand(role)
        assert [r.backend for r in instances] == ["claude-code", "codex"]
        # backlog T2 case 4: instance role 的 backends 必须置空,
        # 避免重新进入 __post_init__ 时被二次展开或语义混乱。
        for r in instances:
            assert r.backends == []
            assert r.replicas == 1

    def test_instance_replicas_is_1_after_expansion(self):
        role = RoleConfig(name="dev", replicas=3)
        for r in self._expand(role):
            assert r.replicas == 1
            assert r.backends == []

    def test_list_fields_are_shallow_copied(self):
        """Mutating one instance's list must not leak to siblings."""
        role = RoleConfig(
            name="dev",
            replicas=2,
            allowed_tools=["Read"],
            triggers=["task.assigned"],
            publishes=["dev.build.done"],
            stages=["impl"],
            plugins=["p1"],
            skills=["sk1"],
        )
        instances = self._expand(role)
        instances[0].allowed_tools.append("Write")
        instances[0].plugins.append("p2")
        assert instances[1].allowed_tools == ["Read"]
        assert instances[1].plugins == ["p1"]


class TestReplicaExpansionFieldClosure:
    """P0-REPLICA-FIELDS-01 / T3: defensive bulk check.

    When a future field is added to RoleConfig, this test fails
    unless expansion preserves it (or the field name appears in
    _OVERRIDABLE). Prevents the silent-drop pattern from coming back
    via the same hand-written-whitelist mechanism the original bug
    used.
    """

    def test_every_non_override_field_is_inherited(self):
        from dataclasses import fields

        # Build a RoleConfig where every list/scalar field has a
        # non-default sentinel value, so any field that silently falls
        # back to the default is detectable.
        role = RoleConfig(
            name="dev",
            backend="codex",
            backends=["codex", "claude-code"],
            model="gpt-5",
            allowed_tools=["Read", "Edit"],
            permission_mode="allowlist",
            transport="stream-json",
            stuck_threshold_seconds=42.0,
            replicas=2,
            context_window_tokens=300_000,
            recycle_threshold=0.5,
            recycle_hard_cap=0.8,
            max_rework_attempts=7,
            orphan_warning_seconds=11.0,
            orphan_escalate_seconds=22.0,
            drain_hold_seconds=33.0,
            budget_usd=12.5,
            stages=["s1", "s2"],
            triggers=["t1"],
            publishes=["p1"],
            plugins=["pl1"],
            skills=["sk1"],
            agent="ag1",
        )
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[role],
        )
        # Fields that legitimately differ per instance.
        OVERRIDES = {"backend", "backends", "instance_id", "replicas"}
        for instance in cfg.roles:
            for f in fields(RoleConfig):
                if f.name in OVERRIDES:
                    continue
                expected = getattr(role, f.name)
                actual = getattr(instance, f.name)
                assert actual == expected, (
                    f"replica {instance.instance_id} dropped field "
                    f"{f.name!r}: got {actual!r}, expected {expected!r}"
                )
