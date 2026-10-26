from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_script(relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_tampering_cannot_silently_change_the_experiment(tmp_path):
    script = load_script('scripts/eval-delivery.py')
    registration = tmp_path / 'registration.json'
    registration.write_text('{"gate":0.8}')
    (tmp_path / 'registration.sha256').write_text(script.digest(registration))
    assert script.registration(tmp_path) == {'gate': .8}
    registration.write_text('{"gate":1.2}')
    with pytest.raises(ValueError, match='changed after freeze'):
        script.registration(tmp_path)


def test_plan_or_task_completion_is_not_full_goal_completion(tmp_path):
    script = load_script('scripts/eval-delivery.py')
    events = tmp_path / 'events.jsonl'
    events.write_text('\n'.join(json.dumps({'type': t}) for t in [
        'task_map.ready', 'plan.approved', 'task.done', 'run.completed',
    ]) + '\n')
    assert not script.summarize_events(events)['goal_completed']
    with events.open('a') as stream:
        stream.write(json.dumps({'type': 'run.goal.completed'}) + '\n{"unfinished":')
    assert script.summarize_events(events)['goal_completed']


def test_external_oracle_rejects_success_status_on_stale_request():
    oracle = load_script('tests/e2e/delivery_acceptance.py')
    with pytest.raises(AssertionError, match='expected 409'):
        oracle.expect((200, {'ok': True}), 409)


def test_unknown_price_is_not_zero_and_cumulative_snapshots_are_not_added(tmp_path):
    script = load_script('scripts/eval-delivery.py')
    path = tmp_path / 'cost.jsonl'
    rows = [dict(dedupe_key='receipt-1', input_tokens=10, output_tokens=3,
                 cache_read_tokens=20, cost_status='unpriced', cost_usd=0,
                 cumulative_usage={'input_tokens': 999999})] * 2
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    totals = script.usage_totals(path)
    assert totals['rows'] == 1
    assert totals['noncached_input_tokens'] == 10
    assert totals['cached_input_tokens'] == 20
    assert totals['output_tokens'] == 3
    assert totals['authoritative_cost_usd'] is None
    assert totals['cost_gate_status'] == 'UNKNOWN'


def test_corrupt_middle_event_is_not_silently_ignored(tmp_path):
    script = load_script('scripts/eval-delivery.py')
    path = tmp_path / 'events.jsonl'
    path.write_text('not-json\n{"type":"run.goal.completed"}\n')
    with pytest.raises(json.JSONDecodeError):
        script.summarize_events(path)


def test_evaluation_reads_configured_state_and_refuses_foreign_directory(tmp_path):
    script = load_script('scripts/eval-delivery.py')
    config = tmp_path / 'zf.yaml'
    config.write_text('kind: PrdFlow\nspec: {}\n---\nkind: ZfConfig\nspec:\n  project:\n    state_dir: custom-runtime\n')
    assert script.configured_state(tmp_path) == tmp_path / 'custom-runtime'
    config.write_text(config.read_text().replace('custom-runtime', '../foreign-state'))
    with pytest.raises(ValueError, match='inside its owned project'):
        script.configured_state(tmp_path)


def test_external_oracle_does_not_claim_build_without_product(tmp_path):
    oracle = load_script('tests/e2e/delivery_acceptance.py')
    project = tmp_path / 'project'
    project.mkdir()
    product = oracle.Product(project, tmp_path)
    try:
        with pytest.raises(RuntimeError, match='exited before health'):
            product.start()
    finally:
        product.stop()
        product.log.close()
