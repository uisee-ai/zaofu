"""Two independent catalog lanes must join before candidate and final delivery."""

from pathlib import Path
import subprocess

import pytest

from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.issue_route import git, isolate_issue_processes
from tests.testkit.refactor_two_lane_route import TwoLaneStrongRefactorRoute
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def two_lane(tmp_path, monkeypatch):
    cls = TwoLaneStrongRefactorRoute
    forbidden = isolate_issue_processes(tmp_path, monkeypatch, extra_commands=(
        cls.probe_command(cls.second_probe), cls.probe_command(cls.product_probe),
    ))
    route = cls(tmp_path / "target", monkeypatch)
    source = {p.name: p.read_bytes() for p in route.source_root.iterdir()}
    with route.evidence.guard("two_lane", {}):
        yield route
        assert not forbidden
        assert {p.name: p.read_bytes() for p in route.source_root.iterdir()} == source
        assert not any(line.startswith("remote.") for line in
                       git(route.root, "config", "--local", "--list").splitlines())
        ledger = (route.state / "events.jsonl").read_text()
        assert all(token not in ledger for token in route.credentials.values())


@pytest.mark.parametrize("reverse_and_reopen", [False, True])
def test_two_lane_signed_delivery_barriers_and_rebuild(two_lane, reverse_and_reopen):
    assert_two_lane_delivery(two_lane, reverse_and_reopen=reverse_and_reopen)


def assert_two_lane_delivery(route, *, reverse_and_reopen):
    product_command = route.probe_command(route.product_probe)
    assert subprocess.run(product_command, shell=True, cwd=route.root,
                          capture_output=True, timeout=15).returncode == 1
    route.start()
    for role in ("scan-contract", "scan-runtime", "scan-verification"):
        route.complete_reader(route.dispatch("flow-scan", role), extra={
            "coverage_matrix": [{"area": "core", "status": "covered", "source_ref": "answer.txt"}],
        })
    route.complete_reader(route.dispatch("flow-plan"), plan=True)
    route.complete_critic()
    route.dispatch("flow-lanes-impl")
    writers = [e for e in route.events("fanout.child.dispatched") if e.payload["stage_id"] == "flow-lanes-impl"]
    assert len(writers) == 2
    assert {e.payload["role_instance"] for e in writers} == {"dev-lane-0", "dev-lane-1"}
    task_ids = {route.delivery_task_id, route.second_task_id}
    assert {e.payload["task_id"] for e in writers} == task_ids
    assert len({e.payload["workdir"] for e in writers}) == 2
    assert len({e.payload["run_id"] for e in writers}) == 2
    if reverse_and_reopen:
        writers.reverse()
    source_commits = []
    for index, writer in enumerate(writers):
        completed = route.complete_writer(writer)
        source_commits.append(completed.payload["source_commit"])
        route.tick()
        if index == 0:
            assert not route.events("candidate.ready")
            assert git(route.root, "rev-parse", "main") == route.base
            if reverse_and_reopen:
                route.reopen()
    route.dispatch("flow-lanes-verify")
    path, candidate_commit = route.candidate()
    for commit in source_commits:
        changed = git(route.root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        assert changed in {"answer.txt", "answer-second.txt"}
        assert git(route.root, "rev-list", "--cherry-pick", "--right-only",
                   f"{candidate_commit}...{commit}") == ""
    verifiers = [e for e in route.events("fanout.child.dispatched") if e.payload["stage_id"] == "flow-lanes-verify"]
    assert len(verifiers) == 2
    assert {e.payload["role_instance"] for e in verifiers} == {"verify-lane-0", "verify-lane-1"}
    assert {e.payload["task_id"] for e in verifiers} == task_ids
    assert {e.payload["target_commit"] for e in verifiers} == {candidate_commit}
    writer_lanes = {e.payload["task_id"]: e.payload["role_instance"].rsplit("-", 1)[1] for e in writers}
    assert all(writer_lanes[e.payload["task_id"]] == e.payload["role_instance"].rsplit("-", 1)[1]
               for e in verifiers)
    if reverse_and_reopen:
        verifiers.reverse()
    route.complete_verify(verifiers[0])
    route.tick()
    assert not route.events("test.passed")
    assert not route.events("ship.started") and not route.events("run.goal.completed")
    if reverse_and_reopen:
        route.reopen()
    route.complete_verify(verifiers[1])
    route.complete_reader(route.dispatch("flow-verify-bridge"))
    route.complete_reader(route.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 0})
    route.complete_judge(route.dispatch("flow-final-judge"))
    ticks = TickServiceState()
    for _ in range(12):
        route.tick()
        run_standard_tick_services(route.runtime, state=ticks, now=1.0)
        if (audit_terminal_delivery(route.state)["status"] == "passed"
                and all(route.store.get(task_id).status == "done" for task_id in task_ids)):
            break
    audit = audit_terminal_delivery(route.state)
    assert audit["status"] == "passed", audit
    [terminal], [ship], [settled] = (route.events(kind) for kind in (
        "run.goal.completed", "ship.completed", "run.delivery.settled"))
    assert set(terminal.payload["completed_task_ids"]) == task_ids
    assert all(route.store.get(task_id).status == "done" for task_id in task_ids)
    assert terminal.payload["workflow_run_id"] == settled.payload["run_id"] == route.invoke.payload["workflow_run_id"]
    assert terminal.payload["target_commit"] == ship.payload["source_commit"] == candidate_commit
    assert terminal.payload["delivery_policy"] == "ship_candidate"
    assert terminal.payload["product_acceptance_required"] is True
    assert terminal.payload["product_acceptance_verdict"] == "passed"
    landed = git(route.root, "rev-parse", "main")
    assert landed == ship.payload["final_commit"] != route.base
    assert git(route.root, "rev-parse", ship.payload["final_tag"]) == landed
    assert git(route.root, "merge-base", "--is-ancestor", candidate_commit, landed) == ""
    for name in ("answer.txt", "answer-second.txt"):
        assert git(route.root, "show", f"{landed}:{name}") == "fixed"
    report = route.body(terminal.payload["product_acceptance_report_ref"],
                        terminal.payload["product_acceptance_report_digest"])
    assert report["target_commit"] == candidate_commit
    refs = report["journey_results"][0]["assertion_results"][0]["evidence_refs"]
    probes = [route.body(ref["ref"], ref["sha256"]) for ref in refs]
    [product] = [body for body in probes if body["command"] == product_command]
    assert product["exit_code"] == 0 and product["stdout"] == "two-lane product passed\n"
    assert product["target_commit"] == candidate_commit
    assert Path(path).exists()
    admissions = route.events("workflow.call.result.admitted")
    assert len(admissions) == 13 + len(getattr(route, "catalog_results", {}))
    for admission in admissions:
        assert admission.payload["workflow_run_id"] == route.invoke.payload["workflow_run_id"]
        for key in ("envelope_ref", "control_result_ref"):
            ref = admission.payload[key]
            route.body(ref["ref"], ref["sha256"])
    assert not route.events("orchestrator.tick.failed")
    kinds = {"fanout.child.dispatched", "fanout.synth.dispatched", "workflow.call.result.admitted",
             "candidate.ready", "run.goal.completed", "ship.started", "ship.completed",
             "run.delivery.requested", "run.delivery.settled", "owner.visible_message.requested"}
    before = [e.to_json() for e in route.log.read_all() if e.type in kinds]
    route.reopen()
    for _ in range(3):
        route.tick()
        run_standard_tick_services(route.runtime, state=ticks, now=1.0)
    assert [e.to_json() for e in route.log.read_all() if e.type in kinds] == before
    assert git(route.root, "rev-parse", "main") == landed
    assert all(route.store.get(task_id).status == "done" for task_id in task_ids)
