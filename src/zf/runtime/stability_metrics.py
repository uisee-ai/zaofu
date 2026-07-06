"""「跑稳」可测判据 S1-S5(审计 SYNTHESIS §6,P1-12)。

同 profile 连续 2 轮 run 同时满足则判「稳」;本模块把五条判据机械化,
消费 events.jsonl(或归档副本),供 `zf metrics stability` 与回归 fixture
(R12=fail / R14=pass)双向使用。

- S1 无新 failure 类:本轮 failure_class 集合 ⊆ 基线(经 registry 归类);
- S2 干预阈值:operator 干预事件 ≤ 2 / 10h(human.resolved、
  verification.waived、operator_authorized 载荷、human steer);
- S3 no-dead-end:每条 human.escalate 有 ack(human.resolved /
  remediation.escalated_acked)或降级 runtime.safe_halted;
- S4 stall 恢复:worker.stuck → 恢复(recovered/respawn/终态)p95;
  另 S4b(avbs-r5 F15 增补):误报 stall(pane 活着被判死)不可量测于
  事件流,以 stuck→recovered 极短间隔占比作代理观察项,不判 pass/fail;
- S5 双盲区为零:cost.usage.blackout = 0 且 env.preflight.failed = 0。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import EVENT_PROBLEM_SPECS

_ACK_TYPES = frozenset({"human.resolved", "remediation.escalated_acked"})
_INTERVENTION_TYPES = frozenset({
    "human.resolved",
    "verification.waived",
    "verification.waiver.revoked",
    "task.contract.update",
})
_STALL_RECOVERY_TYPES = frozenset({
    "worker.stuck.recovered",
    "worker.respawned",
    "dev.build.done",
})


@dataclass
class StabilityReport:
    s1_new_failure_classes: list[str] = field(default_factory=list)
    s1_pass: bool | None = None  # None = 无基线,不判
    s2_interventions: int = 0
    s2_window_hours: float = 0.0
    s2_pass: bool = True
    s3_unacked_escalates: int = 0
    s3_total_escalates: int = 0
    s3_pass: bool = True
    s4_recovery_p95_s: float | None = None
    s4_samples: int = 0
    s5_blackouts: int = 0
    s5_env_failures: int = 0
    s5_pass: bool = True

    @property
    def passes(self) -> bool:
        checks = [self.s2_pass, self.s3_pass, self.s5_pass]
        if self.s1_pass is not None:
            checks.append(self.s1_pass)
        return all(checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "s1": {"new_failure_classes": self.s1_new_failure_classes, "pass": self.s1_pass},
            "s2": {"interventions": self.s2_interventions,
                   "window_hours": round(self.s2_window_hours, 2), "pass": self.s2_pass},
            "s3": {"unacked": self.s3_unacked_escalates,
                   "total": self.s3_total_escalates, "pass": self.s3_pass},
            "s4": {"recovery_p95_s": self.s4_recovery_p95_s, "samples": self.s4_samples},
            "s5": {"blackouts": self.s5_blackouts,
                   "env_failures": self.s5_env_failures, "pass": self.s5_pass},
            "stable": self.passes,
        }


def _epoch(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def _failure_classes(events: list[ZfEvent]) -> set[str]:
    classes: set[str] = set()
    for event in events:
        spec = EVENT_PROBLEM_SPECS.get(event.type)
        if spec is not None and spec.event_class in ("abnormal", "expected_negative"):
            classes.add(spec.failure_class or event.type)
    return classes


def _is_intervention(event: ZfEvent) -> bool:
    if event.type in _INTERVENTION_TYPES:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    return bool(payload.get("operator_authorized")) or str(
        payload.get("source") or ""
    ).startswith("operator")


def evaluate_stability(
    events: list[ZfEvent],
    *,
    baseline_events: list[ZfEvent] | None = None,
    interventions_per_10h: int = 2,
) -> StabilityReport:
    report = StabilityReport()
    if not events:
        return report

    # 时间窗
    epochs = [e for e in (_epoch(ev.ts) for ev in events) if e is not None]
    window_s = (max(epochs) - min(epochs)) if len(epochs) >= 2 else 0.0
    report.s2_window_hours = window_s / 3600.0

    # S1
    if baseline_events is not None:
        baseline = _failure_classes(baseline_events)
        current = _failure_classes(events)
        report.s1_new_failure_classes = sorted(current - baseline)
        report.s1_pass = not report.s1_new_failure_classes

    # S2
    report.s2_interventions = sum(1 for ev in events if _is_intervention(ev))
    budget = max(1.0, report.s2_window_hours / 10.0) * interventions_per_10h
    report.s2_pass = report.s2_interventions <= budget

    # S3
    ack_or_halt_after: list[float] = [
        e for e in (
            _epoch(ev.ts) for ev in events
            if ev.type in _ACK_TYPES or ev.type == "runtime.safe_halted"
        ) if e is not None
    ]
    for event in events:
        if event.type != "human.escalate":
            continue
        report.s3_total_escalates += 1
        esc_epoch = _epoch(event.ts)
        if esc_epoch is None:
            continue
        if not any(t >= esc_epoch for t in ack_or_halt_after):
            report.s3_unacked_escalates += 1
    report.s3_pass = report.s3_unacked_escalates == 0

    # S4
    recovery_gaps: list[float] = []
    pending_stuck: dict[str, float] = {}
    for event in events:
        actor_key = str(event.actor or (event.payload or {}).get("role") or "")
        e = _epoch(event.ts)
        if e is None:
            continue
        if event.type == "worker.stuck":
            pending_stuck[actor_key] = e
        elif event.type in _STALL_RECOVERY_TYPES and actor_key in pending_stuck:
            recovery_gaps.append(e - pending_stuck.pop(actor_key))
    if recovery_gaps:
        recovery_gaps.sort()
        idx = min(len(recovery_gaps) - 1, int(len(recovery_gaps) * 0.95))
        report.s4_recovery_p95_s = round(recovery_gaps[idx], 1)
        report.s4_samples = len(recovery_gaps)

    # S5
    report.s5_blackouts = sum(1 for ev in events if ev.type == "cost.usage.blackout")
    report.s5_env_failures = sum(
        1 for ev in events if ev.type == "env.preflight.failed"
    )
    report.s5_pass = report.s5_blackouts == 0 and report.s5_env_failures == 0
    return report
