"""Self-healing for candidate-level rework (review/verify/judge rejection).

Task/candidate scope is classified from the recovery envelope. Candidate and
assembly events may retain a task_id for audit without becoming task-local.

ZaoFu's per-task rework (``_on_review_rejected`` → ``task.contract.rework_to``)
only fires when the rejection carries a ``task_id``. But in the candidate
fanout topology the review/verify/judge stages reject the WHOLE candidate
(``review.rejected`` / ``test.failed`` / ``judge.failed`` have ``task_id =
None``), so the per-task path no-ops and the validate→reject→rework→re-validate
loop never closes — the run stalls forever after the first rejection.

This module plans the missing candidate-level rework deterministically from
the event log: re-trigger the implementation stage (re-emit ``task_map.ready``
so ``_maybe_start_writer_fanout`` re-dispatches the writers) with reviewer
findings attached, capped at ``max_attempts`` before escalating.

Pure function (events in → planned actions out) so the orchestrator tick can run
it as a self-healing sweep and it stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from zf.core.events.module_parity import is_module_parity_scan_completed_event
from zf.core.events.model import ZfEvent
from zf.runtime.failure_kind import budget_candidate_failure_ids
from zf.runtime.candidate_rework_generation import reset_generation_caches, task_ids_from_payload
from zf.runtime.rework_triage import classify_rework_trigger, is_plan_level_task_contract_blocker
from zf.runtime.canonical_recovery import classify_recovery_scope
from zf.runtime.candidate_rework_identity import (
    _candidate_generation_stale,
    _candidate_scope_ref,
    _pdd_by_fanout_id,
    _pdd_from_event,
    _safe_int,
)
from zf.runtime.candidate_rework_fingerprint import (
    dedupe_feedback as _dedupe_feedback,
    rejection_fingerprint as _rejection_fingerprint,
)

if TYPE_CHECKING:
    from zf.autoresearch.bug_candidates import BugCandidate

CANDIDATE_FAIL_EVENTS = (
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "integration.failed",
    # B14-S6 (doc 93 §5): operator 拒绝 plan → 回喂 synth 重新拆解
    # (replan 分支携 reason/feedback,产新 task_map = 新 plan_id 再审)。
    "plan.rejected",
    # doc 78 W2: the candidate build cherry-pick conflict (two slices touching
    # the same files) is the most direct plan-level signal; recover it bounded
    # like the others and let triage route it to re-plan.
    "candidate.conflict",
)
RETRIGGER_EVENT = "task_map.ready"
REPLAN_EVENT = "orchestrator.replan_requested"
ESCALATE_EVENT = "human.escalate"

# doc 78 W2: failures whose root is the decomposition itself (slice overlap,
# spec/contract mismatch, phase-gate order) must re-PLAN, not re-implement the
# same task_map. classify_rework_trigger already routes these to arch.
PLAN_LEVEL_CLASSIFICATIONS = frozenset({"design_issue", "phase_gate_violation"})
CONTRACT_REPLAN_CATEGORIES = frozenset({
    "contract_fixture_gap",
    "parity_gap",
    "schema_gap",
})
STALE_TASK_MAP_SUGGESTED_ACTION = "use_latest_product_delivery_wave_ready"

# R28 (doc 93 §1/§5): admission/W1 机械拒的 fanout.cancelled reason 白名单。
# 只对「task_map 结构本身错、synth 必须重拆」的拒绝触发 replan;task 状态类
# (stale/missing/superseded/terminal)与 dedup/identity 取消**不在内** ——
# 白名单制 = 漏判退回 no_action 现状(零回归),绝不误把 stale/dedup 当重拆。
_ADMISSION_REPLAN_REASON_MARKERS = (
    "admission rejected task_map",  # pipeline 契约门:缺 assembly / 无 root owner
    "overlapping allowed paths",    # W1 跨切片路径重叠
    # avbs-r4 F5:task_map 结构级校验拒(schema_version 错写/verification
    # scope 越界)同属"synth 必须重拆",此前不在名单 → 静默停摆只能人工回炉
    "task_map validation failed",
)

_INFRA_FAILURE_MARKERS = (
    "pane is not running an agent process",
    "worker_transport_not_alive",
    "worker_not_dispatchable",
    "worker_state_not_dispatchable",
    "refusing to send task",
    "dispatch_deferred",
)

def _admission_replan_enabled(config: object) -> bool:
    """R28: 仅当 workflow.admission_replan.enabled 且配了 resynth_trigger 才生效。"""
    workflow = getattr(config, "workflow", None)
    replan = getattr(workflow, "admission_replan", None)
    return bool(
        getattr(replan, "enabled", False)
        and getattr(replan, "resynth_trigger", "")
    )


def _is_admission_replan_cancel(payload: dict) -> bool:
    # New writer admission incidents have a canonical upstream stage failure.
    # Their raw cancellation remains audit-only and must never consume the
    # candidate-level rework budget.  Keep the legacy reason marker path below
    # for historical logs that do not carry this explicit scope.
    if str(payload.get("failure_scope") or "") == "plan_admission":
        return False
    reason = str(payload.get("reason") or "").lower()
    return any(marker in reason for marker in _ADMISSION_REPLAN_REASON_MARKERS)


@dataclass(frozen=True)
class ReworkPlan:
    action: str  # "retrigger" | "replan" | "escalate"
    pdd_id: str
    trace_id: str
    target_ref: str
    attempt: int
    source_event_id: str
    source_event_type: str
    feedback: tuple[str, ...] = field(default_factory=tuple)
    failed_task_ids: tuple[str, ...] = field(default_factory=tuple)
    gap_tasks: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    classification: str = ""
    failure_fingerprint: str = ""
    failure_categories: tuple[str, ...] = field(default_factory=tuple)
    rework_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _CandidateClosure:
    index: int
    event_id: str
    event_type: str
    pdd_id: str
    trace_id: str
    target_ref: str
    candidate_ref: str


def _is_stale_task_map_candidate_failure(event: object, payload: dict) -> bool:
    if getattr(event, "type", "") != "fanout.child.failed":
        return False
    if classify_recovery_scope(event) != "candidate":
        return False
    reason = str(payload.get("reason") or "").strip()
    if reason != "stale_task_map":
        return False
    suggested = str(payload.get("suggested_action") or "").strip()
    return suggested in {"", STALE_TASK_MAP_SUGGESTED_ACTION}


def _fingerprint_counting_enabled(config: object) -> bool:
    goal = getattr(config, "goal", None)
    return bool(getattr(goal, "rework_fingerprint", False))


def plan_candidate_rework(
    events: list,
    *,
    max_attempts: int = 2,
    config: object = None,
) -> list[ReworkPlan]:
    """Return one ReworkPlan per unhandled candidate-level rejection.

    "Handled" = a later ``task_map.ready`` carries ``rework_of == <rejection
    event id>``; attempts count prior re-triggers per pdd_id, and at/over
    ``max_attempts`` the plan escalates instead of re-triggering.
    """
    infra_failure_ids = _infra_only_candidate_failure_ids(events)
    budget_failure_ids = budget_candidate_failure_ids(events, CANDIDATE_FAIL_EVENTS)
    no_attempt_ids = infra_failure_ids | budget_failure_ids
    handled_by_event: dict[str, set[str]] = {}
    attempts_by_pdd: dict[str, set[str]] = {}
    attempts_by_pdd_source: dict[tuple[str, str], set[str]] = {}
    feedback_by_trace: dict[str, list[str]] = {}
    failed_task_ids_by_trace: dict[str, set[str]] = {}
    gap_tasks_by_trace: dict[str, list[dict[str, Any]]] = {}
    pdd_by_fanout_id = _pdd_by_fanout_id(events)
    success_closures = _candidate_success_closures(
        events,
        pdd_by_fanout_id=pdd_by_fanout_id,
    )
    event_type_by_id: dict[str, str] = {}
    rejection_fp_by_id: dict[str, str] = {}
    fingerprint_on = _fingerprint_counting_enabled(config)
    rejections: list = []
    # R28: admission/W1 机械拒 → 回 synth,仅在 config 开关打开时识别(默认关 =
    # fanout.cancelled 落入 no_action 现状,零回归)。
    admission_replan_on = _admission_replan_enabled(config)

    for event_idx, event in enumerate(events):
        etype = getattr(event, "type", "")
        event_id = str(getattr(event, "id", ""))
        if event_id:
            event_type_by_id[event_id] = str(etype)
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        reset_generation_caches(
            event,
            payload,
            boundary_event_types={RETRIGGER_EVENT, REPLAN_EVENT},
            feedback_by_trace=feedback_by_trace,
            failed_task_ids_by_trace=failed_task_ids_by_trace,
            gap_tasks_by_trace=gap_tasks_by_trace,
        )
        if event_id and (
            etype in CANDIDATE_FAIL_EVENTS or etype.endswith(".child.failed")
        ):
            rejection_fp_by_id[event_id] = _rejection_fingerprint(payload)
        if etype == "fanout.started":
            fanout_id = str(payload.get("fanout_id") or "").strip()
            fanout_pdd = str(payload.get("pdd_id") or "").strip()
            if fanout_id and fanout_pdd:
                pdd_by_fanout_id[fanout_id] = fanout_pdd
        integration_only_resume = (
            etype == "workflow.resume.applied"
            and str(payload.get("mode") or "")
            == "candidate_rework_integration_only"
        )
        if etype in (
            RETRIGGER_EVENT,
            REPLAN_EVENT,
            ESCALATE_EVENT,
            # 批B:微环注入 = 该拒收已被处理(并计入指纹 attempt,
            # 同指纹再拒时停滞判定正常工作)
            "task.rework.continuation_injected",
        ) or integration_only_resume:
            rework_of = str(
                payload.get("rework_of")
                or (payload.get("source_event_id") if integration_only_resume else "")
                or ""
            )
            if not rework_of:
                continue
            pdd = str(payload.get("pdd_id") or "")
            handled_by_event.setdefault(rework_of, set()).add(pdd)
            if etype != ESCALATE_EVENT and rework_of not in no_attempt_ids:
                # Count unique handled source events, not raw task_map.ready rows.
                # A replay/dedupe bug can emit equivalent task_map.ready events
                # for the same rework_of; double-counting them prematurely
                # exhausts later review/test rework.
                attempts_by_pdd.setdefault(pdd, set()).add(rework_of)
                source = str(
                    payload.get("rework_source")
                    or event_type_by_id.get(rework_of)
                    or ""
                )
                if source:
                    key = (pdd, source)
                    attempts_by_pdd_source.setdefault(key, set()).add(rework_of)
        elif etype == "diagnosis.completed":
            # Tier-2(doc 131 §5):诊断结论 route_to_lane 经 feedback 管线
            # 回流 replan——归因证据链直接喂给 synth,替代人肉转述。
            report = payload.get("report")
            report = report if isinstance(report, dict) else {}
            if str(report.get("next_action") or "") == "route_to_lane":
                trace = str(
                    payload.get("trace_id")
                    or getattr(event, "correlation_id", "")
                    or ""
                )
                hypothesis = str(
                    report.get("root_cause_hypothesis") or "",
                ).strip()
                target = str(
                    report.get("target_lane") or report.get("fix_target") or "?"
                )
                evidence = str(report.get("attribution_evidence") or "")[:200]
                if hypothesis:
                    feedback_by_trace.setdefault(trace, []).append(
                        f"diagnosis→{target}: {hypothesis[:220]}"
                        + (f" (evidence: {evidence})" if evidence else "")
                    )
        elif etype == "dev.failed":
            # FIX-11(bizsim r4 F11):worker 诚实拒单(implicated surface 不在
            # 本 lane)是归因错误的最强反证——r4 里 dev-sim-core 拒了被误
            # 路由的 five-camera 修复。拒单理由必须回流 replan 输入,否则
            # synth 下一轮还往同一条错 lane 派。
            reason = str(payload.get("reason") or "").strip()
            if reason:
                trace = str(
                    payload.get("trace_id")
                    or getattr(event, "correlation_id", "")
                    or ""
                )
                who = str(
                    payload.get("child_id") or payload.get("role_instance") or "?"
                )
                feedback_by_trace.setdefault(trace, []).append(
                    f"worker-rejection {who}: {reason[:220]}"
                )
        elif is_plan_level_task_contract_blocker(event):
            trace = str(
                payload.get("trace_id")
                or getattr(event, "correlation_id", "")
                or ""
            )
            reason = str(payload.get("reason") or payload.get("summary") or "").strip()
            if reason:
                feedback_by_trace.setdefault(trace, []).append(
                    f"task-contract-blocker {event.task_id or '?'}: {reason}"
                )
            task_ids = task_ids_from_payload(payload)
            if event.task_id:
                task_ids.add(str(event.task_id))
            blocker_ids = payload.get("blocker_task_ids")
            if not isinstance(blocker_ids, list):
                blocker_ids = [payload.get("blocked_on_task")]
            task_ids.update(
                str(item).strip()
                for item in blocker_ids
                if str(item or "").strip()
            )
            if task_ids:
                failed_task_ids_by_trace.setdefault(trace, set()).update(task_ids)
            rejections.append(event)
        elif etype.endswith(".child.failed"):
            trace = str(payload.get("trace_id") or getattr(event, "correlation_id", "") or "")
            task_ids = task_ids_from_payload(payload)
            if task_ids:
                failed_task_ids_by_trace.setdefault(trace, set()).update(task_ids)
            reason = str(payload.get("reason") or "").strip()
            if reason:
                feedback_by_trace.setdefault(trace, []).append(
                    f"{payload.get('child_id', '?')}: {reason}"
                )
            feedback_by_trace.setdefault(trace, []).extend(
                _feedback_lines_from_payload(payload)
            )
            gap_tasks = _gap_tasks_from_payload(payload)
            if gap_tasks:
                gap_tasks_by_trace.setdefault(trace, []).extend(gap_tasks)
            if _is_stale_task_map_candidate_failure(event, payload):
                if _candidate_generation_stale(
                    events,
                    event_idx=event_idx,
                    event=event,
                    payload=payload,
                    pdd_by_fanout_id=pdd_by_fanout_id,
                ):
                    continue
                if _candidate_failure_superseded(
                    event,
                    payload,
                    event_idx,
                    pdd_by_fanout_id=pdd_by_fanout_id,
                    success_closures=success_closures,
                ):
                    continue
                rejections.append(event)
        elif etype in CANDIDATE_FAIL_EVENTS and classify_recovery_scope(event) == "candidate":
            if event_id in infra_failure_ids:
                continue
            if _candidate_generation_stale(
                events,
                event_idx=event_idx,
                event=event,
                payload=payload,
                pdd_by_fanout_id=pdd_by_fanout_id,
            ):
                continue
            if _candidate_failure_superseded(
                event,
                payload,
                event_idx,
                pdd_by_fanout_id=pdd_by_fanout_id,
                success_closures=success_closures,
            ):
                continue
            trace = str(
                payload.get("trace_id")
                or getattr(event, "correlation_id", "")
                or ""
            )
            feedback = _feedback_lines_from_payload(payload)
            if feedback:
                feedback_by_trace.setdefault(trace, []).extend(feedback)
            gap_tasks = _gap_tasks_from_payload(payload)
            if gap_tasks:
                gap_tasks_by_trace.setdefault(trace, []).extend(gap_tasks)
            task_ids = task_ids_from_payload(payload)
            if task_ids:
                failed_task_ids_by_trace.setdefault(trace, set()).update(task_ids)
            rejections.append(event)
        elif (
            admission_replan_on
            and etype == "fanout.cancelled"
            and classify_recovery_scope(event) == "candidate"
            and _is_admission_replan_cancel(payload)
        ):
            # R28 (doc 93 §1/§5): admission/W1 拒了 task_map → synth 必须重拆。
            # 把 admission reason 作为 feedback 喂回(orchestrator_fanout 已有
            # rework_feedback→briefing 管线),synth 据此补 root owner / assembly。
            trace = str(
                payload.get("trace_id") or getattr(event, "correlation_id", "") or ""
            )
            reason = str(payload.get("reason") or "").strip()
            if reason:
                feedback_by_trace.setdefault(trace, []).append(f"admission: {reason}")
            if _candidate_generation_stale(
                events,
                event_idx=event_idx,
                event=event,
                payload=payload,
                pdd_by_fanout_id=pdd_by_fanout_id,
            ):
                continue
            if _candidate_failure_superseded(
                event,
                payload,
                event_idx,
                pdd_by_fanout_id=pdd_by_fanout_id,
                success_closures=success_closures,
            ):
                continue
            rejections.append(event)

    # Dedupe to at most one rework per pdd (the latest unhandled rejection):
    # multiple child rejections / re-evaluations of the same candidate must
    # not each fire a writer fanout.
    latest_by_pdd: dict[str, object] = {}
    for event in rejections:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        pdd = _pdd_from_event(
            payload,
            str(payload.get("target_ref") or ""),
            pdd_by_fanout_id=pdd_by_fanout_id,
        )
        if pdd:
            latest_by_pdd[pdd] = event

    plans: list[ReworkPlan] = []
    for pdd, event in latest_by_pdd.items():
        if pdd in handled_by_event.get(str(getattr(event, "id", "")), set()):
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        target_ref = str(payload.get("target_ref") or "")
        trace = str(payload.get("trace_id") or getattr(event, "correlation_id", "") or "")
        source_event_type = str(getattr(event, "type", ""))
        feedback = tuple(_dedupe_feedback(feedback_by_trace.get(trace, [])))
        failed_task_ids = tuple(sorted(failed_task_ids_by_trace.get(trace, set())))
        gap_tasks = tuple(_dedupe_gap_tasks(gap_tasks_by_trace.get(trace, [])))
        source_attempts = attempts_by_pdd_source.get((pdd, source_event_type), set())
        ineffective_rejection = False
        current_fp = _rejection_fingerprint(payload)
        if fingerprint_on:
            # U2:同 findings 指纹才计满(doom-loop 形态);findings 在
            # 前进 → 新预算,不误报 escalate(r6.1 续跑 6 次误报实弹)。
            source_attempts = {
                rework_id for rework_id in source_attempts
                if rejection_fp_by_id.get(rework_id) == current_fp
            }
            # U22:被审 candidate 落后于最新交付 → 驳回无效,不计 cap
            # (触发 r6.1 续跑停机的第 12 轮正是此类伪拒)。
            from zf.runtime.rejection_validity import rejection_effective

            check_ids = task_ids_from_payload(payload) or sorted(
                failed_task_ids_by_trace.get(trace, set())
            )
            for check_id in check_ids:
                verdict = rejection_effective(
                    events,
                    task_id=check_id,
                    rejection_event_id=str(getattr(event, "id", "")),
                )
                if not verdict.effective:
                    ineffective_rejection = True
                    break
        if source_attempts:
            attempt = len(source_attempts)
        elif any(key_pdd == pdd for key_pdd, _source in attempts_by_pdd_source):
            # If prior attempts have an explicit source and this failure source
            # has none, start a fresh source budget. Example: stale_task_map
            # runtime recovery must not consume the later review.rejected budget.
            attempt = 0
        else:
            # Backward-compatible fallback for older logs/tests whose rework
            # markers did not include rework_source and whose source event is
            # absent from the window.
            attempt = len(attempts_by_pdd.get(pdd, set()))
        classification = _classify(event, config)
        failure_categories = _failure_categories(event, feedback)
        repeated_contract_verify = (
            source_event_type == "verify.failed"
            and attempt >= 1
            and bool(CONTRACT_REPLAN_CATEGORIES & set(failure_categories))
        )
        if fingerprint_on and ineffective_rejection:
            attempt = 0
            repeated_contract_verify = False
            classification = "rejection_ineffective_candidate_behind"
        if str(getattr(event, "id", "")) in budget_failure_ids:
            # ZF-E2E-PRDCTL-P0-1:预算失败是 funding 决策非质量 finding,
            # retrigger 只会把付费段重新撞进同一道预算闸——路由 owner。
            classification = "budget_blocked"
            action = "escalate"
        elif attempt >= max_attempts:
            action = "escalate"
        elif source_event_type == "plan.rejected":
            # B14-S6: operator 拒绝即 plan 级 — 恒走 replan(回喂 synth),
            # 绝不 retrigger 同一张被拒的 task_map。
            action = "replan"
        elif source_event_type == "fanout.cancelled":
            # R28: admission/W1 机械拒同样是 plan 级 — task_map 结构错,必须
            # 回 synth 重拆,绝不 retrigger 同一张被拒的 task_map(否则 admission
            # 再拒 → 烧 cap → escalate,等于慢性 stall)。
            action = "replan"
        elif repeated_contract_verify:
            classification = "contract_freeze_gap"
            action = "replan"
        elif classification in PLAN_LEVEL_CLASSIFICATIONS:
            action = "replan"
        else:
            action = "retrigger"
        plans.append(ReworkPlan(
            action=action,
            pdd_id=pdd,
            trace_id=trace,
            target_ref=target_ref,
            attempt=attempt + 1,
            source_event_id=str(getattr(event, "id", "")),
            source_event_type=source_event_type,
            feedback=feedback,
            failed_task_ids=failed_task_ids,
            gap_tasks=gap_tasks,
            classification=classification,
            failure_fingerprint=current_fp,
            failure_categories=failure_categories,
            rework_summary=_rework_summary(
                pdd_id=pdd,
                source_event_type=source_event_type,
                attempt=attempt + 1,
                action=action,
                classification=classification,
                categories=failure_categories,
                feedback=feedback,
                failed_task_ids=failed_task_ids,
                gap_tasks=gap_tasks,
            ),
        ))
    return plans


def _candidate_success_closures(
    events: list,
    *,
    pdd_by_fanout_id: dict[str, str],
) -> list[_CandidateClosure]:
    closures: list[_CandidateClosure] = []
    for idx, event in enumerate(events):
        etype = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        if not _is_candidate_success_closure(etype, payload):
            continue
        target_ref = _candidate_scope_ref(payload)
        closures.append(_CandidateClosure(
            index=idx,
            event_id=str(getattr(event, "id", "") or ""),
            event_type=etype,
            pdd_id=_pdd_from_event(
                payload,
                target_ref,
                pdd_by_fanout_id=pdd_by_fanout_id,
            ),
            trace_id=str(
                payload.get("trace_id")
                or getattr(event, "correlation_id", "")
                or ""
            ).strip(),
            target_ref=target_ref,
            candidate_ref=str(payload.get("candidate_ref") or "").strip(),
        ))
    return closures


def _is_candidate_success_closure(etype: str, payload: dict[str, Any]) -> bool:
    if is_module_parity_scan_completed_event(etype):
        return (
            "open_p0_p1_gap_count" in payload
            and _safe_int(payload.get("open_p0_p1_gap_count")) == 0
        )
    if etype == "module.parity.closed":
        return True
    return etype in {
        "candidate.ready",
        "candidate.quality.passed",
        "verify.passed",
        "judge.passed",
    }


def _candidate_failure_superseded(
    event: object,
    payload: dict[str, Any],
    event_idx: int,
    *,
    pdd_by_fanout_id: dict[str, str],
    success_closures: list[_CandidateClosure],
) -> bool:
    if not success_closures:
        return False
    target_ref = _candidate_scope_ref(payload)
    failure_pdd = _pdd_from_event(
        payload,
        target_ref,
        pdd_by_fanout_id=pdd_by_fanout_id,
    )
    failure_trace = str(
        payload.get("trace_id") or getattr(event, "correlation_id", "") or ""
    ).strip()
    failure_candidate = str(payload.get("candidate_ref") or "").strip()
    if not (failure_pdd or failure_trace or target_ref or failure_candidate):
        return False
    for closure in success_closures:
        if closure.index <= event_idx:
            continue
        if _candidate_closure_matches_failure(
            closure,
            pdd_id=failure_pdd,
            trace_id=failure_trace,
            target_ref=target_ref,
            candidate_ref=failure_candidate,
        ):
            return True
    return False


def _candidate_closure_matches_failure(
    closure: _CandidateClosure,
    *,
    pdd_id: str,
    trace_id: str,
    target_ref: str,
    candidate_ref: str,
) -> bool:
    closure_refs = {
        ref
        for ref in (closure.target_ref, closure.candidate_ref)
        if ref
    }
    failure_refs = {
        ref
        for ref in (target_ref, candidate_ref)
        if ref
    }
    pdd_match = bool(pdd_id and closure.pdd_id and pdd_id == closure.pdd_id)
    trace_match = bool(trace_id and closure.trace_id and trace_id == closure.trace_id)
    ref_match = bool(closure_refs and failure_refs and closure_refs & failure_refs)
    if pdd_id and closure.pdd_id and not pdd_match:
        return False
    if pdd_match and (trace_match or ref_match or not trace_id or not closure.trace_id):
        return True
    if trace_match and (ref_match or not pdd_id or not closure.pdd_id):
        return True
    return ref_match and (pdd_match or trace_match)


def _infra_only_candidate_failure_ids(events: list) -> set[str]:
    """Candidate failures caused only by harness/worker transport issues.

    These failures are real operational signals, but they are not reviewer
    findings about the candidate. They must not consume the bounded business
    rework budget; otherwise a dead pane can burn an implementation/replan
    attempt and escalate the next real verify failure prematurely.
    """
    child_reasons_by_fanout: dict[str, list[str]] = {}
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        if not str(getattr(event, "type", "")).endswith(".child.failed"):
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id:
            continue
        reason = str(payload.get("reason") or "").strip()
        if reason:
            child_reasons_by_fanout.setdefault(fanout_id, []).append(reason)

    out: set[str] = set()
    for event in events:
        event_id = str(getattr(event, "id", "") or "")
        if not event_id:
            continue
        etype = str(getattr(event, "type", "") or "")
        if etype not in CANDIDATE_FAIL_EVENTS:
            continue
        if classify_recovery_scope(event) != "candidate":
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        direct_text = _payload_text(payload).lower()
        if _contains_infra_failure_marker(direct_text):
            out.add(event_id)
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_reasons = child_reasons_by_fanout.get(fanout_id, [])
        if child_reasons and all(
            _contains_infra_failure_marker(reason.lower())
            for reason in child_reasons
        ):
            out.add(event_id)
    return out


def _contains_infra_failure_marker(text: str) -> bool:
    return any(marker in text for marker in _INFRA_FAILURE_MARKERS)


def _feedback_lines_from_payload(payload: dict) -> list[str]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    if not isinstance(findings, list):
        findings = []
    lines: list[str] = []
    seen: set[str] = set()
    for item in findings:
        if isinstance(item, dict):
            task_id = str(item.get("task_id") or item.get("child_id") or "").strip()
            message = str(
                item.get("message")
                or item.get("summary")
                or item.get("title")
                or item.get("reason")
                or ""
            ).strip()
            command = str(item.get("verification_command") or "").strip()
            category = str(item.get("category") or "").strip()
            parts = []
            if task_id:
                parts.append(task_id)
            if category:
                parts.append(category)
            prefix = " / ".join(parts)
            line = f"{prefix}: {message}" if prefix else message
            if command:
                line = f"{line} (verify: {command})"
        else:
            line = str(item).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _gap_tasks_from_payload(payload: dict) -> list[dict[str, Any]]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    out: list[dict[str, Any]] = []
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            gap_task = item.get("gap_task")
            if isinstance(gap_task, dict):
                out.append(dict(gap_task))
            gap_tasks = item.get("gap_tasks")
            if isinstance(gap_tasks, list):
                out.extend(dict(task) for task in gap_tasks if isinstance(task, dict))
    payload_gap_tasks = payload.get("gap_tasks")
    if isinstance(payload_gap_tasks, list):
        out.extend(dict(task) for task in payload_gap_tasks if isinstance(task, dict))
    return out


def _dedupe_gap_tasks(gap_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in gap_tasks:
        task_id = str(task.get("task_id") or task.get("id") or "").strip()
        key = task_id or repr(sorted(task.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(task)
    return out


def _rework_summary(
    *,
    pdd_id: str,
    source_event_type: str,
    attempt: int,
    action: str,
    classification: str,
    categories: tuple[str, ...],
    feedback: tuple[str, ...],
    failed_task_ids: tuple[str, ...],
    gap_tasks: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "pdd_id": pdd_id,
        "source_event_type": source_event_type,
        "attempt": attempt,
        "action": action,
        "classification": classification,
        "categories": list(categories),
        "failed_task_ids": list(failed_task_ids),
        "gap_tasks": list(gap_tasks),
        "feedback_count": len(feedback),
        "feedback_excerpt": list(feedback[:5]),
        # FIX-11(bizsim r4 F11):归因是判断题归 synth,但合约要求它给出
        # 证据链——失败测试→牵涉文件/包→目标 lane,并顺带任何 worker
        # 拒单反证。r4 的 five-camera 误路由渲染 lane 即缺这一环。
        "attribution_contract": (
            "Route the rework to the lane whose owned files the failure "
            "actually implicates. Provide attribution_evidence: failing "
            "check -> implicated files/packages -> target lane. Treat any "
            "worker-rejection feedback above as counter-evidence against "
            "the previous routing."
        ),
    }


def _failure_categories(event: object, feedback: tuple[str, ...]) -> tuple[str, ...]:
    payload = getattr(event, "payload", {}) or {}
    text = " ".join([_payload_text(payload), *feedback]).lower()
    categories: list[str] = []
    if any(
        marker in text
        for marker in (
            "fixture",
            "golden",
            "contract mismatch",
            "frozen contract",
            "shape-only",
            "shape only",
            "completion",
            "replace_from",
            "request_id",
            "tool_id",
        )
    ):
        categories.append("contract_fixture_gap")
    if any(
        marker in text
        for marker in (
            "parity",
            "python reference",
            "python-covered",
            "snake_case",
            "camelcase",
            "camel_case",
            "allowedevents",
            "allowed events",
            "ignored-event",
            "arguments_delta",
            "argumentsdelta",
        )
    ):
        categories.append("parity_gap")
    if any(
        marker in text
        for marker in (
            "schema",
            "fts",
            "index",
            "session_db_schema",
            "redaction prefix",
        )
    ):
        categories.append("schema_gap")
    if any(
        marker in text
        for marker in (
            "missing script:",
            "unknown script",
            "script not found",
            "quality_gate_contract_mismatch",
            "candidate_quality_gate_contract_mismatch",
        )
    ):
        categories.append("quality_gate_contract_gap")
    if any(
        marker in text
        for marker in (
            "frozen-lockfile",
            "pnpm-lock",
            "package-lock",
            "lockfile",
            "workspace package dependency",
        )
    ):
        categories.append("workspace_quality_gap")
    return tuple(dict.fromkeys(categories))


def _payload_text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(_payload_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_payload_text(item) for item in value)
    return str(value or "")


def _classify(event: object, config: object = None) -> str:
    """Classification bucket for a candidate-level rejection (plan vs impl).

    ``config`` is threaded through so the operator's yaml
    ``workflow.rework_routing`` override stays authoritative on the replan
    decision (classify_rework_trigger honors it only when config is passed).
    """
    zf_event = event if isinstance(event, ZfEvent) else ZfEvent(
        type=str(getattr(event, "type", "")),
        actor="zf-cli",
        payload=getattr(event, "payload", {}) or {},
    )
    try:
        return classify_rework_trigger(zf_event, config=config).classification
    except Exception:
        return ""


def quarantine_candidate_from_plan(plan: ReworkPlan) -> "BugCandidate":
    """B12 (doc 92 §5): cap 耗尽 → 铸 issue 候选(quarantine 形态)。

    rework 额度烧光后绝不静默停摆(R25 三轮 reject 触顶即无下文的
    反例):把 trace 全部证据(findings/attempt 历史)降级回治理平面
    (backlogs/)等人批。dedupe = trace_id + 失败事件类型(同 trace 同
    stage 不重复铸)。纯函数 —— 落盘与事件由调用方做。
    """
    from zf.autoresearch.bug_candidates import BugCandidate, _stable_bug_id

    dedupe_key = f"quarantine:{plan.trace_id or plan.pdd_id}:{plan.source_event_type}"
    findings = "; ".join(plan.feedback) or "(no findings captured)"
    return BugCandidate(
        bug_id=_stable_bug_id(dedupe_key),
        dedupe_key=dedupe_key,
        status="proposed",
        priority="P0",
        failure_class=plan.source_event_type,
        title=(
            f"rework exhausted: {plan.pdd_id} "
            f"({plan.attempt - 1} attempts, findings unresolved)"
        ),
        summary=(
            f"candidate-level rework cap exhausted for pdd {plan.pdd_id} "
            f"after {plan.attempt - 1} attempts; unresolved reviewer "
            f"findings: {findings}"
        ),
        expected="reviewer findings resolved within rework cap",
        actual=f"cap exhausted; findings still open: {findings[:400]}",
        event_ids=[plan.source_event_id] if plan.source_event_id else [],
        severity="high",
        source_kind="quarantine",
        affinity_tag=plan.pdd_id,
    )


def candidate_quality_failure_message(quality: Any) -> str:
    """Rework message for a quality-gate failure, carrying the failing check's
    actual output (both stdout+stderr tails) so rework sees the real cause — e.g.
    ``Cannot find module '../lib/task-store'`` — not just the command that ran.
    Without it the agent re-guesses and exhausts the rework cap into escalate.
    `node --test` writes failures to stdout while stderr is often only npm noise,
    so keep both; the payload already tail-bounds each stream and the error can
    sit mid-tail (the summary trails it), so re-truncate only the joined body.
    """
    header = "candidate quality gates failed"
    if not isinstance(quality, dict):
        return header
    excerpts: list[str] = []
    for checks in (quality.get("gate_checks") or {}).values():
        for check in checks if isinstance(checks, list) else []:
            if not isinstance(check, dict) or check.get("passed"):
                continue
            command = str(check.get("command") or "").strip()
            streams = [s for s in (
                str(check.get("stdout_tail") or "").strip(),
                str(check.get("stderr_tail") or "").strip(),
            ) if s]
            if not command and not streams:
                continue
            excerpts.append("\n".join([f"$ {command}".rstrip(), *streams]))
    if not excerpts:
        return header
    return header + "\n\n" + "\n\n".join(excerpts[:5])[:4000]
