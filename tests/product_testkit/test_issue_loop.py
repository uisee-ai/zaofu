import json

import pytest

from .issue_loop import IssueLoopError, IssueMergeLoop


def _ready_tests(verdict: str = "pass") -> dict:
    return {
        "verdict": verdict,
        "delivery_readiness": {"allowed": verdict == "pass"},
    }


def test_issue_loop_runs_ordered_success_and_post_merge_regression():
    calls: list[str] = []
    loop = IssueMergeLoop()

    def triage(issue):
        calls.append("triage")
        return {"status": "ready", "issue_id": issue["id"], "revision": issue["revision"]}

    def approve(result):
        calls.append("approve")
        return True

    def tests(result):
        calls.append("tests")
        return _ready_tests()

    def merge(result):
        calls.append("merge")
        return {"status": "merged", "candidate_ref": "candidate/1"}

    def post(result):
        calls.append("post_merge")
        return {"verdict": "pass"}

    result = loop.run({"id": "ISSUE-1", "revision": "r1", "body": "untrusted"},
                      triage=triage, approve=approve, run_tests=tests,
                      merge=merge, post_merge=post)
    assert result.passed is True
    assert result.states == (
        "ingressed", "triage:ready", "approval:accepted", "tests:pass",
        "delivery:allowed", "merge:merged", "post_merge:pass",
    )
    assert calls == ["triage", "approve", "tests", "merge", "post_merge"]


def test_issue_loop_blocks_merge_when_product_tests_fail():
    calls: list[str] = []
    loop = IssueMergeLoop()
    result = loop.run(
        {"issue_id": "ISSUE-2", "revision": "r1"},
        triage=lambda issue: {"status": "ready"},
        approve=lambda result: True,
        run_tests=lambda result: (calls.append("tests") or _ready_tests("fail")),
        merge=lambda result: (calls.append("merge") or {"status": "merged"}),
        post_merge=lambda result: {"verdict": "pass"},
    )
    assert result.status == "blocked"
    assert result.reason == "product_tests_not_passed"
    assert result.merge_called is False
    assert calls == ["tests"]


def test_issue_loop_requires_explicit_approval_and_is_idempotent_for_duplicate_revision():
    calls: list[str] = []
    loop = IssueMergeLoop()
    issue = {"id": "ISSUE-3", "revision": "r1"}
    kwargs = {
        "triage": lambda value: (calls.append("triage") or {"status": "ready"}),
        "approve": lambda value: (calls.append("approve") or False),
        "run_tests": lambda value: (calls.append("tests") or _ready_tests()),
        "merge": lambda value: (calls.append("merge") or {"status": "merged"}),
        "post_merge": lambda value: {"verdict": "pass"},
    }
    first = loop.run(issue, **kwargs)
    second = loop.run(issue, **kwargs)
    assert first.status == second.status == "blocked"
    assert second.duplicate is True
    assert calls == ["triage", "approve"]


def test_issue_loop_surfaces_post_merge_regression_failure_without_rewriting_merge():
    merge_calls: list[str] = []
    loop = IssueMergeLoop()
    result = loop.run(
        {"id": "ISSUE-4", "revision": "r1"},
        triage=lambda issue: {"status": "ready"},
        approve=lambda result: True,
        run_tests=lambda result: _ready_tests(),
        merge=lambda result: (merge_calls.append("merge") or {"status": "merged"}),
        post_merge=lambda result: {"verdict": "fail"},
    )
    assert result.status == "post_merge_failed"
    assert result.merge_called is True and merge_calls == ["merge"]
    assert result.reason == "post_merge_regression_failed"


def test_issue_loop_persists_revision_idempotency_across_restart(tmp_path):
    state_path = tmp_path / "issue-loop.json"
    calls: list[str] = []
    issue = {"id": "ISSUE-5", "revision": "r1"}
    first = IssueMergeLoop(state_path).run(
        issue,
        triage=lambda value: {"status": "ready"},
        approve=lambda value: True,
        run_tests=lambda value: _ready_tests(),
        merge=lambda value: (calls.append("merge") or {"status": "merged"}),
        post_merge=lambda value: {"verdict": "pass"},
    )
    second = IssueMergeLoop(state_path).run(
        issue,
        triage=lambda value: (_ for _ in ()).throw(AssertionError("duplicate re-entered triage")),
        approve=lambda value: True,
        run_tests=lambda value: _ready_tests(),
        merge=lambda value: (_ for _ in ()).throw(AssertionError("duplicate merged twice")),
        post_merge=lambda value: {"verdict": "pass"},
    )
    assert first.passed and second.passed and second.duplicate
    assert calls == ["merge"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["results"][0]["revision"] == "r1"


def test_issue_loop_rejects_corrupt_persisted_state(tmp_path):
    path = tmp_path / "issue-loop.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(IssueLoopError):
        IssueMergeLoop(path)


def test_issue_loop_does_not_report_success_when_state_persistence_fails(tmp_path):
    path = tmp_path / "state-dir"
    path.mkdir()
    loop = IssueMergeLoop(path)
    with pytest.raises(IssueLoopError):
        loop.run(
            {"id": "ISSUE-6", "revision": "r1"},
            triage=lambda value: {"status": "ready"},
            approve=lambda value: True,
            run_tests=lambda value: _ready_tests(),
            merge=lambda value: {"status": "merged"},
            post_merge=lambda value: {"verdict": "pass"},
        )
