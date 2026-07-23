"""Typed, target-bound implementation self-check sidecars.

The implementation Agent owns semantic sufficiency. Runtime only validates
identity, coverage, declared command receipts, and durable evidence refs.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_contract_snapshot import TaskContractSnapshotError


SCHEMA_VERSION = "impl-self-check.v1"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_AC_STATUSES = {"passed", "failed", "blocked", "waived"}
_RECEIPT_STATUSES = {"passed", "failed", "blocked"}


class ImplSelfCheckError(TaskContractSnapshotError):
    """The self-check is incomplete, stale, or not bound to its target."""


def normalize_impl_self_check(
    payload: Mapping[str, Any],
    *,
    contract_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
    expected_attempt_id: str = "",
    strict: bool = True,
) -> dict[str, Any]:
    raw = payload.get("impl_self_check")
    if not isinstance(raw, Mapping):
        raise ImplSelfCheckError("completion payload lacks impl_self_check object")
    body = dict(raw)
    expected = {
        "task_id": str(contract_snapshot.get("task_id") or ""),
        "workflow_run_id": str(contract_snapshot.get("workflow_run_id") or ""),
        "contract_revision": str(contract_snapshot.get("contract_revision") or ""),
        "task_map_generation": str(contract_snapshot.get("task_map_generation") or ""),
        "source_commit": str(target_snapshot.get("target_commit") or ""),
        "target_commit": str(target_snapshot.get("target_commit") or ""),
        "contract_snapshot_ref": str(target_snapshot.get("contract_snapshot_ref") or ""),
        "contract_snapshot_digest": str(target_snapshot.get("contract_snapshot_digest") or ""),
    }
    body.setdefault("schema_version", SCHEMA_VERSION)
    if not strict:
        for key, value in expected.items():
            body.setdefault(key, value)
        body.setdefault("attempt_id", expected_attempt_id)
    _validate_identity(body, expected=expected, expected_attempt_id=expected_attempt_id)

    command_specs = {
        str(item.get("command_id") or ""): item
        for item in contract_snapshot.get("verification_commands") or []
        if isinstance(item, Mapping) and str(item.get("command_id") or "")
    }
    receipts = _normalize_receipts(
        body.get("command_receipts"),
        command_specs=command_specs,
        target_commit=expected["target_commit"],
        strict=strict,
    )
    body["command_receipts"] = receipts
    receipt_ids = {str(item["receipt_id"]) for item in receipts}

    criteria = {
        str(item.get("acceptance_id") or ""): item
        for item in contract_snapshot.get("acceptance_criteria") or []
        if isinstance(item, Mapping) and str(item.get("acceptance_id") or "")
    }
    results = _normalize_acceptance_results(body.get("acceptance_results"))
    unknown = sorted(
        str(item["acceptance_id"])
        for item in results
        if str(item["acceptance_id"]) not in criteria
    )
    if unknown:
        raise ImplSelfCheckError(
            "self-check references unknown acceptance ids: " + ", ".join(unknown)
        )
    result_by_id = {str(item["acceptance_id"]): item for item in results}
    mandatory = {
        acceptance_id
        for acceptance_id, item in criteria.items()
        if bool(item.get("mandatory", True))
    }
    missing = sorted(mandatory - set(result_by_id))
    if strict and missing:
        raise ImplSelfCheckError(
            "self-check misses mandatory acceptance ids: " + ", ".join(missing)
        )
    incomplete_mandatory = sorted(
        acceptance_id
        for acceptance_id in mandatory
        if str((result_by_id.get(acceptance_id) or {}).get("status") or "")
        != "passed"
    )
    if strict and incomplete_mandatory:
        raise ImplSelfCheckError(
            "self-check mandatory acceptance did not pass: "
            + ", ".join(incomplete_mandatory)
        )
    receipt_by_id = {str(item["receipt_id"]): item for item in receipts}
    for item in results:
        unknown_receipts = sorted(set(item["command_receipt_ids"]) - receipt_ids)
        if unknown_receipts:
            raise ImplSelfCheckError(
                f"{item['acceptance_id']} references unknown receipt ids: "
                + ", ".join(unknown_receipts)
            )
        if strict and item["status"] == "passed":
            if not item["evidence_refs"]:
                raise ImplSelfCheckError(
                    f"{item['acceptance_id']} passed without evidence_refs"
                )
            required_commands = set(criteria.get(
                str(item["acceptance_id"]), {}
            ).get("verification_command_ids") or [])
            referenced_receipts = [
                receipt_by_id[receipt_id]
                for receipt_id in item["command_receipt_ids"]
                if receipt_id in receipt_by_id
            ]
            referenced_commands = {
                str(receipt.get("command_id") or "")
                for receipt in referenced_receipts
            }
            if required_commands - referenced_commands:
                raise ImplSelfCheckError(
                    f"{item['acceptance_id']} misses passing command receipts: "
                    + ", ".join(sorted(required_commands - referenced_commands))
                )
            failed_receipts = [
                str(receipt.get("receipt_id") or "")
                for receipt in referenced_receipts
                if str(receipt.get("status") or "") != "passed"
            ]
            if failed_receipts:
                raise ImplSelfCheckError(
                    f"{item['acceptance_id']} references non-passing receipts: "
                    + ", ".join(failed_receipts)
                )
    body["acceptance_results"] = results
    body["residual_risks"] = _string_list(body.get("residual_risks"))
    body["evidence_refs"] = _string_list(body.get("evidence_refs"))
    return body


def write_impl_self_check(
    state_dir: Path,
    body: Mapping[str, Any],
    *,
    source_event_id: str,
    created_by: str,
) -> dict[str, Any]:
    stable = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    suffix = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
    ref = (
        "artifacts/impl-self-check/"
        f"{_segment(body.get('workflow_run_id'))}/"
        f"{_segment(body.get('task_id'))}/"
        f"{_segment(body.get('attempt_id'))}-{suffix}.json"
    )
    return write_sidecar_json(
        state_dir,
        ref,
        dict(body),
        kind="impl_self_check",
        schema_version=SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        required=True,
        preview=(
            f"{body.get('task_id')}@{str(body.get('source_commit') or '')[:12]}"
        ),
    )


def hydrate_impl_self_check(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    contract_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
    allow_prior_target: bool = False,
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            dict(descriptor),
            purpose="impl_self_check_dispatch",
            actor="orchestrator",
        )
    except Exception as exc:
        raise ImplSelfCheckError(str(exc)) from exc
    if not isinstance(hydrated.payload, Mapping):
        raise ImplSelfCheckError("impl self-check sidecar is not an object")
    validation_target = target_snapshot
    if allow_prior_target:
        prior_target = str(hydrated.payload.get("target_commit") or "").strip()
        if not prior_target:
            raise ImplSelfCheckError("impl self-check target_commit missing")
        validation_target = {**target_snapshot, "target_commit": prior_target}
    return normalize_impl_self_check(
        {"impl_self_check": hydrated.payload},
        contract_snapshot=contract_snapshot,
        target_snapshot=validation_target,
        expected_attempt_id=str(hydrated.payload.get("attempt_id") or ""),
        strict=True,
    )


def descriptor_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ref = str(payload.get("impl_self_check_ref") or "").strip()
    digest = str(payload.get("impl_self_check_digest") or "").strip()
    if not ref or not digest:
        raise ImplSelfCheckError("impl self-check ref/digest missing")
    return {
        "ref": ref,
        "sha256": digest,
        "kind": "impl_self_check",
        "schema_version": SCHEMA_VERSION,
        "content_type": "application/json",
        "required": True,
    }


def self_check_payload_fields(descriptor: Mapping[str, Any]) -> dict[str, str]:
    return {
        "impl_self_check_ref": str(descriptor.get("ref") or ""),
        "impl_self_check_digest": str(descriptor.get("sha256") or ""),
    }


def reusable_command_receipts(
    body: Mapping[str, Any],
    *,
    contract_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    target_commit = str(target_snapshot.get("target_commit") or "")
    specs = {
        str(item.get("command_id") or ""): item
        for item in contract_snapshot.get("verification_commands") or []
        if isinstance(item, Mapping)
    }
    reusable: list[dict[str, Any]] = []
    for raw in body.get("command_receipts") or []:
        if not isinstance(raw, Mapping) or str(raw.get("status") or "") != "passed":
            continue
        spec = specs.get(str(raw.get("command_id") or ""))
        if not spec or not bool(spec.get("deterministic")) or not bool(spec.get("reusable")):
            continue
        if str(raw.get("target_commit") or "") != target_commit:
            continue
        if str(raw.get("command_digest") or "") != str(spec.get("command_digest") or ""):
            continue
        reusable.append(dict(raw))
    return reusable


def completion_payload_template(
    *,
    contract_snapshot: Mapping[str, Any],
    task_item: Mapping[str, Any],
    task_id: str,
    run_id: str,
    child_id: str,
) -> dict[str, Any]:
    """Return the worker-facing exact-target self-check completion template."""

    attempt_id = str(
        task_item.get("attempt_id")
        or task_item.get("dispatch_id")
        or f"{run_id}:{child_id}"
    )
    commands = contract_snapshot.get("verification_commands") or []
    return {
        "attempt_id": attempt_id,
        "impl_self_check": {
            "schema_version": SCHEMA_VERSION,
            "workflow_run_id": str(contract_snapshot.get("workflow_run_id") or ""),
            "task_id": task_id,
            "attempt_id": attempt_id,
            "contract_revision": str(contract_snapshot.get("contract_revision") or ""),
            "task_map_generation": str(contract_snapshot.get("task_map_generation") or ""),
            "source_commit": "<HEAD commit>",
            "target_commit": "<HEAD commit>",
            "contract_snapshot_ref": str(task_item.get("contract_snapshot_ref") or ""),
            "contract_snapshot_digest": str(
                task_item.get("contract_snapshot_digest") or ""
            ),
            "command_receipts": [
                {
                    "receipt_id": f"receipt-{item.get('command_id')}",
                    "command_id": str(item.get("command_id") or ""),
                    "command_digest": str(item.get("command_digest") or ""),
                    "target_commit": "<HEAD commit>",
                    "status": "passed",
                    "exit_code": 0,
                    "evidence_refs": ["<durable command output or event ref>"],
                }
                for item in commands
                if isinstance(item, Mapping)
            ],
            "acceptance_results": [
                {
                    "acceptance_id": str(item.get("acceptance_id") or ""),
                    "status": "passed",
                    "command_receipt_ids": [
                        f"receipt-{command_id}"
                        for command_id in item.get("verification_command_ids") or []
                    ],
                    "evidence_refs": ["<durable AC evidence ref>"],
                    "residual_risks": [],
                }
                for item in contract_snapshot.get("acceptance_criteria") or []
                if isinstance(item, Mapping)
            ],
            "residual_risks": [],
            "evidence_refs": ["<implementation summary artifact or event ref>"],
        },
    }


def _validate_identity(
    body: Mapping[str, Any],
    *,
    expected: Mapping[str, str],
    expected_attempt_id: str,
) -> None:
    if str(body.get("schema_version") or "") != SCHEMA_VERSION:
        raise ImplSelfCheckError("unsupported impl self-check schema")
    required = (*expected.keys(), "attempt_id")
    missing = [key for key in required if not str(body.get(key) or "").strip()]
    if missing:
        raise ImplSelfCheckError("impl self-check missing: " + ", ".join(missing))
    for key, value in expected.items():
        if value and str(body.get(key) or "") != value:
            raise ImplSelfCheckError(f"impl self-check {key} mismatch")
    if expected_attempt_id and str(body.get("attempt_id") or "") != expected_attempt_id:
        raise ImplSelfCheckError("impl self-check attempt_id mismatch")


def _normalize_receipts(
    raw: Any,
    *,
    command_specs: Mapping[str, Mapping[str, Any]],
    target_commit: str,
    strict: bool,
) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(source):
        if not isinstance(item, Mapping):
            raise ImplSelfCheckError(f"command_receipts[{index}] must be an object")
        receipt = dict(item)
        receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
        command_id = str(receipt.get("command_id") or "").strip()
        spec = command_specs.get(command_id)
        if not receipt_id or receipt_id in seen:
            raise ImplSelfCheckError("command receipt id is missing or duplicated")
        if spec is None:
            raise ImplSelfCheckError(f"unknown command id {command_id!r}")
        seen.add(receipt_id)
        status = _status(receipt.get("status"), receipt=True)
        normalized = {
            "receipt_id": receipt_id,
            "command_id": command_id,
            "command_digest": str(receipt.get("command_digest") or ""),
            "target_commit": str(receipt.get("target_commit") or ""),
            "status": status,
            "exit_code": int(receipt.get("exit_code", 0 if status == "passed" else 1)),
            "evidence_refs": _string_list(receipt.get("evidence_refs")),
        }
        if normalized["command_digest"] != str(spec.get("command_digest") or ""):
            raise ImplSelfCheckError(f"command receipt {receipt_id} digest mismatch")
        if normalized["target_commit"] != target_commit:
            raise ImplSelfCheckError(f"command receipt {receipt_id} target mismatch")
        if strict and status == "passed" and not normalized["evidence_refs"]:
            raise ImplSelfCheckError(f"command receipt {receipt_id} passed without evidence")
        out.append(normalized)
    if strict and command_specs and not out:
        raise ImplSelfCheckError("impl self-check has no command receipts")
    return out


def _normalize_acceptance_results(raw: Any) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(source):
        if not isinstance(item, Mapping):
            raise ImplSelfCheckError(f"acceptance_results[{index}] must be an object")
        acceptance_id = str(item.get("acceptance_id") or "").strip()
        if not acceptance_id or acceptance_id in seen:
            raise ImplSelfCheckError("acceptance result id is missing or duplicated")
        seen.add(acceptance_id)
        out.append({
            "acceptance_id": acceptance_id,
            "status": _status(item.get("status"), receipt=False),
            "command_receipt_ids": _string_list(item.get("command_receipt_ids")),
            "evidence_refs": _string_list(item.get("evidence_refs")),
            "residual_risks": _string_list(item.get("residual_risks")),
        })
    return out


def _status(value: Any, *, receipt: bool) -> str:
    aliases = {"pass": "passed", "fail": "failed"}
    status = aliases.get(str(value or "").strip(), str(value or "").strip())
    allowed = _RECEIPT_STATUSES if receipt else _AC_STATUSES
    if status not in allowed:
        raise ImplSelfCheckError(f"invalid self-check status {status!r}")
    return status


def _string_list(value: Any) -> list[str]:
    source = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(str(item).strip() for item in source if str(item).strip()))


def _segment(value: Any) -> str:
    return _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip("-._") or "unknown"


__all__ = [
    "ImplSelfCheckError",
    "SCHEMA_VERSION",
    "completion_payload_template",
    "descriptor_from_payload",
    "hydrate_impl_self_check",
    "normalize_impl_self_check",
    "reusable_command_receipts",
    "self_check_payload_fields",
    "write_impl_self_check",
]
