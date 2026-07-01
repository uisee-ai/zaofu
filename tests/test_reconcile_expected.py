"""doc 87 P0 等价证明:expected_next 纯函数 + 回放模拟器。

合成 fixture 复刻 R22(livelock→有终轨迹)/ R23(synth 终态缺失被点名)/
R24(健康全链零误报)的形状;真实归档集成测试 skip-if-missing。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.workflow.reconcile_expected import (
    GraphContract,
    StageContract,
    contract_from_config,
    expected_next,
    fold_state,
    simulate_replay,
)

_REPO = Path(__file__).resolve().parent.parent


def _ev(etype: str, ts: float, trace: str = "T1") -> dict:
    return {"type": etype, "ts": ts, "correlation_id": trace, "payload": {}}


def _contract(deadline: float = 100.0, cap: int = 1, budget: int = 0) -> GraphContract:
    return GraphContract(
        stages=(
            StageContract(
                stage_id="impl",
                triggers=("task_map.ready",),
                success_events=("candidate.ready",),
                failure_events=("integration.failed",),
                deadline_s=deadline,
                rearm_cap=cap,
            ),
            StageContract(
                stage_id="review",
                triggers=("candidate.ready",),
                success_events=("review.approved",),
                failure_events=("review.rejected",),
                deadline_s=deadline,
                rearm_cap=cap,
            ),
        ),
        trace_budget=budget,
    )


class TestExpectedNextPure:
    def test_closed_stage_has_no_missing(self):
        c = _contract()
        traces = fold_state(c, [
            _ev("task_map.ready", 0.0),
            _ev("candidate.ready", 50.0),
        ])
        assert expected_next(c, traces, now=1000.0) == [] or all(
            m.stage_id != "impl" for m in expected_next(c, traces, now=1000.0)
        )

    def test_missing_flagged_past_deadline(self):
        # R23 形状:stage 被触发,终态 6 小时不来 —— deadline+1 即点名,
        # 而不是 6 小时静默。
        c = _contract(deadline=100.0)
        traces = fold_state(c, [_ev("candidate.ready", 0.0)])
        missing = expected_next(c, traces, now=101.0)
        assert [m.stage_id for m in missing] == ["review"]
        assert missing[0].expected == ("review.approved", "review.rejected")

    def test_within_deadline_stays_silent(self):
        c = _contract(deadline=100.0)
        traces = fold_state(c, [_ev("candidate.ready", 0.0)])
        assert expected_next(c, traces, now=99.0) == []

    def test_failure_terminal_also_closes_stage(self):
        # rework 是 failure 终态 + 新 trigger,不是 missing。
        c = _contract(deadline=100.0)
        traces = fold_state(c, [
            _ev("candidate.ready", 0.0),
            _ev("review.rejected", 10.0),
        ])
        assert expected_next(c, traces, now=1000.0) == []

    def test_terminal_alias_closes_single_open_trigger(self):
        # R25 形状:trigger 只带 pdd_id,terminal 只带 trace_id。若当前
        # stage 只有一个 open trigger,terminal 应归并闭合,而不是制造
        # PDD/TRACE 两条影子轨迹导致 false missing。
        c = GraphContract(stages=(StageContract(
            stage_id="scan",
            triggers=("scan.requested",),
            success_events=("scan.done",),
            failure_events=("scan.failed",),
            deadline_s=100.0,
            rearm_cap=1,
        ),))
        traces = fold_state(c, [
            {
                "type": "scan.requested",
                "ts": 0.0,
                "payload": {"pdd_id": "PDD-1"},
            },
            {
                "type": "scan.done",
                "ts": 10.0,
                "payload": {"trace_id": "TRACE-1"},
            },
        ])

        assert list(traces) == ["PDD-1"]
        assert expected_next(c, traces, now=1000.0) == []

    def test_retrigger_reopens_stage(self):
        c = _contract(deadline=100.0)
        traces = fold_state(c, [
            _ev("candidate.ready", 0.0),
            _ev("review.rejected", 10.0),
            _ev("candidate.ready", 20.0),  # rework 重入
        ])
        missing = expected_next(c, traces, now=200.0)
        assert [m.stage_id for m in missing] == ["review"]

    def test_no_io_and_no_pipeline_literals(self):
        # doc 87 §5 通用性:零管线专用 if。
        src = (
            _REPO / "src/zf/core/workflow/reconcile_expected.py"
        ).read_text()
        for banned in ("cj-min", "hermes", "safe-team", "open(", "Path("):
            assert banned not in src, f"reconcile_expected must not contain {banned!r}"


class TestSimulateReplay:
    def test_rearm_then_terminal_converges(self):
        c = _contract(deadline=100.0, cap=2)
        events = [
            _ev("candidate.ready", 0.0),
            _ev("review.approved", 400.0),  # 终态在一次 re-arm 之后到达
        ]
        traj = simulate_replay(c, events, tick_s=50.0)
        kinds = [r.kind for r in traj]
        assert "re_arm" in kinds
        assert "quarantine" not in kinds

    def test_cap_exhaustion_quarantines_and_terminates(self):
        # R22 形状:livelock 必须归约为有终轨迹。
        c = _contract(deadline=100.0, cap=1)
        events = [_ev("candidate.ready", 0.0)]
        traj = simulate_replay(c, events, tick_s=50.0, horizon_s=1000.0)
        kinds = [r.kind for r in traj]
        assert kinds.count("re_arm") == 1
        assert kinds.count("quarantine") == 1
        q_ts = next(r.ts for r in traj if r.kind == "quarantine")
        assert not [
            r for r in traj if r.ts > q_ts
        ], "quarantine 后轨迹必须终止(终态)"

    def test_trace_budget_caps_multiplicative_sources(self):
        # rev3 G1:两个 stage 各自 cap=2,trace_budget=2 封死轮流消耗。
        c = _contract(deadline=100.0, cap=2, budget=2)
        events = [
            _ev("task_map.ready", 0.0),
            _ev("candidate.ready", 1.0),
        ]
        traj = simulate_replay(c, events, tick_s=50.0, horizon_s=2000.0)
        assert [r.kind for r in traj].count("re_arm") <= 2
        assert "quarantine" in [r.kind for r in traj]
        q = next(r for r in traj if r.kind == "quarantine")
        assert q.reason == "trace_budget_exhausted"

    def test_healthy_run_with_legit_reworks_is_silent(self):
        # R24 阳性对照(合成形状):全链 + 两次 failure 终态驱动的 rework,
        # 终态全部按时到达 → reconciler 全程零记录。
        c = _contract(deadline=100.0, cap=2)
        events = [
            _ev("task_map.ready", 0.0),
            _ev("candidate.ready", 50.0),
            _ev("review.rejected", 90.0),     # 合法 rework 1(终态,非 missing)
            _ev("candidate.ready", 120.0),
            _ev("review.rejected", 160.0),    # 合法 rework 2
            _ev("candidate.ready", 200.0),
            _ev("review.approved", 240.0),
        ]
        traj = simulate_replay(c, events, tick_s=25.0)
        assert traj == [], (
            "健康跑必须零 missing/re_arm/quarantine —— 否则 reconciler "
            f"自己就是下一个 R4 误报源: {traj}"
        )


_HERMES_YAML = Path("/path/to/hermes-agent/zf.yaml")
_R23_ARCHIVE = Path(
    "/path/to/hermes-agent/"
    ".zf-cj-min-refactor/events/2026-06-10.jsonl"
)
_R24_ARCHIVE = _REPO / "docs/records/runs/2026-06-11-r24-cj-min-events.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    import json

    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "event" in obj and isinstance(obj["event"], dict):
            obj = obj["event"]
        if isinstance(obj, dict):
            out.append(obj)
    return out


@pytest.mark.skipif(
    not (_HERMES_YAML.exists() and _R23_ARCHIVE.exists()),
    reason="R23 归档/hermes 配置不在本机",
)
def test_r23_archive_replay_flags_synth_terminal_missing():
    """R23 实案:candidate-review 触发后终态 6 小时不来。expected_next
    必须在 deadline 后点名 missing(而非 14 个机制集体沉默)。"""
    from zf.core.config.loader import load_config

    contract = contract_from_config(load_config(_HERMES_YAML))
    assert contract.stages, "hermes 配置应派生出非空合同"
    events = _load_jsonl(_R23_ARCHIVE)
    traces = fold_state(contract, events)
    review_stage = next(
        (s for s in contract.stages if "review" in s.stage_id), None,
    )
    if review_stage is None:
        pytest.skip("hermes 合同无 review stage")
    triggered = [
        t for t in traces.values()
        if (st := t.stages.get(review_stage.stage_id)) and st.last_trigger_ts > 0
    ]
    if not triggered:
        pytest.skip("R23 归档窗口内无 review 触发(轮次切片不同)")
    probe = max(
        st.last_trigger_ts
        for t in triggered
        for sid, st in t.stages.items()
        if sid == review_stage.stage_id
    )
    missing = expected_next(
        contract, traces, now=probe + review_stage.deadline_s + 60.0,
    )
    assert any(m.stage_id == review_stage.stage_id for m in missing), (
        "R23 形状:review 终态缺失必须被点名"
    )


@pytest.mark.skipif(
    not (_HERMES_YAML.exists() and _R24_ARCHIVE.exists()),
    reason="R24 归档未落盘(backlog 2026-06-11-0321 blocked:待运行节点回传)",
)
def test_r24_archive_positive_control_zero_false_alarms():
    """R24 阳性对照:健康全链(含两次合法 rework)回放,除真实 rework 的
    failure 终态外,轨迹必须为空。"""
    from zf.core.config.loader import load_config

    contract = contract_from_config(load_config(_HERMES_YAML))
    events = _load_jsonl(_R24_ARCHIVE)
    traj = simulate_replay(contract, events, tick_s=60.0)
    assert traj == [], f"R24 健康归档出现误报: {traj[:5]}"


class TestW3P0Shadow:
    """W3-P0:reconcile 影子在 run_once 尾部发射(零执行)。"""

    def test_shadow_wiring_present_and_throttled(self):
        from pathlib import Path
        src = Path("src/zf/runtime/orchestrator.py").read_text(encoding="utf-8")
        assert "reconcile.decision.shadow" in src
        assert "_emit_reconcile_shadow" in src
        assert "shadow_only" in src
        assert "30.0" in src  # 每 wake 频控

    def test_shadow_payload_from_synthetic_missing(self):
        # 纯函数链:contract→fold→expected_next 的 missing 形状即影子 payload 形状
        from zf.core.workflow.reconcile_expected import (
            GraphContract,
            StageContract,
            expected_next,
            fold_state,
        )
        from zf.core.events.model import ZfEvent
        contract = GraphContract(stages=[StageContract(
            stage_id="impl", triggers=("go",),
            success_events=("done",), failure_events=("fail",),
            deadline_s=10.0, rearm_cap=2,
        )], trace_budget=0)
        events = [ZfEvent(type="go", correlation_id="t1",
                          payload={}, ts="2026-06-11T00:00:00+00:00")]
        traces = fold_state(contract, events)
        missing = expected_next(
            contract, traces,
            now=__import__("datetime").datetime.fromisoformat(
                "2026-06-11T00:00:00+00:00").timestamp() + 60,
        )
        assert len(missing) == 1 and missing[0].stage_id == "impl"
