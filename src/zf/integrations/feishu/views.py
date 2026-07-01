"""Feishu view dataclasses for projections."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskView:
    task_id: str
    title: str
    status: str
    assigned_to: str = ""
    priority: str = "medium"
    last_event: str = ""
    blocked_reason: str = ""


@dataclass
class AlertView:
    alert_type: str
    severity: str  # critical, high, medium, low
    task_id: str = ""
    reason: str = ""
    recommended_actions: list[str] = field(default_factory=list)


@dataclass
class SummaryView:
    project: str
    loop_state: str = "unknown"
    done: int = 0
    in_progress: int = 0
    blocked: int = 0
    backlog: int = 0
    risks: list[str] = field(default_factory=list)


@dataclass
class ApprovalView:
    approval_id: str
    status: str = "requested"  # requested, approved, denied, expired
    kind: str = "escalation"
    task_id: str = ""
    reason: str = ""
    allowed_actions: list[str] = field(default_factory=list)
