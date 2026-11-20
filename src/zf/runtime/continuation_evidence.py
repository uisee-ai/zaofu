"""Digest-bound historical inputs shared by continuation reviewers.

This is an evidence index, not a qualification verdict. Preserve original Run,
Task and event identities; never translate historical execution into new work.
"""
from __future__ import annotations

from pathlib import Path

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_continuation import closure_provenance
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def continuation_input_refs(state_dir: Path, events: list, workflow_run_id: str) -> list[dict]:
    history = closure_provenance(events, state_dir=state_dir, workflow_run_id=workflow_run_id)
    if not history:
        return []
    receipt = hydrate_sidecar_ref(state_dir, history['receipt_ref']).payload
    manifest = receipt['manifest']
    source_run = history['source_run_id']
    tasks = {p['task_id']: p for p in history['task_proofs']}
    by_id = {e.id: e for e in events}
    positions = {e.id: i for i, e in enumerate(events)}
    terminal = by_id[history['source_terminal_event_id']]
    source_events = events[:events.index(terminal) + 1]
    refs: list[dict] = []

    def add(ref: dict, source_id: str) -> dict:
        hydrated = hydrate_sidecar_ref(state_dir, ref)
        if not hydrated.ok or not isinstance(hydrated.payload, dict):
            raise ValueError('invalid historical continuation evidence: ' + source_id)
        refs.append({**ref, 'source_id': source_id, 'allowed_paths': ['$']})
        return hydrated.payload

    def snapshot(body: dict, source_id: str) -> dict:
        descriptor = write_immutable_json_sidecar(state_dir, body,
            root='continuation-evidence', kind='historical_execution_evidence',
            schema_version='continuation-evidence.v1', created_by='canonical-handoff-resolver')
        refs.append({**descriptor, 'source_id': source_id, 'allowed_paths': ['$']})
        return descriptor

    for task, proof in tasks.items():
        add(proof['control_result_ref'], 'continuation-verify-control-' + task)
        add(proof['envelope_ref'], 'continuation-verify-envelope-' + task)

    admitted_impl = []
    for e in source_events:
        p = e.payload
        if (e.type != 'workflow.call.result.admitted' or e.origin != 'kernel'
                or p.get('workflow_run_id') != source_run
                or p.get('control_result_schema') != 'implementation-result.v1'
                or str(p.get('task_id') or e.task_id or '') not in tasks):
            continue
        task = str(p.get('task_id') or e.task_id)
        ref = p.get('control_result_ref')
        if not isinstance(ref, dict):
            continue
        result = hydrate_sidecar_ref(state_dir, ref).payload
        if result.get('workflow_run_id') != source_run or result.get('task_id') != task:
            raise ValueError('historical implementation identity mismatch')
        admitted_impl.append((e, task, ref, result))

    implementations = []
    captured_attempts = {}
    for e, task, ref, result in admitted_impl:
        if e.payload.get('semantic_verdict') != 'passed':
            continue
        if result.get('target_commit') != tasks[task]['target_commit']:
            continue
        add(ref, 'continuation-impl-' + e.id)
        # Only referenced tool facts from the admitted implementation are exposed.
        # Missing or foreign references stay missing; no guessed replacement.
        from zf.runtime.task_execution_evidence import capture_execution_evidence
        check = result.get('self_check') or {}
        evidence = []
        for raw in check.get('evidence_refs', []):
            if not isinstance(raw, str) or not raw.startswith('event:'):
                continue
            fact = by_id.get(raw.removeprefix('event:'))
            prior = None
            entry = None
            if fact and positions[fact.id] < positions[e.id]:
                for admission, prior_task, _, body in admitted_impl:
                    if prior_task != task or positions[admission.id] > positions[e.id]:
                        continue
                    if admission.id not in captured_attempts:
                        dispatch = next((d for d in source_events[:positions[admission.id]]
                            if d.origin == 'kernel' and d.type == 'task.pipeline.stage.dispatched'
                            and d.payload.get('task_map_generation') == manifest['task_map_generation']
                            and all(body.get(k) and body[k] == d.payload.get(k) for k in
                                ('workflow_run_id', 'task_id', 'operation_id', 'attempt_id', 'role_instance'))), None)
                        entries = {}
                        if dispatch:
                            captured = hydrate_sidecar_ref(state_dir, capture_execution_evidence(
                                state_dir, {**dispatch.payload, 'active_attempt_id': body['attempt_id'],
                                    'last_event_id': admission.id}, source_events,
                            )).payload
                            entries = {row['event_id']: row for row in captured['entries']}
                        captured_attempts[admission.id] = entries
                    entry = captured_attempts[admission.id].get(fact.id)
                    if entry:
                        prior = admission, body
                        break
            if entry is None:
                evidence.append({'event_ref': raw, 'status': 'unavailable',
                    'event_type': fact.type if fact else None,
                    'reason': 'not_a_bound_execution_observation' if fact else 'event_missing'})
                continue
            evidence.append({'event_ref': raw, 'event_type': fact.type,
                'timestamp': fact.ts, 'actor': fact.actor, 'origin': fact.origin,
                'source_admission_event_id': prior[0].id,
                'source_semantic_verdict': prior[0].payload.get('semantic_verdict'),
                'source_target_commit': prior[1].get('target_commit'),
                'source_control_result_ref': prior[0].payload['control_result_ref'],
                'scope': 'historical_task_observation_not_current_candidate_verdict',
                'causation_id': fact.causation_id, 'observation_ref': entry['ref'],
                'observation': hydrate_sidecar_ref(state_dir, entry['ref']).payload})
        tool_ref = snapshot({'schema_version': 'continuation-evidence.v1',
            'source_run_id': source_run, 'task_id': task, 'admission_event_id': e.id,
            'control_result_ref': ref, 'target_commit': result.get('target_commit'),
            'referenced_tool_facts': evidence}, 'continuation-tool-facts-' + task)
        implementations.append({'task_id': task, 'admission_event_id': e.id,
            'control_result_ref': ref, 'tool_facts_ref': tool_ref})

    # Dispatch and native hook occurrences are observations, not inference/GPU concurrency.
    bindings = []
    observations = []
    for e in source_events:
        p = e.payload
        if (e.type == 'task.pipeline.stage.dispatched' and e.origin == 'kernel'
                and p.get('workflow_run_id') == source_run and p.get('task_id') in tasks
                and p.get('task_map_generation') == manifest['task_map_generation']):
            binding = {k: p[k] for k in ('task_id', 'operation_id', 'attempt_id', 'dispatch_id',
                'task_pipeline_stage', 'role_instance', 'workdir', 'provider_session_id') if k in p}
            binding.update(event_id=e.id, timestamp=e.ts)
            bindings.append(binding)
        if e.type not in {
                'codex.hook.user_prompt_submit', 'codex.hook.stop', 'codex.hook.session_end'}:
            continue
        matches = [b for b in bindings if b['event_id'] == e.causation_id
                   and e.correlation_id == source_run and b.get('role_instance') == e.actor
                   and b.get('workdir') and b['workdir'] == p.get('cwd')]
        if not matches:
            continue
        observations.append({'event_id': e.id, 'timestamp': e.ts,
            'event_type': e.type, 'actor': e.actor, 'origin': e.origin,
            'dispatch_event_id': matches[-1]['event_id'],
            'payload': {k: p[k] for k in ('cwd', 'session_id', 'turn_id', 'hook_event') if k in p}})
    activity = snapshot({'schema_version': 'continuation-evidence.v1', 'source_run_id': source_run,
        'task_map_generation': manifest['task_map_generation'], 'dispatches': bindings,
        'observations': observations,
        'scope': 'Original bound dispatch/hook observations; no successor concurrency or final verdict asserted.'},
        'continuation-source-activity')
    snapshot({'schema_version': 'continuation-evidence.v1', **history,
        'workflow_run_id': workflow_run_id, 'candidate_head_commit': manifest['candidate_head_commit'],
        'task_map_generation': manifest['task_map_generation'], 'approval_ref': receipt['approval_ref'],
        'execution_scope': manifest['rerun_policy'],
        'qualification_policy': 'historical_facts_are_not_successor_execution_or_verdicts',
        'acceptance_policy': 'Keep original product claims. Reconcile applicability of original execution '
            'constraints against the authorized continuation scope. Report unresolved scope separately; '
            'never infer unattended success or waive a requirement from a successful product check.',
        'implementations': implementations, 'source_activity_ref': activity,
        'owner_recovery_reviews': _owner_recovery_reviews(events, workflow_run_id),
    }, 'critical-continuation-context')
    return refs


def _owner_recovery_reviews(events: list, run_id: str) -> list[dict]:
    """Carry successful controlled retry feedback, never loose agent payloads."""
    by_id = {e.id: e for e in events}
    reviews = []
    for event in events:
        p = event.payload
        if (event.origin != 'kernel' or event.type != 'runtime.action.completed'
                or p.get('action') != 'stage-replan-new-generation' or p.get('status') != 'applied'):
            continue
        trigger = by_id.get(p.get('stage_trigger_event_id'))
        if (trigger is None or trigger.payload.get('workflow_run_id') != run_id
                or not trigger.payload.get('operator_feedback')):
            continue
        reviews.append({'action_event_id': event.id, 'trigger_event_id': trigger.id,
            'repaired_failure_event_id': trigger.payload.get('repaired_failure_event_id'),
            'operator_feedback': trigger.payload['operator_feedback'],
            'scope': 'Owner recovery review; not a product verdict or retrospective unattended qualification.'})
    return reviews
