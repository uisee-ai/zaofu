"""Recovery evidence must be discoverable before another unavailable probe."""
from copy import deepcopy

from zf.runtime.artifact_read_ledger import render_attempt_source_briefing


def _payload():
    return {
        "attempt_source_manifest_ref": "artifacts/attempts/a1/source.json",
        "attempt_id": "a1",
        "required_reads": [
            {"source_id": f"plan-{i}", "artifact_id": f"plan-{i}.json",
             "artifact_sha256": "a" * 64, "json_path": "$"}
            for i in range(16)
        ] + [{"source_id": "critical-reader-repair-context",
              "artifact_id": "recovery.json", "artifact_sha256": "b" * 64,
              "json_path": "$"}],
    }


def test_recovery_locator_precedes_bulk_read_without_weakening_policy():
    payload = _payload()
    before = deepcopy(payload)
    text = render_attempt_source_briefing(payload)
    locator = "--source critical-reader-repair-context --artifact recovery.json"
    assert locator in text
    assert text.index(locator) < text.index("Preferred deduplicated read")
    assert "before probing the runner" in text
    assert "not a product verdict" in text
    assert "--page-start 0 --page-size 4096" in text
    assert "17 selections" in text
    assert payload == before


def test_no_repair_source_does_not_invent_feedback():
    payload = _payload()
    payload["required_reads"].pop()
    assert "before probing the runner" not in render_attempt_source_briefing(payload)


def test_actual_fanout_briefing_delivers_recovery_navigation(tmp_path):
    from tests.test_reader_fanout_runtime import _durable_reader_state
    from zf.runtime.fanout import FanoutContext

    state, log, transport, orch, config, child = _durable_reader_state(tmp_path)
    context = FanoutContext(
        fanout_id="recovered", stage_id="verify-selected", topology="fanout_reader",
        trace_id="run-durable", trigger_event_id="retry", target_ref="candidate",
        expected_children=[],
    )
    path = orch._write_fanout_briefing(
        role=config.roles[0], context=context, child_id="verify-1", run_id="a1",
        aggregate=config.workflow.stages[0].aggregate,
        child_payload={**child["payload"], **_payload()},
    )
    text = path.read_text()
    assert "--source critical-reader-repair-context --artifact recovery.json" in text
    assert text.index("before probing the runner") < text.index("Success command:")
