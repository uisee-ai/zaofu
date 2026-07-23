"""Product delivery spine helpers.

This module turns an accepted task-map artifact into canonical kanban tasks.
It is deterministic and only writes through TaskStore plus append-only events.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events.writer import EventWriter
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore, TERMINAL_STATES as FEATURE_TERMINAL
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.replan_contract_eval import event_payload_for_eval
from zf.runtime.task_map import (
    task_verification_commands,
    validate_coverage_report_payload,
    validate_source_index_payload,
    validate_task_map_payload,
)
from zf.runtime.verification_commands import validation_with_commands
from zf.runtime.task_contract_normalize import (
    canonical_verification_tiers,
    owner_fields_from_task_map_item,
)
from zf.runtime.task_doc import write_task_doc


@dataclass(frozen=True)
class ProductDeliveryIngestResult:
    passed: bool
    created_task_ids: list[str] = field(default_factory=list)
    skipped_task_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ingest_task_map_to_kanban(
    state_dir: Path,
    task_map: dict[str, Any],
    *,
    source_refs: dict[str, str] | None = None,
    source_index: dict[str, Any] | None = None,
    source_index_ref: str = "",
    coverage_report: dict[str, Any] | None = None,
    coverage_report_ref: str = "",
    require_source_index: bool = False,
    require_coverage_report: bool = False,
    replan_eval: dict[str, Any] | None = None,
    require_replan_eval: bool = False,
    task_map_ref: str = "",
    writer: EventWriter | None = None,
    actor: str = "zf-cli",
    causation_id: str = "",
    correlation_id: str = "",
) -> ProductDeliveryIngestResult:
    state_dir = Path(state_dir)
    validation = validate_task_map_payload(task_map, require_task_verification=True)
    source_validation = None
    if source_index is not None:
        source_validation = validate_source_index_payload(
            source_index,
            task_map=task_map,
            require_canonical=require_source_index,
        )
    elif require_source_index:
        source_validation = validate_source_index_payload(
            {},
            task_map=task_map,
            require_canonical=True,
        )
    coverage_validation = None
    if coverage_report is not None:
        coverage_validation = validate_coverage_report_payload(
            coverage_report,
            task_map=task_map,
        )
    gate_errors = _contract_completeness_errors(task_map)
    # E3-1(审计 D2):presence-gated → 可强制。strict profile 下 plan
    # 不交 coverage_report 不再"kernel 视角完全合法"。
    if require_coverage_report and coverage_report is None:
        gate_errors.append(
            "coverage_report required (strict profile) but missing; "
            "plan must submit coverage_report_ref"
        )
    if coverage_report is not None and isinstance(
        coverage_report.get("unresolved_unknowns"), list
    ):
        for item in coverage_report.get("unresolved_unknowns") or []:
            gate_errors.append(f"coverage_report unresolved_unknown: {item}")
    if not validation.passed:
        gate_errors.extend(validation.errors)
    if source_validation is not None and not source_validation.passed:
        gate_errors.extend(source_validation.errors)
    if coverage_validation is not None and not coverage_validation.passed:
        gate_errors.extend(coverage_validation.errors)
    if gate_errors:
        if writer is not None:
            writer.emit(
                "product_delivery.task_map.rejected",
                actor=actor,
                payload={
                    "task_map_ref": task_map_ref,
                    "source_index_ref": source_index_ref,
                    "coverage_report_ref": coverage_report_ref,
                    "errors": list(gate_errors),
                    "summary": {
                        "task_map": dict(validation.summary),
                        "source_index": (
                            dict(source_validation.summary)
                            if source_validation is not None
                            else {}
                        ),
                        "coverage_report": (
                            dict(coverage_validation.summary)
                            if coverage_validation is not None
                            else {}
                        ),
                    },
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        return ProductDeliveryIngestResult(
            passed=False,
            errors=list(gate_errors),
            summary={
                "task_map": dict(validation.summary),
                "source_index": (
                    dict(source_validation.summary)
                    if source_validation is not None
                    else {}
                ),
                "coverage_report": (
                    dict(coverage_validation.summary)
                    if coverage_validation is not None
                    else {}
                ),
            },
        )

    refs = dict(source_refs or {})
    refs.update({
        key: str(value)
        for key, value in (task_map.get("source_refs") or {}).items()
        if str(value).strip()
    } if isinstance(task_map.get("source_refs"), dict) else {})
    if task_map_ref:
        refs.setdefault("task_map_ref", task_map_ref)
        refs.setdefault("plan_ref", task_map_ref)
    if source_index_ref:
        refs.setdefault("source_index_ref", source_index_ref)
    if coverage_report_ref:
        refs.setdefault("coverage_report_ref", coverage_report_ref)

    adoption_gate = _prepare_replan_adoption(
        state_dir=state_dir,
        task_map=task_map,
        refs=refs,
        replan_eval=replan_eval,
        require_replan_eval=require_replan_eval,
        writer=writer,
        actor=actor,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    if adoption_gate is not None and not adoption_gate.passed:
        return adoption_gate
    if adoption_gate is not None and adoption_gate.summary.get("idempotent"):
        return adoption_gate

    source_entries = _source_entries_by_task_id(source_index)
    superseded = _cancel_superseded_tasks(
        state_dir=state_dir,
        feature_id=str(task_map.get("feature_id") or ""),
        refs=refs,
        writer=writer,
        actor=actor,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    feature_projection = _upsert_delivery_feature(
        state_dir=state_dir,
        task_map=task_map,
        refs=refs,
        writer=writer,
        actor=actor,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )

    store = TaskStore(state_dir / "kanban.json")
    created: list[str] = []
    skipped: list[str] = []
    task_doc_failures: list[str] = []
    for raw in task_map.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
        if not task_id:
            continue
        if store.get(task_id) is not None:
            skipped.append(task_id)
            continue
        _owner_role, owner_instance = owner_fields_from_task_map_item(raw)
        contract = _contract_from_task_map_item(
            raw,
            feature_id=str(task_map.get("feature_id") or ""),
            refs=refs,
            source_entry=source_entries.get(task_id),
        )
        task = Task(
            id=task_id,
            title=str(raw.get("title") or task_id),
            key=str(raw.get("key") or f"{task_map.get('feature_id') or 'product'}:{task_id}"),
            status="backlog",
            priority=_priority(raw.get("priority")),
            assigned_to=str(raw.get("assigned_to") or owner_instance or "") or None,
            blocked_by=_string_list(raw.get("blocked_by")),
            contract=contract,
        )
        store.add(task)
        try:
            write_task_doc(
                state_dir,
                task,
                source_event="product_delivery_task_map",
                project_root=state_dir.parent,
            )
            store.update(task.id, contract=task.contract)
        except Exception as exc:
            task_doc_failures.append(f"{task_id}: {exc}")
        created.append(task.id)
        if writer is not None:
            created_event = writer.emit(
                "task.created",
                actor=actor,
                task_id=task.id,
                payload={
                    "source": "product_delivery_task_map",
                    "task_map_ref": task_map_ref,
                    "source_index_ref": source_index_ref,
                    "task": asdict(task),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            writer.emit(
                "task.contract.update",
                actor=actor,
                task_id=task.id,
                payload={
                    "source": "product_delivery_task_map",
                    "task_map_ref": task_map_ref,
                    "source_index_ref": source_index_ref,
                    "contract": asdict(contract),
                },
                causation_id=created_event.id,
                correlation_id=created_event.correlation_id,
            )

    result = ProductDeliveryIngestResult(
        passed=True,
        created_task_ids=created,
        skipped_task_ids=skipped,
        summary={
            **dict(validation.summary),
            "source_index": (
                dict(source_validation.summary)
                if source_validation is not None
                else {"source_modes": {"degraded": len(created) + len(skipped)}}
            ),
            "coverage_report": (
                dict(coverage_validation.summary)
                if coverage_validation is not None
                else {}
            ),
            "created_task_count": len(created),
            "skipped_task_count": len(skipped),
            "task_doc_failure_count": len(task_doc_failures),
            "task_doc_failures": task_doc_failures,
            "superseded_task_count": len(superseded),
            "superseded_task_ids": superseded,
            "feature_projection": feature_projection,
            "task_map_ref": task_map_ref,
            "source_index_ref": source_index_ref,
            "coverage_report_ref": coverage_report_ref,
            "replan_adoption": (
                dict(adoption_gate.summary)
                if adoption_gate is not None
                else {}
            ),
        },
    )
    if writer is not None:
        writer.emit(
            "product_delivery.task_map.accepted",
            actor=actor,
            payload=result.to_dict(),
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        if adoption_gate is not None:
            writer.emit(
                "replan.adoption.completed",
                actor=actor,
                payload={
                    **dict(adoption_gate.summary),
                    "created_task_ids": list(created),
                    "skipped_task_ids": list(skipped),
                    "superseded_task_ids": list(superseded),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        first_wave = _first_wave(task_map)
        writer.emit(
            "product_delivery.wave.ready",
            actor=actor,
            payload={
                "feature_id": str(task_map.get("feature_id") or ""),
                "pdd_id": str(task_map.get("feature_id") or ""),
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "coverage_report_ref": coverage_report_ref,
                "wave": first_wave,
                "task_ids": [
                    str(item.get("task_id") or item.get("id") or "")
                    for item in task_map.get("tasks") or []
                    if isinstance(item, dict) and _int_value(item.get("wave")) == first_wave
                ],
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    return result


def _prepare_replan_adoption(
    *,
    state_dir: Path,
    task_map: dict[str, Any],
    refs: dict[str, str],
    replan_eval: dict[str, Any] | None,
    require_replan_eval: bool,
    writer: EventWriter | None,
    actor: str,
    causation_id: str,
    correlation_id: str,
) -> ProductDeliveryIngestResult | None:
    supersedes_ref = _supersedes_ref(refs)
    if not (require_replan_eval or replan_eval is not None):
        return None
    if not isinstance(replan_eval, dict):
        return _blocked_replan_adoption(
            writer=writer,
            actor=actor,
            event_type="replan.contract_eval.adoption_blocked",
            errors=["replan_eval is required for this adoption path"],
            summary={
                "task_map_ref": refs.get("task_map_ref", ""),
                "supersedes_task_map_ref": supersedes_ref,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    eval_payload = event_payload_for_eval(replan_eval)
    _emit_replan_eval_overlay_events(
        writer=writer,
        actor=actor,
        eval_payload=eval_payload,
        replan_eval=replan_eval,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    idempotency_key = str(
        eval_payload.get("idempotency_key")
        or replan_eval.get("idempotency_key")
        or ""
    ).strip()
    if idempotency_key and _adoption_completed(writer, idempotency_key):
        task_ids = [
            str(raw.get("task_id") or raw.get("id") or "")
            for raw in task_map.get("tasks") or []
            if isinstance(raw, dict) and str(raw.get("task_id") or raw.get("id") or "")
        ]
        return ProductDeliveryIngestResult(
            passed=True,
            skipped_task_ids=task_ids,
            summary={
                "idempotent": True,
                "idempotency_key": idempotency_key,
                "task_map_ref": refs.get("task_map_ref", ""),
                "supersedes_task_map_ref": supersedes_ref,
                "eval_id": eval_payload.get("eval_id", ""),
            },
        )
    decision = str(eval_payload.get("decision") or replan_eval.get("decision") or "").strip()
    if decision != "adopt":
        return _blocked_replan_adoption(
            writer=writer,
            actor=actor,
            event_type="replan.contract_eval.adoption_blocked",
            errors=[f"replan_eval decision must be adopt, got {decision!r}"],
            summary={
                "task_map_ref": refs.get("task_map_ref", ""),
                "supersedes_task_map_ref": supersedes_ref,
                "eval": eval_payload,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    expected = str(
        eval_payload.get("expected_current_task_map_ref")
        or replan_eval.get("expected_current_task_map_ref")
        or ""
    ).strip()
    latest = _latest_task_map_ref_for_feature(
        state_dir,
        feature_id=str(task_map.get("feature_id") or ""),
    ) or supersedes_ref
    if expected and latest and expected != latest:
        return _blocked_replan_adoption(
            writer=writer,
            actor=actor,
            event_type="replan.adoption.stale_rejected",
            errors=[
                "replan_eval expected_current_task_map_ref "
                f"{expected!r} does not match latest {latest!r}"
            ],
            summary={
                "task_map_ref": refs.get("task_map_ref", ""),
                "supersedes_task_map_ref": supersedes_ref,
                "expected_current_task_map_ref": expected,
                "latest_task_map_ref": latest,
                "eval": eval_payload,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    # Owner gate (doc 84 A): an owner-gated replan must carry a recorded owner
    # approval before adoption. Missing/deferred → visible ``awaiting_owner``
    # (no silent dangle); rejected → ``owner_rejected`` and the original plan
    # continues. Non-owner-gated proposals skip this entirely.
    if _is_owner_gated(eval_payload, replan_eval, refs):
        owner_decision = _latest_owner_decision(
            writer,
            eval_id=str(eval_payload.get("eval_id") or idempotency_key or ""),
            task_map_ref=refs.get("task_map_ref", ""),
            proposal_ref=str(refs.get("proposal_ref") or ""),
        )
        if owner_decision == "rejected":
            return _blocked_replan_adoption(
                writer=writer,
                actor=actor,
                event_type="replan.adoption.owner_rejected",
                errors=["owner rejected this replan; original plan continues"],
                summary={
                    "task_map_ref": refs.get("task_map_ref", ""),
                    "supersedes_task_map_ref": supersedes_ref,
                    "eval_id": eval_payload.get("eval_id", ""),
                    "owner_decision": owner_decision,
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        if owner_decision != "approved":
            return _blocked_replan_adoption(
                writer=writer,
                actor=actor,
                event_type="replan.adoption.awaiting_owner",
                errors=["owner approval required before adoption"],
                summary={
                    "task_map_ref": refs.get("task_map_ref", ""),
                    "supersedes_task_map_ref": supersedes_ref,
                    "eval_id": eval_payload.get("eval_id", ""),
                    "owner_decision": owner_decision or "none",
                    # Re-drive context (doc 84 B): once the owner approves, the
                    # tick sweep reloads the candidate from these refs and
                    # re-ingests — no manual manifest re-apply.
                    "refs": dict(refs),
                    "replan_eval": dict(replan_eval),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
    summary = {
        "idempotent": False,
        "idempotency_key": idempotency_key,
        "task_map_ref": refs.get("task_map_ref", ""),
        "supersedes_task_map_ref": supersedes_ref,
        "expected_current_task_map_ref": expected,
        "latest_task_map_ref": latest,
        "eval_id": eval_payload.get("eval_id", ""),
        "eval": eval_payload,
    }
    if writer is not None:
        writer.emit(
            "replan.adoption.prepared",
            actor=actor,
            payload=summary,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    return ProductDeliveryIngestResult(passed=True, summary=summary)


def _blocked_replan_adoption(
    *,
    writer: EventWriter | None,
    actor: str,
    event_type: str,
    errors: list[str],
    summary: dict[str, Any],
    causation_id: str,
    correlation_id: str,
) -> ProductDeliveryIngestResult:
    if writer is not None:
        writer.emit(
            event_type,
            actor=actor,
            payload={
                "errors": list(errors),
                **summary,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
    return ProductDeliveryIngestResult(
        passed=False,
        errors=list(errors),
        summary=summary,
    )


def _adoption_completed(writer: EventWriter | None, idempotency_key: str) -> bool:
    if writer is None or not idempotency_key:
        return False
    event_log = getattr(writer, "event_log", None)
    if event_log is None:
        return False
    for event in reversed(event_log.read_all()):
        if event.type != "replan.adoption.completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("idempotency_key") or "") == idempotency_key:
            return True
    return False


def _emit_replan_eval_overlay_events(
    *,
    writer: EventWriter | None,
    actor: str,
    eval_payload: dict[str, Any],
    replan_eval: dict[str, Any],
    causation_id: str,
    correlation_id: str,
) -> None:
    """Append owner-side eval/behavior conclusions for Delivery Trace.

    The evaluator remains pure; this callsite is the append-only event boundary.
    """

    if writer is None:
        return
    eval_id = str(
        eval_payload.get("eval_id")
        or eval_payload.get("idempotency_key")
        or eval_payload.get("new_task_map_ref")
        or ""
    ).strip()
    common = {
        "schema_version": "delivery-eval-overlay-event.v1",
        "eval_id": eval_id,
        "profile": str(eval_payload.get("profile") or ""),
        "decision": str(eval_payload.get("decision") or ""),
        "failed_checks": list(eval_payload.get("failed_checks") or []),
        "refs": dict(eval_payload.get("refs") or {}),
        "new_task_map_ref": str(eval_payload.get("new_task_map_ref") or ""),
        "old_task_map_ref": str(eval_payload.get("old_task_map_ref") or ""),
    }
    if not _overlay_event_exists(
        writer,
        event_type="replan.contract_eval.completed",
        eval_id=eval_id,
        check_name="",
    ):
        writer.emit(
            "replan.contract_eval.completed",
            actor=actor,
            payload={
                **common,
                "schema_version": str(
                    eval_payload.get("schema_version") or "replan-contract-eval.v1"
                ),
                "check_summary": dict(eval_payload.get("check_summary") or {}),
                "contract_delta_counts": dict(eval_payload.get("contract_delta_counts") or {}),
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    checks = replan_eval.get("checks") if isinstance(replan_eval.get("checks"), list) else []
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "").strip()
        if not name:
            continue
        passed = bool(check.get("passed"))
        status = "passed" if passed else "failed"
        errors = list(check.get("errors") or [])
        payload = {
            **common,
            "check_name": name,
            "status": status,
            "score": 1.0 if passed else 0.0,
            "detail": {
                "errors": errors,
                "summary": dict(check.get("summary") or {}),
            },
        }
        if name == "contract_completeness":
            _emit_overlay_once(
                writer,
                "eval.contract_completeness.completed",
                eval_id=eval_id,
                check_name=name,
                actor=actor,
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        elif name in {"done_evidence_carry_forward", "failure_taxonomy_binding"}:
            _emit_overlay_once(
                writer,
                "eval.evidence_sufficiency.completed",
                eval_id=eval_id,
                check_name=name,
                actor=actor,
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        elif name == "source_coverage_no_invention" and not passed:
            _emit_overlay_once(
                writer,
                "behavior.source_coverage_gap.detected",
                eval_id=eval_id,
                check_name=name,
                actor=actor,
                payload={
                    **payload,
                    "reason": "; ".join(str(item) for item in errors[:3]),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )


def _emit_overlay_once(
    writer: EventWriter,
    event_type: str,
    *,
    eval_id: str,
    check_name: str,
    actor: str,
    payload: dict[str, Any],
    causation_id: str,
    correlation_id: str,
) -> None:
    if _overlay_event_exists(
        writer,
        event_type=event_type,
        eval_id=eval_id,
        check_name=check_name,
    ):
        return
    writer.emit(
        event_type,
        actor=actor,
        payload=payload,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )


def _overlay_event_exists(
    writer: EventWriter,
    *,
    event_type: str,
    eval_id: str,
    check_name: str,
) -> bool:
    try:
        events = writer.event_log.read_all()
    except Exception:
        return False
    for event in events:
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if eval_id and str(payload.get("eval_id") or "") != eval_id:
            continue
        if check_name and str(payload.get("check_name") or "") != check_name:
            continue
        return True
    return False


_OWNER_APPROVAL_KEYS = ("owner_approval_required", "requires_owner_approval")


def _is_owner_gated(*sources: Any) -> bool:
    """True when any adoption input marks the replan as owner-gated.

    Default off → non-owner-gated proposals keep the eval-only adoption path
    unchanged. The signal originates from the proposal's ``owner_approval_required``
    (doc 84 §4.3), carried forward via the eval payload or adoption refs.
    """
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _OWNER_APPROVAL_KEYS:
            value = source.get(key)
            if value is True or str(value).strip().lower() in {"true", "1", "yes"}:
                return True
    return False


def _latest_owner_decision(
    writer: EventWriter | None,
    *,
    eval_id: str,
    task_map_ref: str,
    proposal_ref: str,
) -> str:
    """Latest owner replan decision (``approved`` / ``deferred`` / ``rejected``)
    matching this adoption, or ``""`` when the owner has not decided.

    Deterministic: derived only from ``events.jsonl``. Matching the owner
    decision recorded by the token-gated Web action (``replan.owner_decision.*``)
    is what bridges the human approval into this deterministic gate — without a
    second control plane (doc 84 §0.1 closure).
    """
    if writer is None:
        return ""
    event_log = getattr(writer, "event_log", None)
    if event_log is None:
        return ""
    keys = {key for key in (eval_id, task_map_ref, proposal_ref) if key}
    if not keys:
        return ""
    for event in reversed(event_log.read_all()):
        if not event.type.startswith("replan.owner_decision."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        decided_refs = {
            str(payload.get("eval_ref") or ""),
            str(payload.get("eval_id") or ""),
            str(payload.get("candidate_task_map_ref") or ""),
            str(payload.get("proposal_ref") or ""),
        }
        if keys & {ref for ref in decided_refs if ref}:
            return event.type.rsplit(".", 1)[-1]
    return ""


def _latest_task_map_ref_for_feature(state_dir: Path, *, feature_id: str) -> str:
    store = TaskStore(state_dir / "kanban.json")
    refs: list[str] = []
    for task in store.list_all():
        contract = task.contract
        if feature_id and getattr(contract, "feature_id", "") != feature_id:
            continue
        if task.status == "cancelled":
            continue
        source_refs = {}
        if contract and isinstance(contract.evidence_contract, dict):
            source_refs = contract.evidence_contract.get("source_refs") or {}
        ref = str(source_refs.get("task_map_ref") or "").strip()
        if ref:
            refs.append(ref)
    return refs[-1] if refs else ""


def _contract_from_task_map_item(
    raw: dict[str, Any],
    *,
    feature_id: str,
    refs: dict[str, str],
    source_entry: dict[str, Any] | None = None,
) -> TaskContract:
    acceptance = _acceptance_criteria_list(
        raw.get("acceptance_criteria") or raw.get("acceptance")
    )
    validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
    commands = task_verification_commands(raw)
    validation = validation_with_commands(validation, commands) if commands else dict(validation)
    verification = str(commands[0]["command"] if commands else "")
    verification_tiers = canonical_verification_tiers(
        raw.get("verification_tiers"),
        verification=verification,
        validation=validation,
    )
    owner_role, owner_instance = owner_fields_from_task_map_item(raw)
    source_entry = source_entry or {}
    source_mode = str(
        source_entry.get("source_mode")
        or source_entry.get("mode")
        or raw.get("source_mode")
        or ("canonical" if source_entry else "degraded")
    ).strip()
    source_ref = _first_nonempty(
        source_entry.get("source_ref"),
        source_entry.get("ref"),
        raw.get("source_ref"),
        refs.get("source_ref"),
        refs.get("plan_ref"),
        refs.get("spec_ref"),
    )
    source_key = _first_nonempty(
        source_entry.get("source_key"),
        raw.get("source_key"),
        f"{refs.get('task_map_ref', '')}#{raw.get('task_id') or raw.get('id') or ''}",
        source_ref,
    )
    source_task_id = _first_nonempty(
        source_entry.get("source_task_id"),
        source_entry.get("task_id"),
        raw.get("source_task_id"),
        raw.get("task_id"),
        raw.get("id"),
    )
    source_excerpt = str(
        source_entry.get("source_excerpt")
        or source_entry.get("excerpt")
        or source_entry.get("text")
        or raw.get("source_excerpt")
        or ""
    ).strip()
    product_contract_ref = _first_nonempty(
        raw.get("product_contract_ref"),
        refs.get("product_contract_ref"),
        refs.get("spec_ref"),
        refs.get("plan_ref"),
        source_ref,
    )
    evidence_contract = (
        dict(raw.get("evidence_contract"))
        if isinstance(raw.get("evidence_contract"), dict)
        else {}
    )
    evidence_contract.update({
        "source": "product_delivery_task_map",
        "source_refs": dict(refs),
    })
    success_criteria = evidence_contract.get("success_criteria")
    if not isinstance(success_criteria, list):
        success_criteria = []
    evidence_contract["success_criteria"] = list(success_criteria)
    for command in commands:
        evidence_contract["success_criteria"].append({
            "kind": "command_passed",
            "command_id": command["id"],
            "command": command["command"],
            "acceptance_ids": list(command["acceptance_ids"]),
        })
    return TaskContract(
        schema_version="task-contract.v1",
        feature_id=feature_id,
        # doc 69 S-b: phase from the task-map item (was hardcoded
        # "product_delivery"). Falls back to plan_section, then "default" — so
        # phase rollup can group by the real delivery phases.
        phase=str(raw.get("phase") or raw.get("plan_section") or "").strip() or "default",
        behavior=str(raw.get("behavior") or raw.get("plan_section") or raw.get("title") or ""),
        verification=verification,
        verification_tiers=verification_tiers,
        validation=validation,
        # r6-F3:验收声明的 runtime evidence 路径自动并入 scope——
        # 合同曾自相矛盾(要求写 docs/validation/** 却只给 src/**),
        # scope guard 忠实拦截,verify 死循环真根因。声明驱动,零声明
        # 零扩权。
        scope=_merge_evidence_paths_into_scope(
            _string_list(raw.get("scope")),
            raw,
        ),
        acceptance="\n".join(_criterion_text(item) for item in acceptance) or verification or "exit_code=0",
        acceptance_criteria=acceptance,
        goal_claim_ids=_unique(_string_list(raw.get("goal_claim_ids"))),
        source_key=source_key,
        source_ref=source_ref,
        source_task_id=source_task_id,
        source_index_ref=refs.get("source_index_ref", ""),
        source_mode=source_mode,
        source_title=str(source_entry.get("source_title") or source_entry.get("title") or "").strip(),
        source_excerpt=source_excerpt,
        product_contract_ref=product_contract_ref,
        spec_skip_reason=str(raw.get("spec_skip_reason") or "").strip(),
        spec_ref=refs.get("spec_ref", ""),
        plan_ref=refs.get("plan_ref", "") or refs.get("task_map_ref", ""),
        tdd_ref=refs.get("tdd_ref", ""),
        critic_event_id=refs.get("critic_event_id", ""),
        critic_gate_ref=refs.get("critic_gate_ref", ""),
        evidence_contract=evidence_contract,
        owner_role=owner_role,
        owner_instance=owner_instance,
        wave=_int_value(raw.get("wave")),
        shared_files=_string_list(raw.get("shared_files")),
        exclusive_files=_string_list(raw.get("exclusive_files")),
        handoff_artifacts=_unique([
            *_string_list(raw.get("handoff_artifacts")),
            *_string_list(raw.get("source_refs")),
        ]),
        unknowns=_string_list(raw.get("unknowns"))
        + _string_list(source_entry.get("unknowns")),
        complexity=str(raw.get("complexity") or "").strip(),
    )


def _contract_completeness_errors(task_map: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for idx, raw in enumerate(task_map.get("tasks") or []):
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or raw.get("id") or f"tasks[{idx}]").strip()
        if not str(
            raw.get("behavior") or raw.get("plan_section") or raw.get("title") or ""
        ).strip():
            errors.append(f"{task_id}.behavior/title is required")
        if not _string_list(raw.get("scope")):
            errors.append(f"{task_id}.scope is required")
        if not (
            task_verification_commands(raw)
            or _string_list(raw.get("acceptance"))
        ):
            errors.append(f"{task_id}.verification or acceptance is required")
        commands = task_verification_commands(raw)
        verification = str(commands[0]["command"] if commands else "")
        for command in commands:
            if _verification_contains_prose(str(command["command"])):
                errors.append(
                    f"{task_id}.verification must be an executable command only; "
                    "put expected-red/prose in validation or evidence_contract"
                )
        validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
        if not canonical_verification_tiers(
            raw.get("verification_tiers"),
            verification=verification,
            validation=validation,
        ):
            errors.append(f"{task_id}.verification_tiers is required")
        owner_role, owner_instance = owner_fields_from_task_map_item(raw)
        if not owner_role and not owner_instance and not str(raw.get("assigned_to") or "").strip():
            errors.append(f"{task_id}.owner_role or owner_instance is required")
    return errors


def _source_entries_by_task_id(source_index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(source_index, dict):
        return {}
    raw = source_index.get("tasks")
    entries: list[Any]
    if isinstance(raw, dict):
        entries = [
            {"task_id": key, **value}
            if isinstance(value, dict)
            else {"task_id": key, "source_excerpt": str(value)}
            for key, value in raw.items()
        ]
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []
    out: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or item.get("id") or "").strip()
        if task_id:
            out[task_id] = item
    return out


def _cancel_superseded_tasks(
    *,
    state_dir: Path,
    feature_id: str,
    refs: dict[str, str],
    writer: EventWriter | None,
    actor: str,
    causation_id: str,
    correlation_id: str,
) -> list[str]:
    supersedes_ref = _supersedes_ref(refs)
    if not supersedes_ref:
        return []
    store = TaskStore(state_dir / "kanban.json")
    cancelled: list[str] = []
    for task in store.list_all():
        contract = task.contract
        if task.status in {"done", "cancelled"}:
            continue
        if feature_id and getattr(contract, "feature_id", "") != feature_id:
            continue
        task_refs = {}
        if contract and isinstance(contract.evidence_contract, dict):
            task_refs = contract.evidence_contract.get("source_refs") or {}
        if str(task_refs.get("task_map_ref") or "") != supersedes_ref:
            continue
        updated = store.update(
            task.id,
            status="cancelled",
            blocked_reason=f"superseded by {refs.get('task_map_ref', '')}",
            active_dispatch_id="",
        )
        if updated is None:
            continue
        cancelled.append(task.id)
        if writer is not None:
            writer.emit(
                "task.superseded",
                actor=actor,
                task_id=task.id,
                payload={
                    "source": "product_delivery_task_map",
                    "superseded_by_task_map_ref": refs.get("task_map_ref", ""),
                    "supersedes_task_map_ref": supersedes_ref,
                    "status": "cancelled",
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
    return cancelled


def _supersedes_ref(refs: dict[str, str]) -> str:
    return _first_nonempty(
        refs.get("supersedes_task_map_ref"),
        refs.get("supersedes"),
        refs.get("supersedes_ref"),
    )


def _upsert_delivery_feature(
    *,
    state_dir: Path,
    task_map: dict[str, Any],
    refs: dict[str, str],
    writer: EventWriter | None,
    actor: str,
    causation_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    feature_id = str(task_map.get("feature_id") or "").strip()
    if not feature_id:
        return {"status": "skipped", "reason": "missing_feature_id"}
    store = FeatureStore(state_dir / "feature_list.json")
    existing = store.get(feature_id)
    title = _delivery_feature_title(task_map, refs)
    if existing is None:
        feature = store.add(Feature(
            id=feature_id,
            title=title,
            description=str(task_map.get("description") or "").strip(),
            status="active",
            user_message=title,
        ))
        if writer is not None:
            writer.emit(
                "feature.created",
                actor=actor,
                payload={
                    "feature_id": feature_id,
                    "source": "product_delivery_task_map",
                    "task_map_ref": refs.get("task_map_ref", ""),
                    "source_index_ref": refs.get("source_index_ref", ""),
                    "coverage_report_ref": refs.get("coverage_report_ref", ""),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        return {"status": "created", "feature_id": feature.id}
    if existing.status in FEATURE_TERMINAL:
        return {
            "status": "terminal_existing",
            "feature_id": existing.id,
            "feature_status": existing.status,
        }
    if existing.status == "planning":
        updated = store.update(feature_id, status="active")
        if writer is not None and updated is not None:
            writer.emit(
                "feature.status_changed",
                actor=actor,
                task_id=feature_id,
                payload={
                    "feature_id": feature_id,
                    "from": existing.status,
                    "to": "active",
                    "source": "product_delivery_task_map",
                    "task_map_ref": refs.get("task_map_ref", ""),
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        return {"status": "activated", "feature_id": feature_id}
    return {
        "status": "reused",
        "feature_id": existing.id,
        "feature_status": existing.status,
    }


def _delivery_feature_title(task_map: dict[str, Any], refs: dict[str, str]) -> str:
    title = str(
        task_map.get("title")
        or task_map.get("feature_title")
        or task_map.get("name")
        or refs.get("plan_ref")
        or refs.get("spec_ref")
        or task_map.get("feature_id")
        or "product delivery"
    ).strip()
    return title or str(task_map.get("feature_id") or "product delivery")


def _first_wave(task_map: dict[str, Any]) -> int:
    waves = [
        _int_value(item.get("wave"))
        for item in task_map.get("tasks") or []
        if isinstance(item, dict)
    ]
    return min(waves) if waves else 0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _criterion_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("text")
            or value.get("criterion")
            or value.get("description")
            or value.get("acceptance")
            or ""
        ).strip()
    return str(value or "").strip()


def _acceptance_criteria_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return _string_list(value)
    return [
        dict(item) if isinstance(item, dict) else str(item).strip()
        for item in value
        if _criterion_text(item)
    ]


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _priority(value: Any) -> int:
    if isinstance(value, str) and value.upper().startswith("P"):
        value = value[1:]
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, number))



def _merge_evidence_paths_into_scope(
    scope: list[str],
    raw: dict[str, Any],
) -> list[str]:
    """r6-F3:required_runtime_evidence 声明的路径并入 scope。"""
    if not scope:
        return scope  # 无 scope 约束的任务不引入新约束语义
    merged = list(scope)
    evidence = raw.get("required_runtime_evidence")
    if not isinstance(evidence, list):
        return merged
    for item in evidence:
        path = str(item or "").strip()
        if not path or path in merged:
            continue
        merged.append(path)
    return merged

def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _verification_contains_prose(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return True
    if any(ch in text for ch in ("（", "）", "；")):
        return True
    lowered = text.lower()
    prose_markers = (
        "expected red",
        "red expected",
        "expected failure",
        "should fail",
        "evidence:",
    )
    return any(marker in lowered for marker in prose_markers)


__all__ = [
    "ProductDeliveryIngestResult",
    "ingest_task_map_to_kanban",
]
