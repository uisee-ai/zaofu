"""TaskAttempt 账本 — 执行状态的一等派生(131-P2 / E5,shadow-first)。

裁决 A 分层收编:本账本管「执行状态」(谁持有/第几次/终态/该不该计数),
补救「决策」仍归 triage/SM。不重写派发路径——attempt 从既有事件确定性
派生(兼容映射),truth 恒在 events.jsonl。

两条 avbs-r5 实证条款(131 §7.2):

- **计数键 = (task, stage, failure 签名)**,supersede/重放不计数。
  F16:r4/r5 的 rework_of 链每轮重置 attempt 计数,cap 被机制性逃逸
  10 圈;反向地,echo 重放曾把 task.retry_count 刷爆(4/3)。scalar
  retry_count 两头失真,账本按"派发后真实失败"逐条计。
- **superseded fanout 的终态不计数**:r4 实测 SCENE-001 名义 24 次
  attempt,其中大半是 supersede 风暴的重绑,不是真实失败轮次。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import EVENT_PROBLEM_SPECS
from zf.runtime.housekeeping import _REWORK_FAILURE_TYPES
from zf.runtime.terminal_events import (
    is_task_attempt_failure_event,
    is_task_attempt_success_event,
    is_task_attempt_terminal_event,
)

_START_EVENTS = frozenset({"task.dispatched", "fanout.child.dispatched"})

# 不可重试签名(131 §7.2 条款 3):environment/终毒类失败重试是烧钱,
# 直达 deadletter/human。标记来源 = registry problem_class + 症状词表
# (avbs-r4/r5 实案:缺系统库、root 属主、权限)。
_NON_RETRYABLE_REASON_MARKERS = (
    "libnspr4",
    "permission denied",
    "read-only file system",
    "not owned by",
    "root-owned",
    "workdir permission",
    "chromium cannot load",
)


@dataclass
class Attempt:
    ordinal: int
    holder: str
    started_ts: str
    fanout_id: str = ""
    terminal_type: str = ""
    terminal_ts: str = ""
    failure_signature: str = ""
    counted: bool = True  # supersede/重放 → False


@dataclass
class TaskAttemptLedger:
    task_id: str
    attempts: list[Attempt] = field(default_factory=list)

    def counted_failures(self, *, signature: str | None = None) -> int:
        return sum(
            1 for attempt in self.attempts
            if attempt.counted
            and is_task_attempt_failure_event(attempt.terminal_type)
            and (signature is None or attempt.failure_signature == signature)
        )


def _failure_signature(event: ZfEvent) -> str:
    spec = EVENT_PROBLEM_SPECS.get(event.type)
    base = spec.failure_class if spec else event.type.replace(".", "_")
    return base


def failure_fingerprint(event: ZfEvent) -> str:
    """Return the stable semantic fingerprint for one task failure.

    Producers may provide an explicit fingerprint.  Older events do not, so
    the compatibility path hashes only stable failure facts and deliberately
    excludes timestamps, attempt ids, refs, and other replay-varying fields.
    """

    payload = event.payload if isinstance(event.payload, dict) else {}
    explicit = str(
        payload.get("failure_fingerprint")
        or payload.get("fingerprint")
        or ""
    ).strip()
    if explicit:
        return explicit
    details = _stable_failure_details(
        payload.get("findings")
        or payload.get("failed_d")
        or payload.get("errors")
        or []
    )
    stable = {
        "event_type": event.type,
        "stage": str(payload.get("stage_id") or payload.get("stage") or ""),
        "reason": str(
            payload.get("reason")
            or payload.get("summary")
            or payload.get("error")
            or ""
        ).strip().lower(),
        "details": details,
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return "task-failure-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _stable_failure_details(value: Any) -> Any:
    if isinstance(value, dict):
        semantic_keys = {
            "actual",
            "category",
            "code",
            "expected",
            "failure_class",
            "message",
            "reason",
            "severity",
            "summary",
            "type",
        }
        return {
            str(key): _stable_failure_details(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) in semantic_keys
        }
    if isinstance(value, (list, tuple, set)):
        normalized = [_stable_failure_details(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return value


def counted_failure_events(
    events: list[ZfEvent],
    task_id: str,
    *,
    fingerprint: str | None = None,
) -> list[ZfEvent]:
    """Return valid rework failures after replay/supersede filtering."""

    superseded_fanouts: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "fanout.cancelled" and "supersede" in str(
            payload.get("reason") or ""
        ):
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
        elif event.type == "fanout.child.stale_completion":
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)

    out: list[ZfEvent] = []
    seen: set[tuple[str, str, str]] = set()
    seen_event_ids: set[str] = set()
    for event in events:
        if event.type not in _REWORK_FAILURE_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.id and event.id in seen_event_ids:
            continue
        if event.id:
            seen_event_ids.add(event.id)
        if bool(payload.get("replay") or payload.get("stale")):
            continue
        if str(payload.get("superseded_by") or "").strip():
            continue
        event_task = str(event.task_id or payload.get("task_id") or "")
        aggregate_tasks = payload.get("failed_task_ids")
        aggregate_task_ids = (
            {str(item) for item in aggregate_tasks if str(item or "").strip()}
            if isinstance(aggregate_tasks, list)
            else set()
        )
        if event_task:
            targets_task = event_task == task_id
        else:
            # Aggregate verification may reject several task slices in one
            # event. The per-task recovery ledger must still count that
            # failure for every explicitly named failed task.
            targets_task = task_id in aggregate_task_ids
        if not targets_task:
            continue
        current_fingerprint = failure_fingerprint(event)
        if fingerprint is not None and current_fingerprint != fingerprint:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        if fanout_id and fanout_id in superseded_fanouts:
            continue
        replay_key = (fanout_id, event.type, current_fingerprint)
        if fanout_id and replay_key in seen:
            continue
        if fanout_id:
            seen.add(replay_key)
        out.append(event)
    return out


def non_retryable_reason(event: ZfEvent) -> str | None:
    """environment/终毒类失败的判定;命中返回原因说明,未命中 None。"""
    spec = EVENT_PROBLEM_SPECS.get(event.type)
    if spec is not None and spec.problem_class == "environment":
        return f"registry problem_class=environment ({event.type})"
    payload = event.payload if isinstance(event.payload, dict) else {}
    blob = (str(payload.get("reason") or "") + " " + str(payload.get("summary") or "")).lower()
    for marker in _NON_RETRYABLE_REASON_MARKERS:
        if marker in blob:
            return f"environment symptom {marker!r} in failure reason"
    return None


def derive_task_ledger(events: list[ZfEvent], task_id: str) -> TaskAttemptLedger:
    """从事件流确定性派生某任务的 attempt 账本(兼容映射)。"""
    ledger = TaskAttemptLedger(task_id=task_id)
    superseded_fanouts: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in ("fanout.cancelled",) and "supersede" in str(
            payload.get("reason") or ""
        ):
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
        elif event.type == "fanout.child.stale_completion":
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)

    seen_failure_keys: set[tuple[str, str]] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_task = str(
            event.task_id or payload.get("task_id") or payload.get("upstream_task_id") or ""
        )
        if event_task != task_id:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        if event.type in _START_EVENTS:
            ledger.attempts.append(Attempt(
                ordinal=len(ledger.attempts) + 1,
                holder=str(
                    payload.get("role")
                    or payload.get("role_instance")
                    or payload.get("assigned_to")
                    or ""
                ),
                started_ts=event.ts,
                fanout_id=fanout_id,
            ))
        elif is_task_attempt_terminal_event(event.type):
            open_attempts = [a for a in ledger.attempts if not a.terminal_type]
            if not open_attempts:
                continue
            attempt = open_attempts[-1]
            attempt.terminal_type = event.type
            attempt.terminal_ts = event.ts
            if is_task_attempt_failure_event(event.type):
                attempt.failure_signature = _failure_signature(event)
                # F16/F12: 同 fanout 重放不重复计数;superseded fanout 不计数
                failure_key = (fanout_id or attempt.fanout_id, event.type)
                replay = failure_key in seen_failure_keys and bool(failure_key[0])
                superseded = (
                    (fanout_id or attempt.fanout_id) in superseded_fanouts
                )
                attempt.counted = not (replay or superseded)
                if failure_key[0]:
                    seen_failure_keys.add(failure_key)
            elif is_task_attempt_success_event(event.type):
                attempt.counted = (
                    (fanout_id or attempt.fanout_id) not in superseded_fanouts
                )
    return ledger


def counted_rework_rounds(events: list[ZfEvent], task_id: str) -> int:
    """cap 口径的真实 rework 轮次(与 apply_rework_failure_event 同族事件,
    F12 同 fanout 去重 + supersede 排除)。F16 修复面:rework_of 链每轮
    新事件照常累积,scalar 归零也骗不过这里。"""
    superseded: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "fanout.cancelled" and "supersede" in str(
            payload.get("reason") or ""
        ):
            fid = str(payload.get("fanout_id") or "")
            if fid:
                superseded.add(fid)
    rounds = 0
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.type not in _REWORK_FAILURE_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_task = str(event.task_id or payload.get("task_id") or "")
        if event_task != task_id:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        if fanout_id and fanout_id in superseded:
            continue
        key = (fanout_id, event.type)
        if fanout_id and key in seen:
            continue  # echo 重放
        seen.add(key)
        rounds += 1
    return rounds


def ledger_summary(ledger: TaskAttemptLedger) -> dict[str, Any]:
    return {
        "task_id": ledger.task_id,
        "attempts_total": len(ledger.attempts),
        "counted_failures": ledger.counted_failures(),
        "uncounted": sum(
            1 for a in ledger.attempts
            if a.terminal_type and not a.counted
        ),
        "open": sum(1 for a in ledger.attempts if not a.terminal_type),
        "last_holder": ledger.attempts[-1].holder if ledger.attempts else "",
    }
