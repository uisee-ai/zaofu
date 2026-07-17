"""Operator-surgery controlled actions (ZF-E2E-PRDCTL-P2-8, 2026-07-12).

Four deterministic verbs the human overseer performed by hand across the
deepwater/csvstats rounds, promoted to controlled actions so the Run Manager
can PROPOSE them and the owner can one-click approve (router policy:
needs_approval — none auto-executes):

- payload-repair-reemit: re-emit an event with missing ref-class keys added
  (deepwater: test.passed re-emitted with candidate_ref by hand).
- briefing-redeliver: request a briefing redelivery through the kernel
  dispatch path (swallowed-briefing recovery).
- human-decision-dismiss: acknowledge a dead human decision (same event
  contract as the web route).
- ship-retry: re-run auto-ship after judge.passed when ship failed/was
  missed (csvstats: manual candidate→master merge).
"""

from __future__ import annotations

from zf.core.events.model import ZfEvent

# Add-only key whitelist: ref-class payload fields whose absence broke a
# downstream contract in live rounds. Repair may add, never overwrite.
PAYLOAD_REPAIR_ALLOWED_KEYS = frozenset({
    "candidate_ref",
    "candidate_head_commit",
    "task_map_ref",
    "task_ref",
    "target_ref",
    "source_index_ref",
    "evidence_refs",
})


class SurgeryActionsMixin:
    def _surgery_failed(
        self, requested, action, requested_action, reason, status_code=422,
    ) -> dict:
        return self._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=None,
            reason=reason,
            status_code=status_code,
            status="failed",
        )

    def _surgery_ok(
        self, requested, event, action, requested_action, extra,
    ) -> dict:
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="applied",
            task_id=None,
            extra=extra,
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "applied",
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            **extra,
        }

    def _payload_repair_reemit_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        source_event_id = str(payload.get("source_event_id") or "").strip()
        patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else {}
        if not source_event_id or not patch:
            return self._surgery_failed(
                requested, action, requested_action,
                "payload-repair-reemit requires source_event_id and patch",
            )
        illegal = sorted(set(patch) - PAYLOAD_REPAIR_ALLOWED_KEYS)
        if illegal:
            return self._surgery_failed(
                requested, action, requested_action,
                f"patch keys not in ref-class whitelist: {illegal}",
            )
        source = None
        for event in self.writer.event_log.read_all():
            if event.id == source_event_id:
                source = event
                break
        if source is None:
            return self._surgery_failed(
                requested, action, requested_action,
                f"source event {source_event_id} not found", 404,
            )
        base = dict(source.payload if isinstance(source.payload, dict) else {})
        overwritten = sorted(
            key for key, value in patch.items()
            if str(base.get(key) or "").strip() and base.get(key) != value
        )
        if overwritten:
            return self._surgery_failed(
                requested, action, requested_action,
                f"repair is add-only; keys already set: {overwritten}", 409,
            )
        base.update(dict(patch))
        base["rework_of"] = source_event_id
        base["repair_source"] = "payload_repair_reemit"
        emitted = self.writer.append(ZfEvent(
            type=source.type,
            actor=self.actor,
            task_id=source.task_id,
            payload=base,
            causation_id=requested.id,
            correlation_id=source.correlation_id,
        ))
        return self._surgery_ok(requested, emitted, action, requested_action, {
            "reemitted_event_id": emitted.id,
            "source_event_id": source_event_id,
            "patched_keys": sorted(patch),
        })

    def _briefing_redeliver_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        role_instance = str(payload.get("role_instance") or "").strip()
        if not role_instance:
            return self._surgery_failed(
                requested, action, requested_action,
                "briefing-redeliver requires role_instance",
            )
        emitted = self.writer.append(ZfEvent(
            type="briefing.redeliver.requested",
            actor=self.actor,
            payload={
                "role_instance": role_instance,
                "briefing_path": str(payload.get("briefing_path") or ""),
                "fanout_id": str(payload.get("fanout_id") or ""),
                "child_id": str(payload.get("child_id") or ""),
                "reason": str(payload.get("reason") or "operator/RM briefing redelivery"),
            },
            causation_id=requested.id,
        ))
        return self._surgery_ok(requested, emitted, action, requested_action, {
            "request_event_id": emitted.id,
            "role_instance": role_instance,
        })

    def _human_decision_dismiss_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        token = str(payload.get("decision_token") or "").strip()
        if not token:
            return self._surgery_failed(
                requested, action, requested_action,
                "human-decision-dismiss requires decision_token",
            )
        open_escalation = None
        acknowledged = False
        for event in self.writer.event_log.read_all():
            event_payload = event.payload if isinstance(event.payload, dict) else {}
            if str(event_payload.get("decision_token") or "") != token:
                continue
            if event.type == "human.escalate":
                open_escalation = event
            elif event.type == "human.escalation.acknowledged":
                acknowledged = True
        if open_escalation is None:
            return self._surgery_failed(
                requested, action, requested_action,
                f"no escalation found for decision_token {token}", 404,
            )
        if acknowledged:
            return self._surgery_failed(
                requested, action, requested_action,
                f"decision_token {token} already acknowledged", 409,
            )
        emitted = self.writer.append(ZfEvent(
            type="human.escalation.acknowledged",
            actor=self.actor,
            task_id=open_escalation.task_id,
            payload={
                "decision_token": token,
                "status": "dismissed",
                "escalation_event_id": open_escalation.id,
                "reason": str(payload.get("reason") or "dismissed via controlled action"),
            },
            causation_id=requested.id,
            correlation_id=open_escalation.correlation_id,
        ))
        return self._surgery_ok(requested, emitted, action, requested_action, {
            "acknowledged_event_id": emitted.id,
            "decision_token": token,
        })

    def _ship_retry_action(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        if self.config is None or self.project_root is None:
            return self._surgery_failed(
                requested, action, requested_action,
                "ship-retry requires config and project_root",
            )
        events = self.writer.event_log.read_all()
        run_id = str(payload.get("run_id") or payload.get("workflow_run_id") or "")
        claim_id = str(payload.get("claim_id") or "")
        if run_id or claim_id:
            return self._scoped_goal_delivery_retry(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
                events=events,
                run_id=run_id,
                claim_id=claim_id,
            )

        judge_passed = None
        shipped = False
        for event in events:
            if event.type == "judge.passed":
                judge_passed = event
            elif event.type == "ship.completed":
                shipped = True
        if judge_passed is None:
            return self._surgery_failed(
                requested, action, requested_action,
                "ship-retry requires a judge.passed event", 409,
            )
        if shipped:
            return self._surgery_failed(
                requested, action, requested_action,
                "delivery already shipped (ship.completed present)", 409,
            )
        judge_payload = (
            judge_passed.payload if isinstance(judge_passed.payload, dict) else {}
        )
        target_ref = str(
            payload.get("target_ref")
            or judge_payload.get("candidate_ref")
            or judge_payload.get("target_ref")
            or ""
        ).strip()
        if not target_ref:
            return self._surgery_failed(
                requested, action, requested_action,
                "ship-retry could not resolve a candidate target_ref",
            )
        from zf.runtime.ship import ShipService

        result = ShipService(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
            event_log=self.writer.event_log,
        ).ship(target_ref=target_ref, event_writer=self.writer)
        if result.status != "completed":
            return self._surgery_failed(
                requested, action, requested_action,
                f"ship-retry {result.status}: "
                f"{result.payload.get('blockers') or result.payload}",
                409,
            )
        return self._surgery_ok(requested, requested, action, requested_action, {
            "ship_status": result.status,
            "target_ref": target_ref,
        })

    def _scoped_goal_delivery_retry(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        events: list[ZfEvent],
        run_id: str,
        claim_id: str,
    ) -> dict:
        claims = [
            event
            for event in events
            if event.type == "run.goal.completion.claimed"
            and isinstance(event.payload, dict)
            and (not run_id or str(event.payload.get("run_id") or "") == run_id)
            and (not claim_id or str(event.payload.get("claim_id") or "") == claim_id)
        ]
        if not claims:
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                "ship-retry requires a scoped Goal completion claim",
                409,
            )
        claim = claims[-1]
        claim_payload = claim.payload if isinstance(claim.payload, dict) else {}
        run_id = str(claim_payload.get("run_id") or run_id)
        claim_id = str(claim_payload.get("claim_id") or claim_id)
        delivery_events = [
            event
            for event in events
            if event.type.startswith("run.delivery.")
            and isinstance(event.payload, dict)
            and str(event.payload.get("claim_id") or "") == claim_id
            and str(event.payload.get("run_id") or event.correlation_id or "") == run_id
        ]
        if any(event.type == "run.delivery.settled" for event in delivery_events):
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                f"delivery for claim {claim_id} is already settled",
                409,
            )
        latest_delivery = delivery_events[-1] if delivery_events else None
        latest_payload = (
            latest_delivery.payload
            if latest_delivery is not None and isinstance(latest_delivery.payload, dict)
            else {}
        )
        target_ref = str(
            latest_payload.get("candidate_ref")
            or claim_payload.get("candidate_ref")
            or ""
        ).strip()
        requested_target = str(
            payload.get("target_ref") or payload.get("candidate_ref") or ""
        ).strip()
        if requested_target and target_ref and requested_target != target_ref:
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                "ship-retry target does not match the scoped Goal claim",
                409,
            )
        target_ref = target_ref or requested_target
        operation_id = str(
            latest_payload.get("delivery_operation_id")
            or f"delivery-{claim_id}"
        )
        requested_operation = str(payload.get("delivery_operation_id") or "")
        if requested_operation and requested_operation != operation_id:
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                "ship-retry operation does not match the scoped Goal delivery",
                409,
            )
        if not target_ref:
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                "ship-retry could not resolve the scoped candidate_ref",
            )

        from zf.runtime.ship import ShipService

        result = ShipService(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
            event_log=self.writer.event_log,
        ).ship(
            target_ref=target_ref,
            pdd_id=str(claim_payload.get("goal_id") or ""),
            event_writer=self.writer,
            causation_id=requested.id,
            correlation_id=run_id,
        )
        terminal_type = "run.delivery.settled" if result.ok else (
            "run.delivery.blocked"
            if result.event_type in {"ship.blocked", "ship.conflict"}
            else "run.delivery.failed"
        )
        terminal = self.writer.append(ZfEvent(
            type=terminal_type,
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": run_id,
                "goal_id": str(claim_payload.get("goal_id") or ""),
                "claim_id": claim_id,
                "delivery_operation_id": operation_id,
                "candidate_ref": target_ref,
                "target_commit": str(claim_payload.get("target_commit") or ""),
                "ship_event_type": result.event_type,
                "ship_status": result.status,
                "ship_result": dict(result.payload or {}),
                "reason": (
                    "scoped Goal delivery retry settled"
                    if result.ok
                    else f"scoped Goal delivery retry {result.status}"
                ),
            },
        ))
        if not result.ok:
            return self._surgery_failed(
                requested,
                action,
                requested_action,
                f"ship-retry {result.status}: "
                f"{result.payload.get('blockers') or result.payload}",
                409,
            )
        return self._surgery_ok(requested, terminal, action, requested_action, {
            "run_id": run_id,
            "claim_id": claim_id,
            "delivery_operation_id": operation_id,
            "ship_status": result.status,
            "target_ref": target_ref,
            "terminal_event_id": terminal.id,
        })
