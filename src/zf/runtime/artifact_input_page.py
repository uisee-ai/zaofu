"""Compact, independently readable index pages; source scope is never merged."""
from __future__ import annotations

from typing import Any
from zf.runtime.call_result_envelope import canonical_json_sha256


def pack_input_items(rows: list[tuple[dict, dict | None]]) -> tuple[list[dict], list[dict]]:
    sources, items, offsets = [], [], {}
    for source, navigation in rows:
        key = source['locator_id']
        if key not in offsets:
            offsets[key] = len(sources)
            sources.append(source)
        item: dict[str, Any] = {'source': offsets[key]}
        if navigation is not None:
            item['navigation'] = navigation
        items.append(item)
    return sources, items


def expand_input_items(page: dict) -> list[dict]:
    """Compatibility reader for v1/v2; never a second transport representation."""
    if page.get('schema_version') != 'attempt-input-index.v2':
        return page['items']
    result = []
    for item in page['items']:
        source = dict(page['sources'][item['source']])
        navigation = item.get('navigation')
        if navigation is not None:
            source['navigation'] = navigation
            source['locator_id'] = canonical_json_sha256([source['locator_id'], navigation])
            if navigation['kind'] == 'section':
                size = min(1024, max(1, navigation['section_end'] - navigation['page_start']))
                source['read_command'] += f" --page-start {navigation['page_start']} --page-size {size}"
        result.append(source)
    return result
