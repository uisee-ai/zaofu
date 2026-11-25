import json

import pytest

from tests.test_plan_candidate_preflight import _source_index, _task_map
from zf.core.config.schema import ZfConfig
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_execution_review import plan_candidate_writer_policy


@pytest.mark.parametrize(("enabled", "registered", "paths", "expected_error"), [
    (True, True, ["artifacts/evidence/browser/"], True),
    (True, True, "desktop.png", True),
    (True, True, ["ZF_EVIDENCE_DIR/desktop.png", "trace/mobile.zip"], False),
    (False, True, ["artifacts/evidence/browser/"], False),
    (True, False, ["artifacts/evidence/browser/"], False),
])
def test_plan_preflight_checks_only_container_producers(tmp_path, enabled, registered, paths, expected_error):
    task_map = _task_map()
    task = task_map["tasks"][0]
    command = task["validation"]["commands"][0]
    command.update(owner="candidate_verify", producer_paths=paths)
    task["acceptance_criteria"][0]["verification_owner"] = "candidate_verify"
    config = ZfConfig()
    host = config.runtime.host_verification
    host.enabled = enabled
    executable = command.pop("command")
    command["cmd"] = f"  {executable}  "
    host.commands = [executable] if registered else []
    # A planner submit validates against the JSON snapshot, also used before Critic dispatch.
    policy = json.loads(json.dumps(plan_candidate_writer_policy(config)))
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf", project_root=tmp_path, writer_policy=policy,
        reports=[{"report": {"task_map": task_map, "source_index": _source_index(), "plan_md": "# Plan"}}],
        manifest={"trigger_payload": {"flow_kind": "issue", "issue_ref": "docs/issue.md"}},
        metadata={"flow_kind": "issue"},
    )
    errors = [row for row in result["errors"] if row["code"] == "host_producer_path_invalid"]
    assert bool(errors) == expected_error
    if errors:
        assert errors[0]["field"] == "TASK-1.validation.commands[test-command].producer_paths"
