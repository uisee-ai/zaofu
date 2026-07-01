"""Module parity scan bridge helpers for refactor workflows."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_types import OrchestratorDecision


class ModuleParityBridgeMixin:
    """Deterministic verify -> parity scan -> gap amend bridge."""

    def _bridge_verify_passed_to_parity_scan(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Require a module parity scan after candidate-level verify passes."""

        if not self._has_fanout_reader_trigger("verify.parity_scan.requested"):
            return None
        if self._has_bridge_output(
            event.id,
            {"verify.parity_scan.requested"},
        ):
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        context = self._latest_refactor_context(payload)
        pdd_id = self._first_payload_text(
            payload,
            context,
            "pdd_id",
            "feature_id",
        ) or str(event.task_id or "")
        feature_id = self._first_payload_text(
            payload,
            context,
            "feature_id",
            "pdd_id",
        ) or pdd_id
        trace_id = (
            self._first_payload_text(payload, context, "trace_id")
            or str(event.correlation_id or event.id)
        )
        candidate_ref = self._first_payload_text(
            payload,
            context,
            "candidate_ref",
            "target_ref",
            "branch",
        )
        task_map_ref = self._first_payload_text(
            payload,
            context,
            "task_map_ref",
            "base_task_map_ref",
            "supersedes_task_map_ref",
        )
        request_payload = {
            "schema_version": "module-parity-scan-request.v1",
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            "trace_id": trace_id,
            "task_map_ref": task_map_ref,
            "source_index_ref": self._first_payload_text(
                payload,
                context,
                "source_index_ref",
            ),
            "source_commit": self._first_payload_text(
                payload,
                context,
                "source_commit",
            ),
            "candidate_base_commit": self._first_payload_text(
                payload,
                context,
                "candidate_base_commit",
                "source_commit",
            ),
            "candidate_ref": candidate_ref,
            "target_ref": candidate_ref,
            "source_event_id": event.id,
            "source": "verify_passed_bridge",
        }
        requested = self.event_writer.append(ZfEvent(
            type="verify.parity_scan.requested",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=trace_id,
            payload=request_payload,
        ))
        self._maybe_start_reader_fanout(requested)
        return OrchestratorDecision(
            action="bridge",
            reason="verify.passed requested module parity scan",
        )

    def _bridge_module_parity_scan_completed(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Close parity or turn scan findings into a canonical gap task-map."""

        if self._has_bridge_output(
            event.id,
            {"gap_plan.ready", "module.parity.closed", "module.parity.blocked"},
        ):
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        feature_id = str(payload.get("feature_id") or pdd_id).strip()
        trace_id = str(payload.get("trace_id") or event.correlation_id or event.id).strip()
        task_map_ref = str(
            payload.get("task_map_ref")
            or payload.get("base_task_map_ref")
            or payload.get("supersedes_task_map_ref")
            or ""
        ).strip()
        gap_tasks = self._parity_gap_tasks(payload)
        open_gap_count = self._payload_int(
            payload,
            "open_p0_p1_gap_count",
            "gap_task_count",
        )
        if gap_tasks:
            gap_event = self.event_writer.append(ZfEvent(
                type="gap_plan.ready",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "schema_version": "module-gap-plan.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "task_map_ref": task_map_ref,
                    "gap_plan_ref": str(payload.get("gap_plan_ref") or ""),
                    "gap_tasks": gap_tasks,
                    "gap_task_count": len(gap_tasks),
                    "source_index_ref": str(payload.get("source_index_ref") or ""),
                    "source_commit": str(payload.get("source_commit") or ""),
                    "candidate_base_commit": str(
                        payload.get("candidate_base_commit")
                        or payload.get("source_commit")
                        or ""
                    ),
                    "target_ref": str(
                        payload.get("target_ref")
                        or payload.get("candidate_ref")
                        or ""
                    ),
                    "source_event_id": event.id,
                    "source": "module_parity_scan_bridge",
                },
            ))
            decision = self._bridge_gap_plan_ready_to_task_map(gap_event)
            if decision:
                return decision
            return OrchestratorDecision(
                action="bridge",
                reason=f"module parity scan produced {len(gap_tasks)} gap task(s)",
            )

        if open_gap_count and open_gap_count > 0:
            self.event_writer.append(ZfEvent(
                type="module.parity.blocked",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "task_map_ref": task_map_ref,
                    "open_p0_p1_gap_count": open_gap_count,
                    "reason": "module parity scan reported open gaps without gap_tasks",
                    "source_event_id": event.id,
                    "source": "module_parity_scan_bridge",
                },
            ))
            return OrchestratorDecision(
                action="block",
                reason="module parity scan missing gap_tasks for open gaps",
            )

        if open_gap_count == 0 or self._payload_declares_parity_closed(payload):
            closed = self.event_writer.append(ZfEvent(
                type="module.parity.closed",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "task_map_ref": task_map_ref,
                    "candidate_ref": str(payload.get("candidate_ref") or ""),
                    "target_ref": str(
                        payload.get("target_ref")
                        or payload.get("candidate_ref")
                        or ""
                    ),
                    "open_p0_p1_gap_count": 0,
                    "source_event_id": event.id,
                    "source": "module_parity_scan_bridge",
                },
            ))
            self._maybe_start_reader_fanout(closed)
            return OrchestratorDecision(
                action="bridge",
                reason="module parity scan closed without open P0/P1 gaps",
            )

        self.event_writer.append(ZfEvent(
            type="module.parity.blocked",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=trace_id,
            payload={
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "trace_id": trace_id,
                "task_map_ref": task_map_ref,
                "reason": (
                    "module parity scan completed without explicit closure "
                    "or dispatchable gap_tasks"
                ),
                "source_event_id": event.id,
                "source": "module_parity_scan_bridge",
            },
        ))
        return OrchestratorDecision(
            action="block",
            reason="module parity scan lacks closure/gap evidence",
        )

    def _has_fanout_reader_trigger(self, trigger: str) -> bool:
        return any(
            getattr(stage, "topology", "") == "fanout_reader"
            and getattr(stage, "trigger", "") == trigger
            for stage in getattr(self.config.workflow, "stages", []) or []
        )

    def _has_bridge_output(self, source_event_id: str, event_types: set[str]) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for existing in reversed(events):
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            if (
                existing.type in event_types
                and str(payload.get("source_event_id") or "") == source_event_id
            ):
                return True
        return False

    def _latest_refactor_context(self, base_payload: dict) -> dict:
        pdd_id = str(
            base_payload.get("pdd_id")
            or base_payload.get("feature_id")
            or ""
        ).strip()
        keys = (
            "pdd_id",
            "feature_id",
            "trace_id",
            "task_map_ref",
            "source_index_ref",
            "source_commit",
            "candidate_base_commit",
            "candidate_ref",
            "target_ref",
            "branch",
        )
        out: dict = {}
        try:
            events = self.event_log.read_all()
        except Exception:
            return out
        for existing in reversed(events):
            if existing.type not in {
                "candidate.ready",
                "task_map.ready",
                "task_map.amended",
                "verify.parity_scan.requested",
            }:
                continue
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            event_pdd_id = str(
                payload.get("pdd_id")
                or payload.get("feature_id")
                or ""
            ).strip()
            if pdd_id and event_pdd_id and event_pdd_id != pdd_id:
                continue
            for key in keys:
                if key in out:
                    continue
                value = payload.get(key)
                if value not in (None, ""):
                    out[key] = value
            if all(key in out for key in keys):
                break
        return out

    @staticmethod
    def _first_payload_text(
        primary: dict,
        fallback: dict,
        *keys: str,
    ) -> str:
        for key in keys:
            for source in (primary, fallback):
                value = source.get(key) if isinstance(source, dict) else None
                text = str(value or "").strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _payload_int(payload: dict, *keys: str) -> int | None:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _parity_gap_tasks(payload: dict) -> list[dict]:
        raw_sources: list[object] = [payload]
        for key in ("report", "summary", "gap_plan"):
            value = payload.get(key)
            if isinstance(value, dict):
                raw_sources.append(value)
        tasks: list[dict] = []
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            raw_tasks = source.get("gap_tasks") or source.get("tasks")
            if isinstance(raw_tasks, list):
                tasks.extend(task for task in raw_tasks if isinstance(task, dict))
        return tasks

    @staticmethod
    def _payload_declares_parity_closed(payload: dict) -> bool:
        values = [
            payload.get("parity_status"),
            payload.get("status"),
            payload.get("recommendation"),
            payload.get("result"),
        ]
        normalized = {str(value or "").strip().lower() for value in values}
        return bool(normalized & {
            "closed",
            "passed",
            "approved",
            "no_open_p0_p1_gaps",
            "no-open-p0-p1-gaps",
        })
