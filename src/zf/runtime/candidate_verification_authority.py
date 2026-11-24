"""Immutable candidate-wide authority for global verification readers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_reader_assignments import candidate_acceptance_id
from zf.runtime.candidate_freeze_recovery_identity import native_freeze_for_recovery
from zf.runtime.candidate_result_binding import (
    candidate_task_source_commits,
    same_task_map_generation,
)
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    current_task_contract_identity,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
    snapshot_payload_fields,
    target_payload_fields,
    write_target_snapshot,
    write_task_contract_snapshot,
)


class CandidateVerificationAuthorityError(ValueError):
    """Raised when a frozen candidate cannot produce exact Verify authority."""


def prepare_candidate_verification_authority(
    runtime: Any,
    *,
    payload: dict[str, Any],
    workflow_run_id: str,
    task_id: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Pin one candidate-wide contract and target before provider dispatch.

    Task-local snapshots remain authoritative for their own implementation and
    Verify attempts.  This aggregate is a separate immutable authority over the
    frozen candidate and never reuses the mutable workflow-anchor contract.
    """

    payload.pop("candidate_reader_scope", None)
    events = runtime.event_log.read_all()
    candidate = frozen_candidate_for_payload(
        events,
        workflow_run_id,
        payload=payload,
    )
    if candidate is None:
        return {}
    body = dict(candidate.payload)
    anchor_task_id = str(task_id or payload.get("task_id") or "").strip()
    if not anchor_task_id:
        raise CandidateVerificationAuthorityError(
            "candidate verification requires a workflow anchor task_id"
        )

    generation = str(body.get("task_map_generation") or "").strip()
    candidate_head = str(
        body.get("candidate_head_commit") or body.get("candidate_head") or ""
    ).strip()
    candidate_base = str(body.get("candidate_base_commit") or "").strip()
    candidate_ref = str(body.get("candidate_ref") or "").strip()
    completed_task_ids = sorted({
        str(item).strip()
        for item in body.get("completed_task_ids") or []
        if str(item).strip()
    })
    required = {
        "task_map_generation": generation,
        "candidate_head_commit": candidate_head,
        "candidate_base_commit": candidate_base,
        "candidate_ref": candidate_ref,
    }
    missing = [key for key, value in required.items() if not value]
    if missing or not completed_task_ids:
        suffix = [*missing, *( ["completed_task_ids"] if not completed_task_ids else [])]
        raise CandidateVerificationAuthorityError(
            "frozen candidate lacks verification identity: " + ", ".join(suffix)
        )
    incoming_target = str(payload.get("target_commit") or "").strip()
    if incoming_target and incoming_target != candidate_head:
        raise CandidateVerificationAuthorityError(
            "candidate verification target does not match the latest frozen candidate"
        )

    freeze_descriptor = body.get("freeze_receipt_ref")
    freeze_descriptor = (
        dict(freeze_descriptor) if isinstance(freeze_descriptor, Mapping) else {}
    )
    freeze_digest = str(body.get("freeze_receipt_digest") or "").strip()
    if (
        not freeze_descriptor
        or not freeze_digest
        or str(freeze_descriptor.get("sha256") or "") != freeze_digest
    ):
        raise CandidateVerificationAuthorityError(
            "frozen candidate lacks an exact freeze receipt"
        )

    from zf.runtime.candidate_continuation import authorized_manifest, source_evidence_run

    evidence_run_id = source_evidence_run(runtime, candidate)
    inherited_proofs = {}
    if body.get("candidate_continuation_ref"):
        receipt = authorized_manifest(runtime, body["candidate_continuation_ref"], workflow_run_id)
        inherited_proofs = {p["task_id"]: p for p in receipt["manifest"]["task_proofs"]}
    task_commits = candidate_task_source_commits(
        events,
        workflow_run_id=evidence_run_id,
        candidate_head_commit=candidate_head,
    )
    missing_commits = sorted(set(completed_task_ids) - set(task_commits))
    if missing_commits:
        raise CandidateVerificationAuthorityError(
            "frozen candidate lacks integrated TaskRefs: "
            + ", ".join(missing_commits)
        )

    missing_verify: set[str] = set()
    from zf.runtime.candidate_retained_tasks import candidate_child_proofs

    try:
        reused = candidate_child_proofs(runtime, events, candidate)
    except ValueError as exc:
        raise CandidateVerificationAuthorityError(str(exc)) from exc
    child_authorities: list[dict[str, Any]] = []
    child_snapshots: list[dict[str, Any]] = []
    for child_task_id in completed_task_ids:
        reuse = reused.get(child_task_id)
        child_generation = str(reuse["task_map_generation"]) if reuse else generation
        verification = _latest_task_verification(
            events,
            workflow_run_id=evidence_run_id,
            task_id=child_task_id,
            task_map_generation=child_generation,
            target_commit=task_commits[child_task_id],
        )
        if verification is None:
            missing_verify.add(child_task_id)
            continue
        if reuse and verification.id != reuse["verification_event_id"]:
            raise CandidateVerificationAuthorityError("reused Task verification changed: " + child_task_id)
        if inherited_proofs:
            current_task = runtime.task_store.get(child_task_id)
            if (verification.id != inherited_proofs[child_task_id]["verification_event_id"]
                    or current_task is None or current_task.status != "done"):
                raise CandidateVerificationAuthorityError("continued Task authority changed: " + child_task_id)
        child_payload = dict(verification.payload)
        contract_descriptor = _descriptor(
            child_payload,
            ref_key="contract_snapshot_ref",
            digest_key="contract_snapshot_digest",
        )
        target_descriptor = _descriptor(
            child_payload,
            ref_key="target_snapshot_ref",
            digest_key="target_snapshot_digest",
        )
        expected_contract = {
            "workflow_run_id": evidence_run_id,
            "task_id": child_task_id,
            "task_map_generation": child_generation,
        }
        contract_snapshot = hydrate_task_contract_snapshot(
            Path(runtime.state_dir),
            contract_descriptor,
            expected=expected_contract,
        )
        snapshot_authority = str(
            contract_snapshot.get("contract_authority_revision") or ""
        )
        if snapshot_authority:
            current_task = runtime.task_store.get(child_task_id)
            if current_task is None:
                raise CandidateVerificationAuthorityError(
                    f"candidate task {child_task_id!r} has no current TaskStore authority"
                )
            try:
                current_identity = reuse["prior_identity"] if reuse else current_task_contract_identity(current_task)
            except Exception as exc:
                raise CandidateVerificationAuthorityError(
                    f"candidate task {child_task_id!r} authority is unreadable: {exc}"
                ) from exc
            for key, expected in current_identity.items():
                if str(contract_snapshot.get(key) or "") != str(expected):
                    raise CandidateVerificationAuthorityError(
                        f"candidate task {child_task_id!r} uses stale {key}"
                    )
        hydrate_target_snapshot(
            Path(runtime.state_dir),
            target_descriptor,
            expected={
                **{
                    key: contract_snapshot.get(key)
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
                "contract_snapshot_ref": contract_descriptor["ref"],
                "contract_snapshot_digest": contract_descriptor["sha256"],
                "target_commit": task_commits[child_task_id],
            },
        )
        child_authorities.append({
            **({"completion_reuse_ref": reuse["completion_reuse_ref"]} if reuse and reuse.get("completion_reuse_ref") else {}),
            **({"retained_task_evidence": reuse["retained_task_evidence"]} if reuse and reuse.get("retained_task_evidence") else {}),
            **({"completion_restore_ref": reuse["completion_restore_ref"]} if reuse and reuse.get("completion_restore_ref") else {}),
            "task_id": child_task_id,
            "target_commit": task_commits[child_task_id],
            "verification_event_id": verification.id,
            "contract_snapshot_ref": contract_descriptor["ref"],
            "contract_snapshot_digest": contract_descriptor["sha256"],
            "target_snapshot_ref": target_descriptor["ref"],
            "target_snapshot_digest": target_descriptor["sha256"],
            "verification_result_ref": _nested_ref(
                child_payload.get("control_result_ref")
            ),
            "verification_result_digest": _nested_digest(
                child_payload.get("control_result_ref")
            ),
            **{
                key: str(contract_snapshot.get(key))
                for key in (
                    "contract_authority_revision",
                    "execution_owner",
                    "workflow_request_id",
                    "workflow_request_revision",
                    "workflow_run_id",
                    "origin_binding_digest",
                    "contract_revision",
                    "task_map_generation",
                )
                if contract_snapshot.get(key) not in (None, "", 0)
            },
        })
        child_snapshots.append(contract_snapshot)

    if missing_verify:
        from zf.runtime.candidate_replacement_authority import verified_predecessors

        replaced = verified_predecessors(
            Path(runtime.state_dir), candidate, events[: events.index(candidate)], child_authorities
        )
        unresolved = sorted(missing_verify - replaced)
        if unresolved:
            raise CandidateVerificationAuthorityError(
                f"candidate task {unresolved[0]!r} lacks current admitted Verify evidence"
            )
        completed_task_ids = sorted(row["task_id"] for row in child_authorities)

    authority_seed = {
        "workflow_run_id": workflow_run_id,
        "anchor_task_id": anchor_task_id,
        "candidate_event_id": candidate.id,
        "candidate_head_commit": candidate_head,
        "candidate_base_commit": candidate_base,
        "candidate_ref": candidate_ref,
        "task_map_generation": generation,
        "freeze_receipt_digest": freeze_digest,
        "child_authorities": child_authorities,
    }
    authority_digest = _digest(authority_seed)
    contract_revision = f"candidate-contract-r{authority_digest[:20]}"
    aggregate = _aggregate_contract_snapshot(
        child_snapshots,
        workflow_run_id=workflow_run_id,
        task_id=anchor_task_id,
        contract_revision=contract_revision,
        task_map_generation=generation,
        base_commit=candidate_base,
        task_ref=candidate_ref,
        candidate_event_id=candidate.id,
        freeze_descriptor=freeze_descriptor,
        child_authorities=child_authorities,
        package_identity={
            "plan_artifact_package_id": str(
                body.get("plan_artifact_package_id") or ""
            ),
            "plan_artifact_package_ref": str(
                body.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                body.get("plan_artifact_package_digest") or ""
            ),
        },
    )
    from zf.runtime.candidate_finding_partition import retained_candidate_obligations

    obligations = retained_candidate_obligations(
        events[:events.index(candidate) + 1], run_id=workflow_run_id, task_id=anchor_task_id, candidate=candidate,
    )
    if obligations:
        aggregate["candidate_recovery_obligations"] = obligations
    from zf.runtime.candidate_reader_assignments import assigned_spec, scope_reader_contract

    spec = assigned_spec(Path(runtime.state_dir), body)
    if spec:
        scope = scope_reader_contract(aggregate, spec, role=str(payload.get("role_instance") or ""),
                                      target=candidate_head)
        payload["candidate_reader_scope"] = scope
        contract_revision = aggregate["contract_revision"]
    contract_descriptor = write_task_contract_snapshot(
        Path(runtime.state_dir),
        aggregate,
        source_event_id=source_event_id or candidate.id,
    )
    target = build_target_snapshot(
        contract_descriptor,
        target_commit=candidate_head,
        contract_snapshot=aggregate,
    )
    target.update({
        "authority_scope": "candidate",
        "candidate_event_id": candidate.id,
        "candidate_ref": candidate_ref,
        "candidate_head_commit": candidate_head,
        "freeze_receipt_ref": str(freeze_descriptor.get("ref") or ""),
        "freeze_receipt_digest": freeze_digest,
        "completed_task_ids": completed_task_ids,
    })
    target_descriptor = write_target_snapshot(
        Path(runtime.state_dir),
        target,
        source_event_id=source_event_id or candidate.id,
    )
    fields = {
        "task_id": anchor_task_id,
        "contract_revision": contract_revision,
        "task_map_generation": generation,
        "base_commit": candidate_base,
        "task_ref": candidate_ref,
        "target_commit": candidate_head,
        "candidate_ref": candidate_ref,
        "candidate_head_commit": candidate_head,
        "candidate_snapshot_event_id": candidate.id,
        "verification_owner": "candidate_verify",
        "verification_tier": "integration",
        **snapshot_payload_fields(contract_descriptor),
        **target_payload_fields(target_descriptor),
    }
    for key in (
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
    ):
        value = str(body.get(key) or "")
        if value:
            fields[key] = value
    payload.update(fields)
    return fields


def _latest_frozen_candidate(
    events: list[ZfEvent],
    workflow_run_id: str,
    *,
    authority_events: list[ZfEvent] | None = None,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != "candidate.ready" or not isinstance(event.payload, Mapping):
            continue
        body = event.payload
        event_run_id = str(
            body.get("workflow_run_id")
            or event.correlation_id
            or ""
        )
        if event_run_id != workflow_run_id:
            continue
        if str(body.get("schema_version") or "") == "candidate-freeze-receipt.v1":
            return native_freeze_for_recovery(events if authority_events is None else authority_events, event)
    return None


def frozen_candidate_for_payload(
    events: list[ZfEvent],
    workflow_run_id: str,
    *,
    payload: Mapping[str, Any],
    authority_events: list[ZfEvent] | None = None,
) -> ZfEvent | None:
    """Select within events; later authority receipts may validate that exact copy."""

    trigger_payload = payload.get("trigger_payload")
    trigger_payload = (
        trigger_payload if isinstance(trigger_payload, Mapping) else {}
    )
    candidate_event_id = str(
        payload.get("candidate_snapshot_event_id")
        or payload.get("candidate_event_id")
        or trigger_payload.get("candidate_snapshot_event_id")
        or trigger_payload.get("candidate_event_id")
        or ""
    ).strip()
    freeze_id = str(
        payload.get("freeze_id")
        or trigger_payload.get("freeze_id")
        or ""
    ).strip()
    if not candidate_event_id and not freeze_id:
        return _latest_frozen_candidate(events, workflow_run_id, authority_events=authority_events)
    candidates = {
        event.id: event
        for event in events
        if event.type == "candidate.ready"
        and isinstance(event.payload, Mapping)
    }
    current = candidates.get(candidate_event_id) if candidate_event_id else None
    visited: set[str] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        body = current.payload
        event_run_id = str(
            body.get("workflow_run_id")
            or current.correlation_id
            or ""
        )
        if event_run_id != workflow_run_id:
            return None
        if str(body.get("schema_version") or "") == "candidate-freeze-receipt.v1":
            if freeze_id and str(body.get("freeze_id") or "") != freeze_id:
                return None
            return native_freeze_for_recovery(events if authority_events is None else authority_events, current)
        parent_id = str(
            body.get("candidate_snapshot_event_id")
            or body.get("candidate_event_id")
            or ""
        ).strip()
        current = candidates.get(parent_id) if parent_id else None
    if freeze_id and not candidate_event_id:
        for event in reversed(events):
            if event.type != "candidate.ready" or not isinstance(event.payload, Mapping):
                continue
            body = event.payload
            event_run_id = str(
                body.get("workflow_run_id")
                or event.correlation_id
                or ""
            )
            if (
                event_run_id == workflow_run_id
                and str(body.get("schema_version") or "")
                == "candidate-freeze-receipt.v1"
                and str(body.get("freeze_id") or "") == freeze_id
            ):
                return native_freeze_for_recovery(events if authority_events is None else authority_events, event)
    return None


def _latest_task_verification(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    task_id: str,
    task_map_generation: str,
    target_commit: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != "task.pipeline.verify.completed" or event.task_id != task_id:
            continue
        body = event.payload if isinstance(event.payload, Mapping) else {}
        if str(body.get("workflow_run_id") or "") != workflow_run_id:
            continue
        if not same_task_map_generation(
            str(body.get("task_map_generation") or ""),
            task_map_generation,
        ):
            continue
        if str(body.get("target_commit") or "") != target_commit:
            continue
        return event
    return None


def _aggregate_contract_snapshot(
    snapshots: list[dict[str, Any]],
    *,
    workflow_run_id: str,
    task_id: str,
    contract_revision: str,
    task_map_generation: str,
    base_commit: str,
    task_ref: str,
    candidate_event_id: str,
    freeze_descriptor: Mapping[str, Any],
    child_authorities: list[dict[str, Any]],
    package_identity: Mapping[str, str],
) -> dict[str, Any]:
    criteria: list[dict[str, Any]] = []
    commands_by_digest: dict[str, dict[str, Any]] = {}
    allowed_paths: list[str] = []
    protected_paths: list[str] = []
    required_source_outputs: list[str] = []
    required_contract_tests: list[str] = []

    for snapshot in snapshots:
        child_task_id = str(snapshot.get("task_id") or "")
        criteria_by_id: dict[str, str] = {}
        for item in snapshot.get("acceptance_criteria") or []:
            if not isinstance(item, Mapping):
                continue
            child_acceptance_id = str(item.get("acceptance_id") or "")
            aggregate_id = candidate_acceptance_id(child_task_id, child_acceptance_id)
            criteria_by_id[child_acceptance_id] = aggregate_id

        command_ids: dict[str, str] = {}
        for item in snapshot.get("verification_commands") or []:
            if not isinstance(item, Mapping):
                continue
            digest = str(item.get("command_digest") or "")
            command = str(item.get("command") or "")
            aggregate_id = f"candidate-cmd-{digest[:20]}"
            existing = commands_by_digest.get(digest)
            if existing is not None and str(existing.get("command") or "") != command:
                raise CandidateVerificationAuthorityError(
                    "candidate verification command digest collision"
                )
            tier = str(item.get("tier") or "integration")
            if existing is not None and existing["tier"] != tier:
                raise CandidateVerificationAuthorityError("same candidate command has conflicting verification tiers")
            command_ids[str(item.get("command_id") or "")] = aggregate_id
            row = commands_by_digest.setdefault(digest, {
                "command_id": aggregate_id,
                "command": command,
                "command_digest": digest,
                "acceptance_ids": [],
                "owner": "candidate_verify",
                "tier": tier,
                "producer_paths": [],
                "deterministic": bool(item.get("deterministic", True)),
                "reusable": False,
                "timeout_seconds": int(item.get("timeout_seconds") or 0),
            })
            row["timeout_seconds"] = max(
                int(row.get("timeout_seconds") or 0),
                int(item.get("timeout_seconds") or 0),
            )
            row["producer_paths"] = _unique([*row["producer_paths"], *item.get("producer_paths", [])])
            row["deterministic"] = row["deterministic"] and bool(item.get("deterministic", True))
            row["acceptance_ids"] = _unique([
                *row.get("acceptance_ids", []),
                *[
                    criteria_by_id[str(item_id)]
                    for item_id in item.get("acceptance_ids") or []
                    if str(item_id) in criteria_by_id
                ],
            ])

        for item in snapshot.get("acceptance_criteria") or []:
            if not isinstance(item, Mapping):
                continue
            child_acceptance_id = str(item.get("acceptance_id") or "")
            aggregate_id = criteria_by_id[child_acceptance_id]
            mapped_commands = [
                command_ids[str(command_id)]
                for command_id in item.get("verification_command_ids") or []
                if str(command_id) in command_ids
            ]
            for command_id in mapped_commands:
                command = next(
                    row for row in commands_by_digest.values()
                    if row["command_id"] == command_id
                )
                command["acceptance_ids"] = _unique([
                    *command.get("acceptance_ids", []),
                    aggregate_id,
                ])
            statement = str(item.get("statement") or item.get("text") or "")
            criteria.append({
                "acceptance_id": aggregate_id,
                "statement": f"[{child_task_id}] {statement}",
                "text": f"[{child_task_id}] {statement}",
                "mandatory": bool(item.get("mandatory", True)),
                "verification_owner": "candidate_verify",
                "verification_tier": str(item.get("verification_tier") or "integration"),
                "producer_paths": list(item.get("producer_paths") or []),
                "verification_command_ids": mapped_commands,
                "source_task_id": child_task_id,
                "source_acceptance_id": child_acceptance_id,
            })

        allowed_paths.extend(str(item) for item in snapshot.get("allowed_paths") or [])
        protected_paths.extend(str(item) for item in snapshot.get("protected_paths") or [])
        required_source_outputs.extend(
            str(item) for item in snapshot.get("required_source_outputs") or []
        )
        required_contract_tests.extend(
            str(item) for item in snapshot.get("required_contract_tests") or []
        )

    commands = sorted(
        commands_by_digest.values(),
        key=lambda item: str(item.get("command_id") or ""),
    )
    criteria.sort(key=lambda item: str(item.get("acceptance_id") or ""))
    return {
        "schema_version": "task-contract-snapshot.v1",
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "contract_revision": contract_revision,
        "task_map_generation": task_map_generation,
        "base_commit": base_commit,
        "task_ref": task_ref,
        **dict(package_identity),
        "title": "Frozen candidate verification contract",
        "behavior": "Verify the integrated frozen candidate against every admitted Task contract.",
        "allowed_paths": _unique(allowed_paths),
        "protected_paths": _unique(protected_paths) or [".zf/**"],
        "acceptance_criteria": criteria,
        "verification_command": str(commands[0]["command"] if commands else ""),
        "verification_commands": commands,
        "verification_tiers": _unique([str(row["tier"]) for row in commands]),
        "verification_owner": "candidate_verify",
        "verification_tier": "integration",
        "required_source_outputs": _unique(required_source_outputs),
        "required_contract_tests": _unique(required_contract_tests),
        "source_refs": {
            "candidate_event_id": candidate_event_id,
            "freeze_receipt_ref": str(freeze_descriptor.get("ref") or ""),
            "freeze_receipt_digest": str(freeze_descriptor.get("sha256") or ""),
            "child_authorities": child_authorities,
        },
        "evidence_contract": {
            "authority_scope": "candidate",
            "candidate_event_id": candidate_event_id,
            "child_task_ids": [
                str(item.get("task_id") or "") for item in child_authorities
            ],
            "source_refs": {
                "freeze_receipt_ref": str(freeze_descriptor.get("ref") or ""),
                "freeze_receipt_digest": str(freeze_descriptor.get("sha256") or ""),
            },
        },
        "authority_scope": "candidate",
        "candidate_event_id": candidate_event_id,
        "completed_task_ids": [
            str(item.get("task_id") or "") for item in child_authorities
        ],
        "child_authorities": child_authorities,
        "source_ref": str(freeze_descriptor.get("ref") or ""),
        "source_index_ref": "",
        "product_contract_ref": "",
        "risk_class": "candidate",
        "integration_admission_profile": "candidate-wide",
    }


def _descriptor(
    payload: Mapping[str, Any],
    *,
    ref_key: str,
    digest_key: str,
) -> dict[str, str]:
    ref = str(payload.get(ref_key) or "").strip()
    digest = str(payload.get(digest_key) or "").strip()
    if not ref or not digest:
        raise CandidateVerificationAuthorityError(
            f"candidate task Verify lacks {ref_key}/{digest_key}"
        )
    return {"ref": ref, "sha256": digest}


def _nested_ref(value: Any) -> str:
    return str(value.get("ref") or "") if isinstance(value, Mapping) else ""


def _nested_digest(value: Any) -> str:
    return str(value.get("sha256") or "") if isinstance(value, Mapping) else ""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "CandidateVerificationAuthorityError",
    "frozen_candidate_for_payload",
    "prepare_candidate_verification_authority",
]
