import json
from pathlib import Path
from types import SimpleNamespace

from zf.runtime.plan_gap_input_partitions import rebind_gap_port_partitions
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_scoped_plan_inputs import task_input_paths


def test_gap_rebind_assigns_new_package_identity_to_shared_partition(tmp_path: Path):
    state = tmp_path / '.zf'; state.mkdir()
    body = {
      'schema_version': 'product_acceptance_spec.v1', 'workflow_run_id': 'run',
      'plan_revision': 'rev', 'task_map_generation': 'gen',
      'assembly_owner': 'P20', 'entrypoints': [{'id': 'web'}],
      'task_input_policy': {'schema_version': 'task-input-policy.v1', 'shared_paths': ['$.schema_version', '$.entrypoints'],
          'tasks': [{'task_id': 'OLD', 'paths': ['$.assembly_owner']}]},
    }
    ref = write_immutable_json_sidecar(state, body, root='plan-input-partitions', kind='product_acceptance_spec', schema_version='product_acceptance_spec.v1', created_by='test', source_event_id='seed')
    ports = {'product_acceptance_spec': {'logical_name': 'product_acceptance_spec', 'rebound_from_ref': ref['ref'], **ref, 'byte_count': 0}}
    task_map = {'tasks': [{'task_id': 'GAP', 'parent_task_id': 'OLD', 'payload': {}}]}
    rebind_gap_port_partitions(state_dir=state, task_map=task_map, payload={'resume_scope': 'gap_tasks_only', 'gap_task_ids': ['GAP']}, explicit_ports=ports, inherited=[], source_event_id='gap')
    updated = hydrate_sidecar_ref(state, ports['product_acceptance_spec']).payload
    assert '$.plan_revision' in updated['task_input_policy']['shared_paths']
    assert '$.task_map_generation' in updated['task_input_policy']['shared_paths']
    assert '$.workflow_run_id' in updated['task_input_policy']['shared_paths']
    assert task_input_paths(updated, task_id='GAP')
