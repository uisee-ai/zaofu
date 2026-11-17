"""A returned submit STOP is terminal and a corrected choice can be resynthesized."""
import json
from types import SimpleNamespace

from tests.test_workflow_synthesis import _context, _result
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_preparation_view import preparation_view
from zf.runtime.workflow_prepare import prepare_request_revision
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis import enqueue_workflow_synthesis
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations
from tests.test_workflow_registered_controller import assembly  # noqa: F401


def test_returned_stop_fails_once_and_retry_does_not_reuse_conflicting_result(tmp_path, monkeypatch):
    config, state, writer, request = _context(tmp_path, kind="prd")
    queued = enqueue_workflow_synthesis(state_dir=state, project_root=tmp_path, config=config,
        writer=writer, request_id="req-synth", actor="test", backend="mock")
    calls = []

    class Agent:
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, reply=json.dumps(_result(request, "PrdFlow")))

    blocker = {"kind": "workflow_synthesis_compile_mismatch", "severity": "STOP",
               "message": "role closure missing plan-critic", "fix_it": "Correct requested_roles"}
    monkeypatch.setattr("zf.runtime.workflow_synthesis._build_synthesis_proposal",
                        lambda **kwargs: {"status": "STOP", "blockers": [blocker]})

    def consume():
        return consume_workflow_synthesis_operations(state_dir=state, project_root=tmp_path,
            config=config, writer=writer, agent=Agent())

    assert consume() == 1
    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation["status"] == "failed"
    assert consume() == 0
    current = load_workflow_request(state, "req-synth")
    view = preparation_view(current, operation, git_blockers=[])
    assert view["status"] == "preparation_failed" and view["can_retry"]
    assert "role closure missing plan-critic" in view["blockers"][0]["message"]
    failure = next(e for e in writer.event_log.read_all() if e.type == "workflow.synthesis.proposal.failed")
    assert failure.payload["blockers"] == [blocker]
    assert failure.payload["request_id"] == "req-synth"
    retried = prepare_request_revision(state_dir=state, project_root=tmp_path, config=config,
        writer=writer, request=current, actor="operator", payload={"retry_failed": True})
    assert retried["status"] == "synthesis_queued"
    assert retried["synthesis_operation_id"] != queued.operation_id
    assert consume() == 1
    assert len(calls) == 2  # The admitted but incompatible selection is not reused.
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())


def test_none_role_conflict_recompiles_after_corrected_synthesis_without_new_task(assembly):
    from tests.test_control_actions_workflow_request import _execute
    from zf.core.config.loader import load_config
    from zf.runtime.control_actions import ControlledActionService

    base, state, _, result, writer = assembly
    config = load_config(base)
    service = ControlledActionService(state, writer, config=config, project_root=base.parent,
                                     actor="operator", source="kanban-agent", surface="web")
    queued = _execute(service, "workflow-request", {"request_id": "none-entry", "kind": "auto",
        "objective": "Prepare the full registered PRD workflow with no Plan Critic",
        "source_ref": "prd.md", "target_root": ".", "backend": "mock", "synthesis_backend": "mock",
        "allow_missing_env": True, "acceptance": ["Hello World and unittest pass"]})
    request = load_workflow_request(state, queued["request_id"])
    reply = {**result, "request_id": request["request_id"], "request_revision": request["revision"],
             "requirement_ref": request["requirement_spec_ref"], "requirement_digest": request["requirement_spec_digest"],
             "requested_roles": ["plan-critic"]}
    reply["short_flow_spec"]["parameters"]["plan_review"] = {"mode": "none", "reason": "Owner authorized", "focus": []}
    calls = []

    class Agent:
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, reply=json.dumps(reply))

    def consume():
        return consume_workflow_synthesis_operations(state_dir=state, project_root=base.parent,
            config=config, writer=writer, agent=Agent())

    assert consume() == 1
    assert load_workflow_operation(writer.event_log, queued["synthesis_operation_id"])["status"] == "failed"
    assert consume() == 0
    reply["requested_roles"] = []
    current = load_workflow_request(state, request["request_id"])
    retried = prepare_request_revision(state_dir=state, project_root=base.parent, config=config,
        writer=writer, request=current, actor="operator", payload={"retry_failed": True})
    assert retried["status"] == "synthesis_queued"
    assert consume() == 1 and len(calls) == 2
    final = load_workflow_request(state, request["request_id"])
    assert final["status"] == "proposed", final
    assert final["revision"] == current["revision"]
    assert final["requirement_spec_digest"] == current["requirement_spec_digest"]
