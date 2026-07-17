"""PRD e2e 残余八项(E1-E8)定向测试,场景全部来自实弹。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.events.model import ZfEvent


# ---------- E1:invoke 自举任务 ----------

def test_e1_submit_chain_invoke_bootstraps_task() -> None:
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    added = []

    class _Store:
        def get(self, task_id):
            return None

        def add(self, task):
            added.append(task)
            return task

    class _Writer:
        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)
            return event

    host = SimpleNamespace(task_store=_Store(), event_writer=_Writer())
    event = ZfEvent(type="workflow.invoke.requested", actor="zf-cli",
                    payload={"task_id": "PRD-WFINT-1",
                             "workflow_input_manifest_ref": "artifacts/m.json",
                             "objective": "交付 X", "request_id": "wfint-1"})
    task = EventReactorMixin._bootstrap_invoke_task(
        host, event, event.payload, "PRD-WFINT-1",
    )
    assert task is not None and task.id == "PRD-WFINT-1"
    assert added and host.event_writer.events[0].type == "task.created"
    assert host.event_writer.events[0].payload["source"] == "workflow_invoke_bootstrap"


def test_e1_non_submit_invoke_still_rejected() -> None:
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    host = SimpleNamespace(task_store=None, event_writer=None)
    event = ZfEvent(type="workflow.invoke.requested", actor="zf-cli",
                    payload={"task_id": "T-1"})  # 无 manifest 凭证
    assert EventReactorMixin._bootstrap_invoke_task(
        host, event, event.payload, "T-1",
    ) is None


# ---------- E4:stage replan cap 认后续成功 ----------

def _stage_cfg():
    stage = SimpleNamespace(
        id="prd-plan", trigger="prd.scan.completed", topology="fanout_reader",
        aggregate=SimpleNamespace(
            success_event="task_map.ready", failure_event="prd.plan.failed",
        ),
    )
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def test_e4_cap_resets_after_stage_success() -> None:
    from zf.runtime.stage_failure_replan import plan_reader_stage_replan

    fail1 = ZfEvent(type="prd.plan.failed", actor="zf-cli", payload={})
    fail2 = ZfEvent(type="prd.plan.failed", actor="zf-cli", payload={})
    success = ZfEvent(type="task_map.ready", actor="zf-cli", payload={})
    fail3 = ZfEvent(type="prd.plan.failed", actor="zf-cli", payload={})
    # 无成功事件:两次历史失败 → cap 拒
    capped, note = plan_reader_stage_replan(
        _stage_cfg(), [fail1, fail2, fail3], fail3,
    )
    assert capped is None and note == "cap_exhausted"
    # 成功翻篇:相同历史 + 成功 → 新失败重新计数,允许 replan
    ok, note = plan_reader_stage_replan(
        _stage_cfg(), [fail1, fail2, success, fail3], fail3,
    )
    assert ok is not None
    assert ok.payload["rework_attempt"] == 1


# ---------- E8:quiescent 不压待派发 ----------

def test_e8_inflight_fanout_blocks_quiescence() -> None:
    from datetime import datetime, timedelta, timezone

    from zf.runtime.quiescent import quiescent_now

    now = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    cfg = SimpleNamespace(goal=SimpleNamespace(
        enabled=True, quiescent_after_escalate=True,
    ))

    def ev(etype, minutes_ago, **payload):
        return ZfEvent(type=etype, actor="zf-cli",
                       ts=(now - timedelta(minutes=minutes_ago)).isoformat(),
                       payload=payload)

    events = [
        ev("fanout.started", 40, fanout_id="f-1"),
        ev("human.escalate", 30),
    ]
    s = quiescent_now(events, config=cfg, now_epoch=now.timestamp())
    assert s.quiescent is False and s.reason == "inflight_fanouts"
    # fanout 终局后恢复可静默
    events.append(ev("fanout.timed_out", 20, fanout_id="f-1"))
    s2 = quiescent_now(events, config=cfg, now_epoch=now.timestamp())
    assert s2.quiescent is True


# ---------- E6:超时地板 ----------

def test_e6_timeout_floor_for_unconfigured_stage() -> None:
    from zf.runtime.orchestrator_fanout import (
        DEFAULT_FANOUT_TIMEOUT_S,
        FanoutCoordinationMixin,
    )

    host = SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace(
        stages=[SimpleNamespace(id="prd-lanes-verify", timeout_seconds=0)],
    )))
    got = FanoutCoordinationMixin._fanout_timeout_seconds(host, "prd-lanes-verify")
    assert got == DEFAULT_FANOUT_TIMEOUT_S
    unknown = FanoutCoordinationMixin._fanout_timeout_seconds(host, "nope")
    assert unknown == DEFAULT_FANOUT_TIMEOUT_S
    host2 = SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace(
        stages=[SimpleNamespace(id="s", timeout_seconds=900)],
    )))
    assert FanoutCoordinationMixin._fanout_timeout_seconds(host2, "s") == 900


# ---------- E2:objective 传导 ----------

def test_e2_submit_payload_carries_manifest_objective(tmp_path: Path, monkeypatch) -> None:
    import json

    from zf.cli import flow as flow_cli

    # 最小 manifest + intake
    manifest = {"request_id": "wfint-x", "kind": "prd",
                "objective": "交付 textstat CLI", "source_ref": "docs/p.md",
                "source_root": "docs", "target_root": "app",
                "missing_required_fields": []}
    intake = tmp_path / "wfint-x.md"
    intake.write_text("# intake", encoding="utf-8")
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        "\n".join([
            "apiVersion: zaofu.dev/v1",
            "kind: ZfConfig",
            "metadata: {name: objective-e2e}",
            "spec:",
            '  version: "1.0"',
            "  project: {name: objective-e2e, state_dir: .zf-objective}",
            "",
        ]),
        encoding="utf-8",
    )
    mpath = tmp_path / "workflow-input-manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(flow_cli, "_load_manifest_for_intake",
                        lambda p: (mpath, manifest))
    monkeypatch.setattr(flow_cli, "build_flow_preflight_report",
                        lambda *a, **kw: {"status": "PASS", "blockers": [],
                                          "flow_kind": "prd"})
    preview = flow_cli.build_flow_submit_preview(
        config_path=config_path, intake_path=intake,
        flow_kind="prd", task_id="T-1", pattern_id="prd-scan",
        requested_by="op", reason="e2e", output=None,
        allow_missing_env=True,
    )
    assert preview["payload"]["objective"] == "交付 textstat CLI"
