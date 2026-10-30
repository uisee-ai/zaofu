"""A registered controller must traverse the real synthesis/approval boundary."""
import copy
import json
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.test_workflow_proposal import _config, _request, _synthesis_result
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService, WorkflowConfigApplyError
from zf.runtime.workflow_proposal import (
    build_workflow_proposal, materialize_synthesized_workflow_candidate, WorkflowProposalError,
)
from zf.runtime.workflow_registered_controller import controller_catalog, selected_controller
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis import run_workflow_synthesis, WorkflowSynthesisError


CONTROLLER = "zaofu-prd-v5-production-codex"


@pytest.fixture
def assembly(tmp_path):
    base = tmp_path / "zf.yaml"
    _config(base)
    base.write_text(base.read_text().replace("state_dir: .zf", "state_dir: .state-demo"))
    for args in (("init", "-b", "main"), ("-c", "user.name=Test", "-c", "user.email=test@example.test",
                                          "commit", "--allow-empty", "-m", "chore: initialize")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "prd.md").write_text("# Hello\nUse Python standard library and unittest.\n")
    state = tmp_path / ".state-demo"
    request = _request(state, requirement_fields={"kind": "prd", "source_ref": "prd.md", "target_root": "."})
    entry = controller_catalog()[CONTROLLER]
    result = _synthesis_result(request)
    result["selected_flow_family"] = result["short_flow_spec"]["flow_family"] = "PrdFlow"
    result["short_flow_spec"]["parameters"] = {
        "controller_id": CONTROLLER, "controller_digest": entry["digest"],
        "verification_commands": ["python -m unittest discover -s tests -v"],
    }
    result["requested_profiles"] = ["bounded-direct-v1"]
    result["completion_profile"] = entry["completion_profile"]
    return base, state, request, result, EventWriter(EventLog(state / "events.jsonl"))


def _admit(assembly, result=None):
    base, state, request, original_result, writer = assembly
    outcome = run_workflow_synthesis(
        state_dir=state, project_root=base.parent, config=load_config(base), writer=writer,
        request_id=request["request_id"], actor="test", candidate_result=result or original_result,
    )
    request = load_workflow_request(state, request["request_id"])
    return outcome, request


def _candidate(assembly):
    base, state, _, _, _ = assembly
    outcome, request = _admit(assembly)
    candidate = materialize_synthesized_workflow_candidate(
        state, request=request, base_config_path=base,
        synthesis_result_ref=outcome.result_ref, flow_kind="prd",
    )
    return outcome, request, candidate


def test_v5_selection_preserves_policy_and_applies_only_exact_approved_candidate(assembly):
    base, state, _, result, writer = assembly
    original = base.read_bytes()
    outcome, request, candidate = _candidate(assembly)
    config = load_config(candidate)
    assert config.project.name == "demo"
    assert config.project.state_dir == ".state-demo"
    assert config.session == load_config(base).session
    assert config.runtime.git.candidate_base_ref == "main"
    assert config.runtime.git.ship_target_branch == "main"
    assert config.runtime.git.auto_ship_on_judge_passed is False
    assert config.runtime.workdirs.provision_paths == []
    assert config.workflow.orchestration.normal_frontier_owner == "wrc"
    assert config.workflow.execution_profiles["bounded-direct-v1"].limits.timeout_seconds == 600
    assert config.workflow.flow_metadata["task_pipeline"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["task_pipeline"]["pools"]["impl"]["capacity"] == 8
    assert config.quality_gates["static"].required_checks == result["short_flow_spec"]["parameters"]["verification_commands"]
    docs = list(yaml.safe_load_all(candidate.read_text()))
    assert next(d for d in docs if d["kind"] == "PrdFlow")["spec"]["prdRef"] == "prd.md"
    assert all("profile_sources" not in d["spec"] for d in docs)
    proposal, ref = build_workflow_proposal(
        state, request=request, base_config_path=base, candidate_config_path=candidate,
        synthesis_result_ref=outcome.result_ref, flow_kind="prd", writer=writer,
        preflight={"status": "GO", "blockers": []},
    )
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    assert proposal["change_mode"] == "config_change"
    assert base.read_bytes() == original
    assert not any(e.type in {"workflow.config.change.applied", "workflow.invoke.requested"}
                   for e in writer.event_log.read_all())
    payload = {"proposal_ref": ref, "proposal_digest": proposal["proposal_digest"],
               "validation_result_ref": proposal["validation_result_ref"]}
    service = WorkflowConfigApplyService(state_dir=state, project_root=base.parent)
    with pytest.raises(WorkflowConfigApplyError, match="owner approval"):
        service.apply(payload, actor="test", source_event_id="test")
    assert base.read_bytes() == original
    applied = service.apply({**payload, "approval_ref": "test-exact-proposal-approval"},
                            actor="test", source_event_id="test")
    assert applied.status == "applied"
    assert load_config(base).workflow.orchestration.normal_frontier_owner == "wrc"
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())


@pytest.mark.parametrize("change,error", [
    ({"controller_id": "../../arbitrary.yaml"}, "identity/digest"),
    ({"controller_digest": "0" * 64}, "identity/digest"),
    ({"lanes": 1}, "preserves"),
    ({"verification_commands": []}, "verification_commands"),
])
def test_selection_rejects_unknown_stale_or_unbounded_inputs(assembly, change, error):
    result = copy.deepcopy(assembly[3])
    result["short_flow_spec"]["parameters"].update(change)
    with pytest.raises(WorkflowSynthesisError, match=error):
        _admit(assembly, result)


def test_profile_is_not_globally_enabled_by_catalog_presence(assembly):
    result = copy.deepcopy(assembly[3])
    result["short_flow_spec"]["parameters"] = {"lanes": 1}
    with pytest.raises(WorkflowSynthesisError, match="unknown profile"):
        _admit(assembly, result)


def test_candidate_cannot_weaken_registered_controller(assembly):
    base, state, _, _, writer = assembly
    outcome, request, candidate = _candidate(assembly)
    candidate.write_text(candidate.read_text().replace("normal_frontier_owner: wrc", "normal_frontier_owner: kernel"))
    with pytest.raises(WorkflowProposalError, match="controller candidate digest collision"):
        build_workflow_proposal(state, request=request, base_config_path=base,
                                candidate_config_path=candidate, synthesis_result_ref=outcome.result_ref,
                                flow_kind="prd", writer=writer)


def test_template_profile_change_invalidates_digest(monkeypatch):
    import zf.runtime.workflow_registered_controller as module
    old = controller_catalog()
    documents = module._documents

    def changed(path):
        docs = documents(path)
        profile = next(d for d in docs if d["kind"] == "ConfigProfile")
        profile["spec"]["workflow"]["execution_profiles"]["bounded-direct-v1"]["limits"]["timeout_seconds"] += 1
        return docs

    monkeypatch.setattr(module, "_documents", changed)
    new = controller_catalog()
    assert old[CONTROLLER]["digest"] != new[CONTROLLER]["digest"]
    with pytest.raises(ValueError, match="stale"):
        selected_controller({"controller_id": CONTROLLER, "controller_digest": old[CONTROLLER]["digest"],
                             "verification_commands": ["python -m unittest"]}, family="PrdFlow", catalog=new)


def test_assembly_rejects_source_outside_project(assembly):
    from zf.runtime.workflow_registered_controller import assemble_controller
    with pytest.raises(ValueError, match="inside the project"):
        assemble_controller(base_path=assembly[0], short_spec=assembly[3]["short_flow_spec"],
                            requirement={"source_ref": "../external-prd.md", "target_root": "."})


def test_existing_project_skills_do_not_collide_with_missing_skill_package(assembly):
    from zf.core.skills import validate_skill_sources
    base = assembly[0]
    name = "zf-yoke-dev-worker-role-context"
    source = Path(__file__).parents[1] / "skills" / name
    local = base.parent / "skills" / name
    shutil.copytree(source, local)
    before = (local / "SKILL.md").read_bytes()
    _, _, candidate = _candidate(assembly)
    config = load_config(candidate)
    assert validate_skill_sources(config=config, project_root=base.parent) == []
    package = next(s for s in config.skill_sources if s.name == "registered-controller-skills")
    assert not (Path(package.path) / name).exists()
    assert (local / "SKILL.md").read_bytes() == before


def test_approved_config_rejects_tampered_skill_package(assembly):
    base, state, _, _, writer = assembly
    outcome, request, candidate = _candidate(assembly)
    config = load_config(candidate)
    package = next(s for s in config.skill_sources if s.name == "registered-controller-skills")
    proposal, ref = build_workflow_proposal(state, request=request, base_config_path=base,
        candidate_config_path=candidate, synthesis_result_ref=outcome.result_ref, flow_kind="prd", writer=writer,
        preflight={"status": "GO", "blockers": []})
    skill = next(Path(package.path).glob("*/SKILL.md"))
    skill.write_text(skill.read_text() + "\nChanged after approval.\n")
    with pytest.raises(WorkflowConfigApplyError, match="skill package content changed"):
        WorkflowConfigApplyService(state_dir=state, project_root=base.parent).apply({
            "proposal_ref": ref, "proposal_digest": proposal["proposal_digest"],
            "validation_result_ref": proposal["validation_result_ref"], "approval_ref": "test-approved",
        })


@pytest.mark.mock_e2e
def test_controlled_request_consumes_v5_selection_into_config_preview(assembly):
    from tests.test_control_actions_workflow_request import _execute
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations
    from zf.runtime.workflow_proposal import load_workflow_proposal

    base, state, _, result, writer = assembly
    service = ControlledActionService(state, writer, config=load_config(base), project_root=base.parent,
                                      actor="operator", source="kanban-agent", surface="web")
    before = base.read_bytes()
    queued = _execute(service, "workflow-request", {
        "request_id": "v5-entry", "kind": "auto", "objective": "Deliver Hello World with full V5",
        "source_ref": "prd.md", "target_root": ".", "backend": "mock", "synthesis_backend": "mock",
        "allow_missing_env": True, "acceptance": ["Hello World and unittest pass"],
    })
    assert queued["ok"], queued
    request = load_workflow_request(state, queued["request_id"])
    reply = {**result, "request_id": request["request_id"], "request_revision": request["revision"],
             "requirement_ref": request["requirement_spec_ref"], "requirement_digest": request["requirement_spec_digest"]}

    class Agent:
        def run_turn(self, **kwargs):
            return SimpleNamespace(ok=True, reply=json.dumps(reply))

    assert consume_workflow_synthesis_operations(state_dir=state, project_root=base.parent,
                                                 config=service.config, writer=writer, agent=Agent(), limit=1) == 1
    request = load_workflow_request(state, queued["request_id"])
    assert request["status"] == "proposed", request
    proposal = load_workflow_proposal(state, request["proposal_ref"])
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    assert proposal["change_mode"] == "config_change"
    assert base.read_bytes() == before
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())
