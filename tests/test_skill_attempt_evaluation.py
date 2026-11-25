from __future__ import annotations

import json

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.skill_attempt_evaluation import EvaluationHistory


def event(kind, identity, **fields):
    return ZfEvent(type=kind, origin="kernel", payload={**identity, **fields})


def identity(attempt="a1"):
    return dict(
        workflow_run_id="go-build",
        operation_id="op",
        attempt_id=attempt,
        dispatch_id="d-" + attempt,
        request_hash="request",
        task_id="go-package",
    )


def dispatch(identity):
    return event(
        "task.pipeline.stage.dispatched",
        identity,
        output_profile_id="task-verify",
        role_instance="reader",
        target_commit="abc",
        skills=["review-method"],
    )


def test_cancelled_attempt_without_materialization_or_result(tmp_path):
    history = EvaluationHistory(tmp_path, tmp_path.parent)
    first, second = identity(), identity("a2")
    history.consume(dispatch(first))
    stop = event(
        "workflow.operation.cancelled",
        {k: first[k] for k in ("workflow_run_id", "operation_id", "request_hash")},
    )
    history.consume(stop)
    history.consume(dispatch(second))
    history.consume(stop)  # Duplicate unscoped cancel delivered after the retry.
    history.consume(event("task.attempt.failed", second))
    rows = list(history.cards())
    assert [row["task_outcome"] for row in rows] == ["cancelled", "failed"]
    assert rows[0]["skills"][0]["digest"] is None
    assert rows[0]["score"]["percent"] is None
    assert rows[0]["terminal_event_id"] == stop.id


def test_untrusted_terminal_cannot_issue_a_card(tmp_path):
    h = EvaluationHistory(tmp_path, tmp_path.parent)
    i = identity()
    h.consume(dispatch(i))
    fake = event("task.attempt.failed", i)
    fake.origin = "worker"
    h.consume(fake)
    assert list(h.cards()) == []


def test_result_from_wrong_target_marks_critical_failure(tmp_path):
    i = identity()
    h = EvaluationHistory(tmp_path, tmp_path.parent)
    h.consume(dispatch(i))
    ref = write_sidecar_json(
        tmp_path,
        "artifacts/control/x.json",
        {
            **i,
            "schema_version": "verification-result.v1",
            "verdict": "passed",
            "role_instance": "reader",
            "target_commit": "other",
        },
        kind="control",
        schema_version="verification-result.v1",
        created_by="test",
    )
    h.consume(
        event(
            "workflow.call.result.admitted",
            i,
            admission_status="admitted",
            semantic_verdict="passed",
            control_result_ref=ref,
        )
    )
    (card,) = h.cards()
    assert card["critical_failures"] == ["frozen_target_and_contract"]
    assert card["score"]["failed"] == 1
    assert card["skills"][0]["lift"] == "unmeasured"


def test_online_revision_after_late_return_and_sidecar(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from zf.core.events.log import EventLog
    from zf.runtime.skill_evaluation_projection import SkillEvaluationObserver, query_cards, replay_cards

    root, state = tmp_path, tmp_path / "state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    i = identity()
    path = "state/workdirs/reader/project/.codex/skills/review-method"
    manifest = "state/workdirs/reader/runtime/skills-manifest.json"
    treatment = write_sidecar_json(
        state,
        "artifacts/pack.json",
        {
            "schema_version": "skill-dispatch-treatment.v1",
            "role_instance": "reader",
            "task_id": i["task_id"],
            "materialized_skills": [
                {
                    "skill_name": "review-method",
                    "digest": "abcd",
                    "status": "materialized",
                    "materialized_to": path,
                }
            ],
        },
        kind="pack",
        schema_version="skill-dispatch-treatment.v1",
        created_by="test",
    )
    log.append(
        event(
            "skills.materialized",
            {},
            instance_id="reader",
            manifest_path=manifest,
            task_id=i["task_id"],
            skill_dispatch_treatment_ref=treatment["ref"],
            skill_dispatch_treatment_digest=treatment["sha256"],
        )
    )
    d = dispatch(i)
    d.payload["workdir"] = str(state / "workdirs/reader/project")
    log.append(d)
    result = write_sidecar_json(
        state,
        "artifacts/result.json",
        {
            **i,
            "schema_version": "verification-result.v1",
            "target_commit": "wrong",
            "summary": "blocked by a missing archive",
        },
        kind="result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    held = state / result["ref"]
    parked = held.rename(tmp_path / "held.json")
    terminal = event(
        "workflow.call.result.admitted",
        i,
        semantic_verdict="blocked",
        admission_status="admitted",
        control_result_ref=result,
    )
    log.append(terminal)
    online = SkillEvaluationObserver(state, root, None)
    online.poll()
    (first,) = query_cards(state)
    assert (first["revision"], first["score"]["percent"]) == (1, None)
    assert first["skills"][0]["digest"] == "abcd"

    parked.rename(held)
    online.poll()
    (second,) = query_cards(state)
    assert second["predecessor"] == first["digest"]
    assert second["critical_failures"] == ["frozen_target_and_contract"]

    log.append(terminal)
    other = identity("a2")
    log.append(dispatch(other))
    returned = event(
        "codex.hook.post_tool_use",
        i,
        tool_name="Read",
        tool_input={"file_path": str(root / path / "SKILL.md")},
        tool_response="---\nname: review-method\n---\nPRIVATE BODY",
    )
    returned.actor = "reader"
    log.append(returned)
    relative = event(
        "codex.hook.post_tool_use",
        i,
        tool_name="Read",
        tool_input={"file_path": path + "/SKILL.md"},
        cwd=str(tmp_path / "other-project"),
        tool_response="---\nname: review-method\n---",
    )
    relative.actor = "reader"
    log.append(relative)
    other_result = event("workflow.operation.cancelled", other)
    log.append(other_result)
    with ThreadPoolExecutor(2) as pool:
        for future in [
            pool.submit(observer.poll) for observer in (online, SkillEvaluationObserver(state, root, None))
        ]:
            future.result()
    rows = query_cards(state, history=True, attempt_id=i["attempt_id"])
    assert [r["revision"] for r in rows] == [1, 2, 3]
    assert rows[2]["predecessor"] == rows[1]["digest"]
    assert rows[2]["skills"][0]["read_event_ids"] == [returned.id]
    assert "PRIVATE BODY" not in json.dumps(rows)
    assert query_cards(state, attempt_id=other["attempt_id"])[0]["skills"][0]["read_event_ids"] == []
    (replay,) = replay_cards(state, root, None, dispatch_id=i["dispatch_id"])
    assert replay == {
        k: v for k, v in rows[2].items() if k not in {"digest", "predecessor", "change_reason", "revision"}
    }


def test_project_scoped_cli_web_replay_and_observer_restart(tmp_path, monkeypatch, capsys):
    import time
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zf.cli.main import main
    from zf.core.events.log import EventLog
    from zf.runtime.skill_evaluation_projection import SkillEvaluationObserver, query_cards
    from zf.web.artifact_query_routes import build_artifact_query_router

    state = tmp_path / "configured-state"
    state.mkdir()
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ZF_STATE_DIR", str(state))
    log = EventLog(state / "events.jsonl")
    i = identity()
    log.append(dispatch(i))
    log.append(event("workflow.operation.cancelled", i))
    cmd = ["attempt", "skill-evaluation", "--run", i["workflow_run_id"]]
    assert main([*cmd, "--replay"]) == 0
    assert json.loads(capsys.readouterr().out)["cards"][0]["task_outcome"] == "cancelled"
    assert not (state / "projections").exists()

    def collect(expected):
        observer = SkillEvaluationObserver(state, tmp_path, None, interval=0.02)
        observer.start()
        try:
            deadline = time.monotonic() + 4
            while len(query_cards(state)) != expected:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        finally:
            observer.stop()

    collect(1)
    archive = state / "events" / "day.jsonl"
    archive.parent.mkdir(exist_ok=True)
    log.path.rename(archive)
    next_attempt = identity("retry")
    log.append(dispatch(next_attempt))
    log.append(event("workflow.operation.failed", next_attempt))
    collect(2)
    app = FastAPI()
    app.include_router(
        build_artifact_query_router(
            state_dir=state,
            project_root=tmp_path,
            config=None,
            resolve_ctx=lambda project: SimpleNamespace(
                state_dir=state if project == "one" else tmp_path / "other"
            ),
        )
    )
    client = TestClient(app)
    assert main([*cmd, "--history"]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert client.get("/api/projects/one/tasks/go-package/skill-evaluations?history=true").json() == cli
    assert client.get("/api/projects/two/tasks/go-package/skill-evaluations").json() == {"cards": []}


def test_scoped_evidence_and_stale_failure_are_not_credited(tmp_path):
    h = EvaluationHistory(tmp_path, tmp_path.parent)
    i, retry = identity(), identity("retry")
    h.consume(dispatch(i))
    h.consume(dispatch(retry))
    failure = event(
        "workflow.operation.failed",
        {k: i[k] for k in ("workflow_run_id", "operation_id", "request_hash")},
        source_attempt_id=i["attempt_id"],
    )
    h.consume(failure)
    ref = write_sidecar_json(
        tmp_path,
        "artifacts/hidden.json",
        {
            **retry,
            "schema_version": "verification-result.v1",
            "summary": "sensitive",
            "target_commit": "wrong",
        },
        kind="result",
        schema_version="verification-result.v1",
        created_by="test",
        access_scope={"roles": ["secret"]},
    )
    h.consume(
        event(
            "workflow.call.result.admitted",
            retry,
            admission_status="admitted",
            semantic_verdict="blocked",
            control_result_ref=ref,
        )
    )
    first, second = h.cards()
    assert first["terminal_event_id"] == failure.id
    assert not second["critical_failures"]
    assert second["score"]["percent"] is None
    assert "hidden.json" not in json.dumps(second)


def test_dispatch_materialization_pointer_survives_lane_reuse(tmp_path):
    history = EvaluationHistory(tmp_path, tmp_path.parent)
    material = event(
        "skills.materialized",
        {},
        task_id="go-package",
        instance_id="reader",
        manifest_path="state/workdirs/reader/runtime/skills-manifest.json",
    )
    other_task = event(
        "skills.materialized",
        {},
        task_id="another-task",
        instance_id="reader",
        manifest_path=material.payload["manifest_path"],
    )
    history.consume(material)
    history.consume(other_task)
    for name, pointer in [("good", material.id), ("unbound", None), ("wrong-task", other_task.id)]:
        data = identity(name)
        stage = dispatch(data)
        stage.payload["workdir"] = str(tmp_path.parent / "state/workdirs/reader/project")
        stage.payload["skill_materialization_event_id"] = pointer
        history.consume(stage)
        history.consume(event("workflow.operation.blocked", data))
    cards = list(history.cards())
    assert [c["materialization_event_id"] for c in cards] == [material.id, None, None]


def test_blocked_contract_records_missing_ac_and_independent_unknown(tmp_path):
    data = identity()
    history = EvaluationHistory(tmp_path, tmp_path.parent)
    stage = dispatch(data)
    contract = write_sidecar_json(
        tmp_path,
        "artifacts/frozen.json",
        {
            "workflow_run_id": data["workflow_run_id"],
            "task_id": data["task_id"],
            "acceptance_criteria": [{"acceptance_id": "lint"}, {"acceptance_id": "install"}],
        },
        kind="contract",
        schema_version="contract",
        created_by="test",
    )
    stage.payload.update(
        contract_snapshot_ref=contract["ref"],
        contract_snapshot_digest=contract["sha256"],
        target_snapshot_digest="snapshot",
    )
    history.consume(stage)
    result = write_sidecar_json(
        tmp_path,
        "artifacts/blocked.json",
        {
            **data,
            "schema_version": "verification-result.v1",
            "verdict": "blocked",
            "role_instance": "reader",
            "target_commit": "abc",
            "target_snapshot_digest": "snapshot",
            "contract_snapshot_digest": contract["sha256"],
            "requirement_results": [
                {"acceptance_id": "install", "findings": ["installation receipt missing"]}
            ],
        },
        kind="result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    history.consume(
        event(
            "workflow.call.result.admitted",
            data,
            admission_status="admitted",
            semantic_verdict="blocked",
            control_result_ref=result,
        )
    )
    (card,) = history.cards()
    assert card["missing_mandatory_ids"] == ["lint"]
    assert {c["id"]: c["result"] for c in card["criteria"]} == {
        "frozen_target_and_contract": "pass",
        "mandatory_ac_has_result": "fail",
        "nonpass_reason_or_handoff": "pass",
        "archived_probe_claims_match": "unknown",
        "business_verdict_independently_confirmed": "unknown",
    }
    assert card["score"]["denominator"] == 3
