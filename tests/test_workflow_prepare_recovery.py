"""Repair and retry use one Task/Request and preserve the failed attempt."""
import json
from concurrent.futures import ThreadPoolExecutor
import shutil
from types import SimpleNamespace

import pytest

from tests.test_control_actions_workflow_request import _execute, _service
from tests.test_workflow_prepare import _create_payload
from tests.test_workflow_registered_controller import assembly  # noqa: F401
from zf.core.config.loader import load_config
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.project_git_baseline import apply_git_baseline, preview_git_baseline
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_prepare import prepare_request_revision
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations


def test_registered_project_blocks_before_provider_then_same_task_queues(tmp_path):
    service, log = _service(tmp_path)
    path = tmp_path / "zf.yaml"
    path.write_text(path.read_text().replace("metadata: {name: issue-demo}",
        "metadata: {name: issue-demo, zaofu: {catalog: {assembly: project-bound-v1}}}"))
    service.config = load_config(path)
    created = _execute(service, "create-task", _create_payload())
    preparation = created["workflow_preparation"]
    assert preparation["status"] == "preparation_blocked", preparation
    assert preparation["blockers"][0]["kind"] == "git_repository_missing"
    assert not any(e.type == "workflow.synthesis.queued" for e in log.read_all())
    manifest = preview_git_baseline(tmp_path, service.state_dir, paths=["zf.yaml"], exclude_paths=["dataset"])
    assert _execute(service, "project-git-baseline", {"manifest": manifest})["ok"]
    request = load_workflow_request(service.state_dir, preparation["request_id"])
    queued = prepare_request_revision(state_dir=service.state_dir, project_root=tmp_path,
        config=service.config, writer=service.writer, request=request, actor="operator",
        payload={"backend": "mock", "retry_failed": True})
    assert queued["status"] == "synthesis_queued"
    assert queued["request_id"] == preparation["request_id"]


def test_failed_registered_assembly_recovers_without_second_provider_call(assembly):
    base, state, _, result, writer = assembly
    # Model an existing populated project before its first Git baseline.
    shutil.rmtree(base.parent / ".git")
    config = load_config(base)
    service = ControlledActionService(state, writer, config=config, project_root=base.parent,
                                     actor="operator", source="kanban-agent", surface="web")
    queued = _execute(service, "workflow-request", {
        "request_id": "recovery-entry", "kind": "auto", "objective": "Deliver the full registered PRD workflow",
        "source_ref": "prd.md", "target_root": ".", "backend": "mock", "synthesis_backend": "mock",
        "allow_missing_env": True, "acceptance": ["Hello World and unittest pass"],
    })
    request = load_workflow_request(state, queued["request_id"])
    reply = {**result, "request_id": request["request_id"], "request_revision": request["revision"],
             "requirement_ref": request["requirement_spec_ref"], "requirement_digest": request["requirement_spec_digest"]}

    class Agent:
        calls = 0

        def run_turn(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(ok=True, reply=json.dumps(reply))

    agent = Agent()

    def consume():
        return consume_workflow_synthesis_operations(state_dir=state, project_root=base.parent,
            config=config, writer=writer, agent=agent, limit=1)

    assert consume() == 1
    old_id = queued["synthesis_operation_id"]
    assert load_workflow_operation(writer.event_log, old_id)["status"] == "failed"
    assert consume() == 0  # A deterministic prerequisite is not retried blindly.
    current = load_workflow_request(state, request["request_id"])

    def prepare(retry=False):
        return prepare_request_revision(state_dir=state, project_root=base.parent,
            config=config, writer=writer, request=load_workflow_request(state, request["request_id"]),
            actor="operator", payload={"retry_failed": retry})

    assert prepare()["status"] == "preparation_failed"
    baseline = preview_git_baseline(base.parent, state, paths=["zf.yaml", "prd.md"], exclude_paths=["dataset"])
    apply_git_baseline(base.parent, state, baseline)
    retried = prepare(True)
    assert retried["ok"], retried
    assert retried["synthesis_operation_id"] != old_id
    assert prepare(True)["synthesis_operation_id"] == retried["synthesis_operation_id"]
    assert consume() == 1
    assert agent.calls == 1
    final = load_workflow_request(state, request["request_id"])
    assert final["status"] == "proposed", final
    assert final["revision"] == current["revision"]
    assert final["requirement_spec_digest"] == current["requirement_spec_digest"]
    assert load_workflow_operation(writer.event_log, old_id)["status"] == "failed"
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())

    # A genuine authorization/requirement change must leave recovery context behind.
    from zf.runtime.workflow_requests import revise_workflow_request
    from pathlib import Path
    revised = revise_workflow_request(state, Path(final["workflow_input_manifest_ref"]),
        actor="operator", expected_revision=final["revision"],
        objective="Deliver the product with newly delegated execution approval", confirm=True, writer=writer)
    reply.update(request_revision=revised["revision"], requirement_ref=revised["requirement_spec_ref"],
                 requirement_digest=revised["requirement_spec_digest"])
    next_attempt = prepare()
    assert next_attempt["status"] == "synthesis_queued"
    # Superseding old work does not spend the new revision's single dispatch slot.
    assert consume() == 2
    assert consume() == 0
    assert agent.calls == 2
    assert load_workflow_request(state, request["request_id"])["status"] == "proposed"


def test_cli_settled_proposal_failure_replays_same_admission_without_provider(assembly, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from zf.cli.main import build_parser
    from zf.core.events.model import ZfEvent
    from zf.web.projections.preparation_events import read_preparation_events
    from zf.web.server import create_app
    from zf.runtime.workflow_synthesis_consumer import WorkflowSynthesisConsumerBusy
    from zf.cli.workflow_prepare import request_preview
    from zf.runtime import workflow_synthesis as synthesis

    base, state, _, declared, writer = assembly
    monkeypatch.chdir(base.parent)
    monkeypatch.delenv("ZF_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ZF_STATE_DIR", raising=False)
    config = load_config(base)
    service = ControlledActionService(
        state,
        writer,
        config=config,
        project_root=base.parent,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )
    queued = _execute(
        service,
        "workflow-request",
        {
            "request_id": "replay-local",
            "kind": "auto",
            "objective": "Prepare the registered PRD workflow",
            "source_ref": "prd.md",
            "target_root": ".",
            "backend": "mock",
            "synthesis_backend": "mock",
            "allow_missing_env": True,
            "acceptance": ["Hello World and unittest pass"],
        },
    )
    rid, op = queued["request_id"], queued["synthesis_operation_id"]
    request = load_workflow_request(state, rid)
    result = {
        **declared,
        "request_id": rid,
        "request_revision": request["revision"],
        "requirement_ref": request["requirement_spec_ref"],
        "requirement_digest": request["requirement_spec_digest"],
    }

    class Agent:
        def run_turn(self, **kwargs):
            return SimpleNamespace(ok=True, reply=json.dumps(result))

    build = synthesis._build_synthesis_proposal

    def materializer_unavailable(**kwargs):
        raise ValueError("local assembly unavailable")

    monkeypatch.setattr(synthesis, "_build_synthesis_proposal", materializer_unavailable)
    assert (
        consume_workflow_synthesis_operations(
            state_dir=state,
            project_root=base.parent,
            config=config,
            writer=writer,
            agent=Agent(),
            request_id=rid,
        )
        == 1
    )
    receipt = load_workflow_operation(writer.event_log, op)
    assert receipt["status"] == "settled" and receipt["admitted_call_result_ref"]
    failure = next(
        e for e in reversed(writer.event_log.read_all()) if e.type == "workflow.synthesis.proposal.failed"
    )
    writer.append(ZfEvent(type=failure.type, actor=failure.actor,
                          payload={**failure.payload, "attempt": "unknown"}))
    # A Web tick discovers legacy failures through its normal event index.
    assert consume_workflow_synthesis_operations(
        state_dir=state, project_root=base.parent, config=config, writer=writer, request_id=rid,
        read_operation_events=lambda: read_preparation_events(state, config, writer.event_log),
    ) == 0
    legacy = writer.append(ZfEvent(type=failure.type, actor=failure.actor, payload=failure.payload))
    writer.append(
        ZfEvent(type=failure.type, actor=failure.actor, payload={**legacy.payload, "request_hash": "stale"})
    )
    writer.append(ZfEvent(type=failure.type, actor="worker-1", origin="worker", payload=legacy.payload))
    view = request_preview(state, rid, project_root=base.parent)
    assert not view["ok"] and view["preparation"]["phase"] == "proposal_materialization"
    assert view["preparation"]["blockers"][0]["message"] == "local assembly unavailable"
    assert view["preparation"]["source_event_id"] == legacy.id
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    client = TestClient(create_app(state, config=config, project_root=base.parent))
    detail = client.get(f"/api/projects/default/workflow-requests/{rid}")
    listing = client.get("/api/projects/default/workflow-requests")
    assert detail.status_code == listing.status_code == 200
    listed = next(row for row in listing.json()["items"] if row["request_id"] == rid)
    assert detail.json()["preparation"] == listed["preparation"] == view["preparation"]
    parser = build_parser()

    def prepare(*flags):
        args = parser.parse_args(
            ["workflow", "prepare", "--state-dir", str(state), "--request", rid, "--format", "json", *flags]
        )
        code = args.func(args)
        return code, json.loads(capsys.readouterr().out)

    code, old = prepare()
    assert code == 1 and old["status"] == "preparation_failed" and old["attempt"] == 1

    def overlapping_build(**kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            other = pool.submit(
                consume_workflow_synthesis_operations,
                state_dir=state,
                project_root=base.parent,
                config=config,
                writer=writer,
                request_id=rid,
                replay_operation_id=op,
            )
            with pytest.raises(WorkflowSynthesisConsumerBusy):
                other.result(timeout=5)
        return build(**kwargs)

    monkeypatch.setattr(synthesis, "_build_synthesis_proposal", overlapping_build)

    def denied(*args, **kwargs):
        raise AssertionError("local proposal retry attempted a new model synthesis")

    monkeypatch.setattr(synthesis, "run_workflow_synthesis", denied)
    before = writer.event_log.read_all()
    code, new = prepare("--retry-failed")
    assert code == 0 and new["status"] == "proposed", new
    assert new["operation"]["operation_id"] == op
    assert new["operation"]["admitted_call_result_ref"] == receipt["admitted_call_result_ref"]
    assert prepare("--retry-failed")[1]["status"] == "proposed"
    emitted = [e.type for e in writer.event_log.read_all()[len(before) :]]
    assert emitted.count("workflow.request.proposed") == 1
    assert not set(emitted) & {
        "workflow.operation.requested",
        "workflow.synthesis.requested",
        "workflow.request.approved",
        "workflow.submit.accepted",
        "workflow.invoke.requested",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": None},
        {"attempt": "1"},
        {"attempt": True},
        {"retry_limit": None},
        {"retry_limit": []},
        {"reason": None},
        {"reason": {}},
        {"requires_resynthesis": None},
    ],
)
def test_legacy_proposal_failure_with_incomplete_receipt_fails_closed(changes):
    from zf.core.events.model import ZfEvent
    from zf.runtime.workflow_preparation_view import preparation_view, proposal_failure

    request = {
        "status": "ready",
        "request_id": "r",
        "revision": 1,
        "synthesis_operation_id": "op",
        "synthesis_operation_revision": 1,
    }
    operation = {
        "operation_id": "op",
        "request_hash": "h",
        "status": "settled",
        "admitted_call_result_ref": {"ref": "a"},
    }
    body = {
        "phase": "proposal_materialization",
        "request_id": "r",
        "operation_id": "op",
        "request_hash": "h",
        "reason": "assembly failed",
        "attempt": 1,
        "retry_limit": 3,
        "requires_resynthesis": False,
    }
    body.update(changes)
    event = ZfEvent(
        type="workflow.synthesis.proposal.failed",
        actor="workflow-synthesis-consumer",
        payload={key: value for key, value in body.items() if value is not None},
    )
    view = preparation_view(request, operation, failure=proposal_failure([event], request, operation))
    assert view["status"] == "preparation_failed" and view["can_retry"] is False
    assert isinstance(view["blockers"][0]["message"], str)
    if "reason" in changes:
        assert "unavailable" in view["blockers"][0]["message"]
