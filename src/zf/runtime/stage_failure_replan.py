"""Reader stage 级失败的机械 replan(prod-e2e 2026-07-05 定案)。

问题形状(prd/issue 两流实弹):plan/triage 类 reader stage 的
failure_event(prd.plan.failed / issue.triage.failed)发射后,
rework_routing 的映射需要任务载体,而这类失败发生在任务被接纳之前——
映射空转,流程死端,只能 operator 手工重发 trigger。

机械闭环:failure_event → 重发该 stage 的 trigger 事件(原载荷 +
rework_of / rework_attempt / rework_feedback=findings),reader fanout
照常重开,briefing 管线自带 findings 下发(F2 修复面)。决策成为事件,
可审计可回放;cap 之外升级 human。
"""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    plan_artifact_package_binding,
    reduce_plan_artifact_packages,
)

# stage 级 replan 上限:超过即该升级 human,不许无限重合成
# (r5 教训:机械重试无界 = 烧钱活锁)。
STAGE_REPLAN_CAP = 2

_CANDIDATE_REPLAN_SOURCE_EVENTS = frozenset({
    "candidate.conflict",
    "candidate.quality.failed",
    "integration.failed",
})
_CANDIDATE_TERMINAL_EVENTS = frozenset({
    *_CANDIDATE_REPLAN_SOURCE_EVENTS,
    "candidate.ready",
})
_RUN_FLOW_MARKER_EVENTS = frozenset({
    "flow.roles.activation.recovered",
    "task_map.ready",
})
_GAP_PLAN_READY_EVENTS = frozenset({
    "gap_plan.ready",
    "flow.gap_plan.ready",
    "goal.gap_plan.ready",
})
_CANONICAL_INPUT_KEYS = (
    "task_contract_digest",
    "contract_snapshot_digest",
    "requirement_spec_digest",
    "source_digest",
    "prd_digest",
    "spec_digest",
    "workflow_input_manifest_digest",
    "source_workflow_input_manifest_digest",
    "effective_config_digest",
    "run_contract_digest",
    "task_map_digest",
    "plan_artifact_package_digest",
    "source_revision",
    "prd_revision",
    "workflow_input_manifest_ref",
    "source_ref",
    "prd_ref",
    "target_ref",
)
_SEMANTIC_FAILURE_CLASSES = frozenset({
    "semantic_rejection",
    "product_rejection",
    "candidate_product_quality_failed",
})
_NON_SEMANTIC_FAILURE_CLASSES = frozenset({
    "dependency_blocked",
    "environment_failure",
    "harness_failure",
    "integration_failure",
    "reader_execution_failure",
    "reader_abstained",
    "transport_delivery",
    "verifier_execution_failure",
})
_PLAN_PACKAGE_IDENTITY_KEYS = (
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)
_CURRENT_PLAN_IDENTITY_GROUPS = (
    ("task_map_ref", "task_map_digest"),
    _PLAN_PACKAGE_IDENTITY_KEYS,
)
_CURRENT_PLAN_IDENTITY_KEYS = frozenset(
    key for group in _CURRENT_PLAN_IDENTITY_GROUPS for key in group
)
_GOAL_CLAIM_SET_IDENTITY_KEYS = (
    "goal_claim_set_ref",
    "goal_claim_set_digest",
)


def _bind_current_plan_package_identity(
    payload: dict[str, Any],
    *,
    events: list[ZfEvent],
    workflow_run_id: str,
) -> None:
    """Keep stage replans on the canonical admitted package for the run."""

    if not workflow_run_id:
        return
    projection = reduce_plan_artifact_packages(
        events,
        workflow_run_id=workflow_run_id,
    )
    current = projection.get("current")
    if not isinstance(current, dict) or not current:
        return
    for key in _GOAL_CLAIM_SET_IDENTITY_KEYS:
        payload.pop(key, None)
    payload.update(plan_artifact_package_binding(events, current))


def _payload_target_ref(payload: dict[str, Any]) -> str:
    for key in ("target_ref", "issue_ref", "prd_ref"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    source_refs = payload.get("source_refs")
    if isinstance(source_refs, dict):
        for key in ("target_ref", "issue_ref", "prd_ref"):
            value = str(source_refs.get(key) or "").strip()
            if value:
                return value
    return ""


def rework_source_from_payload(payload: dict[str, Any]) -> str:
    source = str(payload.get("rework_source") or "").strip()
    if source:
        return source
    summary = payload.get("rework_summary")
    if isinstance(summary, dict):
        return str(summary.get("source_event_type") or "").strip()
    return ""


def _event_run_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("trace_id")
        or event.correlation_id
        or ""
    ).strip()


def _same_run(event: ZfEvent, run_id: str) -> bool:
    """Keep stage retry accounting inside one canonical workflow Run."""

    return not run_id or _event_run_id(event) == run_id


def _stage_flow_kind(stage: Any, trigger_type: str) -> str:
    stage_id = str(getattr(stage, "id", "") or "").strip().lower()
    trigger = str(trigger_type or "").strip().lower()
    for kind in ("issue", "prd", "refactor"):
        if stage_id.startswith(f"{kind}-"):
            return kind
        if trigger.startswith(f"{kind}."):
            return kind
    if trigger.startswith("zaofu.refactor."):
        return "refactor"
    return ""


def _canonical_input_identity(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, str(payload.get(key) or "").strip())
        for key in _CANONICAL_INPUT_KEYS
        if payload.get(key) not in (None, "", [], {})
    )


def _semantic_failure(payload: dict[str, Any]) -> bool:
    failure_class = str(
        payload.get("failure_class")
        or payload.get("failure_classification")
        or ""
    ).strip()
    if failure_class in _NON_SEMANTIC_FAILURE_CLASSES:
        return False
    if failure_class in _SEMANTIC_FAILURE_CLASSES:
        return True
    return str(payload.get("recommendation") or "").strip().lower() in {
        "reject",
        "rejected",
        "needs_rework",
    }


def _requires_changed_scan_input(
    stage: Any,
    failure_event_type: str,
) -> bool:
    stage_id = str(getattr(stage, "id", "") or "").strip().lower()
    event_type = str(failure_event_type or "").strip().lower()
    return "scan" in stage_id or event_type.endswith(".scan.failed")


def _same_replan_scope(event: ZfEvent, failure_event: ZfEvent) -> bool:
    failure_run_id = _event_run_id(failure_event)
    if failure_run_id and _event_run_id(event) != failure_run_id:
        return False
    failure_payload = (
        failure_event.payload if isinstance(failure_event.payload, dict) else {}
    )
    event_payload = event.payload if isinstance(event.payload, dict) else {}
    failure_generation = str(
        failure_payload.get("stage_replan_generation") or ""
    ).strip()
    event_generation = str(
        event_payload.get("stage_replan_generation") or ""
    ).strip()
    if (failure_generation or event_generation) and (
        failure_generation != event_generation
    ):
        return False
    failure_pdd = str(
        failure_payload.get("pdd_id") or failure_payload.get("feature_id") or ""
    ).strip()
    event_pdd = str(
        event_payload.get("pdd_id") or event_payload.get("feature_id") or ""
    ).strip()
    return not (failure_pdd and event_pdd and failure_pdd != event_pdd)


def _canonical_run_flow_kind(
    events: list[ZfEvent],
    failure_event: ZfEvent,
) -> str:
    run_id = _event_run_id(failure_event)
    if not run_id:
        return ""
    flow_kind = ""
    for event in events:
        if event.id == failure_event.id:
            break
        if event.type not in _RUN_FLOW_MARKER_EVENTS:
            continue
        if _event_run_id(event) != run_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidate = str(payload.get("flow_kind") or "").strip().lower()
        if candidate:
            flow_kind = candidate
    return flow_kind


def superseding_candidate_ready(
    events: list[ZfEvent],
    *,
    rework_source: str,
    correlation_id: str = "",
    pdd_id: str = "",
) -> ZfEvent | None:
    """Return a candidate success when it is the latest matching terminal fact."""
    if rework_source not in _CANDIDATE_REPLAN_SOURCE_EVENTS:
        return None
    if not correlation_id and not pdd_id:
        return None
    latest: ZfEvent | None = None
    for event in events:
        if event.type not in _CANDIDATE_TERMINAL_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_trace = str(
            event.correlation_id
            or payload.get("trace_id")
            or payload.get("workflow_run_id")
            or ""
        ).strip()
        if correlation_id and event_trace != correlation_id:
            continue
        event_pdd = str(
            payload.get("pdd_id") or payload.get("feature_id") or ""
        ).strip()
        if pdd_id and event_pdd != pdd_id:
            continue
        latest = event
    if latest is not None and latest.type == "candidate.ready":
        return latest
    return None


def reader_stage_failure_candidates(config: Any) -> dict[str, list[Any]]:
    """Return every reader stage that may publish each failure event."""

    out: dict[str, list[Any]] = {}
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        if str(getattr(stage, "topology", "") or "") != "fanout_reader":
            continue
        assignment = getattr(stage, "assignment", None)
        if assignment is None:
            assignment = getattr(getattr(stage, "fanout", None), "assignment", None)
        if str(getattr(assignment, "strategy", "") or "") == "affinity_stage_slots":
            # Lane Verify already owns a bounded same-lane back-edge. Generic
            # reader replan would race that back-edge and can start Verify
            # before the replacement Impl result exists.
            continue
        aggregate = getattr(stage, "aggregate", None)
        failure = str(
            getattr(stage, "failure_event", "")
            or getattr(aggregate, "failure_event", "")
            or ""
        )
        if failure:
            out.setdefault(failure, []).append(stage)
    return out


def reader_stage_failure_events(config: Any) -> dict[str, Any]:
    """Return the event membership map kept for legacy callers.

    Selection must use :func:`reader_stage_for_failure`; a multi-kind config
    may intentionally reuse one canonical event across flow-scoped stages.
    """

    return {
        event_type: stages[0]
        for event_type, stages in reader_stage_failure_candidates(config).items()
        if stages
    }


def reader_stage_for_failure(
    config: Any,
    events: list[ZfEvent],
    failure_event: ZfEvent,
) -> tuple[Any | None, str]:
    """Resolve one stage by durable stage/run identity, never first-wins."""

    candidates = reader_stage_failure_candidates(config).get(
        failure_event.type,
        [],
    )
    if not candidates:
        return None, "no_reader_stage_for_failure"

    payload = (
        failure_event.payload if isinstance(failure_event.payload, dict) else {}
    )
    stage_id = str(payload.get("stage_id") or "").strip()
    if stage_id:
        matching_stage_ids = [
            stage
            for stage in candidates
            if str(getattr(stage, "id", "") or "").strip() == stage_id
        ]
        if len(matching_stage_ids) == 1:
            return matching_stage_ids[0], "stage_id"
        return None, "failure_stage_id_mismatch"

    canonical_flow_kind = str(payload.get("flow_kind") or "").strip().lower()
    if not canonical_flow_kind:
        canonical_flow_kind = _canonical_run_flow_kind(events, failure_event)
    if canonical_flow_kind:
        matching_flow_kinds = [
            stage
            for stage in candidates
            if (
                str(getattr(stage, "flow_kind", "") or "").strip().lower()
                or _stage_flow_kind(
                    stage,
                    str(getattr(stage, "trigger", "") or ""),
                )
            )
            == canonical_flow_kind
        ]
        if len(matching_flow_kinds) == 1:
            return matching_flow_kinds[0], "flow_kind"
        if not matching_flow_kinds:
            return None, "cross_flow_failure"
        return None, "ambiguous_reader_stage_for_failure"

    if len(candidates) == 1:
        return candidates[0], "single_candidate"
    return None, "ambiguous_reader_stage_for_failure"


def reader_stage_lineage_payload(
    events: list[ZfEvent],
    *,
    stage_id: str,
    correlation_id: str = "",
    fanout_id: str = "",
) -> dict[str, Any]:
    """Recover the durable parent payload across pattern-bridged replans."""

    by_id = {event.id: event for event in events}
    lineage: dict[str, Any] = {}
    for event in events:
        if event.type != "fanout.started":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if fanout_id and str(payload.get("fanout_id") or "") != fanout_id:
            continue
        if str(payload.get("stage_id") or "") != stage_id:
            continue
        trace_id = str(
            event.correlation_id
            or payload.get("trace_id")
            or ""
        )
        if correlation_id and trace_id != correlation_id:
            continue
        trigger = by_id.get(str(payload.get("trigger_event_id") or ""))
        trigger_payload = (
            trigger.payload
            if trigger is not None and isinstance(trigger.payload, dict)
            else {}
        )
        for key, value in trigger_payload.items():
            if value not in (None, "", [], {}):
                lineage[key] = value
    return lineage


def plan_reader_stage_replan(
    config: Any,
    events: list[ZfEvent],
    failure_event: ZfEvent,
    *,
    authorized_generation: str = "",
    generation_source_event_id: str = "",
    authorization_event_id: str = "",
    authorization_ref: str = "",
) -> tuple[ZfEvent | None, str]:
    """返回 (待 append 的 replan 事件, 说明)。None = 不 replan(附原因)。

    - 幂等:同一 failure 事件只 replan 一次(causation_id 判重);
    - cap:同类 failure 达 STAGE_REPLAN_CAP 次后不再 replan(说明含
      "cap_exhausted",调用方据此升级 human)。
    """
    stage, selection = reader_stage_for_failure(config, events, failure_event)
    if stage is None:
        return None, selection
    trigger_type = str(getattr(stage, "trigger", "") or "")
    if not trigger_type:
        return None, "stage_has_no_trigger"
    failure_run_id = _event_run_id(failure_event)
    stage_flow_kind = _stage_flow_kind(stage, trigger_type)
    canonical_flow_kind = _canonical_run_flow_kind(events, failure_event)
    if (
        stage_flow_kind
        and canonical_flow_kind
        and stage_flow_kind != canonical_flow_kind
    ):
        return None, "cross_flow_failure"
    failure_payload = (
        failure_event.payload if isinstance(failure_event.payload, dict) else {}
    )
    failure_fanout_id = str(failure_payload.get("fanout_id") or "")
    origin_payload: dict[str, Any] = {}
    for event in events:
        if (
            event.type == trigger_type
            and _same_run(event, failure_run_id)
            and isinstance(event.payload, dict)
            and _same_replan_scope(event, failure_event)
        ):
            origin_payload = dict(event.payload)
    lineage = reader_stage_lineage_payload(
        events,
        stage_id=str(getattr(stage, "id", "") or ""),
        correlation_id=str(failure_event.correlation_id or ""),
        fanout_id=failure_fanout_id,
    )
    if failure_fanout_id:
        if not lineage:
            return None, "reader_origin_missing"
        # A Run may contain many candidates for the same reader stage. Its
        # latest request is not the causal input of this failed operation.
        origin_payload = lineage
    else:
        origin_payload = {**lineage, **origin_payload}
    if (
        _requires_changed_scan_input(stage, failure_event.type)
        and _semantic_failure(failure_payload)
    ):
        origin_identity = _canonical_input_identity(origin_payload)
        current_identity = _canonical_input_identity({
            **origin_payload,
            **{
                key: value
                for key, value in failure_payload.items()
                if value not in (None, "", [], {})
            },
        })
        if origin_identity and current_identity == origin_identity:
            return None, "input_unchanged"
    superseding_success = superseding_candidate_ready(
        events,
        rework_source=rework_source_from_payload(origin_payload),
        correlation_id=str(failure_event.correlation_id or ""),
        pdd_id=str(
            origin_payload.get("pdd_id")
            or origin_payload.get("feature_id")
            or failure_payload.get("pdd_id")
            or failure_payload.get("feature_id")
            or ""
        ),
    )
    if superseding_success is not None:
        return None, "superseded_by_candidate_ready"
    failure_seen = False
    for event in events:
        if event.id == failure_event.id:
            failure_seen = True
            continue
        if not failure_seen or event.type not in _GAP_PLAN_READY_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(event.causation_id or "") == failure_event.id
            or str(payload.get("source_event_id") or "") == failure_event.id
        ) and _same_replan_scope(event, failure_event):
            return None, "handled_by_gap_plan"
    # E4(prd-goal e2e finding-8):cap 曾数全量历史,后续 admission
    # 成功也不清零 → 每周期回声 escalate。只计"最近一次 stage 成功
    # 之后"的失败——成功即翻篇。
    success_event = str(
        getattr(getattr(stage, "aggregate", None), "success_event", "") or ""
    )
    last_success_index = -1
    if success_event:
        for index, event in enumerate(events):
            if (
                event.type == success_event
                and _same_run(event, failure_run_id)
                and _same_replan_scope(event, failure_event)
            ):
                last_success_index = index
    prior_failures = 0
    failure_index = -1
    for index, event in enumerate(events):
        if event.id == failure_event.id:
            failure_index = index
        if index <= last_success_index:
            continue
        if (
            event.type != failure_event.type
            or not _same_run(event, failure_run_id)
        ):
            continue
        if not _same_replan_scope(event, failure_event):
            continue
        if event.id == failure_event.id:
            continue
        prior_failures += 1
    # A reader child can emit its configured success_event before downstream
    # writer admission rejects the same Plan candidate.  Preserve the durable
    # rework lineage carried by the latest trigger so that this intermediate
    # success cannot reset attempt 2 back to attempt 1.
    try:
        lineage_attempt = int(origin_payload.get("rework_attempt") or 0)
    except (TypeError, ValueError):
        lineage_attempt = 0
    prior_failures = max(prior_failures, lineage_attempt)
    # ZF-E2E-PRDCTL-P2-7-2:trigger 资格判定——早于最近一次 stage 成功的
    # 失败事件是陈旧的,不得复活 replan(深水轮 12:34 复活 46 分钟前的
    # 旧失败实证;cap 计数窗已按成功翻篇,但 trigger 侧此前不设防)。
    if 0 <= failure_index <= last_success_index:
        return None, "stale_failure"
        # 本失败已 replan 过?(replay/echo 安全)
    if authorized_generation:
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                event.type == trigger_type
                and str(payload.get("stage_replan_generation") or "")
                == authorized_generation
            ):
                return None, "generation_already_started"
        prior_failures = 0
    else:
        for event in events:
            if (
                event.type == trigger_type
                and str(event.causation_id or "") == failure_event.id
            ):
                return None, "already_replanned"
        if prior_failures >= STAGE_REPLAN_CAP:
            return None, "cap_exhausted"
    target_ref = _payload_target_ref(failure_payload) or _payload_target_ref(
        origin_payload
    )
    if not target_ref and not failure_fanout_id:
        for event in reversed(events):
            if (
                not _same_run(event, failure_run_id)
                or not isinstance(event.payload, dict)
            ):
                continue
            if not _same_replan_scope(event, failure_event):
                continue
            target_ref = _payload_target_ref(event.payload)
            if target_ref:
                break
    findings = [
        dict(item)
        for item in failure_payload.get("findings", []) or []
        if isinstance(item, dict)
    ]
    diagnostics_ref = str(failure_payload.get("diagnostics_ref") or "").strip()
    diagnostic_reason = str(failure_payload.get("reason") or "").strip()
    if diagnostics_ref and diagnostic_reason and not any(
        str(item.get("message") or "") == diagnostic_reason
        for item in findings
    ):
        findings.append({
            "severity": "high",
            "category": str(
                "plan_compile_gate"
                if failure_payload.get("plan_compile_gate") == "failed"
                else "artifact_gate"
            ),
            "path": diagnostics_ref,
            "message": diagnostic_reason,
        })
    if not findings:
        findings = [{
            "severity": "high",
            "message": str(
                failure_payload.get("reason")
                or failure_payload.get("summary")
                or failure_event.type
            ),
        }]
    for key in (
        *_CANONICAL_INPUT_KEYS,
        "target_ref",
        "source_refs",
        "workflow_prompt_ref",
        "workflow_input_manifest_ref",
        "task_map_ref",
        "task_map_digest",
        "diagnostics_ref",
        "plan_compile_gate",
        "artifact_gate",
        *_PLAN_PACKAGE_IDENTITY_KEYS,
        "plan_admission_incident_id",
        "canonical_failure_event_id",
        "artifact_refs",
        "evidence_refs",
        "previous_plan_candidate_refs",
        "owner_confirmation_ref",
        "owner_confirmation",
        "owner_decision_items",
        "owner_decision_resolution",
    ):
        if key in _CURRENT_PLAN_IDENTITY_KEYS:
            continue
        value = failure_payload.get(key)
        if value not in (None, "", [], {}) and (
            key in _CANONICAL_INPUT_KEYS or not origin_payload.get(key)
        ):
            origin_payload[key] = value
    source_event_ids = {
        str(failure_payload.get("trigger_event_id") or "").strip(),
        str(failure_event.causation_id or "").strip(),
    }
    source_event_ids.discard("")
    source_identity_payload: dict[str, Any] = {}
    for source_event in reversed(events):
        if source_event.id not in source_event_ids:
            continue
        source_identity_payload = (
            source_event.payload
            if isinstance(source_event.payload, dict)
            else {}
        )
        break
    for identity_group in _CURRENT_PLAN_IDENTITY_GROUPS:
        for current_payload in (source_identity_payload, failure_payload):
            current_identity = {
                key: current_payload.get(key) for key in identity_group
            }
            if all(value not in (None, "") for value in current_identity.values()):
                origin_payload.update(current_identity)
        for key in identity_group:
            if origin_payload.get(key):
                continue
            value = failure_payload.get(key) or source_identity_payload.get(key)
            if value not in (None, ""):
                origin_payload[key] = value
    _bind_current_plan_package_identity(
        origin_payload,
        events=events,
        workflow_run_id=failure_run_id,
    )
    if target_ref:
        origin_payload.setdefault("target_ref", target_ref)
        if trigger_type.startswith("issue."):
            origin_payload.setdefault("issue_ref", target_ref)
        elif trigger_type.startswith("prd."):
            origin_payload.setdefault("prd_ref", target_ref)
    origin_payload.update({
        "rework_of": str(
            failure_payload.get("trigger_event_id") or failure_event.id
        ),
        "rework_attempt": prior_failures + 1,
        "rework_feedback": findings,
        "reason": (
            f"stage replan {prior_failures + 1}/{STAGE_REPLAN_CAP} after "
            f"{failure_event.type}"
        ),
    })
    if authorized_generation:
        origin_payload.update({
            "stage_replan_generation": authorized_generation,
            "stage_replan_generation_source_event_id": (
                generation_source_event_id
            ),
            "operator_authorized": True,
            "operator_authorization_ref": authorization_ref,
        })
    attempt_domain = str(
        getattr(stage, "attempt_domain", "") or ""
    ).strip()
    if attempt_domain:
        origin_payload["attempt_domain"] = attempt_domain
    return ZfEvent(
        type=trigger_type,
        actor="zf-cli",
        payload=origin_payload,
        causation_id=(
            authorization_event_id
            if authorized_generation and authorization_event_id
            else failure_event.id
        ),
        correlation_id=failure_event.correlation_id or None,
    ), f"replan {getattr(stage, 'id', '')}"


__all__ = [
    "STAGE_REPLAN_CAP",
    "plan_reader_stage_replan",
    "reader_stage_failure_candidates",
    "reader_stage_lineage_payload",
    "reader_stage_for_failure",
    "rework_source_from_payload",
    "reader_stage_failure_events",
    "superseding_candidate_ready",
]
