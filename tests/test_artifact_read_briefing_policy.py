"""A shorter Plan briefing must still point to the exact complete read policy."""
from copy import deepcopy
import json

import pytest

from zf.runtime.artifact_read_ledger import (
    build_input_consumption_policy, render_attempt_source_briefing, write_input_consumption_policy,
)


@pytest.fixture
def bound(tmp_path):
    rows = [{"source_id": "matrix", "artifact_id": "test_matrix.json",
             "artifact_sha256": "a" * 64, "json_path": f"$.tests[{i}]"} for i in range(70)]
    policy = build_input_consumption_policy(workflow_run_id="run", attempt_id="attempt", required_reads=rows)
    descriptor = write_input_consumption_policy(tmp_path, policy)
    payload = {"attempt_source_manifest_ref": "artifacts/attempts/attempt/manifest.json",
               "workflow_run_id": "run", "attempt_id": "attempt", "output_profile_id": "plan-synth",
               "required_reads": policy["required_reads"], "input_consumption_policy_ref": descriptor,
               "input_consumption_policy_digest": descriptor["sha256"]}
    return payload, policy


def test_briefing_reuses_bound_policy_without_omitting_required_content(tmp_path, bound):
    payload, policy = bound
    before = deepcopy(payload)
    old = render_attempt_source_briefing(payload)
    compact = render_attempt_source_briefing(payload, state_dir=tmp_path)
    assert len(compact) < len(old) / 2
    assert "70 required selections" in compact
    assert payload["input_consumption_policy_ref"]["ref"] in compact
    assert "--required" in compact and "artifact index --attempt attempt" in compact
    assert "consume it before requesting" in compact and "not source content or read evidence" in compact
    assert "--source matrix" not in compact
    assert payload == before
    stored = json.loads((tmp_path / payload["input_consumption_policy_ref"]["ref"]).read_text())
    assert stored == policy and len(stored["required_reads"]) == 70


@pytest.mark.parametrize("fault", ["digest", "corrupt", "missing", "attempt", "run", "rows", "profile"])
def test_unbound_policy_keeps_explicit_fallback_commands(tmp_path, bound, fault):
    payload, _ = bound
    descriptor = payload["input_consumption_policy_ref"]
    if fault == "digest":
        payload["input_consumption_policy_digest"] = "b" * 64
    elif fault == "corrupt":
        (tmp_path / descriptor["ref"]).write_text("{}")
    elif fault == "missing":
        (tmp_path / descriptor["ref"]).unlink()
    elif fault == "attempt":
        payload["attempt_id"] = "other"
    elif fault == "run":
        payload["workflow_run_id"] = "other"
    elif fault == "rows":
        payload["required_reads"] = payload["required_reads"][:-1]
    else:
        payload["output_profile_id"] = "implementation"
    rendered = render_attempt_source_briefing(payload, state_dir=tmp_path)
    assert "--source matrix" in rendered
    assert "Complete required-read policy:" not in rendered
