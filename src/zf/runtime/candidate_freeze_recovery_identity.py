"""Resolve historical fixed-input Reader retries to their original native freeze.

Old recovery copied the native schema marker and pruned empty values. Only a
complete controlled-action / failed-fanout chain can identify that exact copy.
Neither the ledger nor the immutable receipt is rewritten.
"""
from collections.abc import Iterable, Iterator, Sequence

from zf.core.events.model import ZfEvent


def _valid_stall_freezes(events: Sequence[ZfEvent]) -> set[str]:
    """A Stall retry can redeliver only an existing, still-current native receipt."""
    from zf.runtime.fanout_retrigger_guard import is_stall_trigger_copy

    current: dict[str, ZfEvent] = {}
    originals: dict[str, ZfEvent] = {}
    valid: set[str] = set()
    for event in events:
        body = event.payload
        if (event.type != "candidate.ready" or event.origin != "kernel"
                or body.get("schema_version") != "candidate-freeze-receipt.v1"
                or body.get("repaired_failure_event_id") or body.get("rework_of")):
            continue
        run = str(body.get("workflow_run_id") or event.correlation_id or "")
        if not run or not body.get("freeze_id"):
            continue
        if event.actor != "zf-stall-redispatch":
            originals[event.id] = current[run] = event
            continue
        source = originals.get(body.get("original_trigger_event_id"))
        latest = current.get(run)
        if source is None or latest is None or not is_stall_trigger_copy(event, source):
            continue
        if source.id == latest.id or (
            source.payload.get("freeze_receipt_digest")
            and all(source.payload.get(key) == latest.payload.get(key)
                    for key in ("freeze_receipt_digest", "freeze_id", "candidate_head_commit"))
        ):
            valid.add(event.id)
    return valid


def frozen_candidate_occurrences(events: Sequence[ZfEvent]) -> Iterator[ZfEvent]:
    """Newest first, preserving valid Stall dispatch ids and ignoring invalid copies."""
    valid_stalls: set[str] | None = None
    for event in reversed(events):
        if event.type != "candidate.ready":
            continue
        if event.actor == "zf-stall-redispatch":
            if valid_stalls is None:
                valid_stalls = _valid_stall_freezes(events)
            if event.id not in valid_stalls:
                continue
        yield event


def native_freeze_for_recovery(events: list[ZfEvent], candidate: ZfEvent) -> ZfEvent:
    rows = {event.id: event for event in events}
    positions = {event.id: index for index, event in enumerate(events)}
    seen: set[str] = set()

    def run(event: ZfEvent) -> str:
        return str(event.payload.get('workflow_run_id') or event.correlation_id or '')

    def resolve(current: ZfEvent) -> ZfEvent:
        body = current.payload
        if current.actor == "zf-stall-redispatch" and current.id not in _valid_stall_freezes(events):
            raise ValueError("Candidate Stall retry lacks its current native freeze")
        if not body.get('repaired_failure_event_id'):
            return current
        if current.id in seen:
            raise ValueError('Candidate repair freeze lineage is cyclic')
        seen.add(current.id)
        request = rows.get(current.causation_id)
        failure = rows.get(body['repaired_failure_event_id'])
        scope = run(current)
        if (current.origin != 'kernel' or not scope or request is None or failure is None
                or request.type != 'control.action.requested' or failure.origin != 'kernel'
                or request.payload.get('action') != 'stage-replan-new-generation'
                or run(failure) != scope or failure.payload.get('failure_kind') != 'infra'
                or not failure.payload.get('fanout_id') or not failure.payload.get('stage_id')
                or request.payload.get('repaired_failure_event_id') != failure.id
                or not request.payload.get('approval_ref')
                or request.payload['approval_ref'] != body.get('operator_authorization_ref')
                or body.get('stage_replan_generation_source_event_id') != failure.id
                or body.get('stage_replan_generation') != 'repair-' + request.id):
            raise ValueError('Candidate repair freeze lacks its exact controlled request')
        fanouts = [event for event in events[:positions[failure.id]]
            if event.type == 'fanout.started' and event.origin == 'kernel' and run(event) == scope
            and event.payload.get('fanout_id') == failure.payload.get('fanout_id')
            and event.payload.get('stage_id') == failure.payload.get('stage_id')]
        if len(fanouts) != 1:
            raise ValueError('Candidate repair freeze lacks its unique failed fanout')
        fanout = fanouts[0]
        source = rows.get(fanout.payload.get('trigger_event_id'))
        starts = [event for event in events if event.type == 'runtime.action.attempt.started'
            and event.origin == 'kernel' and event.causation_id == request.id
            and event.payload.get('action') == 'stage-replan-new-generation'
            and event.payload.get('payload', {}).get('repaired_failure_event_id') == failure.id
            and event.payload.get('payload', {}).get('approval_ref') == request.payload['approval_ref']]
        if source is None or len(starts) != 1:
            raise ValueError('Candidate repair freeze lacks its original trigger or action')
        start = starts[0]
        completed = [event for event in events if event.type == 'runtime.action.attempt.completed'
            and event.origin == 'kernel' and event.causation_id == start.id
            and event.payload.get('action') == 'stage-replan-new-generation'
            and event.payload.get('attempt_id') == start.payload.get('attempt_id')
            and event.payload.get('status') == 'applied' and event.payload.get('result_event_id') == current.id]
        if (len(completed) != 1 or source.type != 'candidate.ready' or source.origin != 'kernel'
                or source.payload.get('schema_version') != 'candidate-freeze-receipt.v1'
                or run(source) != scope
                or not positions[source.id] < positions[fanout.id] < positions[failure.id]
                    < positions[request.id] < positions[start.id] < positions[current.id] < positions[completed[0].id]):
            raise ValueError('Candidate repair freeze effect is incomplete or out of order')
        native = resolve(source)
        if any(event.type == 'candidate.ready' and run(event) == scope
               and event.payload.get('schema_version') == 'candidate-freeze-receipt.v1'
               and not event.payload.get('repaired_failure_event_id')
               for event in events[positions[source.id] + 1:positions[current.id]]):
            raise ValueError('Candidate repair freeze was superseded before recovery')
        # Only the two known empty roster members may have been pruned by the
        # historical copier. Explicit nulls and every other missing key reject.
        for key, value in native.payload.items():
            if key not in body and key in {'superseded_task_ids', 'task_supersession_event_ids'} and value == []:
                continue
            if key not in body or body[key] != value:
                raise ValueError('Candidate repair changed frozen field: ' + key)
        return native

    return resolve(candidate)


def superseding_reader_freeze(
    newest_first: Iterable[ZfEvent],
    workflow_run_id: str,
    freeze_id: str,
    candidate_head_commit: str,
) -> ZfEvent | None:
    """A late Reader failure remains bound to the native freeze it evaluated."""
    if not workflow_run_id or not freeze_id:
        return None
    for event in newest_first:
        if event.type != "candidate.ready" or event.origin != "kernel" or event.actor == "zf-stall-redispatch":
            continue
        body = event.payload
        if (
            (body.get("workflow_run_id") or event.correlation_id) != workflow_run_id
            or body.get("schema_version") != "candidate-freeze-receipt.v1"
            or body.get("repaired_failure_event_id")
            or body.get("rework_of")
            or not body.get("freeze_id")
        ):
            continue
        if body["freeze_id"] != freeze_id or (
            candidate_head_commit and body.get("candidate_head_commit") != candidate_head_commit
        ):
            return event
        return None
    return None


def bind_retrigger_snapshot(events: list[ZfEvent], source: ZfEvent, payload: dict) -> None:
    """A Reader redrive may reference a current native freeze, never create one."""
    if source.type != "candidate.ready" or not (
        payload.get("schema_version") == "candidate-freeze-receipt.v1"
        or payload.get("candidate_snapshot_event_id")
    ):
        return
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    run = str(payload.get("workflow_run_id") or source.correlation_id or "")
    native = frozen_candidate_for_payload(events, run, payload={"candidate_event_id": source.id})
    current = frozen_candidate_for_payload(events, run, payload={})
    if source.origin != "kernel" or not run or native is None or native.origin != "kernel" or native != current:
        raise ValueError("Candidate retrigger requires its current native freeze")
    payload["candidate_snapshot_event_id"] = native.id
    payload.pop("schema_version", None)
