import pytest

from tests.test_product_acceptance import (
    CANDIDATE,
    GENERATION,
    RUN_ID,
    TARGET,
    _report,
    _spec,
)
from zf.core.events import ZfEvent
from zf.runtime import goal_historical_candidate_evidence as history
from zf.runtime.sidecar_refs import write_sidecar_json


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "tampered",
        "cross_run",
        "wrong_target",
        "unbound_envelope",
        "unrelated_plan",
    ],
)
def test_closure_pins_only_the_previous_admitted_product_report(
    tmp_path, monkeypatch, change
):
    def save(name, payload):
        return write_sidecar_json(
            tmp_path,
            f"artifacts/{name}.json",
            payload,
            kind=name,
            schema_version="fixture",
            created_by="test",
        )

    def event(kind, **fields):
        return ZfEvent(
            type=kind, origin="kernel", payload={"workflow_run_id": RUN_ID, **fields}
        )

    spec = save("spec", _spec())
    partial = _report(spec)
    partial.update(
        verdict="blocked", target_commit="wrong" if change == "wrong_target" else TARGET
    )
    journey = partial["journey_results"][0]
    journey["status"] = journey["assertion_results"][1]["status"] = "blocked"
    report = save("report", partial)
    control = save("control", {"verdict": "blocked"})
    envelope = save(
        "envelope",
        {"control_result": spec if change == "unbound_envelope" else control},
    )
    body = {
        "verdict": "blocked",
        "product_acceptance_spec_ref": spec["ref"],
        "product_acceptance_spec_digest": spec["sha256"],
        "product_acceptance_report_ref": report["ref"],
        "product_acceptance_report_digest": report["sha256"],
    }
    old = {
        "workflow_run_id": RUN_ID,
        "candidate_event_id": "freeze-original",
        "source_commit": TARGET,
        "task_map_generation": GENERATION,
        "plan_artifact_package_digest": "old-package",
    }
    admitted = event(
        "workflow.call.result.admitted",
        operation_id="reader",
        control_result_ref=control,
        envelope_ref=envelope,
    )
    failure = event(
        "test.failed",
        **old,
        fanout_id="fanout",
        candidate_verify_operation_ids=["reader", "reader"],
    )
    new = {
        "task_map_generation": "revised",
        "plan_artifact_package_digest": "new-package",
    }
    ready = event("task_map.ready", **new, source_commit=TARGET, rework_of=failure.id)

    def binding(_state, earlier, *, fanout_id, operation_id):
        assert earlier == [admitted] and (fanout_id, operation_id) == (
            "fanout",
            "reader",
        )
        return {**old, "control_result_ref": control, "candidate_ref": CANDIDATE}, body

    monkeypatch.setattr(history, "candidate_reader_binding", binding)
    if change == "tampered":
        (tmp_path / report["ref"]).write_text("{}")
    if change == "cross_run":
        admitted.payload["workflow_run_id"] = "another-run"
    if change == "unrelated_plan":
        ready.payload.pop("rework_of")
    events = [
        admitted,
        failure,
        ready,
        event("task_map.ready", **new, source_commit="subsequent-replay"),
    ]
    selected = history.historical_candidate_reports(
        tmp_path, events, RUN_ID, "revised", "new-package"
    )
    if change == "none":
        assert len(selected) == 1
        assert selected[0]["verdict"] == "blocked"
        assert selected[0]["target_commit"] == TARGET
        assert selected[0]["admitted_event_id"] == admitted.id
        assert selected[0]["product_acceptance_report"]["sha256"] == report["sha256"]
    else:
        assert selected == []
    if selected:
        pinned = save("closure-fact", {"historical_candidate_reports": selected})
        closure = {**new, "workflow_run_id": RUN_ID, "closure_fact_ref": pinned["ref"],
                   "closure_fact_digest": pinned["sha256"], "input_result_refs": [envelope["ref"]]}
        sources = list(history.historical_judge_sources(tmp_path, events, closure))
        assert {s["ref"] for s in sources} == {envelope["ref"], spec["ref"], report["ref"]}
        assert history.pinned_historical_result_refs(tmp_path, events, closure) == {envelope["ref"]}
        (tmp_path / report["ref"]).write_text("{}")
        with pytest.raises(history.HistoricalCandidateEvidenceError):
            list(history.historical_judge_sources(tmp_path, events, closure))
        assert history.pinned_historical_result_refs(tmp_path, events, closure) == set()



@pytest.mark.parametrize(
    "change", ["none", "fact_changed", "omitted_at_dispatch", "unbound_report"]
)
def test_goal_admission_rechecks_the_exact_history_in_the_dispatch(
    tmp_path, monkeypatch, change
):
    from tests.test_call_result_admission import (
        _append_admitted_upstream,
        _goal_closure_payload,
        _runtime,
    )

    service, operations = _runtime(tmp_path)
    old_ref = _append_admitted_upstream(
        tmp_path, operations, generation="previous", target_commit="b" * 40
    )
    report = {"envelope": {"ref": old_ref}, "failure_event_id": "old-failure"}

    def save(name, value):
        return write_sidecar_json(
            tmp_path,
            f"artifacts/{name}.json",
            value,
            kind=name,
            schema_version="fixture",
            created_by="test",
        )

    claims = save(
        "claim-set", {"claims": [{"goal_claim_id": "GOAL-AC-1", "mandatory": True}]}
    )
    fact = save("fact", {"historical_candidate_reports": [report]})
    result = _goal_closure_payload(claim_descriptor=claims, upstream_ref=old_ref)[
        "goal_closure_result"
    ]
    result.update(closure_fact_ref=fact["ref"], closure_fact_digest=fact["sha256"])
    closure = {
        **result,
        "candidate_head_commit": result["target_commit"],
        "plan_artifact_package_digest": "new-package",
        "input_result_refs": [] if change == "omitted_at_dispatch" else [old_ref],
        "has_historical_candidate_reports": True,
    }
    if change == "fact_changed":
        (tmp_path / fact["ref"]).write_text("{}")
    monkeypatch.setattr(
        history,
        "historical_candidate_reports",
        lambda *_: [] if change == "unbound_report" else [report],
    )
    operations.event_log.append(
        ZfEvent(type="flow.goal.closed", origin="kernel", payload=closure)
    )
    issues = service._goal_closure_issues(result)
    if change == "none":
        assert issues == []
        result["product_acceptance_report_ref"] = "old-product-report"
        operations.event_log.append(
            ZfEvent(
                type="flow.goal.closed",
                origin="kernel",
                payload={
                    **closure,
                    "product_acceptance_required": True,
                    "product_acceptance_report_ref": "current-product-report",
                },
            )
        )
        assert any(
            i["field"] == "control_result.product_acceptance_report_ref"
            for i in service._goal_closure_issues(result)
        )
    else:
        assert any(issue["code"] == "result_not_admitted" for issue in issues)
