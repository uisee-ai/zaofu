from types import SimpleNamespace

import pytest

from zf.core.events import EventLog
from zf.runtime.artifact_review_scope import select_review_inputs
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)


@pytest.mark.parametrize(
    "profile", ["integration-acceptance-review", "thin-judge-goal-closure"]
)
def test_profile_specific_bound_scope_and_unknown_dependency_fallback(
    tmp_path, profile
):
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    import json

    file = tmp_path / "artifacts" / "review.json"
    file.parent.mkdir()
    file.write_text(
        json.dumps(
            {
                "shared": "retained rule",
                "integration": "task risk",
                "goal": "goal claim",
            }
        )
    )
    manifest, _ = source_manifest_from_payload(
        state_dir=tmp_path,
        project_root=tmp_path.parent,
        workflow_run_id="r",
        task_id="T",
        attempt_id="a",
        dispatch_id="d",
        payload={
            "input_refs": [
                {
                    "ref": "artifacts/review.json",
                    "source_id": "source",
                    "artifact_id": "review.json",
                }
            ]
        },
    )
    row = {
        "source_id": "source",
        "artifact_id": "review.json",
        "artifact_sha256": manifest["sources"][0]["sha256"],
        "json_path": "$",
    }
    payload = {
        "workflow_run_id": "r",
        "target_commit": "c",
        "role_instance": "reviewer",
    }
    source = "$.integration" if profile.startswith("integration") else "$.goal"
    cert = {
        "schema_version": "approved-review-scope.v1",
        "approved_by": "owner",
        "revision": "1",
        "binding": {
            "workflow_run_id": "r",
            "target_commit": "c",
            "output_profile_id": profile,
            "baseline_sha256": canonical_json_sha256([row]),
        },
        "replacements": [
            {
                "original": row,
                "selectors": ["$.shared", source],
                "obligations": ["shared-rule", "AC1"],
            }
        ],
    }
    desc = write_immutable_json_sidecar(
        tmp_path,
        cert,
        root="review-scopes",
        kind="review_scope",
        schema_version=cert["schema_version"],
        created_by="owner",
    )
    runtime = SimpleNamespace(
        state_dir=tmp_path,
        event_log=EventLog(tmp_path / "events.jsonl"),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"approved_review_scopes": {profile: desc}}
            )
        ),
    )

    def choose(p, explicit=None):
        return select_review_inputs(
            runtime, "op", p, payload, manifest, [row], explicit
        )

    assert [r["json_path"] for r in choose(profile)] == ["$.shared", source]
    assert payload["review_scope_ref"] == desc
    assert choose(profile, [row]) == [row]
    assert choose("candidate-verify") == [row]
    payload["target_commit"] = "other"
    assert choose(profile) == [row]
