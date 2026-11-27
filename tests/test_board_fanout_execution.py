from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.runtime.run_scope import run_aliases
from zf.web.projections.board_fanout_execution import board_fanout_executions


def _event(kind, *, run="r", task="ROOT", **payload):
    return ZfEvent(type=kind, origin="kernel", task_id=task, correlation_id=run,
                   payload={"workflow_run_id": run, **payload})


def _invocation(run="r", generation="g", revision=1):
    return _event("workflow.invoke.accepted", run=run, workflow_generation=generation,
                  request_revision=revision, request_id=f"request-{run}")


def _start(fid, *, run="r", stage="plan", generation="g"):
    return _event("fanout.started", run=run, stage_id=stage, fanout_id=fid,
                  trigger_payload={"workflow_generation": generation})


def _worker(worker, fanout, *, run="r", stage="plan", attempt="1", synth=False, task="ROOT"):
    oid = f"{worker}-{fanout}"
    pin = f"{oid}-attempt-{attempt}"
    common = {"run": run, "operation_id": oid, "request_hash": "body", "task": task}
    return [
        _event("workflow.operation.requested", **common, parent_stage_id=stage),
        _event("workflow.operation.started", **common, role_instance=worker, active_attempt_id=pin,
               dispatch_id=pin, lease_id=pin),
        _event("fanout.synth.dispatched" if synth else "fanout.child.dispatched", **common, fanout_id=fanout,
               attempt_id=pin, dispatch_id=pin, lease_id="transport-lease", role_instance=worker,
               stage_id=stage, workflow_generation="g", request_revision=1),
    ]


def _view(events, tasks=None, bound=None):
    return board_fanout_executions(events, tasks or [Task(id="ROOT")], bound or {}, run_aliases(events))


def _names(view, task="ROOT"):
    return sorted(row["instance_id"] for row in view.get(task, {}).get("active_instances", []))


def test_root_through_parallel_readers_and_synth_uses_only_the_current_receipt():
    rows = [_invocation(), _start("f"), *_worker("product", "f"), *_worker("tech", "f")]
    assert _names(_view(rows)) == ["product", "tech"]
    rows.append(_event("workflow.operation.settled", operation_id="product-f"))
    old = _worker("tech", "f")[-1]
    rows.append(_event("fanout.child.completed", fanout_id="f", operation_id="tech-f",
                       attempt_id=old.payload["attempt_id"]))
    assert not _names(_view(rows))
    synth = _worker("critic", "f", synth=True)
    synth[-1].task_id = None  # The fanout identifies the owner of a synth dispatch.
    rows.extend(synth)
    result = _view(rows)
    assert result["ROOT"]["stage"] == "plan"
    assert result["ROOT"]["state"] == "running"
    assert result["ROOT"]["request_id"] == "request-r"
    assert _names(result) == ["critic"]
    assert not _names(_view(rows + [_event("run.paused")]))
    assert _view(rows + [_event("run.paused")])["ROOT"]["state"] == "paused"
    assert not _names(_view(rows + [_event("run.completed")]))
    assert not _names(_view(rows, [Task(id="ROOT", status="done")]))


def test_retries_superseded_fanouts_generations_and_conflicting_identities_are_excluded():
    rows = [_invocation(), _start("old"), *_worker("old-reader", "old"), _start("new")]
    first = _worker("reader", "new")
    rows.extend(first)
    # A retry changes the Operation attempt before the matching replacement is sent.
    second = _worker("reader", "new", attempt="2")
    rows.append(second[1])
    assert not _names(_view(rows))
    rows.append(second[2])
    rows.append(first[2])
    assert _view(rows)["ROOT"]["active_instances"][0]["attempt_id"] == "reader-new-attempt-2"
    assert not _names(_view(rows + [_event("fanout.cancelled", fanout_id="new")]))
    assert not _names(_view(rows + [_invocation(generation="next", revision=2)]))
    forged = _worker("forged", "new")
    forged[-1].origin = "worker"
    damaged = _worker("damaged", "new")
    damaged[-1].payload["request_hash"] = "different"
    stale = _worker("foreign-stage", "new", stage="other")
    assert _names(_view(rows + forged + damaged + stale)) == ["reader"]


def test_root_run_resolution_preserves_ambiguity_pending_requests_and_task_scope():
    rows = [_invocation(), _start("a"), *_worker("one", "a"),
            _invocation(run="r2"), _start("b", run="r2"), *_worker("two", "b", run="r2")]
    assert _view(rows) == {}
    assert _names(_view(rows, bound={"ROOT": "r2"})) == ["two"]
    pending = Task(id="ROOT", execution_binding=TaskExecutionBinding(request_id="pending"))
    assert _view(rows, [pending]) == {}
    pinned = Task(id="ROOT", active_dispatch_id="retired")
    assert not _names(_view(rows, [pinned], {"ROOT": "r"}))
    child = Task(id="CHILD", execution_binding=TaskExecutionBinding(workflow_run_id="r"))
    rows += _worker("other-task", "a", task=child.id)
    result = _view(rows, [Task(id="ROOT"), child], {"ROOT": "r"})
    assert _names(result, "ROOT") == ["one"]
    assert _names(result, child.id) == ["other-task"]
