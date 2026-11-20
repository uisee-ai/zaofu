"""The browser oracle must reject a plausible but wrong generated Plan."""
import pytest
from tests.e2e.prd_ui.audit import check_preview

PLAN = '# PRD\nREADME.md\n## SDD\nFiles\n## TDD\nMissing-file error\nLast acceptance\n'


@pytest.mark.parametrize("packet,valid", [
    ({"status": "ready", "diagnostics": []}, True),
    ({"status": "blocked", "diagnostics": [{"severity": "warning", "code": "owner_pending"}]}, True),
    ({"diagnostics": [{"severity": "error", "code": "artifact_lineage_mismatch"}]}, False),
    ({"diagnostics": [{"severity": "error", "code": "review_artifact_unreadable"}]}, False),
    ({}, False), (None, False),
])
def test_preview_requires_mechanical_artifact_integrity_without_approving_owner_decisions(packet, valid):
    from tests.e2e.prd_ui.audit import check_preview_artifact_integrity
    if valid:
        check_preview_artifact_integrity({"review_packet": packet})
    else:
        with pytest.raises(AssertionError):
            check_preview_artifact_integrity({"review_packet": packet})


@pytest.mark.parametrize("fault", ["", "old_verdict", "wrong_run", "wrong_plan", "not_web"])
def test_replan_audit_requires_new_same_run_review_after_controlled_rejection(fault):
    from zf.core.events import ZfEvent
    from tests.e2e.prd_ui.audit import check_replan
    old = ZfEvent(type="fanout.synth.completed", payload={"workflow_run_id": "r", "fanout_id": "f1"})
    first = ZfEvent(type="plan.approval.requested", payload={"workflow_run_id": "r", "plan_id": old.id})
    reject = ZfEvent(type="plan.rejected", actor="operator", payload={"plan_id": old.id, "surface": "web"})
    new = ZfEvent(type="fanout.synth.completed", payload={"workflow_run_id": "r", "fanout_id": "f2"})
    final = ZfEvent(type="plan.approval.requested", payload={"workflow_run_id": "r", "plan_id": new.id})
    events = [old, first, reject, new, final]
    if fault == "old_verdict": events = [old, first, new, reject, final]
    if fault == "wrong_run": final.payload["workflow_run_id"] = "other"
    if fault == "wrong_plan": reject.payload["plan_id"] = "other"
    if fault == "not_web": reject.payload["surface"] = "cli"
    if fault:
        with pytest.raises(AssertionError): check_replan(events, [first, final], "r")
    else:
        assert check_replan(events, [first, final], "r")["final_plan_id"] == new.id


@pytest.mark.parametrize("body", [
    PLAN,
    "# Shared Spec\nUse system cat README.md.\n## Task Spec\nOne owner; restore input.\n## Acceptance\nSeparate normal and missing-file evidence.\n",
])
def test_exact_generated_plan_preview(body):
    check_preview({'plan_id': 'current', 'content_profile': 'derived_plan_full', 'markdown': body},
                  expected_plan_id='current', expected_markdown=body)


def test_empty_artifact_is_not_a_complete_preview():
    with pytest.raises(AssertionError, match="empty Plan"):
        check_preview({'plan_id': 'current', 'content_profile': 'derived_plan_full', 'markdown': ''},
                      expected_plan_id='current', expected_markdown='')


@pytest.mark.parametrize('change', [
    {'plan_id': 'previous'},
    {'content_profile': 'summary_only'},
    {'content_profile': 'derived_plan'},
    {'markdown': PLAN.removesuffix('Last acceptance\n')},
    {'markdown': PLAN.replace('Missing-file error', 'Always succeeds')},
])
def test_reject_wrong_or_incomplete_preview(change):
    preview = {'plan_id': 'current', 'content_profile': 'derived_plan_full', 'markdown': PLAN} | change
    with pytest.raises(AssertionError):
        check_preview(preview, expected_plan_id='current', expected_markdown=PLAN)


@pytest.mark.parametrize('role', ['impl', 'unknown', ''])
def test_dispatch_oracle_rejects_writer_or_missing_operation_identity(role):
    from tests.e2e.prd_ui.audit import check_dispatches
    with pytest.raises(AssertionError):
        check_dispatches([{'operation_id': 'op'}], {'op': {'role_instance': role}},
                         {'planner': 'reader', 'impl': 'writer'})


def test_dispatch_oracle_joins_attempt_to_operation():
    from tests.e2e.prd_ui.audit import check_dispatches
    assert check_dispatches([{'operation_id': 'op'}], {'op': {'role_instance': 'planner'}},
                            {'planner': 'reader'}) == {'planner'}


@pytest.mark.parametrize("change", ["none", "same_pid", "same_config", "early_invoke", "no_stop", "rejection"])
def test_warm_config_handoff_oracle(change):
    from zf.core.events import ZfEvent
    from tests.e2e.prd_ui.audit import check_config_handoff
    rows = [ZfEvent(type="loop.started", payload={"pid": 1, "effective_config_sha256": "old"}),
            ZfEvent(type="loop.shutdown_requested"),
            ZfEvent(type="runtime.start.requested", payload={"pid": 2}),
            ZfEvent(type="loop.started", payload={"pid": 2, "effective_config_sha256": "new"}),
            ZfEvent(type="workflow.invoke.requested")]
    if change == "same_pid": rows[0].payload["pid"] = 2
    if change == "same_config": rows[0].payload["effective_config_sha256"] = "new"
    if change == "early_invoke": rows[3], rows[4] = rows[4], rows[3]
    if change == "no_stop": rows.pop(1)
    if change == "rejection": rows.append(ZfEvent(type="workflow.invoke.rejected"))
    if change == "none":
        assert check_config_handoff(rows)["new_pid"] == 2
    else:
        with pytest.raises(AssertionError): check_config_handoff(rows)


@pytest.mark.parametrize("change", ["none", "changed_body", "changed_contract", "other_run", "other_fanout", "unbound_ref", "missing_binding"])
def test_pure_critic_plan_is_bound_to_reviewed_artifact(tmp_path, change):
    import hashlib
    import json
    from zf.core.events import ZfEvent
    from tests.e2e.prd_ui.audit import read_generated_plan
    state = tmp_path / "state"
    (state / "artifacts").mkdir(parents=True)
    ref = "artifacts/planner.md"
    (state / ref).write_text(PLAN)
    digest = hashlib.sha256(PLAN.encode()).hexdigest()
    contract = {"workflow_run_id": "run", "fanout_id": "fan", "source_bindings": [{"ref": ref, "sha256": digest}]}
    if change == "missing_binding": contract["source_bindings"] = []
    raw = json.dumps(contract)
    (state / "artifacts/contract.json").write_text(raw)
    review = ZfEvent(type="fanout.synth.completed", payload={"workflow_run_id": "run", "fanout_id": "fan", "plan_synth_contract_ref": "artifacts/contract.json", "reviewed_plan_candidate_digest": hashlib.sha256(raw.encode()).hexdigest()})
    source = ZfEvent(type="task_map.ready", payload={"workflow_run_id": "run", "fanout_id": "fan", "evidence_refs": [ref]})
    approval = ZfEvent(type="plan.approval.requested", payload={"workflow_run_id": "run", "plan_id": source.id, "plan_artifact_ref": "docs/nonexistent.md"})
    preview = {"refs": {"markdown_ref": ref}}
    if change == "changed_body": (state / ref).write_text("plausible but changed")
    if change == "changed_contract": (state / "artifacts/contract.json").write_text(raw + " ")
    if change == "other_run": review.payload["workflow_run_id"] = "other"
    if change == "other_fanout": review.payload["fanout_id"] = "other"
    if change == "unbound_ref": source.payload["evidence_refs"] = []
    if change == "none":
        assert read_generated_plan(approval, preview, [review, source, approval], state=state, project=tmp_path) == (ref, digest, PLAN)
    else:
        with pytest.raises((AssertionError, ValueError)):
            read_generated_plan(approval, preview, [review, source, approval], state=state, project=tmp_path)
