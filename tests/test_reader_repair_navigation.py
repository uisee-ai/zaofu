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


def test_recovery_first_policy_uses_one_required_cursor_for_the_whole_attempt():
    payload = _payload()
    rows = payload["required_reads"]
    rows.insert(0, rows.pop())
    original = deepcopy(payload)
    text = render_attempt_source_briefing(payload)
    before_read = text.split("Preferred deduplicated read")[0]
    assert "Follow its next_cursor through that selection and the remaining required inputs" in before_read
    assert "--source critical-reader-repair-context" not in before_read
    assert "--page-start 0 --page-size 4096" not in text
    assert "17 selections" in text
    assert payload == original


def test_no_repair_source_does_not_invent_feedback():
    payload = _payload()
    payload["required_reads"].pop()
    assert "before probing the runner" not in render_attempt_source_briefing(payload)


def test_failure_guidance_requires_only_a_pinned_recovery_source():
    from zf.runtime.reader_submission_guidance import verifier_failure_schema_guidance
    text = verifier_failure_schema_guidance()
    assert "If the current manifest pins critical-reader-repair-context" in text
    assert "host_execution_evidence_reads" in text
    assert "absence of an unpinned repair source is not a read failure" in text
    assert "On a recovery attempt, first consume every page" not in text


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
        child_payload={**child["payload"], **_payload(),
                       "semantic_result_submit_mode": "blocking",
                       "output_profile_id": "candidate-verify", "output_profile_revision": "1"},
    )
    text = path.read_text()
    assert "--source critical-reader-repair-context --artifact recovery.json" in text
    assert text.index("before probing the runner") < text.index("Success command:")
    assert "complete=false with next_cursor is remaining work, not an execution failure" in text
    assert "A corrected pagination option error is no longer a blocker" in text


def test_repair_cursor_delivers_every_page_once_and_preserves_delivery_witness(tmp_path, monkeypatch, capsys):
    import hashlib
    import json
    from datetime import datetime, timezone
    from uuid import uuid4

    from tests.test_artifact_read_capability import _grant
    from zf.cli.main import main
    from zf.core.events import EventLog, EventWriter, ZfEvent
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.artifact_read_ledger import (
        build_attempt_source_manifest, required_read_satisfied, write_attempt_source_manifest,
    )

    state = tmp_path / "state"
    (state / "artifacts").mkdir(parents=True)
    bodies = {"critical-reader-repair-context": "恢复证据" * 1536, "contract": "frozen acceptance"}
    sources = []
    requirements = []
    for source_id, body in bodies.items():
        path = state / "artifacts" / f"{source_id}.json"
        path.write_text(json.dumps({"text": body}, ensure_ascii=False))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        sources.append({"source_id": source_id, "artifact_id": source_id, "ref": str(path.relative_to(state)),
                        "sha256": sha, "allowed_paths": ["$.text"]})
        requirements.append({"source_id": source_id, "artifact_id": source_id,
                             "artifact_sha256": sha, "json_path": "$.text"})
    attempt = "attempt-1"
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1", task_id="T1", attempt_id=attempt, dispatch_id=attempt,
        sources=sources, metadata={"read_purpose": "implementation"},
    )
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op-1")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for kind in ("workflow.operation.requested", "workflow.operation.started"):
        writer.emit(kind, payload={"operation_id": "op-1", "active_attempt_id": attempt})
    writer.emit("task.dispatched", payload={"attempt_id": attempt, "required_reads": requirements})
    sid = str(uuid4())
    transcript = tmp_path / "codex.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session("dev-1", sid, session_path=transcript)
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid}}) + "\n")

    command = ["artifact", "read", "--attempt", attempt, "--required", "--page-bytes", "4096", "--state-dir", str(state)]
    cursor = None
    read = {key: "" for key in bodies}
    pages = 0
    while True:
        assert main(command + (["--cursor", cursor] if cursor else [])) == 0
        raw = capsys.readouterr().out.strip()
        page = json.loads(raw)
        for item in page["items"]:
            key = item["source_id"]
            if key == "contract":
                assert read["critical-reader-repair-context"] == bodies["critical-reader-repair-context"]
            assert item["page_start"] == len(read[key])
            read[key] += item["content"]
        pages += 1
        with transcript.open("a") as out:
            out.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "type": "response_item",
                                 "payload": {"type": "function_call_output", "call_id": f"call-{pages}",
                                             "output": raw}}) + "\n")
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages > 2 and read == bodies
    ledger = state / "artifacts" / "attempts" / attempt / "read-ledger.active.jsonl"
    receipts = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert all(required_read_satisfied(receipts, requirement) for requirement in requirements)
    assert main([*command, "--resume"]) == 0
    continuation = json.loads(capsys.readouterr().out)["continuation"]
    assert continuation["reason"] == "observed_complete"
    assert continuation["skipped_chars"] == sum(map(len, bodies.values()))
    assert continuation["required_occurrences"] == 2
