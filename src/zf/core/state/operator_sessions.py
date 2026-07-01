"""ZF-PWF-SESSION-ISO-001 — operator-session-scoped active target registry.

ZaoFu has two independent session dimensions:

- **Provider session** (existing): which CLI process is bound to which
  role instance. Owned by :class:`RoleSessionRegistry` in
  ``role_sessions.py``.
- **Operator session** (this module): which human / UI / external
  channel is currently "looking at" which task. Owned by
  :class:`OperatorSessionRegistry` here.

Without this dimension, web / Feishu / CLI chat entry points can only
guess "the most recently active dev worker", which silently breaks
parallel multi-campaign runs on the same install (doc 41 §4.6,
PWF's ``.planning/.active_plan`` pattern).

Discipline:
- Registry is YAML-backed (``.zf/operator_sessions.yaml``) and
  atomic-writes via ``atomic_write_text``.
- Ambiguous resolution is the caller's problem to surface — the
  resolver in :mod:`zf.runtime.operator_target_resolver` returns a
  ``TargetAmbiguous`` sentinel and the entry point fails closed.
- This file deliberately does not touch ``RoleSessionRegistry`` —
  the two dimensions are independent (an operator session may be
  bound to ``role_name=dev / instance_id=dev-1`` *because* that
  instance's provider session is alive, but binding either
  dimension does not implicitly bind the other).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OperatorSession:
    """Snapshot of one operator's currently-attached active target.

    ``operator_session_id`` is an opaque string assigned by the entry
    point (e.g. ``"op-feishu-thread-1234"``, ``"op-web-tab-3"``,
    ``"op-cli-pid-9876"``) — zaofu does not infer or validate it.

    All fields except ``operator_session_id`` and ``source`` are
    optional; the resolver only requires whatever the entry point
    needs to disambiguate.
    """

    operator_session_id: str
    source: str  # "feishu" | "web" | "cli" | "manual"
    run_id: str = ""
    task_id: str = ""
    role_name: str = ""
    instance_id: str = ""
    dispatch_id: str = ""
    provider_session_id: str = ""
    workdir: str = ""
    bound_at: str = ""
    last_active: str = ""


class OperatorSessionRegistry:
    """YAML-backed registry of operator → active-target bindings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, OperatorSession] = {}
        self._load()

    def _load(self) -> None:
        self._entries = {}
        if not self.path.exists():
            return
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return
        if not isinstance(raw, dict):
            return
        sessions = raw.get("sessions", {}) or {}
        if not isinstance(sessions, dict):
            return
        for key, value in sessions.items():
            if not isinstance(value, dict):
                continue
            try:
                self._entries[str(key)] = OperatorSession(
                    operator_session_id=str(key),
                    source=str(value.get("source", "manual")),
                    run_id=str(value.get("run_id", "") or ""),
                    task_id=str(value.get("task_id", "") or ""),
                    role_name=str(value.get("role_name", "") or ""),
                    instance_id=str(value.get("instance_id", "") or ""),
                    dispatch_id=str(value.get("dispatch_id", "") or ""),
                    provider_session_id=str(
                        value.get("provider_session_id", "") or ""
                    ),
                    workdir=str(value.get("workdir", "") or ""),
                    bound_at=str(value.get("bound_at", "") or ""),
                    last_active=str(value.get("last_active", "") or ""),
                )
            except (TypeError, ValueError):
                continue

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out: dict[str, object] = {"sessions": {}}
        for key, sess in self._entries.items():
            data = asdict(sess)
            # operator_session_id is already the key — drop from value
            data.pop("operator_session_id", None)
            out["sessions"][key] = data  # type: ignore[index]
        atomic_write_text(
            self.path,
            yaml.dump(out, sort_keys=True, default_flow_style=False),
        )

    def bind(self, session: OperatorSession) -> OperatorSession:
        """Bind / re-bind an operator to a target. Returns the
        canonical stored copy (with ``bound_at`` / ``last_active``
        filled if absent)."""
        now = _now_iso()
        canonical = replace(
            session,
            bound_at=session.bound_at or now,
            last_active=now,
        )
        with locked_path(self.path):
            self._load()
            self._entries[canonical.operator_session_id] = canonical
            self._save()
        return canonical

    def resolve(self, operator_session_id: str) -> OperatorSession | None:
        """Look up by opaque id. Returns ``None`` when no binding
        exists — callers must fail closed."""
        self._load()
        return self._entries.get(operator_session_id)

    def unbind(self, operator_session_id: str) -> bool:
        """Remove a binding. Returns True if a binding existed."""
        with locked_path(self.path):
            self._load()
            if operator_session_id not in self._entries:
                return False
            del self._entries[operator_session_id]
            self._save()
            return True

    def list_all(self) -> list[OperatorSession]:
        """Return a snapshot of every current binding."""
        self._load()
        return list(self._entries.values())
