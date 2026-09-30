from dataclasses import replace
import base64
import json
from pathlib import Path
import threading
import asyncio

import pytest

from issue_core import AgentResult
from issue_core.session import IssueSessionRunner
from zf.runtime.self_issue_session_host import SelfIssueSessionHost
from zf.web.kanban_agent_attachments import store_kanban_agent_attachment


class ReadyAgent:
    def run(self, *, session, working_set, tools):
        return AgentResult(status="ready", working_set=replace(
            working_set, fields={}, report={"title": "Original", "actual_behavior": "pending"},
            evidences=({"ref": "e1", "summary": "evidence"},),
        ))


def make_host(tmp_path):
    return SelfIssueSessionHost(tmp_path / ".state", tmp_path, agent=ReadyAgent())


def test_agent_collected_environment_and_impact_reach_editor_and_publication(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run('CLI fails')['session_id']
    working = host.store.load_working_set(sid)
    report = {
        'title': 'CLI fails', 'environment': {'os': 'Linux', 'version': '7.0.0-test',
                                             'arch': 'aarch64', 'python': '3.12.3'},
        'project_version': '1.2.3', 'inspected_source_revision': 'inspected-source-sha',
        'issue_workspace_revision': 'workspace-sha', 'wheel_reported_source_revision': None,
        'severity': 'P1', 'severity_status': 'provisional', 'severity_reason': 'CLI blocked',
        'classification': 'bug', 'component': 'CLI', 'impact_scope': 'Base-wheel users',
        'actual_behavior': 'Base-wheel users', 'assessment_confidence': 'high',
    }
    host.store.save_working_set(replace(working, report=report), expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    editor = result['editor']['body']
    draft = host._service().drafts.get(result['publication_draft_id'])
    published = host._service()._publication_payload(draft, marker='test', provider='github')[0]['body']
    for body in (editor, published):
        environment = body.split('## Environment\n\n')[1].split('\n\n## ')[0]
        assert environment == ('- **Operating system:** Linux\n- **OS version:** 7.0.0-test\n'
            '- **Architecture:** aarch64\n- **Python version:** 3.12.3\n'
            '- **Project version:** 1.2.3\n- **Project source commit:** inspected-source-sha')
        assert '- **Component:** CLI\n- **Impact scope:** Base-wheel users' in body
        assert '- **Assessment confidence:** high\n- **Assessment:** completed' in body
        assert 'Reported source revision' not in body
        assert 'Project source commit:** workspace-sha' not in body
    assert draft.analysis['version_context']['issue_project_root'] == 'workspace-sha'


def test_rich_report_draft_preserves_environment_revision_and_provisional_facts(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run("base wheel fails")['session_id']
    working = host.store.load_working_set(sid)
    report = {
        "title": "CLI import fails", "bug_description": "Missing httpx", "actual_behavior": "ImportError",
        "reproduction_steps": ["Create venv", "Install wheel", "Run help", "Observe ImportError"],
        "operating_system": "Ubuntu 24.04.2 LTS, x86_64",
        "python_versions": ["调查环境 Python 3.11.15", "附件复现环境 Python 3.12.3"],
        "repository_revisions": {"issue_project_root": "fixture-sha", "related_source_checkout": "source-sha"},
        "project_version": "0.0.7", "severity": "高", "scope": "基础 wheel 安装后的 CLI 启动",
        "suggested_components": ["pyproject.toml", "CLI parser", "cost.catalog"],
        "impact": "CLI is unavailable", "external_issue_disposition": "needs_triage",
        "investigation_gaps": [], "downstream_verification_tasks": ["Rebuild fixed wheel"],
    }
    host.store.save_working_set(replace(working, report=report), expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    body = result['editor']['body']
    assert '1. Create venv\n2. Install wheel\n3. Run help\n4. Observe ImportError' in body
    assert 'Ubuntu 24.04.2 LTS, x86_64' in body
    assert '调查环境 Python 3.11.15' in body and '附件复现环境 Python 3.12.3' in body
    assert 'Project source commit:** source-sha' in body
    assert 'Issue project root:** fixture-sha' in body
    assert 'Severity:** 高' in body and 'Severity:** P2' not in body
    assert '基础 wheel 安装后的 CLI 启动' in body and 'cost.catalog' in body
    assert 'Rebuild fixed wheel' in body and 'CLI is unavailable' in body
    for fabricated in ('Not provided.', 'Candidate head', 'Merge sha', 'Fix base', 'static_only', 'unverified', 'Assessment confidence:** high'):
        assert fabricated not in body
    draft = host._service().drafts.get(result['publication_draft_id'])
    assert host._service()._publication_payload(draft, marker='', provider='github')[0]['labels'] == []
    assert 'Reported source revision' not in body  # Related checkout != claimed wheel revision.
    assert 'Classification:** needs_triage (not classified)' in body


def test_agent_evidence_snapshots_keep_sources_lines_and_latest_working_set(tmp_path):
    host = make_host(tmp_path)
    source = tmp_path / 'src' / 'cli.py'
    source.parent.mkdir()
    source.write_text('import httpx\nAPI_TOKEN = "super-secret-value"\n')
    log = tmp_path / 'test.log'
    log.write_text('ModuleNotFoundError: httpx\n')
    secret = tmp_path / '.env.production'
    secret.write_text('password=do-not-upload')
    link = tmp_path / 'credentials.log'
    link.symlink_to(secret)
    sid = host.create_and_run('base wheel fails')['session_id']
    working = host.store.load_working_set(sid)
    refs = (
        {'kind': 'source', 'ref': f'{source}:1-2', 'summary': 'top-level import'},
        {'kind': 'test_log', 'ref': str(log), 'summary': 'test output', 'purpose': 'Confirm failure'},
        {'kind': 'file', 'ref': str(secret)}, {'kind': 'file', 'ref': str(link)},
        {'kind': 'file', 'ref': str(tmp_path / 'missing.log')},
        {'kind': 'source', 'ref': str(source), 'sha256': '0' * 64},
    )
    host.store.save_working_set(replace(working, evidences=refs), expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    refs_out = result['draft']['attachment_refs']
    assert {item['filename'] for item in refs_out} == {'cli.py', 'test.log'}
    assert all(item['source'] == 'agent' for item in refs_out)
    assert 'src/cli.py:1-2' in result['editor']['body']
    assert '[REDACTED_PERSONAL_PATH]' not in result['editor']['body']
    content = (host.state_dir / refs_out[0]['ref']).read_text()
    assert 'import httpx' in content and 'super-secret-value' not in content
    assert refs_out[0]['redaction_applied']
    assert len(result['editor']['attachment_warnings']) == 4
    working = host.store.load_working_set(sid)
    host.store.save_working_set(replace(working, evidences=(refs[1],)), expected_revision=working.revision)
    updated = host.materialize_publication_draft(sid)
    assert [item['filename'] for item in updated['draft']['attachment_refs']] == ['test.log']
    assert (host.state_dir / refs_out[0]['ref']).is_file()  # History remains recoverable.


def test_scoped_environment_versions_and_report_reasoning_reach_public_draft(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run('clean install fails')['session_id']
    working = host.store.load_working_set(sid)
    report = {
        'title': 'CLI fails', 'bug_description': 'Missing dependency',
        'environment': {}, 'project_version': '0.0.7',
        'reported_environment': {'python': '3.12', 'platform': 'Linux x86_64',
                                 'historical_os_distribution': 'not recorded', 'installed_packages': 'PyYAML'},
        'investigator_environment': {'os': 'Ubuntu 24.04.2', 'kernel': 'Linux 7.0',
                                     'architecture': 'x86_64', 'default_python': '3.11.15',
                                     'reproduction_python': '3.12.3'},
        'inspected_source_revision': 'source-sha', 'inspected_source_dirty': True,
        'issue_workspace_revision': 'fixture-sha', 'wheel_reported_source_revision': None,
        'reproduction_artifact_sha256': '1' * 64,
        'root_cause': 'httpx declared in dev extra', 'test_assessment': 'Tests not executed',
        'assessment_confidence_reason': 'artifact matches inspected files',
        'classification_reason': 'dependency closure fails',
        'executed_reproduction': {'command': 'import CLI', 'outcome': 'exit 1', 'evidence_refs': ['E1']},
    }
    host.store.save_working_set(replace(working, report=report,
                                      evidences=({'id': 'E1', 'result': 'import failed'},)),
                                expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    body = result['editor']['body']
    for text in ('Operating system:** Ubuntu 24.04.2', 'OS version:** 7.0',
                 'Python version:** 3.11.15', 'Architecture:** x86_64',
                 'fixture-sha', 'Evaluated source revision:** source-sha', 'Inspected source dirty:** Yes',
                 '1' * 64, 'httpx declared in dev extra', 'Tests not executed',
                 'artifact matches inspected files', 'dependency closure fails', 'import failed'):
        assert text in body
    assert result['draft']['environment']['reported_python'] == '3.12'
    assert result['draft']['environment']['investigator_reproduction_python'] == '3.12.3'
    assert 'Severity:** 待评估' in body and 'Severity:** P2' not in body
    assert 'Reported source revision' not in body
    draft = host._service().drafts.get(result['publication_draft_id'])
    assert 'p2' not in host._service()._publication_payload(draft, marker='', provider='github')[0]['labels']


def test_evidence_location_and_artifact_aliases_snapshot_files_and_report_rejections(tmp_path):
    host = make_host(tmp_path)
    src = tmp_path / 'module.py'
    src.write_text('import httpx\n')
    meta = tmp_path / 'METADATA.txt'
    meta.write_text('Requires-Dist: PyYAML\n')
    wheel = tmp_path / 'example.whl'
    wheel.write_bytes(b'not an allowed disclosure format')
    sid = host.create_and_run('missing dependency')['session_id']
    working = host.store.load_working_set(sid)
    host.store.save_working_set(replace(working, evidences=(
        {'id': 'E1', 'locations': ['module.py:1', 'module.py:1-2'], 'result': 'Top-level import'},
        {'id': 'E2', 'artifact': str(wheel), 'metadata_ref': str(meta), 'sha256': 'f' * 64},
    )), expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    attachments = result['draft']['attachment_refs']
    assert {item['filename'] for item in attachments} == {'module.py', 'METADATA.txt'}
    assert all(item['source'] == 'agent' for item in attachments)
    assert next(item for item in attachments if item['filename'] == 'module.py')['description'] == 'Top-level import'
    assert result['editor']['attachment_warnings'][0]['reason'] == 'unsupported_file_type'


def test_portable_explicit_assessment_facts_survive_publication(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run('issue')['session_id']
    working = host.store.load_working_set(sid)
    host.store.save_working_set(replace(working, report={
        'title': 'Issue', 'classification': 'bug', 'severity': 'P1',
        'reproduction_status': 'reproduced', 'evidence_collection_mode': 'full',
        'assessment_confidence': 'medium',
    }), expected_revision=working.revision)
    result = host.materialize_publication_draft(sid)
    body = result['editor']['body']
    for fact in ('Classification:** bug', 'Severity:** P1', 'Reproduction status:** reproduced',
                 'Evidence collection:** full', 'Assessment confidence:** medium'):
        assert fact in body


def test_draft_versions_save_refresh_conflict_and_exact_publication(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run("pending request")["session_id"]
    generated = host.materialize_publication_draft(sid)
    assert generated["editor"]["versions"] == [1]
    edited = "## User edited\n\nA complete Issue body.\n"
    result = host.save_editor(sid, revision=1, title="Edited", body=edited)
    assert result["ok"] and result["editor"]["versions"] == [1, 2]
    assert make_host(tmp_path).get(sid)["editor"]["body"] == edited
    assert host.save_editor(sid, revision=2, title="Edited", body=edited)["editor"]["versions"] == [1, 2]
    assert host.save_editor(sid, revision=1, title="Stale tab", body="stale")["status"] == "revision_conflict"
    service = host._service()
    draft = service.drafts.get(generated["publication_draft_id"])
    payload = service._publication_payload(draft, marker="", provider="github")[0]
    assert payload["body"] == edited and payload["title"] == "Edited"
    assert host.draft_version(sid, 1)["title"] == "Original"
    assert host.materialize_publication_draft(sid)["editor"]["versions"] == [1, 2, 3]


def test_reanalysis_preserves_reports_evidence_activity_and_draft(tmp_path, monkeypatch):
    host = make_host(tmp_path)
    sid = host.create_and_run("pending request")["session_id"]
    host.materialize_publication_draft(sid)
    original = host.editor(sid)
    host._progress(sid, "investigation", "read", detail={"kind": "tool_result", "result_detail": "a" * 50000})
    activity_id = host.get(sid)["workflow"]["activity_log"][0]["id"]
    monkeypatch.setattr(host, "_schedule", host.get)
    for index in range(22):
        host.reanalyze_and_schedule(sid, ["actual_behavior"], f"comment {index}")
        IssueSessionRunner(host.store).run_once(sid, host.agent, host._tools())
    current = host.get(sid)
    assert len(current["workflow"]["reanalysis_history"]) == 22
    assert current["workflow"]["reanalysis_history"][0]["evidences"][0]["ref"] == "e1"
    assert host.activity_result(sid, activity_id)["body"] == "a" * 50000
    assert host.editor(sid) == original
    host.materialize_publication_draft(sid, report_index=0)
    assert host.editor(sid)["versions"] == [1, 2]


def test_pause_revokes_late_result_and_resume_uses_checkpoint(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run("pending request")["session_id"]
    host._mark_investigating(sid)
    started, release = threading.Event(), threading.Event()
    errors = []

    class Delayed:
        def run(self, *, session, working_set, tools):
            started.set()
            assert release.wait(3)
            return AgentResult(status="ready", working_set=replace(working_set, report={"title": "late"}))

    def run():
        try:
            IssueSessionRunner(host.store).run_once(sid, Delayed(), host._tools())
        except ValueError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(3)
    host.pause_and_resume_later(sid)
    release.set()
    thread.join(3)
    assert errors and "superseded" in errors[0]
    paused = host.get(sid)
    assert paused["session_status"] == "paused"
    assert paused["report"]["title"] == "Original"
    assert paused["workflow"]["interruption_source"] == "user"
    host._progress(sid, "failed", "late failure")
    assert host.get(sid)["workflow"]["phase"] == "paused"
    restored = make_host(tmp_path)
    restored._mark_investigating(sid)
    checkpoint = restored.store.load_working_set(sid)
    assert checkpoint.evidences[0]["ref"] == "e1"


def test_progress_is_not_lost_when_turn_commits(tmp_path):
    host = make_host(tmp_path)
    session = host.store.create("pending")
    class WithProgress(ReadyAgent):
        def run(self, **kwargs):
            host._progress(session.session_id, "investigation", "tool", detail={"kind": "tool_result", "result_detail": "complete"})
            return super().run(**kwargs)
    IssueSessionRunner(host.store).run_once(session.session_id, WithProgress(), host._tools())
    log = host.get(session.session_id)["workflow"]["activity_log"]
    assert len(log) == 1
    assert host.activity_result(session.session_id, log[0]["id"])["body"] == "complete"


def test_materialize_publishes_investigation_process_transcript(tmp_path):
    host = make_host(tmp_path)
    sid = host.create_and_run("pending")['session_id']
    host._progress(sid, "investigation", "执行只读检查", detail={
        "kind": "tool_call", "tool": "run_read_only",
        "command": ["python", "-c", "print('ok')"],
    })
    host._progress(sid, "investigation", "整理调查结果", detail={
        "kind": "tool_result", "tool": "run_read_only", "status": "completed",
        "result_detail": "stdout: ok\nexit_code=0",
    })
    result = host.materialize_publication_draft(sid)
    process = next(item for item in result['draft']['attachment_refs']
                   if item['filename'] == 'investigation-process.log')
    content = (host.state_dir / process['ref']).read_text()
    assert "run_read_only" in content
    assert "python -c print('ok')" in content
    assert "stdout: ok" in content and "exit_code=0" in content


def test_attachment_ingress_validated_and_preserved_in_draft(tmp_path, monkeypatch):
    host = make_host(tmp_path)
    monkeypatch.setattr(host, "_schedule", host.get)
    descriptor = store_kanban_agent_attachment(host.state_dir, {
        "filename": "trace.log", "content_type": "text/plain",
        "content_base64": base64.b64encode(b"evidence line").decode(),
    })
    created = host.create_and_schedule("pending", attachments=[descriptor])
    sid = created["session_id"]
    assert host.store.load_session(sid).attachments[0].digest == descriptor["sha256"]
    IssueSessionRunner(host.store).run_once(sid, host.agent, host._tools())
    draft = host.materialize_publication_draft(sid)["draft"]
    assert draft["attachment_refs"][0]["source"] == "user"
    assert draft["attachment_refs"][0]["filename"] == "trace.log"
    with pytest.raises(ValueError, match="invalid attachment ref"):
        host.create_and_schedule("bad", attachments=[{**descriptor, "sha256": "0" * 64}])


def test_workspace_actions_are_wired_and_authorized():
    from zf.runtime.control_actions_self_issue import SELF_ISSUE_ACTIONS
    from zf.web.operator_contract import KANBAN_AGENT_ALLOWED_ACTIONS, PROJECT_OPERATOR_CONTROLLED_ACTIONS
    from zf.web.self_issue_actions import validate_self_issue_web_action
    actions = {"self-issue-session-reanalyze", "self-issue-session-pause", "self-issue-session-save-draft",
               "self-issue-session-draft-version", "self-issue-session-activity-result"}
    for registry in (SELF_ISSUE_ACTIONS, KANBAN_AGENT_ALLOWED_ACTIONS, PROJECT_OPERATOR_CONTROLLED_ACTIONS):
        assert actions <= set(registry)
    for action in actions:
        assert validate_self_issue_web_action(action, {})
        assert validate_self_issue_web_action(action, {"session_id": "issue-session-test"}) is None


def test_web_workspace_actions_use_canonical_editor_and_verified_attachments(tmp_path, monkeypatch):
    import httpx
    from zf.core.config.schema import ProjectConfig, SelfIssueConfig, ZfConfig
    from zf.web.server import create_app
    state = tmp_path / ".state"
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "workspace-test-token")
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda host, sid: (
        IssueSessionRunner(host.store).run_once(sid, ReadyAgent(), host._tools()) and host.get(sid)
    ))
    app = create_app(state, config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state"), self_issue=SelfIssueConfig(enabled=True)), project_root=tmp_path)
    attachment = store_kanban_agent_attachment(state, {"filename": "evidence.log", "content_type": "text/plain", "content_base64": base64.b64encode(b"observation").decode()})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def action(name, payload):
                response = await client.post(f"/api/actions/{name}", headers={"X-ZF-Web-Token": "workspace-test-token"}, json=payload)
                assert response.status_code == 200, response.text
                assert response.json()["ok"], response.text
                return response.json()
            captured = await action("self-issue-capture", {"portable_session": True, "description": "pending", "attachments": [attachment]})
            sid = captured["session_id"]
            generated = await action("self-issue-session-materialize", {"session_id": sid})
            assert generated["editor"]["versions"] == [1]
            saved = await action("self-issue-session-save-draft", {"session_id": sid, "revision": 1, "title": "Edited title", "body": "Edited Markdown"})
            assert saved["editor"]["revision"] == 2
            prior = await action("self-issue-session-draft-version", {"session_id": sid, "version": 1})
            assert prior["title"] == "Original"
            refreshed = await action("self-issue-session-get", {"session_id": sid})
            assert refreshed["editor"]["body"] == "Edited Markdown"
            rerun = await action("self-issue-session-reanalyze", {"session_id": sid, "selected_fields": ["actual_behavior"], "comment": "Check again"})
            assert len(rerun["workflow"]["reanalysis_history"]) == 1
            assert rerun["editor"]["body"] == "Edited Markdown"
    asyncio.run(exercise())
