"""Mechanical admission checks for contract-bound typed worker results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.impl_self_check import (
    ImplSelfCheckError,
    hydrate_impl_self_check,
    normalize_impl_self_check,
    reusable_command_receipts,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_target_snapshot,
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
    target_descriptor_from_payload,
)
from zf.runtime.verification_result import (
    VerificationResultError,
    validate_verification_result,
    validate_verification_acceptance,
)
from zf.runtime.integration_acceptance_result import (
    IntegrationAcceptanceResultError,
    validate_integration_acceptance_result,
)
from zf.runtime.verification_commands import verification_command_required_for_stage


def typed_result_admission_issues(
    state_dir: Path,
    *,
    schema_version: str,
    result: Mapping[str, Any],
    semantic_submit: bool,
) -> list[dict[str, str]]:
    """Return fail-closed issues for secure, contract-bound result submits.

    Legacy results without a Contract Snapshot remain compatible. A secure
    submit that carries either half of the ref/digest pair is malformed.
    """

    if not semantic_submit:
        return []
    if schema_version == "implementation-result.v1":
        return _implementation_issues(Path(state_dir), result)
    if schema_version == "verification-result.v1":
        return _verification_issues(Path(state_dir), result)
    if schema_version == "task-integration-acceptance-result.v1":
        return _integration_acceptance_issues(Path(state_dir), result)
    return []


def typed_admission_issues_for(
    state_dir: Path,
    adapted: Any,
    semantic_submit: bool,
) -> list[dict[str, str]]:
    return typed_result_admission_issues(
        state_dir,
        schema_version=str(getattr(adapted, "schema_version", "") or ""),
        result=getattr(adapted, "payload", {}) or {},
        semantic_submit=semantic_submit,
    )


def _implementation_issues(
    state_dir: Path,
    result: Mapping[str, Any],
) -> list[dict[str, str]]:
    descriptor, descriptor_issue = _contract_descriptor(result)
    if descriptor_issue:
        return [descriptor_issue]
    if not descriptor:
        return []
    try:
        contract = hydrate_task_contract_snapshot(
            state_dir,
            descriptor,
            expected={
                "workflow_run_id": str(result.get("workflow_run_id") or ""),
                "task_id": str(result.get("task_id") or ""),
                "contract_revision": str(result.get("contract_revision") or ""),
                "task_map_generation": str(
                    result.get("task_map_generation") or ""
                ),
            },
        )
    except TaskContractSnapshotError as exc:
        return [_plan_contract_issue(exc)]

    execution_status = str(result.get("execution_status") or "completed")
    verdict = str(result.get("verdict") or "passed")
    if execution_status == "failed" or verdict in {"rejected", "blocked", "abstained"}:
        return []
    if not _strings(result.get("evidence_refs")):
        return [_issue(
            field="control_result.evidence_refs",
            code="implementation_evidence_missing",
            message="passed implementation result requires durable evidence_refs",
            owner="implementation_owner",
            action="result_repair",
        )]

    self_check = result.get("self_check")
    if not isinstance(self_check, Mapping):
        return [_issue(
            field="control_result.self_check",
            code="impl_self_check_missing",
            message="passed implementation result requires impl-self-check.v1",
            owner="implementation_owner",
            action="result_repair",
        )]
    try:
        target = build_target_snapshot(
            descriptor,
            target_commit=str(result.get("target_commit") or ""),
            contract_snapshot=contract,
        )
        normalize_impl_self_check(
            {"impl_self_check": dict(self_check)},
            contract_snapshot=contract,
            target_snapshot=target,
            expected_attempt_id=str(result.get("attempt_id") or ""),
            strict=True,
        )
    except (ImplSelfCheckError, TaskContractSnapshotError) as exc:
        message = str(exc)
        code = (
            "worker_result_command_unknown"
            if "unknown command id" in message
            else "impl_self_check_invalid"
        )
        return [_issue(
            field="control_result.self_check",
            code=code,
            message=message,
            owner="implementation_owner",
            action="result_repair",
        )]
    return []


def _verification_issues(
    state_dir: Path,
    result: Mapping[str, Any],
) -> list[dict[str, str]]:
    descriptor, descriptor_issue = _contract_descriptor(result)
    if descriptor_issue:
        return [descriptor_issue]
    if not descriptor:
        return []
    try:
        contract = hydrate_task_contract_snapshot(
            state_dir,
            descriptor,
            expected={
                "workflow_run_id": str(result.get("workflow_run_id") or ""),
                "task_id": str(result.get("task_id") or ""),
                "contract_revision": str(result.get("contract_revision") or ""),
                "task_map_generation": str(
                    result.get("task_map_generation") or ""
                ),
            },
        )
        target = hydrate_target_snapshot(
            state_dir,
            target_descriptor_from_payload(result),
            expected={
                "contract_snapshot_ref": str(descriptor.get("ref") or ""),
                "contract_snapshot_digest": str(descriptor.get("sha256") or ""),
                "target_commit": str(result.get("target_commit") or ""),
                "task_id": str(contract.get("task_id") or ""),
            },
        )
    except TaskContractSnapshotError as exc:
        return [_issue(
            field="control_result.target_snapshot_ref",
            code="verification_target_invalid",
            message=str(exc),
            owner="task_verify",
            action="result_repair",
        )]

    expected_owner = str(
        contract.get("verification_owner") or "task_verify"
    )
    actual_owner = str(result.get("verification_owner") or "task_verify")
    if actual_owner != expected_owner:
        return [_issue(
            field="control_result.verification_owner",
            code="verification_owner_mismatch",
            message=(
                f"verification owner {actual_owner!r} does not match "
                f"contract route {expected_owner!r}"
            ),
            owner=expected_owner,
            action="result_repair",
        )]

    try:
        validate_verification_result(result, strict=True)
        validate_verification_acceptance(result, contract)
    except VerificationResultError as exc:
        message = str(exc)
        if "report evidence_refs" in message:
            code = "verification_report_evidence_missing"
        elif "probe_receipts" in message and "evidence" in message:
            code = "verification_probe_evidence_missing"
        elif "command or probe receipt" in message:
            code = "verification_command_evidence_missing"
        else:
            code = "verification_result_invalid"
        return [_issue(
            field="control_result.verification_result",
            code=code,
            message=message,
            owner="task_verify",
            action="result_repair",
        )]

    reused_ids = set(_strings(result.get("reused_command_receipt_ids")))
    reused_receipts: list[dict[str, Any]] = []
    if reused_ids:
        ref = str(result.get("impl_self_check_ref") or "").strip()
        digest = str(result.get("impl_self_check_digest") or "").strip()
        if not ref or not digest:
            return [_issue(
                field="control_result.reused_command_receipt_ids",
                code="verification_reused_receipt_source_missing",
                message=(
                    "reused command receipts require an exact impl self-check "
                    "ref/digest"
                ),
                owner="task_verify",
                action="result_repair",
            )]
        try:
            self_check = hydrate_impl_self_check(
                state_dir,
                {"ref": ref, "sha256": digest},
                contract_snapshot=contract,
                target_snapshot=target,
            )
            reusable = reusable_command_receipts(
                self_check,
                contract_snapshot=contract,
                target_snapshot=target,
            )
            reusable_by_id = {
                str(item.get("receipt_id") or ""): item
                for item in reusable
                if str(item.get("receipt_id") or "")
            }
        except Exception as exc:
            return [_issue(
                field="control_result.impl_self_check_ref",
                code="verification_reused_receipt_source_invalid",
                message=str(exc),
                owner="task_verify",
                action="result_repair",
            )]
        unknown = sorted(reused_ids - set(reusable_by_id))
        if unknown:
            return [_issue(
                field="control_result.reused_command_receipt_ids",
                code="verification_reused_receipt_unknown",
                message="unknown or non-reusable impl receipt ids: " + ", ".join(unknown),
                owner="task_verify",
                action="result_repair",
            )]
        reused_receipts = [reusable_by_id[item] for item in sorted(reused_ids)]

    if str(result.get("verdict") or "") != "passed":
        return []
    command_issue = _passed_verification_command_issue(
        result,
        contract=contract,
        target=target,
        reused_receipts=reused_receipts,
        verification_owner=expected_owner,
    )
    return [command_issue] if command_issue else []


def _passed_verification_command_issue(
    result: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    target: Mapping[str, Any],
    reused_receipts: list[dict[str, Any]],
    verification_owner: str,
) -> dict[str, str] | None:
    specs = {
        str(item.get("command_id") or ""): item
        for item in contract.get("verification_commands") or []
        if isinstance(item, Mapping) and str(item.get("command_id") or "")
    }
    required = {
        command_id
        for criterion in contract.get("acceptance_criteria") or []
        if isinstance(criterion, Mapping)
        and bool(criterion.get("mandatory", True))
        for command_id in _strings(criterion.get("verification_command_ids"))
        if command_id in specs
        and verification_command_required_for_stage(
            specs[command_id],
            verification_owner=verification_owner,
            task_id=str(contract.get("task_id") or ""),
        )
    }
    covered = {
        str(item.get("command_id") or "")
        for item in reused_receipts
        if str(item.get("command_id") or "")
    }
    probes = result.get("probe_receipts")
    probes = probes if isinstance(probes, list) else []
    expected_target = str(target.get("target_commit") or "")
    for index, raw in enumerate(probes):
        if not isinstance(raw, Mapping):
            continue
        command_id = str(raw.get("command_id") or "").strip()
        if not command_id:
            continue
        spec = specs.get(command_id)
        if spec is None:
            return _issue(
                field=f"control_result.probe_receipts[{index}].command_id",
                code="verification_probe_command_unknown",
                message=f"probe references unknown canonical command {command_id!r}",
                owner="task_verify",
                action="result_repair",
            )
        bindings = {
            "command": str(spec.get("command") or ""),
            "command_digest": str(spec.get("command_digest") or ""),
            "target_commit": expected_target,
        }
        mismatches = [
            key
            for key, expected in bindings.items()
            if str(raw.get(key) or "") != expected
        ]
        if mismatches:
            return _issue(
                field=f"control_result.probe_receipts[{index}]",
                code="verification_probe_command_mismatch",
                message=(
                    f"canonical command {command_id!r} receipt mismatch: "
                    + ", ".join(mismatches)
                ),
                owner="task_verify",
                action="result_repair",
            )
        exit_code = raw.get("exit_code")
        if (
            str(raw.get("status") or "") != "passed"
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or exit_code != 0
        ):
            return _issue(
                field=f"control_result.probe_receipts[{index}]",
                code="verification_probe_command_not_passed",
                message=(
                    f"passed verdict requires canonical command {command_id!r} "
                    "to have status=passed and exit_code=0"
                ),
                owner="task_verify",
                action="result_repair",
            )
        covered.add(command_id)
    missing = sorted(required - covered)
    if missing:
        return _issue(
            field="control_result.probe_receipts",
            code="verification_canonical_command_coverage_missing",
            message=(
                "passed verdict lacks exact-target passing receipts for canonical "
                "commands: " + ", ".join(missing)
            ),
            owner="task_verify",
            action="result_repair",
        )
    return None


def _integration_acceptance_issues(
    state_dir: Path,
    result: Mapping[str, Any],
) -> list[dict[str, str]]:
    try:
        validate_integration_acceptance_result(
            result,
            require_read_ledger=True,
        )
    except IntegrationAcceptanceResultError as exc:
        return [_issue(
            field="control_result.integration_acceptance_result",
            code="integration_acceptance_result_invalid",
            message=str(exc),
            owner="integration_acceptance_reviewer",
            action="result_repair",
        )]
    descriptor, descriptor_issue = _contract_descriptor(result)
    if descriptor_issue:
        return [descriptor_issue]
    if not descriptor:
        return [_issue(
            field="control_result.contract_snapshot_ref",
            code="canonical_plan_contract_ref_incomplete",
            message="risk acceptance requires an exact Task Contract snapshot",
            owner="planner",
            action="return_to_plan",
        )]
    try:
        contract = hydrate_task_contract_snapshot(
            state_dir,
            descriptor,
            expected={
                "workflow_run_id": str(result.get("workflow_run_id") or ""),
                "task_id": str(result.get("task_id") or ""),
                "contract_revision": str(result.get("contract_revision") or ""),
                "task_map_generation": str(
                    result.get("task_map_generation") or ""
                ),
            },
        )
        hydrate_target_snapshot(
            state_dir,
            target_descriptor_from_payload(result),
            expected={
                "contract_snapshot_ref": str(descriptor.get("ref") or ""),
                "contract_snapshot_digest": str(descriptor.get("sha256") or ""),
                "target_commit": str(
                    result.get("exact_task_target_commit") or ""
                ),
                "task_id": str(contract.get("task_id") or ""),
            },
        )
    except TaskContractSnapshotError as exc:
        return [_issue(
            field="control_result.target_snapshot_ref",
            code="integration_acceptance_target_invalid",
            message=str(exc),
            owner="integration_acceptance_reviewer",
            action="result_repair",
        )]
    if (
        str(contract.get("risk_class") or "")
        != str(result.get("risk_class") or "")
        or str(contract.get("integration_admission_profile") or "")
        != str(result.get("integration_admission_profile") or "")
    ):
        return [_issue(
            field="control_result.integration_admission_profile",
            code="integration_acceptance_contract_mismatch",
            message="risk/profile facts differ from the admitted Task Contract",
            owner="planner",
            action="return_to_plan",
        )]
    verification_descriptor = {
        "ref": str(result.get("verification_result_ref") or ""),
        "sha256": str(result.get("verification_result_digest") or ""),
    }
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            verification_descriptor,
            purpose="integration_acceptance_admission",
            actor="call-result-admission",
        )
        verification = (
            hydrated.payload
            if isinstance(hydrated.payload, Mapping)
            else {}
        )
        validate_verification_result(verification, strict=True)
    except Exception as exc:
        return [_issue(
            field="control_result.verification_result_ref",
            code="integration_acceptance_verification_invalid",
            message=str(exc),
            owner="task_verify",
            action="reverify",
        )]
    expected = {
        "workflow_run_id": str(result.get("workflow_run_id") or ""),
        "task_id": str(result.get("task_id") or ""),
        "contract_revision": str(result.get("contract_revision") or ""),
        "task_map_generation": str(result.get("task_map_generation") or ""),
        "target_commit": str(result.get("exact_task_target_commit") or ""),
    }
    mismatch = [
        key for key, value in expected.items()
        if str(verification.get(key) or "") != value
    ]
    if mismatch or str(verification.get("verdict") or "") != "passed":
        return [_issue(
            field="control_result.verification_result_ref",
            code="integration_acceptance_verification_not_admitted",
            message=(
                "verification result must be current and passed; mismatch: "
                + ", ".join(mismatch)
            ),
            owner="task_verify",
            action="reverify",
        )]
    return []


def _contract_descriptor(
    result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    ref = str(result.get("contract_snapshot_ref") or "").strip()
    digest = str(result.get("contract_snapshot_digest") or "").strip()
    if not ref and not digest:
        return {}, None
    if not ref or not digest:
        return {}, _issue(
            field="control_result.contract_snapshot_ref",
            code="canonical_plan_contract_ref_incomplete",
            message="contract snapshot requires an exact ref/digest pair",
            owner="planner",
            action="return_to_plan",
        )
    try:
        return contract_descriptor_from_payload(result), None
    except TaskContractSnapshotError as exc:
        return {}, _plan_contract_issue(exc)


def _plan_contract_issue(exc: Exception) -> dict[str, str]:
    message = str(exc)
    code = (
        "canonical_plan_command_unknown"
        if "unknown command ids" in message
        else "canonical_plan_contract_invalid"
    )
    return _issue(
        field="control_result.contract_snapshot_ref",
        code=code,
        message=message,
        owner="planner",
        action="return_to_plan",
    )


def _issue(
    *,
    field: str,
    code: str,
    message: str,
    owner: str,
    action: str,
) -> dict[str, str]:
    return {
        "field": field,
        "code": code,
        "message": message,
        "failure_owner": "canonical_plan" if owner == "planner" else "worker_result",
        "recovery_owner": owner,
        "recovery_action": action,
    }


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return [str(item).strip() for item in values if str(item).strip()]


__all__ = ["typed_admission_issues_for", "typed_result_admission_issues"]
