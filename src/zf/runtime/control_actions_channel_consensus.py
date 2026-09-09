"""Controlled Channel question and consensus decisions."""

from __future__ import annotations

from zf.core.state.locks import locked_path
from zf.core.events import ZfEvent
from zf.runtime.channel_contracts import normalize_product_discussion_mode
from zf.runtime.channel_consensus_identity import consensus_reached_payload
from zf.runtime.channel_owner_authority import (
    channel_owner_authority_error,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_gate import question_dedup_gate_state
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _optional_str,
    _required_text,
    _stable_control_id,
    _task_id_from_payload,
)


class ChannelConsensusActionsMixin:
    def _channel_question_resolve(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _normal_channel_id(
            _required_text(payload, "channel_id")
        )
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        question_id = _required_text(payload, "question_id")
        resolution = _required_text(payload, "resolution")
        channel = project_channel(self.state_dir, channel_id) or {}
        dedup_gate_state = question_dedup_gate_state(
            channel,
            thread_id=thread_id,
        )
        if dedup_gate_state != "open":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel questions are being consolidated; wait for "
                    "canonical questions before resolving"
                    if dedup_gate_state == "consolidating"
                    else "channel question consolidation is blocked; restart "
                    "or repair the discussion before resolving"
                ),
                status_code=409,
                status=(
                    "question_consolidation_pending"
                    if dedup_gate_state == "consolidating"
                    else "question_consolidation_blocked"
                ),
            )
        question = next(
            (
                item
                for item in channel.get("open_questions") or []
                if isinstance(item, dict)
                and str(item.get("question_id") or "") == question_id
                and str(item.get("thread_id") or "main") == thread_id
            ),
            None,
        )
        if question is None or str(question.get("status") or "") != "open":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel question is missing or already resolved",
                status_code=409,
                status="question_not_open",
            )
        answer = str(payload.get("answer") or "").strip()
        if resolution in {"answered", "assumption"} and not answer:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="answer is required for answered or assumption resolution",
                status="invalid_payload",
            )
        event = self.writer.emit(
            "channel.question.resolved",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "resolution": resolution,
                "resolved_by": str(
                    payload.get("resolved_by") or "owner:operator"
                ),
                "answer": answer,
                "risk_note": str(payload.get("risk_note") or ""),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="resolved",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "resolution": resolution,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "resolved",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "question_id": question_id,
            "event_id": event.id,
        }

    def _channel_consensus_decision(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        decision: str,
    ) -> dict:
        channel_id = _normal_channel_id(
            _required_text(payload, "channel_id")
        )
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        lock = (
            self.state_dir
            / "locks"
            / _stable_control_id("channel-consensus", channel_id, thread_id)
        )
        with locked_path(lock):
            return self._channel_consensus_decision_locked(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
                decision=decision,
                channel_id=channel_id,
                thread_id=thread_id,
            )

    def _channel_consensus_decision_locked(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        decision: str,
        channel_id: str,
        thread_id: str,
    ) -> dict:
        channel = project_channel(self.state_dir, channel_id) or {}
        consensus = (
            channel.get("consensus", {}).get(thread_id)
            if isinstance(channel.get("consensus"), dict)
            else {}
        )
        if not isinstance(consensus, dict) or not str(
            consensus.get("artifact_ref") or ""
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel consensus proposal is missing",
                status_code=409,
                status="consensus_not_proposed",
            )
        artifact_ref = str(consensus.get("artifact_ref") or "")
        artifact_digest = str(consensus.get("artifact_digest") or "")
        expected_ref = str(payload.get("artifact_ref") or "")
        expected_digest = str(payload.get("artifact_digest") or "")
        expected_revision = int(payload.get("prd_revision") or 0)
        current_revision = int(consensus.get("prd_revision") or 0)
        if (
            (expected_ref and expected_ref != artifact_ref)
            or (
                expected_digest
                and expected_digest.removeprefix("sha256:")
                != artifact_digest.removeprefix("sha256:")
            )
            or expected_revision != current_revision
            or bool(consensus.get("revision_pending"))
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel consensus artifact/revision binding is stale; "
                    f"current revision is {current_revision}"
                ),
                status_code=409,
                status="consensus_stale",
            )
        owner_actor_ref = str(
            channel.get("owner_actor_ref")
            or consensus.get("owner_actor_ref")
            or ""
        )
        authority_error = channel_owner_authority_error(
            channel,
            actor=self.actor,
            capability=(
                "channel.consensus.confirm"
                if decision == "confirm"
                else "channel.consensus.block"
            ),
        )
        if not owner_actor_ref or authority_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=authority_error or "Channel Owner binding is missing",
                status_code=403,
                status="forbidden",
            )
        member_id = self.actor
        idempotency_key = str(
            payload.get("idempotency_key")
            or _stable_control_id(
                "channel-consensus-decision",
                decision,
                channel_id,
                thread_id,
                current_revision,
                artifact_digest,
                self.actor,
            )
        )
        for prior in reversed(self.writer.event_log.read_all()):
            if prior.type not in {
                "channel.consensus.signed",
                "channel.consensus.blocked",
            }:
                continue
            prior_payload = (
                prior.payload if isinstance(prior.payload, dict) else {}
            )
            if (
                str(prior_payload.get("channel_id") or "") == channel_id
                and str(prior_payload.get("thread_id") or "main")
                == thread_id
                and str(prior_payload.get("idempotency_key") or "")
                == idempotency_key
            ):
                return {
                    "_status_code": 200,
                    "ok": True,
                    "status": (
                        "confirmed"
                        if prior.type == "channel.consensus.signed"
                        else "blocked"
                    ),
                    "action": action,
                    "requested_action": requested_action,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "artifact_ref": artifact_ref,
                    "artifact_digest": artifact_digest,
                    "prd_revision": current_revision,
                    "event_id": prior.id,
                    "idempotency_key": idempotency_key,
                    "duplicate": True,
                }
        if decision == "confirm":
            readiness_verdict = str(
                consensus.get("readiness_verdict") or "unassessed"
            )
            if (
                readiness_verdict in {"needs_owner", "needs_multi_lens"}
                and not bool(payload.get("accept_readiness_risk"))
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason=(
                        "readiness risk requires explicit "
                        "accept_readiness_risk=true"
                    ),
                    status_code=409,
                    status="readiness_risk_unaccepted",
                )
            event_type = "channel.consensus.signed"
            event_payload = {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "prd_revision": current_revision,
                "readiness_ref": str(
                    consensus.get("readiness_ref") or ""
                ),
                "readiness_digest": str(
                    consensus.get("readiness_digest") or ""
                ),
                "risk_accepted": bool(
                    payload.get("accept_readiness_risk")
                ),
                "idempotency_key": idempotency_key,
                "source": self.surface,
            }
            status = "confirmed"
        else:
            blocker = str(
                payload.get("blocker_question")
                or payload.get("reason")
                or ""
            ).strip()
            if not blocker:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="blocker_question is required",
                    status="invalid_payload",
                )
            event_type = "channel.consensus.blocked"
            event_payload = {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "blocker_question_id": str(
                    payload.get("blocker_question_id")
                    or _stable_control_id(
                        "q-blocker",
                        channel_id,
                        thread_id,
                        artifact_digest,
                        blocker,
                    )
                ),
                "blocker_question": blocker,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "prd_revision": current_revision,
                "idempotency_key": idempotency_key,
                "source": self.surface,
            }
            status = "blocked"
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=event_payload,
        )
        product_mode = normalize_product_discussion_mode(
            consensus.get("product_mode")
            or (
                channel.get("discussion", {}).get("mode")
                if isinstance(channel.get("discussion"), dict)
                else ""
            )
        )
        reached_event = None
        if decision == "confirm" and product_mode != "multi_lens":
            reached_event = self.writer.emit(
                "channel.consensus.reached",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=event.id,
                correlation_id=channel_id,
                payload=consensus_reached_payload(
                    consensus,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    source=self.surface,
                    confirmed_by=self.actor,
                    risk_accepted=bool(
                        payload.get("accept_readiness_risk")
                    ),
                ),
            )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "prd_revision": current_revision,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
            "prd_revision": current_revision,
            "event_id": event.id,
            "reached_event_id": (
                reached_event.id if reached_event is not None else ""
            ),
            "idempotency_key": idempotency_key,
            "duplicate": False,
        }
