from zf.runtime.artifact_input_page import expand_input_items
import json

from zf.runtime.artifact_navigation import build_navigation


def test_declared_relation_fields_are_locatable_without_inventing_semantics():
    nav = build_navigation(json.dumps({"task_id": "T7", "body": {"requirement_ids": ["REQ-7"],
        "acceptance_ids": ["AC-7"], "blocked_by": ["T6"], "prose": "REQ-PRIVATE is only prose"}}))
    assert {(e["relation"], e["id"]) for e in nav["entries"]} == {
        ("task", "T7"), ("requirement", "REQ-7"), ("acceptance", "AC-7"), ("dependency", "T6")}
    assert next(e for e in nav["entries"] if e["id"] == "AC-7")["field_pointer"] == "/body/acceptance_ids/0"
    assert nav["complete"] and nav["authority"] == "navigation_only"


def test_markdown_unicode_duplicate_headings_and_fences_have_exact_spans():
    body = "# 标题\n规则\n## 重复\n甲\n```md\n# 假标题\n```\n## 重复\n乙\n末章\n====\n丙\n"
    nav = build_navigation(body)
    assert [e["title"] for e in nav["entries"]] == ["标题", "重复", "重复", "末章"]
    first, second = nav["entries"][1:3]
    assert first["page_start"] != second["page_start"]
    assert body[first["page_start"]:first["section_end"]].startswith("## 重复\n甲")
    assert body[second["page_start"]:second["section_end"]] == "## 重复\n乙\n"
    assert nav["selected_sha256"] != build_navigation("changed" + body)["selected_sha256"]


def test_navigation_caps_are_explicit_not_claimed_as_full_coverage():
    nav = build_navigation(json.dumps({"requirement_ids": [f"REQ-{i}" for i in range(1000)]}))
    assert len(nav["entries"]) == 512
    assert nav["complete"] is False


def test_task_scoped_relation_navigation_is_bound_and_does_not_read_body(tmp_path):
    from tests.test_artifact_read_snapshot import published, IDENTITY
    from zf.runtime.artifact_input_index import input_index
    from zf.runtime.artifact_read_ledger import active_read_ledger_path
    import pytest
    body = {"shared": {}, "tasks": [{"task_id": f"T{i}", "body": {
        "requirement_ids": [f"REQ-{i}"], "acceptance_ids": [f"AC-{i}"]}} for i in range(100)]}
    manifest, required, _ = published(tmp_path, body=body)
    result = input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], relation_id="AC-7")
    assert result["total"] == 1
    item = expand_input_items(result)[0]
    assert item["navigation"]["id"] == "AC-7"
    assert item["json_path"] == "$.tasks[7]"  # Child pointer is navigation, not expanded read authority.
    assert item["read_command"].endswith("'$.tasks[7]'")
    assert input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], relation_id="AC-8")["total"] == 0
    page = input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], navigate=True, limit=1)
    with pytest.raises(ValueError, match="stale"):
        input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], cursor=page["next_cursor"])
    assert not active_read_ledger_path(tmp_path, "a").exists()


def test_reordered_array_and_unicode_section_read_keep_published_selection(tmp_path):
    from tests.test_artifact_read_snapshot import published, IDENTITY
    from zf.runtime.artifact_input_index import input_index
    from zf.runtime.artifact_read_snapshot import open_selection
    body = {'shared': '# 入口\n规则\n## 表单\n字段\n', 'tasks': [{'task_id': f'T{i}'} for i in range(100)]}
    manifest, required, _ = published(tmp_path, body=body)
    body['tasks'].reverse()
    body['shared'] = '# 已改变\n'
    (tmp_path / 'artifacts/input.json').write_text(json.dumps(body))
    result = input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], query='表单')
    section = result['items'][0]['navigation']
    selection = open_selection(tmp_path, manifest=manifest, row=required[0], identity=IDENTITY)
    assert selection.read(section['page_start'], section['section_end']) == '## 表单\n字段\n'
    task = open_selection(tmp_path, manifest=manifest, row=required[1], identity=IDENTITY)
    assert json.loads(task.read())['task_id'] == 'T7'


def test_generated_section_command_reads_a_bounded_unicode_page(tmp_path, monkeypatch, capsys):
    import shlex
    from tests.test_artifact_read_snapshot import published, IDENTITY
    from tests.test_artifact_read_capability import _grant
    from zf.cli.main import main
    from zf.runtime.artifact_input_index import input_index
    manifest, required, _ = published(tmp_path, body={'shared': '# 表单\n' + '字段😃' * 5000,
        'tasks': [{} for _ in range(8)]})
    _grant(tmp_path, monkeypatch, manifest=manifest)
    monkeypatch.setenv('ZF_CLI_CMD', 'zf')
    item = expand_input_items(input_index(state_dir=tmp_path, manifest=manifest, required=required, identity=IDENTITY, reads=[], query='表单'))[0]
    assert main(shlex.split(item['read_command'])[1:] + ['--state-dir', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['content'].startswith('# 表单\n')
    assert result['next_page_start'] < result['page_total']
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 8192


def test_relation_cli_and_web_route_share_authorized_navigation(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from tests.test_artifact_read_snapshot import published
    from tests.test_artifact_read_capability import _grant
    from zf.cli.main import main
    from zf.core.events import EventLog, EventWriter, ZfEvent
    from zf.web.server import create_app
    manifest, required, _ = published(tmp_path, body={'shared': {}, 'tasks': [
        {'task_id': f'T{i}', 'acceptance_ids': [f'AC-{i}']} for i in range(100)]})
    _grant(tmp_path, monkeypatch, manifest=manifest)
    EventWriter(EventLog(tmp_path / 'events.jsonl')).append(ZfEvent(type='task.dispatched',
        task_id='T7', payload={'dispatch_id': 'a', 'required_reads': required}))
    assert main(['artifact', 'index', '--attempt', 'a', '--relation-id', 'AC-7', '--state-dir', str(tmp_path)]) == 0
    cli = json.loads(capsys.readouterr().out)
    client = TestClient(create_app(tmp_path, project_root=tmp_path.parent))
    response = client.get('/api/attempts/a/inputs?relation_id=AC-7')
    assert response.status_code == 200 and response.json()['items'] == cli['items']
    assert cli['total'] == 1 and cli['items'][0]['navigation']['id'] == 'AC-7'
    assert client.get('/api/attempts/a/inputs?relation_id=AC-8').json()['total'] == 0
    expanded = client.get('/api/attempts/a/inputs?navigate=true').json()
    assert len({row['locator_id'] for row in expand_input_items(expanded)}) == expanded['total'] == 4
