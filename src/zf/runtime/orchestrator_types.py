"""Shared types used by Orchestrator and its Mixin modules.

Lives in its own module so Mixin files can import these types without
forming a circular import with orchestrator.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrchestratorDecision:
    action: str  # dispatch, move, respawn, capture, skip
    task_id: str | None = None
    role: str | None = None
    target_role: str | None = None
    reason: str = ""
