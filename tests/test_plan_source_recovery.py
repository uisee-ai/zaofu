"""A rejected scan source returns to the approved OA before a business Task exists."""

import pytest

from tests.test_plan_team_inputs import submit_plan_expert
from tests.test_prd_stage_teams import select
from tests.test_reader_fanout_runtime import _state
from tests.test_workflow_registered_controller import _candidate, assembly
from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation, prepared_operation_from_checkpoint_event
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.workflow_proposal import build_workflow_proposal


def _start(assembly, tmp_path):
    base, state, _, _, _ = assembly
    for name in ("api.md", "api-index.md"):
        (base.parent / name).write_text("API inputs and exact source index")
    plan = select(assembly, ["product-scan", "tech-scan"])
    plan["members"][1].update(input_refs=["api.md"], input_alternatives={"api.md": ["api-index.md"]})
    admitted, request, candidate = _candidate(assembly)
    config = load_config(candidate)
    proposal, ref = build_workflow_proposal(state, request=request, base_config_path=base,
        candidate_config_path=candidate, synthesis_result_ref=admitted.result_ref, flow_kind="prd",
        preflight={"status": "GO", "blockers": []})
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    root = base.parent
    fixture_root = tmp_path / "transport"
    fixture_root.mkdir()
    _, _, transport, template = _state(fixture_root)
    runtime_state = state
    runtime = Orchestrator(runtime_state, template.config, transport, project_root=root)
    log = runtime.event_log
    # A real regular file existed at approval; at dispatch only the approved alternative remains.
    (root / "api.md").unlink()
    runtime.config.workflow.flow_metadata = config.workflow.flow_metadata
    runtime.config.workflow.orchestration = config.workflow.orchestration
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    roles = ["product-scan", "tech-scan", "planner", "plan-critic", "orchestrator"]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader") for r in roles]
    for role in roles:
        provision_role_submit_credential(runtime_state, role)
    provision_role_artifact_read_credential(runtime_state, "orchestrator", role_name="orchestrator", provider="mock")
    run = "run-before-task"
    def record(kind, data, cause=None):
        return runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", actor="zf-cli",
            correlation_id=run, causation_id=cause, payload={"workflow_run_id": run, **data}))
    invoke = record("workflow.invoke.requested", {"workflow_proposal_ref": ref,
        "workflow_proposal_digest": proposal["proposal_digest"], "effective_config_ref": proposal["effective_config_ref"]})
    record("workflow.invoke.accepted", {}, invoke.id)
    trigger = record("prd.requested", {"flow_kind": "prd", "prd_ref": "prd.md", "pdd_id": "F-first"})
    runtime.run_once(events=[trigger])
    return runtime, log, transport


def _decision(runtime, checkpoint, replacement):
    p = prepared_operation_from_checkpoint_event(runtime, checkpoint)
    activate_orchestrator_agent_operation(runtime, p, dispatch_id=checkpoint.id, causation_id=checkpoint.id)
    for read in p.context.read_policy["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=p.context.source_manifest, source_id=read["source_id"],
            artifact_id=read["artifact_id"], json_path=read["json_path"], actor="orchestrator", role="orchestrator",
            provider="mock")
    token = provision_role_submit_credential(runtime.state_dir, "orchestrator").read_text().strip()
    body = {"schema_version": "orchestration-decision.v1", "decision": "select_source",
            "identity": {"workflow_run_id": p.workflow_run_id, "operation_id": p.operation_id,
                         "checkpoint": p.checkpoint, "input_digest": p.context.input_ref["sha256"],
                         "effective_config_digest": p.context.effective_config_ref["sha256"]},
            "reason_codes": ["api_index_covers_same_operation"], "summary": "The API index answers the same question.",
            "replacement_ref": replacement}
    receipt = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
        event_writer=runtime.event_writer).submit(operation_id=p.operation_id, role_instance="orchestrator",
                                                  semantic_result=body, credential=token)
    return next(e for e in runtime.event_log.read_all() if e.id == receipt.canonical_event_id)


@pytest.mark.parametrize("authorized", [True, False])
def test_approved_oa_replaces_only_the_failed_source_and_original_scan_receipts_resume(assembly, tmp_path, monkeypatch, authorized):
    runtime, log, transport = _start(assembly, tmp_path)
    rejection, = log.query(type="fanout.plan.source.rejected")
    assert rejection.payload["recovery_owner"] == "orchestrator" and rejection.payload["input_ref"] == "api.md"
    assert not log.query(type="fanout.child.failed")
    def complete(role):
        d, = [e for e in log.query(type="fanout.child.dispatched") if e.payload["role_instance"] == role]
        return submit_plan_expert(runtime, d, {"child_id": d.payload["child_id"], "status": "passed",
                "summary": role + " reviewed its own sources", "recommendation": "approve", "findings": []})
    complete("product-scan")
    runtime._reconcile_reader_fanout_triggers()
    checkpoint, = log.query(type="orchestrator.semantic.checkpoint.requested")
    assert checkpoint.payload["source_event_id"] == rejection.id
    assert not log.query(type="prd.scan.completed")
    assert "tech-scan" not in [r for r, *_ in transport.sent]
    decision = _decision(runtime, checkpoint, "api-index.md" if authorized else "prd.md")
    # Interrupt after the OA's checked resolution, before the resumed child is allocated.
    with monkeypatch.context() as patch:
        patch.setattr("zf.runtime.plan_source_recovery._dispatch_rebound", lambda *a: None)
        patch.setattr(runtime, "_recover_pending_reader_fanout_dispatches", lambda *a, **kw: False)
        runtime.run_once(events=[decision])
    runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    runtime.run_once(events=[decision])
    runtime._reconcile_reader_fanout_triggers()
    runtime._recover_pending_reader_fanout_dispatches(log.read_all())
    if not authorized:
        assert not log.query(type="fanout.plan.source.rebound")
        failure, = log.query(type="fanout.child.failed")
        assert failure.payload["source_event_id"] == rejection.id and "not approved" in failure.payload["reason"]
        assert not log.query(type="prd.scan.completed")
        return
    selection, = log.query(type="fanout.plan.source.rebound")
    assert selection.payload["source_event_id"] == rejection.id
    assert [r for r, *_ in transport.sent].count("tech-scan") == 1
    complete("tech-scan")
    admission, = log.query(type="prd.scan.completed")
    assert len(admission.payload["stage_member_results"]) == 2
    runtime.run_once(events=[admission])
    assert [r for r, *_ in transport.sent].count("planner") == 1
    assert [r for r, *_ in transport.sent].count("product-scan") == 1


def test_restart_between_failure_and_oa_notice_reconciles_and_timeout_has_a_receiver(assembly, tmp_path, monkeypatch):
    from zf.runtime.orchestrator_agent_operations import workflow_operation_service
    from zf.runtime.workflow_operation import reduce_workflow_operations

    with monkeypatch.context() as patch:
        patch.setattr("zf.runtime.orchestrator_agent_operations._publish_checkpoint", lambda *args: None)
        runtime, log, transport = _start(assembly, tmp_path)
        runtime._reconcile_reader_fanout_triggers()
    rejection, = log.query(type="fanout.plan.source.rejected")
    assert not log.query(type="orchestrator.semantic.checkpoint.requested")
    row, = [r for r in reduce_workflow_operations(log.read_all()).values() if r["parent_stage_id"] == "oa-plan_source"]
    runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    runtime._reconcile_reader_fanout_triggers()
    runtime._reconcile_reader_fanout_triggers()
    checkpoint, = log.query(type="orchestrator.semantic.checkpoint.requested")
    assert checkpoint.payload["operation_id"] == row["operation_id"] and checkpoint.payload["source_event_id"] == rejection.id
    workflow_operation_service(runtime).fail(operation_id=row["operation_id"], request_hash=row["request_hash"],
        workflow_run_id=row["workflow_run_id"], reason="provider_deadline_exhausted", causation_id=checkpoint.id)
    runtime._reconcile_reader_fanout_triggers()
    runtime._reconcile_reader_fanout_triggers()
    failure, = log.query(type="fanout.child.failed")
    assert failure.payload["recovery_owner"] == "run_manager" and failure.payload["source_event_id"] == rejection.id
    assert "provider_deadline_exhausted" in failure.payload["reason"]
    assert not log.query(type="fanout.plan.source.rebound")


def test_untrusted_resolution_cannot_consume_the_original_obligation(assembly, tmp_path):
    from zf.runtime.fanout import FanoutManifestProjector

    runtime, log, _ = _start(assembly, tmp_path)
    source, = log.query(type="fanout.plan.source.rejected")
    runtime.event_writer.append(ZfEvent(type="fanout.plan.source.rebound", actor="tech-scan", origin="worker",
        causation_id=source.id, correlation_id=source.correlation_id, payload={**source.payload,
            "source_event_id": source.id, "replacement_ref": "unauthorized", "source_sha256": "a" * 64}))
    manifest = FanoutManifestProjector(runtime.state_dir).rebuild(source.payload["fanout_id"], log.read_all())
    child, = [r for r in manifest["children"] if r["role_instance"] == "tech-scan"]
    assert child["status"] == "awaiting_source" and child["payload"]["stage_obligation"]["input_refs"] == ["api.md"]
    assert not log.query(type="prd.scan.completed")


def test_directory_ref_can_only_narrow_to_a_real_in_scope_file(tmp_path):
    from types import SimpleNamespace
    from zf.runtime.plan_source_recovery import _permitted

    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "app" / "service.py").touch()
    outside = tmp_path / "private"
    outside.touch()
    (root / "app" / "alias").symlink_to(outside)
    runtime = SimpleNamespace(project_root=root)
    charter = {"input_refs": ["app/"]}
    _permitted(runtime, charter, "app/", "app/service.py")
    for path in ["../private", "app/../other", "another/dir.py", "app/alias", "/tmp/file", "app/"]:
        with pytest.raises(ValueError):
            _permitted(runtime, charter, "app/", path)


def test_redrive_rechecks_selected_bytes_and_carries_forward_the_exact_incident(assembly, tmp_path, monkeypatch):
    runtime, log, transport = _start(assembly, tmp_path)
    runtime._reconcile_reader_fanout_triggers()
    checkpoint, = log.query(type="orchestrator.semantic.checkpoint.requested")
    decision = _decision(runtime, checkpoint, "api-index.md")
    with monkeypatch.context() as patch:
        patch.setattr("zf.runtime.plan_source_recovery._dispatch_rebound", lambda *a: None)
        patch.setattr(runtime, "_recover_pending_reader_fanout_dispatches", lambda *a, **kw: False)
        runtime.run_once(events=[decision])
    selection, = log.query(type="fanout.plan.source.rebound")
    (runtime.project_root / "api-index.md").write_text("Different revision after selection.")
    runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    runtime._recover_pending_reader_fanout_dispatches(log.read_all())
    failure, = log.query(type="fanout.child.failed")
    assert failure.payload["source_resolution_event_id"] == selection.id
    assert failure.payload["recovery_owner"] == "run_manager"
    assert "changed after OA source selection" in failure.payload["reason"]
    assert [r for r, *_ in transport.sent].count("tech-scan") == 0


@pytest.mark.parametrize("interruption", ["before_notice", "after_admission", "paused"])
def test_oa_admitted_result_is_delivered_without_manual_event_replay(assembly, tmp_path, monkeypatch, interruption):
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from tests.test_run_admission import _control

    class Interrupted(BaseException):
        pass

    runtime, log, transport = _start(assembly, tmp_path)
    runtime._reconcile_reader_fanout_triggers()
    checkpoint, = log.query(type="orchestrator.semantic.checkpoint.requested")
    if interruption == "before_notice":
        append = runtime.event_writer.append
        def interrupted(event):
            if event.type == "orchestrator.semantic.decision.submitted":
                raise Interrupted
            return append(event)
        with monkeypatch.context() as patch:
            patch.setattr(runtime.event_writer, "append", interrupted)
            with pytest.raises(Interrupted):
                _decision(runtime, checkpoint, "api-index.md")
    else:
        decision = _decision(runtime, checkpoint, "api-index.md")
        if interruption == "after_admission":
            def interrupt(*args):
                raise Interrupted
            with monkeypatch.context() as patch:
                patch.setattr("zf.runtime.plan_source_recovery.apply_source_decision", interrupt)
                with pytest.raises(Interrupted):
                    apply_orchestrator_agent_decision(runtime, hydrate_profiled_control_result_event(runtime.state_dir, decision))
            assert len(log.query(type="orchestrator.semantic.decision.admitted")) == 1
        else:
            assert _control(runtime, action="run-pause", run_id=checkpoint.correlation_id)["status"] == "paused"

    assert len(log.query(type="workflow.call.result.admitted")) == 1
    assert not log.query(type="fanout.plan.source.rebound")
    runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    runtime._reconcile_reader_fanout_triggers()
    assert len(log.query(type="fanout.plan.source.rebound")) == 1
    if interruption == "paused":
        runtime._recover_pending_reader_fanout_dispatches(log.read_all())
        assert "tech-scan" not in [r for r, *_ in transport.sent]
        assert _control(runtime, action="run-resume", run_id=checkpoint.correlation_id)["status"] == "running"
    runtime._reconcile_reader_fanout_triggers()
    runtime._recover_pending_reader_fanout_dispatches(log.read_all())
    assert [r for r, *_ in transport.sent].count("tech-scan") == 1
    assert len(log.query(type="orchestrator.semantic.checkpoint.requested")) == 1
    assert len(log.query(type="orchestrator.semantic.decision.admitted")) == 1


@pytest.mark.parametrize(("flow", "policy"), [
    ("issue", "blocking"), ("refactor", "blocking"), ("workflow", "shadow"),
    ("research", "shadow"), ("prd", "shadow"),
])
def test_source_checkpoint_requires_its_registered_prd_route(tmp_path, flow, policy):
    from tests.test_orchestrator_agent_contracts import _write_config
    from zf.core.config.loader import ConfigError

    path = _write_config(tmp_path, f"""orchestration:
  flow_policies:
    {flow}:
      mode: semantic_control
      checkpoints: [plan_source]
      pilot_id: source-recovery
      checkpoint_policies: {{plan_source: {policy}}}""")
    with pytest.raises(ConfigError, match="requires a blocking PRD"):
        load_config(path)
