"""Proposal identity and exactly-once wrapper for controlled actions."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.action_orchestrator import ControlledActionOrchestrator
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.kanban_proposals import (
    LEGACY_PROPOSAL_EVENT,
    LEGACY_PROPOSAL_RESOLVED_EVENT,
    PROPOSAL_RESOLVED_EVENT,
    proposal_execution_gate,
)


class ProposalExecutionActionsMixin:
    def execute(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict,
        requested: ZfEvent,
    ) -> dict:
        proposal_event_id = str(
            payload.get("proposal_event_id") or ""
        ).strip()
        if proposal_event_id:
            with locked_path(
                self.state_dir
                / "locks"
                / "kanban-proposal-execution"
            ):
                return self._execute_with_proposal_gate(
                    action=action,
                    requested_action=requested_action,
                    payload=payload,
                    requested=requested,
                    proposal_event_id=proposal_event_id,
                )
        return self._run_controlled_action(
            action=action,
            requested_action=requested_action,
            payload=payload,
            requested=requested,
            proposal_event_id="",
            proposal_gate={},
        )

    def _execute_with_proposal_gate(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict,
        requested: ZfEvent,
        proposal_event_id: str,
    ) -> dict:
        proposal_gate: dict[str, Any] = proposal_execution_gate(
            self.writer.event_log.read_all(),
            proposal_event_id=proposal_event_id,
            action=action,
            execution_payload=payload,
        )
        if not proposal_gate.get("ok"):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=str(
                    proposal_gate.get("status") or "proposal_rejected"
                ),
                status_code=409,
                status=str(
                    proposal_gate.get("status") or "proposal_rejected"
                ),
            )
        if proposal_gate.get("status") == "already_resolved":
            return _already_resolved_result(
                action=action,
                requested_action=requested_action,
                proposal_event_id=proposal_event_id,
                proposal_gate=proposal_gate,
            )
        return self._run_controlled_action(
            action=action,
            requested_action=requested_action,
            payload=payload,
            requested=requested,
            proposal_event_id=proposal_event_id,
            proposal_gate=proposal_gate,
        )

    def _run_controlled_action(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict,
        requested: ZfEvent,
        proposal_event_id: str,
        proposal_gate: dict[str, Any],
    ) -> dict:
        from contextlib import nullcontext
        from zf.runtime.stage_replan_generation import generation_publication_lock

        publication = (locked_path(generation_publication_lock(self.state_dir, requested.id))
                       if action == "stage-replan-new-generation" else nullcontext())
        with publication:
            result = ControlledActionOrchestrator(
                writer=self.writer,
                actor=self.actor,
                surface=self.surface,
            ).run(
                action=action,
                requested_action=requested_action,
                payload=payload,
                requested=requested,
                task_id=_task_id_from_payload(payload),
                handler=lambda: self._execute_action(
                    action=action,
                    requested_action=requested_action,
                    payload=payload,
                    requested=requested,
                ),
            )
        if (
            proposal_event_id
            and bool(result.get("ok"))
            and action not in {"create-task", "kanban-proposal-dismiss"}
        ):
            resolution_event_type = (
                LEGACY_PROPOSAL_RESOLVED_EVENT
                if str(proposal_gate.get("proposal_event_type") or "")
                == LEGACY_PROPOSAL_EVENT
                else PROPOSAL_RESOLVED_EVENT
            )
            resolution_payload = {
                **dict(proposal_gate.get("proposal_context") or {}),
                "proposal_event_id": proposal_event_id,
                "resolution": "executed",
                "action": action,
                "proposal_id": str(
                    proposal_gate.get("proposal_id") or ""
                ),
                "proposal_digest": str(
                    proposal_gate.get("proposal_digest") or ""
                ),
                "revision": int(
                    proposal_gate.get("revision") or 1
                ),
                "source": self.source,
            }
            self.writer.emit(
                resolution_event_type,
                actor=self.actor,
                task_id=(
                    str(proposal_gate.get("task_id") or "").strip()
                    or _task_id_from_payload(payload)
                    or None
                ),
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload=resolution_payload,
            )
        return result


def _already_resolved_result(
    *,
    action: str,
    requested_action: str,
    proposal_event_id: str,
    proposal_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "_status_code": 200,
        "ok": True,
        "status": "already_resolved",
        "action": action,
        "requested_action": requested_action,
        "proposal_event_id": proposal_event_id,
        "proposal_id": str(proposal_gate.get("proposal_id") or ""),
        "proposal_digest": str(
            proposal_gate.get("proposal_digest") or ""
        ),
        "revision": int(proposal_gate.get("revision") or 1),
        "event_id": str(
            proposal_gate.get("resolution_event_id") or ""
        ),
        "task_id": str(proposal_gate.get("task_id") or ""),
    }


__all__ = ["ProposalExecutionActionsMixin"]
