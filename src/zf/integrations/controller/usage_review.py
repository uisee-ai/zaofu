"""Append a conservative correction to a previously overstated complete receipt."""

import hashlib
import json
import re

from zf.integrations.controller.binding import ManagedDenied, managed_config
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.ledger import Ledger


def mark_partial(context, execution_id, *, reason, evidence_ref):
    if (not 3 <= len(reason) <= 500 or not re.fullmatch(r'[^\s]{1,400}#sha256=[a-f0-9]{64}', evidence_ref)):
        raise ManagedDenied('usage review requires a reason and digest-bound evidence reference')
    config = managed_config()
    if not config or not context.config.observability.controller.enabled:
        raise ManagedDenied('usage review requires a managed project')
    ledger = Ledger(config.state_dir)
    row = ledger.get(execution_id)
    if (not row or row['kind'] != 'execution' or row['body'].get('project_id')
            != context.config.observability.controller.project_id):
        raise ManagedDenied('execution does not belong to this project')
    body = row['body']
    review = {'reason': reason, 'evidence_ref': evidence_ref}
    if body.get('usage_review'):
        if body['usage_review'] != review:
            raise ManagedDenied('usage review already recorded with different evidence')
        return {'execution_id': execution_id, 'revision': 2, 'status': 'recorded'}
    if row['status'] != 'settled' or not body.get('release') or not body.get('finished_at'):
        raise ManagedDenied('only released, previously settled executions can be reviewed')
    collector = Collector(context.state_dir, context.config)
    with collector.outbox.connect() as db:
        original = db.execute('SELECT body,status FROM messages WHERE source_key=?',
                              ('usage:' + execution_id,)).fetchone()
    if not original or original['status'] != 'acked':
        raise ManagedDenied('original acknowledged usage message is required')
    message = json.loads(original['body'])
    wire = message['payload']
    if (wire.get('revision') != 1 or wire.get('completeness') != 'complete'
            or wire.get('budget_reservation_id') != body['budget']['id']
            or message['origin_deployment_id'] != body['deployment_id']):
        raise ManagedDenied('original usage binding is not reviewable')
    receipt = {**body['receipt'], 'usage_revision': 2, 'usage_completeness': 'partial',
               'usage_timestamp': message['occurred_at'], 'usage_review': review,
               'previous_message_sha256': hashlib.sha256(original['body'].encode()).hexdigest()}
    changed = {**body, 'original_receipt': body['receipt'], 'receipt': receipt, 'usage_review': review}
    # A durable correction precedes the EventWriter append; normal recovery replays it.
    # Keep the original remote settlement and counters as evidence, never a refund.
    if not ledger.put_if_current(row, 'receipt_pending', changed):
        raise ManagedDenied('execution changed during usage review')
    publish_review(context, ledger, ledger.get(execution_id))
    return {'execution_id': execution_id, 'revision': 2, 'status': 'recorded'}


def publish_review(context, ledger, row):
    """Replay only the correction, including across deployment handoff."""
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    log = event_log_from_project(context.state_dir, config=context.config)
    try:
        event = EventWriter(log).emit('controller.usage.receipt', actor='controller-connector', payload=row['body']['receipt'])
        Collector(context.state_dir, context.config)(event)
    finally:
        log.close()
    ledger.put_if_current(row, 'settlement_pending', row['body'])
