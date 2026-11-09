"""Immutable task-contract snapshots shared by implementation and verification."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.core.task.schema import Task
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.verification_commands import task_contract_verification_commands


SCHEMA_VERSION = "task-contract-snapshot.v1"
TARGET_SCHEMA_VERSION = "task-verification-target.v1"
VERIFICATION_OWNERS = frozenset({
    "impl_self_check",
    "task_verify",
    "candidate_verify",
    "human",
})
VERIFICATION_TIERS = frozenset({
    "fast",
    "task_non_smoke",
    "integration",
    "real_e2e",
    "release",
})
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")

TASK_CONTRACT_CURRENTNESS_FIELDS = (
    "workflow_run_id",
    "task_id",
    "contract_authority_revision",
    "execution_owner",
    "workflow_request_id",
    "workflow_request_revision",
    "origin_binding_digest",
    "contract_revision",
    "task_map_generation",
    "base_commit",
    "task_ref",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)
TASK_CONTRACT_AUTHORITY_FIELDS = (
    "contract_authority_revision",
    "execution_owner",
    "workflow_request_id",
    "workflow_request_revision",
    "origin_binding_digest",
)


class TaskContractSnapshotError(ValueError):
    """Raised when a snapshot is incomplete, stale, or has been tampered with."""


def acceptance_contract_errors(
    values: Any,
    *,
    task_id: str,
) -> list[str]:
    """Preflight structured criteria with the snapshot's canonicalizers."""

    if not isinstance(values, list):
        return []
    errors: list[str] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            continue
        criterion_id = str(
            raw.get("acceptance_id") or raw.get("id") or index
        ).strip()
        prefix = f"{task_id}.acceptance_criteria[{criterion_id}]"
        owner = str(raw.get("verification_owner") or "").strip()
        if owner:
            try:
                canonical_verification_owner(owner)
            except TaskContractSnapshotError as exc:
                errors.append(f"{prefix}.verification_owner {exc}")
        tier = str(raw.get("verification_tier") or "").strip()
        if tier:
            try:
                canonical_verification_tier(tier)
            except TaskContractSnapshotError as exc:
                errors.append(f"{prefix}.verification_tier {exc}")
    return errors


def criterion_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(
            value.get("text")
            or value.get("statement")
            or value.get("criterion")
            or value.get("description")
            or value.get("acceptance")
            or ""
        ).strip()
    return str(value or "").strip()


def normalize_acceptance_criteria(
    values: Any,
    *,
    task_id: str,
    contract_revision: str,
    verification_tiers: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return stable, owner-routable acceptance criteria.

    Historical string criteria remain valid. Structured criteria may provide
    ``id``, ``verification_owner`` and ``verification_tier`` explicitly.
    """

    raw_values = values if isinstance(values, list) else [values]
    default_tier = canonical_verification_tier(next(
        (str(item).strip() for item in verification_tiers if str(item).strip()),
        "task_non_smoke",
    ))
    out: list[dict[str, Any]] = []
    occurrences: dict[str, int] = {}
    for raw in raw_values:
        text = criterion_text(raw)
        if not text:
            continue
        record = dict(raw) if isinstance(raw, Mapping) else {}
        occurrences[text] = occurrences.get(text, 0) + 1
        stable_seed = "\0".join((
            task_id,
            contract_revision,
            text,
            str(occurrences[text]),
        ))
        criterion_id = str(record.get("id") or record.get("acceptance_id") or "").strip()
        if not criterion_id:
            criterion_id = "ac-" + hashlib.sha256(
                stable_seed.encode("utf-8"),
            ).hexdigest()[:16]
        owner = canonical_verification_owner(
            record.get("verification_owner") or "task_verify"
        )
        tier = canonical_verification_tier(
            record.get("verification_tier") or default_tier
        )
        command_ids = _string_list(
            record.get("verification_command_ids")
            or record.get("command_ids")
            or record.get("verification_commands")
        )
        out.append({
            "acceptance_id": criterion_id,
            "statement": text,
            "text": text,
            "mandatory": bool(record.get("mandatory", True)),
            "verification_owner": owner,
            "verification_tier": tier,
            "verification_command_ids": command_ids,
            "producer_paths": _string_list(record.get("producer_paths")),
        })
    if not out:
        raise TaskContractSnapshotError("task contract has no acceptance criteria")
    return out


def effective_contract_revision(task: Task) -> str:
    # Task docs are projections and refresh their stored revision during
    # lifecycle moves. Derive the snapshot identity from the same canonical
    # semantic body so a dispatch cannot become stale merely because that
    # projection refresh ran after operation preregistration.
    from zf.runtime.task_doc import compute_task_capsule_revisions

    return compute_task_capsule_revisions(task)["contract_revision"]


def task_map_generation(task: Task, *, task_map_ref: str = "") -> str:
    contract = task.contract
    evidence = (
        contract.evidence_contract
        if isinstance(getattr(contract, "evidence_contract", None), dict)
        else {}
    )
    source_refs = evidence.get("source_refs") if isinstance(evidence.get("source_refs"), dict) else {}
    explicit = str(
        evidence.get("task_map_generation")
        or source_refs.get("task_map_generation")
        or ""
    ).strip()
    if explicit:
        return explicit
    ref = str(
        task_map_ref
        or source_refs.get("task_map_ref")
        or getattr(contract, "plan_ref", "")
        or getattr(contract, "source_ref", "")
        or ""
    ).strip()
    if not ref:
        raise TaskContractSnapshotError("task contract lacks task_map generation source")
    return "task-map-" + hashlib.sha256(ref.encode("utf-8")).hexdigest()[:20]


def current_task_contract_identity(
    task: Task,
    *,
    task_map_ref: str = "",
) -> dict[str, str]:
    """Return the TaskStore-owned identity used to reject stale snapshots."""

    from zf.runtime.task_contract_authority import task_execution_binding

    identity = {
        "task_id": str(task.id or "").strip(),
        "contract_revision": effective_contract_revision(task),
        "task_map_generation": task_map_generation(
            task,
            task_map_ref=task_map_ref,
        ),
    }
    authority_revision = str(
        getattr(task, "contract_authority_revision", "") or ""
    ).strip()
    if authority_revision:
        identity["contract_authority_revision"] = authority_revision
    binding = task_execution_binding(task)
    for key, value in (
        ("execution_owner", binding.owner),
        ("workflow_request_id", binding.request_id),
        ("workflow_request_revision", binding.request_revision),
        ("workflow_run_id", binding.workflow_run_id),
        ("origin_binding_digest", binding.origin_binding_digest),
    ):
        if value not in (None, "", 0):
            identity[key] = str(value)
    return identity


def build_task_contract_snapshot(
    task: Task,
    *,
    workflow_run_id: str,
    task_map_generation_id: str,
    base_commit: str,
    task_ref: str,
) -> dict[str, Any]:
    from zf.runtime.task_contract_authority import task_execution_binding

    binding = task_execution_binding(task)
    canonical_run_id = str(binding.workflow_run_id or "").strip()
    supplied_run_id = str(workflow_run_id or "").strip()
    if canonical_run_id and supplied_run_id != canonical_run_id:
        raise TaskContractSnapshotError(
            "task contract snapshot workflow_run_id differs from execution binding"
        )
    identity = {
        "workflow_run_id": supplied_run_id,
        "task_id": str(task.id or "").strip(),
        "contract_revision": effective_contract_revision(task),
        "task_map_generation": str(task_map_generation_id or "").strip(),
        "base_commit": str(base_commit or "").strip(),
        "task_ref": str(task_ref or "").strip(),
    }
    authority_revision = str(
        getattr(task, "contract_authority_revision", "") or ""
    ).strip()
    if authority_revision:
        identity["contract_authority_revision"] = authority_revision
        for key, value in (
            ("execution_owner", binding.owner),
            ("workflow_request_id", binding.request_id),
            ("workflow_request_revision", binding.request_revision),
            ("origin_binding_digest", binding.origin_binding_digest),
        ):
            if value not in (None, "", 0):
                identity[key] = str(value)
    required_identity = (
        "workflow_run_id",
        "task_id",
        "contract_revision",
        "task_map_generation",
        "base_commit",
        "task_ref",
    )
    missing = [key for key in required_identity if not identity.get(key)]
    if missing:
        raise TaskContractSnapshotError(
            "task contract snapshot missing identity: " + ", ".join(missing)
        )
    contract = task.contract
    evidence_contract = (
        dict(contract.evidence_contract)
        if isinstance(getattr(contract, "evidence_contract", None), dict)
        else {}
    )
    commands = task_contract_verification_commands(contract)
    source_refs = dict(evidence_contract.get("source_refs") or {})
    verification_command = str(commands[0]["command"] if commands else "")
    criteria = normalize_acceptance_criteria(
        getattr(contract, "acceptance_criteria", []) or [getattr(contract, "acceptance", "")],
        task_id=identity["task_id"],
        contract_revision=identity["contract_revision"],
        verification_tiers=list(getattr(contract, "verification_tiers", []) or []),
    )
    verification_owner, verification_tier = _snapshot_verification_route(
        criteria, commands=commands, task_id=identity["task_id"],
    )
    all_command_ids = [str(item["id"]) for item in commands]
    for criterion in criteria:
        if criterion["verification_command_ids"]:
            continue
        acceptance_id = str(criterion["acceptance_id"])
        mapped = [
            str(item["id"])
            for item in commands
            if not item["acceptance_ids"] or acceptance_id in item["acceptance_ids"]
        ]
        criterion["verification_command_ids"] = mapped or list(all_command_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "plan_artifact_package_id": str(
            source_refs.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_ref": str(
            source_refs.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            source_refs.get("plan_artifact_package_digest") or ""
        ),
        "title": str(task.title or ""),
        "behavior": str(getattr(contract, "behavior", "") or ""),
        "allowed_paths": list(getattr(contract, "scope", []) or []),
        "protected_paths": [".zf/**"],
        "acceptance_criteria": criteria,
        "verification_owner": verification_owner,
        "verification_tier": verification_tier,
        "verification_command": verification_command,
        "verification_commands": [
            {
                "command_id": item["id"],
                "command": item["command"],
                "command_digest": item["command_digest"],
                "acceptance_ids": list(item["acceptance_ids"]),
                "owner": item["owner"],
                "tier": item["tier"],
                "deterministic": item["deterministic"],
                "reusable": item["reusable"],
                "timeout_seconds": item["timeout_seconds"],
                **(
                    {"producer_paths": list(item["producer_paths"])}
                    if item.get("producer_paths")
                    else {}
                ),
                **(
                    {"producer_task_id": str(item["producer_task_id"])}
                    if item.get("producer_task_id")
                    else {}
                ),
            }
            for item in commands
        ],
        "verification_tiers": list(getattr(contract, "verification_tiers", []) or []),
        "required_source_outputs": list(dict.fromkeys([
            *_string_list(
                evidence_contract.get("required_source_outputs")
                or evidence_contract.get("required_files")
            ),
            *[
                path
                for criterion in criteria
                for path in _string_list(criterion.get("producer_paths"))
            ],
            *[
                path
                for command in commands
                for path in _string_list(command.get("producer_paths"))
            ],
        ])),
        "required_contract_tests": _string_list(
            evidence_contract.get("required_contract_tests")
            or evidence_contract.get("required_tests")
        ),
        "source_refs": source_refs,
        "evidence_contract": evidence_contract,
        "source_ref": str(getattr(contract, "source_ref", "") or ""),
        "source_index_ref": str(getattr(contract, "source_index_ref", "") or ""),
        "product_contract_ref": str(getattr(contract, "product_contract_ref", "") or ""),
        "risk_class": str(getattr(contract, "risk_class", "") or ""),
        "integration_admission_profile": str(
            getattr(contract, "integration_admission_profile", "") or ""
        ),
    }


def write_task_contract_snapshot(
    state_dir: Path,
    snapshot: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    _validate_snapshot(snapshot)
    run = _segment(snapshot["workflow_run_id"])
    task = _segment(snapshot["task_id"])
    revision = _segment(snapshot["contract_revision"])
    body_digest = _digest(snapshot)
    ref = f"artifacts/task-contract-snapshots/{run}/{task}/{revision}-{body_digest[:16]}.json"
    return write_sidecar_json(
        state_dir,
        ref,
        dict(snapshot),
        kind="task_contract_snapshot",
        schema_version=SCHEMA_VERSION,
        created_by="orchestrator",
        source_event_id=source_event_id,
        required=True,
        preview=f"{snapshot['task_id']}@{snapshot['contract_revision']}",
    )


def hydrate_task_contract_snapshot(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            dict(descriptor),
            purpose="task_contract_dispatch",
            actor="orchestrator",
        )
    except Exception as exc:  # sidecar errors become one stable contract error
        raise TaskContractSnapshotError(str(exc)) from exc
    if not isinstance(hydrated.payload, dict):
        raise TaskContractSnapshotError("task contract snapshot is not an object")
    snapshot = dict(hydrated.payload)
    _validate_snapshot(snapshot)
    for key, value in (expected or {}).items():
        if value in (None, ""):
            continue
        if str(snapshot.get(key) or "") != str(value):
            raise TaskContractSnapshotError(
                f"task contract snapshot {key} mismatch: "
                f"expected {value!r}, got {snapshot.get(key)!r}"
            )
    return snapshot


def descriptor_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ref = str(
        payload.get("task_contract_snapshot_ref")
        or payload.get("contract_snapshot_ref")
        or ""
    ).strip()
    digest = str(
        payload.get("task_contract_snapshot_digest")
        or payload.get("contract_snapshot_digest")
        or ""
    ).strip()
    if not ref or not digest:
        raise TaskContractSnapshotError("contract snapshot ref/digest missing")
    return {
        "ref": ref,
        "sha256": digest,
        "kind": "task_contract_snapshot",
        "schema_version": SCHEMA_VERSION,
        "content_type": "application/json",
        "required": True,
    }


def snapshot_payload_fields(descriptor: Mapping[str, Any]) -> dict[str, str]:
    ref = str(descriptor.get("ref") or "")
    digest = str(descriptor.get("sha256") or "")
    return {
        "task_contract_snapshot_ref": ref,
        "task_contract_snapshot_digest": digest,
        # Replay compatibility for events and profiles written before the
        # typed snapshot fields were introduced.
        "contract_snapshot_ref": ref,
        "contract_snapshot_digest": digest,
    }


def contract_snapshot_identity_fields(
    snapshot: Mapping[str, Any],
) -> dict[str, str]:
    """Return the non-empty currentness identity pinned by one snapshot."""

    return {
        key: str(snapshot.get(key))
        for key in TASK_CONTRACT_CURRENTNESS_FIELDS
        if snapshot.get(key) not in (None, "", 0)
    }


def build_target_snapshot(
    descriptor: Mapping[str, Any],
    *,
    target_commit: str,
    contract_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    ref = str(descriptor.get("ref") or "").strip()
    digest = str(descriptor.get("sha256") or "").strip()
    commit = str(target_commit or "").strip()
    if not ref or not digest or not commit:
        raise TaskContractSnapshotError("verification target lacks contract ref/digest/commit")
    identity = {
        key: str((contract_snapshot or {}).get(key) or "")
        for key in (
            "workflow_run_id",
            "task_id",
            "contract_authority_revision",
            "execution_owner",
            "workflow_request_id",
            "workflow_request_revision",
            "origin_binding_digest",
            "contract_revision",
            "task_map_generation",
            "base_commit",
            "task_ref",
            "plan_artifact_package_id",
            "plan_artifact_package_ref",
            "plan_artifact_package_digest",
        )
        if str((contract_snapshot or {}).get(key) or "").strip()
    }
    return {
        "schema_version": TARGET_SCHEMA_VERSION,
        **identity,
        "contract_snapshot_ref": ref,
        "contract_snapshot_digest": digest,
        "target_commit": commit,
    }


def write_target_snapshot(
    state_dir: Path,
    target_snapshot: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    _validate_target_snapshot(target_snapshot)
    run = _segment(target_snapshot.get("workflow_run_id") or "legacy-run")
    task = _segment(target_snapshot.get("task_id") or "unknown-task")
    commit = _segment(str(target_snapshot["target_commit"])[:20])
    digest = _digest(target_snapshot)
    ref = f"artifacts/task-verification-targets/{run}/{task}/{commit}-{digest[:16]}.json"
    return write_sidecar_json(
        state_dir,
        ref,
        dict(target_snapshot),
        kind="task_verification_target",
        schema_version=TARGET_SCHEMA_VERSION,
        created_by="orchestrator",
        source_event_id=source_event_id,
        required=True,
        preview=f"{task}@{str(target_snapshot['target_commit'])[:12]}",
    )


def target_payload_fields(descriptor: Mapping[str, Any]) -> dict[str, str]:
    return {
        "target_snapshot_ref": str(descriptor.get("ref") or ""),
        "target_snapshot_digest": str(descriptor.get("sha256") or ""),
    }


def target_descriptor_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ref = str(payload.get("target_snapshot_ref") or "").strip()
    digest = str(payload.get("target_snapshot_digest") or "").strip()
    if not ref or not digest:
        raise TaskContractSnapshotError("target snapshot ref/digest missing")
    return {
        "ref": ref,
        "sha256": digest,
        "kind": "task_verification_target",
        "schema_version": TARGET_SCHEMA_VERSION,
        "content_type": "application/json",
        "required": True,
    }


def hydrate_target_snapshot(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            dict(descriptor),
            purpose="task_verification_target",
            actor="orchestrator",
        )
    except Exception as exc:
        raise TaskContractSnapshotError(str(exc)) from exc
    if not isinstance(hydrated.payload, dict):
        raise TaskContractSnapshotError("verification target snapshot is not an object")
    snapshot = dict(hydrated.payload)
    _validate_target_snapshot(snapshot)
    for key, value in (expected or {}).items():
        if value in (None, ""):
            continue
        if str(snapshot.get(key) or "") != str(value):
            raise TaskContractSnapshotError(
                f"verification target {key} mismatch: "
                f"expected {value!r}, got {snapshot.get(key)!r}"
            )
    return snapshot


def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if str(snapshot.get("schema_version") or "") != SCHEMA_VERSION:
        raise TaskContractSnapshotError("unsupported task contract snapshot schema")
    required = (
        "workflow_run_id",
        "task_id",
        "contract_revision",
        "task_map_generation",
        "base_commit",
        "task_ref",
    )
    missing = [key for key in required if not str(snapshot.get(key) or "").strip()]
    if missing:
        raise TaskContractSnapshotError("snapshot missing: " + ", ".join(missing))
    criteria = snapshot.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise TaskContractSnapshotError("snapshot acceptance_criteria must be non-empty")
    for item in criteria:
        if not isinstance(item, Mapping) or any(
            not str(item.get(key) or "").strip()
            for key in (
                "acceptance_id", "statement", "verification_owner",
                "verification_tier",
            )
        ):
            raise TaskContractSnapshotError("snapshot acceptance criterion is incomplete")
        if str(item.get("verification_owner") or "") not in VERIFICATION_OWNERS:
            raise TaskContractSnapshotError("snapshot acceptance owner is invalid")
        if str(item.get("verification_tier") or "") not in VERIFICATION_TIERS:
            raise TaskContractSnapshotError("snapshot acceptance tier is invalid")
    acceptance_ids = [str(item.get("acceptance_id") or "") for item in criteria]
    if len(acceptance_ids) != len(set(acceptance_ids)):
        raise TaskContractSnapshotError("snapshot acceptance ids must be unique")
    snapshot_owner = str(snapshot.get("verification_owner") or "")
    if snapshot_owner and snapshot_owner not in VERIFICATION_OWNERS:
        raise TaskContractSnapshotError("snapshot verification owner is invalid")
    snapshot_tier = str(snapshot.get("verification_tier") or "")
    if snapshot_tier and snapshot_tier not in VERIFICATION_TIERS:
        raise TaskContractSnapshotError("snapshot verification tier is invalid")
    commands = snapshot.get("verification_commands", [])
    if not isinstance(commands, list):
        raise TaskContractSnapshotError("snapshot verification_commands must be a list")
    command_ids: list[str] = []
    for item in commands:
        if not isinstance(item, Mapping):
            raise TaskContractSnapshotError("snapshot verification command must be an object")
        command_id = str(item.get("command_id") or "").strip()
        command = str(item.get("command") or "").strip()
        digest = str(item.get("command_digest") or "").strip()
        if not command_id or not command or len(digest) != 64:
            raise TaskContractSnapshotError("snapshot verification command is incomplete")
        if _digest_text(command) != digest:
            raise TaskContractSnapshotError("snapshot verification command digest mismatch")
        command_ids.append(command_id)
    if len(command_ids) != len(set(command_ids)):
        raise TaskContractSnapshotError("snapshot verification command ids must be unique")
    known_commands = set(command_ids)
    for criterion in criteria:
        unknown = set(_string_list(criterion.get("verification_command_ids"))) - known_commands
        if unknown:
            raise TaskContractSnapshotError(
                "snapshot acceptance criterion references unknown command ids: "
                + ", ".join(sorted(unknown))
            )


def _validate_target_snapshot(snapshot: Mapping[str, Any]) -> None:
    if str(snapshot.get("schema_version") or "") != TARGET_SCHEMA_VERSION:
        raise TaskContractSnapshotError("unsupported verification target schema")
    missing = [
        key
        for key in (
            "contract_snapshot_ref",
            "contract_snapshot_digest",
            "target_commit",
        )
        if not str(snapshot.get(key) or "").strip()
    ]
    if missing:
        raise TaskContractSnapshotError(
            "verification target missing: " + ", ".join(missing)
        )


def _snapshot_verification_route(
    criteria: list[dict[str, Any]],
    *,
    commands: list[dict[str, Any]],
    task_id: str,
) -> tuple[str, str]:
    mandatory = [
        item for item in criteria
        if item.get("mandatory", True) is not False
    ]
    owners = {
        str(item.get("verification_owner") or "task_verify")
        for item in mandatory
    }
    # Final AC ownership does not remove explicitly declared module checks.
    # Imported producer evidence and Impl-only checks do not create that stage.
    local_checks = [
        item for item in commands
        if item.get("owner") == "task_verify"
        and str(item.get("producer_task_id") or task_id).strip() == task_id
    ]
    owner = (
        "candidate_verify"
        if owners == {"candidate_verify"} and not local_checks
        else "task_verify"
    )
    tiers = {
        str(item.get("verification_tier") or "task_non_smoke")
        for item in mandatory
        if str(item.get("verification_owner") or "task_verify") == owner
    }
    if not tiers and owner == "task_verify":
        tiers = {
            canonical_verification_tier(item.get("tier") or "task_non_smoke")
            for item in local_checks
        }
    tier = next(iter(tiers)) if len(tiers) == 1 else "task_non_smoke"
    return owner, tier


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _segment(value: Any) -> str:
    return _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip("-._") or "unknown"


def _string_list(value: Any) -> list[str]:
    source = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return [str(item).strip() for item in source if str(item).strip()]


def canonical_verification_owner(value: Any) -> str:
    owner = str(value or "").strip()
    if owner in VERIFICATION_OWNERS:
        return owner
    lowered = owner.lower()
    if any(marker in lowered for marker in ("candidate", "assembly", "judge", "integration")):
        return "candidate_verify"
    if any(marker in lowered for marker in ("impl", "dev", "self")):
        return "impl_self_check"
    if "human" in lowered or "owner" in lowered:
        return "human"
    if any(
        marker in lowered
        for marker in ("verify", "test", "review", "qa", "contract", "gate")
    ):
        return "task_verify"
    raise TaskContractSnapshotError(f"unsupported verification owner {owner!r}")


def canonical_verification_tier(value: Any) -> str:
    tier = str(value or "").strip()
    # ZF-TIER-ALIAS-01(07-16 复跑实弹):planner 产出 tier 'unit' 直达
    # 此处,未过 canonical 归一化 → 整张 task_map 被拒 → integration.failed
    # → replan 同因两连败 → cap。先按 canonical 别名表归一(LLM 词汇
    # 宽进),再映射内部档位(严出)。
    from zf.runtime.task_contract_normalize import _TIER_ALIASES

    tier = _TIER_ALIASES.get(tier.lower(), tier)
    aliases = {
        "static": "fast",
        "runtime": "task_non_smoke",
        "e2e": "real_e2e",
        "manual_evidence": "release",
    }
    tier = aliases.get(tier, tier)
    if tier not in VERIFICATION_TIERS:
        raise TaskContractSnapshotError(f"unsupported verification tier {tier!r}")
    return tier


__all__ = [
    "SCHEMA_VERSION",
    "TARGET_SCHEMA_VERSION",
    "VERIFICATION_OWNERS",
    "VERIFICATION_TIERS",
    "TaskContractSnapshotError",
    "TASK_CONTRACT_CURRENTNESS_FIELDS",
    "TASK_CONTRACT_AUTHORITY_FIELDS",
    "acceptance_contract_errors",
    "build_target_snapshot",
    "build_task_contract_snapshot",
    "canonical_verification_owner",
    "canonical_verification_tier",
    "contract_snapshot_identity_fields",
    "criterion_text",
    "descriptor_from_payload",
    "effective_contract_revision",
    "hydrate_task_contract_snapshot",
    "hydrate_target_snapshot",
    "normalize_acceptance_criteria",
    "snapshot_payload_fields",
    "target_payload_fields",
    "target_descriptor_from_payload",
    "task_map_generation",
    "write_task_contract_snapshot",
    "write_target_snapshot",
]
