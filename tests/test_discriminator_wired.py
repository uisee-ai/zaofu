"""Tests for G-DISC-4: DiscriminatorRunner wired into _on_test_passed."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    ContractDConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _make_config(
    *, gates: dict | None = None,
    require_contract: bool = False,
    roles: list[RoleConfig] | None = None,
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=roles or [RoleConfig(name="dev", backend="mock")],
        quality_gates=gates or {},
        verification=VerificationConfig(
            contract=ContractDConfig(required=require_contract),
        ),
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD")


def _review_test_judge_roles() -> list[RoleConfig]:
    return [
        RoleConfig(
            name="review",
            backend="mock",
            publishes=["review.approved", "review.rejected"],
        ),
        RoleConfig(
            name="test",
            backend="mock",
            publishes=["test.passed", "test.failed"],
        ),
        RoleConfig(
            name="judge",
            backend="mock",
            publishes=["judge.passed", "judge.failed"],
        ),
    ]


def _layer2_review_test_judge_roles() -> list[RoleConfig]:
    return [
        RoleConfig(
            name="orchestrator",
            backend="mock",
            triggers=[
                "user.message",
                "review.approved",
                "test.passed",
                "judge.passed",
            ],
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
    ]


def _judge_payload(*, tiers: list[str] | None = None) -> dict:
    tiers = tiers or ["static"]
    return {
        "summary": "verified by judge",
        "checks": [
            {
                "command": "true",
                "exit_code": 0,
                "passed": True,
                "tier": tier,
                "artifact_refs": [f".zf/artifacts/{tier}.txt"],
                "evidence_refs": [f".zf/evidence/{tier}.json"],
            }
            for tier in tiers
        ],
        "scores": {
            "correctness": {"score": 1, "passed": True},
            "completeness": {"score": 1, "passed": True},
            "regression_risk": {"score": 1, "passed": True},
            "evidence_quality": {"score": 1, "passed": True},
        },
        "artifact_refs": [".zf/artifacts/judge.txt"],
        "evidence_refs": [".zf/events.jsonl"],
    }


class TestImportProof:
    def test_discriminator_imported_by_orchestrator(self):
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        assert "DiscriminatorRunner" in src


class TestRunnerInitialized:
    def test_runner_attribute_exists(self, state_dir, transport):
        orch = Orchestrator(state_dir, _make_config(), transport)
        assert hasattr(orch, "_discriminator_runner")
        assert orch._discriminator_runner is not None

    def test_semantic_discriminator_enabled_from_loaded_yaml(
        self,
        tmp_path: Path,
        state_dir,
        transport,
    ):
        from zf.core.config.loader import load_config

        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: test\n"
            "verification:\n"
            "  semantic:\n"
            "    enabled: true\n"
        )
        cfg = load_config(cfg_path)

        orch = Orchestrator(state_dir, cfg, transport)

        names = [
            d.__class__.__name__
            for d in orch._discriminator_runner.discriminators
        ]
        assert "SemanticDiscriminator" in names


class TestEmptyContractAllowsTaskDone:
    def test_empty_contract_no_gates_advances_to_done(
        self, state_dir, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, _make_config(), transport)
        orch.run_once()

        assert store.get("T1").status == "done"

    def test_empty_contract_required_blocks_done(self, state_dir, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(
            state_dir, _make_config(require_contract=True), transport,
        )
        orch.run_once()

        assert store.get("T1").status == "in_progress"
        events = log.read_all()
        assert any(e.type == "discriminator.failed" for e in events)
        assert any(e.type == "task.rework.requested" for e in events)


class TestFailingContractBlocks:
    def test_failing_verification_blocks_done(self, state_dir, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="testing", assigned_to="dev",
            contract=TaskContract(behavior="x", verification="false"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, _make_config(), transport)
        orch.run_once()

        # Task NOT moved to done; verification failure is routed to rework.
        assert store.get("T1").status == "in_progress"
        events = log.read_all()
        assert any(e.type == "discriminator.failed" for e in events)
        assert any(e.type == "task.rework.requested" for e in events)

    def test_discriminator_failed_event_dispatches_bounded_rework(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(behavior="x", verification="false"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id="T1",
            payload={
                "failed_d": ["FunctionalD"],
                "details": [
                    {
                        "d": "FunctionalD",
                        "passed": False,
                        "reason": "pytest failed",
                        "evidence": {
                            "gate_checks": {
                                "test": [
                                    {
                                        "command": "PYTHONPATH=src pytest -q",
                                        "passed": False,
                                    },
                                ],
                            },
                        },
                    },
                ],
            },
        ))

        orch = Orchestrator(
            state_dir,
            _make_config(
                roles=[
                    RoleConfig(
                        name="dev",
                        backend="mock",
                        publishes=["dev.build.done"],
                    ),
                    RoleConfig(name="judge", backend="mock"),
                ],
            ),
            transport,
        )
        decisions = orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "in_progress"
        assert task.assigned_to == "dev"
        assert task.retry_count == 1
        events = log.read_all()
        assert any(e.type == "task.rework.requested" for e in events)
        assert any(
            e.type == "task.dispatched"
            and e.payload.get("source") == "rework"
            for e in events
        )
        assert any(d.action == "dispatch" for d in decisions)
        briefing = state_dir / "briefings" / "dev-T1-rework.md"
        assert briefing.exists()
        text = briefing.read_text()
        assert "FunctionalD: pytest failed" in text
        assert "test failed command `PYTHONPATH=src pytest -q`" in text

    def test_discriminator_failed_kernel_reworks_when_layer2_active(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            retry_count=1,
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id="T1",
            payload={"failed_d": ["ContractD"]},
        ))

        orch = Orchestrator(
            state_dir,
            _make_config(
                roles=[
                    RoleConfig(
                        name="orchestrator",
                        backend="mock",
                        triggers=["user.message"],
                    ),
                    RoleConfig(
                        name="dev",
                        backend="mock",
                        publishes=["dev.build.done"],
                    ),
                ],
            ),
            transport,
        )
        decisions = orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "in_progress"
        assert task.retry_count == 2
        assert any(d.action == "dispatch" for d in decisions)
        assert any(
            e.type == "task.rework.requested"
            and e.payload.get("trigger_event_type") == "discriminator.failed"
            for e in log.read_all()
        )

    def test_discriminator_failed_respects_rework_cap(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            retry_count=3,
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id="T1",
            payload={"failed_d": ["FunctionalD"]},
        ))

        orch = Orchestrator(
            state_dir,
            _make_config(
                roles=[
                    RoleConfig(
                        name="dev",
                        backend="mock",
                        max_rework_attempts=3,
                    ),
                    RoleConfig(name="judge", backend="mock"),
                ],
            ),
            transport,
        )
        decisions = orch.run_once()

        events = log.read_all()
        assert store.get("T1").retry_count == 4
        assert any(e.type == "task.rework.capped" for e in events)
        assert not any(e.type == "task.rework.requested" for e in events)
        assert any(d.action == "block" for d in decisions)


class TestPassingContractAdvances:
    def test_passing_verification_moves_to_done(self, state_dir, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="testing", assigned_to="dev",
            contract=TaskContract(behavior="x", verification="true"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, _make_config(), transport)
        orch.run_once()

        assert store.get("T1").status == "done"
        events = log.read_all()
        assert any(e.type == "discriminator.passed" for e in events)
        assert any(e.type == "task.done.evidence" for e in events)


class TestJudgeTerminalEvidence:
    def test_strict_judge_passed_without_payload_evidence_blocks_done(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(behavior="x", verification="true"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        judge_event = ZfEvent(type="judge.passed", actor="judge", task_id="T1")
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "block"
        assert store.get("T1").status == "testing"
        events = log.read_all()
        blocked = [e for e in events if e.type == "task.done.blocked"]
        assert len(blocked) == 1
        assert any(
            "judge.passed payload" in item
            for item in blocked[0].payload["missing"]
        )

    def test_strict_judge_passed_with_evidence_moves_to_done(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(behavior="x", verification="true"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        review_event = ZfEvent(
            type="review.approved", actor="review", task_id="T1",
        )
        test_event = ZfEvent(type="test.passed", actor="test", task_id="T1")
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(),
        )
        for event in [review_event, test_event, judge_event]:
            log.append(event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "move"
        assert store.get("T1").status == "done"
        events = log.read_all()
        done_events = [e for e in events if e.type == "task.done.evidence"]
        assert len(done_events) == 1
        payload = done_events[0].payload
        assert payload["review_event_id"] == review_event.id
        assert payload["test_event_id"] == test_event.id
        assert payload["judge_event_id"] == judge_event.id
        assert payload["payload_summary"] == "verified by judge"

    def test_judge_passed_discriminator_runs_on_task_ref_workspace(
        self, state_dir, transport,
    ):
        base_branch = _init_repo(state_dir.parent)
        _git(state_dir.parent, "checkout", "-q", "-b", "task/T1")
        (state_dir.parent / "marker.txt").write_text("task only\n", encoding="utf-8")
        _git(state_dir.parent, "add", "marker.txt")
        _git(state_dir.parent, "commit", "-q", "-m", "task marker")
        _git(state_dir.parent, "checkout", "-q", base_branch)
        assert not (state_dir.parent / "marker.txt").exists()
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(
                behavior="x",
                verification="test -f marker.txt",
            ),
        ))

        log = EventLog(state_dir / "events.jsonl")
        for event in [
            ZfEvent(type="review.approved", actor="review", task_id="T1"),
            ZfEvent(type="test.passed", actor="test", task_id="T1"),
        ]:
            log.append(event)
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload={
                **_judge_payload(),
                "target_ref": "refs/heads/task/T1",
            },
        )
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "move"
        events = log.read_all()
        passed = [event for event in events if event.type == "discriminator.passed"]
        assert len(passed) == 1
        assert passed[0].payload["workspace"]["target_commit"] == _git(
            state_dir.parent, "rev-parse", "refs/heads/task/T1",
        )

    def test_layer2_judge_passed_from_in_progress_still_runs_discriminator(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="judge",
            contract=TaskContract(behavior="x", verification="true"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        for event in [
            ZfEvent(type="review.approved", actor="review", task_id="T1"),
            ZfEvent(type="test.passed", actor="test", task_id="T1"),
        ]:
            log.append(event)
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(),
        )
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "move"
        assert store.get("T1").status == "done"
        events = log.read_all()
        assert any(e.type == "discriminator.passed" for e in events)
        assert any(e.type == "task.done.evidence" for e in events)

    def test_strict_judge_requires_contract_verification_tiers(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(
                behavior="x",
                verification="true",
                verification_tiers=["e2e"],
            ),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(tiers=["static"]),
        )
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "block"
        assert store.get("T1").status == "testing"
        blocked = [e for e in log.read_all() if e.type == "task.done.blocked"]
        assert any("e2e" in item for item in blocked[0].payload["missing"])

    def test_strict_judge_accepts_verification_tier_subtypes(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(
                behavior="x",
                verification="true",
                verification_tiers=["static", "runtime"],
            ),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(tiers=["static_quality", "runtime_regression"]),
        )
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "move"
        assert store.get("T1").status == "done"
        assert not [
            e for e in log.read_all()
            if e.type == "task.done.blocked"
        ]

    def test_strict_judge_requires_passed_contract_verification_tiers(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="testing",
            assigned_to="judge",
            contract=TaskContract(
                behavior="x",
                verification="true",
                verification_tiers=["e2e"],
            ),
        ))

        payload = _judge_payload(tiers=["static", "e2e"])
        payload["checks"][1]["passed"] = False
        payload["checks"][1]["exit_code"] = 1

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        judge_event = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=payload,
        )
        log.append(judge_event)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_review_test_judge_roles(),
            ),
            transport,
        )
        decision = orch._on_judge_passed(judge_event)

        assert decision is not None
        assert decision.action == "block"
        assert store.get("T1").status == "testing"
        blocked = [e for e in log.read_all() if e.type == "task.done.blocked"]
        assert any("e2e" in item for item in blocked[0].payload["missing"])

    def test_layer2_terminal_judge_passed_closes_after_corrected_evidence(
        self, state_dir, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="judge",
            contract=TaskContract(
                behavior="x",
                verification="true",
                verification_tiers=["runtime"],
            ),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        bad_judge = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(tiers=["static"]),
        )
        log.append(bad_judge)

        orch = Orchestrator(
            state_dir,
            _make_config(
                require_contract=True,
                roles=_layer2_review_test_judge_roles(),
            ),
            transport,
        )
        orch.run_once()

        assert store.get("T1").status == "in_progress"
        assert any(e.type == "task.done.blocked" for e in log.read_all())

        good_judge = ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload=_judge_payload(tiers=["runtime"]),
        )
        log.append(good_judge)
        decisions = orch.run_once()

        assert any(d.action == "move" and d.task_id == "T1" for d in decisions)
        assert store.get("T1").status == "done"
        events = log.read_all()
        done_events = [e for e in events if e.type == "task.done.evidence"]
        assert done_events[-1].payload["trigger_event_id"] == good_judge.id
        assert any(e.type == "discriminator.passed" for e in events)


class TestFailingFunctionalGateBlocks:
    def test_failing_gate_blocks_done(self, state_dir, transport):
        gates = {
            "lint": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        cfg = _make_config(gates=gates)
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, cfg, transport)
        orch.run_once()

        assert store.get("T1").status == "in_progress"
        events = log.read_all()
        assert any(e.type == "discriminator.failed" for e in events)
        assert any(e.type == "task.rework.requested" for e in events)


class TestEventPayload:
    def test_failed_event_lists_failed_d_names(self, state_dir, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="testing", assigned_to="dev",
            contract=TaskContract(behavior="x", verification="false"),
        ))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, _make_config(), transport)
        orch.run_once()

        events = log.read_all()
        failed_events = [e for e in events if e.type == "discriminator.failed"]
        assert len(failed_events) >= 1
        e = failed_events[-1]
        assert "ContractD" in (e.payload.get("failed_d") or [])

    def test_passed_event_lists_all_d_names(self, state_dir, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, _make_config(), transport)
        orch.run_once()

        events = log.read_all()
        passed_events = [e for e in events if e.type == "discriminator.passed"]
        assert len(passed_events) >= 1
        e = passed_events[-1]
        all_d = e.payload.get("all_d") or []
        assert "ContractD" in all_d
        assert "FunctionalD" in all_d

    def test_stale_fanout_terminal_is_rejected_before_discriminator(
        self,
        state_dir,
        transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="test"))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            task_id="T1",
            payload={
                "fanout_id": "fanout-old",
                "stage_id": "verify",
                "target_ref": "candidate/T1",
            },
        ))
        log.append(ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            task_id="T1",
            payload={
                "fanout_id": "fanout-new",
                "stage_id": "verify",
                "target_ref": "candidate/T1",
            },
        ))
        log.append(ZfEvent(
            type="test.passed",
            actor="test",
            task_id="T1",
            payload={
                "fanout_id": "fanout-old",
                "summary": "late stale success from old fanout",
            },
        ))
        orch = Orchestrator(
            state_dir,
            _make_config(roles=_review_test_judge_roles()),
            transport,
        )

        decisions = orch.run_once()

        task = store.get("T1")
        events = log.read_all()
        assert task is not None
        assert task.status == "testing"
        assert not [event for event in events if event.type == "discriminator.passed"]
        stale = [
            event for event in events
            if event.type == "task.completion.stale_rejected"
        ]
        terminal_rejected = [
            event for event in events if event.type == "dispatch.terminal.rejected"
        ]
        assert stale[-1].payload["reason"] == "stale_fanout_instance"
        assert stale[-1].payload["fanout_id"] == "fanout-old"
        assert stale[-1].payload["superseded_by"] == "fanout-new"
        assert terminal_rejected[-1].payload["reason"] == "stale_fanout_instance"
        assert decisions[-1].reason == "test.passed rejected: stale fanout instance"
