"""Structural stage-progression stall detector — feeds the authorized self-repair loop.

backlog 2026-06-05-1329 (replaces the time-threshold detector 43bcd35 + the agent
phantom-stall patch 858b2a3). autoresearch only catches FATAL signals; a SILENT
stall — a stage's trigger fired but the kernel never dispatched the stage (e.g.
candidate.ready fired but the verify fanout never started, the cj-min R10/R11
blocker) — produces no fatal, so the loop never auto-triggered on it.

The detection is STRUCTURAL, not time-based. The kernel already records what it
dispatched: when a stage's ``trigger`` fires, the kernel either starts the stage
(``fanout.started`` with that stage_id), cancels it (``fanout.cancelled``), or
the stage's ``success_event`` fires. If the trigger fired but NONE of those
followed (the stage was silently skipped), that is a structural stall.

This never false-positives on a slow-but-progressing stage — a slow stage DID
start (fanout.started); it is merely slow internally. The old time-threshold
detector fired on exactly that (the R11 scan→plan phantom stall). It anchors to
the kernel's own dispatch records, so it points the self-repair loop at the real
blocker (a stage that never started) rather than a slow one.
"""

from __future__ import annotations

from dataclasses import dataclass

from zf.core.events.model import ZfEvent
from zf.core.workflow.flow_metadata import flow_kind_from_payload, normalize_flow_kind
from zf.runtime.terminal_events import terminal_after_event

# Events with no stage_id that nonetheless mean "the kernel handled the trigger".
_FANOUT_START = "fanout.started"
_FANOUT_CANCEL = "fanout.cancelled"
# Grace: the dispatch (fanout.started) lands in the same run_once as the trigger,
# so within a few events. Require this many kernel events AFTER the trigger with
# no start/cancel/success before calling it a structural stall — guards only
# against the detector running in the same instant the trigger fired.
DEFAULT_MIN_EVENTS_AFTER = 5


@dataclass(frozen=True)
class StallFinding:
    trigger: str
    stage_id: str
    success_event: str
    feature_id: str
    fingerprint: str
    trigger_event_id: str = ""
    workflow_run_id: str = ""
    trace_id: str = ""
    fanout_id: str = ""
    flow_kind: str = ""


def _payload(event) -> dict:
    p = getattr(event, "payload", {}) or {}
    return p if isinstance(p, dict) else {}


def _event_id(event, *, fallback: str) -> str:
    return str(getattr(event, "id", "") or fallback)


def _is_original_trigger(event) -> bool:
    """Redispatches are recovery attempts, never independent root triggers."""

    if str(getattr(event, "actor", "") or "") == "zf-stall-redispatch":
        return False
    payload = _payload(event)
    return not str(payload.get(_REDISPATCH_FINGERPRINT) or "")


def _trigger_targets_stage(
    event,
    *,
    stage_id: str,
    trigger: str,
    flow_kind: str = "",
) -> bool:
    """Whether a shared lane handoff actually enters ``stage_id``.

    ``lane.stage.completed`` is deliberately reused by every lane slot.  A
    terminal verify handoff therefore has the same event type as the impl ->
    verify trigger, but it carries an empty ``next_stage_slot``.  Treating the
    latest event by type as the next trigger creates a self-redispatch loop
    after a healthy verify completion.

    Ordinary unique trigger events retain their existing semantics.  For a
    lane handoff, the producer's explicit next slot is the routing contract.
    """

    payload = _payload(event)
    expected_kind = normalize_flow_kind(flow_kind)
    observed_kind = flow_kind_from_payload(payload)
    if expected_kind and observed_kind and expected_kind != observed_kind:
        return False
    if trigger != "lane.stage.completed":
        return True
    expected_slot = stage_id.rsplit("-", 1)[-1]
    if str(payload.get("next_stage_slot") or "") != expected_slot:
        return False
    pipeline_id = str(payload.get("pipeline_id") or "").strip()
    if pipeline_id and stage_id != f"{pipeline_id}-{expected_slot}":
        return False
    return True


def _matches_trigger_scope(event, trigger_event, *, stage_id: str) -> bool:
    """Avoid treating another lane/run's terminal fact as this stage's proof."""

    payload = _payload(event)
    trigger_payload = _payload(trigger_event)
    observed_stage = str(payload.get("stage_id") or "")
    if observed_stage and observed_stage != stage_id:
        return False
    for key in ("workflow_run_id", "run_id", "trace_id"):
        expected = str(trigger_payload.get(key) or "")
        observed = str(payload.get(key) or "")
        if expected and observed and expected != observed:
            return False
    return True


def detect_structural_stalls(
    events,
    *,
    stages,
    min_events_after: int = DEFAULT_MIN_EVENTS_AFTER,
) -> list[StallFinding]:
    """A stage whose ``trigger`` fired but which never started, cancelled, or
    succeeded — and which has since been bypassed by ``min_events_after`` kernel
    events — is a structural stall. ``stages`` is an iterable of
    ``(stage_id, trigger, success_event[, flow_kind])``.
    """
    seq = list(enumerate(events))
    if not seq:
        return []
    total = len(seq)
    feature_id = ""
    for _, event in seq:
        payload = _payload(event)
        fid = str(payload.get("feature_id") or payload.get("pdd_id") or "")
        if fid:
            feature_id = fid

    findings: list[StallFinding] = []
    for stage in stages:
        stage_id, trigger, success_event, *scope = stage
        stage_flow_kind = normalize_flow_kind(scope[0] if scope else "")
        stage_id, trigger = str(stage_id), str(trigger)
        if not stage_id or not trigger:
            continue
        # The latest *upstream* trigger is the active attempt.  A recovery
        # redispatch must never become a new root and renew its own stall.
        trigger_idx = None
        trigger_event = None
        for idx, event in seq:
            if (
                getattr(event, "type", "") == trigger
                and _is_original_trigger(event)
                and _trigger_targets_stage(
                    event,
                    stage_id=stage_id,
                    trigger=trigger,
                    flow_kind=stage_flow_kind,
                )
            ):
                trigger_idx = idx
                trigger_event = event
        if trigger_idx is None:
            continue
        assert trigger_event is not None
        # did the kernel handle it after the trigger?
        handled = terminal_after_event([event for _, event in seq], trigger_event) is not None
        for idx, event in seq:
            if idx <= trigger_idx:
                continue
            etype = getattr(event, "type", "")
            if (
                etype == success_event
                and success_event
                and _matches_trigger_scope(event, trigger_event, stage_id=stage_id)
            ):
                handled = True
                break
            if etype in (_FANOUT_START, _FANOUT_CANCEL):
                if (
                    str(_payload(event).get("stage_id") or "") == stage_id
                    and _matches_trigger_scope(event, trigger_event, stage_id=stage_id)
                ):
                    handled = True
                    break
            # B14-S4 (doc 93 §6): awaiting_approval 是合法 hold 态 —
            # 审批门 hold 不是 stall,re-arm 重发会绕过人批(自愈机制
            # "修复"掉审核门是必须显式排除的失效模式)。approved/
            # rejected 后该 hold 终结:approved → fanout.started 很快
            # 跟上(正常 handled);rejected → rework 产新 trigger。
            if (
                etype == "plan.approval.requested"
                and str(_payload(event).get("stage_id") or "") == stage_id
                and _matches_trigger_scope(event, trigger_event, stage_id=stage_id)
            ):
                handled = True
                break
        if handled:
            continue
        if (total - 1 - trigger_idx) < min_events_after:
            continue  # trigger just fired; the kernel hasn't had its turn yet
        trigger_payload = _payload(trigger_event)
        trigger_event_id = _event_id(trigger_event, fallback=f"index-{trigger_idx}")
        workflow_run_id = str(
            trigger_payload.get("workflow_run_id")
            or trigger_payload.get("run_id")
            or ""
        )
        trace_id = str(
            trigger_payload.get("trace_id")
            or getattr(trigger_event, "correlation_id", "")
            or ""
        )
        fanout_id = str(trigger_payload.get("fanout_id") or "")
        identity_scope = workflow_run_id or trace_id or "legacy"
        findings.append(StallFinding(
            trigger=trigger,
            stage_id=stage_id,
            success_event=str(success_event or ""),
            feature_id=feature_id,
            fingerprint=(
                f"stall:{identity_scope}:{stage_id}:{fanout_id or '-'}:"
                f"{trigger_event_id}"
            ),
            trigger_event_id=trigger_event_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            fanout_id=fanout_id,
            flow_kind=stage_flow_kind,
        ))
    return findings


def stall_invocation_event(finding: StallFinding, events) -> ZfEvent | None:
    """Report a capped stall to the recovery owner, never directly to L2.

    ``dispatch.silent_stall`` is already a registry-owned abnormal event.  It
    becomes Supervisor attention and then a Run Manager diagnosis action on
    the next tick, preserving one mutating/diagnosis owner.
    """

    if any(
        getattr(event, "type", "") == "dispatch.silent_stall"
        and str(_payload(event).get("fingerprint") or "") == finding.fingerprint
        for event in events
    ):
        return None
    return ZfEvent(
        type="dispatch.silent_stall",
        actor="zf-stall-detector",
        correlation_id=finding.trace_id or finding.workflow_run_id or finding.fingerprint,
        causation_id=finding.trigger_event_id or None,
        payload={
            "fingerprint": finding.fingerprint,
            "failure_scope": "structural_stall",
            "workflow_run_id": finding.workflow_run_id,
            "trace_id": finding.trace_id,
            "fanout_id": finding.fanout_id,
            "stage_id": finding.stage_id,
            "trigger": finding.trigger,
            "original_trigger_event_id": finding.trigger_event_id,
            "feature_id": finding.feature_id,
            "severity": "high",
            "summary": (
                f"structural stall: original trigger {finding.trigger} fired but "
                f"stage {finding.stage_id} never started/cancelled/succeeded"
            ),
        },
    )


def stages_from_config(config) -> list[tuple[str, str, str, str]]:
    """Extract stage routing facts from a loaded config."""
    out: list[tuple[str, str, str, str]] = []
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        stage_id = str(getattr(stage, "id", "") or "")
        trigger = str(getattr(stage, "trigger", "") or "")
        aggregate = getattr(stage, "aggregate", None)
        success_event = str(getattr(aggregate, "success_event", "") or "")
        flow_kind = normalize_flow_kind(getattr(stage, "flow_kind", ""))
        if stage_id and trigger:
            out.append((stage_id, trigger, success_event, flow_kind))
    return out


def emit_stall_invocations(
    events,
    writer,
    *,
    stages,
    min_events_after: int = DEFAULT_MIN_EVENTS_AFTER,
) -> int:
    """Detect structural stalls and emit invocation.requested for new ones."""
    emitted = 0
    events = list(events)
    for finding in detect_structural_stalls(
        events, stages=stages, min_events_after=min_events_after,
    ):
        ev = stall_invocation_event(finding, events)
        if ev is not None:
            writer.append(ev)
            emitted += 1
    return emitted


# --- B1 (R20): bounded re-dispatch of a stalled stage trigger ----------------
#
# The structural stall above is detected but the only response was to escalate to
# the self-repair loop (which fixes the *kernel*, not the stuck run). R20 showed
# the run itself can be recovered cheaply: the stage's trigger fired while its
# upstream affinity manifest / target lanes were not yet ready, so the kernel
# skipped dispatch and — being event-driven — never retried. Re-emitting the
# trigger (a NEW event id, which bypasses ``_fanout_started``) makes the stage
# re-attempt; once the transient clears it dispatches. Bounded per fingerprint so
# a *persistent* (real-bug) stall still escalates rather than re-emitting forever.

DEFAULT_REDISPATCH_CAP = 3
_REDISPATCH_FINGERPRINT = "redispatch_fingerprint"


def _redispatch_attempts(fingerprint: str, events) -> int:
    return sum(
        1 for e in events
        if str(_payload(e).get(_REDISPATCH_FINGERPRINT) or "") == fingerprint
    )


def _trigger_has_active_fanout(trigger_event, events) -> bool:
    """B-FIX-06 (R32 双派发): the latest trigger已经起了一个尚未 terminal 的 fanout
    → 重发它就是双派发。redispatch 只该对"trigger 触发但 NO fanout 起"的真停滞
    场景生效。判定 = 存在 fanout.started(trigger_event_id==此 trigger)且其
    fanout_id 未被 cancelled/aggregate.completed/timed_out 终结。"""
    trigger_id = str(getattr(trigger_event, "id", "") or "")
    if not trigger_id:
        return False
    started: set[str] = set()
    terminated: set[str] = set()
    for e in events:
        p = _payload(e)
        etype = getattr(e, "type", "")
        if etype == "fanout.started" and str(p.get("trigger_event_id") or "") == trigger_id:
            fid = str(p.get("fanout_id") or "")
            if fid:
                started.add(fid)
        elif etype in ("fanout.cancelled", "fanout.aggregate.completed", "fanout.timed_out"):
            fid = str(p.get("fanout_id") or "")
            if fid:
                terminated.add(fid)
    return bool(started - terminated)


def stall_redispatch_event(
    finding: StallFinding, events, *, cap: int = DEFAULT_REDISPATCH_CAP,
) -> ZfEvent | None:
    """Re-emit the stalled stage's trigger (bounded by ``cap`` per fingerprint).

    Returns None at the cap so the caller escalates to autoresearch self-repair
    (no-dead-end: retry → retry → … → escalate). Carries the original trigger
    payload forward (the kernel reads ``fanout_id`` / ``candidate_ref`` from it).
    """
    events = list(events)
    if _redispatch_attempts(finding.fingerprint, events) >= cap:
        return None
    latest_trigger = None
    for e in events:
        if (
            getattr(e, "type", "") == finding.trigger
            and _is_original_trigger(e)
            and (
                not finding.trigger_event_id
                or str(getattr(e, "id", "") or "") == finding.trigger_event_id
            )
        ):
            latest_trigger = e
    if latest_trigger is None:
        return None
    # B-FIX-06 (R32 双派发): 抑制重发 —— latest trigger 已起 active fanout 时,
    # 该 stage 不是真停滞(fanout 只是慢),重发会双派发同一组 task(R32:impl
    # fanout 01:03:45 起,stall-redispatch 01:04:09 又重发 → 第 2 个 impl fanout)。
    if _trigger_has_active_fanout(latest_trigger, events):
        return None
    payload = dict(_payload(latest_trigger))
    payload[_REDISPATCH_FINGERPRINT] = finding.fingerprint
    payload["redispatch_attempt"] = _redispatch_attempts(finding.fingerprint, events) + 1
    payload["original_trigger_event_id"] = finding.trigger_event_id or str(
        getattr(latest_trigger, "id", "") or ""
    )
    if finding.flow_kind:
        payload.setdefault("flow_kind", finding.flow_kind)
    return ZfEvent(
        type=finding.trigger,
        actor="zf-stall-redispatch",
        payload=payload,
        correlation_id=getattr(latest_trigger, "correlation_id", "") or finding.fingerprint,
    )


def emit_stall_recoveries(
    events,
    writer,
    *,
    stages,
    redispatch_cap: int = DEFAULT_REDISPATCH_CAP,
    min_events_after: int = DEFAULT_MIN_EVENTS_AFTER,
) -> int:
    """B1 no-dead-end recovery: for each structural stall, RE-DISPATCH the trigger
    first (bounded), escalating to autoresearch self-repair only once the
    re-dispatch cap is reached. Replaces the escalate-only ``emit_stall_invocations``
    in the watcher tick — a transient skip now self-recovers instead of stalling
    until a kernel fix lands.
    """
    events = list(events)
    n = 0
    for finding in detect_structural_stalls(
        events, stages=stages, min_events_after=min_events_after,
    ):
        redispatch = stall_redispatch_event(finding, events, cap=redispatch_cap)
        if redispatch is not None:
            writer.append(redispatch)
            n += 1
        else:
            ev = stall_invocation_event(finding, events)
            if ev is not None:
                writer.append(ev)
                n += 1
    return n
