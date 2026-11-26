from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.goal_evidence_recovery import GoalEvidenceRecovery
from zf.runtime.sidecar_refs import write_sidecar_json


def _held(tmp_path, monkeypatch, task_gap=None):
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
    frozen = emit(
        "candidate.ready",
        candidate_ref="refs/heads/candidate/parent",
        **scope,
        candidate_head_commit="candidate",
        schema_version="candidate-freeze-receipt.v1",
    )
    source = emit("flow.discovery.completed")
    fact = write_sidecar_json(
        tmp_path,
        "artifacts/fact.json",
        {
            "source_payload": {"goal_id": "goal"},
            **(
                {
                    "task_verify_evidence": [
                        {"control_result": {"ref": "artifacts/verify.json"}}
                    ]
                }
                if task_gap == "pinned"
                else {}
            ),
        },
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
        candidate_ref="refs/heads/candidate/parent",
        judge_batch_admissions={"candidate_event_id": frozen.id},
        closure_fact_ref=fact["ref"],
        closure_fact_digest=fact["sha256"],
    )
    receipt = {"ref": "artifacts/admitted-judge.json", "sha256": "a" * 64}
    extra = {}
    if task_gap:
        extra = _dispatch_evidence_hold(
            tmp_path, monkeypatch, emit, task_gap, scope, receipt, fact, frozen.id
        )
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
        **extra,
        admitted_call_result_ref=receipt,
        closure_fact_digest=fact["sha256"],
    )
    monkeypatch.setattr(
        "zf.runtime.goal_evidence_recovery.historical_candidate_reports",
        lambda *_: [] if task_gap else [{"admitted_event_id": "prior-verify"}],
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


def _dispatch_evidence_hold(
    state, monkeypatch, emit, case, scope, receipt, fact, freeze_id
):
    def artifact(name, body):
        return write_sidecar_json(
            state,
            "artifacts/" + name + ".json",
            body,
            kind="fixture",
            schema_version="v1",
            created_by="test",
        )

    gap = "artifacts/verify.json"
    gaps = [gap, "artifacts/other.json"] if case == "mixed" else [gap]
    control = artifact(
        "judge",
        {
            "workflow_run_id": "run",
            "target_commit": "candidate",
            "recommended_action": "hold",
            "verdict": "blocked",
            "open_gap_refs": gaps,
            "closure_fact_digest": fact["sha256"],
        },
    )
    readable = gap if case == "already_granted" else fact["ref"]
    manifest = artifact(
        "manifest",
        {
            **scope,
            "handoff_authority_profile": "thin-judge-goal-closure",
            "workflow_run_id": "run",
            "target_commit": "other" if case == "wrong_target" else "candidate",
            "attempt_id": "att",
            "dispatch_id": "dispatch",
            "sources": [{"ref": readable, "sha256": fact["sha256"]}],
        },
    )
    binding = {
        "operation_id": "judge",
        "request_hash": "hash",
        "attempt_id": "att",
        "dispatch_id": "dispatch",
    }
    emit("workflow.invoke.requested")
    emit(
        "fanout.child.dispatched",
        **{
            **binding,
            "task_id": "parent",
            "attempt_source_manifest": manifest,
            "payload": {"trigger_payload": {"closure_identity": "original"}},
            "dispatch_id": "unrelated"
            if case == "wrong_dispatch"
            else binding["dispatch_id"],
        },
    )
    emit(
        "workflow.call.result.admitted",
        **binding,
        envelope_ref=receipt,
        control_result_ref=control,
        control_result_schema="goal-closure-result.v1",
        semantic_verdict="blocked",
    )
    rows = (
        []
        if case == "no_authority"
        else [
            {
                "admitted_event_id": "verify",
                "candidate_event_id": freeze_id if case != "wrong_freeze" else "other",
                "control_result": {"ref": gap, "sha256": "e" * 64},
            }
        ]
    )
    monkeypatch.setattr(
        "zf.runtime.goal_evidence_recovery.judge_task_verify_evidence",
        lambda *a, **kw: rows,
    )
    monkeypatch.setattr(
        "zf.runtime.candidate_continuation._git_commit",
        lambda *a: "moved" if case == "moved_ref" else "candidate",
    )
    if case == "unauthorized":
        monkeypatch.setattr(
            "zf.runtime.workflow_config_consumer.approved_config_consumer_error",
            lambda *a: "drift",
        )
    if case == "human":
        monkeypatch.setattr(
            "zf.runtime.run_manager._pending_human_decisions",
            lambda *a: [{"blocking_scope": "run"}],
        )
    return {
        "operation_id": "judge",
        "open_gap_refs": gaps if case != "wrong_gap" else ["artifacts/replaced.json"],
    }


def test_held_judge_requests_only_one_review_for_a_newly_readable_task_body(
    tmp_path, monkeypatch
):
    service, blocked, emit = _held(tmp_path, monkeypatch, "missing")
    count = len(service.event_log.read_all())
    assert service.review(blocked.id, apply=False)["task_verify_results"] == ["verify"]
    assert len(service.event_log.read_all()) == count
    monkeypatch.setattr(
        service,
        "_emit_current_goal_closure",
        lambda **kw: emit(
            kw["event_type"], kw["source_event"].id, closure_identity="new-facts"
        ),
    )
    assert service.review(blocked.id, apply=True)["status"] == "requested"
    with pytest.raises(ValueError, match="current closure"):
        service.review(blocked.id, apply=True)
    assert [e.type for e in service.event_log.read_all()[count:]] == [
        "flow.goal.closed"
    ]


@pytest.mark.parametrize(
    "case",
    [
        "already_granted",
        "pinned",
        "wrong_target",
        "wrong_dispatch",
        "no_authority",
        "mixed",
        "wrong_gap",
        "moved_ref",
        "unauthorized",
        "human",
        "later_failure",
        "wrong_freeze",
    ],
)
def test_task_verify_recovery_fails_closed_without_a_new_approved_grant(
    tmp_path, monkeypatch, case
):
    service, blocked, emit = _held(tmp_path, monkeypatch, case)
    if case == "later_failure":
        emit("verify.failed")
    count = len(service.event_log.read_all())
    with pytest.raises(ValueError):
        service.review(blocked.id, apply=True)
    assert len(service.event_log.read_all()) == count
