"""Per-instance CLI session_id registry.

Each role instance (e.g. dev-1, dev-2, review) gets a deterministic UUID
via uuid5(NAMESPACE_DNS, project_root + ":" + instance_id) the first time
it is observed. The same (project_root, instance_id) always resolves to
the same UUID, so ``claude --resume <uuid>`` / ``codex exec resume <uuid>``
extends the same conversation across orchestrator restarts.

Persisted to .zf/role_sessions.yaml with three per-instance records:

    roles:
        dev-1: <uuid>                       # the canonical seed uuid
    instance_meta:
        dev-1:
            spawned_at: "2026-04-15T08:12:34+00:00"   # first spawn time
            session_path: ~/.codex/sessions/...       # codex only (cached glob)
            rotation_counter: 0                       # Sprint E recycle counter
"""

from __future__ import annotations

import glob
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


# Codex session file root. Patchable in tests via monkeypatch.
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid5_seed(
    project_root: str,
    instance_id: str,
    rotation: int = 0,
    backend: str = "",
) -> uuid.UUID:
    """Deterministic UUID for a (project, instance, [backend]) triple.

    B-MIXEDBACKEND-01 (2026-04-23): backend is now part of the seed when
    supplied. This prevents the "dev-1 was claude yesterday, is codex
    today" class of bug (UUID persists in role_sessions.yaml but points
    at a session file the new backend can't read). When backend="" the
    old two-field seed is used, preserving UUID stability for configs
    that don't opt into per-replica backends.
    """
    seed = f"{project_root}:{instance_id}"
    if backend:
        seed += f":{backend}"
    if rotation > 0:
        seed += f":rot-{rotation}"
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed)


class RoleSessionRegistry:
    def __init__(self, path: Path, project_root: str) -> None:
        self.path = path
        self.project_root = project_root
        self._entries: dict[str, str] = {}          # instance_id → uuid str
        self._meta: dict[str, dict] = {}            # instance_id → metadata dict
        self._load()

    # -- yaml I/O --

    def _load(self) -> None:
        self._entries = {}
        self._meta = {}
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return
        roles = data.get("roles", {}) or {}
        self._entries = {str(k): str(v) for k, v in roles.items()}
        meta = data.get("instance_meta", {}) or {}
        if isinstance(meta, dict):
            self._meta = {
                str(k): dict(v) if isinstance(v, dict) else {}
                for k, v in meta.items()
            }

    def _save(self) -> None:
        atomic_write_text(
            self.path,
            yaml.dump(
                {
                    "project_root": self.project_root,
                    "roles": self._entries,
                    "instance_meta": self._meta,
                },
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=True,
            ),
        )

    def _locked(self):
        return locked_path(self.path)

    # -- public API --

    def get_or_create(self, instance_id: str, backend: str = "") -> uuid.UUID:
        """Return (and persist) the deterministic session UUID for an instance.

        B-MIXEDBACKEND-01 (2026-04-23): when ``backend`` is supplied, bind
        the UUID to that (instance, backend) pair and record the binding
        in ``instance_meta[instance_id].backend``. If a prior entry
        exists with a different backend, regenerate the UUID — a stale
        claude UUID must not be handed to a codex respawn (the session
        file does not exist on codex's side; ``codex resume <uuid>``
        would fail with "No saved session"). Callers that don't know or
        care about backend (legacy paths) pass ``backend=""`` and keep
        the prior behavior.
        """
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            stored_backend = meta.get("backend", "")
            backend_changed = (
                bool(backend) and bool(stored_backend) and stored_backend != backend
            )
            if instance_id in self._entries and not backend_changed:
                if backend and not stored_backend:
                    # First observation of backend for an existing UUID: upgrade
                    # the meta record without touching the UUID. Seed-backward-
                    # compat: UUIDs written pre-B-MIXEDBACKEND-01 used no
                    # backend component and stay stable here.
                    meta["backend"] = backend
                    self._save()
                return uuid.UUID(self._entries[instance_id])
            rotation = meta.get("rotation_counter", 0)
            if backend_changed:
                # Bump rotation so the new seed diverges from the stale one
                # even for callers that keep backend="" elsewhere.
                rotation = int(rotation) + 1
                meta["rotation_counter"] = rotation
                meta["backend_rotated_from"] = stored_backend
                meta["backend_rotated_at"] = _now_iso()
            sid = _uuid5_seed(self.project_root, instance_id, rotation, backend)
            self._entries[instance_id] = str(sid)
            if backend:
                meta["backend"] = backend
            self._save()
            return sid

    def get(self, instance_id: str) -> uuid.UUID | None:
        """Return the cached session UUID for an instance, or None if never set."""
        raw = self._entries.get(instance_id)
        return uuid.UUID(raw) if raw else None

    def mark_backend(self, instance_id: str, backend: str) -> bool:
        """Bind a backend to an instance without generating a UUID.

        B-MIXEDBACKEND-01 (2026-04-23): for codex (where UUID is only
        known post-observation), we still want the backend recorded in
        meta so a future flip (codex → claude, or codex → different
        codex after schema change) can be detected. If the stored
        backend differs from ``backend``, clears any cached UUID /
        session_path so the next get_or_create seeds a fresh one.
        Returns True when a rebind happened, False otherwise.
        """
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            stored = meta.get("backend", "")
            if stored and stored != backend:
                # Flip detected: bump rotation + drop stale cached UUID/path.
                rotation = int(meta.get("rotation_counter", 0)) + 1
                meta["rotation_counter"] = rotation
                meta["backend_rotated_from"] = stored
                meta["backend_rotated_at"] = _now_iso()
                meta["session_path"] = None
                self._entries.pop(instance_id, None)
                meta["backend"] = backend
                self._save()
                return True
            if stored != backend:
                meta["backend"] = backend
                self._save()
            return False

    def all(self) -> dict[str, uuid.UUID]:
        return {role: uuid.UUID(sid) for role, sid in self._entries.items()}

    def update_instance_meta(self, instance_id: str, **values: object) -> dict:
        """Merge runtime metadata for a worker instance.

        Used by deterministic runtime paths such as autoscale. The registry
        remains a runtime projection under state_dir, not a second control
        plane; zf.yaml still declares the role pool policy.
        """
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            meta.update(values)
            self._save()
            return dict(meta)

    def instance_meta(self) -> dict[str, dict]:
        return {instance_id: dict(meta) for instance_id, meta in self._meta.items()}

    def get_instance_by_uuid(self, uuid_str: str) -> str | None:
        """Reverse lookup: session UUID → instance_id.

        Used by hooks and session-file tailers to attribute a Claude /
        Codex event stream back to the role that owns it. Returns None
        when the uuid doesn't match any registered instance (e.g. stale
        hook firing from a terminated role).
        """
        for instance_id, registered in self._entries.items():
            if registered == uuid_str:
                return instance_id
        return None

    def mark_spawned(self, instance_id: str) -> bool:
        """Record that the instance has been spawned.

        Returns True if this is a re-spawn (i.e. the instance was
        already marked as spawned before this call), False if first time.
        """
        with self._locked():
            if self.path.exists():
                self._load()
            existing = self._meta.setdefault(instance_id, {})
            was_spawned = bool(existing.get("spawned_at"))
            if not was_spawned:
                existing["spawned_at"] = _now_iso()
                self._save()
            return was_spawned

    def get_path(self, instance_id: str) -> Path | None:
        """Return the cached session file path (codex only) or None."""
        raw = self._meta.get(instance_id, {}).get("session_path")
        return Path(raw) if raw else None

    def bind_codex_session(
        self,
        instance_id: str,
        session_id: str,
        *,
        session_path: Path | None = None,
        observed_from: str = "hook",
    ) -> bool:
        """Bind an observed Codex session UUID to an instance.

        Codex hooks include the live ``session_id`` and ``transcript_path``.
        The hook bridge can therefore repair a missing or stale registry
        binding without waiting for the background session observer. Returns
        False when ``session_id`` is not a UUID.
        """
        try:
            parsed_uuid = uuid.UUID(session_id)
        except ValueError:
            return False
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            meta["backend"] = "codex"
            meta["observed_at"] = _now_iso()
            meta["observed_from"] = observed_from
            if session_path is not None:
                meta["session_path"] = str(session_path)
            for existing_instance, existing_uuid in list(self._entries.items()):
                if (
                    existing_instance != instance_id
                    and existing_uuid == str(parsed_uuid)
                ):
                    self._entries.pop(existing_instance, None)
                    old_meta = self._meta.setdefault(existing_instance, {})
                    old_meta["session_path"] = None
                    old_meta["unbound_at"] = _now_iso()
                    old_meta["unbound_reason"] = "codex_session_rebound"
            self._entries[instance_id] = str(parsed_uuid)
            self._save()
        return True

    def clear(self, instance_id: str) -> None:
        """Drop the cached session UUID/path for one instance.

        Used when a provider-specific resume target is known stale. Keep
        backend/spawn metadata intact so the next spawn can still be treated as
        a respawn attempt, but force Codex observation to learn the next real
        rollout UUID instead of reusing a dead one.
        """
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            meta["session_path"] = None
            meta["cleared_at"] = _now_iso()
            self._entries.pop(instance_id, None)
            self._save()

    def record_heartbeat(
        self,
        instance_id: str,
        payload: dict,
    ) -> None:
        """α-2 (2026-05-17): persist a worker.heartbeat into instance_meta.

        Writes:
          - ``meta[instance_id]["last_heartbeat_at"]``: kernel-side
            timestamp (when housekeeping wrote it)
          - ``meta[instance_id]["last_heartbeat_payload"]``: the original
            event payload (current_task_id, state, last_action_ts,
            context_used_ratio?, checkpoint_ref?)

        Consumed by α-3 EventWatcher sweep to detect idle workers
        eligible for proactive dispatch, and by α-5 / web UI for live
        worker badges.
        """
        if not instance_id:
            return
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            meta["last_heartbeat_at"] = _now_iso()
            # Store payload as plain dict to keep yaml round-trippable.
            meta["last_heartbeat_payload"] = (
                dict(payload) if isinstance(payload, dict) else {}
            )
            self._save()

    def get_last_heartbeat(
        self, instance_id: str,
    ) -> tuple[str | None, dict | None]:
        """α-2 companion. Return (kernel_ts, payload) of the latest
        recorded worker.heartbeat. ``(None, None)`` when never recorded.
        """
        meta = self._meta.get(instance_id, {})
        ts = meta.get("last_heartbeat_at")
        payload = meta.get("last_heartbeat_payload")
        return (ts if ts else None, payload if isinstance(payload, dict) else None)

    def observe_codex_session(
        self,
        instance_id: str,
        *,
        since_ts: float = 0.0,
        max_wait_seconds: float = 5.0,
        sessions_root: Path | None = None,
    ) -> tuple[uuid.UUID, Path] | None:
        """Glob a Codex sessions root for the newest session file
        whose mtime > since_ts, parse the UUID from the filename, and
        cache the path into instance_meta.

        ``sessions_root`` defaults to ``CODEX_SESSIONS_ROOT`` for
        backward compatibility. SpawnCoordinator passes the role-local
        ``CODEX_HOME/sessions`` root so multi-Codex runs do not observe
        another role's rollout.

        Poll up to ``max_wait_seconds`` to handle the race where codex
        hasn't written the file yet right after spawn.

        Returns (uuid, path) on success or None if nothing found.
        """
        deadline = time.monotonic() + max_wait_seconds
        while True:
            found = self._glob_newest_since(since_ts, sessions_root=sessions_root)
            if found is not None:
                path, parsed_uuid = found
                self.bind_codex_session(
                    instance_id,
                    str(parsed_uuid),
                    session_path=path,
                    observed_from="observer",
                )
                return parsed_uuid, path
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    def _glob_newest_since(
        self,
        since_ts: float,
        *,
        sessions_root: Path | None = None,
    ) -> tuple[Path, uuid.UUID] | None:
        root = sessions_root or CODEX_SESSIONS_ROOT
        pattern = str(root / "*" / "*" / "*" / "rollout-*.jsonl")
        candidates = []
        for raw in glob.glob(pattern):
            p = Path(raw)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime > since_ts:
                candidates.append((mtime, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        for _, path in candidates:
            # B-1203-06 R-2: filter by project cwd. Codex rollout JSONL's
            # first line is a session_meta record with payload.cwd set
            # to the directory codex was launched from. Skip files whose
            # cwd doesn't match our project — they belong to another
            # codex invocation.
            if not self._rollout_matches_project(path):
                continue
            # Filename: rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.jsonl
            # UUID is the last 5 dash-separated segments of the stem
            stem = path.stem  # no .jsonl
            parts = stem.split("-")
            if len(parts) < 5:
                continue
            uuid_str = "-".join(parts[-5:])
            try:
                parsed = uuid.UUID(uuid_str)
            except ValueError:
                continue
            return path, parsed
        return None

    def _rollout_matches_project(self, path: Path) -> bool:
        """Peek the first line of a codex rollout jsonl and decide
        whether the file belongs to this project.

        Policy is **accept unless clearly mismatched**:
        - Unreadable / unparseable / missing session_meta → accept
          (legacy fixtures and pre-0.120 rollouts land here; rejecting
          would break observe on older data.)
        - session_meta present with ``payload.cwd`` set → require the
          cwd to resolve to the same path as ``self.project_root``.
          This is the case codex 0.120.0+ emits and it's what blocks
          cross-run contamination in concurrent-project hosts.
        """
        import json as _json
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline().strip()
            if not first:
                return True
            data = _json.loads(first)
        except (OSError, _json.JSONDecodeError):
            return True
        if not isinstance(data, dict):
            return True
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            return True
        rollout_cwd = payload.get("cwd", "")
        if not rollout_cwd:
            return True
        # We have a concrete cwd; require it to match our project.
        try:
            rollout_path = Path(rollout_cwd).resolve()
            project_path = Path(self.project_root).resolve()
            if rollout_path == project_path:
                return True
            state_workdirs = self.path.parent / "workdirs"
            legacy_workdirs = project_path / ".zf" / "workdirs"
            for root in (state_workdirs, legacy_workdirs):
                try:
                    rollout_path.relative_to(root.resolve())
                    return True
                except (OSError, ValueError):
                    continue
            return False
        except OSError:
            return str(rollout_cwd) == str(self.project_root)

    def rotate(self, instance_id: str) -> uuid.UUID:
        """Generate a fresh session UUID for the instance (Sprint E recycle).

        Increments rotation_counter, reseeds via uuid5 with the new
        counter, and clears the cached session_path (the old codex
        file is no longer ours).
        """
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            counter = int(meta.get("rotation_counter", 0)) + 1
            meta["rotation_counter"] = counter
            meta["session_path"] = None
            meta["rotated_at"] = _now_iso()
            new_uuid = _uuid5_seed(self.project_root, instance_id, counter)
            self._entries[instance_id] = str(new_uuid)
            self._save()
            return new_uuid
