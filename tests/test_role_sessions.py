"""Tests for per-role session_id registry."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry


def test_get_or_create_returns_uuid(tmp_path: Path):
    reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path))
    sid = reg.get_or_create("dev")
    assert isinstance(sid, uuid.UUID)


def test_same_role_same_project_returns_same_id(tmp_path: Path):
    reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root="/x/y")
    a = reg.get_or_create("dev")
    b = reg.get_or_create("dev")
    assert a == b


def test_different_roles_get_different_ids(tmp_path: Path):
    reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root="/x/y")
    dev = reg.get_or_create("dev")
    review = reg.get_or_create("review")
    assert dev != review


def test_different_projects_get_different_ids(tmp_path: Path):
    reg_a = RoleSessionRegistry(tmp_path / "a.yaml", project_root="/proj/a")
    reg_b = RoleSessionRegistry(tmp_path / "b.yaml", project_root="/proj/b")
    assert reg_a.get_or_create("dev") != reg_b.get_or_create("dev")


def test_id_is_deterministic_across_registry_instances(tmp_path: Path):
    """A fresh registry pointing at the same project_root produces the same
    UUID for the same role name — no need to read the file at all."""
    path = tmp_path / "role_sessions.yaml"
    a = RoleSessionRegistry(path, project_root="/proj/x").get_or_create("dev")
    # delete the file — the UUID must still come back the same
    if path.exists():
        path.unlink()
    b = RoleSessionRegistry(path, project_root="/proj/x").get_or_create("dev")
    assert a == b


def test_persists_to_disk(tmp_path: Path):
    path = tmp_path / "role_sessions.yaml"
    reg = RoleSessionRegistry(path, project_root="/proj/x")
    reg.get_or_create("dev")
    reg.get_or_create("review")
    assert path.exists()
    text = path.read_text()
    assert "dev" in text
    assert "review" in text


def test_load_from_existing_file(tmp_path: Path):
    """A registry instance should load existing entries from disk."""
    path = tmp_path / "role_sessions.yaml"
    reg1 = RoleSessionRegistry(path, project_root="/proj/x")
    sid_dev = reg1.get_or_create("dev")
    reg2 = RoleSessionRegistry(path, project_root="/proj/x")
    assert reg2.get_or_create("dev") == sid_dev


def test_uses_uuid5_namespace_dns(tmp_path: Path):
    """The deterministic UUID is uuid5(NAMESPACE_DNS, project_root + ':' + role)."""
    reg = RoleSessionRegistry(tmp_path / "x.yaml", project_root="/proj/x")
    expected = uuid.uuid5(uuid.NAMESPACE_DNS, "/proj/x:dev")
    assert reg.get_or_create("dev") == expected
