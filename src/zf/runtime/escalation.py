"""Human escalation flow — escalate, steer, resolve."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent


@dataclass
class EscalationRequest:
    reason: str
    task_id: str | None = None


@dataclass
class EscalationResponse:
    text: str
    resolved: bool = True


class EscalationManager:
    """Manage human escalation flow."""

    def __init__(self, state_dir: Path, config: ZfConfig | None = None) -> None:
        self.state_dir = state_dir
        self.event_log = event_log_from_project(state_dir, config=config)
        self.steer_path = state_dir / "steer"

    def escalate(self, reason: str, task_id: str | None = None) -> None:
        """Emit escalation event and write steer marker."""
        self.event_log.append(ZfEvent(
            type="human.escalate",
            actor="orchestrator",
            task_id=task_id or "",
            payload={"reason": reason},
        ))
        self.steer_path.write_text("")  # empty steer file signals escalation

    def has_steer(self) -> bool:
        """Check if human has written a steer response."""
        if not self.steer_path.exists():
            return False
        return len(self.steer_path.read_text().strip()) > 0

    def read_steer(self) -> EscalationResponse | None:
        """Read the human's steer response."""
        if not self.has_steer():
            return None
        text = self.steer_path.read_text().strip()
        return EscalationResponse(text=text)

    def resolve(self, response: str) -> None:
        """Resolve the escalation."""
        self.event_log.append(ZfEvent(
            type="human.resolved",
            actor="human",
            payload={"response": response},
        ))
        # Clear steer file
        if self.steer_path.exists():
            self.steer_path.unlink()
