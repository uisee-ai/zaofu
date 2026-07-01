"""Tests for G-INST-5: role_sessions seed by instance_id.

StreamJsonTransport and Orchestrator now both pass instance_id to
RoleSessionRegistry. Two replicas of the same role type should get
independent deterministic session_ids.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "role_sessions.yaml"


class TestInstanceBasedSeeding:
    def test_two_instances_get_different_session_ids(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/tmp/zf")
        uuid_1 = reg.get_or_create("dev-1")
        uuid_2 = reg.get_or_create("dev-2")
        assert uuid_1 != uuid_2

    def test_session_id_stable_across_registry_reloads(self, registry_path):
        reg1 = RoleSessionRegistry(registry_path, project_root="/tmp/zf")
        sid = reg1.get_or_create("dev-1")

        # New registry from same file reads it back
        reg2 = RoleSessionRegistry(registry_path, project_root="/tmp/zf")
        sid2 = reg2.get_or_create("dev-1")
        assert sid == sid2

    def test_same_instance_id_deterministic_uuid5(self, registry_path):
        """Without persistence, same (project_root, instance_id) must
        still generate the same uuid via uuid5."""
        reg_a = RoleSessionRegistry(registry_path, project_root="/p")
        sid_a = reg_a.get_or_create("dev-1")

        # Simulate a fresh project with no file
        other = registry_path.parent / "other.yaml"
        reg_b = RoleSessionRegistry(other, project_root="/p")
        sid_b = reg_b.get_or_create("dev-1")
        assert sid_a == sid_b

    def test_different_project_roots_different_uuids(self, tmp_path):
        r_a = RoleSessionRegistry(tmp_path / "a.yaml", project_root="/proj-a")
        r_b = RoleSessionRegistry(tmp_path / "b.yaml", project_root="/proj-b")
        sid_a = r_a.get_or_create("dev-1")
        sid_b = r_b.get_or_create("dev-1")
        assert sid_a != sid_b


class TestLegacyRoleNameStillWorks:
    def test_legacy_caller_passing_name_still_gets_stable_uuid(self, registry_path):
        """Single-instance configs pass role.name = instance_id = "dev"
        and should get a stable uuid indistinguishable from passing
        "dev" as an instance_id. Backward compat."""
        reg = RoleSessionRegistry(registry_path, project_root="/tmp/zf")
        a = reg.get_or_create("dev")
        b = reg.get_or_create("dev")
        assert a == b
