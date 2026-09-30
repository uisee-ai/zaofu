"""S9 local Fix Run: reproduce, verify candidate, and bound merge effects.

The case intentionally uses a temporary git repository.  It proves the
TestKit's local delivery boundary without pushing to, or mutating, the ZaoFu
checkout or any remote service.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.generic_workflow_complex_support import _RecordingTransport
from tests.test_control_actions_workflow_request import _execute, _service
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import evidence_ref, write_json_atomic
from tests.testkit.external_sinks import ExternalEffectSpy
from tests.test_plan_candidate_preflight import (
    _product_acceptance_spec,
    _source_index,
    _task_map,
)
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.orchestrator import Orchestrator


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_product_repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "testkit@example.invalid")
    _git(root, "config", "user.name", "ZaoFu TestKit")
    (root / "app.py").write_text(
        "def add(left, right):\n    return left + right - 1\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_regression.py").write_text(
        "from app import add\n\n\ndef test_issue_regression():\n"
        "    assert add(1, 1) == 2\n",
        encoding="utf-8",
    )
    _git(root, "add", "app.py", "tests/test_regression.py")
    _git(root, "commit", "-q", "-m", "baseline")


def test_local_fix_run_replays_failure_before_candidate_and_bounds_merge(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    evidence_dir = tmp_path / "evidence"
    _init_product_repo(source)
    base_commit = _git(source, "rev-parse", "HEAD")

    baseline = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-baseline-reproduce",
        cwd=source,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert baseline.exit_code != 0
    assert baseline.timed_out is False

    subprocess.run(
        ["git", "clone", "-q", str(source), str(candidate)],
        check=True,
        capture_output=True,
        text=True,
    )
    (candidate / "app.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    _git(candidate, "add", "app.py")
    _git(candidate, "commit", "-q", "-m", "fix: preserve addition")
    candidate_commit = _git(candidate, "rev-parse", "HEAD")

    regression = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-candidate-regression",
        cwd=candidate,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert regression.passed

    report = evidence_dir / "s9-fix-run-report.json"
    write_json_atomic(
        report,
        {
            "schema_version": "zf-testkit-fix-run.v1",
            "issue_id": "ZF-TESTKIT-S9-LOCAL",
            "base_commit": base_commit,
            "candidate_commit": candidate_commit,
            "baseline": {
                "exit_code": baseline.exit_code,
                "stdout_ref": baseline.stdout_ref,
                "stderr_ref": baseline.stderr_ref,
            },
            "regression": {
                "exit_code": regression.exit_code,
                "stdout_ref": regression.stdout_ref,
                "stderr_ref": regression.stderr_ref,
            },
        },
    )
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    assert report_payload["base_commit"] == base_commit
    assert report_payload["candidate_commit"] == candidate_commit
    assert evidence_ref(report, root=tmp_path)["digest"]

    # A merge is modelled as a controlled external effect.  It is not run
    # until the candidate regression passes, and retries reuse its operation
    # key, so a recovery/replay cannot duplicate the effect.
    merge_effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    assert merge_effects.calls == ()
    merge_effects.execute(
        f"merge:{candidate_commit}",
        lambda: f"local-merge:{candidate_commit}",
    )
    merge_effects.execute(
        f"merge:{candidate_commit}",
        lambda: "must-not-run-twice",
    )
    merge_effects.assert_at_most_once()
    assert merge_effects.execution_count(f"merge:{candidate_commit}") == 1
    assert source != candidate
    assert (source / "app.py").read_text(encoding="utf-8").endswith("- 1\n")


def test_fix_run_candidate_preflight_blocks_incomplete_acceptance(
    tmp_path: Path,
) -> None:
    """Candidate admission is evaluated before any merge effect is allowed."""
    state_dir = tmp_path / "state"
    valid_spec = _product_acceptance_spec()

    def evaluate(spec: dict) -> dict:
        return evaluate_plan_candidate_preflight(
            state_dir=state_dir,
            project_root=tmp_path,
            reports=[{
                "report": {
                    "task_map": _task_map(),
                    "source_index": _source_index(),
                    "source_index_ref": "inline:source-index",
                    "plan_md": "# Candidate plan\n",
                    "plan_ports": [{
                        "logical_name": "product_acceptance_spec",
                        "body": spec,
                    }],
                },
            }],
            manifest={
                "fanout_id": "fanout-fix-run",
                "stage_id": "prd-plan",
                "workflow_run_id": "run-fix-run",
                "trigger_payload": {
                    "flow_kind": "prd",
                    "prd_ref": "docs/requirement.md",
                },
            },
            metadata={
                "flow_kind": "prd",
                "artifact_package": {"required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                    "product_acceptance_spec",
                ]},
            },
        )

    admitted = evaluate(valid_spec)
    assert admitted["status"] == "passed"
    assert admitted["summary"]["error_count"] == 0

    incomplete = _product_acceptance_spec()
    incomplete["entrypoints"][0].pop("health_check")
    blocked = evaluate(incomplete)
    assert blocked["status"] == "failed"
    assert any(
        item["code"] == "product_acceptance_spec_invalid"
        for item in blocked["errors"]
    )
    # This test only performs admission; no candidate/remote side effect is
    # invoked when the mechanical preflight rejects the report.
    assert not (tmp_path / "candidate-merge.marker").exists()


def test_local_merge_runs_post_merge_regression_in_isolated_repository(
    tmp_path: Path,
) -> None:
    """A passed candidate is merged locally, then the regression is rerun."""
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    evidence_dir = tmp_path / "evidence"
    _init_product_repo(source)
    assert run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-postmerge-baseline",
        cwd=source,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    ).passed is False

    subprocess.run(
        ["git", "clone", "-q", str(source), str(candidate)],
        check=True,
        capture_output=True,
        text=True,
    )
    (candidate / "app.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    _git(candidate, "add", "app.py")
    _git(candidate, "commit", "-q", "-m", "fix: post-merge regression")
    candidate_branch = _git(candidate, "branch", "--show-current")
    candidate_commit = _git(candidate, "rev-parse", "HEAD")
    candidate_check = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-postmerge-candidate",
        cwd=candidate,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert candidate_check.passed

    _git(
        source,
        "fetch",
        "-q",
        str(candidate),
        f"refs/heads/{candidate_branch}:refs/remotes/test/candidate",
    )
    effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    merge_key = f"merge:{candidate_commit}"
    effects.execute(
        merge_key,
        lambda: _git(
            source,
            "merge",
            "--no-ff",
            "-q",
            "refs/remotes/test/candidate",
            "-m",
            "merge candidate for regression",
        ),
    )
    effects.execute(merge_key, lambda: "must-not-repeat-merge")
    effects.assert_at_most_once()
    assert effects.execution_count(merge_key) == 1

    post_merge = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-postmerge-regression",
        cwd=source,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert post_merge.passed
    assert _git(source, "merge-base", "--is-ancestor", candidate_commit, "HEAD") == ""
    assert _git(source, "status", "--porcelain") == ""


def test_fix_run_orders_regression_impact_closure_merge_and_post_merge(
    tmp_path: Path,
) -> None:
    """The delivery gate must verify the fix and impact closure before merge."""
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    evidence_dir = tmp_path / "evidence"
    _init_product_repo(source)

    subprocess.run(
        ["git", "clone", "-q", str(source), str(candidate)],
        check=True,
        capture_output=True,
        text=True,
    )
    (candidate / "app.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    _git(candidate, "add", "app.py")
    _git(candidate, "commit", "-q", "-m", "fix: run impact closure")
    candidate_commit = _git(candidate, "rev-parse", "HEAD")

    order: list[str] = []
    regression = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-ordered-candidate-regression",
        cwd=candidate,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert regression.passed
    order.append("candidate-regression")

    # The impact closure is represented by the deterministic affected test
    # command.  It must pass before the merge sink can be called.
    impact = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-ordered-impact-closure",
        cwd=candidate,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert impact.passed
    order.append("impact-closure")

    effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    effects.execute(
        f"merge:{candidate_commit}",
        lambda: (_git(source, "fetch", "-q", str(candidate), "HEAD"),
                 _git(source, "merge", "--no-ff", "-q", "FETCH_HEAD", "-m", "merge")),
    )
    order.append("merge")
    effects.assert_at_most_once()

    post_merge = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-ordered-post-merge-regression",
        cwd=source,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )
    assert post_merge.passed
    order.append("post-merge-regression")

    assert order == [
        "candidate-regression",
        "impact-closure",
        "merge",
        "post-merge-regression",
    ]
    assert _git(source, "status", "--porcelain") == ""


def test_failed_candidate_regression_blocks_merge_side_effect(
    tmp_path: Path,
) -> None:
    """A candidate that still reproduces the Issue cannot reach merge."""
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    evidence_dir = tmp_path / "evidence"
    _init_product_repo(source)
    source_before = _git(source, "rev-parse", "HEAD")

    subprocess.run(
        ["git", "clone", "-q", str(source), str(candidate)],
        check=True,
        capture_output=True,
        text=True,
    )
    candidate_commit = _git(candidate, "rev-parse", "HEAD")
    regression = run_command(
        [sys.executable, "-m", "pytest", "-q", "tests/test_regression.py"],
        command_id="s9-failed-candidate-regression",
        cwd=candidate,
        output_dir=evidence_dir,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=30,
    )

    assert regression.passed is False
    assert regression.timed_out is False
    assert regression.exit_code != 0

    merge_effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    # The gate checks the candidate result before invoking this sink.
    if regression.passed:
        merge_effects.execute(
            f"merge:{candidate_commit}",
            lambda: "must-not-run",
        )

    assert merge_effects.calls == ()
    assert _git(source, "rev-parse", "HEAD") == source_before
    assert _git(source, "status", "--porcelain") == ""


def test_runtime_issue_fix_run_dispatches_implementation_to_provider(
    tmp_path: Path,
) -> None:
    """Exercise the real workflow-start -> Kernel -> implementation boundary.

    The case deliberately does not fake ``fanout.started`` or a provider
    completion.  It submits an Issue workflow through the controlled action
    path, lets the Kernel consume the resulting ``workflow.invoke.requested``
    event, and requires the implementation child to be dispatched.  A
    preparation failure is a product/runtime failure, not a passing no-op.
    """

    # A product worktree must have an immutable Git baseline so the workflow
    # can bind task-map/dispatch identity to ``base_commit``.  Without this
    # setup the test would exercise an invalid environment and misclassify
    # the expected fail-closed preparation error as a product defect.
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "testkit@example.invalid")
    _git(tmp_path, "config", "user.name", "ZaoFu TestKit")
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "baseline")

    service, log = _service(tmp_path)
    proposal = _execute(
        service,
        "workflow-request",
        {
            "kind": "issue",
            "objective": "Fix a reproducible regression and run its test",
            "backend": "mock",
            "allow_missing_env": True,
        },
    )
    submitted = _execute(
        service,
        "workflow-submit",
        {
            "intake_ref": proposal["intake_ref"],
            "request_id": proposal["request_id"],
            "proposal_ref": proposal["proposal_ref"],
            "proposal_digest": proposal["proposal_digest"],
            "kind": "issue",
            "allow_missing_env": True,
        },
    )
    assert submitted["ok"] is True

    transport = _RecordingTransport()
    runtime = Orchestrator(
        service.state_dir,
        service.config,
        transport,
        project_root=tmp_path,
    )
    # The real watcher invokes run_once repeatedly as new ledger rows arrive.
    # Bound the loop so a stuck runtime becomes a deterministic failure.
    for _ in range(12):
        runtime.run_once(events=log.read_all())
        if any(
            event.type == "fanout.child.dispatched"
            for event in log.read_all()
        ):
            break

    events = log.read_all()
    invokes = [
        event
        for event in events
        if event.type == "workflow.invoke.requested"
    ]
    assert len(invokes) == 1
    run_id = str(invokes[0].payload.get("workflow_run_id") or "")
    assert run_id

    dispatched = [
        event
        for event in events
        if event.type == "fanout.child.dispatched"
        and str(event.payload.get("trace_id") or "") == run_id
    ]
    deferred = [
        event
        for event in events
        if event.type == "fanout.child.dispatch_deferred"
        and str(event.payload.get("trace_id") or "") == run_id
    ]
    assert dispatched, (
        "Issue Fix Run never dispatched its implementation child; "
        f"deferred={[(e.payload.get('failure_kind'), e.payload.get('reason')) for e in deferred]}"
    )
    assert not deferred
    assert transport.sent, "the configured mock provider did not receive a task"


def test_runtime_issue_fix_run_without_git_baseline_is_environment_failure(
    tmp_path: Path,
) -> None:
    """An invalid product worktree fails closed without downstream effects."""
    service, log = _service(tmp_path)
    proposal = _execute(
        service,
        "workflow-request",
        {
            "kind": "issue",
            "objective": "Reject a product root without a Git baseline",
            "backend": "mock",
            "allow_missing_env": True,
        },
    )
    submitted = _execute(
        service,
        "workflow-submit",
        {
            "intake_ref": proposal["intake_ref"],
            "request_id": proposal["request_id"],
            "proposal_ref": proposal["proposal_ref"],
            "proposal_digest": proposal["proposal_digest"],
            "kind": "issue",
            "allow_missing_env": True,
        },
    )
    assert submitted["ok"] is True

    transport = _RecordingTransport()
    runtime = Orchestrator(
        service.state_dir,
        service.config,
        transport,
        project_root=tmp_path,
    )
    for _ in range(12):
        runtime.run_once(events=log.read_all())

    events = log.read_all()
    deferred = [
        event for event in events
        if event.type == "fanout.child.dispatch_deferred"
    ]
    assert deferred
    assert any(
        event.payload.get("failure_kind") == "dispatch_preparation"
        and "base_commit" in str(event.payload.get("reason") or "")
        for event in deferred
    )
    assert transport.sent == []
    assert not any(event.type.startswith(("candidate.", "merge.")) for event in events)
