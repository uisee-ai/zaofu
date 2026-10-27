"""Historical Raw YAML stays bound to verified snapshots, never live config."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from tests.test_web_workflow_proposals import _action_body, _client, _write_config
from zf.runtime import workflow_proposal
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.web.projections.workflow_yaml import project_workflow_yaml


@pytest.fixture
def legacy(tmp_path):
    state_dir = tmp_path / "custom-state"
    content = b"kind: IssueFlow\nspec:\n  lanes: 2\n  token: private-value\n---\nkind: ZfConfig\n"
    digest = hashlib.sha256(content).hexdigest()
    candidate = state_dir / "private/workflow-config-candidates/request" / f"{digest}.yaml"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(content)
    source = tmp_path / "zf.yaml"
    source.write_text("current-config-must-not-appear: true\n")
    snapshot = {
        "source_config": {"path": str(source), "sha256": digest},
        "config": {"project": {"name": "frozen"}, "password": "frozen-secret"},
    }
    proposal = {
        "private_config_candidate_ref": str(candidate),
        "target_config": {"sha256": digest},
    }
    return state_dir, candidate, snapshot, proposal


def test_legacy_yaml_uses_bound_candidate_and_redacts_without_mutating(legacy, tmp_path):
    state_dir, candidate, snapshot, proposal = legacy
    original = copy.deepcopy(snapshot)
    content = candidate.read_bytes()
    result = project_workflow_yaml(snapshot, proposal, state_dir=state_dir, project_root=tmp_path)
    assert result["yaml_source"] == "bound_candidate"
    docs = list(yaml.safe_load_all(result["yaml"]))
    assert [doc["kind"] for doc in docs] == ["IssueFlow", "ZfConfig"]
    assert docs[0]["spec"]["lanes"] == 2
    assert "private-value" not in result["yaml"]
    assert "current-config" not in result["yaml"]
    assert snapshot == original
    assert candidate.read_bytes() == content


@pytest.mark.parametrize("fault", ["missing", "corrupt", "outside", "symlink", "wrong_digest", "oversized", "invalid_yaml"])
def test_unusable_candidate_falls_back_only_to_redacted_frozen_config(legacy, tmp_path, monkeypatch, fault):
    state_dir, candidate, snapshot, proposal = legacy
    if fault == "missing":
        candidate.unlink()
    elif fault == "corrupt":
        candidate.write_text("unbound: true\n")
    elif fault in {"outside", "symlink"}:
        outside = tmp_path / "outside.yaml"
        outside.write_bytes(candidate.read_bytes())
        if fault == "outside":
            proposal["private_config_candidate_ref"] = str(outside)
        else:
            candidate.unlink()
            candidate.symlink_to(outside)
    elif fault == "wrong_digest":
        proposal["target_config"]["sha256"] = "0" * 64
    elif fault == "oversized":
        monkeypatch.setattr("zf.runtime.workflow_snapshot_yaml.DEFAULT_HYDRATE_LIMIT_BYTES", 10)
    else:
        candidate.write_text("spec: [\n")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        snapshot["source_config"]["sha256"] = digest
        proposal["target_config"]["sha256"] = digest
    result = project_workflow_yaml(snapshot, proposal, state_dir=state_dir, project_root=tmp_path)
    assert result["yaml_source"] == "compiled_snapshot"
    assert yaml.safe_load(result["yaml"])["project"]["name"] == "frozen"
    assert "frozen-secret" not in result["yaml"]
    assert "current-config" not in result["yaml"]
    assert "unbound" not in result["yaml"]


def test_modern_or_unreadable_snapshot_is_preserved(legacy, tmp_path):
    state_dir, _, snapshot, proposal = legacy
    for payload in ({**snapshot, "yaml": "existing: true\n"}, {"status": "unreadable"}):
        assert project_workflow_yaml(payload, proposal, state_dir=state_dir, project_root=tmp_path) is payload


@pytest.mark.parametrize("fault", [None, "missing_candidate", "corrupt_snapshot"])
def test_detail_projects_verified_legacy_yaml_without_rewriting_artifacts(tmp_path, monkeypatch, fault):
    # Compile a valid old-format immutable artifact, including its real digest.
    write = workflow_proposal.write_immutable_json_sidecar

    def write_legacy(state_dir, payload, **kwargs):
        if kwargs["kind"] == "effective_config_snapshot":
            payload = {key: value for key, value in payload.items() if key not in {"yaml", "compiled_graph"}}
        return write(state_dir, payload, **kwargs)

    monkeypatch.setattr(workflow_proposal, "write_immutable_json_sidecar", write_legacy)
    client, state_dir = _client(tmp_path, monkeypatch)
    request_id = "REQ-LEGACY-YAML"
    created = client.post(
        "/api/projects/default/actions/workflow-request",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": request_id},
        json=_action_body(request_id, {
            "request_id": request_id, "kind": "issue", "objective": "Fix expiry",
            "acceptance": ["Session remains valid"], "backend": "mock", "allow_missing_env": True,
        }),
    )
    assert created.status_code == 202, created.text
    proposal = workflow_proposal.load_workflow_proposal(state_dir, created.json()["proposal_ref"])
    ref = proposal["effective_config_ref"]
    snapshot = hydrate_sidecar_ref(state_dir, ref).payload
    assert "yaml" not in snapshot
    snapshot_path = state_dir / ref["ref"]
    if fault == "missing_candidate":
        Path(proposal["private_config_candidate_ref"]).unlink()
    elif fault == "corrupt_snapshot":
        snapshot_path.write_text("{}\n")
    _write_config(tmp_path / "zf.yaml", lanes=9)
    before = snapshot_path.read_bytes()
    ledger_before = (state_dir / "events.jsonl").read_bytes()
    detail = client.get(f"/api/projects/default/workflow-requests/{request_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    projected = body["artifacts"]["effective_config"]
    assert body["proposal"]["effective_config_ref"] == ref
    assert snapshot_path.read_bytes() == before
    assert (state_dir / "events.jsonl").read_bytes() == ledger_before
    if fault == "corrupt_snapshot":
        assert projected == {"status": "unreadable"}
    elif fault == "missing_candidate":
        assert projected["yaml_source"] == "compiled_snapshot"
        compiled = yaml.safe_load(projected["yaml"])
        assert compiled.keys() == snapshot["config"].keys()
        assert compiled["project"] == snapshot["config"]["project"]
        assert [role["name"] for role in compiled["roles"]] == [
            role["name"] for role in snapshot["config"]["roles"]
        ]
    else:
        assert projected["yaml_source"] == "bound_candidate"
        assert "lanes: 1" in projected["yaml"]
        assert "lanes: 9" not in projected["yaml"]
