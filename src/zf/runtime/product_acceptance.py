"""Mechanical Product Acceptance artifact contracts and admission binding."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


PRODUCT_ACCEPTANCE_SPEC_SCHEMA = "product_acceptance_spec.v1"
PRODUCT_ACCEPTANCE_REPORT_SCHEMA = "product_acceptance_report.v1"
PROVIDER_QUALIFICATION_RECEIPT_SCHEMA = "provider-qualification-receipt.v1"
PRODUCT_ACCEPTANCE_PORT = "product_acceptance_spec"
PRODUCT_ACCEPTANCE_HANDOFF_KEYS = (
    "product_acceptance_required",
    "product_acceptance_spec_ref",
    "product_acceptance_spec_digest",
    "product_acceptance_report_ref",
    "product_acceptance_report_digest",
    "product_acceptance_verdict",
    "provider_qualification_required",
    "provider_qualification_status",
)


class ProductAcceptanceError(ValueError):
    """A Product Acceptance artifact cannot be admitted mechanically."""


def product_acceptance_handoff_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an all-or-none Product identity for downstream operations."""

    if not bool(payload.get("product_acceptance_required")):
        return {}
    return {
        key: payload[key]
        for key in PRODUCT_ACCEPTANCE_HANDOFF_KEYS
        if key in payload and payload[key] not in (None, "")
    }


def validate_product_acceptance_spec(
    spec: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
) -> None:
    if str(spec.get("schema_version") or "") != PRODUCT_ACCEPTANCE_SPEC_SCHEMA:
        raise ProductAcceptanceError("unsupported Product Acceptance spec schema")
    _require_identity(
        spec,
        (
            "workflow_run_id",
            "flow_kind",
            "plan_revision",
            "task_map_generation",
            "assembly_owner",
        ),
        subject="Product Acceptance spec",
    )
    if str(spec.get("flow_kind") or "") not in {"issue", "prd", "refactor"}:
        raise ProductAcceptanceError("Product Acceptance spec has invalid flow_kind")
    _check_expected(spec, expected or {}, subject="Product Acceptance spec")

    entrypoints = _objects(spec.get("entrypoints"))
    if not entrypoints:
        raise ProductAcceptanceError("Product Acceptance spec requires entrypoints")
    _validate_unique_rows(
        entrypoints,
        id_key="entrypoint_id",
        required=("entrypoint_id", "start_command", "health_check", "owner"),
        subject="entrypoint",
    )

    journeys = _objects(spec.get("user_journeys"))
    if not journeys:
        raise ProductAcceptanceError("Product Acceptance spec requires user_journeys")
    _validate_unique_rows(
        journeys,
        id_key="journey_id",
        required=("journey_id", "title", "verification_tier"),
        subject="user journey",
    )
    for index, journey in enumerate(journeys):
        tier = str(journey.get("verification_tier") or "")
        if tier not in {"runtime", "e2e", "manual_evidence"}:
            raise ProductAcceptanceError(
                f"user_journeys[{index}] has invalid verification_tier"
            )
        assertions = _objects(journey.get("assertions"))
        if not assertions:
            raise ProductAcceptanceError(
                f"user_journeys[{index}] requires observable assertions"
            )
        _validate_unique_rows(
            assertions,
            id_key="assertion_id",
            required=("assertion_id", "statement", "observation_kind"),
            subject=f"user_journeys[{index}] assertion",
        )
        for assertion in assertions:
            if str(assertion.get("observation_kind") or "") not in {
                "api",
                "cli",
                "dom",
                "interaction",
                "state",
                "visual",
            }:
                raise ProductAcceptanceError(
                    f"user_journeys[{index}] assertion has invalid observation_kind"
                )

    provider = _mapping(spec.get("provider_qualification"))
    if bool(provider.get("required_for_goal")):
        providers = _strings(provider.get("providers"))
        if not providers:
            raise ProductAcceptanceError(
                "required provider qualification must declare providers"
            )
        if int(provider.get("ttl_seconds") or 0) <= 0:
            raise ProductAcceptanceError(
                "required provider qualification must declare ttl_seconds"
            )


def validate_provider_qualification_receipt(
    receipt: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    ttl_seconds: int = 0,
) -> None:
    if str(receipt.get("schema_version") or "") != (
        PROVIDER_QUALIFICATION_RECEIPT_SCHEMA
    ):
        raise ProductAcceptanceError("unsupported provider qualification schema")
    _require_identity(
        receipt,
        ("provider", "workflow_run_id", "target_commit", "status", "observed_at", "expires_at"),
        subject="provider qualification receipt",
    )
    if str(receipt.get("status") or "") not in {"passed", "failed", "blocked"}:
        raise ProductAcceptanceError("provider qualification receipt has invalid status")
    if receipt.get("deterministic") is not False:
        raise ProductAcceptanceError("provider qualification must be deterministic=false")
    if receipt.get("reusable") is not False:
        raise ProductAcceptanceError("provider qualification must be reusable=false")
    observed = _timestamp(receipt.get("observed_at"), field="observed_at")
    expires = _timestamp(receipt.get("expires_at"), field="expires_at")
    if expires <= observed:
        raise ProductAcceptanceError("provider qualification expires_at must follow observed_at")
    if ttl_seconds > 0 and (expires - observed).total_seconds() > ttl_seconds:
        raise ProductAcceptanceError(
            "provider qualification expires_at exceeds policy ttl_seconds"
        )
    _check_expected(receipt, expected or {}, subject="provider qualification receipt")
    _validate_evidence_refs(
        receipt.get("evidence_refs"),
        subject="provider qualification receipt",
    )


def validate_product_acceptance_report(
    report: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if str(report.get("schema_version") or "") != PRODUCT_ACCEPTANCE_REPORT_SCHEMA:
        raise ProductAcceptanceError("unsupported Product Acceptance report schema")
    _require_identity(
        report,
        (
            "workflow_run_id",
            "task_map_generation",
            "candidate_ref",
            "target_commit",
            "product_acceptance_spec_ref",
            "product_acceptance_spec_digest",
            "verdict",
        ),
        subject="Product Acceptance report",
    )
    _check_expected(report, expected, subject="Product Acceptance report")
    verdict = str(report.get("verdict") or "")
    if verdict not in {"passed", "rejected", "blocked"}:
        raise ProductAcceptanceError("Product Acceptance report has invalid verdict")

    expected_journeys = {
        str(item.get("journey_id") or ""): item
        for item in _objects(spec.get("user_journeys"))
    }
    results = _objects(report.get("journey_results"))
    _validate_unique_rows(
        results,
        id_key="journey_id",
        required=("journey_id", "status"),
        subject="journey result",
    )
    actual_ids = {str(item.get("journey_id") or "") for item in results}
    mandatory_ids = {
        journey_id
        for journey_id, journey in expected_journeys.items()
        if bool(journey.get("mandatory", True))
    }
    missing = sorted(mandatory_ids - actual_ids)
    unknown = sorted(actual_ids - set(expected_journeys))
    if missing:
        raise ProductAcceptanceError(
            "Product Acceptance report misses mandatory journeys: " + ", ".join(missing)
        )
    if unknown:
        raise ProductAcceptanceError(
            "Product Acceptance report has unknown journeys: " + ", ".join(unknown)
        )

    statuses: set[str] = set()
    provider_policy = _mapping(spec.get("provider_qualification"))
    for index, result in enumerate(results):
        status = str(result.get("status") or "")
        if status not in {"passed", "failed", "blocked", "waived"}:
            raise ProductAcceptanceError(
                f"journey_results[{index}] has invalid status"
            )
        statuses.add(status)
        _validate_evidence_refs(
            result.get("evidence_refs"),
            subject=f"journey_results[{index}]",
        )
        journey = expected_journeys[str(result.get("journey_id") or "")]
        expected_assertions = {
            str(item.get("assertion_id") or ""): item
            for item in _objects(journey.get("assertions"))
        }
        assertion_results = _objects(result.get("assertion_results"))
        _validate_unique_rows(
            assertion_results,
            id_key="assertion_id",
            required=("assertion_id", "status"),
            subject=f"journey_results[{index}] assertion result",
        )
        actual_assertions = {
            str(item.get("assertion_id") or "") for item in assertion_results
        }
        required_assertions = {
            assertion_id
            for assertion_id, assertion in expected_assertions.items()
            if bool(assertion.get("mandatory", True))
        }
        if required_assertions - actual_assertions:
            raise ProductAcceptanceError(
                f"journey_results[{index}] misses mandatory assertions"
            )
        if actual_assertions - set(expected_assertions):
            raise ProductAcceptanceError(
                f"journey_results[{index}] has unknown assertions"
            )
        assertion_statuses: set[str] = set()
        for assertion in assertion_results:
            assertion_status = str(assertion.get("status") or "")
            if assertion_status not in {"passed", "failed", "blocked", "waived"}:
                raise ProductAcceptanceError(
                    f"journey_results[{index}] assertion has invalid status"
                )
            assertion_statuses.add(assertion_status)
            _validate_evidence_refs(
                assertion.get("evidence_refs"),
                subject=f"journey_results[{index}] assertion",
            )
        if status == "passed" and assertion_statuses - {"passed", "waived"}:
            raise ProductAcceptanceError(
                f"journey_results[{index}] passed with failed assertions"
            )
        if status == "failed" and "failed" not in assertion_statuses:
            raise ProductAcceptanceError(
                f"journey_results[{index}] failed without a failed assertion"
            )
        if status == "blocked" and "blocked" not in assertion_statuses:
            raise ProductAcceptanceError(
                f"journey_results[{index}] blocked without a blocked assertion"
            )

    if verdict == "passed" and statuses - {"passed", "waived"}:
        raise ProductAcceptanceError("passed Product Acceptance contains failed journeys")
    if verdict == "rejected" and "failed" not in statuses:
        raise ProductAcceptanceError("rejected Product Acceptance requires a failed journey")
    if verdict == "blocked" and "blocked" not in statuses:
        raise ProductAcceptanceError("blocked Product Acceptance requires a blocked journey")

    receipt_bodies = _objects(report.get("provider_qualification_receipts"))
    seen_providers: set[str] = set()
    for receipt in receipt_bodies:
        provider = str(receipt.get("provider") or "")
        if provider in seen_providers:
            raise ProductAcceptanceError(
                f"duplicate provider qualification receipt for {provider!r}"
            )
        seen_providers.add(provider)
        validate_provider_qualification_receipt(
            receipt,
            expected={
                "workflow_run_id": expected.get("workflow_run_id"),
                "target_commit": expected.get("target_commit"),
            },
            ttl_seconds=int(provider_policy.get("ttl_seconds") or 0),
        )


def product_acceptance_binding_from_package(
    state_dir: Path,
    package_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    if not str(package_descriptor.get("ref") or ""):
        return {"required": False}
    package = hydrate_plan_artifact_package(state_dir, package_descriptor)
    port = next(
        (
            dict(item)
            for item in [*package.get("produced", []), *package.get("inherited", [])]
            if isinstance(item, Mapping)
            and str(item.get("logical_name") or "") == PRODUCT_ACCEPTANCE_PORT
        ),
        {},
    )
    required = PRODUCT_ACCEPTANCE_PORT in {
        str(item) for item in package.get("required_ports") or []
    }
    if not port:
        return {"required": required, "package": package}
    hydrated = hydrate_sidecar_ref(
        state_dir,
        {"ref": str(port.get("ref") or ""), "sha256": str(port.get("sha256") or "")},
    )
    spec = hydrated.payload if isinstance(hydrated.payload, Mapping) else {}
    validate_product_acceptance_spec(
        spec,
        expected={
            "workflow_run_id": package.get("workflow_run_id"),
            "flow_kind": package.get("flow_kind"),
            "plan_revision": package.get("plan_revision"),
            "task_map_generation": package.get("task_map_generation"),
        },
    )
    return {
        "required": required,
        "package": package,
        "spec": dict(spec),
        "spec_ref": str(port.get("ref") or ""),
        "spec_digest": str(port.get("sha256") or ""),
    }


def product_acceptance_report_template(
    *,
    spec: Mapping[str, Any],
    spec_ref: str,
    spec_digest: str,
    candidate_ref: str,
    target_commit: str,
) -> dict[str, Any]:
    evidence = {"ref": "<durable evidence sidecar ref>", "sha256": "<sha256>"}
    return {
        "schema_version": PRODUCT_ACCEPTANCE_REPORT_SCHEMA,
        "workflow_run_id": str(spec.get("workflow_run_id") or ""),
        "task_map_generation": str(spec.get("task_map_generation") or ""),
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
        "product_acceptance_spec_ref": spec_ref,
        "product_acceptance_spec_digest": spec_digest,
        "verdict": "passed",
        "journey_results": [
            {
                "journey_id": str(journey.get("journey_id") or ""),
                "status": "passed",
                "evidence_refs": [dict(evidence)],
                "assertion_results": [
                    {
                        "assertion_id": str(assertion.get("assertion_id") or ""),
                        "status": "passed",
                        "evidence_refs": [dict(evidence)],
                    }
                    for assertion in _objects(journey.get("assertions"))
                ],
            }
            for journey in _objects(spec.get("user_journeys"))
        ],
        "provider_qualification_receipts": [],
    }


def bind_product_acceptance_report(
    adapted: Any,
    *,
    state_dir: Path,
    operation: Mapping[str, Any] | None,
    source_event_id: str,
    persist: bool = True,
) -> Any:
    """Bind Candidate Verify and discovery to the exact full-product report.

    Preflight can validate without materializing refs.
    """

    schema = str(getattr(adapted, "schema_version", ""))
    operation = operation or {}
    is_discovery = (
        schema == "workflow-read-result.v1"
        and operation.get("output_profile_id") == "workflow-read"
        and operation.get("canonical_success_event") == "flow.discovery.child.completed"
    )
    if schema != "verification-result.v1" and not is_discovery:
        return adapted
    result = dict(getattr(adapted, "payload", {}) or {})
    # Discovery may report gaps or infrastructure failure without pretending
    # that a complete product evaluation ran. Only a successful closeout must
    # provide the full report; explicit reports still undergo validation.
    if (
        is_discovery
        and result.get("verdict") != "passed"
        and not isinstance(result.get("product_acceptance_report"), Mapping)
    ):
        return adapted
    identity = _mapping(operation.get("result_identity"))
    package_ref = str(
        result.get("plan_artifact_package_ref")
        or identity.get("plan_artifact_package_ref")
        or ""
    )
    package_digest = str(
        result.get("plan_artifact_package_digest")
        or identity.get("plan_artifact_package_digest")
        or ""
    )
    is_candidate = (
        is_discovery
        or str(operation.get("output_profile_id") or "") == "candidate-verify"
        or str(result.get("verification_owner") or "") == "candidate_verify"
    )
    if not package_ref or not package_digest:
        return adapted
    try:
        binding = product_acceptance_binding_from_package(
            Path(state_dir),
            {"ref": package_ref, "sha256": package_digest},
        )
        if not binding.get("spec"):
            if is_candidate and binding.get("required"):
                raise ProductAcceptanceError(
                    "Candidate Verify requires product_acceptance_spec"
                )
            return adapted
        if not is_candidate:
            return adapted
        raw_report = result.get("product_acceptance_report")
        if not isinstance(raw_report, Mapping) and result.get("product_acceptance_report_ref"):
            raw_report = dict(hydrate_sidecar_ref(Path(state_dir), {
                "ref": result["product_acceptance_report_ref"],
                "sha256": str(result.get("product_acceptance_report_digest") or ""),
            }).payload)
            raw_report["provider_qualification_receipts"] = [
                dict(hydrate_sidecar_ref(Path(state_dir), descriptor).payload)
                for descriptor in _objects(raw_report.pop("provider_qualification_receipt_refs", []))
            ]
        if not isinstance(raw_report, Mapping):
            if binding.get("required"):
                raise ProductAcceptanceError(
                    "Candidate Verify requires product_acceptance_report"
                )
            return adapted
        spec = _mapping(binding.get("spec"))
        expected = {
            "workflow_run_id": str(binding["package"].get("workflow_run_id") or ""),
            "task_map_generation": str(binding["package"].get("task_map_generation") or ""),
            "candidate_ref": str(result.get("candidate_ref") or identity.get("candidate_ref") or ""),
            "target_commit": str(result.get("target_commit") or identity.get("target_commit") or ""),
            "product_acceptance_spec_ref": str(binding.get("spec_ref") or ""),
            "product_acceptance_spec_digest": str(binding.get("spec_digest") or ""),
        }
        missing_expected = [
            field
            for field in (
                "workflow_run_id",
                "task_map_generation",
                "candidate_ref",
                "target_commit",
                "product_acceptance_spec_ref",
                "product_acceptance_spec_digest",
            )
            if not str(expected.get(field) or "").strip()
        ]
        if missing_expected:
            raise ProductAcceptanceError(
                "Candidate Verify lacks Product Acceptance authority: "
                + ", ".join(missing_expected)
            )
        report = dict(raw_report)
        validate_product_acceptance_report(report, spec=spec, expected=expected)
        verification_verdict = str(result.get("verdict") or "")
        product_verdict = str(report.get("verdict") or "")
        if verification_verdict == "passed" and product_verdict != "passed":
            raise ProductAcceptanceError(
                "passed Candidate Verify requires passed Product Acceptance"
            )
        if verification_verdict == "rejected" and product_verdict == "passed":
            raise ProductAcceptanceError(
                "rejected Candidate Verify cannot carry passed Product Acceptance"
            )

        if not persist:
            return adapted

        receipt_refs: list[dict[str, Any]] = []
        for receipt in _objects(report.get("provider_qualification_receipts")):
            receipt_refs.append(write_immutable_json_sidecar(
                Path(state_dir),
                receipt,
                root="provider-qualification/receipts",
                kind="provider_qualification_receipt",
                schema_version=PROVIDER_QUALIFICATION_RECEIPT_SCHEMA,
                created_by="candidate-verify-admission",
                source_event_id=source_event_id,
            ))
        persisted_report = dict(report)
        persisted_report.pop("provider_qualification_receipts", None)
        persisted_report["provider_qualification_receipt_refs"] = receipt_refs
        report_descriptor = write_immutable_json_sidecar(
            Path(state_dir),
            persisted_report,
            root="product-acceptance/reports",
            kind="product_acceptance_report",
            schema_version=PRODUCT_ACCEPTANCE_REPORT_SCHEMA,
            created_by="candidate-verify-admission",
            source_event_id=source_event_id,
        )
        provider = provider_qualification_status(
            Path(state_dir), spec=spec, report=persisted_report
        )
        result.pop("product_acceptance_report", None)
        result.update({
            "product_acceptance_required": bool(binding.get("required")),
            "product_acceptance_spec_ref": str(binding.get("spec_ref") or ""),
            "product_acceptance_spec_digest": str(binding.get("spec_digest") or ""),
            "product_acceptance_report_ref": str(report_descriptor.get("ref") or ""),
            "product_acceptance_report_digest": str(report_descriptor.get("sha256") or ""),
            "product_acceptance_verdict": product_verdict,
            "provider_qualification_required": bool(provider["required"]),
            "provider_qualification_status": str(provider["status"]),
            "provider_qualification_receipt_refs": receipt_refs,
        })
        descriptor = write_immutable_json_sidecar(
            Path(state_dir),
            result,
            root=f"call-results/control/{schema}",
            kind="call_control_result",
            schema_version=schema,
            created_by="call-result-admission:product-acceptance-binding",
            source_event_id=source_event_id,
        )
        return replace(adapted, payload=result, descriptor=descriptor)
    except (OSError, ValueError) as exc:
        return replace(
            adapted,
            issues=(
                *tuple(getattr(adapted, "issues", ()) or ()),
                {
                    "field": "control_result.product_acceptance_report",
                    "code": "product_acceptance_invalid",
                    "message": str(exc),
                },
            ),
        )


def provider_qualification_status(
    state_dir: Path,
    *,
    spec: Mapping[str, Any],
    report: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    policy = _mapping(spec.get("provider_qualification"))
    required = bool(policy.get("required_for_goal"))
    providers = set(_strings(policy.get("providers")))
    if not required:
        return {"required": False, "status": "not_required", "providers": []}
    receipts: dict[str, dict[str, Any]] = {}
    for descriptor in _objects(report.get("provider_qualification_receipt_refs")):
        hydrated = hydrate_sidecar_ref(Path(state_dir), descriptor)
        receipt = hydrated.payload if isinstance(hydrated.payload, Mapping) else {}
        validate_provider_qualification_receipt(
            receipt,
            expected={
                "workflow_run_id": spec.get("workflow_run_id"),
                "target_commit": report.get("target_commit"),
            },
            ttl_seconds=int(policy.get("ttl_seconds") or 0),
        )
        receipts[str(receipt.get("provider") or "")] = dict(receipt)
    missing = sorted(providers - set(receipts))
    if missing:
        return {"required": True, "status": "missing", "providers": sorted(providers)}
    current = now or datetime.now(timezone.utc)
    for provider in sorted(providers):
        receipt = receipts[provider]
        if _timestamp(receipt.get("expires_at"), field="expires_at") <= current:
            return {"required": True, "status": "expired", "providers": sorted(providers)}
        if str(receipt.get("status") or "") != "passed":
            return {"required": True, "status": "waiting_external", "providers": sorted(providers)}
    return {"required": True, "status": "passed", "providers": sorted(providers)}


def _validate_evidence_refs(value: Any, *, subject: str) -> None:
    refs = _objects(value)
    if not refs:
        raise ProductAcceptanceError(f"{subject} requires evidence_refs")
    for descriptor in refs:
        digest = str(descriptor.get("sha256") or "")
        if (
            not str(descriptor.get("ref") or "")
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ProductAcceptanceError(
                f"{subject} evidence requires exact ref and sha256"
            )


def _validate_unique_rows(
    rows: list[dict[str, Any]],
    *,
    id_key: str,
    required: tuple[str, ...],
    subject: str,
) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows):
        missing = [key for key in required if not str(row.get(key) or "").strip()]
        if missing:
            raise ProductAcceptanceError(
                f"{subject}[{index}] missing: {', '.join(missing)}"
            )
        identity = str(row.get(id_key) or "")
        if identity in seen:
            raise ProductAcceptanceError(f"duplicate {subject} id {identity!r}")
        seen.add(identity)


def _require_identity(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
    *,
    subject: str,
) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "", [], {})]
    if missing:
        raise ProductAcceptanceError(f"{subject} missing: {', '.join(missing)}")


def _check_expected(
    payload: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    subject: str,
) -> None:
    for field, expected_value in expected.items():
        if expected_value in (None, ""):
            continue
        actual = str(payload.get(field) or "")
        if actual != str(expected_value):
            raise ProductAcceptanceError(
                f"{subject} {field} mismatch: expected {expected_value}, got {actual or '<missing>'}"
            )


def _timestamp(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductAcceptanceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ProductAcceptanceError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _objects(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, Mapping)] if isinstance(value, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


__all__ = [
    "PRODUCT_ACCEPTANCE_HANDOFF_KEYS",
    "PRODUCT_ACCEPTANCE_PORT",
    "PRODUCT_ACCEPTANCE_REPORT_SCHEMA",
    "PRODUCT_ACCEPTANCE_SPEC_SCHEMA",
    "PROVIDER_QUALIFICATION_RECEIPT_SCHEMA",
    "ProductAcceptanceError",
    "bind_product_acceptance_report",
    "product_acceptance_handoff_payload",
    "product_acceptance_binding_from_package",
    "product_acceptance_report_template",
    "provider_qualification_status",
    "validate_product_acceptance_report",
    "validate_product_acceptance_spec",
    "validate_provider_qualification_receipt",
]
