"""An admitted pre-Impl revision must reach the Planner with the reviewed baseline."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import test_orchestrator_agent_run_plan as pre_impl
from tests.test_reader_fanout_runtime import _RecordingTransport
from zf.cli.plan_baseline_draft import draft_files
from zf.core.config.schema import FanoutAggregateConfig, RoleConfig, WorkflowStageConfig
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package, package_event_payload, write_plan_artifact_package,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

RUN = pre_impl.RUN_ID
STAGE_GENERATION = 'stage-replan-owner-authorized'
FEEDBACK = [
    'Keep the admitted business tasks and their independent write sets.',
    'Assign command and browser evidence to their respective readers.',
    'Assign the native execution interval proof to the independent concurrency reader.',
    'Bind the revised assignment and its evidence to the frozen candidate.',
]


def _stage(stage_id, trigger, reader, success, failure):
    return WorkflowStageConfig(
        id=stage_id, trigger=trigger, topology='fanout_reader', roles=[reader],
        aggregate=FanoutAggregateConfig(mode='wait_for_all', success_event=success,
                                        failure_event=failure),
    )


def _event(runtime, kind, *, cause=None, **payload):
    event = ZfEvent(type=kind, actor='zf-cli', origin='kernel', correlation_id=RUN,
                    causation_id=cause, payload={'workflow_run_id': RUN, **payload})
    runtime.event_writer.append(event)
    return event


def _review(tmp_path, *, stage='prd-plan', role='planner', action='revise'):
    runtime, _, _, old_package, old_ref = pre_impl._runtime(tmp_path)
    runtime.config.roles.extend(RoleConfig(name=r, instance_id=r, backend='mock', role_kind='reader')
                                for r in ('planner', 'scanner'))
    runtime.config.workflow.stages = [
        _stage('prd-scan', 'prd.requested', 'scanner', 'prd.scan.completed', 'prd.scan.failed'),
        _stage('prd-plan', 'prd.scan.completed', 'planner', 'task_map.ready', 'prd.plan.failed'),
    ]
    runtime.config.workflow.flow_metadata = {'result_protocol': {'mode': 'blocking'}}
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    owner = write_immutable_json_sidecar(runtime.state_dir, {"decision":"approved", "scope":"original assembly"},
        root="fixtures/owner-decision", kind="owner-confirmation", schema_version="owner.v1", created_by="test")
    scan = _event(runtime, 'prd.scan.completed', stage_id='prd-scan', flow_kind='prd',
                  pdd_id=pre_impl.GOAL_ID, target_ref='requirements.md',
                  owner_confirmation_ref=owner['ref'], owner_confirmation=owner,
                  owner_decision_resolution='approved', owner_decision_items=[{'question':'Original assembly?'}],
                  stage_replan_generation=STAGE_GENERATION,
                  stage_replan_generation_source_event_id='evt-authorize-generation',
                  task_map_generation=old_package['task_map_generation'],
                  plan_artifact_package_id=old_ref['package_id'],
                  plan_artifact_package_ref=old_ref['ref'], plan_artifact_package_digest=old_ref['sha256'])
    (tmp_path / 'requirements.md').write_text('Independent implementation and audit lanes.\n')
    stage = stage or 'prd-plan'
    fanout = _event(runtime, 'fanout.started', cause=scan.id, stage_id='prd-plan',
                    pattern_id='prd-plan', fanout_id='fanout-reviewed-plan', trigger_event_id=scan.id,
                    expected_children=[{'role_instance':'planner', 'child_id':'planner'}])
    ports = [pre_impl._port(runtime.state_dir, name, revision='R4') for name in (
        'requirement_spec', 'goal_claim_set', 'task_map', 'planning_result', 'product_acceptance_spec')]
    package = build_plan_artifact_package(
        workflow_run_id=RUN, flow_kind='prd', producer_stage_id='prd-plan', plan_revision='4',
        task_map_generation='R4', supersedes=old_ref, produced=ports,
        required_ports=[port['logical_name'] for port in ports],
        run_contract={'ref': old_package['run_contract_ref'], 'sha256': old_package['run_contract_sha256'],
                      'contract_digest': old_package['run_contract_digest']},
    )
    desc = write_plan_artifact_package(runtime.state_dir, package)
    current = package_event_payload(package, desc, status='admitted')
    _event(runtime, 'plan.artifact_package.admitted', **current)
    task_map = next(p for p in ports if p['logical_name'] == 'task_map')
    ready = _event(runtime, 'task_map.ready', cause=fanout.id, stage_id='prd-plan',
                   fanout_id='fanout-reviewed-plan', flow_kind='prd', goal_id=pre_impl.GOAL_ID,
                   stage_replan_generation=STAGE_GENERATION,
                   stage_replan_generation_source_event_id='evt-authorize-generation',
                   feature_id=pre_impl.GOAL_ID, task_map_generation='R4', task_map_ref=task_map['ref'],
                   task_map_digest=task_map['sha256'], plan_artifact_package_id=desc['package_id'],
                   plan_artifact_package_ref=desc['ref'], plan_artifact_package_digest=desc['sha256'])
    loaded = SimpleNamespace(**ready.payload, pdd_id=pre_impl.GOAL_ID)
    _, prepared = pre_impl._checkpoint(runtime, ready, loaded)
    decision = pre_impl._decision(prepared)
    del decision['run_plan']
    decision.update(decision=action, summary='The frozen evidence assignments still conflict.',
                    required_followup='Correct the evidence assignment in the Plan.',
                    reason_codes=['evidence_assignment_conflict'])
    if action == 'revise':
        source = next(s for s in prepared.context.source_manifest['sources'] if s['source_id']=='plan-artifact-package')
        decision['delta'] = {
            'schema_version':'orchestration-delta.v1','identity':decision['identity'],
            'directives':[{'directive_id':'split-evidence', 'action':'revise',
                           'target':{'role_instance':role, 'stage_id':stage},
                           'basis_refs':[{'ref':source['ref'], 'sha256':source['sha256']}],
                           'required_actions':list(FEEDBACK)}],
        }
    return runtime, pre_impl._submit(runtime, prepared, decision), ready, scan, desc, package


def test_admitted_revision_dispatches_only_planner_with_reviewed_ports(tmp_path):
    runtime, submitted, ready, scan, desc, package = _review(tmp_path)
    before = len(runtime.event_log.read_all())
    outcome = apply_orchestrator_agent_decision(runtime, submitted)
    assert outcome['status'] == 'applied', outcome
    assert apply_orchestrator_agent_decision(runtime, submitted) == outcome
    fresh = runtime.event_log.read_all()[before:]
    admitted, = (e for e in fresh if e.type == 'orchestrator.semantic.decision.admitted')
    retry, = (e for e in fresh if e.type == 'prd.scan.completed')
    assert retry.causation_id == admitted.id
    assert retry.payload['workflow_run_id'] == RUN
    assert retry.payload['stage_replan_generation'] == scan.payload['stage_replan_generation']
    assert retry.payload['stage_replan_generation_source_event_id'] == scan.payload['stage_replan_generation_source_event_id']
    assert retry.payload['task_map_generation'] == package['task_map_generation']
    assert retry.payload['plan_artifact_package_ref'] == desc['ref']
    assert retry.payload['plan_artifact_package_digest'] == desc['sha256']
    assert not any(e.type in {'human.escalate', 'prd.requested', 'prd.plan.failed', 'plan.approved'} for e in fresh)

    transport = _RecordingTransport()
    orchestrator = Orchestrator(runtime.state_dir, runtime.config, transport)
    orchestrator.run_once(events=[retry])
    orchestrator.run_once(events=[retry])
    assert [call[0] for call in transport.sent if call[0] in {'scanner', 'planner'}] == ['planner']
    child, = (e for e in runtime.event_log.read_all() if e.type == 'fanout.child.dispatched')
    operation, = (e for e in runtime.event_log.query(type='workflow.operation.requested')
                  if e.payload.get('operation_id') == child.payload['operation_id'])
    request = hydrate_sidecar_ref(runtime.state_dir, operation.payload['request_ref']).payload['request']
    digest = request['source_manifest_digest']
    source_manifest = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': f"artifacts/attempts/{request['active_attempt_id']}/source-manifests/{digest}.json", 'sha256':digest,
    }).payload
    sources = {s['source_id']:s for s in source_manifest['sources']}
    assert sources['plan-rework-package']['sha256'] == desc['sha256']
    context = hydrate_sidecar_ref(runtime.state_dir, sources['plan-rework-context']).payload
    assert 'owner_decision_resolution' not in context and 'owner_decision_resolution' not in retry.payload
    assert 'owner_decision_items' not in retry.payload and 'owner_confirmation_ref' not in retry.payload
    assert context['rework_summary']['reviewed_plan_fanout_id'] == 'fanout-reviewed-plan'
    assert context['rework_summary']['upstream_trigger_event_id'] == scan.id
    assert context['rework_summary']['stage_replan_generation'] == STAGE_GENERATION
    assert context['historical_orchestration_context']['authority'] == 'observation_only'
    assert context['historical_orchestration_context']['source_event_id'] == scan.id
    assert 'owner-confirmation' not in sources
    assert sources['historical-owner-confirmation']['sha256'] == scan.payload['owner_confirmation']['sha256']
    assert context['rework_feedback'] == FEEDBACK
    assert context['required_actions'] == FEEDBACK and 'operator_feedback' not in context
    assert source_manifest['task_map_generation'] == ready.payload['task_map_generation']
    names = ['task_map', 'product_acceptance_spec']
    expected = {p['logical_name']:p for p in package['produced']}
    files = dict(draft_files(runtime.state_dir, source_manifest,
                             {'actor':'planner','role':'planner','operation_id':child.payload['operation_id'],
                              'purpose':'workflow'}, names))
    for name in names:
        assert sources[f'baseline-plan-port-{name}']['sha256'] == expected[name]['sha256']
        assert files[f'{name}.json'] == (runtime.state_dir / expected[name]['ref']).read_bytes()


@pytest.mark.parametrize(('stage','role','action'), [('prd-scan','scanner','revise'),
                          ('prd-plan','other-writer','revise'),('prd-plan','planner','clarify')])
def test_owner_or_out_of_scope_decision_does_not_dispatch_planner(tmp_path, stage, role, action):
    runtime, submitted, *_ = _review(tmp_path, stage=stage, role=role, action=action)
    before = len(runtime.event_log.read_all())
    result = apply_orchestrator_agent_decision(runtime, submitted)
    assert not result.get('applied')
    assert not any(e.type in ('prd.scan.completed','plan.approved','fanout.child.dispatched')
                   for e in runtime.event_log.read_all()[before:])


@pytest.mark.parametrize('change', ['superseded', 'owner-approved', 'successor-planning'])
def test_late_revision_cannot_reopen_prior_or_approved_plan(tmp_path, change):
    runtime, submitted, ready, _, desc, package = _review(tmp_path)
    if change == 'superseded':
        pre_impl._revised_candidate(runtime, prior_package=package, prior_ref=desc, revision='5', generation='R5')
    elif change == 'successor-planning':
        _event(runtime, 'fanout.started', stage_id='prd-plan', fanout_id='fanout-new-author')
    else:
        _event(runtime, 'plan.approved', plan_id=ready.id, task_map_generation='R4',
               plan_artifact_package_ref=desc['ref'], plan_artifact_package_digest=desc['sha256'],
               task_map_ref=ready.payload['task_map_ref'])
    before = len(runtime.event_log.read_all())
    result = apply_orchestrator_agent_decision(runtime, submitted)
    assert not result.get('applied')
    assert not any(e.type in ('prd.scan.completed', 'plan.approved','fanout.child.dispatched')
                   for e in runtime.event_log.read_all()[before:])


def _legacy_escalation(runtime, submitted):
    """Rebuild the historical unsupported-route outcome through canonical event writers."""
    from zf.runtime.orchestrator_agent_decision_apply import _decision_fingerprint

    decision = submitted.payload['orchestration_decision']
    base = {'workflow_run_id':RUN, 'operation_id':decision['identity']['operation_id'],
            'checkpoint':'pre_impl', 'checkpoint_policy':'blocking', 'decision':'revise',
            'decision_ref':submitted.payload['control_result_ref'], 'decision_event_id':submitted.id,
            'source_event_id':submitted.payload['source_event_id'], 'decision_fingerprint':_decision_fingerprint(decision)}
    _event(runtime, 'orchestrator.semantic.decision.admitted', cause=submitted.id, **base)
    escalation = _event(runtime, 'human.escalate', cause=submitted.id,
                        orchestrator_operation_id=base['operation_id'], orchestrator_decision_ref=base['decision_ref'])
    _event(runtime, 'orchestrator.semantic.decision.applied', cause=submitted.id, **base, applied=False, status='escalated')
    return escalation


def test_historical_recovery_previews_redrives_and_replays_on_the_same_manifest(tmp_path):
    from zf.runtime.orchestrator_agent_pre_impl_revision import reconcile_pre_impl_revision

    runtime, submitted, *_ = _review(tmp_path)
    escalation = _legacy_escalation(runtime, submitted)
    kwargs = {'escalation_event_id':escalation.id}
    log = runtime.event_log.path
    before = log.read_bytes()
    preview = reconcile_pre_impl_revision(runtime, submitted, **kwargs)
    assert preview['status'] == 'preview' and preview['required_actions'] == FEEDBACK
    with pytest.raises(ValueError, match='approval reference'):
        reconcile_pre_impl_revision(runtime, submitted, apply=True, **kwargs)
    assert log.read_bytes() == before
    kwargs.update(apply=True, approval_ref='owner-permitted-redrive')
    first = reconcile_pre_impl_revision(runtime, submitted, **kwargs)
    after = log.read_bytes()
    assert first == reconcile_pre_impl_revision(runtime, submitted, **kwargs)
    assert log.read_bytes() == after
    assert apply_orchestrator_agent_decision(runtime, submitted) == first
    issued = [e for e in runtime.event_log.read_all() if e.payload.get('replan_classification') == 'oa_pre_impl_revision']
    assert [e.id for e in issued] == [first['revision_trigger_event_id']]
    assert issued[0].payload['recovery_approval_ref'] == kwargs['approval_ref']


@pytest.mark.parametrize('fence', ['run.paused', 'dispatch.paused', 'run.completed'])
def test_fenced_checkpoint_does_not_dispatch_planner(tmp_path, fence):
    from zf.runtime.orchestrator_agent_pre_impl_revision import reconcile_pre_impl_revision

    runtime, submitted, *_ = _review(tmp_path)
    escalation = _legacy_escalation(runtime, submitted)
    _event(runtime, 'run.admission.admitted')
    _event(runtime, fence)
    before = runtime.event_log.path.read_bytes()
    with pytest.raises(ValueError, match='paused|terminal'):
        reconcile_pre_impl_revision(runtime, submitted, apply=True, escalation_event_id=escalation.id,
                                    approval_ref='may-revise-but-cannot-resume')
    assert runtime.event_log.path.read_bytes() == before


@pytest.mark.parametrize(('interleaved', 'expected'), [('worker.idle', True), ('run.paused', False)])
def test_conditional_apply_rechecks_the_checkpoint_after_concurrent_event(tmp_path, monkeypatch, interleaved, expected):
    runtime, submitted, *_ = _review(tmp_path)
    _event(runtime, 'run.admission.admitted')
    append = runtime.event_writer.append
    calls = []

    def race(event, **kwargs):
        if event.payload.get('replan_classification') == 'oa_pre_impl_revision':
            if not calls:
                _event(runtime, interleaved)
            calls.append(event.id)
        return append(event, **kwargs)

    monkeypatch.setattr(runtime.event_writer, 'append', race)
    result = apply_orchestrator_agent_decision(runtime, submitted)
    assert result['applied'] is expected
    assert len(calls) == (2 if expected else 1)
    assert len([e for e in runtime.event_log.read_all() if e.payload.get('replan_classification') ==
                'oa_pre_impl_revision']) == int(expected)


def _issued_revision(tmp_path):
    runtime, submitted, ready, _, desc, package = _review(tmp_path)
    runtime.config.roles.append(RoleConfig(name="observer", instance_id="observer", backend="mock", role_kind="reader"))
    runtime.config.workflow.stages.append(_stage("parallel-reader", "prd.scan.completed", "observer",
                                                "parallel.complete", "parallel.failed"))
    runtime.config.workflow.stages.append(WorkflowStageConfig(id="parallel-writer", trigger="prd.scan.completed",
        topology="fanout_writer_scoped", roles=["dev-1"]))
    result = apply_orchestrator_agent_decision(runtime, submitted)
    trigger = next(e for e in runtime.event_log.read_all() if e.id == result["revision_trigger_event_id"])
    return runtime, ready, desc, package, trigger


def test_directed_revision_only_starts_its_author_live_and_on_replay(tmp_path):
    runtime, _, _, _, trigger = _issued_revision(tmp_path)
    transport = _RecordingTransport()
    orchestrator = Orchestrator(runtime.state_dir, runtime.config, transport)
    orchestrator.run_once(events=[trigger])
    assert [role for role, *_ in transport.sent] == ["planner"]
    restarted = Orchestrator(runtime.state_dir, runtime.config, _RecordingTransport())
    restarted._resume_unstarted_reader_fanouts([trigger, *runtime.event_log.query(type="fanout.started")])
    rows = [e.payload["stage_id"] for e in runtime.event_log.query(type="fanout.started")
            if e.payload.get("trigger_event_id") == trigger.id]
    assert rows == ["prd-plan"]
    restarted._maybe_start_writer_fanout(trigger)
    assert not any(e.payload.get("stage_id") == "parallel-writer" and
                   (e.causation_id == trigger.id or e.payload.get("trigger_event_id") == trigger.id)
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("change", ["new_package", "plan.approved", "plan.rejected", "successor"])
def test_published_revision_cannot_dispatch_into_a_decided_or_superseded_candidate(tmp_path, change):
    runtime, ready, desc, package, trigger = _issued_revision(tmp_path)
    if change == "new_package":
        pre_impl._revised_candidate(runtime, prior_package=package, prior_ref=desc, revision="5", generation="R5")
    elif change == "successor":
        _event(runtime, "fanout.started", stage_id="prd-plan", fanout_id="later-author", trigger_event_id="later-request")
    else:
        # The public owner command may bind by plan_id without a Run field.
        runtime.event_writer.append(ZfEvent(type=change, actor="operator", origin="kernel", payload={"plan_id":ready.id}))
    transport = _RecordingTransport()
    orchestrator = Orchestrator(runtime.state_dir, runtime.config, transport)
    orchestrator.run_once(events=[trigger])
    restarted = Orchestrator(runtime.state_dir, runtime.config, _RecordingTransport())
    assert not restarted._resume_unstarted_reader_fanouts([trigger])
    assert not transport.sent
    assert not any(e.payload.get("trigger_event_id") == trigger.id for e in runtime.event_log.query(type="fanout.started"))


def test_foreign_scope_cannot_narrow_a_normal_event_or_claim_a_revision(tmp_path):
    from dataclasses import replace

    runtime, ready, _, _, trigger = _issued_revision(tmp_path)
    # A foreign Plan decision and a non-kernel declaration of approval aren't Owner receipts.
    runtime.event_writer.append(ZfEvent(type="plan.approved", actor="operator", origin="kernel",
        payload={"workflow_run_id":"another-run", "plan_id":ready.id}))
    runtime.event_writer.append(ZfEvent(type="plan.rejected", actor="observer", origin="worker",
        payload={"workflow_run_id":RUN, "plan_id":ready.id}))
    normal = ZfEvent(type=trigger.type, actor="upstream", origin="worker", correlation_id="new-run",
        payload={"target_ref":"requirements.md", "workflow_run_id":"new-run"})
    copied = replace(trigger, id="evt-copied-scope", actor="observer", origin="worker")
    broken = replace(copied, id="evt-missing-self", payload={k:v for k,v in copied.payload.items()
                     if k not in {"revision_trigger_event_id","revision_trigger_type"}})
    forged = replace(normal, id="evt-fake-target", payload={**normal.payload, "revision_target_stage":"parallel-reader"})
    for event in (copied, broken, forged, normal):
        runtime.event_writer.append(event)
    transport = _RecordingTransport()
    orchestrator = Orchestrator(runtime.state_dir, runtime.config, transport)
    for malformed in (copied, broken, forged):
        assert not orchestrator._maybe_start_reader_fanout(malformed)
    assert orchestrator._maybe_start_reader_fanout(trigger)
    assert [r for r, *_ in transport.sent] == ["planner"]
    assert orchestrator._maybe_start_reader_fanout(normal)
    rows = [e.payload["stage_id"] for e in runtime.event_log.query(type="fanout.started")
            if e.payload.get("trigger_event_id") == normal.id]
    assert sorted(rows) == ["parallel-reader", "prd-plan"]


@pytest.mark.parametrize("handoff", ["start", "send"])
def test_new_plan_between_route_check_and_fanout_or_transport(tmp_path, monkeypatch, handoff):
    runtime, _, desc, package, trigger = _issued_revision(tmp_path)
    transport = _RecordingTransport()
    orchestrator = Orchestrator(runtime.state_dir, runtime.config, transport)
    if handoff == "start":
        append = orchestrator.event_writer.append
        def advance_then_start(event, **kwargs):
            if event.type == "fanout.started" and event.payload.get("trigger_event_id") == trigger.id:
                pre_impl._revised_candidate(runtime, prior_package=package, prior_ref=desc, revision="5", generation="R5")
            return append(event, **kwargs)
        monkeypatch.setattr(orchestrator.event_writer, "append", advance_then_start)
    else:
        preregister = orchestrator._preregister_reader_fanout_operations
        def advance_before_send(**kwargs):
            prepared = preregister(**kwargs)
            pre_impl._revised_candidate(runtime, prior_package=package, prior_ref=desc, revision="5", generation="R5")
            return prepared
        monkeypatch.setattr(orchestrator, "_preregister_reader_fanout_operations", advance_before_send)
    orchestrator.run_once(events=[trigger])
    assert not transport.sent
    assert not runtime.event_log.query(type="fanout.child.dispatched")
    started = [e for e in runtime.event_log.query(type="fanout.started") if e.payload.get("trigger_event_id") == trigger.id]
    assert len(started) == int(handoff == "send")
