"""Tests for B-MIXEDBACKEND-01 briefing roster rendering.

When a role has replicas with DIFFERENT backends (e.g. dev-1 claude,
dev-2 codex), the briefing's `## 可派发的 worker` section must list
each instance on its own line so Layer 2 can see the (instance_id,
backend) pairs. Homogeneous pools keep the one-line summary form.
"""

from __future__ import annotations

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.runtime.orchestrator_briefing import _render_worker_roster


def _cfg(roles: list[RoleConfig]) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=roles,
    )


class TestHomogeneousRoster:
    def test_single_replica_role(self):
        cfg = _cfg([RoleConfig(name="arch", backend="claude-code")])
        text = _render_worker_roster(cfg)
        assert "`arch` — 1 replica, backend=`claude-code`" in text

    def test_multi_replica_same_backend_collapses(self):
        cfg = _cfg(
            [RoleConfig(name="dev", backend="claude-code", replicas=3)]
        )
        text = _render_worker_roster(cfg)
        # One summary line, no per-instance lines
        assert "`dev` — 3 replicas, backend=`claude-code`" in text
        assert "`dev-1` — backend" not in text

    def test_orchestrator_role_excluded(self):
        """Layer 2 never dispatches to itself."""
        cfg = _cfg(
            [
                RoleConfig(name="orchestrator", backend="claude-code"),
                RoleConfig(name="dev", backend="claude-code"),
            ]
        )
        text = _render_worker_roster(cfg)
        assert "orchestrator" not in text
        assert "`dev`" in text


class TestMixedBackendRoster:
    def test_mixed_dev_pool_lists_per_instance(self):
        cfg = _cfg(
            [
                RoleConfig(
                    name="dev",
                    replicas=2,
                    backends=["claude-code", "codex"],
                ),
            ]
        )
        text = _render_worker_roster(cfg)
        # Header notes the mixed pool
        assert "`dev` — mixed backend pool (2 replicas)" in text
        # Per-instance lines show (instance_id, backend) pairs
        assert "`dev-1` — backend=`claude-code`" in text
        assert "`dev-2` — backend=`codex`" in text

    def test_mixed_test_pool_lists_per_instance(self):
        cfg = _cfg(
            [
                RoleConfig(
                    name="test",
                    replicas=2,
                    backends=["claude-code", "codex"],
                ),
            ]
        )
        text = _render_worker_roster(cfg)
        assert "mixed backend pool" in text
        assert "`test-1` — backend=`claude-code`" in text
        assert "`test-2` — backend=`codex`" in text

    def test_homogeneous_and_mixed_pools_coexist(self):
        cfg = _cfg(
            [
                RoleConfig(name="arch", backend="claude-code"),
                RoleConfig(
                    name="dev",
                    replicas=2,
                    backends=["claude-code", "codex"],
                ),
                RoleConfig(name="review", backend="claude-code"),
                RoleConfig(name="test", backend="codex"),
            ]
        )
        text = _render_worker_roster(cfg)
        # homogeneous: summary line
        assert "`arch` — 1 replica, backend=`claude-code`" in text
        assert "`review` — 1 replica, backend=`claude-code`" in text
        assert "`test` — 1 replica, backend=`codex`" in text
        # mixed: per-instance lines
        assert "`dev` — mixed backend pool (2 replicas)" in text
        assert "`dev-1` — backend=`claude-code`" in text
        assert "`dev-2` — backend=`codex`" in text


class TestRosterGuidanceStill:
    def test_guidance_paragraph_present(self):
        """The 'pass role.name, Layer 1 picks replica' guidance must
        still be emitted — mixed pools don't change the default advice."""
        cfg = _cfg(
            [
                RoleConfig(
                    name="dev",
                    replicas=2,
                    backends=["claude-code", "codex"],
                ),
            ]
        )
        text = _render_worker_roster(cfg)
        assert "zf kanban assign TASK-XXX dev" in text
        assert "Layer 1 会自动" in text or "Layer 1" in text
