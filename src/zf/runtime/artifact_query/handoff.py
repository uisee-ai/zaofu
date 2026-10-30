"""Canonical-first resolver used by real provider dispatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.task.store import TaskStore
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.artifact_read_policy import reader_stage_input_refs
from zf.runtime.artifact_read_ledger import (
    ArtifactReadError,
    source_manifest_from_payload,
)
from zf.runtime.candidate_result_binding import candidate_task_source_commits
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    admitted_plan_artifact_package_for_generation,
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.artifact_package_policy import effective_artifact_package_mode
from zf.runtime.run_scope import events_for_run
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    current_task_contract_identity,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
)

from zf.runtime.artifact_query.authority_contract import (
    HANDOFF_AUTHORITY_SCHEMA,
    build_handoff_authority_contract,
)

class CanonicalHandoffResolver:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig | None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.config = config
        self.query = ArtifactQueryService(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=config,
        )

    def resolve_payload(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
        attempt_id: str,
        dispatch_id: str,
        source_event_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        mutable = dict(payload)
        currentness = self._validate_currentness(
            payload=mutable,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
        )
        profile = str(mutable.get("output_profile_id") or "")
        discovery = (
            profile == "workflow-read"
            and mutable.get("canonical_success_event") == "flow.discovery.child.completed"
        )
        authority_contract = self._authority_contract(
            mutable,
            profile=profile,
        )
        candidate_snapshot = (
            self._validate_candidate_authority(
                payload=mutable,
                workflow_run_id=workflow_run_id,
                currentness=currentness,
            )
            if profile == "candidate-verify" or authority_contract.get("requires_candidate_lineage")
            else {}
        )
        freeze_ref = str(candidate_snapshot.get("freeze_receipt_ref") or "")
        freeze_digest = str(
            candidate_snapshot.get("freeze_receipt_digest") or ""
        )
        if freeze_ref and freeze_digest:
            artifact_refs = (
                list(mutable.get("artifact_refs") or [])
                if isinstance(mutable.get("artifact_refs"), list)
                else []
            )
            mutable["artifact_refs"] = [
                *artifact_refs,
                {
                    "source_id": "candidate-freeze",
                    "artifact_id": Path(freeze_ref).name,
                    "kind": "candidate_freeze_receipt",
                    "ref": freeze_ref,
                    "sha256": freeze_digest,
                    "allowed_paths": ["$"],
                },
            ]
        plan_port_sources = (
            self._current_plan_port_sources(
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                currentness=currentness,
                allow_without_task=profile == "candidate-verify" or discovery,
                # Discovery evaluates the admitted generation, whose parent
                # is not an implementation partition in the task map.
                candidate=profile == "candidate-verify" or discovery,
                profile=profile,
            )
            if profile in {"implementation", "task-verify", "candidate-verify"} or discovery
            else []
        )
        if plan_port_sources:
            artifact_refs = (
                list(mutable.get("artifact_refs") or [])
                if isinstance(mutable.get("artifact_refs"), list)
                else []
            )
            mutable["artifact_refs"] = [*artifact_refs, *plan_port_sources]
        from zf.runtime.orchestrator_agent_run_plan import (
            admitted_context_route_sources,
        )

        route_sources = admitted_context_route_sources(
            state_dir=self.state_dir,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            stage_id=str(authority_contract.get("stage_id") or ""),
            task_map_generation=str(
                currentness.get("task_map_generation") or ""
            ),
            plan_artifact_package_ref=str(
                currentness.get("plan_artifact_package_ref") or ""
            ),
            plan_artifact_package_digest=str(
                currentness.get("plan_artifact_package_digest") or ""
            ),
        )
        if route_sources:
            artifact_refs = (
                list(mutable.get("artifact_refs") or [])
                if isinstance(mutable.get("artifact_refs"), list)
                else []
            )
            mutable["artifact_refs"] = [*artifact_refs, *route_sources]
        reader_inputs = reader_stage_input_refs(
            mutable,
            stage_id=str(authority_contract.get("stage_id") or ""),
        )
        if reader_inputs:
            input_refs = (
                list(mutable.get("input_refs") or [])
                if isinstance(mutable.get("input_refs"), list)
                else []
            )
            mutable["input_refs"] = [*input_refs, *reader_inputs]
        if str(mutable.get("attempt_domain") or "") == "plan":
            from zf.runtime.plan_synth_handoff import (
                build_plan_handoff_input_refs,
            )

            plan_inputs = build_plan_handoff_input_refs(
                state_dir=self.state_dir,
                project_root=self.project_root,
                payload=mutable,
                source_event_id=source_event_id,
            )
            if plan_inputs:
                input_refs = (
                    list(mutable.get("input_refs") or [])
                    if isinstance(mutable.get("input_refs"), list)
                    else []
                )
                mutable["input_refs"] = [*input_refs, *plan_inputs]
        metadata = {
            **currentness,
            "feedback_revision": str(
                mutable.get("feedback_revision")
                or mutable.get("rework_feedback_digest")
                or ""
            ),
            "source_snapshot": self._stable_source_snapshot(
                payload=mutable,
                currentness=currentness,
            ),
            "resolver": {
                "schema_version": "canonical-handoff-resolver.v1",
                "selection": "canonical-authority-matrix",
            },
            "source_event_id": source_event_id,
            "read_purpose": profile,
            "handoff_authority_contract": authority_contract,
            "candidate_snapshot": candidate_snapshot,
            "orchestrator_context_route_source_ids": [
                str(source.get("source_id") or "")
                for source in route_sources
            ],
        }
        manifest, descriptor = source_manifest_from_payload(
            state_dir=self.state_dir,
            project_root=self.project_root,
            payload=mutable,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            dispatch_id=dispatch_id,
            source_event_id=source_event_id,
            manifest_metadata=metadata,
        )
        if plan_port_sources:
            expected_sources = {
                str(source.get("source_id") or "")
                for source in plan_port_sources
            }
            materialized_sources = {
                str(source.get("source_id") or "")
                for source in manifest.get("sources", [])
                if isinstance(source, Mapping)
            }
            missing_sources = sorted(expected_sources - materialized_sources)
            if missing_sources:
                raise ArtifactReadError(
                    "required Plan Artifact Package sources could not be "
                    "materialized: " + ", ".join(missing_sources)
                )
        if route_sources:
            expected_sources = {
                str(source.get("source_id") or "")
                for source in route_sources
            }
            materialized_sources = {
                str(source.get("source_id") or "")
                for source in manifest.get("sources", [])
                if isinstance(source, Mapping)
            }
            missing_sources = sorted(expected_sources - materialized_sources)
            if missing_sources:
                raise ArtifactReadError(
                    "admitted OA context-route sources could not be materialized: "
                    + ", ".join(missing_sources)
                )
        return manifest, descriptor

    def _validate_currentness(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        result = {
            "contract_revision": str(payload.get("contract_revision") or ""),
            "task_map_generation": str(payload.get("task_map_generation") or ""),
            "base_commit": str(payload.get("base_commit") or ""),
            "target_commit": str(payload.get("target_commit") or ""),
            "task_ref": str(payload.get("task_ref") or ""),
            "plan_artifact_package_id": str(
                payload.get("plan_artifact_package_id")
                or payload.get("package_id")
                or ""
            ),
            "plan_artifact_package_ref": str(
                payload.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                payload.get("plan_artifact_package_digest") or ""
            ),
        }
        profile = str(payload.get("output_profile_id") or "")
        task_bound_profile = profile in {"implementation", "task-verify"}
        candidate_bound_profile = profile == "candidate-verify"
        authority_bound_profile = task_bound_profile or candidate_bound_profile
        authority_profile = self._authority_profile(payload, profile=profile)
        result["handoff_authority_profile"] = authority_profile
        blocking = self._blocking_handoff(payload)
        authority_contract = self._authority_contract(payload, profile=profile)
        if blocking and authority_bound_profile and not authority_contract:
            raise ArtifactReadError(
                f"{authority_profile} handoff requires an authority contract"
            )
        if (
            blocking
            and authority_profile == "implementation-initial"
            and not result["base_commit"]
        ):
            raise ArtifactReadError(
                "implementation-initial handoff requires a pinned base_commit"
            )
        task = TaskStore(self.state_dir / "kanban.json").get(task_id) if task_id else None
        if blocking and task_bound_profile and task is None:
            raise ArtifactReadError(
                f"{authority_profile} handoff requires a current canonical task"
            )
        if task is not None and task_bound_profile:
            try:
                current = current_task_contract_identity(task)
            except TaskContractSnapshotError as exc:
                raise ArtifactReadError(
                    f"cannot prove current task contract identity: {exc}"
                ) from exc
            self._require_match(
                "contract_revision",
                result["contract_revision"],
                str(current.get("contract_revision") or ""),
            )
            self._require_match(
                "task_map_generation",
                result["task_map_generation"],
                str(current.get("task_map_generation") or ""),
            )
            result["contract_revision"] = str(
                current.get("contract_revision")
                or result["contract_revision"]
            )
            result["task_map_generation"] = str(
                current.get("task_map_generation")
                or result["task_map_generation"]
            )

        task_ref = self._task_ref_entry(task_id)
        expected_ref = str(task_ref.get("task_ref") or "")
        accepted_commit = str(task_ref.get("source_commit") or "")
        requires_prior_task_ref = bool(
            authority_contract.get("requires_prior_task_ref")
        )
        if (
            authority_profile == "implementation-rework"
            and requires_prior_task_ref
            and not str(authority_contract.get("continuation_key") or "")
        ):
            raise ArtifactReadError(
                "implementation-rework continuation requires continuation_key"
            )
        if task is not None and (
            authority_profile == "task-verify"
            or (
                authority_profile == "implementation-rework"
                and requires_prior_task_ref
            )
        ):
            if not expected_ref or not accepted_commit:
                raise ArtifactReadError(
                    f"{authority_profile} handoff requires an accepted "
                    "TaskRef with source_commit"
                )
            self._require_match("task_ref", result["task_ref"], expected_ref)
            result["task_ref"] = expected_ref
        elif expected_ref and result["task_ref"] == expected_ref:
            result["task_ref"] = expected_ref
        if (
            task is not None
            and authority_profile == "task-verify"
            and not authority_contract.get("requires_candidate_lineage")
        ):
            self._require_match(
                "target_commit",
                result["target_commit"],
                accepted_commit,
            )
            result["target_commit"] = accepted_commit

        package_id = result["plan_artifact_package_id"]
        package_authority_required = candidate_bound_profile or (
            task is not None and task_bound_profile
        )
        if workflow_run_id and (package_id or package_authority_required):
            events = self.query._events()
            reduced = reduce_plan_artifact_packages(
                events,
                workflow_run_id=workflow_run_id,
            )
            generation_bound_package = (
                task_bound_profile or candidate_bound_profile
            )
            current_package = (
                admitted_plan_artifact_package_for_generation(
                    reduced,
                    task_map_generation=result["task_map_generation"],
                )
                if generation_bound_package
                else reduced.get("current")
            )
            current_package = (
                current_package if isinstance(current_package, Mapping) else {}
            )
            expected_package = str(
                current_package.get("plan_artifact_package_id")
                or current_package.get("package_id")
                or ""
            )
            expected_ref = str(current_package.get("package_ref") or "")
            expected_digest = str(current_package.get("package_digest") or "")
            if blocking and authority_bound_profile and not all(
                (expected_package, expected_ref, expected_digest)
            ):
                raise ArtifactReadError(
                    f"{authority_profile} handoff requires a current "
                    "Plan Artifact Package"
                )
            for field, incoming, current in (
                ("plan_artifact_package_id", package_id, expected_package),
                (
                    "plan_artifact_package_ref",
                    result["plan_artifact_package_ref"],
                    expected_ref,
                ),
                (
                    "plan_artifact_package_digest",
                    result["plan_artifact_package_digest"],
                    expected_digest,
                ),
            ):
                if incoming and not current:
                    raise ArtifactReadError(
                        f"cannot prove current {field}: incoming {incoming!r}"
                    )
                if current or incoming:
                    if candidate_bound_profile and not incoming:
                        continue
                    self._require_match(field, incoming, current)
            result["plan_artifact_package_id"] = expected_package or package_id
            result["plan_artifact_package_ref"] = (
                expected_ref or result["plan_artifact_package_ref"]
            )
            result["plan_artifact_package_digest"] = (
                expected_digest or result["plan_artifact_package_digest"]
            )
        if task is not None and task_bound_profile:
            self._validate_task_snapshots(
                payload=payload,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                profile=profile,
                currentness=result,
            )
        elif candidate_bound_profile and blocking:
            self._validate_task_snapshots(
                payload=payload,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                profile=profile,
                currentness=result,
            )
        return result

    def _authority_contract(
        self,
        payload: Mapping[str, Any],
        *,
        profile: str,
    ) -> dict[str, Any]:
        value = payload.get("handoff_authority_contract")
        if not isinstance(value, Mapping):
            return {}
        contract = dict(value)
        if str(contract.get("schema_version") or "") != HANDOFF_AUTHORITY_SCHEMA:
            raise ArtifactReadError("unsupported handoff authority contract")
        if str(contract.get("output_profile_id") or "") != profile:
            raise ArtifactReadError(
                "handoff authority output_profile_id mismatch"
            )
        if not isinstance(contract.get("requires_prior_task_ref"), bool):
            raise ArtifactReadError(
                "handoff authority requires_prior_task_ref must be boolean"
            )
        if not isinstance(contract.get("requires_candidate_lineage"), bool):
            raise ArtifactReadError(
                "handoff authority requires_candidate_lineage must be boolean"
            )
        return contract

    def _blocking_handoff(self, payload: Mapping[str, Any]) -> bool:
        metadata = flow_metadata_for(self.config, payload=payload)
        return effective_artifact_package_mode(
            state_dir=self.state_dir,
            payload=payload,
            metadata=metadata,
        ) == "blocking"

    @staticmethod
    def _authority_profile(
        payload: Mapping[str, Any],
        *,
        profile: str,
    ) -> str:
        if profile == "task-verify":
            return "task-verify"
        if profile == "candidate-verify":
            return "candidate-verify"
        if profile != "implementation":
            return profile or "non-task"
        attempt_domain = str(payload.get("attempt_domain") or "").lower()
        is_rework = any(
            (
                str(payload.get("rework_feedback_ref") or "").strip(),
                str(payload.get("rework_of_attempt_id") or "").strip(),
                str(payload.get("parent_attempt_id") or "").strip(),
            )
        ) or "rework" in attempt_domain
        return "implementation-rework" if is_rework else "implementation-initial"

    def _validate_candidate_authority(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        currentness: dict[str, Any],
    ) -> dict[str, Any]:
        contract = self._authority_contract(
            payload,
            profile=str(payload.get("output_profile_id") or "candidate-verify"),
        )
        if self._blocking_handoff(payload) and not contract.get(
            "requires_candidate_lineage"
        ):
            raise ArtifactReadError(
                "candidate-verify authority contract requires candidate lineage"
            )
        all_events = self.query._events()
        scoped = events_for_run(all_events, run_id=workflow_run_id)
        candidates = [
            event
            for event in scoped
            if event.type == "candidate.ready"
            and isinstance(event.payload, Mapping)
        ]
        if not candidates:
            if self._blocking_handoff(payload):
                raise ArtifactReadError(
                    "candidate-verify handoff requires current candidate.ready"
                )
            return {}
        incoming_event_id = str(
            payload.get("candidate_snapshot_event_id")
            or payload.get("candidate_event_id")
            or ""
        ).strip()
        if incoming_event_id:
            event = next(
                (
                    candidate
                    for candidate in reversed(candidates)
                    if candidate.id == incoming_event_id
                ),
                None,
            )
            if event is None:
                raise ArtifactReadError(
                    "candidate-verify candidate event is missing"
                )
        else:
            event = candidates[-1]
        body = dict(event.payload)
        candidate_head = str(body.get("candidate_head_commit") or "").strip()
        completed = [
            str(item).strip()
            for item in body.get("completed_task_ids") or []
            if str(item).strip()
        ]
        if not candidate_head or not completed:
            raise ArtifactReadError(
                "candidate-verify current candidate lacks head/tasks"
            )
        if (
            payload.get("output_profile_id") == "task-verify"
            and str(payload.get("task_id") or "") not in completed
        ):
            raise ArtifactReadError("Task Verify is not included in the current candidate")
        incoming_target = str(currentness.get("target_commit") or "")
        if incoming_target != candidate_head:
            raise ArtifactReadError(
                "candidate-verify target_commit does not match current candidate"
            )
        candidate_generation = str(
            body.get("task_map_generation") or ""
        ).strip()
        current_generation = str(
            currentness.get("task_map_generation") or ""
        ).strip()
        if (
            candidate_generation
            and current_generation
            and candidate_generation != current_generation
        ):
            raise ArtifactReadError(
                "candidate-verify task_map_generation is stale"
            )
        freeze_ref = body.get("freeze_receipt_ref")
        freeze_digest = str(body.get("freeze_receipt_digest") or "").strip()
        frozen_candidate = (
            str(body.get("schema_version") or "").strip()
            == "candidate-freeze-receipt.v1"
        )
        freeze_receipt: dict[str, Any] = {}
        freeze_descriptor: dict[str, Any] = {}
        if frozen_candidate:
            if not isinstance(freeze_ref, Mapping) or not freeze_digest:
                raise ArtifactReadError(
                    "candidate-verify frozen candidate requires freeze receipt"
                )
            freeze_descriptor = dict(freeze_ref)
            if str(freeze_descriptor.get("sha256") or "") != freeze_digest:
                raise ArtifactReadError(
                    "candidate-verify freeze receipt digest mismatch"
                )
            try:
                hydrated = hydrate_sidecar_ref(self.state_dir, freeze_descriptor)
            except SidecarRefError as exc:
                raise ArtifactReadError(
                    f"candidate-verify freeze receipt cannot be hydrated: {exc}"
                ) from exc
            if not isinstance(hydrated.payload, Mapping):
                raise ArtifactReadError(
                    "candidate-verify freeze receipt must be an object"
                )
            freeze_receipt = dict(hydrated.payload)
            expected_fields = {
                "schema_version": "candidate-freeze-receipt.v1",
                "freeze_id": str(body.get("freeze_id") or ""),
                "workflow_run_id": workflow_run_id,
                "task_map_generation": candidate_generation,
                "candidate_generation": str(
                    body.get("candidate_generation") or ""
                ),
                "candidate_base_commit": str(
                    body.get("candidate_base_commit") or ""
                ),
                "candidate_head_commit": candidate_head,
                "integration_ledger_digest": str(
                    body.get("integration_ledger_digest") or ""
                ),
                "status": "frozen",
            }
            for field, expected in expected_fields.items():
                actual = str(
                    freeze_receipt.get(field)
                    or (
                        freeze_receipt.get("candidate_head")
                        if field == "candidate_head_commit"
                        else ""
                    )
                    or ""
                )
                if actual != expected:
                    raise ArtifactReadError(
                        f"candidate-verify freeze receipt {field} mismatch"
                    )
            frozen_tasks = sorted(
                str(item).strip()
                for item in freeze_receipt.get("completed_task_ids") or []
                if str(item).strip()
            )
            if frozen_tasks != sorted(completed):
                raise ArtifactReadError(
                    "candidate-verify freeze receipt task set mismatch"
                )
        integrated = candidate_task_source_commits(
            all_events,
            workflow_run_id=workflow_run_id,
            candidate_head_commit=candidate_head,
        )
        missing = sorted(set(completed) - set(integrated))
        if missing:
            raise ArtifactReadError(
                "candidate-verify missing integrated TaskRefs: "
                + ", ".join(missing)
            )
        snapshot = {
            "schema_version": "candidate-authority-snapshot.v1",
            "candidate_event_id": event.id,
            "candidate_head_commit": candidate_head,
            "candidate_base_commit": str(
                body.get("candidate_base_commit") or ""
            ),
            "completed_task_ids": completed,
            "integrated_task_refs": [
                {
                    "task_id": task,
                    "source_commit": integrated[task],
                }
                for task in sorted(integrated)
                if task in set(completed)
            ],
        }
        if freeze_descriptor:
            snapshot.update({
                "freeze_receipt_ref": str(
                    freeze_descriptor.get("ref") or ""
                ),
                "freeze_receipt_digest": freeze_digest,
            })
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot["identity_digest"] = hashlib.sha256(encoded).hexdigest()
        currentness.update({
            "candidate_event_id": event.id,
            "candidate_head_commit": candidate_head,
            "candidate_snapshot_digest": snapshot["identity_digest"],
        })
        return snapshot

    def _current_plan_port_sources(
        self, *, workflow_run_id: str, task_id: str,
        currentness: Mapping[str, Any], allow_without_task: bool = False,
        candidate: bool = False, profile: str = "",
    ) -> list[dict[str, Any]]:
        from zf.runtime.task_scoped_plan_inputs import current_plan_port_sources

        return current_plan_port_sources(
            state_dir=self.state_dir, workflow_run_id=workflow_run_id,
            task_id=task_id, currentness=currentness,
            allow_without_task=allow_without_task, candidate=candidate, profile=profile,
        )

    def _validate_task_snapshots(
        self,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str,
        task_id: str,
        profile: str,
        currentness: Mapping[str, Any],
    ) -> None:
        contract_ref = str(payload.get("contract_snapshot_ref") or "")
        contract_digest = str(payload.get("contract_snapshot_digest") or "")
        if not contract_ref or not contract_digest:
            raise ArtifactReadError(
                "task-bound handoff requires contract snapshot ref/digest"
            )
        expected = {
            "workflow_run_id": workflow_run_id,
            "task_id": task_id,
            **{
                key: value
                for key, value in currentness.items()
                if key in {
                    "contract_revision",
                    "task_map_generation",
                    "base_commit",
                    "task_ref",
                    "plan_artifact_package_id",
                    "plan_artifact_package_ref",
                    "plan_artifact_package_digest",
                }
                and value
            },
        }
        try:
            contract = hydrate_task_contract_snapshot(
                self.state_dir,
                {
                    "ref": contract_ref,
                    "sha256": contract_digest,
                },
                expected=expected,
            )
        except TaskContractSnapshotError as exc:
            raise ArtifactReadError(
                f"invalid current contract snapshot: {exc}"
            ) from exc

        if profile not in {"task-verify", "candidate-verify"}:
            return
        target_ref = str(payload.get("target_snapshot_ref") or "")
        target_digest = str(payload.get("target_snapshot_digest") or "")
        target_commit = str(payload.get("target_commit") or "")
        if not target_ref or not target_digest or not target_commit:
            raise ArtifactReadError(
                "verify handoff requires target snapshot ref/digest/commit"
            )
        try:
            hydrate_target_snapshot(
                self.state_dir,
                {
                    "ref": target_ref,
                    "sha256": target_digest,
                },
                expected={
                    **{
                        key: contract.get(key)
                        for key in (
                            "workflow_run_id",
                            "task_id",
                            "contract_revision",
                            "task_map_generation",
                            "base_commit",
                            "task_ref",
                            "plan_artifact_package_id",
                            "plan_artifact_package_ref",
                            "plan_artifact_package_digest",
                        )
                    },
                    "contract_snapshot_ref": contract_ref,
                    "contract_snapshot_digest": contract_digest,
                    "target_commit": target_commit,
                },
            )
        except TaskContractSnapshotError as exc:
            raise ArtifactReadError(
                f"invalid current target snapshot: {exc}"
            ) from exc

    @staticmethod
    def _stable_source_snapshot(
        *,
        payload: Mapping[str, Any],
        currentness: Mapping[str, str],
    ) -> dict[str, Any]:
        source_rows: list[tuple[str, str, str]] = []
        for field in ("artifact_refs", "input_refs"):
            values = payload.get(field)
            for item in values if isinstance(values, list) else []:
                if not isinstance(item, Mapping):
                    continue
                source_rows.append((
                    str(item.get("kind") or item.get("source_id") or ""),
                    str(item.get("ref") or item.get("path") or ""),
                    str(item.get("sha256") or ""),
                ))
        for prefix in (
            "contract_snapshot",
            "target_snapshot",
            "impl_self_check",
            "rework_feedback",
            "parent_call_result",
        ):
            ref = str(payload.get(f"{prefix}_ref") or "")
            digest = str(payload.get(f"{prefix}_digest") or "")
            if ref or digest:
                source_rows.append((prefix, ref, digest))
        body = json.dumps(
            {
                "currentness": dict(currentness),
                "sources": sorted(source_rows),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "schema_version": "handoff-source-snapshot.v1",
            "identity_digest": hashlib.sha256(body).hexdigest(),
        }

    def _task_ref_entry(self, task_id: str) -> dict[str, Any]:
        if not task_id:
            return {}
        path = self.state_dir / "refs" / "task-index.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        value = payload.get(task_id)
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _require_match(field: str, incoming: str, current: str) -> None:
        if current and incoming != current:
            raise ArtifactReadError(
                f"stale or missing {field}: incoming {incoming!r}, "
                f"current {current!r}"
            )


__all__ = [
    "HANDOFF_AUTHORITY_SCHEMA",
    "CanonicalHandoffResolver",
    "build_handoff_authority_contract",
]
