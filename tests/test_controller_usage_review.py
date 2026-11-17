import json

import pytest

from tests.test_controller_managed import managed as managed, workspace as workspace
from zf.core.events import EventWriter
from zf.integrations.controller.binding import ManagedDenied
from zf.integrations.controller.execution import Execution
from zf.integrations.controller.usage_review import mark_partial
from zf.integrations.controller.workspace import synchronize_workspace


def settled(managed):
    config, _, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='review-fixture',
                          provider='codex', model='fixture').begin(lambda: None)
    execution.finish({'input_tokens': 13, 'output_tokens': 2}, status='stopped')
    synchronize_workspace(config)
    assert execution.ledger.get(execution.id)['status'] == 'settled'
    return execution


EVIDENCE = 'qualification/native-cancel#sha256=' + 'a' * 64


def test_usage_review_cli_appends_revision_without_rewriting_ack_or_refunding(managed, monkeypatch):
    from zf.cli.main import main
    config, peer, context, _ = managed
    execution = settled(managed)
    original = next(row for row in peer.records.values() if row['payload']['kind'] == 'usage')
    original_json = json.dumps(original, sort_keys=True)
    monkeypatch.chdir(context.project_root)
    args = ['controller', 'usage-review', '--execution-id', execution.id,
            '--reason', 'Native turn was interrupted', '--evidence-ref', EVIDENCE]
    assert main(args) == 0
    synchronize_workspace(config)
    assert main(args) == 0
    synchronize_workspace(config)
    records = [row for row in peer.records.values() if row['payload']['kind'] == 'usage']
    assert len(records) == 2 and len(peer.settlements) == 1
    assert json.dumps(original, sort_keys=True) == original_json
    corrected = next(row for row in records if row['payload']['revision'] == 2)
    assert corrected['occurred_at'] == original['occurred_at']
    assert corrected['origin_deployment_id'] == original['origin_deployment_id']
    assert corrected['payload']['tokens'] == original['payload']['tokens']
    assert corrected['payload']['record_id'] == original['payload']['record_id']
    assert corrected['payload']['completeness'] == 'partial'
    row = execution.ledger.get(execution.id)
    assert row['status'] == 'settlement_pending'
    assert row['body']['original_receipt']['usage_completeness'] == 'complete'
    assert row['body']['settlement']['status'] == 'settled'
    with pytest.raises(ManagedDenied, match='different evidence'):
        mark_partial(context, execution.id, reason='different reason', evidence_ref=EVIDENCE)


def test_review_event_write_failure_is_replayed_from_durable_obligation(managed, monkeypatch):
    config, peer, context, _ = managed
    execution = settled(managed)
    original = EventWriter.emit
    def fail(self, name, **kwargs):
        if name == 'controller.usage.receipt' and kwargs['payload'].get('usage_revision') == 2:
            raise OSError('fixture write failure')
        return original(self, name, **kwargs)
    monkeypatch.setattr(EventWriter, 'emit', fail)
    with pytest.raises(OSError):
        mark_partial(context, execution.id, reason='Interrupted receipt', evidence_ref=EVIDENCE)
    assert execution.ledger.get(execution.id)['status'] == 'receipt_pending'
    monkeypatch.setattr(EventWriter, 'emit', original)
    synchronize_workspace(config)
    synchronize_workspace(config)
    records = [row['payload'] for row in peer.records.values() if row['payload']['kind'] == 'usage']
    assert len(records) == 2
    assert max(records, key=lambda r: r['revision'])['completeness'] == 'partial'
    assert execution.ledger.get(execution.id)['status'] == 'settlement_pending'


def test_review_refuses_unsettled_execution_and_unbound_evidence(managed):
    config, _, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='running-review',
                          provider='codex', model='fixture').begin(lambda: None)
    try:
        with pytest.raises(ManagedDenied, match='settled'):
            mark_partial(context, execution.id, reason='Interrupted receipt', evidence_ref=EVIDENCE)
        with pytest.raises(ManagedDenied, match='digest-bound'):
            mark_partial(context, execution.id, reason='Interrupted receipt', evidence_ref='unbound')
    finally:
        execution.finish({}, status='stopped', usage_complete=False)
