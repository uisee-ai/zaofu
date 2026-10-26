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
import hashlib
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
        self._task_stage_bindings: dict[str, dict] = {}
        self._load()

    # -- yaml I/O --

    def _load(self) -> None:
        self._entries = {}
        self._meta = {}
        self._task_stage_bindings = {}
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
        bindings = data.get("task_stage_bindings", {}) or {}
        if isinstance(bindings, dict):
            self._task_stage_bindings = {
                str(key): dict(value)
                for key, value in bindings.items()
                if isinstance(value, dict)
            }

    def _save(self) -> None:
        atomic_write_text(
            self.path,
            yaml.dump(
                {
                    "project_root": self.project_root,
                    "roles": self._entries,
                    "instance_meta": self._meta,
                    "task_stage_bindings": self._task_stage_bindings,
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

    def bind_provider_session_config(
        self,
        instance_id: str,
        *,
        digest: str,
        ref: str,
        explicit: bool,
    ) -> dict[str, object]:
        """Bind a frozen provider config and recycle an unprovable session.

        The decision and mutation happen under the registry lock so concurrent
        activation attempts cannot both resume an old provider session.
        """

        if not digest or not ref:
            raise ValueError("provider session config requires digest and ref")
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            previous_digest = str(
                meta.get("provider_session_config_digest") or ""
            )
            previous_ref = str(meta.get("provider_session_config_ref") or "")
            was_spawned = bool(meta.get("spawned_at"))
            recycle_reason = ""
            if previous_digest and previous_digest != digest:
                recycle_reason = "provider_session_config_changed"
            elif not previous_digest and explicit and was_spawned:
                recycle_reason = "provider_session_config_currentness_unproven"

            recycled = bool(recycle_reason)
            if recycled:
                meta["rotation_counter"] = int(
                    meta.get("rotation_counter", 0)
                ) + 1
                meta["session_path"] = None
                meta.pop("spawned_at", None)
                self._entries.pop(instance_id, None)
                meta["provider_session_config_recycled_at"] = _now_iso()
                meta["provider_session_config_recycle_reason"] = recycle_reason
                meta["provider_session_config_previous_digest"] = previous_digest
                meta["provider_session_config_previous_ref"] = previous_ref

            meta["provider_session_config_digest"] = digest
            meta["provider_session_config_ref"] = ref
            meta["provider_session_config_explicit"] = bool(explicit)
            meta["provider_session_config_bound_at"] = _now_iso()
            self._save()
            if recycled:
                status = "recycled"
            elif previous_digest == digest:
                status = "current"
            elif previous_digest:
                status = "rebound"
            elif was_spawned:
                status = "legacy_bound"
            else:
                status = "bound"
            return {
                "status": status,
                "recycled": recycled,
                "reason": recycle_reason,
                "previous_digest": previous_digest,
                "previous_ref": previous_ref,
                "digest": digest,
                "ref": ref,
            }

    def instance_meta(self) -> dict[str, dict]:
        return {instance_id: dict(meta) for instance_id, meta in self._meta.items()}

    def bind_task_stage_session(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        stage: str,
        rework_affinity_id: str,
        role_instance: str,
        role_config_digest: str,
        workspace_generation: int,
        placement_epoch: int,
        backend: str = "",
    ) -> dict:
        """Create or relocate one v4 Task-stage provider session binding.

        The binding isolates transcript identity from the physical worker pane.
        Role configuration remains role-scoped and is frozen by digest.
        """

        fields = {
            "workflow_run_id": workflow_run_id,
            "task_id": task_id,
            "stage": stage,
            "rework_affinity_id": rework_affinity_id,
            "role_instance": role_instance,
            "role_config_digest": role_config_digest,
        }
        normalized = {
            key: str(value or "").strip()
            for key, value in fields.items()
        }
        missing = [key for key, value in normalized.items() if not value]
        if missing:
            raise ValueError(
                "task-stage session binding missing: " + ", ".join(missing)
            )
        if workspace_generation < 1:
            raise ValueError("workspace_generation must be >= 1")
        if placement_epoch < 1:
            raise ValueError("placement_epoch must be >= 1")
        binding_key = _task_stage_binding_key(
            normalized["workflow_run_id"],
            normalized["task_id"],
            normalized["stage"],
            normalized["rework_affinity_id"],
        )
        with self._locked():
            if self.path.exists():
                self._load()
            binding = self._task_stage_bindings.get(binding_key)
            if binding is None:
                session_id = uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    f"{self.project_root}:task-stage:{binding_key}:{backend}",
                )
                binding = {
                    "schema_version": "task-stage-session-binding.v1",
                    "binding_key": binding_key,
                    "workflow_run_id": normalized["workflow_run_id"],
                    "task_id": normalized["task_id"],
                    "stage": normalized["stage"],
                    "rework_affinity_id": normalized["rework_affinity_id"],
                    "session_id": str(session_id),
                    "backend": str(backend or ""),
                    "role_config_digest": normalized["role_config_digest"],
                    "workspace_generation": workspace_generation,
                    "rotation_counter": 0,
                    "status": "active",
                    "placement_history": [],
                    "created_at": _now_iso(),
                }
            else:
                if binding.get("status") == "archived":
                    raise ValueError("task-stage session binding is archived")
                if (
                    str(binding.get("role_config_digest") or "")
                    != normalized["role_config_digest"]
                ):
                    raise ValueError("task-stage role config digest changed")
                if int(binding.get("workspace_generation") or 0) != workspace_generation:
                    raise ValueError("task-stage workspace generation changed")
            history = list(binding.get("placement_history") or [])
            if history:
                previous = history[-1]
                previous_epoch = int(previous.get("placement_epoch") or 0)
                if placement_epoch < previous_epoch:
                    raise ValueError("task-stage placement epoch regressed")
                if placement_epoch == previous_epoch:
                    if str(previous.get("role_instance") or "") != normalized["role_instance"]:
                        raise ValueError("task-stage placement epoch is already bound")
                    self._task_stage_bindings[binding_key] = binding
                    return _copy_binding(binding)
            if not history or placement_epoch > int(
                history[-1].get("placement_epoch") or 0
            ):
                history.append({
                    "placement_epoch": placement_epoch,
                    "role_instance": normalized["role_instance"],
                    "workspace_generation": workspace_generation,
                    "bound_at": _now_iso(),
                })
            binding["placement_history"] = history
            binding["current_role_instance"] = normalized["role_instance"]
            binding["current_placement_epoch"] = placement_epoch
            binding["status"] = "active"
            binding["updated_at"] = _now_iso()
            self._task_stage_bindings[binding_key] = binding
            self._save()
            return _copy_binding(binding)

    def task_stage_binding(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        stage: str,
        rework_affinity_id: str,
    ) -> dict | None:
        key = _task_stage_binding_key(
            workflow_run_id, task_id, stage, rework_affinity_id
        )
        value = self._task_stage_bindings.get(key)
        return _copy_binding(value) if value is not None else None

    def task_stage_bindings(self) -> dict[str, dict]:
        return {
            key: _copy_binding(value)
            for key, value in self._task_stage_bindings.items()
        }

    def activate_task_stage_session(
        self,
        *,
        binding_key: str,
        role_instance: str,
    ) -> dict:
        """Project one durable Task-stage session onto a physical slot.

        The binding remains authoritative for conversation identity.  The
        role-scoped entry is only the currently activated provider projection
        consumed by ``SpawnCoordinator``.
        """

        key = str(binding_key or "").strip()
        instance = str(role_instance or "").strip()
        if not key or not instance:
            raise ValueError("task-stage activation requires binding and role")
        with self._locked():
            if self.path.exists():
                self._load()
            binding = self._task_stage_bindings.get(key)
            if binding is None:
                raise ValueError("task-stage session binding does not exist")
            if str(binding.get("status") or "") == "archived":
                raise ValueError("task-stage session binding is archived")
            if str(binding.get("current_role_instance") or "") != instance:
                raise ValueError("task-stage session placement is not current")

            backend = str(binding.get("backend") or "")
            meta = self._meta.setdefault(instance, {})
            previous_key = str(meta.get("active_task_stage_binding_key") or "")
            meta.update({
                "active_task_stage_binding_key": key,
                "task_stage_binding_activated_at": _now_iso(),
                "task_stage_binding_previous_key": previous_key,
                "backend": backend or str(meta.get("backend") or ""),
            })
            session_id = str(binding.get("session_id") or "")
            observed = bool(binding.get("provider_session_observed"))
            if backend == "codex" and not observed:
                self._entries.pop(instance, None)
                meta["session_path"] = None
            elif session_id:
                for existing_instance, existing_uuid in list(
                    self._entries.items()
                ):
                    if existing_instance != instance and existing_uuid == session_id:
                        self._entries.pop(existing_instance, None)
                        old_meta = self._meta.setdefault(existing_instance, {})
                        old_meta["session_path"] = None
                        old_meta["unbound_at"] = _now_iso()
                        old_meta["unbound_reason"] = (
                            "task_stage_session_relocated"
                        )
                self._entries[instance] = session_id
                session_path = str(binding.get("session_path") or "")
                if session_path:
                    meta["session_path"] = session_path
            binding["status"] = "active"
            binding["activated_at"] = _now_iso()
            self._task_stage_bindings[key] = binding
            self._save()
            return _copy_binding(binding)

    def seal_task_stage_session(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        stage: str,
        rework_affinity_id: str,
    ) -> dict | None:
        key = _task_stage_binding_key(
            workflow_run_id, task_id, stage, rework_affinity_id
        )
        with self._locked():
            if self.path.exists():
                self._load()
            binding = self._task_stage_bindings.get(key)
            if binding is None:
                return None
            binding["status"] = "sealed"
            binding["sealed_at"] = _now_iso()
            self._save()
            return _copy_binding(binding)

    def archive_task_stage_session(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        stage: str,
        rework_affinity_id: str,
    ) -> dict | None:
        key = _task_stage_binding_key(
            workflow_run_id, task_id, stage, rework_affinity_id
        )
        with self._locked():
            if self.path.exists():
                self._load()
            binding = self._task_stage_bindings.get(key)
            if binding is None:
                return None
            if binding.get("status") != "sealed":
                raise ValueError("task-stage session must be sealed before archive")
            binding["status"] = "archived"
            binding["archived_at"] = _now_iso()
            self._save()
            return _copy_binding(binding)

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
        False when ``session_id`` is not a UUID or was explicitly retired.
        """
        try:
            parsed_uuid = uuid.UUID(session_id)
        except ValueError:
            return False
        with self._locked():
            if self.path.exists():
                self._load()
            meta = self._meta.setdefault(instance_id, {})
            if str(parsed_uuid) in meta.get("retired_codex_session_ids", []):
                return False
            meta["backend"] = "codex"
            meta["observed_at"] = _now_iso()
            meta["observed_from"] = observed_from
            if session_path is not None:
                meta["session_path"] = str(session_path)
            binding_key = str(meta.get("active_task_stage_binding_key") or "")
            binding = self._task_stage_bindings.get(binding_key)
            if binding is not None:
                binding["session_id"] = str(parsed_uuid)
                binding["provider_session_observed"] = True
                binding["provider_session_observed_at"] = _now_iso()
                binding["provider_session_role_instance"] = instance_id
                if session_path is not None:
                    binding["session_path"] = str(session_path)
                self._task_stage_bindings[binding_key] = binding
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
            retired = self._entries.get(instance_id)
            if retired and meta.get("backend") == "codex":
                # Hooks from the departing process can arrive after clear().
                # Persist the fence under the same lock as the binding update.
                retired_ids = meta.setdefault("retired_codex_session_ids", [])
                if retired not in retired_ids:
                    retired_ids.append(retired)
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
            found = self._glob_newest_since(
                since_ts, sessions_root=sessions_root,
                excluded_session_ids=set(self._meta.get(instance_id, {}).get(
                    "retired_codex_session_ids", [])),
            )
            if found is not None:
                path, parsed_uuid = found
                if self.bind_codex_session(
                    instance_id,
                    str(parsed_uuid),
                    session_path=path,
                    observed_from="observer",
                ):
                    return parsed_uuid, path
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    def _glob_newest_since(
        self,
        since_ts: float,
        *,
        sessions_root: Path | None = None,
        excluded_session_ids: set[str] | None = None,
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
            if excluded_session_ids and str(parsed) in excluded_session_ids:
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


def _task_stage_binding_key(
    workflow_run_id: str,
    task_id: str,
    stage: str,
    rework_affinity_id: str,
) -> str:
    values = [
        str(value or "").strip()
        for value in (workflow_run_id, task_id, stage, rework_affinity_id)
    ]
    if any(not value for value in values):
        raise ValueError("task-stage binding identity is incomplete")
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:24]
    return f"task-stage-{digest}"


def _copy_binding(binding: dict) -> dict:
    copy = dict(binding)
    copy["placement_history"] = [
        dict(item)
        for item in binding.get("placement_history", [])
        if isinstance(item, dict)
    ]
    return copy
