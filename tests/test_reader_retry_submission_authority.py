"""A new diagnostic attempt must not inherit a blanket ban on submission."""
import shlex
from pathlib import Path

import pytest

from tests.test_reader_fanout_runtime import _state


@pytest.mark.parametrize("typed", [True, False])
def test_nested_reader_retry_binds_own_state_and_current_submission(tmp_path: Path, typed: bool):
    source_state = tmp_path / "source-state"
    child_root = source_state / "repair-worktrees" / "diagnostic"
    child_root.mkdir(parents=True)
    state, _log, _transport, runtime = _state(child_root)
    run_id = "run-fanout-review-review-a-retry-1"
    payload = {"source_state_dir": str(source_state)}
    if typed:
        payload.update(
            operation_id="op-review", request_hash="request-review",
            attempt_id=run_id, semantic_result_submit_mode="blocking",
            result_protocol_mode="blocking", output_profile_id="candidate-verify",
            output_profile_revision="1",
            result_scratch_ref=f"tmp/result-submit/op-review/{run_id}/result.json",
        )
    path = runtime._write_fanout_retry_briefing(
        role=runtime.config.roles[0],
        manifest={
            "fanout_id": "fanout-review", "stage_id": "review-candidate",
            "trace_id": "trace-1", "trigger_event_id": "evt-candidate",
            "target_ref": "candidate/F-11111111", "topology": "fanout_reader",
            "aggregate_config": {
                "success_event": "review.approved", "failure_event": "review.rejected",
                "child_success_event": "verify.child.completed",
                "child_failure_event": "verify.child.failed",
            },
        },
        child={"child_id": "review-a", "role_instance": "review-a", "payload": payload},
        run_id=run_id,
    )
    briefing = path.read_text()
    commands = [shlex.split(block.split("\n```", 1)[0])
                for block in briefing.split("```bash\n")[1:]]
    submission = [args for args in commands if "--state-dir" in args]
    assert submission
    assert all(Path(args[args.index("--state-dir") + 1]) == state for args in submission)
    assert "source_state_dir is read-only evidence, not the submission target" in briefing
    assert f"This dispatch authorizes one submission for `{run_id}`" in briefing
    assert "Previous attempt failures do not cancel this authorization" in briefing
    assert "Do not repeat a successful submission or reuse stale identities" in briefing
    assert "Never retry or re-send" not in briefing
    if typed:
        assert "On error, preserve the exact command/error and stop this attempt" not in briefing
        assert "preflight" in briefing and "same scratch" in briefing
        assert "verifier_execution_failure" in briefing
        assert "omit product_acceptance_report" in briefing
