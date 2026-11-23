"""Freeze host execution facts for independent Candidate consumers, not verdicts."""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
from typing import Any

from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.artifact_query.extractors import _intersect_access_scopes
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.host_evidence_media import materialize_container_media, required_media_paths
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref
from zf.runtime.task_host_evidence import host_execution_facts
from zf.runtime.workflow_operation import load_workflow_operation, reduce_workflow_operations


BOUND_FIELDS = ('workflow_run_id', 'task_id', 'task_map_generation', 'target_commit',
                'contract_snapshot_digest', 'target_snapshot_digest')
PREFIX = 'candidate-host-'


def _request(state_dir: Path, operation: dict) -> dict:
    return hydrate_sidecar_ref(state_dir, operation['request_ref']).payload['request']


def _scope(receipt: dict, descriptor: dict) -> dict:
    scope = dict(descriptor.get('access_scope') or {})
    for field in ('output_ref', 'evidence_ref'):
        scope = _intersect_access_scopes(scope, (receipt.get(field) or {}).get('access_scope'))
    return scope


def _source_fact(state_dir: Path, source: dict, request: dict, fact: dict,
                 consumer: dict, commands: dict) -> tuple[dict, dict] | None:
    """Keep the authenticated source identity; never rename it as a new probe."""
    if (request.get('output_profile_id') != 'candidate-verify'
            or any(request.get(k) != consumer.get(k) for k in BOUND_FIELDS[:4])
            or source.get('workflow_run_id') != consumer['workflow_run_id']
            or source.get('task_id') != consumer['task_id']):
        return None
    receipt = hydrate_sidecar_ref(state_dir, fact['receipt_ref']).payload
    if any(request.get(k) != consumer.get(k) for k in BOUND_FIELDS[4:]):
        from zf.runtime.candidate_execution_equivalence import same_execution_contract
        if not same_execution_contract(state_dir, request, consumer):
            return None
    if receipt.get('execution_origin') == 'preauthorized_container':
        from zf.core.config.host_verification import parse_host_verification
        from zf.runtime.host_verification_policy import policy_receipt_matches
        policy = consumer.get('host_policy_snapshot')
        if (policy and not policy_receipt_matches(state_dir, parse_host_verification(policy), receipt)) or (
                not policy and request['contract_snapshot_digest'] != consumer['contract_snapshot_digest']):
            return None
    if any(ref.get('allowed_paths') not in (None, ['$']) for ref in (
            fact['receipt_ref'], receipt.get('output_ref') or {}, receipt.get('evidence_ref') or {})):
        raise ValueError('partial host source cannot grant full evidence access')
    manifest = receipt['manifest']
    if ('dependency_digest' in consumer
            and (not consumer['dependency_digest'] or manifest.get('dependency_digest') != consumer['dependency_digest'])):
        return None
    spec = commands.get(fact['command_id'])
    if (not spec or manifest.get('contract_digest') != request['contract_snapshot_digest']
            or fact['target_commit'] != consumer['target_commit']
            or any(manifest['command'].get(k) != spec.get(k)
                   for k in ('command_id', 'command', 'command_digest', 'producer_paths'))):
        return None
    scope = _scope(receipt, fact['receipt_ref'])
    require_artifact_access(scope, role=consumer['role_instance'],
                            actor=consumer['role_instance'], purpose='candidate-verify')
    return receipt, scope


def prepare_candidate_host_consumption(runtime: Any, *, payload: dict, operation_id: str,
                                        attempt_id: str) -> None:
    """Only new calls receive new evidence. Frozen legacy calls remain unchanged."""
    payload.pop('candidate_host_evidence_ref', None)
    payload['input_refs'] = [s for s in payload.get('input_refs', [])
        if not isinstance(s, dict) or not str(s.get('source_id', '')).startswith(PREFIX)]
    existing = load_workflow_operation(runtime.event_log, operation_id)
    if existing:
        ref = _request(runtime.state_dir, existing).get('candidate_host_evidence_ref')
        if ref:
            binding = hydrate_sidecar_ref(runtime.state_dir, ref).payload
            payload['candidate_host_evidence_ref'] = ref
            payload['input_refs'].extend(_inputs(ref, binding))
        return
    if not all(payload.get(k) for k in BOUND_FIELDS):
        return
    consumer = {k: payload[k] for k in BOUND_FIELDS}
    consumer.update(operation_id=operation_id, attempt_id=attempt_id,
                    role_instance=payload['role_instance'],
                    contract_snapshot_ref=payload.get('contract_snapshot_ref'),
                    target_snapshot_ref=payload.get('target_snapshot_ref'))
    policy = getattr(getattr(runtime.config, 'runtime', None), 'host_verification', None)
    if policy is not None:
        consumer['host_policy_snapshot'] = asdict(policy)
    from zf.runtime.candidate_execution_equivalence import consumer_dependency_digest
    try:
        consumer['dependency_digest'] = consumer_dependency_digest(runtime, payload)
    except (ValueError, OSError, KeyError, TypeError):
        consumer['dependency_digest'] = None
    contract = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': payload['contract_snapshot_ref'], 'sha256': payload['contract_snapshot_digest']}).payload
    commands = {c['command_id']: c for c in contract['verification_commands']}
    events = runtime.event_log.read_all()
    positions = {e.id: i for i, e in enumerate(events)}
    latest = {}
    for source in reduce_workflow_operations(events).values():
        if (source.get('workflow_run_id') != consumer['workflow_run_id']
                or source.get('task_id') != consumer['task_id']):
            continue
        try:
            request = _request(runtime.state_dir, source)
            for fact in host_execution_facts(runtime.state_dir, source, events):
                resolved = _source_fact(runtime.state_dir, source, request, fact, consumer, commands)
                if resolved:
                    old = latest.get(fact['command_id'])
                    if not old or positions[fact['event_id']] > positions[old[0]['event_id']]:
                        latest[fact['command_id']] = (fact, *resolved)
        except (ValueError, OSError, KeyError, TypeError):
            continue  # An unbound or inaccessible historical receipt grants nothing.
    if not latest:
        return
    policy = getattr(getattr(runtime.config, 'runtime', None), 'host_verification', None)
    receipts, inputs, grant_scope = [], [], {}
    for command_id, (fact, receipt, scope) in sorted(latest.items()):
        item, sources = _freeze_fact(runtime.state_dir, fact, receipt, scope,
                                    commands[command_id], policy)
        receipts.append(item)
        inputs.extend(sources)
        grant_scope = _intersect_access_scopes(grant_scope, scope)
    binding = {'schema_version': 'candidate-host-evidence.v1', 'consumer': consumer,
               'receipts': receipts, 'input_refs': inputs, 'authorizes_execution': False,
               'claims_status': 'execution_facts_only_independent_acceptance_required'}
    ref = write_immutable_json_sidecar(runtime.state_dir, binding,
        root='candidate-host-evidence', kind='candidate_host_evidence',
        schema_version='candidate-host-evidence.v1', created_by='kernel')
    ref['access_scope'] = grant_scope
    payload['candidate_host_evidence_ref'] = ref
    payload['input_refs'].extend(_inputs(ref, binding))


def _inputs(ref: dict, binding: dict) -> list[dict]:
    return [{**ref, 'source_id': PREFIX + 'execution', 'artifact_id': 'candidate-host-evidence.json',
             'allowed_paths': ['$']}, *binding['input_refs']]


def _freeze_fact(state_dir: Path, fact: dict, receipt: dict, scope: dict,
                 command: dict, policy: Any) -> tuple[dict, list[dict]]:
    suffix = fact['event_id']
    item = {'receipt_id': suffix, 'source_operation_id': fact['binding']['operation_id'],
            'source_attempt_id': fact['attempt_id'], 'receipt_ref': fact['receipt_ref'],
            'command_id': fact['command_id'], 'status': fact['status'], 'media_refs': []}
    sources = []
    try:
        indices = materialize_container_media(state_dir, [fact['receipt_ref']],
            image=receipt.get('image', ''), policy=policy)
        produced = set()
        for index, ref in enumerate(indices):
            ref = {**ref, 'access_scope': scope}
            item['media_refs'].append(ref)
            body = hydrate_sidecar_ref(state_dir, ref).payload
            produced.update(f['path'] for f in body['files'])
            sources.append({**ref, 'source_id': f'{PREFIX}media-{suffix}-{index}',
                            'artifact_id': 'container-media-index.json', 'allowed_paths': ['$']})
            sources.extend({**f, 'encoding': 'utf-8' if f['content_type'] == 'application/json' else f['encoding'],
                            'access_scope': scope, 'source_id': f'{PREFIX}file-{suffix}-{i}',
                            'artifact_id': f['path'], 'allowed_paths': ['$']}
                           for i, f in enumerate(body['files']))
        if not required_media_paths(command) <= produced:
            raise ValueError('required host media is missing')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        item.update(status='unavailable', reason=str(exc))
    body = {**fact, 'consumption_status': item['status'], 'media_refs': item['media_refs']}
    if item.get('reason'):
        body['consumption_reason'] = item['reason']
    ref = write_immutable_json_sidecar(state_dir, body, root='candidate-host-facts',
        kind='candidate_host_fact', schema_version='candidate-host-fact.v1',
        created_by='kernel', source_event_id=suffix)
    item['fact_ref'] = {**ref, 'access_scope': scope}
    sources.insert(0, {**item['fact_ref'], 'source_id': PREFIX + 'fact-' + suffix,
                       'artifact_id': 'candidate-host-fact.json', 'allowed_paths': ['$']})
    for field, value in (('receipt', fact['receipt_ref']), ('output', fact.get('source_ref')),
                         ('evidence', receipt.get('evidence_ref'))):
        if value:
            sources.append({**value, 'access_scope': scope, 'source_id': f'{PREFIX}original-{field}-{suffix}',
                            'artifact_id': Path(value['ref']).name, 'allowed_paths': ['$']})
    return item, sources


def admitted_host_commands(state_dir: Path, result: dict, *, operation: dict | None,
                            events: list | None) -> list[dict]:
    """Read-only validation against this consumer's immutable request and ledger."""
    ids = result.get('host_execution_receipt_ids') or []
    if not ids:
        return []
    if not operation or events is None:
        raise ValueError('host evidence requires an authenticated consumer operation')
    request = _request(state_dir, operation)
    ref = request.get('candidate_host_evidence_ref')
    if request.get('output_profile_id') != 'candidate-verify' or not ref:
        raise ValueError('consumer has no frozen host evidence binding')
    binding = hydrate_sidecar_ref(state_dir, ref).payload
    consumer = binding['consumer']
    if (binding.get('schema_version') != 'candidate-host-evidence.v1'
            or binding.get('authorizes_execution') is not False
            or any(consumer.get(k) != request.get(k) or result.get(k) != request.get(k)
                   for k in BOUND_FIELDS)
            or consumer.get('operation_id') != operation.get('operation_id')
            or consumer.get('attempt_id') != operation.get('active_attempt_id')
            or consumer.get('role_instance') != request.get('role_instance')
            or result.get('operation_id') != operation.get('operation_id')
            or result.get('attempt_id') != operation.get('active_attempt_id')
            or result.get('request_hash') != operation.get('request_hash')):
        raise ValueError('host evidence consumer binding mismatch')
    require_artifact_access(ref.get('access_scope'), role=consumer['role_instance'],
                            actor=consumer['role_instance'], purpose='candidate-verify')
    available = {r['receipt_id']: r for r in binding['receipts']}
    if len(available) != len(binding['receipts']) or any(i not in available for i in ids):
        raise ValueError('unknown or duplicate bound host receipt')
    contract = hydrate_sidecar_ref(state_dir, {
        'ref': result['contract_snapshot_ref'], 'sha256': consumer['contract_snapshot_digest']}).payload
    specs = {c['command_id']: c for c in contract['verification_commands']}
    operations = reduce_workflow_operations(events)
    covered = []
    for receipt_id in ids:
        row = available[receipt_id]
        source = operations.get(row['source_operation_id'])
        if not source:
            raise ValueError('host source operation missing')
        facts = [f for f in host_execution_facts(state_dir, source, events)
                 if f['event_id'] == receipt_id and f['receipt_ref'] == row['receipt_ref']]
        if len(facts) != 1:
            raise ValueError('host execution effect is missing, changed or ambiguous')
        fact = facts[0]
        resolved = _source_fact(state_dir, source, _request(state_dir, source), fact, consumer, specs)
        if not resolved or fact['attempt_id'] != row['source_attempt_id']:
            raise ValueError('host evidence source binding mismatch')
        receipt, _ = resolved
        retained = hydrate_sidecar_ref(state_dir, row['fact_ref']).payload
        if (any(retained.get(k) != v for k, v in fact.items())
                or fact['status'] != 'available' or row['status'] != 'available'):
            raise ValueError('host execution output or required media is unavailable')
        if receipt.get('evidence_ref'):
            hydrate_sidecar_ref(state_dir, receipt['evidence_ref'], max_bytes=200 * 1024 * 1024)
        produced = set()
        for media in row['media_refs']:
            index = hydrate_sidecar_ref(state_dir, media).payload
            if (index.get('source_evidence_ref') != receipt.get('evidence_ref')
                    or index.get('target_commit') != consumer['target_commit']):
                raise ValueError('host media binding mismatch')
            for file in index['files']:
                verify_sidecar_ref(state_dir, file)
                produced.add(file['path'])
        spec = specs[fact['command_id']]
        if not required_media_paths(spec) <= produced:
            raise ValueError('required host media is missing')
        if result.get('verdict') == 'passed' and (receipt['status'] != 'completed' or fact['exit_code'] != 0):
            raise ValueError('failed host execution cannot support a passed verdict')
        covered.append(spec)
    return covered
