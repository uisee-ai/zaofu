"""OA corrections retain their original selection and independent approval gate."""
import copy
import json
from types import SimpleNamespace

import pytest

from tests.test_prd_stage_teams import _real_scan_inputs
from tests.test_workflow_registered_controller import assembly  # noqa: F401
from zf.core.config.loader import load_config
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_synthesis import WorkflowSynthesisError, run_workflow_synthesis


class Author:
    def __init__(self, result, correction):
        self.result = result
        self.correction = correction
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.result if len(self.calls) == 1 else self.correction
        return SimpleNamespace(ok=True, reply=json.dumps(reply))


def _setup(assembly, response):
    base, state, request, result, writer = assembly
    _real_scan_inputs(assembly, "app/")
    author = Author(copy.deepcopy(result), response)
    kwargs = dict(state_dir=state, project_root=base.parent, config=load_config(base), writer=writer,
                  request_id=request["request_id"], actor="original-author", backend="mock", agent=author)
    return kwargs, author


def test_original_author_corrects_only_rejected_ref_and_replays_without_reinvocation(assembly):
    kwargs, author = _setup(assembly, {"replacement_refs": ["app/package.json"]})
    outcome = run_workflow_synthesis(**kwargs)
    initial, corrected = author.calls
    assert initial["thread_key"] == corrected["thread_key"]
    assert corrected["permission_profile"] == "read_only"
    for expected in ("app/", "tech-scan", "plan-task-entry", "regular file", "replacement_refs"):
        assert expected in corrected["message"]
    admitted = hydrate_sidecar_ref(kwargs["state_dir"], outcome.result["short_flow_spec_ref"]).payload
    expected = copy.deepcopy(author.result["short_flow_spec"]["parameters"])
    tech, = [member for member in expected["stage_teams"]["plan"]["members"] if member["role"] == "tech-scan"]
    tech["input_refs"][tech["input_refs"].index("app/")] = "app/package.json"
    assert admitted["parameters"] == expected
    assert run_workflow_synthesis(**kwargs).replayed
    assert len(author.calls) == 2
    log = kwargs["writer"].event_log
    assert len(log.query(type="workflow.synthesis.admitted")) == 1
    assert not log.query(type="workflow.request.approved")
    assert not log.query(type="workflow.invoke.requested")


@pytest.mark.parametrize("response", [
    {"replacement_refs": ["app/"]},
    {"replacement_refs": ["nonexistent-future-file"]},
    {"replacement_refs": ["requirement"]},
    {"replacement_refs": ["../unbound-file"]},
    {"replacement_refs": [], "stage_teams": {}},
])
def test_invalid_or_scope_changing_correction_cannot_admit_or_loop(assembly, response):
    kwargs, author = _setup(assembly, response)
    with pytest.raises(WorkflowSynthesisError):
        run_workflow_synthesis(**kwargs)
    log = kwargs["writer"].event_log
    assert len(author.calls) == 2
    assert len(log.query(type="workflow.synthesis.input_correction.returned")) == 1
    assert len(log.query(type="workflow.synthesis.failed")) == 1
    assert not log.query(type="workflow.synthesis.admitted")
    with pytest.raises(WorkflowSynthesisError, match="failed"):
        run_workflow_synthesis(**kwargs, resume_running=True)
    assert len(author.calls) == 2


@pytest.mark.parametrize("crash_event, expected_provider_calls", [
    ("workflow.synthesis.input_correction.requested", 2),
    ("workflow.synthesis.input_correction.returned", 2),
])
def test_restart_consumes_original_feedback_and_reuses_durable_reply(assembly, monkeypatch, crash_event,
                                                                     expected_provider_calls):
    from zf.core.events.writer import EventWriter

    kwargs, author = _setup(assembly, {"replacement_refs": ["app/package.json"]})
    writer = kwargs["writer"]
    append = writer.append

    def crash(event, **kw):
        receipt = append(event, **kw)
        if event.type == crash_event:
            raise KeyboardInterrupt
        return receipt

    with monkeypatch.context() as m:
        m.setattr(writer, "append", crash)
        with pytest.raises(KeyboardInterrupt):
            run_workflow_synthesis(**kwargs)
    outcome = run_workflow_synthesis(**{**kwargs, "writer": EventWriter(writer.event_log)}, resume_running=True)
    assert outcome.result["selected_flow_family"] == "PrdFlow"
    assert len(author.calls) == expected_provider_calls
    assert len(writer.event_log.query(type="workflow.synthesis.admitted")) == 1


def test_repeated_crash_before_provider_does_not_renew_correction_budget(assembly, monkeypatch):
    from zf.runtime.workflow_synthesis import WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT

    kwargs, author = _setup(assembly, {"replacement_refs": ["app/package.json"]})
    writer = kwargs["writer"]
    append = writer.append

    def crash(event, **kw):
        receipt = append(event, **kw)
        if event.type == "workflow.synthesis.input_correction.requested":
            raise KeyboardInterrupt
        return receipt

    with monkeypatch.context() as m:
        m.setattr(writer, "append", crash)
        for attempt in range(WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT):
            with pytest.raises(KeyboardInterrupt):
                run_workflow_synthesis(**kwargs, resume_running=bool(attempt))
    with pytest.raises(WorkflowSynthesisError, match="exhausted"):
        run_workflow_synthesis(**kwargs, resume_running=True)
    assert len(author.calls) == 1
    assert not writer.event_log.query(type="workflow.synthesis.admitted")


def test_queued_correction_builds_proposal_for_owner_without_starting_workflow(assembly):
    from tests.test_control_actions_workflow_request import _execute
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.workflow_operation import load_workflow_operation
    from zf.runtime.workflow_requests import load_workflow_request
    from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations

    kwargs, author = _setup(assembly, {"replacement_refs": ["app/package.json"]})
    state, writer, config, root = (kwargs[k] for k in ("state_dir", "writer", "config", "project_root"))
    service = ControlledActionService(state, writer, config=config, project_root=root, actor="operator",
                                      source="kanban-agent", surface="web")
    submission = _execute(service, "workflow-request", {
        "request_id": "correct-source", "kind": "auto", "objective": "Prepare the registered PRD workflow",
        "source_ref": "prd.md", "target_root": ".", "backend": "mock", "synthesis_backend": "mock",
        "allow_missing_env": True, "acceptance": ["Hello World and unittest pass"],
    })
    request = load_workflow_request(state, submission["request_id"])
    author.result.update(request_id=request["request_id"], request_revision=request["revision"],
                         requirement_ref=request["requirement_spec_ref"], requirement_digest=request["requirement_spec_digest"])
    params = dict(state_dir=state, writer=writer, config=config, project_root=root, agent=author)
    assert consume_workflow_synthesis_operations(**params) == 1
    current = load_workflow_request(state, submission["request_id"])
    assert current["status"] == "proposed", current
    op_id = submission["synthesis_operation_id"]
    assert load_workflow_operation(writer.event_log, op_id)["status"] == "settled"
    assert current["synthesis_operation_id"] == op_id
    assert consume_workflow_synthesis_operations(**params) == 0
    assert len(author.calls) == 2
    assert not writer.event_log.query(type="workflow.request.approved")
    assert not writer.event_log.query(type="workflow.invoke.requested")


@pytest.mark.parametrize("replacement, allowed", [("app/package.json", True), ("prd.md", False)])
def test_declared_replacements_are_scoped_to_the_rejected_input(assembly, replacement, allowed):
    kwargs, author = _setup(assembly, {"replacement_refs": [replacement]})
    members = author.result["short_flow_spec"]["parameters"]["stage_teams"]["plan"]["members"]
    tech, = [m for m in members if m["role"] == "tech-scan"]
    tech["input_alternatives"] = {"app/": ["app/package.json"], "package.json": ["prd.md"]}
    if not allowed:
        with pytest.raises(WorkflowSynthesisError, match="declared input_alternatives"):
            run_workflow_synthesis(**kwargs)
        return
    outcome = run_workflow_synthesis(**kwargs)
    spec = hydrate_sidecar_ref(kwargs["state_dir"], outcome.result["short_flow_spec_ref"]).payload
    tech, = [m for m in spec["parameters"]["stage_teams"]["plan"]["members"] if m["role"] == "tech-scan"]
    assert tech["input_alternatives"] == {"package.json": ["prd.md"]}
    assert "app/package.json" in tech["input_refs"]


@pytest.mark.parametrize("invalid", ["not-yet-created.md", "requirement", "docs/prd/TODO.md#unavailable"])
def test_declared_fallback_must_be_a_concrete_authorized_file(assembly, invalid):
    kwargs, author = _setup(assembly, {"replacement_refs": ["app/package.json"]})
    product = author.result["short_flow_spec"]["parameters"]["stage_teams"]["plan"]["members"][0]
    product["input_alternatives"] = {product["input_refs"][0]: [invalid]}
    with pytest.raises(WorkflowSynthesisError, match="input_alternatives"):
        run_workflow_synthesis(**kwargs)
    assert len(author.calls) == 1
    assert not kwargs["writer"].event_log.query(type="workflow.synthesis.admitted")


def test_author_can_report_an_unresolved_source_without_fabricating_a_replacement(assembly):
    kwargs, author = _setup(assembly, {"unresolved_reason": "No independent technical evidence is available."})
    with pytest.raises(WorkflowSynthesisError, match="plan-task-entry.*No independent technical evidence"):
        run_workflow_synthesis(**kwargs)
    assert len(author.calls) == 2
    assert not kwargs["writer"].event_log.query(type="workflow.synthesis.admitted")
