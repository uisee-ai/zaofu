"""L1 handoff simulation (2026-06-19 handoff-prevention-framework).

Drives the REAL orchestrator aggregate with synthetic worker-completion events
(no tmux, no LLM) and asserts the stage→stage handoff event fires. Unlike
tests/e2e/scripted_runner.py (which hand-emits the handoff events), this
exercises the kernel's reaction logic — the layer where every prod-E2E P0
lived — so a regression that breaks a handoff is caught in milliseconds
instead of a 25-minute real-LLM run.

Flagship case: P0-3. The prd-review aggregate must satisfy the
``prd.approved`` required-schema (prd_ref / artifact_refs / evidence_refs) by
inheriting them from the child's trigger_payload when the reader role does not
re-emit them. Before the fix this aggregated to prd.blocked.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.orchestrator import Orchestrator


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _prd_review_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="prd-critic", instance_id="prd-critic",
                          backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-review",
                trigger="prd.ready",
                topology="fanout_reader",
                roles=["prd-critic"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="prd.approved",
                    failure_event="prd.blocked",
                ),
            ),
        ]),
    )


def _drive_prd_review(tmp_path: Path, *, child_payload: dict) -> list[ZfEvent]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _prd_review_config(), _RecordingTransport())

    # 1) drive the real start: the orchestrator writes the fanout manifest
    #    (aggregate success/failure events from the stage config) and persists
    #    the trigger payload — including the contract fields — onto each
    #    expected child, exactly as production does.
    orch.run_once(events=[ZfEvent(
        type="prd.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-1",
            "prd_ref": "docs/plans/training-mode-prd.md",
            "artifact_refs": ["docs/plans/training-mode-prd.md"],
            "evidence_refs": ["fanout:trigger_payload.text"],
        },
    )])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    child_id = started.payload["expected_children"][0]["child_id"]

    # 2) the reader child completes by emitting the configured child success
    #    event (workflow.child.completed) WITHOUT re-emitting the contract
    #    fields — exactly what a reader critic does in production.
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed",
        actor="prd-critic",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "prd-review",
            "child_id": child_id,
            "status": "completed",
            **child_payload,
        },
    )])
    # 3) idle tick lets the aggregate fire if it didn't already.
    orch.run_once(events=[])
    return log.read_all()


def test_prd_review_aggregate_inherits_prd_ref_and_approves(tmp_path: Path):
    # The critic re-emits nothing structured (no prd_ref in its completion).
    # The aggregate must inherit prd_ref/artifact_refs/evidence_refs from the
    # child's trigger_payload to satisfy prd.approved's required-schema.
    events = _drive_prd_review(tmp_path, child_payload={})
    types = [e.type for e in events]

    assert "prd.approved" in types, f"expected prd.approved, got {types}"
    assert "prd.blocked" not in types
    approved = next(e for e in events if e.type == "prd.approved")
    assert approved.payload.get("prd_ref") == "docs/plans/training-mode-prd.md"
    assert "docs/plans/training-mode-prd.md" in approved.payload.get("artifact_refs", [])

    aggregate = next(
        e for e in events if e.type == "fanout.aggregate.completed"
    )
    assert aggregate.payload["status"] == "completed"


def test_prd_review_blocks_when_contract_fields_absent_everywhere(tmp_path: Path):
    # Edge: nothing carries prd_ref (no trigger_payload, no child re-emit). The
    # aggregate must still fail-closed to prd.blocked — the fix inherits the
    # field when present, it does not fabricate it.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _prd_review_config(), _RecordingTransport())
    # start with a trigger that carries NO contract fields
    orch.run_once(events=[ZfEvent(
        type="prd.ready", actor="zf-cli", correlation_id="trace-1",
        payload={"pdd_id": "F-1"},
    )])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed", actor="prd-critic", correlation_id="trace-1",
        payload={"fanout_id": started.payload["fanout_id"],
                 "child_id": started.payload["expected_children"][0]["child_id"],
                 "status": "completed"},
    )])
    orch.run_once(events=[])
    types = [e.type for e in log.read_all()]
    assert "prd.blocked" in types
    assert "prd.approved" not in types


def test_prd_review_child_failure_blocks(tmp_path: Path):
    # Edge: the critic itself fails → failure_event (prd.blocked), not approved.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _prd_review_config(), _RecordingTransport())
    orch.run_once(events=[ZfEvent(
        type="prd.ready", actor="zf-cli", correlation_id="trace-1",
        payload={"pdd_id": "F-1", "prd_ref": "docs/plans/x.md",
                 "artifact_refs": ["docs/plans/x.md"], "evidence_refs": ["e1"]},
    )])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    orch.run_once(events=[ZfEvent(
        type="workflow.child.failed", actor="prd-critic", correlation_id="trace-1",
        payload={"fanout_id": started.payload["fanout_id"],
                 "child_id": started.payload["expected_children"][0]["child_id"],
                 "status": "failed", "reason": "critic rejected"},
    )])
    orch.run_once(events=[])
    types = [e.type for e in log.read_all()]
    assert "prd.blocked" in types
    assert "prd.approved" not in types


def _refactor_plan_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="refactor-plan-synth", instance_id="refactor-plan-synth",
                          backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="flow-plan",
                trigger="zaofu.refactor.review.ready",
                topology="fanout_reader",
                roles=["refactor-plan-synth"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="zaofu.refactor.plan.ready",
                    failure_event="zaofu.refactor.plan.blocked",
                ),
            ),
        ]),
    )


class _OkProjection:
    ok = True

    def __init__(self, payload):
        self.payload = payload


def test_refactor_plan_ready_aggregate_bridges_to_task_map(tmp_path, monkeypatch):
    # P0-2 integration: when the plan aggregate completes with a gate-passed
    # projection, the kernel must deterministically emit the first
    # task_map.ready (the refactor_plan_bridge) — the hop that livelocked in
    # production. The projection compilation is tested elsewhere; here we stub
    # it to a passing result with a task_map_ref and assert the bridge fires
    # through the real _evaluate_reader_fanout aggregate.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, _refactor_plan_config(), _RecordingTransport())

    monkeypatch.setattr(
        orch, "_project_refactor_success_artifacts",
        lambda **kw: _OkProjection({
            "task_map_ref": str(state_dir / "task_map.json"),
            "source_index_ref": str(state_dir / "source_index.json"),
            "feature_id": "F-1",
        }),
    )
    monkeypatch.setattr(orch, "_publish_refactor_plan_manifest", lambda **kw: None)
    # the bridge calls _maybe_start_writer_fanout; record instead of running it.
    started_impl = []
    monkeypatch.setattr(
        orch, "_maybe_start_writer_fanout", lambda ev: started_impl.append(ev))

    orch.run_once(events=[ZfEvent(
        type="zaofu.refactor.review.ready", actor="zf-cli", correlation_id="trace-1",
        payload={"pdd_id": "F-1", "target_ref": "main"},
    )])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    child_id = started.payload["expected_children"][0]["child_id"]
    orch.run_once(events=[ZfEvent(
        type="workflow.child.completed", actor="refactor-plan-synth",
        correlation_id="trace-1",
        payload={"fanout_id": fanout_id, "child_id": child_id, "status": "completed"},
    )])
    orch.run_once(events=[])

    events = log.read_all()
    task_map_ready = [e for e in events if e.type == "task_map.ready"]
    assert task_map_ready, f"bridge did not fire; got {[e.type for e in events]}"
    assert task_map_ready[-1].payload["source"] == "refactor_plan_bridge"
    assert task_map_ready[-1].payload["task_map_ref"].endswith("task_map.json")
    # and the impl writer fanout was started off the bridged event
    assert started_impl and started_impl[-1].type == "task_map.ready"
