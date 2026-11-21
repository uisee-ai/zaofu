from zf.runtime.task_contract_snapshot import effective_contract_revision
from types import SimpleNamespace
import pytest
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_terminal import (
    reconcile_task_pipeline_terminals,
    reconcile_task_pipeline_freeze,
)
from tests.test_candidate_incremental import _setup, _git, _integrate


def mixed_generation_freeze_case(tmp_path, change="none"):
    state, config, log, writer, base, _ = _setup(tmp_path)
    store = TaskStore(state / "kanban.json")
    task = Task(id="TASK-B", title="delta", key="F-11111111:TASK-B")
    task.contract.contract_revision = "contract-r1"
    task.contract.scope = ["second.txt"]
    task.contract.validation = {
        "commands": [
            {
                "id": "smoke-B",
                "command": "test -f second.txt",
                "tier": "runtime",
                "rolling_smoke": True,
            }
        ]
    }
    store.add(task)
    writer.append(
        ZfEvent(
            type="task.created", task_id=task.id, payload={"feature_id": "F-11111111"}
        )
    )
    _git(tmp_path, "checkout", "-q", "-b", "worker/task-b", base)
    (tmp_path / "second.txt").write_text("B")
    _git(tmp_path, "add", "second.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: B")
    source = _git(tmp_path, "rev-parse", "HEAD")
    ref = TaskRefManager(
        state_dir=state, project_root=tmp_path, config=config
    ).process_dev_build_done(
        ZfEvent(
            type="dev.build.done",
            actor="impl-B",
            task_id=task.id,
            payload={
                "source_commit": source,
                "source_branch": "worker/task-b",
                "base_git_head": base,
                "feature_id": "F-11111111",
                "files_touched": ["second.txt"],
            },
        )
    )
    assert ref.status == "updated"
    for n, tid in [(1, "TASK-A"), (2, "TASK-B")]:
        writer.append(
            ZfEvent(
                type="task.pipeline.generation.admitted",
                origin="kernel",
                correlation_id="run-1",
                payload={
                    "schema_version": "task-pipeline-generation.v1",
                    "generation_id": f"g{n}",
                    "workflow_run_id": "run-1",
                    "task_map_generation": f"map-g{n}",
                    "task_ids": [tid],
                    "dispatch_base_commit": base,
                    "flow_kind": "issue",
                    "pdd_id": "F-11111111",
                    "task_map_ref": f"artifacts/map-g{n}.json",
                },
            )
        )
    result = CandidateRebuilder(
        state_dir=state, project_root=tmp_path, config=config, event_log=log
    ).integrate_task_pipeline_task(
        task_id="TASK-B",
        workflow_run_id="run-1",
        task_map_generation="map-g2",
        operation_generation=1,
        pipeline_key="pipeline-B",
        dispatch_base_commit=base,
        contract_revision=effective_contract_revision(store.get(task.id)),
        event_writer=writer,
        causation_id="g2",
    )
    assert result.status == "integrated"
    runtime = SimpleNamespace(
        state_dir=state,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=writer,
        task_store=store,
    )
    contexts = task_pipeline_generation_contexts(log.read_all())
    reconcile_task_pipeline_terminals(runtime, generation_contexts=contexts)
    assert not reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    assert _integrate(tmp_path, state, config, log, writer, base).status == "integrated"
    if change == "unarchived_old":
        store.update("TASK-A", status="done")
    else:
        reconcile_task_pipeline_terminals(runtime, generation_contexts=contexts)
    if change == "missing_receipt":
        e = next(
            e
            for e in reversed(log.read_all())
            if e.type == "integration.queue.integrated" and e.task_id == "TASK-A"
        )
        (state / e.payload["receipt_ref"]["ref"]).unlink()
    elif change == "active_old":
        writer.append(
            ZfEvent(
                type="workflow.operation.requested",
                task_id="TASK-A",
                payload={
                    "workflow_run_id": "run-1",
                    "operation_id": "active-old",
                    "operation_type": "task_pipeline_impl",
                    "task_id": "TASK-A",
                    "request_hash": "hash",
                    "task_map_generation": "map-g1",
                },
            )
        )
    elif change == "wrong_generation":
        contexts["TASK-A"]["task_map_generation"] = "foreign"
    elif change in {"advanced_head", "divergent_head"}:
        branch = "candidate/F-11111111"
        _git(
            tmp_path,
            "checkout",
            "-q",
            "-b",
            "unreceipted",
            base if change == "divergent_head" else branch,
        )
        (tmp_path / "unreceipted.txt").write_text("unreceipted")
        _git(tmp_path, "add", "unreceipted.txt")
        _git(tmp_path, "commit", "-q", "-m", "test: unreceipted")
        _git(
            tmp_path,
            "update-ref",
            "refs/heads/" + branch,
            _git(tmp_path, "rev-parse", "HEAD"),
        )
    result = reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    if change != "none":
        assert result == []
        assert not any(e.type == "candidate.ready" for e in log.read_all())
        return
    assert len(result) == 1
    ready = [e for e in log.read_all() if e.type == "candidate.ready"]
    assert len(ready) == 1
    p = ready[0].payload
    assert p["task_ids"] == ["TASK-A", "TASK-B"]
    assert p["task_map_generation"] == "map-g2"
    assert len(p["task_integration_receipt_refs"]) == 2
    assert p["candidate_head"] == _git(tmp_path, "rev-parse", "candidate/F-11111111")
    assert not reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    return runtime


@pytest.mark.parametrize("change", ["none", "missing_receipt", "unarchived_old", "active_old",
                                   "wrong_generation", "advanced_head", "divergent_head"])
def test_old_generation_last_completion_freezes_full_run_receipts(tmp_path, change):
    mixed_generation_freeze_case(tmp_path, change)
