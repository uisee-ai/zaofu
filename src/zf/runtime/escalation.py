"""Human escalation flow — escalate, steer, resolve."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent


def _escalate_signature(task_id: str, reason: str) -> tuple[str, str]:
    # 数字归一化:"rework cap (4/3) exceeded" 与 "(5/3)" 是同一件事在刷屏
    return (task_id, re.sub(r"\d+", "#", reason or "").strip())


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

    # avbs-r4 F12: 同签名 escalate 在窗口内去重。r4 终局 'rework cap
    # exceeded' 以 8-10 秒/发刷了 21 条,直接迫使 operator 停机——重复
    # escalate 不带新信息,首条即完整信号。
    _THROTTLE_SECONDS = 600

    def __init__(self, state_dir: Path, config: ZfConfig | None = None) -> None:
        self.state_dir = state_dir
        self.event_log = event_log_from_project(state_dir, config=config)
        self.steer_path = state_dir / "steer"

    def escalate(self, reason: str, task_id: str | None = None) -> None:
        """Emit escalation event and write steer marker."""
        if self._recently_escalated(reason, task_id or ""):
            return
        self.event_log.append(ZfEvent(
            type="human.escalate",
            actor="orchestrator",
            task_id=task_id or "",
            payload={"reason": reason},
        ))
        self.steer_path.write_text("")  # empty steer file signals escalation

    def _recently_escalated(self, reason: str, task_id: str) -> bool:
        signature = _escalate_signature(task_id, reason)
        now = datetime.now(timezone.utc)
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in reversed(events):
            if event.type != "human.escalate":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if _escalate_signature(
                str(event.task_id or ""), str(payload.get("reason") or ""),
            ) != signature:
                continue
            try:
                age = (now - datetime.fromisoformat(event.ts)).total_seconds()
            except (TypeError, ValueError):
                return False
            return 0 <= age < self._THROTTLE_SECONDS
        return False

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
        # P1-11(审计 D5):Tier3 ack 事件此前全仓无发射器,S3 no-dead-end
        # 判据(escalate 有 operator 响应)因此不可机读。human.resolved
        # 即 owner ack,补发 remediation.escalated_acked 供 pulse/稳定性
        # 指标消费(overview_pulse 两类 ack 均已在读)。
        self.event_log.append(ZfEvent(
            type="remediation.escalated_acked",
            actor="human",
            payload={"response": response[:200], "source": "escalation_resolve"},
        ))
        # Clear steer file
        if self.steer_path.exists():
            self.steer_path.unlink()
