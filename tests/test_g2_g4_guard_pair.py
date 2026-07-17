"""2026-06-11-0328:G2(owner 面先陈述 events 态)+ G4(成功 supersede 在途补救)。"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.remediation_pipeline import (
    EV_SUPERSEDED,
    remediation_tick,
    superseding_success,
)
from zf.runtime.supervisor_control_loop import (
    build_supervisor_control_loop_events,
)
from zf.core.workflow.reconcile_expected import GraphContract, StageContract


def _ev(etype, *, task=None, corr=None, payload=None, ts="2026-06-11T01:00:00+00:00"):
    return ZfEvent(
        type=etype, actor="t", task_id=task,
        correlation_id=corr, payload=payload or {}, ts=ts,
    )


class TestG4SupersededBySuccess:
    def test_trace_success_closes_open_sm(self):
        events = [
            _ev("judge.failed", task="T1", payload={"task_id": "T1"}),
            _ev("judge.passed", task="T1", ts="2026-06-11T01:31:00+00:00"),
        ]
        transitions = remediation_tick(events)
        kinds = [t.type for t in transitions]
        assert kinds == [EV_SUPERSEDED]
        assert transitions[0].payload["success_type"] == "judge.passed"

    def test_route_to_spawn_toctou_window_closed(self):
        """R24 时序:failure 已 routed(01:34),judge.passed 落盘(01:31 已在
        events),下一 tick 必须 supersede 而不是 consume/dispatch。"""
        corr = "judge.failed:T1"
        events = [
            _ev("judge.failed", task="T1", payload={"task_id": "T1"},
                ts="2026-06-11T01:20:00+00:00"),
            _ev("remediation.classified", corr=corr,
                payload={"failure_class": "design_issue"},
                ts="2026-06-11T01:21:00+00:00"),
            _ev("remediation.routed", corr=corr,
                payload={"failure_class": "design_issue",
                         "tier": "tier2", "action": "dispatch"},
                ts="2026-06-11T01:34:00+00:00"),
            _ev("judge.passed", task="T1", ts="2026-06-11T01:31:00+00:00"),
        ]
        transitions = remediation_tick(events)
        kinds = [t.type for t in transitions]
        assert EV_SUPERSEDED in kinds
        assert "remediation.consumed" not in kinds
        assert "autoresearch.repair.dispatched" not in kinds

    def test_superseded_sm_is_terminal_next_tick(self):
        corr = "judge.failed:T1"
        events = [
            _ev("judge.failed", task="T1", payload={"task_id": "T1"}),
            _ev("judge.passed", task="T1"),
            _ev(EV_SUPERSEDED, corr=corr, payload={}),
        ]
        assert remediation_tick(events) == []

    def test_success_before_failure_does_not_supersede(self):
        events = [
            _ev("judge.passed", task="T1", ts="2026-06-11T00:50:00+00:00"),
            _ev("judge.failed", task="T1", payload={"task_id": "T1"},
                ts="2026-06-11T01:00:00+00:00"),
        ]
        assert superseding_success("judge.failed:T1", events) is None
        kinds = [t.type for t in remediation_tick(events)]
        assert EV_SUPERSEDED not in kinds  # 真失败照常 classify

    def test_no_task_linkage_is_conservative(self):
        events = [
            _ev("worker.stuck.recovery_failed",
                payload={"instance_id": "dev-1"}),
            _ev("judge.passed", task="T9"),
        ]
        kinds = [t.type for t in remediation_tick(events)]
        assert EV_SUPERSEDED not in kinds


class TestG2EventsDerivedState:
    def _snapshot(self, task="T1"):
        return {"attention_items": [{
            "attention_id": "att-1", "severity": "high", "status": "open",
            "title": "stuck?", "summary": "pane shows a permission box",
            "task_id": task, "fingerprint": "fp-1", "source_event_ids": [],
        }]}

    def _messages(self, events, **kw):
        out = build_supervisor_control_loop_events(
            self._snapshot(), events=events,
            projection_ref={"ref": "x"}, **kw,
        )
        return [e for e in out if e.type == "owner.visible_message.requested"]

    def test_message_payload_carries_events_derived_state(self):
        msgs = self._messages([_ev("task.dispatched", task="T1")])
        assert msgs
        derived = msgs[0].payload["events_derived_state"]
        assert derived["source"] == "events.jsonl"
        assert derived["verdict"] == "no_reconcile_contract"
        assert derived["missing_available"] is False

    def test_terminal_seen_wins_over_pane_appearance(self):
        """R24 案的机械形态:pane 看着卡死,events 显示 trace 已完成 ——
        owner 看到的第一行必须是 terminal_seen。"""
        msgs = self._messages([
            _ev("task.dispatched", task="T1"),
            _ev("judge.passed", task="T1"),
        ])
        derived = msgs[0].payload["events_derived_state"]
        assert derived["verdict"] == "terminal_seen"
        assert derived["task_terminal_seen"] == "judge.passed"

    def test_contract_yields_missing_set(self):
        contract = GraphContract(stages=(StageContract(
            stage_id="review", triggers=("candidate.ready",),
            success_events=("review.approved",),
            failure_events=("review.rejected",),
            deadline_s=60.0,
        ),))
        events = [_ev("candidate.ready", corr="TR-1",
                      ts="2026-06-11T01:00:00+00:00")]
        import datetime
        probe = datetime.datetime.fromisoformat(
            "2026-06-11T01:30:00+00:00").timestamp()
        msgs = self._messages(events, contract=contract, now=probe)
        derived = msgs[0].payload["events_derived_state"]
        assert derived["verdict"] == "missing_present"
        assert derived["missing"][0]["stage_id"] == "review"

    def test_schema_blocking_mode_rejects_missing_field(self):
        from zf.core.verification.event_schema import (
            EventSchemaRegistry,
        )
        registry = EventSchemaRegistry.from_dict({
            "owner.visible_message.requested": {
                "required": ["events_derived_state", "message_id"],
            },
        })
        bad = _ev("owner.visible_message.requested",
                  payload={"message_id": "m1"})
        issues = registry.validate(bad)
        assert any(i.code == "missing_required" for i in issues)
        good = _ev("owner.visible_message.requested", payload={
            "message_id": "m1",
            "events_derived_state": {"verdict": "no_missing"},
        })
        assert registry.validate(good) == []

    def test_renderer_leads_with_events_state(self):
        from zf.runtime.owner_visible_delivery import _format_owner_message
        payload = {
            "severity": "high", "title": "t", "summary": "s",
            "events_derived_state": {
                "verdict": "terminal_seen",
                "task_terminal_seen": "judge.passed",
                "missing": [],
            },
        }
        text = _format_owner_message(_ev("owner.visible_message.requested"), payload)
        lines = text.splitlines()
        # backlog 2026-07-07-1315: G2 events-derived truth still leads (line 2,
        # right after the severity header) but is now plain Chinese, not the raw
        # "events-state:" dump.
        assert lines[1].startswith("事件判定:任务已到达终态")
        assert "judge.passed" in lines[1]
        # events truth leads the body: it sits above the actionable footer and
        # there is no raw "severity:" field dump anymore.
        assert not any(l.startswith("severity:") for l in lines)
        # 2026-07-17 L3: info-only messages carry no action footer at all (the
        # old「回复…」keyword prompt was a dead end nothing consumed). The
        # footer only renders for human_action_required — and the events line
        # must still lead it (G2 unchanged).
        assert not any(l.startswith("——") for l in lines)
        act_lines = _format_owner_message(
            _ev("owner.visible_message.requested"),
            {**payload, "human_action_required": True},
        ).splitlines()
        assert act_lines[1].startswith("事件判定:任务已到达终态")
        assert act_lines.index(act_lines[1]) < next(
            i for i, l in enumerate(act_lines) if l.startswith("——")
        )
