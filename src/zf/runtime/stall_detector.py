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
from zf.runtime.autoresearch_invocation import build_invocation_request_event

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


def _payload(event) -> dict:
    p = getattr(event, "payload", {}) or {}
    return p if isinstance(p, dict) else {}


def detect_structural_stalls(
    events,
    *,
    stages,
    min_events_after: int = DEFAULT_MIN_EVENTS_AFTER,
) -> list[StallFinding]:
    """A stage whose ``trigger`` fired but which never started, cancelled, or
    succeeded — and which has since been bypassed by ``min_events_after`` kernel
    events — is a structural stall. ``stages`` is an iterable of
    ``(stage_id, trigger, success_event)``.
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
        stage_id, trigger, success_event = stage
        stage_id, trigger = str(stage_id), str(trigger)
        if not stage_id or not trigger:
            continue
        # latest trigger occurrence
        trigger_idx = None
        for idx, event in seq:
            if getattr(event, "type", "") == trigger:
                trigger_idx = idx
        if trigger_idx is None:
            continue
        # did the kernel handle it after the trigger?
        handled = False
        for idx, event in seq:
            if idx <= trigger_idx:
                continue
            etype = getattr(event, "type", "")
            if etype == success_event and success_event:
                handled = True
                break
            if etype in (_FANOUT_START, _FANOUT_CANCEL):
                if str(_payload(event).get("stage_id") or "") == stage_id:
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
            ):
                handled = True
                break
        if handled:
            continue
        if (total - 1 - trigger_idx) < min_events_after:
            continue  # trigger just fired; the kernel hasn't had its turn yet
        findings.append(StallFinding(
            trigger=trigger,
            stage_id=stage_id,
            success_event=str(success_event or ""),
            feature_id=feature_id,
            fingerprint=f"stall:{trigger}->{stage_id}:{feature_id}",
        ))
    return findings


def stall_invocation_event(finding: StallFinding, events) -> ZfEvent | None:
    """Build an autoresearch.invocation.requested for a structural stall, or None
    if already requested (dedup lives in build_invocation_request_event)."""
    item = {
        "summary": (
            f"structural stall: trigger {finding.trigger} fired but stage "
            f"{finding.stage_id} never started/cancelled/succeeded "
            f"(feature {finding.feature_id or '?'}) — kernel should have "
            f"dispatched the stage and silently did not"
        ),
        "severity": "high",
        "fingerprint": finding.fingerprint,
        "attention_id": finding.fingerprint,
        "task_id": "",
    }
    decision = {"route": "supervisor_autoresearch", "decision_id": finding.fingerprint}
    return build_invocation_request_event(
        item, decision=decision, events=list(events), projection_ref={},
    )


def stages_from_config(config) -> list[tuple[str, str, str]]:
    """Extract (stage_id, trigger, success_event) from a loaded config."""
    out: list[tuple[str, str, str]] = []
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        stage_id = str(getattr(stage, "id", "") or "")
        trigger = str(getattr(stage, "trigger", "") or "")
        aggregate = getattr(stage, "aggregate", None)
        success_event = str(getattr(aggregate, "success_event", "") or "")
        if stage_id and trigger:
            out.append((stage_id, trigger, success_event))
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
        if getattr(e, "type", "") == finding.trigger:
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
