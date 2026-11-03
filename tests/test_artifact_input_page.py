from zf.runtime.artifact_input_page import expand_input_items, pack_input_items


def test_compact_pages_deduplicate_metadata_without_merging_occurrences():
    source = {'locator_id': 'one', 'sha256': 'same', 'read_command': 'read --source one'}
    alias = {**source, 'locator_id': 'two', 'read_command': 'read --source two'}
    nav = {'kind': 'relation', 'id': 'T1'}
    sources, items = pack_input_items([(source, nav), (source, None), (alias, nav)])
    assert sources == [source, alias]
    assert [item['source'] for item in items] == [0, 0, 1]
    decoded = expand_input_items({'schema_version': 'attempt-input-index.v2', 'sources': sources, 'items': items})
    assert [row['read_command'] for row in decoded] == ['read --source one', 'read --source one', 'read --source two']
    assert len({row['locator_id'] for row in decoded}) == 3
    assert 'navigation' not in source


def test_each_page_repeats_its_own_metadata_and_preserves_section_read_bounds():
    source = {'locator_id': 'one', 'read_command': 'read --source one'}
    nav = {'kind': 'section', 'page_start': 20, 'section_end': 2048}
    sources, items = pack_input_items([(source, nav)])
    page = {'schema_version': 'attempt-input-index.v2', 'sources': sources, 'items': items}
    assert items[0]['source'] == 0
    assert expand_input_items(page)[0]['read_command'].endswith('--page-start 20 --page-size 1024')
    assert expand_input_items({'items': [source]}) == [source]
