import subprocess

import httpx
import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, SelfIssueConfig, SelfIssueTargetConfig, ZfConfig
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config, inject_self_issue_policy
from zf.runtime.self_issue_session_host import SelfIssueSessionHost
from zf.runtime.self_issue_source_settings import current_source_policy, inspect_source, save_source_settings, source_settings
from zf.runtime.self_issue_target import resolve_self_issue_target_root
from zf.web.server import create_app


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(root):
    root.mkdir()
    git(root, 'init', '-b', 'test')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-m', 'initial')
    return root


@pytest.mark.parametrize('config', [
    '# preserve comment\nversion: "1.0"\nproject: {name: test}\n',
    'version: "1.0"\nproject: {name: test}\nself_issue: {source_mode: current_project, enabled: false} # preserve comment\n',
    'version: "1.0"\nproject: {name: test}\nself_issue:\n  # preserve comment\n  enabled: false\n  source_root: "old"\n  source_mode: current_project\n',
    'kind: Workflow\nspec: {example: retained}\n---\nkind: ZfConfig\napiVersion: zaofu.dev/v1\nspec:\n  # preserve comment\n  version: "1.0"\n  project: {name: test}\n',
])
def test_settings_patch_preserves_other_config_and_cas(tmp_path, config):
    path = tmp_path / 'zf.yaml'
    path.write_text(config)
    target = repository(tmp_path / 'product with spaces')
    initial = source_settings(tmp_path, None)
    saved = save_source_settings(tmp_path, None, {'source_mode': 'explicit_target', 'source_root': str(target), 'config_digest': initial['config_digest']})
    assert saved['valid'] and saved['source_mode'] == 'explicit_target'
    assert '# preserve comment' in path.read_text()
    local = current_source_policy(tmp_path, None)
    assert resolve_self_issue_target_root(tmp_path, local) == target
    conflict = save_source_settings(tmp_path, None, {'source_mode': 'current_project', 'config_digest': initial['config_digest']})
    assert conflict['status'] == 'config_conflict'
    reset = save_source_settings(tmp_path, None, {'source_mode': 'current_project', 'config_digest': saved['config_digest']})
    assert reset['configured_root'] == '' and reset['inspection']['source_root'] == str(tmp_path)
    assert list(yaml.safe_load_all(path.read_text()))


def test_source_inspection_records_dirty_branch_and_redacts_remote_credentials(tmp_path):
    root = repository(tmp_path / 'product')
    git(root, 'remote', 'add', 'origin', 'https://user:password@example.org/owner/repo.git?token=secret')
    clean = inspect_source(root)
    assert clean['revision'] == git(root, 'rev-parse', 'HEAD')
    assert clean['branch'] == 'test' and clean['dirty'] is False
    assert clean['remote'] == 'https://example.org/owner/repo.git'
    (root / 'untracked.txt').write_text('new')
    assert inspect_source(root)['dirty'] is True
    assert inspect_source(tmp_path)['is_git'] is False
    with pytest.raises(ValueError, match='目录'):
        inspect_source(tmp_path / 'absent')


def test_config_loader_and_workspace_target_lock_keep_project_source_choice(tmp_path):
    path = tmp_path / 'zf.yaml'
    path.write_text('version: "1.0"\nproject: {name: test}\nself_issue: {source_mode: explicit_target, source_root: /product}\n')
    config = load_config(path)
    assert config.self_issue.source_root == '/product'
    workspace = ZfConfig(self_issue=SelfIssueConfig(enabled=True, target_locked=True, target_project='owner/issues'))
    inherited = inherit_workspace_self_issue_config(config, workspace)
    assert inherited.self_issue.source_root == '/product'
    assert inherited.self_issue.target_project == 'owner/issues'
    assert source_settings(tmp_path, inherited.self_issue)['publication_targets'] == [
        {'provider': 'gitlab', 'project': 'owner/issues'},
    ]
    docs = inject_self_issue_policy([{'kind': 'ZfConfig', 'spec': {'self_issue': {
        'source_mode': 'explicit_target', 'source_root': '/child',
    }}}], workspace.self_issue)
    assert next(doc for doc in docs if doc.get('kind') == 'ZfConfig')['spec']['self_issue']['source_root'] == '/child'


def test_local_source_setting_resolves_environment_and_relative_path(tmp_path, monkeypatch):
    monkeypatch.setenv('ISSUE_TEST_SOURCE', 'product')
    (tmp_path / 'zf.yaml').write_text('self_issue: {source_mode: explicit_target, source_root: "${ISSUE_TEST_SOURCE}"}\n')
    policy = current_source_policy(tmp_path, None)
    assert resolve_self_issue_target_root(tmp_path, policy) == tmp_path / 'product'


@pytest.mark.parametrize('targets, expected', [
    ({}, [{'provider': 'github', 'project': 'owner/issues'}]),
    ({'github': SelfIssueTargetConfig(project='owner/other-issues'),
      'gitlab': SelfIssueTargetConfig(project='group/reports')},
     [{'provider': 'github', 'project': 'owner/other-issues'},
      {'provider': 'gitlab', 'project': 'group/reports'}]),
])
def test_publication_projection_is_independent_of_source_and_uses_effective_policy(tmp_path, targets, expected):
    (tmp_path / 'zf.yaml').write_text('project: {name: test}\n')
    root = repository(tmp_path / 'product')
    git(root, 'remote', 'add', 'origin', 'https://github.com/source/product.git')
    policy = SelfIssueConfig(provider='github', target_project='owner/issues', targets=targets)
    settings = source_settings(tmp_path, policy)
    candidate = {'source_mode': 'explicit_target', 'source_root': str(root),
                 'config_digest': settings['config_digest']}
    inspected = source_settings(tmp_path, policy, candidate)
    saved = save_source_settings(tmp_path, policy, candidate)
    for result in (settings, inspected, saved, source_settings(tmp_path, policy)):
        assert result['publication_targets'] == expected
    assert saved['inspection']['remote'] == 'https://github.com/source/product.git'
    assert source_settings(tmp_path, SelfIssueConfig())['publication_targets'] == []


async def test_settings_actions_auth_hot_reload_and_session_pin(tmp_path, monkeypatch):
    (tmp_path / 'zf.yaml').write_text('version: "1.0"\nproject: {name: test, state_dir: .state}\nself_issue: {enabled: true}\n')
    first = repository(tmp_path / 'first')
    second = repository(tmp_path / 'second')
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN', 'source-test-token')
    state = tmp_path / '.state'
    state.mkdir()
    captures = []

    def scheduled(host, sid):
        captures.append((sid, host.project_root))
        return host.get(sid)

    monkeypatch.setattr(SelfIssueSessionHost, '_schedule', scheduled)
    app = create_app(state, config=ZfConfig(project=ProjectConfig(name='test', state_dir='.state')), project_root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        async def call(action, payload=None):
            response = await client.post('/api/actions/' + action, json=payload or {}, headers={'X-ZF-Web-Token': 'source-test-token'})
            assert response.status_code == 200, response.text
            assert response.json()['ok'], response.text
            return response.json()

        denied = await client.post('/api/actions/self-issue-source-save', json={'source_root': str(first)})
        assert denied.status_code == 403
        settings = await call('self-issue-source-get')
        assert not (state / 'web-actions/idempotency.jsonl').exists()
        for root in (first, second):
            inspected = await call('self-issue-source-inspect', {'source_mode': 'explicit_target', 'source_root': str(root)})
            settings = await call('self-issue-source-save', {'source_mode': 'explicit_target', 'source_root': str(root), 'config_digest': inspected['config_digest']})
            created = await call('self-issue-capture', {'portable_session': True, 'description': 'CLI fails'})
            assert captures[-1][1] == root
            assert created['source_binding']['repository_root'] == str(root)
            assert created['source_binding']['dirty'] is False
            if root == first:
                old_id = created['session_id']
        await call('self-issue-session-pause', {'session_id': old_id})
        await call('self-issue-session-retry', {'session_id': old_id})
        assert captures[-1] == (old_id, first)
        old = await call('self-issue-session-get', {'session_id': old_id})
        assert old['source_binding']['repository_root'] == str(first)
        assert settings['inspection']['source_root'] == str(second)


def test_invalid_target_does_not_change_configuration(tmp_path):
    path = tmp_path / 'zf.yaml'
    path.write_text('project: {name: test}\n')
    original = path.read_bytes()
    current = source_settings(tmp_path, None)
    with pytest.raises(ValueError, match='目录'):
        save_source_settings(tmp_path, None, {'source_mode': 'explicit_target', 'source_root': str(tmp_path / 'absent'), 'config_digest': current['config_digest']})
    assert path.read_bytes() == original
