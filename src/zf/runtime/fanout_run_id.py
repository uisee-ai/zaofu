"""PREREQ-C — fanout child run_id rename + dual-write compat.

Historical state (pre-this-sprint):
- ``run_id`` in fanout event payloads = ``run-<fanout_id>-<child_id>``
- ``_current_run_id()`` = SessionStore.session_id (different dimension)
- Doc 39 assumes ``run_id`` is **campaign-level** (one per
  user.message thread)

This module bridges the rename. Writers go through
``fanout_payload_run_ids(child_run_id)`` which produces a dict with
BOTH ``run_id`` and ``child_run_id`` keys (transition-period
double-write). Readers use ``read_child_run_id(payload)`` which
prefers ``child_run_id`` and falls back to ``run_id``.

After 1 release cycle, dual-write can be dropped and ``run_id`` in
fanout payloads will be the campaign-level id (which is what doc
39 / SP-001 assume).
"""

from __future__ import annotations

from typing import Any, Mapping


def fanout_payload_run_ids(child_run_id: str) -> dict[str, str]:
    """Produce the dual-write payload keys for fanout event emit.

    Both ``run_id`` and ``child_run_id`` carry the same value during
    the migration window so existing readers don't break and new
    readers can transition to the canonical ``child_run_id`` field.
    """
    return {"run_id": child_run_id, "child_run_id": child_run_id}


def read_child_run_id(payload: Mapping[str, Any] | None) -> str:
    """Read fanout child id from an event payload, preferring the
    new ``child_run_id`` key and falling back to ``run_id`` for
    pre-migration events."""
    if not isinstance(payload, Mapping):
        return ""
    new = payload.get("child_run_id")
    if isinstance(new, str) and new:
        return new
    legacy = payload.get("run_id")
    if isinstance(legacy, str) and legacy:
        return legacy
    return ""


def is_canonical_payload(payload: Mapping[str, Any] | None) -> bool:
    """True if the payload has the new ``child_run_id`` key (used by
    PREREQ-C migration audit). Old payloads only have ``run_id``."""
    if not isinstance(payload, Mapping):
        return False
    return bool(payload.get("child_run_id"))
