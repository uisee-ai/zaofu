"""Resolve unchanged Task evidence carried by a native mixed-generation freeze.

These are original Task results, never a verdict over the new Candidate target.
The freezer already admits each integrated Task frontier independently; readers
and result admission must use that same frontier instead of the global Plan id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.candidate_continuation import _json
from zf.runtime.candidate_result_binding import candidate_task_source_commits
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_contexts import (
    latest_task_pipeline_generation_context, task_pipeline_generation_contexts,
)


def candidate_child_proofs(runtime: Any, events: list, candidate: Any) -> dict:
    from zf.runtime.terminal_completion_proofs import compensated_completion_view

    runtime, restorations = compensated_completion_view(runtime, events, candidate)
    from zf.runtime.plan_completion_reuse import current_proofs, _native_proof
    from zf.runtime.candidate_verification_authority import _latest_task_verification
    from zf.runtime.task_pipeline_terminal import _latest_integration_event

    proofs = current_proofs(runtime, events, candidate)
    for task_id in set(proofs) & set(restorations):
        proofs[task_id]['completion_restore_ref'] = restorations[task_id]
    before = events[:events.index(candidate)]
    contexts = task_pipeline_generation_contexts(before)
    body = candidate.payload
    run = str(body.get('workflow_run_id') or '')
    generation = str(body.get('task_map_generation') or '')
    retained = {task_id: contexts[task_id] for task_id in body.get('completed_task_ids', [])
                if task_id not in proofs and task_id in contexts
                and contexts[task_id].get('workflow_run_id') == run
                and contexts[task_id].get('task_map_generation') != generation}
    if not retained:
        return proofs
    state = Path(runtime.state_dir)
    freeze = _json(state, body.get('freeze_receipt_ref') or {})
    latest = latest_task_pipeline_generation_context(before, workflow_run_id=run)
    if (candidate.origin != 'kernel' or freeze.get('schema_version') != 'candidate-freeze-receipt.v1'
            or freeze.get('status') != 'frozen' or not latest
            or latest.get('task_map_generation') != generation
            or latest.get('generation_id') != body.get('generation_id')
            or body.get('freeze_receipt_digest') != body.get('freeze_receipt_ref', {}).get('sha256')
            or any(body.get(key) != value for key, value in freeze.items())):
        raise ValueError('retained Task evidence requires the exact native Candidate freeze')
    current_contexts = task_pipeline_generation_contexts(events)
    from zf.runtime.workflow_operation import reduce_workflow_operations

    active = {op.get('task_id') for op in reduce_workflow_operations(events).values()
              if op.get('workflow_run_id') == run
              and op.get('status') in {'requested', 'reserved', 'running', 'suspended'}}
    roster = {task_id for task_id, context in contexts.items() if context.get('workflow_run_id') == run}
    if (roster != set(freeze.get('task_ids', []))
            or set(freeze.get('completed_task_ids', [])) | set(freeze.get('superseded_task_ids', [])) != roster
            or len(freeze.get('completed_task_ids', [])) != len(set(freeze.get('completed_task_ids', [])))):
        raise ValueError('retained Candidate Task roster differs from the frozen frontier')
    commits = candidate_task_source_commits(events, workflow_run_id=run,
        candidate_head_commit=body['candidate_head_commit'])
    for task_id, context in retained.items():
        task = runtime.task_store.get(task_id)
        if (context != current_contexts.get(task_id) or task is None or task.status != 'done'
                or task.active_dispatch_id or task_id in active):
            raise ValueError('retained Task frontier changed: ' + task_id)
        plan_row = _unchanged_plan_task(state, task, body)
        integrated = _latest_integration_event(before, task_id=task_id, context=context)
        descriptor = integrated.payload.get('receipt_ref') if integrated else None
        if (not integrated or integrated.origin != 'kernel'
                or not descriptor or freeze.get('task_integration_receipt_refs', []).count(descriptor) != 1):
            raise ValueError('retained Task lacks frozen integration evidence: ' + task_id)
        receipt = _json(state, descriptor)
        child_generation = str(context['task_map_generation'])
        verify = _latest_task_verification(before, workflow_run_id=run, task_id=task_id,
            task_map_generation=child_generation, target_commit=commits.get(task_id, ''))
        done = next((e for e in reversed(before) if e.type == 'task.done' and e.origin == 'kernel'
            and e.task_id == task_id and e.payload.get('workflow_run_id') == run
            and e.payload.get('task_map_generation') == child_generation
            and e.payload.get('integration_receipt_ref') == descriptor), None)
        if (verify is None or done is None or receipt.get('task_commit') != commits.get(task_id)
                or receipt.get('task_map_generation') != child_generation):
            raise ValueError('retained Task lacks original Verify/completion: ' + task_id)
        _native_proof(state, before, done, verify)
        proof = {'task_map_generation':child_generation,
            'verification_event_id':verify.id, 'prior_identity':current_task_contract_identity(task)}
        retained_evidence = {'generation_admitted_event_id':context['generation_admitted_event_id'],
            'completion_event_id':done.id, 'verification_event_id':verify.id,
            'integration_receipt_ref':descriptor}
        if context.get('completion_reuse_ref'):
            previous, original_candidate = _prior_reviewed_proof(runtime, before, context, task_id)
            if (previous['verification_event_id'] != verify.id
                    or previous['completion_event_id'] != done.id
                    or previous['task_map_generation'] != child_generation):
                raise ValueError('retained Task reviewed source differs: ' + task_id)
            proof.update(previous)
            retained_evidence['reviewed_candidate_event_id'] = original_candidate.id
        snapshot = _verify_snapshots(state, verify, proof['prior_identity'], run)
        _checked_integration(runtime, descriptor, body, task, child_generation, snapshot, plan_row)
        proof['retained_task_evidence'] = retained_evidence
        if task_id in restorations:
            proof['completion_restore_ref'] = restorations[task_id]
        proofs[task_id] = proof
    return proofs


def _verify_snapshots(state: Path, verify: Any, identity: dict, run: str) -> dict:
    from zf.runtime.task_contract_snapshot import hydrate_task_contract_snapshot, hydrate_target_snapshot

    payload = verify.payload
    contract_ref = {'ref':payload.get('contract_snapshot_ref'), 'sha256':payload.get('contract_snapshot_digest')}
    snapshot = hydrate_task_contract_snapshot(state, contract_ref,
        expected={**identity, 'workflow_run_id':run})
    hydrate_target_snapshot(state,
        {'ref':payload.get('target_snapshot_ref'), 'sha256':payload.get('target_snapshot_digest')},
        expected={**{key:snapshot.get(key) for key in ('workflow_run_id','task_id','contract_revision',
            'task_map_generation','base_commit','task_ref','plan_artifact_package_id',
            'plan_artifact_package_ref','plan_artifact_package_digest')},
            'contract_snapshot_ref':contract_ref['ref'], 'contract_snapshot_digest':contract_ref['sha256'],
            'target_commit':payload.get('target_commit')})
    return snapshot


def _checked_integration(runtime: Any, descriptor: dict, candidate: dict, task: Any,
                         generation: str, snapshot: dict, plan_row: dict) -> None:
    from subprocess import TimeoutExpired
    from yaml import YAMLError
    from zf.core.state.session import SessionStore, ZfNotInitialized
    from zf.runtime.candidates import CandidateRebuilder
    from zf.runtime.candidate_incremental import (
        CandidateIncrementalError, hydrate_task_integration_receipt, _resolve_commit,
    )
    from zf.runtime.workflow_git_readiness import git_read

    state = Path(runtime.state_dir)
    root = getattr(runtime, 'project_root', None)
    if root is None:
        try:
            root = SessionStore(state / 'session.yaml').load().project_root
        except (ZfNotInitialized, YAMLError, TypeError, OSError) as exc:
            raise ValueError('retained Candidate requires its canonical project binding') from exc
    if not isinstance(root, (str, Path)) or not root:
        raise ValueError('retained Candidate project binding is empty')
    root = Path(root)
    rebuilder = CandidateRebuilder(state_dir=state, project_root=root,
        config=getattr(runtime, 'config', None), event_log=getattr(runtime, 'event_log', None))
    try:
        receipt = hydrate_task_integration_receipt(rebuilder, descriptor, task_id=task.id,
            workflow_run_id=candidate['workflow_run_id'], task_map_generation=generation)
        candidate_commit = _resolve_commit(root, candidate['candidate_ref'])
        task_commit = _resolve_commit(root, receipt['task_ref'])
    except (CandidateIncrementalError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    if (candidate_commit != candidate['candidate_head_commit']
            or task_commit != receipt['task_commit']):
        raise ValueError('retained Task or Candidate Git ref moved: ' + task.id)
    paths = list(dict.fromkeys([*snapshot.get('allowed_paths', []),
        *plan_row.get('verification_read_paths', []),
        *task.contract.evidence_contract.get('verification_read_paths', [])]))
    if not paths:
        raise ValueError('retained Task lacks declared source/read scope: ' + task.id)
    try:
        unchanged = git_read(root, 'diff', '--quiet', receipt['new_candidate_head'],
            candidate['candidate_head_commit'], '--', *paths)
    except (TimeoutExpired, OSError) as exc:
        raise ValueError('retained Task input currentness could not be checked: ' + task.id) from exc
    if unchanged.returncode == 1:
        raise ValueError('retained Task declared input changed in Candidate: ' + task.id)
    if unchanged.returncode:
        raise ValueError('retained Task input currentness could not be checked: ' + task.id)


def _prior_reviewed_proof(runtime: Any, events: list, context: dict, task_id: str) -> tuple:
    from zf.runtime.plan_completion_reuse import current_proofs

    descriptor = context['completion_reuse_ref']
    manifest = _json(Path(runtime.state_dir), descriptor).get('manifest') or {}
    candidate = next((e for e in reversed(events) if e.type == 'candidate.ready' and e.origin == 'kernel'
        and e.payload.get('workflow_run_id') == manifest.get('workflow_run_id')
        and e.payload.get('task_map_generation') == manifest.get('task_map_generation')
        and e.payload.get('candidate_head_commit') == manifest.get('candidate_head_commit')
        and e.payload.get('candidate_base_commit') == manifest.get('candidate_base_commit')
        and e.payload.get('plan_artifact_package_digest') == manifest.get('plan_package_ref', {}).get('sha256')
        and task_id in e.payload.get('completed_task_ids', [])), None)
    if candidate is None:
        raise ValueError('retained Task reviewed Candidate is unavailable: ' + task_id)
    body = candidate.payload
    freeze = _json(Path(runtime.state_dir), body.get('freeze_receipt_ref') or {})
    if (freeze.get('schema_version') != 'candidate-freeze-receipt.v1'
            or body.get('freeze_receipt_digest') != body.get('freeze_receipt_ref', {}).get('sha256')
            or any(freeze.get(key) != body.get(key) for key in (
                'freeze_id','workflow_run_id','task_map_generation','candidate_head_commit',
                'candidate_base_commit','completed_task_ids','plan_artifact_package_digest'))):
        raise ValueError('retained Task reviewed freeze differs: ' + task_id)
    proof = current_proofs(runtime, events[:events.index(candidate)+1], candidate,
        task_ids={task_id}).get(task_id)
    if not proof or proof['completion_reuse_ref'] != descriptor:
        raise ValueError('retained Task lacks its exact reviewed proof: ' + task_id)
    return proof, candidate


def _unchanged_plan_task(state: Path, task: Any, candidate: dict) -> dict:
    """An out-of-scope row cannot quietly introduce a different Task contract."""
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package

    prior = task.contract.evidence_contract.get('source_refs', {})
    if not candidate.get('plan_artifact_package_ref'):
        if (candidate.get('plan_artifact_package_digest') or candidate.get('plan_artifact_package_id')
                or prior.get('plan_artifact_package_ref')):
            raise ValueError('retained Candidate Plan package binding is incomplete')
        return {}  # Legacy freezes still bind Task authority and declared write scope.

    def row(binding: dict) -> dict:
        package = hydrate_plan_artifact_package(state, {
            'ref':binding.get('plan_artifact_package_ref', ''),
            'sha256':binding.get('plan_artifact_package_digest', '')})
        if (package.get('workflow_run_id') != candidate.get('workflow_run_id')
                or package.get('task_map_generation') != binding.get('task_map_generation')):
            raise ValueError('retained Task Plan belongs to another Run or generation')
        ports = [port for port in [*package.get('produced', []), *package.get('inherited', [])]
                 if port.get('logical_name') == 'task_map']
        if len(ports) != 1:
            raise ValueError('retained Task requires one authoritative Task Map')
        rows = [r for r in _json(state, ports[0]).get('tasks', []) if r.get('task_id') == task.id]
        if len(rows) != 1:
            raise ValueError('retained Task missing or duplicated in Plan: ' + task.id)
        return rows[0]

    previous = row(prior)
    if previous != row(candidate):
        raise ValueError('retained Task differs from the approved Plan; refresh required: ' + task.id)
    return previous
