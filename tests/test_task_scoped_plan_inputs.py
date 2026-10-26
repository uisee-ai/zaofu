import json

import pytest

from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.task_scoped_plan_inputs import SCHEMA_VERSION, task_input_paths


@pytest.mark.parametrize("domain", ["inventory-transfer", "document-publishing"])
def test_shared_rules_and_owned_behavior_survive_bounded_reads(domain):
    body = {"schema_version": SCHEMA_VERSION, "shared": {"rule": "deny revoked access"},
            "tasks": [{"task_id": f"{domain}-{i}", "body": {"case": "x" * 2000}}
                      for i in range(50)]}
    paths = task_input_paths(body, task_id=f"{domain}-12")
    assert paths == ["$.shared", "$.tasks[12]"]
    from zf.runtime.artifact_read_ledger import _select_json_path

    selected = [_select_json_path(body, p) for p in paths]
    assert selected == [body["shared"], body["tasks"][12]]
    assert len(json.dumps(selected)) < len(json.dumps(body)) / 20
    assert task_input_paths(body, task_id="", candidate=True) == ["$"]
    for profile in ("implementation", "task-verify"):
        reads = build_canonical_required_reads({"sources": [{
            "source_id": "plan-port-execution_spec", "artifact_id": "execution_spec",
            "sha256": "a" * 64, "kind": "task_scoped_plan_input", "allowed_paths": paths,
        }]}, output_profile_id=profile)
        assert [r["json_path"] for r in reads] == paths
        assert all(not r["allow_truncated"] for r in reads)


def test_missing_duplicate_or_unassigned_content_fails_closed():
    body = {"schema_version": SCHEMA_VERSION, "shared": {}, "tasks": []}
    with pytest.raises(ValueError, match="no partition"):
        task_input_paths(body, task_id="T")
    body["tasks"] = [{"task_id": "T", "body": {}}, {"task_id": "T", "body": {}}]
    with pytest.raises(ValueError, match="unique"):
        task_input_paths(body, task_id="T", candidate=True)
    body["tasks"] = []
    body["hidden_global_rules"] = ["must not silently omit"]
    with pytest.raises(ValueError, match="exactly"):
        task_input_paths(body, task_id="T")


def test_legacy_and_candidate_still_require_whole_inputs():
    assert task_input_paths({"rows": [1, 2]}, task_id="T") == ["$"]
    for profile, kind in [("implementation", "legacy"), ("candidate-verify", "task_scoped_plan_input")]:
        reads = build_canonical_required_reads({"sources": [{
            "source_id": "plan-port-execution_spec", "artifact_id": "execution_spec",
            "sha256": "a" * 64, "kind": kind, "allowed_paths": ["$.shared"],
        }]}, output_profile_id=profile)
        assert [r["json_path"] for r in reads] == ["$"]
