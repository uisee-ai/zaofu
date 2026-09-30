"""Managed triage and project Workflow must retain separate contract identities."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.self_issue_policy import inject_self_issue_policy
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.generic_workflow_fanout import apply_generic_workflow_stage_payload
from zf.runtime.run_contract import build_run_contract, write_run_contract
from tests.test_generic_workflow import _envelope, _research_spec
from tests.test_self_issue_target import _locked_policy


def _documents() -> list[dict]:
    return inject_self_issue_policy(
        list(yaml.safe_load_all(_envelope(_research_spec()))),
        _locked_policy(),
    )


def _load(tmp_path: Path, documents: list[dict]):
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    return path, load_config(path)


@pytest.mark.parametrize("reverse", [False, True])
def test_managed_and_project_contracts_survive_reload_and_order(tmp_path, reverse):
    documents = _documents()
    path, config = _load(tmp_path, documents[::-1] if reverse else documents)
    assert len(config.workflow.generic_workflows) == 2
    project = flow_metadata_for(config, "workflow")
    managed = flow_metadata_for(config, "workflow", pattern_id="external-issue-triage")
    assert (
        project["generic_workflow_contract_digest"]
        != managed["generic_workflow_contract_digest"]
    )
    assert (
        project["required_delivery_artifacts"][0]["source_ref"] == "synthesize.report"
    )
    assert (
        managed["required_delivery_artifacts"][0]["source_ref"]
        == "external-issue-triage.report"
    )
    for stage in config.workflow.stages:
        child = SimpleNamespace(payload={})
        apply_generic_workflow_stage_payload(config, stage, [child])
        expected = managed if stage.id.startswith("external-issue-") else project
        assert (
            child.payload["generic_workflow_contract_digest"]
            == expected["generic_workflow_contract_digest"]
        )
        assert (
            child.payload["required_delivery_artifacts"]
            == expected["required_delivery_artifacts"]
        )
    for entry, expected in [("scope", project), ("external-issue-triage", managed)]:
        contract = build_run_contract(
            config, config_path=path, metadata_pattern_id=entry
        )
        assert contract["workflow"]["generic_workflow_contract"]["entry"] == entry
        assert (
            contract["workflow"]["generic_workflow_contract_digest"]
            == expected["generic_workflow_contract_digest"]
        )
        assert (
            flow_metadata_for(
                config,
                payload={
                    "flow_kind": "workflow",
                    "generic_workflow_contract_digest": expected[
                        "generic_workflow_contract_digest"
                    ],
                },
            )
            == expected
        )
    path.write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    reloaded = load_config(path)
    assert reloaded.workflow.generic_workflows == config.workflow.generic_workflows
    assert flow_metadata_for(reloaded, "workflow") == project
    assert (
        flow_metadata_for(reloaded, "workflow", pattern_id="external-issue-triage")
        == managed
    )


@pytest.mark.parametrize("damage", ["two-projects", "two-managed", "third", "overlap"])
def test_generic_contract_coexistence_is_bounded(tmp_path, damage):
    documents = _documents()
    managed = next(
        d
        for d in documents
        if d.get("metadata", {}).get("name") == "external-issue-triage/v1"
    )
    project = next(d for d in documents if d["kind"] == "Workflow" and d is not managed)
    if damage == "two-projects":
        documents.remove(managed)
        documents.append(deepcopy(project))
    elif damage == "two-managed":
        documents.remove(project)
        documents.append(deepcopy(managed))
    elif damage == "third":
        documents.append(deepcopy(project))
    else:
        # The two specs are individually valid but share a stage identity.
        project["spec"] = json.loads(
            json.dumps(project["spec"])
            .replace(
                '"scope"',
                '"external-issue-triage"',
            )
            .replace("scope.brief", "external-issue-triage.brief")
        )
    with pytest.raises(ConfigError, match="only one|overlap"):
        _load(tmp_path, documents)


@pytest.mark.parametrize(
    "payload",
    [
        {"pattern_id": "unknown"},
        {"generic_workflow_contract_digest": "0" * 64},
        {"pattern_id": "scope", "managed_digest": True},
    ],
)
def test_conflicting_route_identity_does_not_fall_back_to_project(tmp_path, payload):
    _, config = _load(tmp_path, _documents())
    payload = dict(payload)
    if payload.pop("managed_digest", False):
        payload["generic_workflow_contract_digest"] = flow_metadata_for(
            config,
            "workflow",
            pattern_id="external-issue-triage",
        )["generic_workflow_contract_digest"]
    with pytest.raises(ValueError, match="identity"):
        flow_metadata_for(config, "workflow", payload=payload)


def test_external_ingress_uses_managed_contract_with_project_workflow(
    tmp_path, monkeypatch
):
    from zf.core.events.log import EventLog
    from zf.core.issue_triage.store import IssueMirrorStore
    from zf.runtime.external_issue_ingress import ExternalIssueIngressService
    from tests.test_external_issue_ingress import _Reconciler, _mirror, _config

    monkeypatch.setattr("zf.runtime.self_issue_target.zaofu_v7_root", lambda: tmp_path)
    path, config = _load(tmp_path, _documents())
    config.self_issue = _config().self_issue
    state = tmp_path / config.project.state_dir
    state.mkdir(exist_ok=True)
    write_run_contract(state, build_run_contract(config, config_path=path))
    IssueMirrorStore(state).upsert(
        _mirror(created_at="2026-09-01T00:00:00+00:00"), "Untrusted issue"
    )
    service = ExternalIssueIngressService(
        state, config, project_root=tmp_path, reconciler=_Reconciler()
    )
    result = service.start_manual_triage(8)
    assert result.ok
    invoke = next(
        e
        for e in EventLog(state / "events.jsonl").read_all()
        if e.type == "workflow.invoke.requested"
    )
    managed = flow_metadata_for(config, "workflow", pattern_id="external-issue-triage")
    assert (
        invoke.payload["generic_workflow_contract_digest"]
        == managed["generic_workflow_contract_digest"]
    )
    identity = service._workflow_identity()
    snapshot = json.loads((state / identity["run_contract_ref"]).read_text())
    contract = snapshot["contract"]
    assert (
        contract["workflow"]["generic_workflow_contract"]["entry"]
        == "external-issue-triage"
    )
    assert (
        contract["required_delivery_artifacts"]
        == managed["required_delivery_artifacts"]
    )
