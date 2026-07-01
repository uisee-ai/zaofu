"""Plan approval controlled actions (B15, doc 93 §7.1).

token-gated approve/reject from Web/kanban-agent surfaces. The agent
only recommends; the human clicks — actor is always "operator". Events
land in events.jsonl; the kernel wake re-enters incubation (approved,
B14) or feeds synth replan (rejected, B14-S6).
"""

from __future__ import annotations

from zf.core.events.model import ZfEvent


class PlanApprovalActionsMixin:
    def _plan_approval_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        """B15 (doc 93 §7.1): token-gated plan approve/reject。

        agent 预审只建议、人点按钮 —— actor 恒 operator(surface 标注
        来源);事件落 events.jsonl 后 kernel wake 重入孵化(approved,
        B14)或 replan 回喂(rejected,B14-S6)。
        """
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            return {
                "_status_code": 400,
                "ok": False,
                "action": action,
                "reason": "plan_id is required",
            }
        reason = str(payload.get("reason") or "")
        if action == "plan-reject" and not reason.strip():
            return {
                "_status_code": 400,
                "ok": False,
                "action": action,
                "reason": "reject requires a reason (feedback for synth replan)",
            }
        event_type = (
            "plan.approved" if action == "plan-approve" else "plan.rejected"
        )
        event_payload = {
            "plan_id": plan_id,
            "via": f"controlled-action:{self.source}",
            "surface": self.surface,
        }
        if reason:
            event_payload["reason"] = reason
        event = self.writer.emit(
            event_type,
            actor="operator",
            causation_id=plan_id,
            correlation_id=requested.correlation_id,
            payload=event_payload,
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="recorded",
            task_id="",
            extra={"plan_id": plan_id},
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "recorded",
            "action": action,
            "plan_id": plan_id,
            "event_id": event.id,
        }
