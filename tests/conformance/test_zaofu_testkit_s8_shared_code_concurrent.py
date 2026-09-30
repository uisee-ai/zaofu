"""Two code Runs actually deliver disjoint files and release a queued code Run."""

import json
import shlex
import sys

from tests.testkit.issue_route import git, isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


class ScopedCodeWorker(SharedKindWorker):
    def __init__(self, route, invoke, kind):
        super().__init__(route, invoke, kind)
        self.answer_file = f"{kind}-answer.txt"
        self.probe_file = f"tests/verify_{kind}.py"
        self.command = f"{shlex.quote(sys.executable)} {self.probe_file}"

    def complete_reader(self, dispatch, *, plan=False, extra=None):
        detail = dict(extra or {})
        detail.update(summary=f"Deliver only {self.answer_file} and verify its declared probe.",
                      evidence_refs=[f"docs/intake/{self.flow_kind}.md"])
        if "coverage_matrix" in detail:
            detail["coverage_matrix"] = [
                {**row, "source_ref": self.answer_file} for row in detail["coverage_matrix"]
            ]
        return super().complete_reader(dispatch, plan=plan, extra=detail)

    def plan_result(self):
        plan = super().plan_result()
        task_map = plan["task_map"]
        task = task_map["tasks"][0]
        task["allowed_paths"] = task["scope"] = [self.answer_file]
        task["verification_read_paths"] = [self.probe_file]
        task_map["goal_claims"][0]["text"] = f"{self.answer_file} contains fixed"
        task["acceptance_criteria"][0]["statement"] = f"{self.answer_file} contains fixed"
        plan["plan_md"] = f"# Plan\nDeliver {self.answer_file}; run {self.probe_file}.\n"
        if "refactor_plan_md" in plan:
            plan["refactor_plan_md"] = plan["plan_md"]
        write_sidecar_json(
            self.state, plan["task_map_ref"], task_map,
            kind="task-map", schema_version="task-map.v1", created_by="planner",
        )
        source_index = json.loads((self.state / plan["source_index_ref"]).read_text())
        source_index["tasks"][0].update(
            source_ref=f"docs/intake/{self.flow_kind}.md",
            source_excerpt=f"{self.answer_file} must contain fixed.",
        )
        write_sidecar_json(
            self.state, plan["source_index_ref"], source_index,
            kind="source-index", schema_version="source-index.v1", created_by="planner",
        )
        return plan


def _finish_verified(worker):
    if worker.flow_kind == "prd":
        worker.complete_reader(worker.dispatch("prd-post-verify-discovery"))
        judge = "prd-lanes-final"
    else:
        worker.complete_reader(worker.dispatch("flow-verify-bridge"))
        worker.complete_reader(worker.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 0})
        judge = "flow-final-judge"
    worker.complete_judge(worker.dispatch(judge))
    for _ in range(12):
        worker.tick()
        if worker.events("run.goal.completed") and worker.store.get(worker.delivery_task_id).status == "done":
            break
    [terminal] = worker.events("run.goal.completed")
    assert terminal.payload["target_commit"] == worker.candidate()[1]


def test_disjoint_code_runs_deliver_and_release_queued_issue(tmp_path, monkeypatch):
    commands = tuple(f"{shlex.quote(sys.executable)} tests/verify_{kind}.py" for kind in ("prd", "refactor"))
    forbidden = isolate_issue_processes(tmp_path, monkeypatch, extra_commands=commands)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    for kind in ("prd", "refactor"):
        (root / f"{kind}-answer.txt").write_text("broken\n")
        (root / f"tests/verify_{kind}.py").write_text(
            f"from pathlib import Path\nassert Path('{kind}-answer.txt').read_text().strip() == 'fixed'\n"
            "print('issue regression passed')\n"
        )
        (root / f"docs/intake/{kind}.md").write_text(f"{kind}-answer.txt must contain fixed.\n")
    # Keep the production global candidate check. It now validates the actual
    # changed deliverable(s), not an unrelated Run's still-broken input file.
    (root / "tests/verify_issue.py").write_text(
        "from pathlib import Path\nimport subprocess\n"
        "changed = subprocess.check_output(['git', 'diff', '--name-only', 'fixture-base', 'HEAD'], text=True).splitlines()\n"
        "assert changed and set(changed) <= {'answer.txt', 'prd-answer.txt', 'refactor-answer.txt'}, changed\n"
        "for path in changed:\n    assert Path(path).read_text().strip() == 'fixed', path\n"
        "print('issue regression passed')\n"
    )
    paths = ("tests/verify_issue.py", "prd-answer.txt", "refactor-answer.txt", "tests/verify_prd.py", "tests/verify_refactor.py")
    git(root, "add", "--", *paths)
    git(root, "commit", "-q", "-m", "fixture independent code deliverables", "--", *paths)
    route.base = git(root, "rev-parse", "HEAD")
    git(root, "tag", "fixture-base")
    route.reopen()
    with route.evidence.guard("concurrent_code_terminal_release", {}):
        route.start()
        for stage in ("scope", "collect-1", "collect-2", "synthesize"):
            route.complete_reader(route.dispatch(stage))
        route.complete_verify(route.dispatch("verify"))
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed"):
                break
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == []
        invokes = {kind: route.request_delivery(
            kind, scope=(f"{kind}-answer.txt",),
            objective=f"Set {kind}-answer.txt to fixed and pass tests/verify_{kind}.py",
        )
                   for kind in ("prd", "refactor")}
        workers = {kind: ScopedCodeWorker(route, invoke, kind) for kind, invoke in invokes.items()}
        writers = {kind: worker.continue_to_writer() for kind, worker in workers.items()}
        ids = [invoke.payload["workflow_run_id"] for invoke in invokes.values()]
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == ids
        children = {kind: worker.child(writers[kind]) for kind, worker in workers.items()}
        assert len({child["workdir"] for child in children.values()}) == 2
        for kind, child in children.items():
            snapshot = route.body(child["contract_snapshot_ref"], child["contract_snapshot_digest"])
            assert snapshot["allowed_paths"] == [f"{kind}-answer.txt"]
        issue = route.request_delivery("issue", scope=("answer.txt",))
        route.tick()
        assert build_run_admission_projection(route.log.read_all()).queued_run_ids == [issue.payload["workflow_run_id"]]
        for kind, worker in workers.items():
            worker.complete_writer(writers[kind])
        route.reopen()
        for kind, worker in workers.items():
            prefix = "prd-lanes" if kind == "prd" else "flow-lanes"
            worker.complete_verify(worker.dispatch(f"{prefix}-verify"))
            candidate_root, _ = worker.candidate()
            assert (candidate_root / f"{kind}-answer.txt").read_text() == "fixed\n"
            other = "refactor" if kind == "prd" else "prd"
            assert (candidate_root / f"{other}-answer.txt").read_text() == "broken\n"
        _finish_verified(workers["prd"])
        for _ in range(12):
            route.tick()
            projection = build_run_admission_projection(route.log.read_all())
            if issue.payload["workflow_run_id"] in projection.active_run_ids:
                break
        assert projection.active_run_ids == [ids[1], issue.payload["workflow_run_id"]]
        assert projection.queued_run_ids == []
        _finish_verified(workers["refactor"])
        SharedKindWorker(route, issue, "issue").finish()
        ticks = TickServiceState()
        for _ in range(12):
            route.tick()
            run_standard_tick_services(route.runtime, state=ticks, now=1.0)
            owners = route.events("owner.visible_message.requested")
            if len(owners) == 4:
                break
        assert len(owners) == 4
        receipts = {}
        terminals = {event.payload["run_id"]: event for event in route.events("run.goal.completed")}
        assert len(terminals) == 4
        for owner in owners:
            terminal = terminals[owner.payload["run_id"]]
            for key, identity in (("dossier_ref", "run_id"), ("completion_receipt_ref", "workflow_run_id")):
                path = route.state / owner.payload[key]
                receipts[path] = path.read_bytes()
                body = json.loads(receipts[path])
                assert body[identity] == terminal.payload["run_id"]
                assert body["terminal"]["event_id"] == terminal.id
        for dispatch in route.events("fanout.child.dispatched"):
            [operation] = [event for event in route.events("workflow.operation.settled")
                           if event.payload["operation_id"] == dispatch.payload["operation_id"]]
            assert operation.payload["workflow_run_id"] == dispatch.payload["trace_id"]
        before = {name: [event.id for event in route.events(name)] for name in (
            "run.admission.admitted", "fanout.child.dispatched", "candidate.ready", "run.goal.completed",
        )}
        route.reopen()
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=ticks, now=2.0)
        assert before == {name: [event.id for event in route.events(name)] for name in before}
        assert {path: path.read_bytes() for path in receipts} == receipts
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == []
        assert git(root, "rev-parse", "main") == route.base
        assert not forbidden
