"""expected_next — doc 87 reconciler 的纯函数半边(P0 等价证明)。

从 compiled stage 拓扑(desired)与 events 折叠态(current)推导
「期望的下一个终态事件存在吗」。纯函数、零 I/O、零内存状态(I41/I42):
输入只有 GraphContract + 事件序列 + now,可独立单测、可对归档回放。

simulate_replay 是只读回放模拟器:不发任何事件,仅输出
missing → re-arm → quarantine 轨迹,用于 R20-R24 归档等价证明与
R24 阳性对照(健康跑必须零记录,防 reconciler 自己成为下一个误报源)。

命名说明:与 ``workflow_resume.py`` 的 ``expected_next_stage/role``
字段无关——那是 checkpoint 的字段名,本模块是拓扑推导函数,模块路径
隔离两个语境。

doc 87 §3 / doc 89 §2-§3;trace 级总预算为 rev3 G1 增补
(per-stage cap 各自有界仍可被多 source 轮流消耗,总预算封死乘性账本)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


# ---------------------------------------------------------------- contracts


@dataclass(frozen=True)
class StageContract:
    stage_id: str
    triggers: tuple[str, ...]
    success_events: tuple[str, ...]
    failure_events: tuple[str, ...]
    deadline_s: float
    rearm_cap: int = 1

    @property
    def terminals(self) -> tuple[str, ...]:
        return self.success_events + self.failure_events


@dataclass(frozen=True)
class GraphContract:
    stages: tuple[StageContract, ...]
    # rev3 G1: trace 总 re-arm 预算。默认 = Σ per-stage caps(乘性账本的
    # 加性封顶);trace_budget 是唯一显式覆盖键(doc 87 §3 rev4 措辞)。
    trace_budget: int = 0

    def effective_trace_budget(self) -> int:
        if self.trace_budget > 0:
            return self.trace_budget
        return max(1, sum(s.rearm_cap for s in self.stages))


def contract_from_config(config: Any) -> GraphContract:
    """从 ZfConfig.workflow.stages 派生合同(不新增配置面)。

    只为「可推导期望终态」的 stage 生成规则:有 trigger、有
    aggregate 终态对、timeout_seconds > 0。其余 stage 没有可机械
    判定的 expected,跳过(诚实部分覆盖,而非猜测)。
    """
    stages: list[StageContract] = []
    for stage in getattr(config.workflow, "stages", []) or []:
        trigger = str(getattr(stage, "trigger", "") or "")
        timeout = float(getattr(stage, "timeout_seconds", 0) or 0)
        aggregate = getattr(stage, "aggregate", None)
        success = str(getattr(aggregate, "success_event", "") or "")
        failure = str(getattr(aggregate, "failure_event", "") or "")
        cap = int(getattr(aggregate, "max_retries", 1) or 1)
        if not trigger or timeout <= 0 or not (success or failure):
            continue
        stages.append(StageContract(
            stage_id=str(getattr(stage, "id", "") or ""),
            triggers=(trigger,),
            success_events=tuple(e for e in (success,) if e),
            failure_events=tuple(e for e in (failure,) if e),
            deadline_s=timeout,
            rearm_cap=max(1, cap),
        ))
    return GraphContract(stages=tuple(stages))


# ---------------------------------------------------------------- event view


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _ets(event: Any) -> float:
    raw = (
        event.get("ts") if isinstance(event, dict)
        else getattr(event, "ts", "")
    )
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _trace_aliases(event: Any) -> tuple[str, ...]:
    if isinstance(event, dict):
        payload = event.get("payload") or {}
        corr = event.get("correlation_id")
    else:
        payload = getattr(event, "payload", None) or {}
        corr = getattr(event, "correlation_id", None)
    if not isinstance(payload, dict):
        payload = {}
    aliases: list[str] = []
    for value in (
        corr,
        payload.get("trace_id"),
        payload.get("pdd_id"),
        payload.get("feature_id"),
    ):
        alias = str(value or "").strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases or ("default",))


def _trace_id(event: Any) -> str:
    return _trace_aliases(event)[0]


# ---------------------------------------------------------------- fold/state


@dataclass
class _StageState:
    last_trigger_ts: float | None = None
    last_terminal_ts: float | None = None
    rearms: int = 0


@dataclass
class TraceState:
    trace_id: str
    stages: dict[str, _StageState] = field(default_factory=dict)
    total_rearms: int = 0
    quarantined: bool = False


@dataclass(frozen=True)
class Missing:
    trace_id: str
    stage_id: str
    expected: tuple[str, ...]
    since_ts: float
    age_s: float
    deadline_s: float
    rearms: int


def fold_state(
    contract: GraphContract,
    events: Iterable[Any],
) -> dict[str, TraceState]:
    """把事件序列折叠成 per-trace per-stage 状态(纯函数)。"""
    by_trigger: dict[str, list[StageContract]] = {}
    by_terminal: dict[str, list[StageContract]] = {}
    for stage in contract.stages:
        for t in stage.triggers:
            by_trigger.setdefault(t, []).append(stage)
        for t in stage.terminals:
            by_terminal.setdefault(t, []).append(stage)

    traces: dict[str, TraceState] = {}
    alias_to_key: dict[str, str] = {}

    def merge_trace_state(target: TraceState, source: TraceState) -> None:
        for stage_id, source_stage in source.stages.items():
            target_stage = target.stages.setdefault(stage_id, _StageState())
            if source_stage.last_trigger_ts is not None:
                target_stage.last_trigger_ts = (
                    source_stage.last_trigger_ts
                    if target_stage.last_trigger_ts is None
                    else max(target_stage.last_trigger_ts, source_stage.last_trigger_ts)
                )
            if source_stage.last_terminal_ts is not None:
                target_stage.last_terminal_ts = (
                    source_stage.last_terminal_ts
                    if target_stage.last_terminal_ts is None
                    else max(target_stage.last_terminal_ts, source_stage.last_terminal_ts)
                )
            target_stage.rearms = max(target_stage.rearms, source_stage.rearms)
        target.total_rearms = max(target.total_rearms, source.total_rearms)
        target.quarantined = target.quarantined or source.quarantined

    def trace_for_aliases(aliases: tuple[str, ...]) -> tuple[str, TraceState]:
        keys: list[str] = []
        for alias in aliases:
            key = alias_to_key.get(alias)
            if key and key in traces and key not in keys:
                keys.append(key)
        key = keys[0] if keys else aliases[0]
        trace = traces.setdefault(key, TraceState(trace_id=key))
        for other_key in keys[1:]:
            other = traces.pop(other_key, None)
            if other is None:
                continue
            merge_trace_state(trace, other)
            for alias, mapped in list(alias_to_key.items()):
                if mapped == other_key:
                    alias_to_key[alias] = key
        for alias in aliases:
            alias_to_key[alias] = key
        return key, trace

    def open_trace_for_stage(
        stage: StageContract,
        ts: float,
    ) -> tuple[str, TraceState] | None:
        candidates: list[tuple[float, str, TraceState]] = []
        for key, trace in traces.items():
            state = trace.stages.get(stage.stage_id)
            if state is None or state.last_trigger_ts is None:
                continue
            if state.last_trigger_ts > ts:
                continue
            if (
                state.last_terminal_ts is not None
                and state.last_terminal_ts >= state.last_trigger_ts
            ):
                continue
            candidates.append((state.last_trigger_ts, key, trace))
        if len(candidates) != 1:
            return None
        _, key, trace = candidates[0]
        return key, trace

    for event in events:
        etype = _etype(event)
        hit_trigger = by_trigger.get(etype)
        hit_terminal = by_terminal.get(etype)
        if not hit_trigger and not hit_terminal:
            continue
        ts = _ets(event)
        aliases = _trace_aliases(event)
        trace_key, trace = trace_for_aliases(aliases)
        for stage in hit_trigger or []:
            st = trace.stages.setdefault(stage.stage_id, _StageState())
            st.last_trigger_ts = (
                ts if st.last_trigger_ts is None
                else max(st.last_trigger_ts, ts)
            )
        for stage in hit_terminal or []:
            target_key = trace_key
            target_trace = trace
            current = target_trace.stages.get(stage.stage_id)
            if current is None or current.last_trigger_ts is None:
                open_trace = open_trace_for_stage(stage, ts)
                if open_trace is not None:
                    target_key, target_trace = open_trace
                    for alias in aliases:
                        alias_to_key[alias] = target_key
                    if trace_key != target_key and not trace.stages:
                        traces.pop(trace_key, None)
            st = target_trace.stages.setdefault(stage.stage_id, _StageState())
            st.last_terminal_ts = (
                ts if st.last_terminal_ts is None
                else max(st.last_terminal_ts, ts)
            )
    return traces


def approval_hold_keys(events: Iterable[Any]) -> set[tuple[str, str]]:
    """B14-S4 (doc 93 §6): 未决审批 hold 的 (trace_id, stage_id) 集。

    plan.approval.requested 之后无同 plan_id 的 approved/rejected →
    该 (trace, stage) 处于合法 hold,expected_next 必须豁免 —— 否则
    re-arm 会绕过人批重发(自愈"修复"掉审核门)。
    """
    pending: dict[str, tuple[str, str]] = {}
    for event in events:
        etype = _etype(event)
        if etype not in (
            "plan.approval.requested", "plan.approved", "plan.rejected",
        ):
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            payload = event.get("payload") if isinstance(event, dict) else {}
            if not isinstance(payload, dict):
                payload = {}
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            continue
        if etype == "plan.approval.requested":
            pending[plan_id] = (
                _trace_id(event), str(payload.get("stage_id") or ""),
            )
        else:
            pending.pop(plan_id, None)
    return set(pending.values())


def expected_next(
    contract: GraphContract,
    traces: dict[str, TraceState],
    *,
    now: float,
    holds: set[tuple[str, str]] | frozenset = frozenset(),
) -> list[Missing]:
    """「DAG 期望的下一个事件存在吗?」——唯一的"出问题"信号(doc 87 §3.1)。

    stage 被触发且其后没有终态、且超 deadline → Missing。
    failure 终态也是终态(rework 的 re-trigger 会重开 stage,自然语义)。
    """
    out: list[Missing] = []
    for trace in traces.values():
        if trace.quarantined:
            continue
        for stage in contract.stages:
            st = trace.stages.get(stage.stage_id)
            if st is None or st.last_trigger_ts is None:
                continue
            if (trace.trace_id, stage.stage_id) in holds:
                continue  # B14-S4: awaiting approval 是合法 hold,非 missing
            if (
                st.last_terminal_ts is not None
                and st.last_terminal_ts >= st.last_trigger_ts
            ):
                continue  # 已闭合
            age = now - st.last_trigger_ts
            if age <= stage.deadline_s:
                continue
            out.append(Missing(
                trace_id=trace.trace_id,
                stage_id=stage.stage_id,
                expected=stage.terminals,
                since_ts=st.last_trigger_ts,
                age_s=age,
                deadline_s=stage.deadline_s,
                rearms=st.rearms,
            ))
    return out


# ---------------------------------------------------------------- replay sim


@dataclass(frozen=True)
class TrajectoryRecord:
    kind: str  # "missing" | "re_arm" | "quarantine"
    ts: float
    trace_id: str
    stage_id: str
    attempt: int = 0
    reason: str = ""


def simulate_replay(
    contract: GraphContract,
    events: list[Any],
    *,
    tick_s: float = 60.0,
    horizon_s: float = 0.0,
) -> list[TrajectoryRecord]:
    """只读回放:逐 tick 重建状态并模拟 missing → re-arm → quarantine。

    re-arm 是虚拟的(把 stage 的时钟基线推到 re-arm 时刻,模拟
    re_emit_trigger 后的等待),不产生任何事件。健康归档(R24 阳性对照)
    的轨迹必须为空——否则 reconciler 自己就是下一个 R4 误报源。
    """
    if not events:
        return []
    ordered = sorted(events, key=_ets)
    start = _ets(ordered[0])
    end = _ets(ordered[-1]) + max(horizon_s, 0.0)
    traces: dict[str, TraceState] = {}
    stage_by_id = {s.stage_id: s for s in contract.stages}
    budget = contract.effective_trace_budget()

    trajectory: list[TrajectoryRecord] = []
    seen_missing: set[tuple[str, str, float]] = set()
    now = start
    while now <= end + tick_s:
        # 只用 now 之前的事件重建(无内存状态,每 tick 从头折叠——与
        # doc 80 SM 解释器同一物理;O(ticks×events) 对回放可接受)。
        visible = [e for e in ordered if _ets(e) <= now]
        current = fold_state(contract, visible)
        # 把模拟器自己的 re-arm/quarantine 标记搬进重建态
        for tid, trace in current.items():
            prior = traces.get(tid)
            if prior is None:
                continue
            trace.quarantined = prior.quarantined
            trace.total_rearms = prior.total_rearms
            for sid, st in trace.stages.items():
                pst = prior.stages.get(sid)
                if pst is not None:
                    st.rearms = pst.rearms
                    # re-arm 推进时钟基线:等待重试结果而非旧 trigger
                    if pst.last_trigger_ts is not None:
                        st.last_trigger_ts = (
                            pst.last_trigger_ts
                            if st.last_trigger_ts is None
                            else max(st.last_trigger_ts, pst.last_trigger_ts)
                        )
        for miss in expected_next(contract, current, now=now):
            key = (miss.trace_id, miss.stage_id, miss.since_ts)
            if key in seen_missing:
                continue
            seen_missing.add(key)
            trajectory.append(TrajectoryRecord(
                kind="missing", ts=now,
                trace_id=miss.trace_id, stage_id=miss.stage_id,
                attempt=miss.rearms,
            ))
            trace = current[miss.trace_id]
            stage = stage_by_id[miss.stage_id]
            st = trace.stages[miss.stage_id]
            if (
                st.rearms < stage.rearm_cap
                and trace.total_rearms < budget
            ):
                st.rearms += 1
                trace.total_rearms += 1
                st.last_trigger_ts = now  # 虚拟 re_emit_trigger
                trajectory.append(TrajectoryRecord(
                    kind="re_arm", ts=now,
                    trace_id=miss.trace_id, stage_id=miss.stage_id,
                    attempt=st.rearms,
                ))
            else:
                trace.quarantined = True
                trajectory.append(TrajectoryRecord(
                    kind="quarantine", ts=now,
                    trace_id=miss.trace_id, stage_id=miss.stage_id,
                    attempt=st.rearms,
                    reason=(
                        "trace_budget_exhausted"
                        if trace.total_rearms >= budget
                        else "stage_cap_exhausted"
                    ),
                ))
        traces = current
        now += tick_s
    return trajectory
