"""First-run welcome onboarding state (global, per-install).

The Web welcome wizard must show on first `zf web` open and never again once
completed or explicitly skipped. The gate lives here as a global flag file
(`~/.zaofu/onboarding.json`), NOT in per-workspace registry (welcome covers
host-global environment setup: backend / preflight / notifications, which do
not vary by workspace) and NOT in browser localStorage (a server-backed tool
must behave the same across browsers hitting the same instance).

`step` supports mid-wizard resume; `completed`/`skipped` are the permanent
suppressors. `reset` re-arms the wizard (settings "replay onboarding").
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text

SCHEMA_VERSION = "onboarding.v1"


def onboarding_path(*, home: Path | None = None) -> Path:
    env_home = os.environ.get("ZF_WORKSPACE_HOME", "").strip()
    root = Path(env_home).expanduser() if env_home else (home or Path.home()) / ".zaofu"
    return root / "onboarding.json"


@dataclass
class OnboardingState:
    schema_version: str = SCHEMA_VERSION
    completed: bool = False
    skipped: bool = False
    step: int = 1
    backend: str = ""
    notifications: str = ""
    completed_at: str = ""

    @property
    def show_welcome(self) -> bool:
        """First-run gate: show unless permanently suppressed."""
        return not (self.completed or self.skipped)

    @classmethod
    def from_dict(cls, raw: dict) -> "OnboardingState":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            schema_version=str(raw.get("schema_version") or SCHEMA_VERSION),
            completed=bool(raw.get("completed", False)),
            skipped=bool(raw.get("skipped", False)),
            step=int(raw.get("step") or 1),
            backend=str(raw.get("backend") or ""),
            notifications=str(raw.get("notifications") or ""),
            completed_at=str(raw.get("completed_at") or ""),
        )


def read_onboarding(*, home: Path | None = None) -> OnboardingState:
    """Missing file = fresh install = show welcome (default state)."""
    path = onboarding_path(home=home)
    try:
        return OnboardingState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return OnboardingState()


def write_onboarding(state: OnboardingState, *, home: Path | None = None) -> None:
    path = onboarding_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(asdict(state), ensure_ascii=False, indent=2))


def apply_action(
    action: str,
    *,
    step: int | None = None,
    backend: str = "",
    notifications: str = "",
    now: str = "",
    home: Path | None = None,
) -> OnboardingState:
    """Mutate onboarding state by operator action. Returns the new state.

    action: ``step`` (advance/record progress) | ``complete`` | ``skip`` | ``reset``.
    """
    state = read_onboarding(home=home)
    if action == "reset":
        state = OnboardingState()
    elif action == "complete":
        state.completed = True
        state.completed_at = now
        if backend:
            state.backend = backend
        if notifications:
            state.notifications = notifications
    elif action == "skip":
        state.skipped = True
    elif action == "step":
        if step is not None:
            state.step = max(1, int(step))
        if backend:
            state.backend = backend
        if notifications:
            state.notifications = notifications
    else:
        raise ValueError(f"unknown onboarding action: {action!r}")
    write_onboarding(state, home=home)
    return state


def detect_backends() -> list[dict]:
    """Which agent backends are on PATH (orca 'agent detection' equivalent)."""
    import shutil

    catalog = [
        ("claude-code", "claude", "推荐 · 默认"),
        ("codex", "codex", ""),
        ("mock", "", "先跑通流程不烧 token"),
    ]
    out: list[dict] = []
    for backend_id, binary, note in catalog:
        if backend_id == "mock":
            out.append({"id": backend_id, "detected": True, "path": "", "note": note,
                        "always_available": True})
            continue
        path = shutil.which(binary)
        out.append({"id": backend_id, "detected": bool(path), "path": path or "",
                    "note": note, "always_available": False})
    return out
