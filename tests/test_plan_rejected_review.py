from copy import deepcopy

import pytest

from tests.test_plan_local_review import _policy_fixture, _scope_policy
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize('problem', ['', 'wrong_run', 'wrong_candidate', 'missing_read', 'tampered_control', 'wrong_recovery'])
def test_rejected_facts_actual_handoff_preserves_open_findings_without_approval(tmp_path, problem):
    manifest, required, events = _policy_fixture(tmp_path, 'rejected')
    # A rejected Critic cannot become a passed-package baseline.
    assert _scope_policy(tmp_path, events=events, manifest=manifest, required=required,
                         explicit=[], critic_role='critic') == required
    synth = next(e for e in events if e.id == 'old-synth')
    trigger = next(e for e in events if e.id == 'trigger')
    failure = ZfEvent(id='old-failure', type='lane.stage.failed', origin='kernel',
                     causation_id=synth.id, payload={'workflow_run_id': 'run-1'})
    if problem == 'wrong_run': failure.payload['workflow_run_id'] = 'other'
    if problem == 'wrong_candidate': synth.payload['reviewed_plan_candidate_digest'] = 'b' * 64
    if problem == 'missing_read':
        events = [e for e in events if e.type != 'workflow.operation.started']
    if problem == 'tampered_control':
        (tmp_path / synth.payload['control_result_ref']['ref']).write_text('{}')
    if problem == 'wrong_recovery': trigger.payload['rework_of'] = 'unrelated'
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    for event in [*events, failure]: writer.append(event)
    args = dict(state_dir=tmp_path, project_root=tmp_path.parent, source_event_id='trigger',
                payload={'workflow_run_id': 'run-1', 'rework_of': 'old-failure'})
    sources = build_plan_handoff_input_refs(**args)
    facts = [s for s in sources if s['source_id'] == 'plan-rejected-review']
    if problem:
        assert facts == []
        return
    assert len(facts) == 1
    control = hydrate_sidecar_ref(tmp_path, facts[0]).payload
    assert control['verdict'] == 'rejected' and control['findings'][0]['status'] == 'open'
    assert build_plan_handoff_input_refs(**deepcopy(args)) == sources
    current = {'source_id': 'child-result-new', 'artifact_id': 'new.json', 'sha256': 'd' * 64, 'ref': 'new.json'}
    rows = build_canonical_required_reads({'sources': [*sources, current]}, output_profile_id='plan-synth')
    assert {(r['source_id'], r['json_path']) for r in rows} >= {('plan-rejected-review', '$'), ('child-result-new', '$')}
