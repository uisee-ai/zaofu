"""Candidate assembly cannot consume evidence produced after candidate.ready."""
from pathlib import Path
import json

import pytest

from tests.test_candidates import (
    _add_task, _approve, _config, _init_repo, _rebuilder, _record_task_ref,
    _state, _task_commit,
)
from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.candidate_integration import candidate_failure_envelope


@pytest.mark.parametrize("consumer", [False, True])
@pytest.mark.parametrize("local", [False, True])
def test_rebuild_defers_candidate_commands_only_to_a_wired_consumer(
    tmp_path: Path, consumer: bool, local: bool,
) -> None:
    _init_repo(tmp_path)
    state, _, log = _state(tmp_path)
    config = _config(state)
    config.workflow.candidate_quality_source = "task_contract_required"
    if consumer:
        config.workflow.stages = [WorkflowStageConfig(
            id="independent-verify", trigger="candidate.ready",
            topology="fanout_reader", roles=["review"],
            aggregate=FanoutAggregateConfig(
                child_success_event="verify.child.completed",
                success_event="test.passed",
            ),
        )]
    commit = _task_commit(
        tmp_path, branch="worker/TASK-1", file_name="feature.txt",
        content="implemented\n", message="feat: implement feature",
    )
    _record_task_ref(
        tmp_path, state, config, task_id="TASK-1", commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state, log, task_id="TASK-1")
    store = TaskStore(state / "kanban.json")
    task = store.get("TASK-1")
    task.contract.validation = {"commands": [
        {"id": "local", "command": 'test -f feature.txt && test -f "$ZF_CANDIDATE_QUALITY_CONTEXT"',
         "owner": "task_verify", "timeout_seconds": 90},
        {"id": "global", "command": "echo downstream-not-ready >&2; exit 23",
         "owner": "candidate_verify"},
    ]}
    if not local:
        task.contract.validation["commands"].pop(0)
    store.update(task.id, contract=task.contract)
    _approve(log, "TASK-1")
    result = _rebuilder(tmp_path, state, config, log).rebuild(
        "F-11111111", event_writer=EventWriter(log),
    )
    assert result is not None
    if consumer:
        assert result.status == "updated"
        quality = result.payload["quality"]
        assert quality["status"] == "passed"
        assert quality["gates_passed"] == (["task_contract:TASK-1:local"] if local else [])
        assert not any("global" in gate for gate in quality["gate_checks"])
        if not local:
            return  # No downstream command was run or reported as a passed gate.
        check = quality["gate_checks"]["task_contract:TASK-1:local"][0]
        assert check["timeout_seconds"] == 90
        descriptor = check["execution_context_ref"]
        context = hydrate_sidecar_ref(state, descriptor).payload
        assert context["evidence_kind"] == "candidate_integration"
        assert context["independent_verification"] is False
        assert context["task_id"] == "TASK-1"
        assert context["target_commit"] == result.payload["commit"]
        assert context["command_id"] == "local"
        assert json.loads((state / descriptor["ref"]).read_text()) == context
    else:
        assert result.status == "quality_failed"
        assert result.payload["quality"]["gate_source"] == "task_contract_missing_consumer"
        assert candidate_failure_envelope(result.payload, failed_children=[])["failure_class"] == "candidate_contract_failure"
