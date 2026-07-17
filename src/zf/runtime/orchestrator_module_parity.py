"""Module parity scan bridge helpers for refactor workflows."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin
from zf.runtime.orchestrator_types import OrchestratorDecision


class ModuleParityBridgeMixin(GoalClosureBridgeMixin):
    """Deterministic verify -> parity scan -> gap amend bridge."""

    def _reject_flow_judge_evidence_gap(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Block taskless final judge success when declared flow evidence is absent."""

        from zf.core.workflow.flow_metadata import flow_metadata_for

        metadata = flow_metadata_for(self.config, payload=event.payload)
        quality_floor = str(metadata.get("quality_floor") or "").strip()
        if not quality_floor:
            return None
        required_groups = _quality_floor_required_ref_groups(quality_floor, metadata)
        if not required_groups:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        missing = [
            list(group) for group in required_groups
            if not _payload_has_any_ref(payload, group)
        ]
        if not missing:
            return None
        context = self._latest_refactor_context(payload)
        pdd_id = (
            self._first_payload_text(payload, context, "pdd_id", "feature_id")
            or str(event.task_id or "")
        )
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
        if not self._has_bridge_output(event.id, {"flow.goal.blocked"}):
            self.event_writer.append(ZfEvent(
                type="flow.goal.blocked",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "schema_version": "flow-goal-blocked.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "flow_kind": str(metadata.get("flow_kind") or ""),
                    "quality_floor": quality_floor,
                    "evidence_policy": str(metadata.get("evidence_policy") or ""),
                    "reason": "judge.passed missing required flow evidence refs",
                    "missing_ref_groups": missing,
                    "expected_downstream_events": [
                        "flow.gap_plan.ready",
                        "goal.gap_plan.ready",
                        "flow.goal.closed",
                    ],
                    "task_map_ref": self._first_payload_text(
                        payload,
                        context,
                        "task_map_ref",
                        "base_task_map_ref",
                        "supersedes_task_map_ref",
                    ),
                    "source_event_id": event.id,
                    "source": "flow_judge_evidence_gate",
                },
            ))
        return OrchestratorDecision(
            action="block",
            reason=(
                f"judge.passed missing {quality_floor} evidence refs: "
                f"{missing}"
            ),
        )

    def _bridge_verify_passed_to_flow_discovery(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Emit flow-neutral post-verify discovery for Issue/PRD controllers.

        Refactor keeps the stronger module-parity bridge below. Issue/PRD
        discovery remains semantic work owned by skills/agents; the deterministic
        bridge only records the requested profile and starts a reader fanout when
        the YAML declares one.
        """

        from zf.core.workflow.flow_metadata import flow_metadata_for

        metadata = flow_metadata_for(self.config, payload=event.payload)
        flow_kind = str(metadata.get("flow_kind") or "").strip()
        discovery_profile = str(metadata.get("post_verify_discovery") or "").strip()
        if not flow_kind:
            return None
        if flow_kind == "refactor" or discovery_profile == "module_parity":
            return None
        if not discovery_profile:
            payload = event.payload if isinstance(event.payload, dict) else {}
            context = self._latest_refactor_context(payload)
            pdd_id = (
                self._first_payload_text(payload, context, "pdd_id", "feature_id")
                or str(event.task_id or "")
            )
            feature_id = self._first_payload_text(
                payload, context, "feature_id", "pdd_id",
            ) or pdd_id
            trace_id = (
                self._first_payload_text(payload, context, "trace_id")
                or str(event.correlation_id or event.id)
            )
            closed = self._emit_current_goal_closure(
                event_type="flow.goal.closed",
                source_event=event,
                flow_kind=flow_kind,
                payload={
                    **payload,
                    "schema_version": "flow-goal-closed.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "goal_id": str(payload.get("goal_id") or feature_id or pdd_id),
                    "trace_id": trace_id,
                    "flow_kind": flow_kind,
                    "open_p0_p1_gap_count": 0,
                    "source": "verified_flow_closure_bridge",
                },
            )
            if closed is None:
                return None
            self._maybe_start_reader_fanout(closed)
            return OrchestratorDecision(
                action="bridge",
                reason=f"{event.type} closed {flow_kind} flow without discovery",
            )
        if self._has_bridge_output(event.id, {"flow.discovery.requested"}):
            return None

        payload = event.payload if isinstance(event.payload, dict) else {}
        context = self._latest_refactor_context(payload)
        pdd_id = (
            self._first_payload_text(payload, context, "pdd_id", "feature_id")
            or str(event.task_id or "")
        )
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
        artifact_refs = payload.get("artifact_refs")
        if not isinstance(artifact_refs, list):
            artifact_refs = []
        request_payload = {
            "schema_version": "flow-discovery-request.v1",
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            "trace_id": trace_id,
            "flow_kind": flow_kind,
            "discovery_profile": discovery_profile,
            "quality_floor": str(metadata.get("quality_floor") or ""),
            "evidence_policy": str(metadata.get("evidence_policy") or ""),
            "environment_policy": str(metadata.get("environment_policy") or ""),
            "projection_policy": str(metadata.get("projection_policy") or ""),
            "task_map_ref": self._first_payload_text(
                payload,
                context,
                "task_map_ref",
                "base_task_map_ref",
                "supersedes_task_map_ref",
            ),
            "candidate_ref": candidate_ref,
            "target_ref": candidate_ref,
            "artifact_refs": [str(item) for item in artifact_refs if str(item).strip()],
            "source_event_id": event.id,
            "source": "post_verify_flow_discovery_bridge",
        }
        requested = self.event_writer.append(ZfEvent(
            type="flow.discovery.requested",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=trace_id,
            payload=request_payload,
        ))
        self._maybe_start_reader_fanout(requested)
        return OrchestratorDecision(
            action="bridge",
            reason=(
                f"{event.type} requested {flow_kind} "
                f"{discovery_profile} discovery"
            ),
        )

    def _bridge_verify_passed_to_parity_scan(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Require a module parity scan after candidate-level verify passes."""

        if event.type != "verify.passed":
            return None
        if not self._has_fanout_reader_trigger("verify.parity_scan.requested"):
            return None
        if self._has_bridge_output(
            event.id,
            {"verify.parity_scan.requested", "verify.parity_scan.suppressed"},
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
        active_gap_task_ids = self._active_gap_task_ids(
            pdd_id=pdd_id,
            feature_id=feature_id,
            candidate_ref=candidate_ref,
        )
        if active_gap_task_ids:
            self.event_writer.append(ZfEvent(
                type="verify.parity_scan.suppressed",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "schema_version": "module-parity-scan-suppressed.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "candidate_ref": candidate_ref,
                    "source_event_id": event.id,
                    "active_gap_task_ids": active_gap_task_ids,
                    "reason": "active gap work already owns this candidate scope",
                },
            ))
            return OrchestratorDecision(
                action="suppress",
                reason="active gap work suppresses duplicate module parity scan",
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

    def _active_gap_task_ids(
        self,
        *,
        pdd_id: str,
        feature_id: str,
        candidate_ref: str,
    ) -> list[str]:
        """Return non-terminal gap work already owning this candidate scope."""

        try:
            tasks = self.task_store.list_all()
        except Exception:
            return []
        expected = {value for value in (pdd_id, feature_id, candidate_ref) if value}
        active: list[str] = []
        for task in tasks:
            if str(getattr(task, "status", "")) in {"done", "cancelled"}:
                continue
            contract = getattr(task, "contract", None)
            evidence = getattr(contract, "evidence_contract", {}) if contract else {}
            if not isinstance(evidence, dict):
                continue
            if not str(
                evidence.get("gap_kind")
                or evidence.get("gap_category")
                or ""
            ).strip():
                continue
            identities = {
                str(value).strip()
                for value in (
                    getattr(contract, "feature_id", ""),
                    evidence.get("goal_id"),
                    evidence.get("candidate_ref"),
                    evidence.get("target_ref"),
                )
                if str(value or "").strip()
            }
            if expected and not expected.intersection(identities):
                continue
            active.append(str(getattr(task, "id", "") or ""))
        return sorted(task_id for task_id in active if task_id)

    def _bridge_flow_discovery_completed(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Close a flow goal or convert discovery gaps into canonical gap work."""

        if self._has_bridge_output(
            event.id,
            {"flow.gap_plan.ready", "flow.goal.closed", "flow.goal.blocked"},
        ):
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        feature_id = str(payload.get("feature_id") or pdd_id).strip()
        trace_id = str(payload.get("trace_id") or event.correlation_id or event.id).strip()
        flow_kind = str(payload.get("flow_kind") or payload.get("goal_kind") or "").strip()
        task_map_ref = str(
            payload.get("task_map_ref")
            or payload.get("base_task_map_ref")
            or payload.get("supersedes_task_map_ref")
            or ""
        ).strip()
        ref_payload = {
            key: list(value)
            for key in (
                "artifact_refs",
                "evidence_refs",
                "test_refs",
                "e2e_refs",
                "demo_refs",
                "regression_refs",
                "parity_refs",
                "provider_refs",
            )
            if isinstance((value := payload.get(key)), list) and value
        }
        gap_tasks = self._parity_gap_tasks(payload)
        open_gap_count = self._payload_int(
            payload,
            "open_p0_p1_gap_count",
            "open_gap_count",
            "gap_task_count",
        )
        if gap_tasks:
            gap_event = self.event_writer.append(ZfEvent(
                type="flow.gap_plan.ready",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "schema_version": "goal-gap-plan.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "goal_id": str(payload.get("goal_id") or feature_id or pdd_id),
                    "goal_kind": flow_kind or "flow",
                    "flow_kind": flow_kind,
                    "gap_category": str(
                        payload.get("gap_category")
                        or payload.get("discovery_profile")
                        or "flow_gap"
                    ),
                    "trace_id": trace_id,
                    "task_map_ref": task_map_ref,
                    "gap_plan_ref": str(payload.get("gap_plan_ref") or ""),
                    "gap_tasks": gap_tasks,
                    "gap_task_count": len(gap_tasks),
                    "supersedes_task_ids": self._payload_text_list(
                        payload,
                        "supersedes_task_ids",
                    ),
                    "source_event_id": event.id,
                    "source": "flow_discovery_bridge",
                    **ref_payload,
                },
            ))
            decision = self._bridge_gap_plan_ready_to_task_map(gap_event)
            if decision:
                return decision
            return OrchestratorDecision(
                action="bridge",
                reason=f"flow discovery produced {len(gap_tasks)} gap task(s)",
            )

        if open_gap_count and open_gap_count > 0:
            self.event_writer.append(ZfEvent(
                type="flow.goal.blocked",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "schema_version": "flow-goal-blocked.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "flow_kind": flow_kind,
                    "task_map_ref": task_map_ref,
                    "open_p0_p1_gap_count": open_gap_count,
                    "reason": "flow discovery reported open gaps without gap_tasks",
                    "source_event_id": event.id,
                    "source": "flow_discovery_bridge",
                },
            ))
            return OrchestratorDecision(
                action="block",
                reason="flow discovery missing gap_tasks for open gaps",
            )

        if open_gap_count == 0 or self._payload_declares_parity_closed(payload):
            closed = self._emit_current_goal_closure(
                event_type="flow.goal.closed",
                source_event=event,
                flow_kind=flow_kind or "flow",
                payload={
                    "schema_version": "flow-goal-closed.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "goal_id": str(payload.get("goal_id") or feature_id or pdd_id),
                    "trace_id": trace_id,
                    "flow_kind": flow_kind,
                    "task_map_ref": task_map_ref,
                    "open_p0_p1_gap_count": 0,
                    "source": "flow_discovery_bridge",
                    **ref_payload,
                },
            )
            if closed is None:
                return None
            self._maybe_start_reader_fanout(closed)
            return OrchestratorDecision(
                action="bridge",
                reason="flow discovery closed without open P0/P1 gaps",
            )

        self.event_writer.append(ZfEvent(
            type="flow.goal.blocked",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=trace_id,
            payload={
                "schema_version": "flow-goal-blocked.v1",
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "trace_id": trace_id,
                "flow_kind": flow_kind,
                "task_map_ref": task_map_ref,
                "reason": (
                    "flow discovery completed without explicit closure "
                    "or dispatchable gap_tasks"
                ),
                "source_event_id": event.id,
                "source": "flow_discovery_bridge",
            },
        ))
        return OrchestratorDecision(
            action="block",
            reason="flow discovery lacks closure/gap evidence",
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
                    "supersedes_task_ids": self._payload_text_list(
                        payload,
                        "supersedes_task_ids",
                    ),
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
            closed = self._emit_current_goal_closure(
                event_type="module.parity.closed",
                source_event=event,
                flow_kind="refactor",
                payload={
                    "schema_version": "module-parity-closed.v1",
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "goal_id": str(payload.get("goal_id") or feature_id or pdd_id),
                    "trace_id": trace_id,
                    "flow_kind": "refactor",
                    "task_map_ref": task_map_ref,
                    "candidate_ref": str(payload.get("candidate_ref") or ""),
                    "target_ref": str(
                        payload.get("target_ref")
                        or payload.get("candidate_ref")
                        or ""
                    ),
                    "open_p0_p1_gap_count": 0,
                    "source": "module_parity_scan_bridge",
                },
            )
            if closed is None:
                return None
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
    def _payload_text_list(payload: dict, key: str) -> list[str]:
        value = payload.get(key)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

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
        sources = [payload]
        for key in ("report", "closure", "verdict", "summary"):
            value = payload.get(key)
            if isinstance(value, dict):
                sources.append(value)
        values = []
        for source in sources:
            values.extend([
                source.get("parity_status"),
                source.get("closure_status"),
                source.get("goal_status"),
                source.get("status"),
                source.get("recommendation"),
                source.get("result"),
                source.get("verdict"),
            ])
            for count_key in (
                "open_p0_p1_gap_count",
                "open_gap_count",
                "gap_task_count",
                "blocking_gap_count",
            ):
                if ModuleParityBridgeMixin._payload_int(source, count_key) == 0:
                    values.append("no_open_p0_p1_gaps")
        normalized = {str(value or "").strip().lower() for value in values}
        return bool(normalized & {
            "closed",
            "passed",
            "approved",
            "pass",
            "approve",
            "complete",
            "completed",
            "no_gap",
            "no_gaps",
            "no-open-gaps",
            "no_open_p0_p1_gaps",
            "no-open-p0-p1-gaps",
            "no_blocking_gaps",
            "no-blocking-gaps",
        })


# Builtin floors require floor-specific evidence keys. `artifact_refs` is NOT
# an accepted alternative: aggregated judge.passed payloads almost always carry
# artifact_refs, so accepting it would let any payload with any artifact pass
# every floor.
_QUALITY_FLOOR_REF_GROUPS = {
    "issue-regression": (
        ("repro_ref", "regression_refs", "test_refs"),
    ),
    "product-demo": (
        ("demo_refs", "e2e_refs", "test_refs"),
    ),
    "refactor-parity-real-env": (
        ("parity_refs", "provider_refs", "e2e_refs"),
    ),
}


def _quality_floor_required_ref_groups(
    quality_floor: str,
    metadata: dict,
) -> tuple[tuple[str, ...], ...]:
    """Flow-declared ref groups win over the builtin floor vocabulary.

    ``flow_metadata.quality_floor_ref_groups`` is a list of groups; each group
    is a list of alternative payload keys (any one non-empty satisfies the
    group) or a single key string. "What counts as evidence" stays a
    config/skill decision; the kernel only checks presence.
    """
    declared = metadata.get("quality_floor_ref_groups")
    if isinstance(declared, (list, tuple)):
        groups: list[tuple[str, ...]] = []
        for group in declared:
            if isinstance(group, (list, tuple)):
                keys = tuple(str(key).strip() for key in group if str(key).strip())
                if keys:
                    groups.append(keys)
            elif isinstance(group, str) and group.strip():
                groups.append((group.strip(),))
        return tuple(groups)
    return _QUALITY_FLOOR_REF_GROUPS.get(quality_floor, ())


def _payload_has_any_ref(payload: dict, keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return True
        if isinstance(value, dict) and any(str(item).strip() for item in value.values()):
            return True
    return False
