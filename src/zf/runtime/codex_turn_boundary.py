"""Structured native turn boundaries; never infer completion from terminal text."""

from datetime import datetime


def timestamp(value: object) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, OverflowError):
        return 0


class CodexTurnBoundary:
    """Consume the same records as usage, retaining no prompts or responses."""

    def __init__(self) -> None:
        self.valid = True
        self.session = ""
        self.turn_ids: set[str] = set()
        self.turn = ""
        self.state = "empty"
        self.started = self.completed = self.usage_at = 0.0
        self.prior_usage = False
        self.aborted = False

    def consume(self, row: dict) -> None:
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            return
        if row.get("type") == "session_meta":
            if self.session or not payload.get("id") or not payload.get("cwd"):
                self.valid = False
            self.session = str(payload.get("id") or "")
            return
        if row.get("type") != "event_msg":
            return
        kind = payload.get("type")
        when = timestamp(row.get("timestamp"))
        if kind == "task_started":
            turn = payload.get("turn_id")
            if not isinstance(turn, str) or not turn or turn in self.turn_ids or not when or self.state == "running":
                self.valid = False
                return
            self.turn_ids.add(turn)
            self.turn, self.state, self.started = turn, "running", when
            self.completed = self.usage_at = 0
        elif kind == "token_count" and isinstance(payload.get("info"), dict):
            if self.state != "running" or when < self.started:
                self.prior_usage = True
            else:
                self.usage_at = when
        elif kind in {"task_complete", "turn_aborted"}:
            if (self.state != "running" or payload.get("turn_id") != self.turn
                    or not when or when < max(self.started, self.usage_at)):
                self.valid = False
                return
            self.state = "completed" if kind == "task_complete" else "aborted"
            self.aborted = self.aborted or kind == "turn_aborted"
            self.completed = when

    def project(self) -> dict:
        if not self.valid or not self.session:
            return {}
        return {"session_id": self.session, "turn_count": len(self.turn_ids), "turn_id": self.turn,
                "state": self.state, "started_at": self.started, "completed_at": self.completed,
                "usage_at": self.usage_at, "prior_usage": self.prior_usage, "aborted": self.aborted}


def completed_session(after: dict, *, session_id: str) -> bool:
    """A fresh terminal owns its entire Session, including every earlier turn."""
    boundary = after.get("turn_boundary", {})
    return bool(boundary and boundary.get("session_id") == session_id
                and boundary.get("state") == "completed" and boundary.get("turn_count", 0) > 0
                and boundary.get("aborted") is False and not boundary.get("prior_usage", True)
                and 0 < boundary.get("started_at", 0) <= boundary.get("usage_at", 0)
                <= boundary.get("completed_at", 0))


def completed_turn(before: dict, after: dict, *, started_at: float, session_id: str) -> bool:
    boundary = after.get("turn_boundary", {})
    prior = before.get("turn_boundary", {})
    if (not boundary or boundary.get("session_id") != session_id or boundary.get("state") != "completed"
            or boundary.get("started_at", 0) < started_at
            or not boundary.get("usage_at", 0)
            or not boundary["started_at"] <= boundary["usage_at"] <= boundary.get("completed_at", 0)):
        return False
    if prior and prior.get("session_id") == session_id:
        return (prior.get("state") != "running"
                and boundary["turn_count"] == prior.get("turn_count", 0) + 1)
    # A newly adopted identity can prove its first turn, not an arbitrary resumed turn.
    return boundary["turn_count"] == 1 and not boundary["prior_usage"]


def first_turn(after: dict, *, started_at: float, session_id: str) -> bool:
    return completed_turn({}, after, started_at=started_at, session_id=session_id)
