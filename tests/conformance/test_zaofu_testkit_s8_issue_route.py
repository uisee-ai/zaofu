"""Official default Issue workflow, without Provider or remote writes."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.issue_route import IssueRoute, git, isolate_issue_processes
from zf.core.verification.event_schema import EventSchemaRegistry
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def issue_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    scenario = IssueRoute(tmp_path)
    with scenario.evidence.guard("official_issue_case", {}):
        yield scenario
        assert not forbidden, forbidden  # Also catch errors swallowed by production code.


@pytest.mark.parametrize("reopen_at", ["none", "writer-fact", "candidate"])
def test_issue_route_reaches_real_goal_completion(
    issue_route: IssueRoute, reopen_at: str,
) -> None:
    scenario = issue_route
    with scenario.evidence.guard("writer_verify_judge_terminal", {"reopen_at": reopen_at}):
        writer = scenario.start()
        assert scenario.submit()["ok"]  # Exact proposal replay never creates another invoke.
        assert len(scenario.events("workflow.invoke.requested")) == 1
        scenario.complete_writer(writer)
        if reopen_at == "writer-fact":
            scenario.reopen()
        if reopen_at == "candidate":
            for _ in range(12):
                scenario.tick()
                if scenario.events("candidate.ready"):
                    break
            assert scenario.events("candidate.ready")
            scenario.reopen()
        verify = scenario.dispatch("issue-lanes-verify")
        scenario.candidate()
        assert scenario.events("candidate.ready")
        assert not scenario.events("run.goal.completed")
        verified = scenario.complete_verify(verify)
        registry = EventSchemaRegistry.from_config(scenario.config)
        assert registry.validate(verified) == []
        judge = scenario.dispatch("issue-lanes-final")
        judged = scenario.complete_judge(judge)
        assert registry.validate(judged) == []
        scenario.assert_completed()
        # The same admitted Run must reach the real runtime's owner handoff,
        # not merely expose a terminal event or a separately built dossier.
        tick_state = TickServiceState()
        for _ in range(12):
            scenario.tick()
            run_standard_tick_services(scenario.runtime, state=tick_state, now=1.0)
            delivery = audit_terminal_delivery(scenario.state)
            if delivery["status"] != "pending":
                break
        assert delivery["status"] == "passed", delivery
        observation = summarize_flow_run(scenario.log.read_all(), scenario.invoke)
        assert observation["admission_status"] == "admitted", observation
        assert observation["terminal_status"] == "completed", observation
        assert delivery["terminal"]["run_id"] == observation["run_id"]
        assert delivery["terminal"]["event_id"] == observation["terminal_event_id"]
        [owner] = [event for event in scenario.events("owner.visible_message.requested")
                   if event.payload.get("terminal_event_id") == observation["terminal_event_id"]]
        assert owner.payload["run_id"] == observation["run_id"]
        persisted = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            ref = delivery["delivery"][key]
            assert owner.payload[key] == ref
            path = (scenario.state / ref).resolve()
            assert path.is_relative_to(scenario.state.resolve())
            persisted[path] = path.read_bytes()
            document = json.loads(persisted[path])
            identity_key = "run_id" if key == "dossier_ref" else "workflow_run_id"
            assert document[identity_key] == observation["run_id"]
            assert document["terminal"]["event_id"] == observation["terminal_event_id"]
            assert document["source_fingerprint"] == owner.payload[
                "dossier_source_fingerprint" if key == "dossier_ref"
                else "completion_receipt_fingerprint"
            ]
        before = scenario.settled_snapshot()
        for _ in range(3):
            scenario.tick()
            run_standard_tick_services(scenario.runtime, state=tick_state, now=1.0)
        scenario.assert_completed()
        assert scenario.settled_snapshot() == before
        assert [event.id for event in scenario.events("owner.visible_message.requested")
                if event.payload.get("terminal_event_id") == observation["terminal_event_id"]] == [owner.id]
        assert {path: path.read_bytes() for path in persisted} == persisted
        assert audit_terminal_delivery(scenario.state)["status"] == "passed"


@pytest.mark.parametrize("projection", ["not_projected", "newer_attempt"])
def test_issue_worker_reads_exact_dispatch_before_manifest_projection(
    issue_route: IssueRoute, monkeypatch: pytest.MonkeyPatch, projection: str,
) -> None:
    issue_route.complete_writer(issue_route.start())
    dispatch = issue_route.dispatch("issue-lanes-verify")
    original = deepcopy(dispatch.payload)
    _, commit = issue_route.candidate()
    assert original["target_commit"] == commit
    path = issue_route.state / "fanouts" / dispatch.payload["fanout_id"] / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    child = next(row for row in manifest["children"]
                 if row["child_id"] == dispatch.payload["child_id"])
    if projection == "not_projected":
        child["target_commit"] = ""
    else:
        child["target_commit"] = "wrong-generation"
        child["payload"]["operation_id"] = "wrong-operation"
    read_text = Path.read_text

    def projected_read(current: Path, *args, **kwargs):
        return json.dumps(manifest) if current == path else read_text(current, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", projected_read)
    verified = issue_route.complete_verify(dispatch)
    assert verified.payload["target_commit"] == commit
    assert verified.payload["operation_id"] == original["operation_id"]
    assert verified.payload["input_consumption_status"] == "satisfied"
    assert dispatch.payload == original


@pytest.mark.parametrize("replay_at", ["judge-fact-reopen", "terminal-replay", "terminal-reopen"])
def test_issue_old_candidate_replay_preserves_goal_closure(issue_route: IssueRoute, replay_at: str) -> None:
    scenario = issue_route
    with scenario.evidence.guard("closure_replay", {"replay_at": replay_at}):
        scenario.complete_writer(scenario.start())
        scenario.complete_verify(scenario.dispatch("issue-lanes-verify"))
        scenario.complete_judge(scenario.dispatch("issue-lanes-final"))
        if replay_at != "judge-fact-reopen":
            scenario.assert_completed()
        if replay_at.endswith("reopen"):
            scenario.reopen()
        else:
            scenario.runtime.run_once(events=scenario.log.read_all())
        scenario.assert_completed()
        assert len(scenario.events("flow.goal.closed")) == 1
        assert not scenario.events("goal.closure.superseded")
        assert len([event for event in scenario.events("fanout.child.dispatched")
                    if event.payload["stage_id"] == "issue-lanes-final"]) == 1
        run_standard_tick_services(scenario.runtime, state=TickServiceState(), now=1.0)
        delivery = audit_terminal_delivery(scenario.state)
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["run_id"] == scenario.invoke.payload["workflow_run_id"]


@pytest.mark.parametrize("mode", ["missing_reads", "stale_target"])
def test_issue_verify_rejects_missing_reads_or_old_commit(issue_route: IssueRoute, mode: str) -> None:
    scenario = issue_route
    with scenario.evidence.guard("invalid_verify", {"mode": mode}):
        scenario.complete_writer(scenario.start())
        verify = scenario.dispatch("issue-lanes-verify")
        report = scenario.complete_verify(verify, **{mode: True})
        for _ in range(5):
            scenario.tick()
        if mode == "missing_reads":
            repairs = scenario.events("workflow.call.result.repair.requested")
            assert repairs
            assert any(issue["code"] == "required_read_missing"
                       for event in repairs for issue in event.payload["issues"])
        else:
            [invalid] = scenario.events("workflow.call.result.invalid")
            assert invalid.payload["reason"] == "stale_call_result_superseded"
            assert any(issue["code"] == "stale_target_commit" for issue in invalid.payload["issues"])
        assert not scenario.events("test.passed")
        assert not scenario.events("goal.closure.synthesized")
        assert not scenario.events("run.goal.completed")
        assert all(event.payload["stage_id"] != "issue-lanes-final"
                   for event in scenario.events("fanout.child.dispatched"))
        assert not any(event.payload.get("source_event_id") == report.id
                       for event in scenario.events("workflow.call.result.admitted"))


def test_issue_judge_blocked_result_requests_attention_without_completion(issue_route: IssueRoute) -> None:
    scenario = issue_route
    with scenario.evidence.guard("blocked_judge", {}):
        scenario.complete_writer(scenario.start())
        scenario.complete_verify(scenario.dispatch("issue-lanes-verify"))
        scenario.complete_judge(scenario.dispatch("issue-lanes-final"), blocked=True)
        for _ in range(5):
            scenario.tick()
        [blocked] = scenario.events("goal.closure.blocked")
        assert blocked.origin == "kernel"
        assert any(event.causation_id == blocked.id for event in scenario.events("runtime.attention.needed"))
        assert not scenario.events("run.goal.completion.claimed")
        assert not scenario.events("run.goal.completed")


def test_issue_failed_real_probe_cannot_deliver_candidate(issue_route: IssueRoute) -> None:
    scenario = issue_route
    with scenario.evidence.guard("failed_worker_probe", {}):
        report = scenario.complete_writer(scenario.start(), broken=True)
        assert report.payload["impl_self_check"]["command_receipts"][0]["exit_code"] == 1
        for _ in range(5):
            scenario.tick()
        assert scenario.events("fanout.child.failed") or scenario.events("candidate.quality.failed")
        assert not scenario.events("candidate.ready")
        assert not scenario.events("test.passed")
        assert not scenario.events("run.goal.completed")


def test_issue_candidate_reruns_probe_instead_of_trusting_worker_green(issue_route: IssueRoute) -> None:
    scenario = issue_route
    with scenario.evidence.guard("candidate_rejects_wrong_tree_probe", {}):
        report = scenario.complete_writer(scenario.start(), broken=True, mask_probe=True)
        [receipt] = report.payload["impl_self_check"]["command_receipts"]
        assert receipt["exit_code"] == 0 and receipt["status"] == "passed"
        probe = json.loads((scenario.state / report.payload["evidence_refs"][0]).read_text())
        assert probe["executed_probe_sha256"] != probe["committed_probe_sha256"]
        assert probe["committed_probe_sha256"] == hashlib.sha256(
            (scenario.root / "tests/verify_issue.py").read_bytes()
        ).hexdigest()
        for _ in range(8):
            scenario.tick()
            if scenario.events("candidate.quality.failed"):
                break
        failures = scenario.events("candidate.quality.failed")
        assert failures, [event.type for event in scenario.log.read_all()]
        failed = failures[-1].payload
        assert failed["failed_task_ids"] == [report.task_id]
        quality = failed["quality"]
        assert quality["gate_source"] == "task_contract"
        [check] = [row for rows in quality["gate_checks"].values() for row in rows]
        assert check["command"] == scenario.command
        assert check["exit_code"] == 1 and not check["passed"]
        assert "AssertionError" in check["stderr_tail"]
        workdir = Path(quality["merger_worktree"])
        assert git(workdir, "rev-parse", "HEAD") == failed["commit"] == quality["commit"]
        assert git(workdir, "show", "HEAD:answer.txt") == "still broken"
        assert (workdir / "tests/verify_issue.py").read_bytes() == (
            scenario.root / "tests/verify_issue.py"
        ).read_bytes()
        rerun = subprocess.run(scenario.command, shell=True, cwd=workdir,
                               capture_output=True, text=True, timeout=15, check=False)
        assert rerun.returncode == 1 and "AssertionError" in rerun.stderr
        assert git(scenario.root, "rev-parse", "main") == scenario.base
        assert not scenario.events("candidate.ready")
        assert not scenario.events("candidate.quality.passed")
        assert not scenario.events("test.passed")
        assert not scenario.events("run.goal.completed")
        assert all(event.payload["stage_id"] == "issue-lanes-impl"
                   for event in scenario.events("fanout.child.dispatched"))
