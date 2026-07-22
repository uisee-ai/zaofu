"""Goal closure fact, source-read, and claim-set bridge ownership."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import (
    candidate_task_source_commits,
    same_task_map_generation,
)

# Compatibility for existing focused tests and downstream diagnostics.
_same_task_map_generation = same_task_map_generation


def _resolve_run_objective_ref(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    payload: dict,
    metadata: dict,
) -> str:
    """Resolve the immutable objective artifact for one workflow run."""

    scoped_payloads = [payload]
    for event in reversed(events):
        body = event.payload if isinstance(event.payload, dict) else {}
        identities = {
            str(body.get("workflow_run_id") or "").strip(),
            str(body.get("run_id") or "").strip(),
            str(body.get("request_id") or "").strip(),
            str(body.get("trace_id") or "").strip(),
            str(event.correlation_id or "").strip(),
        }
        if workflow_run_id in identities:
            scoped_payloads.append(body)

    # A submitted Request pins the requirement revision for the Run. It must
    # outrank profile placeholders and mutable intake projections.
    for body in scoped_payloads:
        ref = str(body.get("requirement_spec_ref") or "").strip()
        if ref:
            return ref
    for body in scoped_payloads:
        for key in ("objective_ref", "workflow_prompt_ref", "workflow_input_manifest_ref"):
            ref = str(body.get(key) or "").strip()
            if ref:
                return ref
    return str(
        metadata.get("objective_ref")
        or metadata.get("prd_ref")
        or metadata.get("issue_ref")
        or ""
    ).strip()


def _resolve_candidate_ref(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    target_commit: str,
    payload: dict,
) -> str:
    explicit = str(
        payload.get("candidate_ref") or payload.get("target_ref") or ""
    ).strip()
    if explicit and explicit != target_commit:
        return explicit
    for event in reversed(events):
        if event.type not in {"candidate.ready", "candidate.integration.completed"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        event_run = str(
            body.get("workflow_run_id")
            or body.get("run_id")
            or body.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        if workflow_run_id and event_run and event_run != workflow_run_id:
            continue
        event_target = str(
            body.get("candidate_head_commit") or body.get("commit") or ""
        ).strip()
        if target_commit and event_target != target_commit:
            continue
        candidate_ref = str(
            body.get("candidate_ref") or body.get("branch") or ""
        ).strip()
        if candidate_ref:
            return candidate_ref
    return explicit or target_commit


class GoalClosureBridgeMixin:
    """Deterministic Goal closure identity and Thin Judge input bridge."""

    def _emit_current_goal_closure(
        self,
        *,
        event_type: str,
        source_event: ZfEvent,
        flow_kind: str,
        payload: dict,
    ) -> ZfEvent | None:
        """Publish one current closure fact per stable generation/target identity."""

        from zf.runtime.goal_closure_identity import (
            build_closure_identity,
            current_closure_event,
            same_closure_identity,
        )

        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        source_payload = (
            source_event.payload if isinstance(source_event.payload, dict) else {}
        )
        identity = build_closure_identity(
            events,
            source_event=source_event,
            payload={**source_payload, **payload},
            state_dir=self.state_dir,
            flow_kind=flow_kind,
        )
        workflow_run_id = str(identity.get("workflow_run_id") or "")
        goal_id = str(identity.get("goal_id") or "")
        if not all((workflow_run_id, goal_id, identity.get("task_map_generation"), identity.get("candidate_head_commit"))):
            self.event_writer.append(ZfEvent(
                type="goal.closure.identity.invalid",
                actor="zf-cli",
                causation_id=source_event.id,
                correlation_id=source_event.correlation_id,
                payload={
                    **identity,
                    "source_event_id": source_event.id,
                    "source_event_type": source_event.type,
                    "reason": "closure identity missing run, goal, generation, or candidate head",
                },
            ))
            return None
        prior = current_closure_event(
            events,
            event_type=event_type,
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
        )
        if same_closure_identity(prior, identity):
            # The closure fact may have been persisted immediately before a
            # process crash. Reuse it so the caller can idempotently retry the
            # fanout start instead of treating the durable fact as completion.
            return prior
        claim_set = self._latest_goal_claim_set(
            events,
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
            task_map_generation=str(identity.get("task_map_generation") or ""),
        )
        if not all((
            claim_set.get("goal_claim_set_ref"),
            claim_set.get("goal_claim_set_digest"),
        )):
            self._emit_goal_closure_identity_invalid(
                source_event,
                identity=identity,
                reason="current task-map generation has no pinned goal claim set",
            )
            return None
        read_contract = self._goal_closure_read_contract(
            events,
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
            identity=identity,
            claim_set=claim_set,
            payload=payload,
        )
        if not list(read_contract.get("input_result_refs") or []):
            self._emit_goal_closure_identity_invalid(
                source_event,
                identity=identity,
                reason="goal closure has no admitted planning/verification results",
            )
            return None
        supersedes = str((prior.payload or {}).get("closure_identity") or "") if prior else ""
        if prior is not None and supersedes:
            self.event_writer.append(ZfEvent(
                type="goal.closure.superseded",
                actor="zf-cli",
                causation_id=source_event.id,
                correlation_id=workflow_run_id,
                payload={
                    "workflow_run_id": workflow_run_id,
                    "goal_id": goal_id,
                    "superseded_closure_identity": supersedes,
                    "superseded_event_id": prior.id,
                    "current_closure_identity": str(identity.get("closure_identity") or ""),
                },
            ))
        return self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            causation_id=source_event.id,
            correlation_id=workflow_run_id,
            payload={
                **payload,
                **identity,
                **claim_set,
                **read_contract,
                "source_event_id": source_event.id,
                "supersedes_closure_identity": supersedes,
            },
        ))

    def _emit_goal_closure_identity_invalid(
        self,
        source_event: ZfEvent,
        *,
        identity: dict,
        reason: str,
    ) -> None:
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        if any(
            event.type == "goal.closure.identity.invalid"
            and event.causation_id == source_event.id
            and isinstance(event.payload, dict)
            and str(event.payload.get("reason") or "") == reason
            for event in events
        ):
            return
        self.event_writer.append(ZfEvent(
            type="goal.closure.identity.invalid",
            actor="zf-cli",
            causation_id=source_event.id,
            correlation_id=str(identity.get("workflow_run_id") or ""),
            payload={
                **identity,
                "source_event_id": source_event.id,
                "source_event_type": source_event.type,
                "reason": reason,
            },
        ))

    def _goal_closure_read_contract(
        self,
        events: list[ZfEvent],
        *,
        workflow_run_id: str,
        goal_id: str,
        identity: dict,
        claim_set: dict,
        payload: dict,
    ) -> dict:
        """Bind Thin Judge inputs to immutable sidecars and required reads."""

        descriptors: list[dict] = []

        def add_descriptor(
            *,
            ref: object,
            digest: object,
            source_id: str,
            kind: str,
        ) -> None:
            ref_text = str(ref or "").strip()
            digest_text = str(digest or "").strip()
            if not ref_text or not digest_text:
                return
            descriptors.append({
                "ref": ref_text,
                "sha256": digest_text,
                "source_id": source_id,
                "artifact_id": source_id,
                "kind": kind,
                "allowed_paths": ["$"],
            })

        add_descriptor(
            ref=identity.get("closure_fact_ref"),
            digest=identity.get("closure_fact_digest"),
            source_id="closure-fact",
            kind="goal_closure_fact",
        )
        add_descriptor(
            ref=claim_set.get("goal_claim_set_ref"),
            digest=claim_set.get("goal_claim_set_digest"),
            source_id="goal-claim-set",
            kind="goal_claim_set",
        )

        candidate_task_commits = candidate_task_source_commits(
            events,
            workflow_run_id=workflow_run_id,
            candidate_head_commit=str(identity.get("candidate_head_commit") or ""),
        )
        admitted_refs: list[str] = []
        admitted_index = 0
        for event in reversed(events):
            if event.type != "workflow.call.result.admitted" or not isinstance(event.payload, dict):
                continue
            body = event.payload
            event_run = str(body.get("workflow_run_id") or event.correlation_id or "")
            if workflow_run_id and event_run and event_run != workflow_run_id:
                continue
            if str(body.get("control_result_schema") or "") == "goal-closure-result.v1":
                continue
            descriptor = body.get("envelope_ref")
            if not isinstance(descriptor, dict):
                continue
            try:
                from zf.runtime.call_result_envelope import (
                    hydrate_call_result_envelope,
                )

                envelope = hydrate_call_result_envelope(self.state_dir, descriptor)
            except Exception:
                continue
            envelope_identity = (
                envelope.get("identity")
                if isinstance(envelope.get("identity"), dict)
                else {}
            )
            result_generation = str(
                envelope_identity.get("task_map_generation") or ""
            )
            result_target = str(envelope_identity.get("target_commit") or "")
            if result_generation and not same_task_map_generation(
                result_generation,
                str(identity.get("task_map_generation") or ""),
            ):
                continue
            if (
                result_target
                and result_target != str(identity.get("candidate_head_commit") or "")
            ):
                result_task_id = str(envelope_identity.get("task_id") or "")
                if candidate_task_commits.get(result_task_id) != result_target:
                    continue
            ref_text = str(descriptor.get("ref") or "").strip()
            digest = str(descriptor.get("sha256") or "").strip()
            if not ref_text or not digest or ref_text in admitted_refs:
                continue
            admitted_index += 1
            admitted_refs.append(ref_text)
            add_descriptor(
                ref=ref_text,
                digest=digest,
                source_id=f"admitted-result-{admitted_index}",
                kind="admitted_call_result",
            )

        planning_result_ref = str(payload.get("task_map_ref") or "").strip()
        candidate_ref = _resolve_candidate_ref(
            events,
            workflow_run_id=workflow_run_id,
            target_commit=str(identity.get("candidate_head_commit") or ""),
            payload=payload,
        )
        for event in reversed(events):
            body = event.payload if isinstance(event.payload, dict) else {}
            if not planning_result_ref and event.type in {"task_map.ready", "task_map.amended"}:
                planning_result_ref = str(body.get("task_map_ref") or "").strip()
            if planning_result_ref and candidate_ref:
                break
        from zf.core.workflow.flow_metadata import flow_metadata_for

        metadata = flow_metadata_for(self.config, payload=payload)
        objective_ref = _resolve_run_objective_ref(
            events,
            workflow_run_id=workflow_run_id,
            payload=payload,
            metadata=metadata,
        ) or goal_id
        from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref

        for source_id, ref, kind in (
            ("objective", objective_ref, "goal_objective"),
            ("planning-result", planning_result_ref, "accepted_planning_result"),
        ):
            source = materialize_attempt_source_ref(
                state_dir=self.state_dir,
                project_root=self.project_root,
                ref=ref,
                source_id=source_id,
                kind=kind,
            )
            if source:
                descriptors.append(source)
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar

        contract_snapshot = {
            "schema_version": "goal-closure-contract-snapshot.v1",
            "workflow_run_id": workflow_run_id,
            "goal_id": goal_id,
            "flow_kind": str(payload.get("flow_kind") or metadata.get("flow_kind") or ""),
            "task_map_generation": str(identity.get("task_map_generation") or ""),
            "objective_ref": objective_ref,
            "planning_result_ref": planning_result_ref,
            "goal_claim_set_ref": str(claim_set.get("goal_claim_set_ref") or ""),
            "goal_claim_set_digest": str(claim_set.get("goal_claim_set_digest") or ""),
            "delivery_policy": str(metadata.get("delivery_policy") or "report_only"),
        }
        contract_descriptor = write_immutable_json_sidecar(
            self.state_dir,
            contract_snapshot,
            root="goal-closure/contract-snapshots",
            kind="goal_closure_contract_snapshot",
            schema_version="goal-closure-contract-snapshot.v1",
            created_by="goal-closure-identity",
        )
        target_snapshot = {
            "schema_version": "goal-closure-target-snapshot.v1",
            "workflow_run_id": workflow_run_id,
            "goal_id": goal_id,
            "task_map_generation": str(identity.get("task_map_generation") or ""),
            "candidate_ref": candidate_ref,
            "target_commit": str(identity.get("candidate_head_commit") or ""),
            "closure_identity": str(identity.get("closure_identity") or ""),
        }
        target_descriptor = write_immutable_json_sidecar(
            self.state_dir,
            target_snapshot,
            root="goal-closure/target-snapshots",
            kind="goal_closure_target_snapshot",
            schema_version="goal-closure-target-snapshot.v1",
            created_by="goal-closure-identity",
        )
        add_descriptor(
            ref=contract_descriptor.get("ref"),
            digest=contract_descriptor.get("sha256"),
            source_id="goal-closure-contract",
            kind="goal_closure_contract_snapshot",
        )
        add_descriptor(
            ref=target_descriptor.get("ref"),
            digest=target_descriptor.get("sha256"),
            source_id="goal-closure-target",
            kind="goal_closure_target_snapshot",
        )
        required_reads = [
            {
                "source_id": str(item.get("source_id") or ""),
                "artifact_id": str(item.get("artifact_id") or ""),
                "artifact_sha256": str(item.get("sha256") or ""),
                "json_path": "$",
                "min_returned_bytes": 1,
            }
            for item in descriptors
        ]
        return {
            "objective_ref": objective_ref,
            "planning_result_ref": planning_result_ref,
            "candidate_ref": candidate_ref,
            "target_commit": str(identity.get("candidate_head_commit") or ""),
            "contract_snapshot_ref": str(contract_descriptor.get("ref") or ""),
            "contract_snapshot_digest": str(contract_descriptor.get("sha256") or ""),
            "target_snapshot_ref": str(target_descriptor.get("ref") or ""),
            "target_snapshot_digest": str(target_descriptor.get("sha256") or ""),
            "input_result_refs": admitted_refs,
            "input_refs": descriptors,
            "required_reads": required_reads,
        }

    @staticmethod
    def _latest_goal_claim_set(
        events: list[ZfEvent],
        *,
        workflow_run_id: str,
        goal_id: str,
        task_map_generation: str,
    ) -> dict:
        for event in reversed(events):
            if event.type != "goal.claim_set.pinned" or not isinstance(event.payload, dict):
                continue
            body = event.payload
            if str(body.get("workflow_run_id") or "") != workflow_run_id:
                continue
            if str(body.get("goal_id") or "") != goal_id:
                continue
            generation = str(body.get("task_map_generation") or "")
            if generation and generation != task_map_generation:
                continue
            return {
                "goal_claim_set_ref": str(body.get("goal_claim_set_ref") or ""),
                "goal_claim_set_digest": str(body.get("goal_claim_set_digest") or ""),
            }
        return {}

    def _pin_goal_claim_set(self, event: ZfEvent) -> bool:
        """Project accepted task-map semantics into one immutable claim set."""

        if event.type != "task_map.ready":
            return True
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_map_ref = str(payload.get("task_map_ref") or "").strip()
        if not task_map_ref:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        if any(
            existing.type == "goal.claim_set.pin.failed"
            and isinstance(existing.payload, dict)
            and str(existing.payload.get("source_event_id") or "") == event.id
            and str(existing.payload.get("task_map_ref") or "") == task_map_ref
            for existing in events
        ):
            return False
        if any(
            existing.type == "goal.claim_set.pinned"
            and isinstance(existing.payload, dict)
            and str(existing.payload.get("source_event_id") or "") == event.id
            for existing in events
        ):
            return True
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        if not workflow_run_id:
            try:
                from zf.runtime.run_scope import resolve_run_for_event

                workflow_run_id = resolve_run_for_event(events, event)
            except Exception:
                workflow_run_id = ""
        goal_id = str(
            payload.get("goal_id")
            or payload.get("feature_id")
            or payload.get("pdd_id")
            or ""
        ).strip()
        from zf.runtime.goal_claim_set import canonical_task_map_generation

        generation = canonical_task_map_generation(
            task_map_generation=payload.get("task_map_generation"),
            task_map_digest=payload.get("task_map_digest"),
            task_map_ref=task_map_ref,
        )
        from zf.core.workflow.flow_metadata import flow_metadata_for

        metadata = flow_metadata_for(self.config, payload=payload)
        try:
            from zf.runtime.goal_claim_set import pin_goal_claim_set_from_task_map

            claim_set, descriptor = pin_goal_claim_set_from_task_map(
                state_dir=self.state_dir,
                project_root=self.project_root,
                task_map_ref=task_map_ref,
                workflow_run_id=workflow_run_id,
                goal_id=goal_id,
                task_map_generation=generation,
                objective_ref=_resolve_run_objective_ref(
                    events,
                    workflow_run_id=workflow_run_id,
                    payload=payload,
                    metadata=metadata,
                ),
                source_event_id=event.id,
            )
        except Exception as exc:
            self.event_writer.append(ZfEvent(
                type="goal.claim_set.pin.failed",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=workflow_run_id,
                payload={
                    "workflow_run_id": workflow_run_id,
                    "goal_id": goal_id,
                    "task_map_ref": task_map_ref,
                    "source_event_id": event.id,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            ))
            return False
        self.event_writer.append(ZfEvent(
            type="goal.claim_set.pinned",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=workflow_run_id,
            payload={
                "workflow_run_id": workflow_run_id,
                "goal_id": goal_id,
                "task_map_generation": generation,
                "task_map_ref": task_map_ref,
                "goal_claim_set_ref": str(descriptor.get("ref") or ""),
                "goal_claim_set_digest": str(descriptor.get("sha256") or ""),
                "goal_claim_set_content_digest": str(
                    claim_set.get("claim_set_digest") or ""
                ),
                "claim_count": len(claim_set.get("claims") or []),
                "source_event_id": event.id,
            },
        ))
        return True



__all__ = ["GoalClosureBridgeMixin"]
