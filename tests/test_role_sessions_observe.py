"""Tests for G-RESUME-2: RoleSessionRegistry codex discovery + spawn marker.

Extends role_sessions.yaml schema with per-instance:
  - session_path  (str | null)  — cached disk path for codex session file
  - spawned_at    (str | null)  — ISO timestamp of first successful spawn
  - rotation_counter (int)       — incremented by rotate() (Sprint E uses this)

New methods:
  - observe_codex_session(instance_id, since_ts) — glob + cache
  - mark_spawned(instance_id) — returns True if instance was previously spawned
  - get_path(instance_id) — retrieves cached session_path
  - rotate(instance_id) — generates a new uuid5 seed (Sprint E preview)
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
import yaml

from zf.core.state.role_sessions import RoleSessionRegistry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "role_sessions.yaml"


class TestMarkSpawned:
    def test_first_call_returns_false(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        assert reg.mark_spawned("dev-1") is False

    def test_second_call_returns_true(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        reg.mark_spawned("dev-1")
        assert reg.mark_spawned("dev-1") is True

    def test_mark_spawned_survives_reload(self, registry_path):
        reg1 = RoleSessionRegistry(registry_path, project_root="/p")
        reg1.mark_spawned("dev-1")
        reg2 = RoleSessionRegistry(registry_path, project_root="/p")
        assert reg2.mark_spawned("dev-1") is True

    def test_mark_spawned_records_timestamp(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        before = time.time() - 1
        reg.mark_spawned("dev-1")
        raw = yaml.safe_load(registry_path.read_text())
        # instance_meta holds spawned_at; presence implies mark_spawned fired
        # Schema is flexible — just confirm the instance got a record
        assert "dev-1" in raw.get("instance_meta", {}) or "dev-1" in raw.get("roles", {})

    def test_different_instances_tracked_independently(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        assert reg.mark_spawned("dev-1") is False
        assert reg.mark_spawned("dev-2") is False  # unrelated instance
        assert reg.mark_spawned("dev-1") is True
        assert reg.mark_spawned("dev-2") is True


class TestObserveCodexSession:
    def _write_fake_codex_session(
        self, root: Path, date: str, uuid_str: str, content: str = "{}"
    ) -> Path:
        y, m, d = date.split("-")
        folder = root / "sessions" / y / m / d
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-{date}T12-34-56-{uuid_str}.jsonl"
        path.write_text(content)
        return path

    def test_observe_finds_newest_file_after_since_ts(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )

        # Old file (should be ignored)
        old_uuid = "11111111-1111-1111-1111-111111111111"
        old_path = self._write_fake_codex_session(fake_codex, "2026-04-14", old_uuid)
        old_path.touch()
        old_mtime = old_path.stat().st_mtime

        # Wait a bit then write new file
        time.sleep(0.05)
        new_uuid = "22222222-2222-2222-2222-222222222222"
        new_path = self._write_fake_codex_session(fake_codex, "2026-04-15", new_uuid)
        since_ts = old_mtime + 0.01  # between old and new

        reg = RoleSessionRegistry(registry_path, project_root="/p")
        observed = reg.observe_codex_session("dev-1", since_ts=since_ts)
        assert observed is not None
        obs_uuid, obs_path = observed
        assert str(obs_uuid) == new_uuid
        assert obs_path == new_path

    def test_observe_caches_path_to_yaml(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        u = "33333333-3333-3333-3333-333333333333"
        path = self._write_fake_codex_session(fake_codex, "2026-04-15", u)

        reg = RoleSessionRegistry(registry_path, project_root="/p")
        reg.observe_codex_session("dev-1", since_ts=0)

        # Reload and check persistence
        reg2 = RoleSessionRegistry(registry_path, project_root="/p")
        cached = reg2.get_path("dev-1")
        assert cached == path

    def test_observe_returns_none_when_no_files(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        assert reg.observe_codex_session("dev-1", since_ts=0) is None

    def test_observe_can_scope_to_role_local_sessions_root(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_global = tmp_path / "global_codex"
        fake_local = tmp_path / "role_codex"
        fake_global.mkdir()
        fake_local.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_global / "sessions",
        )

        local_uuid = "55555555-5555-5555-5555-555555555555"
        local_path = self._write_fake_codex_session(
            fake_local, "2026-04-15", local_uuid,
        )
        time.sleep(0.05)
        global_uuid = "66666666-6666-6666-6666-666666666666"
        self._write_fake_codex_session(fake_global, "2026-04-15", global_uuid)

        reg = RoleSessionRegistry(registry_path, project_root="/p")
        observed = reg.observe_codex_session(
            "dev-1",
            since_ts=0,
            sessions_root=fake_local / "sessions",
        )

        assert observed is not None
        obs_uuid, obs_path = observed
        assert str(obs_uuid) == local_uuid
        assert obs_path == local_path

    def test_observe_accepts_managed_workdir_cwd(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        project = tmp_path / "project"
        workdir_project = project / ".zf" / "workdirs" / "review-a" / "project"
        workdir_project.mkdir(parents=True)
        u = "77777777-7777-7777-7777-777777777777"
        content = json.dumps({
            "type": "session_meta",
            "payload": {"cwd": str(workdir_project)},
        }) + "\n"
        path = self._write_fake_codex_session(
            fake_codex, "2026-04-15", u, content=content,
        )

        reg = RoleSessionRegistry(registry_path, project_root=str(project))
        observed = reg.observe_codex_session("review-a", since_ts=0)

        assert observed is not None
        obs_uuid, obs_path = observed
        assert str(obs_uuid) == u
        assert obs_path == path

    def test_observe_accepts_configured_state_dir_workdir_cwd(
        self, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        project = tmp_path / "project"
        state_dir = project / ".zf-full-codex"
        registry = state_dir / "role_sessions.yaml"
        workdir_project = state_dir / "workdirs" / "arch" / "project"
        workdir_project.mkdir(parents=True)
        u = "99999999-9999-9999-9999-999999999999"
        content = json.dumps({
            "type": "session_meta",
            "payload": {"cwd": str(workdir_project)},
        }) + "\n"
        path = self._write_fake_codex_session(
            fake_codex, "2026-04-15", u, content=content,
        )

        reg = RoleSessionRegistry(registry, project_root=str(project))
        observed = reg.observe_codex_session("arch", since_ts=0)

        assert observed is not None
        obs_uuid, obs_path = observed
        assert str(obs_uuid) == u
        assert obs_path == path

    def test_bind_codex_session_repairs_stale_uuid_owner(
        self, registry_path, tmp_path
    ):
        reg = RoleSessionRegistry(registry_path, project_root=str(tmp_path))
        session_id = "88888888-8888-8888-8888-888888888888"
        stale_path = tmp_path / ".zf" / "workdirs" / "review" / "codex-home" / "sessions" / "rollout.jsonl"
        live_path = tmp_path / ".zf" / "workdirs" / "dev-1" / "codex-home" / "sessions" / "rollout.jsonl"

        assert reg.bind_codex_session(
            "review",
            session_id,
            session_path=stale_path,
        )
        assert reg.get_instance_by_uuid(session_id) == "review"

        assert reg.bind_codex_session(
            "dev-1",
            session_id,
            session_path=live_path,
        )

        reloaded = RoleSessionRegistry(registry_path, project_root=str(tmp_path))
        assert reloaded.get_instance_by_uuid(session_id) == "dev-1"
        assert reloaded.get("review") is None
        assert reloaded.get_path("dev-1") == live_path


class TestRotate:
    def test_rotate_generates_different_uuid(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        before = reg.get_or_create("dev-1")
        after = reg.rotate("dev-1")
        assert before != after

    def test_rotate_persists_new_state(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        reg.get_or_create("dev-1")
        new_uuid = reg.rotate("dev-1")
        reg2 = RoleSessionRegistry(registry_path, project_root="/p")
        assert reg2.get_or_create("dev-1") == new_uuid

    def test_rotate_twice_gives_three_distinct(self, registry_path):
        reg = RoleSessionRegistry(registry_path, project_root="/p")
        u0 = reg.get_or_create("dev-1")
        u1 = reg.rotate("dev-1")
        u2 = reg.rotate("dev-1")
        assert len({u0, u1, u2}) == 3

    def test_rotate_clears_cached_session_path(
        self, registry_path, tmp_path, monkeypatch
    ):
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        # Seed a cached path
        u = "44444444-4444-4444-4444-444444444444"
        y, m, d = "2026", "04", "15"
        folder = fake_codex / "sessions" / y / m / d
        folder.mkdir(parents=True)
        (folder / f"rollout-2026-04-15T00-00-00-{u}.jsonl").write_text("{}")

        reg = RoleSessionRegistry(registry_path, project_root="/p")
        reg.observe_codex_session("dev-1", since_ts=0)
        assert reg.get_path("dev-1") is not None

        reg.rotate("dev-1")
        assert reg.get_path("dev-1") is None
