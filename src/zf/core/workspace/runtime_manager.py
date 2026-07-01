"""Workspace runtime health summary."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig


@dataclass(frozen=True)
class RuntimeStatus:
    state: str
    tmux_session: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "state": self.state,
            "tmux_session": self.tmux_session,
            "reason": self.reason,
        }


class RuntimeManager:
    """Read-only runtime status for workspace overview."""

    def status(
        self,
        *,
        state_dir: Path,
        config: ZfConfig | None,
        project_id: str,
    ) -> RuntimeStatus:
        session = _tmux_session_name(config=config, project_id=project_id)
        if not session:
            return RuntimeStatus(state="stopped", tmux_session="", reason="no tmux session")
        if _tmux_has_session(session):
            return RuntimeStatus(state="running", tmux_session=session)
        if (Path(state_dir) / "session.yaml").exists():
            return RuntimeStatus(
                state="stopped",
                tmux_session=session,
                reason="state exists but tmux session is not attached",
            )
        return RuntimeStatus(state="stopped", tmux_session=session)

    def overview_row(
        self,
        *,
        project_id: str,
        name: str,
        root: Path,
        state_dir: Path,
        config: ZfConfig | None,
    ) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "name": name,
            "root": str(Path(root).resolve()),
            "state_dir": str(Path(state_dir).resolve()),
            "runtime": self.status(
                state_dir=state_dir,
                config=config,
                project_id=project_id,
            ).to_dict(),
        }


def _tmux_session_name(*, config: ZfConfig | None, project_id: str) -> str:
    raw = ""
    try:
        raw = str(config.session.tmux_session or "")
    except AttributeError:
        raw = ""
    if raw and raw != "zf":
        return raw
    return f"zf-{project_id[:12]}" if project_id else (raw or "zf")


def _tmux_has_session(session: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0

