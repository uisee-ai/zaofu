"""Complete derived navigation, published in digest-bound bounded shards.

This index never expands a source's ACL or selector. Callers authorize the
occurrence before reading it. Old or damaged indexes rebuild from pinned text.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator

from zf.runtime.artifact_navigation import RELATION_FIELDS, _headings, matching_navigation
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

SHARD_SCHEMA = 'artifact-navigation-shards.v1'
PAGE_ENTRIES = 128
PAGE_BYTES = 192 * 1024


class NavigationWalk:
    def __init__(self, content: str):
        self.content = content
        self.complete = True

    def __iter__(self) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(self.content)
        except json.JSONDecodeError:
            body = None
        except RecursionError as exc:
            raise ValueError('navigation JSON nesting exceeds parser capacity') from exc
        if not isinstance(body, (dict, list)):
            entries, self.complete = _headings(self.content, entry_limit=None)
            yield from entries
            return
        # Iterator frames avoid copying a wide Task list onto the traversal stack.
        stack = [iter([(body, '')])]
        while stack:
            try:
                value, pointer = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if len(pointer) > 512:
                self.complete = False
                continue
            if isinstance(value, dict):
                for key, child in value.items():
                    field = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                    if key not in RELATION_FIELDS:
                        continue
                    values = child if isinstance(child, list) else [child]
                    for index, identifier in enumerate(values):
                        if not isinstance(identifier, str) or not identifier.strip():
                            continue
                        location = field + (f'/{index}' if isinstance(child, list) else '')
                        if len(identifier) > 256 or len(location) > 512:
                            self.complete = False
                            continue
                        yield {'kind': 'relation', 'relation': RELATION_FIELDS[key],
                               'id': identifier, 'field_pointer': location}
                stack.append(_children(value, pointer))
            elif isinstance(value, list):
                stack.append(_children(value, pointer))


def _children(value: dict | list, pointer: str) -> Iterator[tuple[Any, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield child, pointer + '/' + key.replace('~', '~0').replace('/', '~1')
    else:
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                yield child, f'{pointer}/{index}'


def _bucket(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()[0]


def _descriptor(value: dict) -> dict:
    return {key: value[key] for key in ('ref', 'sha256', 'byte_count')}


def publish_navigation(state_dir: Path, content: str) -> dict:
    """Bound memory to sixteen shard buffers; publish the directory last."""
    buffers: dict[str, list] = {}
    buckets: dict[str, list] = {}
    total = 0

    def flush(bucket: str) -> None:
        rows = buffers.pop(bucket)
        payload = {'schema_version': SHARD_SCHEMA, 'entries': rows}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > PAGE_BYTES:
            raise ValueError('navigation shard exceeds byte budget')
        ref = write_immutable_json_sidecar(state_dir, payload, root='navigation/shards',
            kind='navigation_shard', schema_version=SHARD_SCHEMA, created_by='artifact-navigation')
        buckets.setdefault(bucket, []).append({**_descriptor(ref), 'count': len(rows)})

    walk = NavigationWalk(content)
    for entry in walk:
        key = _bucket(str(entry.get('id') or ''))
        buffers.setdefault(key, []).append(entry)
        total += 1
        if len(buffers[key]) == PAGE_ENTRIES:
            flush(key)
    for key in sorted(buffers):
        flush(key)
    metadata = {'schema_version': SHARD_SCHEMA,
        'selected_sha256': hashlib.sha256(content.encode()).hexdigest(),
        'complete': walk.complete, 'entry_count': total}
    root = write_immutable_json_sidecar(state_dir, {**metadata, 'buckets': buckets},
        root='navigation/indexes', kind='navigation_index', schema_version=SHARD_SCHEMA, created_by='artifact-navigation')
    return {**metadata, 'index_ref': _descriptor(root)}


def _matches(entry: dict, query: str, relation_id: str) -> bool:
    return ((not relation_id or entry.get('id') == relation_id)
        and (not query or query.casefold() in str(entry.get('id') or entry.get('title') or '').casefold()))


def _ordered(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (row.get('line', 0), row.get('field_pointer', ''),
        row.get('relation', ''), row.get('id', ''), row.get('title', '')))


def query_navigation(state_dir: Path | None, navigation: dict, *, query: str = '', relation_id: str = '',
                     fallback: Callable[[], str] | None = None) -> tuple[dict, list[dict]]:
    """No response/receipt authority. Recovery verifies the selected digest."""
    sharded = navigation.get('schema_version') == SHARD_SCHEMA
    if not sharded and navigation.get('complete') is True:
        return {**navigation, 'entry_count': len(navigation.get('entries', []))}, _ordered(matching_navigation(navigation, query=query, relation_id=relation_id))
    if state_dir is None:
        if sharded:
            raise ValueError('navigation shards require the configured state directory')
        return navigation, _ordered(matching_navigation(navigation, query=query, relation_id=relation_id))
    legacy = not sharded
    if legacy and navigation.get('selected_sha256'):
        from zf.runtime.artifact_query.store import get_reducer_projection
        try:
            cached = get_reducer_projection(state_dir, projection_kind='complete-navigation',
                subject_id=navigation['selected_sha256'], source_snapshot_key=SHARD_SCHEMA)
        except (OSError, ValueError, sqlite3.Error):
            cached = None
        if cached and cached.get('schema_version') == SHARD_SCHEMA and cached.get('selected_sha256') == navigation['selected_sha256']:
            navigation, sharded = cached, True
    try:
        if not sharded:
            raise ValueError('legacy navigation needs complete reconstruction')
        root = hydrate_sidecar_ref(state_dir, navigation['index_ref']).payload
        if (not isinstance(root, dict) or root.get('schema_version') != SHARD_SCHEMA
                or any(root.get(k) != navigation.get(k) for k in ('selected_sha256', 'complete', 'entry_count'))):
            raise ValueError('navigation directory binding mismatch')
        buckets = root['buckets']
        if not isinstance(buckets, dict):
            raise ValueError('invalid navigation directory')
        if sum(p['count'] for pages in buckets.values() for p in pages) != root['entry_count']:
            raise ValueError('navigation directory count mismatch')
        rows = []
        keys = [_bucket(relation_id)] if relation_id else sorted(buckets)
        for key in keys:
            for descriptor in buckets.get(key, []):
                page = hydrate_sidecar_ref(state_dir, descriptor, max_bytes=PAGE_BYTES).payload
                if (not isinstance(page, dict) or page.get('schema_version') != SHARD_SCHEMA
                        or not isinstance(page.get('entries'), list)
                        or any(not isinstance(row, dict) for row in page['entries'])
                        or len(page['entries']) != descriptor['count']):
                    raise ValueError('navigation shard count/schema mismatch')
                rows.extend(row for row in page['entries'] if _matches(row, query, relation_id))
        return navigation, _ordered(rows)
    except (OSError, ValueError, KeyError, TypeError):
        if fallback is None:
            raise ValueError('navigation unavailable; pinned reconstruction is required') from None
    content = fallback()
    expected = navigation.get('selected_sha256')
    if expected and hashlib.sha256(content.encode()).hexdigest() != expected:
        raise ValueError('navigation recovery differs from pinned selection')
    if legacy and not sharded:
        # A derived cache is shared by content only after per-occurrence ACL.
        # It is disposable; the original immutable manifest is never rewritten.
        from zf.runtime.artifact_query.store import set_reducer_projection
        try:
            rebuilt = publish_navigation(state_dir, content)
            set_reducer_projection(state_dir, projection_kind='complete-navigation',
                subject_id=rebuilt['selected_sha256'], source_snapshot_key=SHARD_SCHEMA,
                source_seq=0, reducer_version=SHARD_SCHEMA, payload=rebuilt)
            metadata, rows = query_navigation(state_dir, rebuilt, query=query, relation_id=relation_id)
            return {**metadata, 'recovery_mode': 'pinned_navigation_rebuilt'}, rows
        except (OSError, ValueError, sqlite3.Error):
            pass  # A cache failure cannot turn known pinned content into no hits.
    walk = NavigationWalk(content)
    rows, count = [], 0
    for row in walk:
        count += 1
        if _matches(row, query, relation_id):
            rows.append(row)
    return {**navigation, 'complete': walk.complete, 'entry_count': count,
            'recovery_mode': 'pinned_navigation_rebuilt'}, _ordered(rows)
