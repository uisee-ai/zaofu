"""LH-2.T1/T2 (降级版): rule-based SemanticDiscriminator.

Rather than LLM-as-judge (high-variance, slow, token-expensive), we
ship a deterministic rule-based D that catches the two most common
goal-drift failures:

  1. Scope fidelity — files touched must be inside task.contract.scope
     (when scope is non-empty; empty scope = unconstrained, pass).
  2. Exclusion hit — no touched file may be inside task.contract.exclusions.

LLM-as-judge with calibration set (the original LH-2 design) is
deferred to LH-2.5 so it can ship with its own calibration tooling and
cost tracking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.task.schema import Task, TaskContract, TaskEvidence
from zf.core.verification.discriminator import DiscriminatorRunner
from zf.core.verification.discriminators.semantic import SemanticDiscriminator


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".zf").mkdir()
    return tmp_path


@pytest.fixture
def event_log(workspace: Path) -> EventLog:
    return EventLog(workspace / ".zf" / "events.jsonl")


class TestScopeFidelity:
    def test_in_scope_files_pass(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="b", verification="v",
                scope=["src/zf/auth/", "tests/test_auth.py"],
            ),
            evidence=TaskEvidence(
                commit="abc",
                files_touched=["src/zf/auth/login.py",
                                "tests/test_auth.py"],
            ),
        )
        d = SemanticDiscriminator()
        r = d.evaluate(task, workspace, event_log)
        assert r.passed, r.reason

    def test_out_of_scope_file_fails(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="b", verification="v",
                scope=["src/zf/auth/"],
            ),
            evidence=TaskEvidence(
                commit="abc",
                files_touched=["src/zf/auth/login.py",
                                "src/zf/billing/charges.py"],
            ),
        )
        d = SemanticDiscriminator()
        r = d.evaluate(task, workspace, event_log)
        assert not r.passed
        assert "scope" in r.reason.lower()
        assert "billing" in r.reason

    def test_empty_scope_is_permissive(self, workspace, event_log):
        """When scope is empty, the task is unconstrained — pass."""
        task = Task(
            id="T1", title="x",
            contract=TaskContract(behavior="b", verification="v", scope=[]),
            evidence=TaskEvidence(commit="abc",
                                    files_touched=["anywhere.py"]),
        )
        d = SemanticDiscriminator()
        r = d.evaluate(task, workspace, event_log)
        assert r.passed


class TestExclusion:
    def test_touching_excluded_file_fails(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="b", verification="v",
                scope=[],  # unconstrained scope
                exclusions=["src/zf/core/state/"],
            ),
            evidence=TaskEvidence(commit="abc",
                                    files_touched=[
                                        "src/zf/core/state/session.py",
                                    ]),
        )
        d = SemanticDiscriminator()
        r = d.evaluate(task, workspace, event_log)
        assert not r.passed
        assert "exclusion" in r.reason.lower()


class TestMissingEvidence:
    def test_no_evidence_is_fail(self, workspace, event_log):
        task = Task(id="T1", title="x",
                    contract=TaskContract(
                        behavior="b", verification="v",
                        scope=["src/"],
                    ),
                    evidence=None)
        d = SemanticDiscriminator()
        r = d.evaluate(task, workspace, event_log)
        assert not r.passed
        assert "evidence" in r.reason.lower()


class TestRunnerIntegration:
    def test_semantic_plugs_into_runner_and_participates_in_AND(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(behavior="b", verification="v",
                                    scope=["src/zf/auth/"]),
            evidence=TaskEvidence(commit="abc",
                                    files_touched=[
                                        "src/zf/elsewhere.py",
                                    ]),
        )
        runner = DiscriminatorRunner([SemanticDiscriminator()])
        report = runner.run(task, workspace, event_log)
        assert not report.passed
        assert any("semantic" in r.d_name.lower()
                   for r in report.d_results)


class TestWireUp:
    def test_semantic_exists_as_module(self):
        src = Path(
            "src/zf/core/verification/discriminators/semantic.py"
        ).read_text()
        assert "class SemanticDiscriminator" in src
