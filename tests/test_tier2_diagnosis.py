"""Tier-2 诊断性介入(doc 131 §5 执行体;task 2026-07-06-0930)。

r4 实锚:judge 五审不收敛的三次破局全靠人肉 attach 诊断。kernel 侧:
不收敛升级 → 指纹判重铸 diagnosis.requested;diagnosis.completed 的
route_to_lane 回流 replan feedback,needs_owner 升级 owner;一指纹一诊。
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.diagnosis import (
    diagnosis_event_schema_rules,
    plan_diagnosis_requests,
    plan_needs_owner_escalations,
)


def _ev(etype: str, payload: dict, *, eid: str = "", corr: str = "") -> ZfEvent:
    event = ZfEvent(type=etype, actor="test", payload=payload)
    if eid:
        event.id = eid
    if corr:
        event.correlation_id = corr
    return event


def test_nonconvergence_escalate_mints_request_once() -> None:
    escalate = _ev("human.escalate", {
        "reason": "judge_nonconvergence",
        "stage_id": "final-judge",
        "failure_count": 3,
        "failure_chain": [{"fanout_id": "f1", "reason": "gate failed"}],
    }, eid="esc-1")

    plans = plan_diagnosis_requests([escalate])
    assert len(plans) == 1
    payload = plans[0]
    assert payload["fingerprint"] == "judge-nonconv:final-judge:3"
    assert payload["source_event_id"] == "esc-1"
    assert payload["failure_chain"]
    assert "route_to_lane" in payload["report_contract"]["next_action"]

    # 同指纹已铸 → 不再铸(一指纹一诊)
    minted = _ev("diagnosis.requested", payload)
    assert plan_diagnosis_requests([escalate, minted, escalate]) == []


def test_rework_exhausted_escalate_also_triggers() -> None:
    escalate = _ev("human.escalate", {
        "reason": "candidate rework exhausted after 2 attempts; findings unresolved",
        "checkpoint_id": "crw-abc",
    }, eid="esc-2")

    plans = plan_diagnosis_requests([escalate])
    assert len(plans) == 1
    assert plans[0]["fingerprint"] == "rework-exhausted:crw-abc"


def test_unrelated_escalate_does_not_trigger() -> None:
    escalate = _ev("human.escalate", {
        "reason": "approval needed", "action": "failure-closeout-activate",
    })
    assert plan_diagnosis_requests([escalate]) == []


def test_needs_owner_conclusion_escalates_once() -> None:
    completed = _ev("diagnosis.completed", {
        "fingerprint": "judge-nonconv:final-judge:3",
        "stage_id": "final-judge",
        "report": {
            "root_cause_hypothesis": "judge workdir pinned to baseline",
            "next_action": "needs_owner",
            "attribution_evidence": "workdir HEAD dc60fcd vs evidence 957a02e",
        },
    }, eid="diag-1")

    escalations = plan_needs_owner_escalations([completed])
    assert len(escalations) == 1
    payload = escalations[0]
    assert payload["reason"] == "diagnosis_needs_owner"
    assert payload["diagnosis_event_id"] == "diag-1"
    assert "baseline" in payload["root_cause_hypothesis"]

    # 已升级过 → 不重复
    emitted = _ev("human.escalate", payload)
    assert plan_needs_owner_escalations([completed, emitted]) == []


def test_route_to_lane_diagnosis_backflows_into_rework_feedback() -> None:
    events = [
        _ev("diagnosis.completed", {
            "trace_id": "t1",
            "report": {
                "root_cause_hypothesis": "five-camera timeout is an e2e port issue",
                "next_action": "route_to_lane",
                "target_lane": "react-ui",
                "attribution_evidence": "pure WEB tree passes five-camera",
            },
        }, eid="diag-2", corr="t1"),
        _ev("verify.failed", {
            "target_ref": "cand/PDD-1", "trace_id": "t1",
        }, eid="vf1", corr="t1"),
    ]

    plans = plan_candidate_rework(events, max_attempts=2)
    assert len(plans) == 1
    joined = " ".join(plans[0].feedback)
    assert "diagnosis→react-ui" in joined
    assert "e2e port issue" in joined


def test_diagnosis_schema_contract_rejects_empty_hypothesis() -> None:
    from zf.core.verification.event_schema import EventSchemaRegistry

    registry = EventSchemaRegistry.from_dict(diagnosis_event_schema_rules())
    violations = registry.validate(_ev("diagnosis.completed", {
        "fingerprint": "fp-1",
        "report": {
            "root_cause_hypothesis": "",
            "next_action": "reroute-somewhere",
            "attribution_evidence": "",
        },
    }))
    codes = {(v.field_path, v.code) for v in violations}
    assert ("payload.report.root_cause_hypothesis", "empty_required") in codes
    assert ("payload.report.next_action", "enum_mismatch") in codes


def test_orchestrator_sweep_mints_and_escalates(tmp_path) -> None:
    """端到端:orchestrator sweep 消费升级信号 → requested;消费诊断
    needs_owner 结论 → owner 升级。"""
    from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.orchestrator import Orchestrator

    class _Transport:
        def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
            pass

        def is_alive(self, role_name):  # noqa: ANN001
            return True

        def capture_log(self, role_name, lines=200):  # noqa: ANN001
            return ""

        def poll_events(self):
            return []

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir,
        ZfConfig(
            project=ProjectConfig(name="test"),
            roles=[RoleConfig(name="dev", backend="mock")],
        ),
        _Transport(),  # type: ignore[arg-type]
    )
    escalate = ZfEvent(type="human.escalate", actor="zf-cli", payload={
        "reason": "judge_nonconvergence",
        "stage_id": "final-judge",
        "failure_count": 3,
        "failure_chain": [],
    })
    EventWriter(log).append(escalate)
    orch.run_once(events=[escalate])

    events = log.read_all()
    requested = [e for e in events if e.type == "diagnosis.requested"]
    assert requested, "sweep 必须铸 diagnosis.requested"
    assert requested[0].payload["fingerprint"] == "judge-nonconv:final-judge:3"

    # 幂等:再跑一轮不重复铸
    orch.run_once(events=[])
    assert len([
        e for e in log.read_all() if e.type == "diagnosis.requested"
    ]) == 1

    # 诊断结论 needs_owner → owner 升级
    completed = ZfEvent(type="diagnosis.completed", actor="diagnostician", payload={
        "fingerprint": "judge-nonconv:final-judge:3",
        "stage_id": "final-judge",
        "report": {
            "root_cause_hypothesis": "workdir at baseline",
            "next_action": "needs_owner",
            "attribution_evidence": "HEAD mismatch",
        },
    })
    EventWriter(log).append(completed)
    orch.run_once(events=[completed])
    owner_escalates = [
        e for e in log.read_all()
        if e.type == "human.escalate"
        and e.payload.get("reason") == "diagnosis_needs_owner"
    ]
    assert len(owner_escalates) == 1


def test_diagnostician_stage_config_validates(tmp_path, monkeypatch, capsys) -> None:
    """unit-5:诊断角色+stage 的 zf.yaml 姿势(文档锚)。"""
    from zf.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: tier2-demo\n"
        "roles:\n"
        "  - name: diagnostician\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "    budget_usd: 10.0\n"
        "workflow:\n"
        "  stages:\n"
        "    - id: tier2-diagnosis\n"
        "      trigger: diagnosis.requested\n"
        "      topology: fanout_reader\n"
        "      roles: [diagnostician]\n"
        "      aggregate:\n"
        "        mode: wait_for_all\n"
        "        success_event: diagnosis.completed\n"
        "        failure_event: diagnosis.failed\n"
        "        child_success_event: diagnosis.completed\n"
        "        child_failure_event: diagnosis.failed\n",
        encoding="utf-8",
    )
    rc = main(["validate"])
    assert rc == 0, capsys.readouterr().err
