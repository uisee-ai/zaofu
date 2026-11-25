"""Return incomplete diagnosis to the existing Run Manager decision owner."""
from hashlib import sha256

from zf.core.events.model import ZfEvent


REQUEST = 'run.manager.autoresearch.requested'
RESULTS = {'autoresearch.loop.failed', 'autoresearch.loop.completed'}


def diagnosis_request_for_receipt(receipt: ZfEvent, requests: list[ZfEvent]) -> ZfEvent | None:
    """Match an earlier request only when identity and any supplied scopes agree."""
    from zf.runtime.run_manager import _autoresearch_request_id

    if receipt.origin not in {'kernel', ''} or not isinstance(receipt.payload, dict):
        return None
    p = receipt.payload
    explicit_id = next((p[k] for k in ('run_manager_request_id', 'request_id', 'loop_request_id',
                                        'source_request_id') if p.get(k)), '')
    run = p.get('workflow_run_id') or p.get('run_id')
    causal = next((r for r in requests if receipt.causation_id == r.id), None)
    candidates = []
    for request in [causal] if causal else requests:
        original = request.payload
        request_id = _autoresearch_request_id(request, original)
        request_run = original.get('workflow_run_id') or original.get('run_id')
        if ((explicit_id and explicit_id != request_id) or (run and request_run and run != request_run)
                or (p.get('request_event_id') and p['request_event_id'] != request.id)
                or any(p.get(k) and original.get(k) and p[k] != original[k]
                       for k in ('checkpoint_id', 'recovery_case_id', 'task_map_generation', 'task_id'))):
            continue
        if (explicit_id or p.get('request_event_id') == request.id or receipt.causation_id == request.id
                or receipt.correlation_id == request_id
                or (p.get('checkpoint_id') and p['checkpoint_id'] == original.get('checkpoint_id'))):
            candidates.append(request)
    return candidates[0] if len(candidates) == 1 else None


def _resident_diagnosis_for_reply(events: list[ZfEvent], action: dict, route: str) -> ZfEvent | None:
    """Resolve a recommendation to its earlier same-Run kernel delivery and effect receipt."""
    run = action.get('workflow_run_id')
    sources = action.get('source_event_ids') or []
    if not run or len(sources) != 1 or action.get('checkpoint_id') != 'recommendation:' + sources[0]:
        return None
    reply = next((e for e in events if e.id == sources[0]), None)
    if (not reply or reply.type != 'run.manager.agent.recommendation'
            or reply.payload.get('recommended_route') != route or not reply.payload.get('request_id')):
        return None
    prior = events[:events.index(reply)]
    request_id = reply.payload['request_id']
    pending = [e for e in prior if e.type == 'run.manager.action.effect.pending'
               and e.origin == 'kernel' and e.actor == 'run-manager' and e.payload.get('request_id') == request_id
               and e.payload.get('workflow_run_id') == run and e.payload.get('request_kind') == 'recovery_diagnosis'
               and reply.type in (e.payload.get('continuation_expected_events') or [])]
    if len(pending) != 1:
        return None
    receipt = pending[0]
    body = receipt.payload
    if not body.get('checkpoint_id') or body.get('recovery_case_id') != action.get('recovery_case_id'):
        return None
    for e in reversed(prior[:prior.index(receipt)]):
        if (e.type != 'run.manager.resident.prompted' or e.origin != 'kernel' or e.actor != 'run-manager'
                or e.payload.get('request_id') != request_id or e.payload.get('instance_id') != reply.actor
                or e.payload.get('source_action_checkpoint_id') != body['checkpoint_id']):
            continue
        diagnosis = e.payload.get('diagnosis_context') or {}
        if (diagnosis.get('request_kind') == 'recovery_diagnosis' and diagnosis.get('workflow_run_id') == run
                and diagnosis.get('source_event_ids') == body.get('source_event_ids')):
            return receipt
    return None


def resident_reflection_followup(events: list[ZfEvent], action: dict) -> dict:
    """Carry the same unfinished diagnosis to the reflection the resident actually requested."""
    pending = _resident_diagnosis_for_reply(events, action, 'reflect')
    if not pending or pending.payload.get('failure_class') != 'autoresearch_diagnosis_incomplete':
        return {}
    receipt = pending.payload
    return next((followup for followup in pending_autoresearch_followups(events)
                 if followup['workflow_run_id'] == receipt['workflow_run_id']
                 and followup['recovery_case_id'] == receipt['recovery_case_id']
                 and followup['checkpoint_id'] == receipt['checkpoint_id']
                 and set(receipt.get('source_event_ids') or []).issubset(followup['source_event_ids'])), {})


def _unresolved_reader(events: list[ZfEvent], index: int, request: ZfEvent, result: ZfEvent) -> ZfEvent | None:
    """A runner exit needs another owner only for its still-open, evidenced Reader fault."""
    from zf.autoresearch.repair_preparation import build_repair_preparation
    from zf.runtime.diagnosis_reader_rebuild import _diagnosed_child
    from zf.runtime.run_continuation import blocking_decisions_for_action
    from zf.runtime.run_manager import _pending_human_decisions
    from zf.runtime.run_scope import events_for_run
    from zf.runtime.terminal_events import terminal_after_event
    from zf.runtime.workflow_operation import reduce_workflow_operations

    original = request.payload
    run = original.get('workflow_run_id') or original.get('run_id')
    sources = set(original.get('source_event_ids') or [])
    if not run or not sources or terminal_after_event(events, request):
        return None
    prior = events[:index]
    if request.origin == 'kernel' and request.actor == 'run-manager':
        pending = _resident_diagnosis_for_reply(prior, original, 'autoresearch')
        if pending:
            sources.update(pending.payload.get('source_event_ids') or [])
    failure = _diagnosed_child(prior, sources, run)
    if not failure:
        return None
    child = failure.payload
    fanout, child_id, attempt = (child.get(k) for k in ('fanout_id', 'child_id', 'run_id'))
    dispatch = next((e for e in reversed(prior) if e.origin == 'kernel' and e.type == 'fanout.child.dispatched'
                     and e.payload.get('workflow_run_id') == run and e.payload.get('fanout_id') == fanout
                     and e.payload.get('child_id') == child_id and e.payload.get('run_id') == attempt), None)
    if not dispatch:
        return None
    operation_id = dispatch.payload.get('operation_id')
    operation = reduce_workflow_operations(prior).get(operation_id, {})
    if (operation.get('operation_type') != 'fanout_reader_child' or operation.get('workflow_run_id') != run
            or (original.get('task_id') and original['task_id'] != operation.get('task_id'))):
        return None

    if any(e.type == 'human.escalate' for e in events):
        scoped = events_for_run(events, run_id=run)
        action = {'action': 'diagnose-attention', 'workflow_run_id': run, 'task_id': operation.get('task_id')}
        if blocking_decisions_for_action(_pending_human_decisions(scoped), action, events=scoped):
            return None

    # A successor actually dispatched for this failed call owns the next outcome.
    # A sibling, a stale call's receipt or a proposed retry cannot transfer ownership.
    for event in events[events.index(failure) + 1:]:
        p = event.payload
        if event.origin != 'kernel':
            continue
        if (event.type == 'fanout.child.dispatched' and p.get('workflow_run_id') == run
                and p.get('fanout_id') == fanout and p.get('child_id') == child_id
                and (p.get('payload') or {}).get('retry_of_operation_id') == operation_id
                and p.get('operation_id') and p['operation_id'] != operation_id):
            return None
        if (event.type == 'autoresearch.bug_candidate.confirmed' and event.actor == 'run-manager'
                and event.causation_id == result.id and build_repair_preparation(p) is not None
                and p.get('recovery_case_id') == original.get('recovery_case_id')):
            return None
    return failure


def followup_escalation(action: dict, events: list[ZfEvent], *, request_id: str,
                        cause: str) -> tuple[dict, str, str]:
    """Preserve the bound RM reflection without promoting its advice into an action."""
    from zf.runtime.run_manager_reflection import COMPLETED, REQUESTED

    run = action.get('workflow_run_id') or action.get('run_id')
    checkpoint = action.get('checkpoint_id')
    sources = action.get('source_event_ids') or []
    request = None
    for event in events:
        if event.type not in {REQUESTED, COMPLETED} or event.origin != 'kernel' or event.actor != 'run-manager':
            continue
        p = event.payload
        if (not run or not checkpoint or not sources or p.get('workflow_run_id') != run
                or p.get('checkpoint_id') != checkpoint or p.get('request_id') != request_id
                or event.correlation_id != request_id or p.get('operation_id') != 'wop-' + request_id
                or p.get('source_event_ids') != sources or p.get('apply_policy') != 'proposal_only'):
            continue
        if event.type == REQUESTED:
            request = event
        elif request is not None:
            status = p.get('status')
            if status in {'failed', 'timeout', 'cancelled', 'superseded'}:
                reason = f'Run Manager reflection {status}. Autoresearch follow-up requires an owner decision.'
            elif p.get('recommended_route') == 'wait':
                reason = ('Run Manager reflection recommended waiting. Autoresearch follow-up '
                          'requires a resumption event or an authorized next action.')
            else:
                reason = ('Run Manager reflection returned a recommendation. Autoresearch follow-up '
                          'requires an authorized next action.')
            evidence = list(dict.fromkeys([*sources, request.id, event.id]))
            return {**action, 'source_event_ids': evidence}, reason, event.id
    if request is not None:
        return ({**action, 'source_event_ids': [*sources, request.id]},
                'Run Manager reflection was requested but has no matching outcome. Autoresearch follow-up requires an owner decision.',
                request.id)
    return action, 'Autoresearch follow-up requires an available Run Manager decision owner', cause


def pending_autoresearch_followups(events: list[ZfEvent]) -> list[dict]:
    from zf.runtime.run_manager import _autoresearch_request_id

    requests = []
    first_requests = {}
    indexes = {}
    terminal = {}
    for index, event in enumerate(events):
        if event.type == REQUEST and event.origin in {'kernel', ''}:
            requests.append(event)
            indexes[event.id] = index
            first_requests.setdefault(_autoresearch_request_id(event, event.payload), event)
        elif event.type in RESULTS:
            source = diagnosis_request_for_receipt(event, requests)
            if source:
                previous = terminal.get(source.id)
                if not previous or previous[1].type != 'autoresearch.loop.completed':
                    terminal[source.id] = (source, event)
        elif (event.type == 'run.manager.action.failed' and event.actor == 'run-manager'
              and event.origin in {'', 'kernel'} and event.payload.get('failure_class') == 'diagnosis_consumer_timeout'):
            source = diagnosis_request_for_receipt(event, requests)
            if (source and source.id == event.causation_id and source.actor == 'run-manager'
                    and source.origin in {'', 'kernel'}):
                terminal.setdefault(source.id, (source, event))
    out = []
    for request, event in terminal.values():
        request_id = _autoresearch_request_id(request, request.payload)
        diagnosis = event.payload.get('diagnosis')
        diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
        original = request.payload
        failure = _unresolved_reader(events, indexes[request.id], request, event)
        if event.type == 'autoresearch.loop.completed':
            if not failure:
                continue
            if not diagnosis:
                diagnosis = {'status': 'unknown', 'failure_class': 'diagnosis_missing',
                             'missing_evidence': 'actionable diagnosis of the bound Reader failure'}
        if event.type == 'run.manager.action.failed':
            diagnosis = {'status': 'unknown', 'failure_class': 'diagnosis_consumer_timeout',
                         'missing_evidence': 'bound diagnosis result'}
        if (diagnosis.get('status') == 'waiting' and diagnosis.get('diagnosis_owner') == 'owner'
                and (event.type != 'autoresearch.loop.completed'
                     or (diagnosis.get('pending_decisions') and diagnosis.get('resume_condition')))):
            continue  # An explicit owner decision retains its authorized path.
        run_id = original.get('workflow_run_id') or original.get('run_id') or ''
        # The original occurrence retains its legacy checkpoint after an upgrade.
        # A later same-name incident cannot consume that historical owner's decision.
        key = request_id if first_requests[request_id].id == request.id else request.id
        checkpoint = 'ar-outcome-' + sha256(key.encode()).hexdigest()[:16]
        sources = [failure.id] if failure else []
        sources.extend(e.id for e in events[:indexes[request.id]] if e.origin == 'kernel'
                       and e.id in (original.get('source_event_ids') or [])
                       and (not run_id or (e.payload.get('workflow_run_id') or e.correlation_id) == run_id))
        out.append({
            'schema_version': 'run-manager.pending-action.v1',
            'action': 'diagnose-attention', 'safe_resume_action': 'diagnose_attention',
            'checkpoint_id': checkpoint, 'fingerprint': checkpoint,
            'recovery_case_id': 'rcase-' + checkpoint,
            'parent_recovery_case_id': original.get('recovery_case_id', ''),
            'workflow_run_id': run_id,
            **{k: original[k] for k in ('task_map_generation', 'fanout_id', 'stage_id') if original.get(k)},
            'task_id': original.get('task_id', ''),
            'source_event_ids': list(dict.fromkeys([*sources, request.id, event.id])),
            'failure_class': 'autoresearch_diagnosis_incomplete',
            'recovery_phase': 'diagnosis_result', 'diagnosis': diagnosis,
            'checkpoint_ref': event.payload.get('checkpoint_ref', {}),
            'diagnosis_ref': event.payload.get('diagnosis_ref', {}),
            'cleanup_pending': ((event.payload.get('worktree_lifecycle') or {}).get('reason') == 'runtime_cleanup_failed'
                or ((event.payload.get('worktree_lifecycle') or {}).get('runtime_cleanup') or {}).get('status') == 'retained'),
            'worktree_lifecycle': event.payload.get('worktree_lifecycle') or {},
            'owner_route': 'run_manager', 'suggested_route': 'run_manager_recovery',
            'action_policy': 'needs_diagnosis', 'intervention_class': 'diagnose',
            'expected_output': ['run.manager.agent.recommendation'],
            'expected_downstream_events': ['run.manager.resident.prompted', 'run.manager.reflect.requested',
                                           'human.escalate'],
            'reason': 'Diagnosis did not complete. Inspect the bound phase, partial reports and '
                      'cleanup separately. Choose an authorized bounded continuation or explicit '
                      'owner request; do not repeat the whole experiment, infer a product defect, '
                      'expand caps, or claim the original task recovered from this receipt.',
        })
    return out
