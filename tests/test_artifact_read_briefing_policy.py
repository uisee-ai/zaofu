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
    assert len(compact) < len(old)
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


@pytest.mark.parametrize("fault", [
    "legacy_request", "other_policy", "missing_file", "corrupt_file", "other_attempt",
    "other_run", "wrong_reads", "different_inline_policy", "nonblocking",
])
def test_uncertain_initial_scratch_keeps_inline_policy(tmp_path, bound, fault):
    from zf.runtime.plan_synth_handoff import render_plan_synth_completion_command

    payload, policy = bound
    payload.update(operation_id="catalog-review", input_consumption_policy=policy,
                   semantic_result_submit_mode="blocking", result_scratch_ref="tmp/result-submit/catalog/result.json")
    descriptor = payload["input_consumption_policy_ref"]
    new_ref = descriptor
    if fault == "legacy_request":
        new_ref = None
    elif fault == "other_policy":
        new_ref = {**descriptor, "sha256": "b" * 64}
    elif fault == "missing_file":
        (tmp_path / descriptor["ref"]).unlink()
    elif fault == "corrupt_file":
        (tmp_path / descriptor["ref"]).write_text("{}")
    elif fault == "other_attempt":
        payload["attempt_id"] = "previous-curator"
    elif fault == "other_run":
        payload["workflow_run_id"] = "another-catalog"
    elif fault == "wrong_reads":
        payload["required_reads"] = []
    elif fault == "different_inline_policy":
        payload["input_consumption_policy"] = {**policy, "delivery_evidence": {"mode": "observed"}}
    else:
        payload["semantic_result_submit_mode"] = "shadow"

    render_plan_synth_completion_command(
        cli_command="zf", actor="curator", state_dir=tmp_path, payload=payload, new_scratch_policy_ref=new_ref,
    )
    assert json.loads((tmp_path / payload["result_scratch_ref"]).read_bytes()) == payload


def test_museum_plan_reader_gets_wide_page_guidance_without_changing_its_policy(tmp_path):
    from tests.test_plan_owner_rework_descriptor_reads import museum

    runtime, prepare, charter, _ = museum(tmp_path)
    issued, payload = prepare("catalog", charter)
    before = deepcopy(payload)
    text = render_attempt_source_briefing(payload, state_dir=runtime.state_dir, role_name="curator")
    assert "--format text --page-bytes 16384" in text
    assert "16384 native tool output tokens" in text
    assert "unknown or smaller capacity" in text and "8192 or 2048 bytes" in text
    assert "retry the same input cursor (omit it for page one)" in text
    assert "Keep raw evidence in the authorized session" in text
    assert "外借藏品清单" not in text
    assert payload == before
    replay, _ = prepare("catalog", charter)
    assert replay.request_hash == issued.request_hash
    native = render_attempt_source_briefing({**payload, "native_input_view": True}, role_name="curator")
    assert native.index("Preferred artifact input") < native.index("CLI fallback") < native.index("--format text --page-bytes 16384")


@pytest.mark.parametrize(("profile", "domain", "operation", "expected"), (
    ("plan-synth", None, None, "32768"),
    ("workflow-read", "plan", "fanout_reader_child", "16384"),
    ("workflow-read", "candidate", "fanout_reader_child", None),
    ("workflow-read", "plan", "fanout_writer_child", None),
    ("workflow-read", None, None, None),
    ("task-verify", "plan", "fanout_reader_child", None),
    ("implementation", "task", "task-stage", "16384"),
    ("task-verify", "task", "task-stage", "16384"),
    ("implementation", "plan", "task-stage", None),
    ("task-verify", "task", "fanout_reader_child", None),
    ("unregistered-profile", "task", "task-stage", None),
))
def test_required_cli_briefing_uses_the_bound_role_profile(bound, profile, domain, operation, expected):
    payload, _ = bound
    payload["output_profile_id"] = profile
    if domain:
        payload["handoff_authority_contract"] = {"output_profile_id": profile, "attempt_domain": domain,
                                                 "operation_type": operation}
    text = render_attempt_source_briefing(payload, role_name="planner")
    assert "Preferred deduplicated read" in text and "--page-bytes 2048" in text
    first_command = next(line for line in text.splitlines() if "Preferred deduplicated read" in line)
    if expected:
        assert f"--required --format text --page-bytes {expected}`" in first_command
        assert "tool output budget" in first_command
        assert "same flags" in text and "ZF_ARTIFACT_TEXT_PAGE_END" in text
        assert "--required --page-bytes 2048`" in text
    else:
        assert "--required`" in first_command
        assert "--format text --page-bytes" not in text
