"""Tests for B-MIXEDBACKEND-01: RoleSessionRegistry binds UUID to backend.

Motivation: before this fix, `role_sessions.yaml` stored only
`instance_id → uuid`. When the backend under an instance_id flipped
(e.g. yaml edit: `dev-1: claude-code` → `dev-1: codex`), the old claude
UUID was handed to the new codex spawn → `codex resume <uuid>` failed
with "No saved session" because the session file only existed on
claude's side. This test file pins the new contract:

  - get_or_create(instance_id, backend=X) seeds UUID via
    uuid5(project:instance:X) and records `meta.backend = X`
  - if a later call passes backend=Y (≠ X), rotate_counter bumps and a
    fresh UUID is generated
  - mark_backend() records backend without generating a UUID (for codex
    where UUID is observed post-spawn)
  - legacy callers that don't pass backend="" keep the old 2-tuple seed
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry, _uuid5_seed


class TestUuid5SeedBackendComponent:
    def test_empty_backend_preserves_legacy_seed(self):
        """UUIDs written before B-MIXEDBACKEND-01 used 2-tuple seed;
        passing backend="" must reproduce that UUID exactly."""
        seed_old = _uuid5_seed("/tmp/proj", "dev-1", rotation=0)
        seed_same = _uuid5_seed("/tmp/proj", "dev-1", rotation=0, backend="")
        assert seed_old == seed_same

    def test_different_backend_yields_different_uuid(self):
        claude_uuid = _uuid5_seed("/tmp/proj", "dev-1", backend="claude-code")
        codex_uuid = _uuid5_seed("/tmp/proj", "dev-1", backend="codex")
        assert claude_uuid != codex_uuid

    def test_same_backend_reproducible(self):
        a = _uuid5_seed("/tmp/p", "dev-1", backend="claude-code")
        b = _uuid5_seed("/tmp/p", "dev-1", backend="claude-code")
        assert a == b


class TestGetOrCreateBindsBackend:
    def test_first_call_records_backend_in_meta(self, tmp_path):
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        uid = reg.get_or_create("dev-1", backend="claude-code")
        assert reg._meta["dev-1"]["backend"] == "claude-code"
        # UUID must match deterministic seed with backend
        assert uid == _uuid5_seed(str(tmp_path), "dev-1", backend="claude-code")

    def test_same_backend_returns_same_uuid(self, tmp_path):
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        uid1 = reg.get_or_create("dev-1", backend="claude-code")
        uid2 = reg.get_or_create("dev-1", backend="claude-code")
        assert uid1 == uid2

    def test_backend_flip_rotates_uuid(self, tmp_path):
        """dev-1 was claude yesterday. Today the yaml flips it to codex.
        Next get_or_create must return a *different* UUID so codex
        doesn't inherit the claude session file."""
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        claude_uid = reg.get_or_create("dev-1", backend="claude-code")
        codex_uid = reg.get_or_create("dev-1", backend="codex")
        assert claude_uid != codex_uid
        # Meta reflects the flip
        assert reg._meta["dev-1"]["backend"] == "codex"
        assert reg._meta["dev-1"]["backend_rotated_from"] == "claude-code"
        assert reg._meta["dev-1"]["rotation_counter"] >= 1

    def test_backend_upgrade_from_empty_preserves_uuid(self, tmp_path):
        """Registries written before this change have no `backend` in
        meta. When such a UUID is looked up WITH a backend hint, we
        must not rotate — the legacy UUID is still valid; we just
        annotate the meta."""
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        legacy_uid = reg.get_or_create("dev-1")  # no backend, legacy seed
        upgraded = reg.get_or_create("dev-1", backend="claude-code")
        assert legacy_uid == upgraded  # NOT rotated
        assert reg._meta["dev-1"]["backend"] == "claude-code"

    def test_legacy_no_backend_still_works(self, tmp_path):
        """Callers that never pass backend (e.g. old stream-json
        transport path) keep their legacy UUID stability."""
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        uid1 = reg.get_or_create("orchestrator")
        uid2 = reg.get_or_create("orchestrator")
        assert uid1 == uid2
        # No backend recorded
        assert reg._meta.get("orchestrator", {}).get("backend", "") == ""


class TestMarkBackend:
    def test_first_call_records_backend(self, tmp_path):
        """For codex: UUID is only known post-observation, so we record
        backend binding first, UUID later."""
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        rebound = reg.mark_backend("dev-1", "codex")
        assert rebound is False  # first bind, not a flip
        assert reg._meta["dev-1"]["backend"] == "codex"
        # No UUID created
        assert reg.get("dev-1") is None

    def test_mark_same_backend_idempotent(self, tmp_path):
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        reg.mark_backend("dev-1", "codex")
        rebound = reg.mark_backend("dev-1", "codex")
        assert rebound is False

    def test_mark_flipped_backend_clears_uuid_and_rotates(self, tmp_path):
        """The bug-fix case: dev-1 was codex with observed UUID, then
        yaml flip to claude. mark_backend must drop the stale UUID so
        the next claude spawn re-seeds."""
        reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        # Simulate codex observation
        reg._entries["dev-1"] = "019db838-1b85-70e1-918c-e13efcefdf5d"
        reg._meta["dev-1"] = {"backend": "codex", "spawned_at": "2026-04-23T00:00:00"}
        rebound = reg.mark_backend("dev-1", "claude-code")
        assert rebound is True
        assert reg.get("dev-1") is None  # stale UUID cleared
        assert reg._meta["dev-1"]["backend"] == "claude-code"
        assert reg._meta["dev-1"]["backend_rotated_from"] == "codex"

    def test_persistence_roundtrip(self, tmp_path):
        """After mark_backend, a fresh Registry loaded from disk still
        sees the binding."""
        path = tmp_path / "role_sessions.yaml"
        reg1 = RoleSessionRegistry(path, str(tmp_path))
        reg1.mark_backend("test", "codex")
        reg2 = RoleSessionRegistry(path, str(tmp_path))
        assert reg2._meta["test"]["backend"] == "codex"
