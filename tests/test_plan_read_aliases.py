"""Plan port aliases share consumption only for identical pinned slices."""

import pytest

from zf.runtime.artifact_read_ledger import canonical_required_reads


def _source(name, **overrides):
    return {
        "source_id": f"plan-port-{name}", "artifact_id": f"{name}.json",
        "ref": "artifacts/task-map.json", "sha256": "a" * 64,
        "kind": "task_scoped_plan_input", "allowed_paths": ["$.tasks[0]"],
        **overrides,
    }


@pytest.mark.parametrize("profile", ["implementation", "task-verify", "candidate-verify"])
def test_identical_plan_port_aliases_require_one_read_without_removing_ports(profile):
    manifest = {"sources": [_source("task_map"), _source("planning_result")]}
    required = canonical_required_reads(manifest, output_profile_id=profile)
    assert len(required) == 1
    assert required[0]["source_id"] == "plan-port-task_map"
    assert len(manifest["sources"]) == 2


@pytest.mark.parametrize("overrides", [
    {"ref": "artifacts/other.json"}, {"sha256": "b" * 64},
    {"allowed_paths": ["$.tasks[1]"]}, {"ref": ""},
    {"access_scope": {"role": "verify"}}, {"retention": {"class": "restricted"}},
])
def test_distinct_or_unpinned_plan_slices_remain_required(overrides):
    required = canonical_required_reads(
        {"sources": [_source("task_map"), _source("planning_result", **overrides)]},
        output_profile_id="implementation",
    )
    assert len(required) == 2


@pytest.mark.parametrize("constraint", [
    {"min_returned_bytes": 20}, {"max_items": 1},
    {"max_chars": 20}, {"allow_truncated": True},
])
def test_different_read_constraints_cannot_alias(constraint):
    first = _source("task_map")
    explicit = {
        "source_id": first["source_id"], "artifact_id": first["artifact_id"],
        "artifact_sha256": first["sha256"], "json_path": "$.tasks[0]",
        "min_returned_bytes": 1, "max_items": 0, "max_chars": 0,
        "allow_truncated": False, **constraint,
    }
    required = canonical_required_reads(
        {"sources": [first, _source("planning_result")]},
        output_profile_id="implementation", explicit=[explicit],
    )
    assert len(required) == 2


def test_non_plan_evidence_authorities_do_not_alias():
    required = canonical_required_reads(
        {"sources": [_source("target", source_id="target"),
                     _source("impl", source_id="impl-self-check")]},
        output_profile_id="task-verify",
    )
    assert {row["source_id"] for row in required} == {"target", "impl-self-check"}
