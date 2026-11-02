import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.test_artifact_query_service import _service, _append
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.artifact_query.store import projection_db_path, get_reducer_projection
from zf.runtime.artifact_navigation_shards import SHARD_SCHEMA as NAVIGATION_SCHEMA
from zf.web.server import create_app


def test_publication_navigation_authorized_versioned_rebuildable_and_bounded(tmp_path):
    root, state, service = _service(tmp_path)
    descriptor = write_sidecar_json(state, 'artifacts/task.json', {'task_id': 'T1',
        'requirement_ids': [f'REQ-{i}' for i in range(1000)]}, kind='task_map',
        schema_version='test.v1', created_by='dev-1')
    # Draft sidecars are never promoted to published catalog entries.
    assert service.artifact_navigation(descriptor['sha256'], context=service.context())['total'] == 0
    _append(state, event_id='published', descriptor=descriptor)
    catalog = service.catalog_list(context=service.context())
    identity = catalog['items'][0]['object_id']
    cached = get_reducer_projection(state, projection_kind='artifact-navigation',
        subject_id=descriptor['sha256'], source_snapshot_key=NAVIGATION_SCHEMA)
    assert cached['status'] == 'ready'  # Built by publication catch-up, not first navigation query.
    result = service.artifact_navigation(identity, context=service.context(limit=2))
    assert result['total'] == 1001 and result['next_cursor']
    assert result['body_read_evidence'] is result['authorizes_attempt_input'] is False
    assert len(json.dumps(result).encode()) <= 8192
    assert len(service.artifact_navigation(identity, context=service.context(limit=2), cursor=result['next_cursor'])['items']) == 2
    with pytest.raises(ValueError, match='stale'):
        service.artifact_navigation(identity, context=service.context(limit=2), query='REQ-1', cursor=result['next_cursor'])
    client = TestClient(create_app(state, project_root=root))
    assert client.get(f'/api/artifacts/catalog/{identity}/navigation?query=REQ-999').json()['total'] == 1
    # A damaged or missing disposable projection is reconstructed from the same digest.
    with sqlite3.connect(projection_db_path(state)) as conn:
        conn.execute("UPDATE artifact_reducer_projection SET payload_json='{}' WHERE projection_kind='artifact-navigation'")
    rebuilt = service.artifact_navigation(identity, context=service.context(limit=2))
    assert rebuilt['items'] == result['items'] and rebuilt['source_sha256'] == descriptor['sha256']
    projection_db_path(state).unlink()
    assert service.artifact_navigation(identity, context=service.context(limit=2))['items'] == result['items']


def test_same_digest_does_not_union_occurrence_permissions(tmp_path):
    _, state, service = _service(tmp_path)
    descriptor = write_sidecar_json(state, 'artifacts/task.json', {'requirement_ids': ['SECRET']},
        kind='task_map', schema_version='test.v1', created_by='worker',
        access_scope={'actor': 'owner', 'purpose': 'implementation'})
    _append(state, event_id='secret', descriptor=descriptor)
    owner = service.context(actor='owner', purpose='implementation')
    rows = service.catalog_list(context=owner, view='occurrences')['items']
    identity = rows[0]['occurrence_id']
    assert service.artifact_navigation(identity, context=owner)['total'] == 1
    denied = service.artifact_navigation(identity, context=service.context(actor='other', purpose='implementation'))
    assert denied['total'] == 0 and 'SECRET' not in str(denied) and 'ref' not in denied


def test_navigation_cache_and_watermark_rollback_together(tmp_path, monkeypatch):
    import zf.runtime.artifact_query.maintenance as maintenance
    _, state, service = _service(tmp_path)
    descriptor = write_sidecar_json(state, 'artifacts/task.json', {'task_id': 'T1'},
        kind='task_map', schema_version='test.v1', created_by='worker')
    _append(state, event_id='publish', descriptor=descriptor)
    original = maintenance.catalog_store._insert_descriptor
    def fail(*args, **kwargs):
        raise RuntimeError('interrupted catalog transaction')
    monkeypatch.setattr(maintenance.catalog_store, '_insert_descriptor', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        service.catalog_list(context=service.context())
    assert get_reducer_projection(state, projection_kind='artifact-navigation',
        subject_id=descriptor['sha256'], source_snapshot_key=NAVIGATION_SCHEMA) is None
    monkeypatch.setattr(maintenance.catalog_store, '_insert_descriptor', original)
    assert service.catalog_list(context=service.context())['items']


def test_navigation_projection_eviction_is_bounded(tmp_path, monkeypatch):
    from zf.runtime.artifact_query import store
    _, state, service = _service(tmp_path)
    monkeypatch.setattr(store, 'MAX_REDUCER_PROJECTIONS', 3)
    for i in range(5):
        descriptor = write_sidecar_json(state, f'artifacts/{i}.json', {'task_id': f'T{i}'},
            kind='task_map', schema_version='test.v1', created_by='worker')
        _append(state, event_id=f'publish-{i}', descriptor=descriptor)
    catalog = service.catalog_list(context=service.context())
    with sqlite3.connect(projection_db_path(state)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM artifact_reducer_projection").fetchone()[0] <= 3
    # Eviction changes cache occupancy only, never catalog/occurrence authority.
    assert len(catalog['items']) == 5
    for item in catalog['items']:
        assert service.artifact_navigation(item['object_id'], context=service.context())['total'] == 1
