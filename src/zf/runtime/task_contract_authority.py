"""Single mutation boundary for canonical Task contracts and execution binding."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import (
    Task,
    TaskContract,
    TaskExecutionBinding,
    task_contract_from_mapping,
)
from zf.core.task.store import TaskStore
from zf.core.task.authority import (
    CONTRACT_METADATA_FIELDS,
    EXECUTION_BINDING_EVIDENCE_FIELDS,
    authority_revision_for,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


MUTATION_SCHEMA_VERSION = "task-contract-mutation.v1"


class TaskContractAuthorityError(RuntimeError):
    """A canonical contract mutation could not be admitted."""


class TaskContractAuthorityConflict(TaskContractAuthorityError):
    """The caller prepared a mutation from a stale Task authority revision."""


@dataclass(frozen=True)
class TaskContractMutation:
    task: Task
    previous_revision: str
    authority_revision: str
    authority_sequence: int
    receipt_ref: str = ""
    receipt_digest: str = ""
    changed: bool = True


def task_execution_binding(task: Task) -> TaskExecutionBinding:
    """Read first-class binding, with one-way compatibility for old Tasks."""

    binding = getattr(task, "execution_binding", None)
    if isinstance(binding, TaskExecutionBinding) and any(
        (
            binding.owner,
            binding.request_id,
            binding.request_revision,
            binding.workflow_run_id,
            binding.origin_binding_digest,
            binding.origin_task_digest,
        )
    ):
        return binding
    evidence = getattr(getattr(task, "contract", None), "evidence_contract", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    try:
        request_revision = int(evidence.get("workflow_request_revision") or 0)
    except (TypeError, ValueError):
        request_revision = 0
    return TaskExecutionBinding(
        owner=str(evidence.get("execution_owner") or "").strip(),
        request_id=str(evidence.get("workflow_request_id") or "").strip(),
        request_revision=request_revision,
        workflow_run_id=str(evidence.get("workflow_run_id") or "").strip(),
        origin_binding_digest=str(
            evidence.get("workflow_origin_binding_digest") or ""
        ).strip(),
        origin_task_digest=str(
            evidence.get("workflow_origin_task_digest") or ""
        ).strip(),
    )


def current_authority_revision(task: Task) -> str:
    stored = str(getattr(task, "contract_authority_revision", "") or "")
    if stored:
        return stored
    return ""


def allowed_task_contract_change_actors(config: Any) -> set[str]:
    """Return configured semantic planning identities allowed to request CAS."""

    return {
        value
        for role in getattr(config, "roles", [])
        if (
            str(getattr(role, "name", "") or "")
            in {"orchestrator", "planner", "synthesizer"}
            or (
                str(getattr(role, "role_kind", "") or "") == "reader"
                and "task.contract.change.requested"
                in set(getattr(role, "publishes", []) or [])
            )
        )
        for value in (
            str(getattr(role, "name", "") or ""),
            str(getattr(role, "instance_id", "") or ""),
        )
        if value
    }


class TaskContractAuthorityService:
    """Prepare, CAS-apply, and audit complete canonical contract mutations."""

    def __init__(
        self,
        *,
        task_store: TaskStore,
        event_writer: EventWriter | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.task_store = task_store
        self.event_writer = event_writer
        self.state_dir = Path(state_dir or task_store.path.parent)

    def replace(
        self,
        task: Task,
        *,
        contract: TaskContract | Mapping[str, Any],
        execution_binding: TaskExecutionBinding | Mapping[str, Any] | None = None,
        source: str,
        actor: str = "zf-cli",
        causation_id: str = "",
        correlation_id: str = "",
        task_updates: Mapping[str, Any] | None = None,
        audit_payload: Mapping[str, Any] | None = None,
        reopen_terminal: bool = False,
    ) -> TaskContractMutation:
        current = self.task_store.get(task.id)
        if current is None:
            raise TaskContractAuthorityError(f"task not found: {task.id}")
        expected_revision = current_authority_revision(task)
        if current_authority_revision(current) != expected_revision:
            self._emit_rejected(
                task_id=task.id,
                source=source,
                expected=expected_revision,
                actual=current_authority_revision(current),
                actor=actor,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            raise TaskContractAuthorityConflict(
                f"task {task.id} authority changed: expected "
                f"{expected_revision or '<legacy>'}, got "
                f"{current_authority_revision(current) or '<legacy>'}"
            )
        materialized = (
            contract
            if isinstance(contract, TaskContract)
            else task_contract_from_mapping(dict(contract))
        )
        materialized = self._preserve_current_metadata(
            materialized,
            current.contract,
        )
        binding = self._binding(execution_binding, current)
        materialized = self._mirror_legacy_binding(materialized, binding)
        effective_task_updates = {
            key: value
            for key, value in dict(task_updates or {}).items()
            if getattr(current, key, None) != value
        }
        sequence = int(getattr(current, "contract_authority_sequence", 0) or 0) + 1
        revision = authority_revision_for(
            task_id=task.id,
            contract=materialized,
            execution_binding=binding,
            sequence=sequence,
        )
        if (
            expected_revision
            and not reopen_terminal
            and asdict(current.contract) == asdict(materialized)
            and asdict(task_execution_binding(current)) == asdict(binding)
        ):
            if effective_task_updates:
                updated, applied = self.task_store.compare_and_update_fields(
                    task.id,
                    expected_authority_revision=expected_revision,
                    updates=effective_task_updates,
                )
                if not applied or updated is None:
                    actual = current_authority_revision(updated) if updated else ""
                    self._emit_rejected(
                        task_id=task.id,
                        source=source,
                        expected=expected_revision,
                        actual=actual,
                        actor=actor,
                        causation_id=causation_id,
                        correlation_id=correlation_id,
                    )
                    raise TaskContractAuthorityConflict(
                        f"task {task.id} field CAS rejected: expected "
                        f"{expected_revision}, got {actual or '<legacy>'}"
                    )
                return TaskContractMutation(
                    task=updated,
                    previous_revision=expected_revision,
                    authority_revision=expected_revision,
                    authority_sequence=int(
                        getattr(updated, "contract_authority_sequence", 0) or 0
                    ),
                    changed=False,
                )
            return TaskContractMutation(
                task=current,
                previous_revision=expected_revision,
                authority_revision=expected_revision,
                authority_sequence=int(
                    getattr(current, "contract_authority_sequence", 0) or 0
                ),
                changed=False,
            )

        receipt = {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "task_id": task.id,
            "source": source,
            "actor": actor,
            "previous_authority_revision": expected_revision,
            "authority_revision": revision,
            "authority_sequence": sequence,
            "contract": asdict(materialized),
            "execution_binding": asdict(binding),
            "task_updates": effective_task_updates,
            "audit_payload": dict(audit_payload or {}),
        }
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            receipt,
            root="task-contract-mutations",
            kind="task_contract_mutation",
            schema_version=MUTATION_SCHEMA_VERSION,
            created_by=actor,
            source_event_id=causation_id,
        )
        prepared = self._emit(
            "task.contract.mutation.prepared",
            task.id,
            {
                **self._receipt_payload(receipt, descriptor),
                **dict(audit_payload or {}),
                "status": "prepared",
            },
            actor=actor,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        updated, applied = self.task_store.compare_and_update_contract(
            task.id,
            expected_authority_revision=expected_revision,
            expected_legacy_contract=(
                asdict(current.contract) if not expected_revision else None
            ),
            expected_legacy_binding=(
                asdict(getattr(current, "execution_binding", TaskExecutionBinding()))
                if not expected_revision
                else None
            ),
            contract=materialized,
            execution_binding=binding,
            contract_authority_revision=revision,
            contract_authority_sequence=sequence,
            task_updates=effective_task_updates,
            reopen_terminal=reopen_terminal,
        )
        if not applied or updated is None:
            actual = current_authority_revision(updated) if updated else ""
            self._emit_rejected(
                task_id=task.id,
                source=source,
                expected=expected_revision,
                actual=actual,
                actor=actor,
                causation_id=(prepared.id if prepared else causation_id),
                correlation_id=correlation_id,
                descriptor=descriptor,
                attempted_revision=revision,
            )
            raise TaskContractAuthorityConflict(
                f"task {task.id} contract CAS rejected: expected "
                f"{expected_revision or '<legacy>'}, got {actual or '<legacy>'}"
            )
        applied_event = self._emit(
            "task.contract.revision.applied",
            task.id,
            {
                **self._receipt_payload(receipt, descriptor),
                **dict(audit_payload or {}),
                "status": "applied",
            },
            actor=actor,
            causation_id=(prepared.id if prepared else causation_id),
            correlation_id=correlation_id,
        )
        # Compatibility audit for historical queries. Housekeeping treats this
        # as a receipt, never as a reducer command.
        self._emit(
            "task.contract.update",
            task.id,
            {
                "source": source,
                "authority_receipt": True,
                "contract": asdict(materialized),
                "contract_authority_revision": revision,
                "contract_authority_sequence": sequence,
                "contract_mutation_ref": descriptor["ref"],
                "contract_mutation_digest": descriptor["sha256"],
                **dict(audit_payload or {}),
            },
            actor=actor,
            causation_id=(applied_event.id if applied_event else causation_id),
            correlation_id=correlation_id,
        )
        return TaskContractMutation(
            task=updated,
            previous_revision=expected_revision,
            authority_revision=revision,
            authority_sequence=sequence,
            receipt_ref=str(descriptor["ref"]),
            receipt_digest=str(descriptor["sha256"]),
        )

    def apply_change_request(
        self,
        event: ZfEvent,
        *,
        allowed_actors: set[str],
    ) -> TaskContractMutation | None:
        """Admit one role-authored intent through the canonical CAS boundary."""

        payload = event.payload if isinstance(event.payload, Mapping) else {}
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        task = self.task_store.get(task_id) if task_id else None
        prior = self._change_request_outcome(event.id)
        if prior == "rejected":
            return None
        if prior == "applied" and task is not None:
            return TaskContractMutation(
                task=task,
                previous_revision=str(
                    getattr(task, "contract_authority_revision", "") or ""
                ),
                authority_revision=current_authority_revision(task),
                authority_sequence=int(
                    getattr(task, "contract_authority_sequence", 0) or 0
                ),
                changed=False,
            )
        expected = str(payload.get("expected_authority_revision") or "").strip()
        actor = str(event.actor or "").strip()
        source = str(payload.get("source") or "agent_change_request").strip()
        contract = payload.get("contract")
        reason = ""
        if task is None:
            reason = "task_not_found"
        elif actor not in allowed_actors:
            reason = "actor_not_authorized"
        elif not isinstance(contract, Mapping):
            reason = "canonical_contract_required"
        elif current_authority_revision(task) and not expected:
            reason = "expected_authority_revision_required"
        elif expected != current_authority_revision(task):
            reason = "stale_contract_authority"
        if reason:
            self._emit_request_rejected(
                event=event,
                task_id=task_id,
                source=source,
                reason=reason,
                expected=expected,
                actual=current_authority_revision(task) if task else "",
            )
            return None
        try:
            assert task is not None and isinstance(contract, Mapping)
            return self.replace(
                task,
                contract=dict(contract),
                execution_binding=(
                    dict(payload["execution_binding"])
                    if isinstance(payload.get("execution_binding"), Mapping)
                    else None
                ),
                source=source,
                actor=actor,
                causation_id=event.id,
                correlation_id=str(event.correlation_id or ""),
                audit_payload={
                    "change_request_event_id": event.id,
                    "request_reason": str(payload.get("reason") or ""),
                },
            )
        except TaskContractAuthorityConflict:
            return None

    def _change_request_outcome(self, event_id: str) -> str:
        if not event_id or self.event_writer is None:
            return ""
        for candidate in reversed(self.event_writer.event_log.read_all()):
            if candidate.type not in {
                "task.contract.revision.applied",
                "task.contract.change.rejected",
            }:
                continue
            body = candidate.payload if isinstance(candidate.payload, dict) else {}
            if str(body.get("change_request_event_id") or "") != event_id:
                continue
            return (
                "applied"
                if candidate.type == "task.contract.revision.applied"
                else "rejected"
            )
        return ""

    def patch_metadata(self, task_id: str, updates: Mapping[str, Any]) -> Task | None:
        """Field-level projection/evidence merge that cannot overwrite semantics."""

        return self.task_store.patch_contract_fields(task_id, dict(updates))

    @staticmethod
    def _preserve_current_metadata(
        replacement: TaskContract,
        current: TaskContract,
    ) -> TaskContract:
        """A semantic writer cannot roll back newer projection/evidence fields."""

        data = asdict(replacement)
        current_data = asdict(current)
        for key in CONTRACT_METADATA_FIELDS:
            data[key] = current_data.get(key)
        return task_contract_from_mapping(data)

    @staticmethod
    def _binding(
        value: TaskExecutionBinding | Mapping[str, Any] | None,
        current: Task,
    ) -> TaskExecutionBinding:
        if value is None:
            return task_execution_binding(current)
        if isinstance(value, TaskExecutionBinding):
            return value
        allowed = TaskExecutionBinding.__dataclass_fields__
        return TaskExecutionBinding(**{
            key: raw for key, raw in dict(value).items() if key in allowed
        })

    @staticmethod
    def _mirror_legacy_binding(
        contract: TaskContract,
        binding: TaskExecutionBinding,
    ) -> TaskContract:
        data = asdict(contract)
        evidence = dict(data.get("evidence_contract") or {})
        for key in EXECUTION_BINDING_EVIDENCE_FIELDS:
            evidence.pop(key, None)
        if binding.owner:
            evidence["execution_owner"] = binding.owner
        if binding.request_id:
            evidence["workflow_request_id"] = binding.request_id
            evidence["workflow_request_revision"] = binding.request_revision
        if binding.workflow_run_id:
            evidence["workflow_run_id"] = binding.workflow_run_id
        if binding.origin_binding_digest:
            evidence["workflow_origin_binding_digest"] = (
                binding.origin_binding_digest
            )
        if binding.origin_task_digest:
            evidence["workflow_origin_task_digest"] = binding.origin_task_digest
        data["evidence_contract"] = evidence
        return task_contract_from_mapping(data)

    @staticmethod
    def _receipt_payload(
        receipt: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "source": receipt["source"],
            "previous_authority_revision": receipt[
                "previous_authority_revision"
            ],
            "contract_authority_revision": receipt["authority_revision"],
            "contract_authority_sequence": receipt["authority_sequence"],
            "contract_mutation_ref": str(descriptor.get("ref") or ""),
            "contract_mutation_digest": str(descriptor.get("sha256") or ""),
        }

    def _emit_rejected(
        self,
        *,
        task_id: str,
        source: str,
        expected: str,
        actual: str,
        actor: str,
        causation_id: str,
        correlation_id: str,
        descriptor: Mapping[str, Any] | None = None,
        attempted_revision: str = "",
    ) -> None:
        self._emit(
            "task.contract.change.rejected",
            task_id,
            {
                "source": source,
                "reason": "stale_contract_authority",
                "expected_authority_revision": expected,
                "current_authority_revision": actual,
                "contract_authority_revision": attempted_revision,
                "contract_mutation_ref": str((descriptor or {}).get("ref") or ""),
                "contract_mutation_digest": str(
                    (descriptor or {}).get("sha256") or ""
                ),
            },
            actor=actor,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def _emit_request_rejected(
        self,
        *,
        event: ZfEvent,
        task_id: str,
        source: str,
        reason: str,
        expected: str,
        actual: str,
    ) -> None:
        self._emit(
            "task.contract.change.rejected",
            task_id,
            {
                "source": source,
                "reason": reason,
                "change_request_event_id": event.id,
                "expected_authority_revision": expected,
                "current_authority_revision": actual,
            },
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=str(event.correlation_id or ""),
        )

    def _emit(
        self,
        event_type: str,
        task_id: str,
        payload: dict[str, Any],
        *,
        actor: str,
        causation_id: str,
        correlation_id: str,
    ) -> ZfEvent | None:
        if self.event_writer is None:
            return None
        return self.event_writer.append(ZfEvent(
            type=event_type,
            actor=actor,
            task_id=task_id,
            payload=payload,
            causation_id=causation_id or None,
            correlation_id=correlation_id or None,
        ))


__all__ = [
    "MUTATION_SCHEMA_VERSION",
    "TaskContractAuthorityConflict",
    "TaskContractAuthorityError",
    "TaskContractAuthorityService",
    "TaskContractMutation",
    "allowed_task_contract_change_actors",
    "authority_revision_for",
    "current_authority_revision",
    "task_execution_binding",
]
