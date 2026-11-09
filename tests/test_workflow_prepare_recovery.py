"""Repair and retry use one Task/Request and preserve the failed attempt."""
import json
import shutil
from types import SimpleNamespace

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
    assert consume() == 1  # Supersede the settled operation from the prior revision.
    assert consume() == 1
    assert agent.calls == 2
    assert load_workflow_request(state, request["request_id"])["status"] == "proposed"
