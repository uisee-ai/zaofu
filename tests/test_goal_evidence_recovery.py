from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.goal_evidence_recovery import GoalEvidenceRecovery
from zf.runtime.sidecar_refs import write_sidecar_json


def _held(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    service = GoalEvidenceRecovery(
        SimpleNamespace(config=None, project_root=tmp_path, state_dir=tmp_path),
        log,
        EventWriter(log),
    )

    def emit(kind, cause=None, **body):
        return service.event_writer.append(
            ZfEvent(
                type=kind,
                origin="kernel",
                actor="zf-cli",
                causation_id=cause,
                correlation_id="run",
                payload={"workflow_run_id": "run", **body},
            )
        )

    scope = {
        "task_map_generation": "revision",
        "plan_artifact_package_digest": "package",
    }
    emit("task_map.ready", **scope)
    emit(
        "candidate.ready",
        **scope,
        candidate_head_commit="candidate",
        schema_version="candidate-freeze-receipt.v1",
    )
    source = emit("flow.discovery.completed")
    fact = write_sidecar_json(
        tmp_path,
        "artifacts/fact.json",
        {"source_payload": {"goal_id": "goal"}},
        kind="facts",
        schema_version="fixture",
        created_by="test",
    )
    prior = emit(
        "flow.goal.closed",
        source.id,
        **scope,
        flow_kind="prd",
        closure_identity="original",
        closure_fact_ref=fact["ref"],
        closure_fact_digest=fact["sha256"],
    )
    receipt = {"ref": "artifacts/admitted-judge.json", "sha256": "a" * 64}
    judge = emit(
        "goal.closure.synthesized",
        prior.id,
        closure_identity="original",
        admitted_call_result_ref=receipt,
    )
    blocked = emit(
        "goal.closure.blocked",
        judge.id,
        recommended_action="hold",
        target_commit="candidate",
        admitted_call_result_ref=receipt,
        closure_fact_digest=fact["sha256"],
    )
    monkeypatch.setattr(
        "zf.runtime.goal_evidence_recovery.historical_candidate_reports",
        lambda *_: [{"admitted_event_id": "prior-verify"}],
    )
    return service, blocked, emit


def test_recovery_previews_then_dispatches_one_new_review(tmp_path, monkeypatch):
    service, blocked, emit = _held(tmp_path, monkeypatch)

    def rereview(*, event_type, source_event, flow_kind, payload):
        assert (event_type, source_event.type, flow_kind) == (
            "flow.goal.closed",
            "flow.discovery.completed",
            "prd",
        )
        assert payload == {"workflow_run_id": "run", "goal_id": "goal"}
        return emit(event_type, source_event.id, closure_identity="new-facts")

    monkeypatch.setattr(service, "_emit_current_goal_closure", rereview)
    count = len(service.event_log.read_all())
    assert service.review(blocked.id, apply=False)["historical_results"] == [
        "prior-verify"
    ]
    assert len(service.event_log.read_all()) == count
    assert service.review(blocked.id, apply=True)["status"] == "requested"
    with pytest.raises(ValueError, match="current closure"):
        service.review(blocked.id, apply=True)
    assert [e.type for e in service.event_log.read_all()[count:]] == [
        "flow.goal.closed"
    ]


@pytest.mark.parametrize(
    "change",
    ["no_evidence", "human", "terminal", "stale", "changed_candidate", "wrong_judge"],
)
def test_recovery_cannot_bypass_owner_or_reuse_stale_evidence(
    tmp_path, monkeypatch, change
):
    service, blocked, emit = _held(tmp_path, monkeypatch)
    if change == "no_evidence":
        monkeypatch.setattr(
            "zf.runtime.goal_evidence_recovery.historical_candidate_reports",
            lambda *_: [],
        )
    elif change == "human":
        blocked = emit(
            blocked.type,
            blocked.causation_id,
            **{**blocked.payload, "recommended_action": "human"},
        )
    elif change == "terminal":
        emit("run.goal.completed")
    elif change == "stale":
        emit("goal.closure.rejected")
    elif change == "wrong_judge":
        blocked = emit(
            blocked.type,
            blocked.causation_id,
            **{**blocked.payload, "admitted_call_result_ref": {}},
        )
    else:
        emit(
            "candidate.ready",
            candidate_head_commit="new-target",
            schema_version="candidate-freeze-receipt.v1",
        )
    count = len(service.event_log.read_all())
    with pytest.raises(ValueError):
        service.review(blocked.id, apply=True)
    assert len(service.event_log.read_all()) == count


def test_recover_cli_rejects_an_unknown_hold_without_writing_events(
    tmp_path, monkeypatch, capsys
):
    import argparse
    import json
    from zf.cli import recover, recover_goal_evidence

    parser = argparse.ArgumentParser()
    recover.register(parser.add_subparsers())
    args = parser.parse_args(
        [
            "recover",
            "goal-evidence",
            "--blocked-event",
            "unknown",
            "--state-dir",
            str(tmp_path),
        ]
    )
    ctx = SimpleNamespace(state_dir=tmp_path, project_root=tmp_path, config=None)
    monkeypatch.setattr(
        recover_goal_evidence, "resolve_project_context", lambda **_: ctx
    )
    log = EventLog(tmp_path / "events.jsonl")
    monkeypatch.setattr(
        recover_goal_evidence, "event_log_from_project", lambda *a, **kw: log
    )
    assert args.func(args) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert EventLog(tmp_path / "events.jsonl").read_all() == []
