from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tests.test_run_manager_rework_triage import _state
from tests.test_task_pipeline_reconciler import _policy, _task, _operation
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.recovery_association import admit_association, shared_repair_gate
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import _execute_controlled_run_action, _action_payload, _settle_pending_action_effects
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler


@pytest.fixture
def cases(tmp_path):
    state, log, writer = _state(tmp_path)
    config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    ids = []
    for i in range(8):
        TaskStore(state / "kanban.json").add(Task(id=f"T{i}", title="CSV", status="blocked"))
        ids.append(writer.append(ZfEvent(type="run.manager.action.failed", actor="run-manager", task_id=f"T{i}",
            correlation_id="run-1", payload={"workflow_run_id": "run-1", "recovery_case_id": f"case-{i}",
                "task_id": f"T{i}", "task_map_generation": "g1", "reason": "database unavailable"})).id)
    proposal = {"kind": "shared_resource", "workflow_run_id": "run-1", "member_case_ids": [f"case-{i}" for i in range(8)],
                "owner_case_id": "case-0", "resource_id": "test-db", "resource_version": "db-v1",
                "repair_step_id": "restart-1", "evidence_event_ids": ids,
                "rationale": "同一隔离数据库实例及配置版本；原失败探针确认共同服务不可用。"}
    return SimpleNamespace(state=state, log=log, writer=writer, config=config, proposal=proposal)


def _admit(c):
    return admit_association(state_dir=c.state, writer=c.writer, config=c.config,
        actor="orchestrator", proposal=c.proposal, apply=True)["association_id"]


def test_one_shared_writer_and_each_task_keeps_its_verification(cases, monkeypatch):
    c = cases
    association_id = _admit(c)
    assert _admit(c) == association_id
    action = {"action": "worker-lifecycle-recover", "safe_resume_action": "worker_lifecycle_recover",
        "task_id": "T0", "workflow_run_id": "run-1", "task_map_generation": "g1",
        "recovery_case_id": "case-0", "shared_repair_association_id": association_id,
        "resource_id": "test-db", "resource_version": "db-v1", "repair_step_id": "restart-1"}
    calls = []
    database = c.state.parent / "shared-test.sqlite"
    # The repair tool is a fixture; the resource and per-consumer probes are
    # real. This tests coordination, not a model's database diagnosis quality.
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            connection.execute("select name, email from contacts").fetchall()
    def repair(**kwargs):
        calls.append(1)
        c.writer.append(ZfEvent(type="run.manager.action.planned", payload=_action_payload(kwargs["action"])))
        with sqlite3.connect(database) as connection:
            connection.execute("create table contacts (name text, email text)")
            connection.execute("insert into contacts values ('客户', 'test@example.invalid')")
        return "applied"
    monkeypatch.setattr("zf.runtime.run_manager._execute_worker_lifecycle_recover", repair)
    from concurrent.futures import ThreadPoolExecutor
    def apply(_):
        return _execute_controlled_run_action(state_dir=c.state, writer=c.writer, config=c.config,
            project_root=c.state.parent, action=action, causation_id="test")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(apply, range(2)))
    assert calls == [1]
    replacement = admit_association(state_dir=c.state, writer=c.writer, config=c.config,
        actor="orchestrator", proposal={**c.proposal, "repair_step_id": "restart-2"}, apply=True)["association_id"]
    assert shared_repair_gate(c.state, c.log.read_all(), {**action,
        "shared_repair_association_id": replacement, "repair_step_id": "restart-2"}) == "shared_resource_repair_inflight"
    assert shared_repair_gate(c.state, c.log.read_all(), {**action, "recovery_case_id": "case-1"}) == "shared_repair_owned_by_another_case"
    for i in range(8):
        binding = {"resource_version": "db-v1", "target_commit": f"consumer-{i}"}
        c.writer.append(ZfEvent(type="run.manager.action.effect.pending", task_id=f"T{i}", payload={
            "recovery_case_id": f"case-{i}", "workflow_run_id": "run-1", "task_id": f"T{i}",
            "effect_id": f"probe-{i}", "expected_event_types": ["verify.passed"],
            "failure_event_types": ["verify.failed"], "effect_bindings": binding}))
        with sqlite3.connect(database) as connection:
            try:
                columns = "name, email" if i < 7 else "account_id, name, email"
                records = connection.execute(f"select {columns} from contacts").fetchall()
                assert records == [("客户", "test@example.invalid")]
                verdict = "verify.passed"
            except sqlite3.OperationalError:
                verdict = "verify.failed"
        c.writer.append(ZfEvent(type=verdict, actor=f"verify-{i}", task_id=f"T{i}",
            payload={"workflow_run_id": "run-1", **binding}))
    assert _settle_pending_action_effects(c.log.read_all(), c.writer) == 8
    assert _settle_pending_action_effects(c.log.read_all(), c.writer) == 0
    rows = build_recovery_case_projection(c.log.read_all())["cases"]
    assert sum(row["status"] == "resolved" for row in rows) == 7
    assert next(row for row in rows if row["recovery_case_id"] == "case-7")["status"] != "resolved"


@pytest.mark.parametrize("change", [
    {"workflow_run_id": "another"}, {"member_case_ids": ["unknown", "case-0"]},
    {"evidence_event_ids": ["missing"]}, {"resource_version": ""},
])
def test_association_cannot_guess_missing_identity_or_evidence(cases, change):
    with pytest.raises(ValueError):
        admit_association(state_dir=cases.state, writer=cases.writer, config=cases.config,
            actor="orchestrator", proposal={**cases.proposal, **change}, apply=True)


def test_inherited_semantic_failures_do_not_rewrite_operation_identity():
    old = [{**_operation("OLD", "verify", "settled", n), "semantic_verdict": "failed",
            "failure_class": "product", "call_result_admitted_event_id": f"old-{n}"} for n in (1, 2)]
    current = [{**_operation("NEW", "impl", "settled"), "semantic_verdict": "failed",
                "failure_class": "product", "call_result_admitted_event_id": "new-1"}]
    result = TaskPipelineReconciler().reconcile(policy={**_policy(), "max_rework_attempts": 2},
        tasks=[_task("NEW", priority=1)], operations=current, attempts=[], inherited_failures={"NEW": old})
    assert result["tasks"][0]["semantic_failure_count"] == 3
    assert result["tasks"][0]["stage"] == "blocked"
    assert old[0]["task_id"] == "OLD"


def test_admitted_problem_lineage_reaches_current_projection_without_reusing_old_passes(cases):
    from zf.runtime.task_pipeline_projection import build_task_pipeline_projection
    from zf.runtime.sidecar_refs import write_sidecar_json
    c = cases
    for task_id, generation, number in [("T1", "g1", 1), ("T1", "g1", 2), ("T0", "g2", 1)]:
        op = f"op-{task_id}-{number}"
        base = {"workflow_run_id": "run-1", "task_id": task_id, "operation_id": op,
                "operation_type": "task-stage", "request_hash": op, "task_pipeline_stage": "impl",
                "task_map_generation": generation, "operation_generation": number}
        control_ref = write_sidecar_json(c.state, f"artifacts/{op}.json", {"verdict": "failed", "failure_class": "product"},
            kind="control-result", schema_version="test.v1", created_by="impl", required=True)
        for kind, extra in [("workflow.operation.requested", {}),
                            ("workflow.call.result.admitted", {"semantic_verdict": "failed", "control_result_ref": control_ref}),
                            ("workflow.operation.settled", {})]:
            c.writer.append(ZfEvent(type=kind, origin="kernel", task_id=task_id, payload={**base, **extra}))
    c.writer.append(ZfEvent(type="run.manager.action.failed", origin="kernel", task_id="T0",
        payload={"workflow_run_id": "run-1", "task_map_generation": "g2", "recovery_case_id": "case-new"}))
    proposal = {"kind": "problem_continuation", "workflow_run_id": "run-1",
        "member_case_ids": ["case-1", "case-new"], "target_case_id": "case-new",
        "failure_operation_ids": ["op-T1-1", "op-T1-2"], "evidence_event_ids": c.proposal["evidence_event_ids"],
        "rationale": "批准的局部拆分延续同一 AC 的失败，旧失败不得通过换任务清零。"}
    admit_association(state_dir=c.state, writer=c.writer, config=c.config, actor="orchestrator", proposal=proposal, apply=True)
    c.writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-1",
        "task_map_generation": "g2", "generation_id": "g2", "task_ids": ["T0"]}))
    projection = build_task_pipeline_projection(events=c.log.read_all(), tasks=[_task("T0", priority=1)],
        attempts=[], state_dir=c.state, policy={**_policy(), "max_rework_attempts": 2})
    row = projection["tasks"][0]
    assert row["pipeline_stage"] == "blocked"
    assert row["semantic_failure_count"] == 3
