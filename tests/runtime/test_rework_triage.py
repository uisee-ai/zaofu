from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "memory").mkdir()
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    EventLog(state / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    return state


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="triage-test", dry_run=True))


def _config(*roles: RoleConfig) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="triage-test"),
        session=SessionConfig(tmux_session="triage-test"),
        roles=list(roles) or [
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", publishes=["review.rejected"]),
            RoleConfig(name="test", backend="mock", publishes=["test.failed"]),
            RoleConfig(name="judge", backend="mock", publishes=["judge.passed", "judge.failed"]),
        ],
    )


def _strict_layer2_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="triage-test"),
        session=SessionConfig(tmux_session="triage-test"),
        verification=VerificationConfig(
            contract=ContractDConfig(
                required=True,
                dispatch_token_required=True,
            ),
        ),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="mock",
                triggers=["review.approved", "orchestrator.evidence_rework.requested"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                publishes=["review.approved", "review.rejected"],
                triggers=["dev.build.done"],
            ),
            RoleConfig(
                name="test",
                backend="mock",
                publishes=["test.passed", "test.failed"],
                triggers=["review.approved"],
            ),
            RoleConfig(
                name="judge",
                backend="mock",
                publishes=["judge.passed", "judge.failed"],
                triggers=["test.passed"],
            ),
        ],
    )


def _events(state_dir: Path):
    return EventLog(state_dir / "events.jsonl").read_all()


def _triage(events):
    return [event for event in events if event.type == "task.rework.triage.completed"]


def _evidence_contract() -> dict:
    return {
        "artifact_refs_must_be_relative": True,
        "evidence_refs_must_be_relative": True,
        "forbidden_ref_prefixes": ["/", ".zf/"],
    }


def test_product_test_failure_counts_retry_and_routes_rework(state_dir, transport):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="test.failed",
        actor="test",
        task_id="T1",
        payload={"reason": "assertion failed: expected token"},
    ))

    decisions = Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.retry_count == 1
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "product_issue"
    assert any(event.type == "task.rework.requested" for event in events)
    assert any(decision.action == "dispatch" for decision in decisions)


def test_verify_failed_counts_retry_and_routes_rework(state_dir, transport):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="verify"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="verify.failed",
        actor="verify",
        task_id="T1",
        payload={"reason": "focused regression failed"},
    ))

    decisions = Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.retry_count == 1
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "product_issue"
    assert any(event.type == "task.rework.requested" for event in events)
    assert any(decision.action == "dispatch" for decision in decisions)


def test_task_done_blocked_reissues_evidence_without_retry(state_dir, transport):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="testing", assigned_to="judge"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.done.blocked",
        actor="zf-cli",
        task_id="T1",
        payload={
            "missing": ["judge.passed payload missing checks"],
            "trigger_event": "judge.passed",
            "trigger_event_id": "evt-judge",
        },
    ))

    decisions = Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.retry_count == 0
    assert task.assigned_to == "judge"
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "evidence_payload_gap"
    assert any(event.type == "task.evidence.reissue.requested" for event in events)
    assert not any(event.type == "task.rework.requested" for event in events)
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "evidence_reissue"
        for event in events
    )
    assert any(decision.action == "dispatch" for decision in decisions)


def test_review_approved_invalid_evidence_reissues_same_assignee_before_layer2(
    state_dir,
    transport,
):
    store = TaskStore(state_dir / "kanban.json")
    task = Task(
        id="T1",
        title="x",
        status="review",
        assigned_to="review",
        active_dispatch_id="disp-review",
    )
    task.contract.evidence_contract = _evidence_contract()
    store.add(task)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="T1",
        payload={"role": "review", "assignee": "review", "dispatch_id": "disp-review"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "review", "assignee": "review", "dispatch_id": "disp-review"},
    ))
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="T1",
        payload={
            "dispatch_id": "disp-review",
            "artifact_refs": ["docs/records/review.md"],
            "evidence_refs": [
                "/path/to/example-project/.zf/briefings/review.md",
            ],
        },
    ))

    decisions = Orchestrator(state_dir, _strict_layer2_config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "review"
    assert task.retry_count == 0
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "evidence_payload_gap"
    assigned = [
        event for event in events
        if event.type == "task.assigned" and event.task_id == "T1"
    ][-1]
    assert assigned.payload["source"] == "evidence_reissue"
    assert assigned.payload["reissue"] is True
    assert assigned.payload["force_dispatch"] is True
    assert any(
        event.type == "task.evidence.reissue.requested"
        and "/.zf/" in event.payload["missing"][0]
        for event in events
    )
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "evidence_reissue"
        for event in events
    )
    assert not any(event.type == "dispatch.terminal.recorded" for event in events)
    assert any(decision.action == "dispatch" for decision in decisions)


def test_lifecycle_evidence_accepts_structured_artifact_refs_from_skill_adapters(
    state_dir,
    transport,
):
    task = Task(
        id="T1",
        title="x",
        status="review",
        assigned_to="review",
        active_dispatch_id="disp-review",
    )
    task.contract.evidence_contract = _evidence_contract()
    event = ZfEvent(
        type="review.approved",
        actor="review",
        task_id="T1",
        payload={
            "dispatch_id": "disp-review",
            "artifact_refs": [{
                "kind": "review_record",
                "path": "docs/records/review.md",
                "sha256": "a" * 64,
            }],
            "evidence_refs": [{"path": "docs/records/review.md"}],
        },
    )

    orch = Orchestrator(state_dir, _strict_layer2_config(), transport)
    violations = orch._evidence_contract_ref_violations(event, task)  # type: ignore[attr-defined]

    assert violations == []


def test_orchestrator_evidence_rework_event_dispatches_evidence_reissue(
    state_dir,
    transport,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="review", assigned_to="review"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.evidence_rework.requested",
        actor="orchestrator",
        task_id="T1",
        payload={
            "role": "orchestrator",
            "reason": "review.approved evidence_refs used .zf internal refs",
        },
    ))

    decisions = Orchestrator(state_dir, _strict_layer2_config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "review"
    events = _events(state_dir)
    assert any(
        event.type == "task.evidence.reissue.requested"
        and event.payload["trigger_event_type"] == "orchestrator.evidence_rework.requested"
        and event.payload["missing"] == [
            "review.approved evidence_refs used .zf internal refs",
        ]
        for event in events
    )
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "evidence_reissue"
        for event in events
    )
    assert any(decision.action == "dispatch" for decision in decisions)


def test_harness_rule_issue_blocks_without_retry_or_rework(state_dir, transport):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="review", assigned_to="review"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="gate.failed",
        actor="review",
        task_id="T1",
        payload={
            "gate": "scope",
            "reason": "false positive from harness rule bug",
        },
    ))

    decisions = Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    assert task.retry_count == 0
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "harness_rule_issue"
    assert any(event.type == "task.rework.triage.blocked" for event in events)
    assert any(event.type == "human.escalate" for event in events)
    assert not any(event.type == "task.rework.requested" for event in events)
    assert any(decision.action == "block" for decision in decisions)


def test_discriminator_harness_profile_failure_blocks_without_retry(
    state_dir,
    transport,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="testing", assigned_to="critic"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="discriminator.failed",
        actor="zf-cli",
        task_id="T1",
        payload={
            "failed_d": ["ContractD", "FunctionalD"],
            "details": [
                {
                    "d": "ContractD",
                    "reason": "verification command failed (rc=128)",
                    "evidence": {
                        "verification_stderr_tail": (
                            "fatal: ambiguous argument 'scoped': unknown "
                            "revision or path not in the working tree."
                        ),
                    },
                },
                {
                    "d": "FunctionalD",
                    "evidence": {
                        "gate_checks": {
                            "static": [
                                {
                                    "command": "pnpm tsc -b --noEmit",
                                    "exit_code": 1,
                                    "stderr": (
                                        "error TS5083: Cannot read file "
                                        "'tsconfig.json'."
                                    ),
                                },
                                {
                                    "command": "pnpm biome ci .",
                                    "exit_code": 1,
                                    "stderr": "No files were processed.",
                                },
                            ],
                        },
                    },
                },
            ],
        },
    ))

    decisions = Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    assert task.retry_count == 0
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "harness_rule_issue"
    assert any(event.type == "task.rework.triage.blocked" for event in events)
    assert not any(event.type == "task.rework.requested" for event in events)
    assert any(decision.action == "block" for decision in decisions)


def test_environment_failure_blocks_without_retry_or_rework(state_dir, transport):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="test.failed",
        actor="test",
        task_id="T1",
        payload={"reason": "external service timeout and connection refused"},
    ))

    Orchestrator(state_dir, _config(), transport).run_once()

    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    assert task.retry_count == 0
    events = _events(state_dir)
    assert _triage(events)[-1].payload["classification"] == "environment_issue"
    assert any(event.type == "task.rework.triage.blocked" for event in events)
    assert not any(event.type == "task.rework.requested" for event in events)
