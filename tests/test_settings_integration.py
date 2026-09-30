from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.config.schema import SelfIssueConfig
from zf.core.security.capture_urls import normalize_capture_url, blocked_capture_url, validate_capture_urls
from zf.runtime.issue_settings import current_issue_policy, policy_projection, save_issue_policy
from zf.runtime.self_issue_browser_evidence import capture_self_issue_browser_evidence, _capture_script
from zf.runtime.self_issue_source_settings import source_settings, save_source_settings
from zf.web.developer_access import COOKIE, DeveloperSessions, install_developer_access
from zf.web.server import _inject_developer_token, _web_mutation_auth_error, _web_triage_auth_error, _web_session_cookie
from zf.web.settings_routes import build_settings_router

CONFIG = '''# retain this comment
version: "1.0"
project: {name: settings-test, state_dir: .state}
self_issue:
  enabled: true
  provider: github
  default_publication_mode: github
  automatic_detection_enabled: true
  browser_capture_enabled: true
  browser_capture_base_url: http://127.0.0.1:8002
  source_mode: current_project
  targets:
    github: {project: example/reports, authorization_domain: github.com}
  ingress:
    enabled: false
    poll_interval_seconds: 300
    approval_label: preserved-approval
    auto_triage_new_only: true
'''

def setup(tmp_path, monkeypatch, *, workspace=None):
    root = tmp_path / 'project';root.mkdir()
    (root/'zf.yaml').write_text(CONFIG)
    state = root/'.state';state.mkdir()
    config = load_config(root/'zf.yaml')
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN','ordinary-user-token')
    monkeypatch.setenv('ZF_WEB_DEVELOPER_TOKEN','actual-develop-token')
    monkeypatch.delenv('ZF_WEB_TRUSTED_SESSION',raising=False)
    app=FastAPI();install_developer_access(app,state)
    ctx=SimpleNamespace(project_root=root,state_dir=state,config=config)
    app.include_router(build_settings_router(resolve_ctx=lambda p:ctx,workspace_root=workspace or root,
        workspace_config=load_config(workspace/'zf.yaml') if workspace else config,
        authorize=_web_mutation_auth_error,authorize_developer=_web_triage_auth_error,session_cookie=_web_session_cookie))
    @app.get('/capability')
    def capability():
        return {'allowed': _web_triage_auth_error('read',authorization=None,x_zf_web_token=None) is None}
    return root,config,TestClient(app)

USER={'X-ZF-Web-Token':'ordinary-user-token'}
URL='/api/projects/test/settings/issue'

def test_real_develop_token_cookie_revocation_rotation_and_csrf(tmp_path,monkeypatch):
    root,config,client=setup(tmp_path,monkeypatch)
    assert not client.get('/api/settings/access').json()['verified']
    assert client.get('/capability').json()['allowed'] is False
    assert client.post('/api/settings/access/unlock',json={'token':'wrong'}).status_code==403
    assert client.get('/capability').json()['allowed'] is False
    result=client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'})
    assert result.status_code==200 and result.json()['verified'] is True
    cookie=result.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=' in cookie
    assert 'actual-develop-token' not in result.text+cookie
    assert client.get('/capability').json()['allowed'] is True
    assert client.get(URL).status_code==200  # authenticated developer cookie also authorizes ordinary settings
    assert client.post('/api/settings/access/forget',headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.get('/capability').json()['allowed'] is True
    saved_cookie=client.cookies.get(COOKIE)
    assert DeveloperSessions(root/'.state').lookup(saved_cookie) # survives new server-side store instance
    assert client.post('/api/settings/access/forget').status_code==200
    assert DeveloperSessions(root/'.state').lookup(saved_cookie) is None
    client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'})
    monkeypatch.setenv('ZF_WEB_DEVELOPER_TOKEN','rotated-token')
    assert client.get('/capability').json()['allowed'] is False
    assert not client.get('/api/settings/access').json()['verified']


def test_unconfigured_token_and_rate_limit(tmp_path,monkeypatch):
    _,_,client=setup(tmp_path,monkeypatch)
    for _ in range(5):assert client.post('/api/settings/access/unlock',json={'token':'wrong'}).status_code==403
    assert client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'}).status_code==429
    monkeypatch.delenv('ZF_WEB_DEVELOPER_TOKEN')
    assert not client.get('/api/settings/access').json()['configured']


def test_page_never_injects_developer_secret(tmp_path,monkeypatch):
    monkeypatch.setenv('ZF_WEB_DEVELOPER_TOKEN','must-not-be-in-html')
    file=tmp_path/'index.html';file.write_text('<html><head></head><body>Settings</body></html>')
    assert _inject_developer_token(file)==file.read_text()


def test_reporting_switch_preserves_locked_target(tmp_path, monkeypatch):
    root, config, client = setup(tmp_path, monkeypatch)
    path = root / 'zf.yaml'
    path.write_text(path.read_text().replace('  enabled: true', '  enabled: true\n  target_locked: true', 1))
    for enabled in (False, True):
        before = client.get(URL, headers=USER).json()
        result = client.patch(URL, headers=USER, json={
            'config_digest': before['config_digest'], 'changes': {'enabled': enabled},
        })
        assert result.status_code == 200, result.text
        assert result.json()['values']['enabled'] is enabled
        saved = load_config(path).self_issue
        assert saved.enabled is enabled
        assert saved.target_locked is True
        assert saved.targets == config.self_issue.targets
        assert current_issue_policy(root, config.self_issue).enabled is enabled


def test_policy_read_user_save_cas_and_preservation(tmp_path,monkeypatch):
    root,config,client=setup(tmp_path,monkeypatch)
    assert client.get(URL).status_code==403
    before=client.get(URL,headers=USER).json()
    assert before['values']['enabled'] is True
    assert 'ingress.enabled' not in before['values']
    denied=client.patch(URL,headers=USER,json={'config_digest':before['config_digest'],'changes':{'ingress.enabled':True}})
    assert denied.status_code==403
    response=client.patch(URL,headers=USER,json={'config_digest':before['config_digest'],'changes':{
        'automatic_detection_enabled':False,'browser_capture_denied_urls':['https://EXAMPLE.com/private/?x=1','http://127.0.0.1:8002/secret'],
    }})
    assert response.status_code==200,response.text
    current=response.json();assert current['values']['automatic_detection_enabled'] is False
    assert current['values']['browser_capture_denied_urls'][0]=='https://example.com/private'
    assert current['restart_required'] is False
    updated=(root/'zf.yaml').read_text()
    assert '# retain this comment' in updated and 'preserved-approval' in updated
    saved=yaml.safe_load(updated)['self_issue']
    assert saved['browser_capture_base_url']=='http://127.0.0.1:8002'
    assert saved['targets']==yaml.safe_load(CONFIG)['self_issue']['targets']
    assert saved['ingress']==yaml.safe_load(CONFIG)['self_issue']['ingress']
    assert current_issue_policy(root,config.self_issue).automatic_detection_enabled is False
    # Current policy remains bound to the config owner even if the agent's code checkout differs.
    assert current_issue_policy(tmp_path/'different-checkout',config.self_issue).browser_capture_denied_urls==current['values']['browser_capture_denied_urls']
    conflict=client.patch(URL,headers=USER,json={'config_digest':before['config_digest'],'changes':{'enabled':False}})
    assert conflict.status_code==409
    assert yaml.safe_load((root/'zf.yaml').read_text())['self_issue']['enabled'] is True
    events=[json.loads(line) for line in (root/'.state/events.jsonl').read_text().splitlines()]
    assert events[-1]['type']=='settings.issue.updated'
    assert set(events[-1]['payload']['changed_fields'])=={'automatic_detection_enabled','browser_capture_denied_urls'}


def test_developer_policy_large_range_and_live_poller_boundary(tmp_path,monkeypatch):
    root,config,client=setup(tmp_path,monkeypatch)
    client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'})
    initial=client.get(URL).json()
    assert initial['values']['ingress.enabled'] is False
    response=client.patch(URL,json={'config_digest':initial['config_digest'],'changes':{'ingress.enabled':True,'ingress.poll_interval_seconds':604800}})
    assert response.status_code==200,response.text
    assert response.json()['restart_required'] is False
    assert load_config(root/'zf.yaml').self_issue.ingress.poll_interval_seconds==604800
    assert config.self_issue.ingress.enabled is False # in-flight config stays immutable
    from zf.runtime.external_issue_ingress import build_external_issue_poller
    poller = build_external_issue_poller(root / '.state', config, project_root=root)
    assert poller is not None  # can enable a poller that started disabled
    assert poller.refresh_policy() is True
    assert poller.interval_seconds == 604800
    assert poller.service.reconciler.minimum_interval_seconds == 604800
    assert poller.service.policy.enabled is True
    paused = client.patch(URL, json={'config_digest': response.json()['config_digest'], 'changes': {'ingress.enabled': False}})
    assert paused.status_code == 200
    assert poller.refresh_policy() is False
    assert poller.service.poll_once().status == 'disabled'
    assert client.patch(URL,json={'config_digest':response.json()['config_digest'],'changes':{'ingress.poll_interval_seconds':True}}).status_code==422
    assert client.patch(URL,json={'config_digest':response.json()['config_digest'],'changes':{'ingress.poll_interval_seconds':604801}}).status_code==422


@pytest.mark.parametrize('field,value', [
    ('enabled', False), ('automatic_detection_enabled', False),
    ('browser_capture_enabled', False),
    ('browser_capture_denied_urls', ['https://example.com/private']),
    ('ingress.enabled', True), ('ingress.poll_interval_seconds', 600),
])
def test_each_writable_settings_field_roundtrips_through_api_and_config(tmp_path, monkeypatch, field, value):
    root, config, client = setup(tmp_path, monkeypatch)
    developer = field.startswith('ingress.')
    if developer:
        assert client.post('/api/settings/access/unlock', json={'token':'actual-develop-token'}).status_code == 200
    before = client.get(URL, headers=USER).json()
    response = client.patch(URL, headers=USER, json={
        'config_digest': before['config_digest'], 'changes': {field: value},
    })
    assert response.status_code == 200, response.text
    assert response.json()['restart_required'] is False
    after = client.get(URL, headers=USER).json()
    assert after['values'][field] == value
    assert after['config_digest'] != before['config_digest']
    saved = load_config(root/'zf.yaml').self_issue
    owner = saved.ingress if developer else saved
    assert getattr(owner, field.split('.')[-1]) == value
    assert {key: val for key, val in after['values'].items() if key != field} == {
        key: val for key, val in before['values'].items() if key != field
    }
    if not developer:
        assert getattr(current_issue_policy(root, config.self_issue), field) == value


def test_workspace_inheritance_requires_confirmation_and_preserves_source(tmp_path,monkeypatch):
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'zf.yaml').write_text(CONFIG.replace('  enabled: true\n','  enabled: true\n  target_locked: true\n',1))
    root,config,client=setup(tmp_path,monkeypatch,workspace=workspace)
    before=client.get(URL,headers=USER).json()
    assert before['inherited'] and before['config_path']==str(workspace/'zf.yaml')
    body={'config_digest':before['config_digest'],'changes':{'browser_capture_enabled':False}}
    assert client.patch(URL,headers=USER,json=body).status_code==422
    body['confirm_workspace_scope']=True
    assert client.patch(URL,headers=USER,json=body).status_code==200
    assert yaml.safe_load((root/'zf.yaml').read_text())['self_issue']['browser_capture_enabled'] is True
    assert yaml.safe_load((workspace/'zf.yaml').read_text())['self_issue']['browser_capture_enabled'] is False


@pytest.mark.parametrize('part',[
    'browser_capture_denied_urls: []',
    'browser_capture_denied_urls: ["https://example.com/a"]',
    'browser_capture_denied_urls:\n    - https://example.com/a\n    - https://example.com/b',
])
def test_url_list_yaml_shapes_preserve_other_fields(tmp_path,part):
    path=tmp_path/'zf.yaml';path.write_text(CONFIG+'  '+part+'\n  structure_backend: ""\n')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    result=save_issue_policy(path,{'config_digest':digest,'changes':{'browser_capture_denied_urls':['https://other.example/private']}},developer=False)
    assert result['ok'] and yaml.safe_load(path.read_text())['self_issue']['structure_backend']==''
    assert yaml.safe_load(path.read_text())['self_issue']['browser_capture_denied_urls']==['https://other.example/private']


@pytest.mark.parametrize('url,blocked',[
 ('https://example.com/admin',True),('https://example.com/admin/a',True),('https://example.com/administrator',False),
 ('https://EXAMPLE.com:443/admin/?key=test#tab',True),('http://example.com/admin',False),('https://example.com:444/admin',False),
 ('https://sub.example.com/admin',False),('https://example.com/%61dmin/a',True),
])
def test_capture_rule_match_boundaries(url,blocked):
    assert bool(blocked_capture_url(url,['https://example.com/admin']))==blocked


@pytest.mark.parametrize('url',['file:///etc/passwd','javascript:alert(1)','https://x:y@example.com/private','https://*.example.com/','https://example.com/a%2fb','https://example.com/%252f','https://example.com/../secret','https://example.com/a\\b'])
def test_capture_rule_invalid_values(url):
    with pytest.raises((ValueError,UnicodeError)):normalize_capture_url(url)


def test_denied_capture_stops_before_runner_or_output(tmp_path):
    calls=[]
    result=capture_self_issue_browser_evidence(state_dir=tmp_path,draft_id='draft',run_id='run',reporter_context={
        'browser_capture':{'requested':True,'target':'kanban_board','base_url':'http://127.0.0.1:8002'},
    },enabled=True,denied_urls=['http://127.0.0.1:8002/'],runner=lambda *a,**k:calls.append(a))
    assert result.status=='blocked_by_policy' and not calls
    assert not (tmp_path/'artifacts').exists()
    script=_capture_script(['http://127.0.0.1:8002/private'])
    assert "context.route('**/*'" in script and "page.frames().some" in script and "serviceWorkers: 'block'" in script


def test_actual_app_routes_keep_token_private_and_share_verified_capability(tmp_path,monkeypatch):
    from zf.web.server import create_app
    root,config,_=setup(tmp_path,monkeypatch)
    (root/'.state/kanban.json').write_text('[]')
    (root/'.state/events.jsonl').write_text('')
    client=TestClient(create_app(root/'.state',config=config,project_root=root))
    assert 'actual-develop-token' not in client.get('/').text
    assert client.get('/api/settings/access').status_code==200
    # The default-project alias resolves through the actual server resolver.
    assert client.get('/api/projects/default/settings/issue',headers=USER).status_code==200,client.get('/api/projects/default/settings/issue',headers=USER).text
    assert client.get('/api/web-session',headers=USER).json()['can_view_issue_triage'] is False
    assert client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'}).status_code==200
    assert client.get('/api/web-session').json()['can_view_issue_triage'] is True
    assert client.get('/api/projects/default/settings/issue').json()['values']['ingress.poll_interval_seconds']==300
    read=client.post('/api/projects/default/actions/self-issue-source-get',json={'project_id':'default','payload':{}})
    assert read.status_code==200,read.text
    assert read.json()['publication_targets']==[{'provider':'github','project':'example/reports'}]
    checkout=tmp_path/'checkout';checkout.mkdir();(checkout/'source.py').write_text('pass\n')
    probe=client.post('/api/projects/default/actions/self-issue-source-inspect',json={'project_id':'default','payload':{'source_mode':'explicit_target','source_root':str(checkout)}}).json()
    assert probe['valid']
    saved=client.post('/api/projects/default/actions/self-issue-source-save',json={'project_id':'default','payload':{'source_mode':'explicit_target','source_root':str(checkout),'config_digest':probe['config_digest']}})
    assert saved.status_code==200,saved.text
    assert saved.json()['ok'] and saved.json()['publication_targets']==read.json()['publication_targets']
    assert yaml.safe_load((root/'zf.yaml').read_text())['self_issue']['source_root']==str(checkout)
    assert client.post('/api/settings/access/forget').status_code==200
    assert client.get('/api/web-session').json()['can_view_issue_triage'] is False
    assert client.get('/api/projects/default/settings/issue').status_code==403


def test_developer_expiry_and_damaged_store_fail_closed(tmp_path,monkeypatch):
    root,_,client=setup(tmp_path,monkeypatch)
    monkeypatch.setenv('ZF_WEB_DEVELOPER_SESSION_SECONDS','3600')
    client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'})
    store=DeveloperSessions(root/'.state')
    session=client.cookies.get(COOKIE)
    assert store.lookup(session)
    import sqlite3
    with sqlite3.connect(store.path) as database:
        database.execute('UPDATE sessions SET expires=0')
    assert not client.get('/api/settings/access').json()['verified']
    store.path.write_bytes(b'invalid sqlite file')
    assert client.get('/api/settings/access').status_code==200
    assert not client.get('/api/settings/access').json()['verified']
    assert client.post('/api/settings/access/unlock',json={'token':'actual-develop-token'}).status_code==503


def test_policy_patch_multidocument_flow_mapping_and_symlink(tmp_path):
    # Field edits must not replace unrelated documents, comments or nested policy.
    raw='kind: Workflow\nspec: {name: retained}\n---\nkind: ZfConfig\nspec:\n  self_issue: {enabled: false, source_root: /existing, browser_capture_denied_urls: []} # retain\n'
    path=tmp_path/'zf.yaml';path.write_text(raw)
    result=save_issue_policy(path,{'config_digest':hashlib.sha256(path.read_bytes()).hexdigest(),'changes':{'browser_capture_denied_urls':['https://example.com/private']}},developer=False)
    assert result['ok'] and '# retain' in path.read_text()
    docs=list(yaml.safe_load_all(path.read_text()))
    assert docs[0]=={'kind':'Workflow','spec':{'name':'retained'}}
    assert docs[1]['spec']['self_issue']['source_root']=='/existing'
    link=tmp_path/'linked.yaml';link.symlink_to(path)
    with pytest.raises(ValueError):save_issue_policy(link,{'config_digest':hashlib.sha256(path.read_bytes()).hexdigest(),'changes':{'enabled':False}},developer=False)


def test_new_project_inherits_capture_exclusions_without_origin_path_metadata(tmp_path):
    from dataclasses import replace
    from zf.core.config.self_issue_policy import inject_self_issue_policy
    path=tmp_path/'zf.yaml';path.write_text(CONFIG)
    policy=replace(load_config(path).self_issue,target_locked=True,
                   browser_capture_denied_urls=['https://example.com/private'])
    documents=[{'kind':'ZfConfig','spec':{'project':{'name':'new-project'},'self_issue':{'source_mode':'explicit_target','source_root':'/project/own-source'}}}]
    result=inject_self_issue_policy(documents,policy)
    child=next(doc for doc in result if doc.get('kind')=='ZfConfig')['spec']['self_issue']
    assert child['browser_capture_denied_urls']==['https://example.com/private']
    assert child['source_root']=='/project/own-source'
    assert 'settings_config_path' not in child
    assert 'browser_capture_denied_urls' not in documents[0]['spec']['self_issue']
    child['browser_capture_denied_urls'].append('https://example.com/other')
    assert policy.browser_capture_denied_urls==['https://example.com/private']


def test_full_app_distinguishes_missing_endpoint_and_reads_and_saves_real_policy(tmp_path, monkeypatch):
    """Hit actual registered routes, not just an isolated settings router/mock."""
    from zf.web.server import create_app
    root, config, _ = setup(tmp_path, monkeypatch)
    (root / '.state/kanban.json').write_text('[]')
    (root / '.state/events.jsonl').write_text('')
    app = create_app(root / '.state', config=config, project_root=root)
    client = TestClient(app)
    route = '/api/projects/default/settings/issue'
    paths = client.get('/openapi.json').json()['paths']
    assert {'get', 'patch'} <= set(paths['/api/projects/{project_id}/settings/issue'])
    # The screenshot's generic "not found" is the catch-all route, not a false
    # policy value. Lack of authorization is a different failure, not a 404.
    assert client.get('/api/settings/nonexistent').json() == {'detail': 'not found'}
    assert client.get(route).status_code == 403
    before = client.get(route, headers=USER)
    assert before.status_code == 200, before.text
    initial = before.json()
    assert initial['values']['enabled'] is True
    assert initial['values']['browser_capture_enabled'] is True
    assert not any(key.startswith('ingress.') for key in initial['values'])
    original = yaml.safe_load((root / 'zf.yaml').read_text())
    saved = client.patch(route, headers=USER, json={
        'config_digest': initial['config_digest'],
        'changes': {'automatic_detection_enabled': False,
                    'browser_capture_denied_urls': ['https://example.com/private']},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()['values']['automatic_detection_enabled'] is False
    actual = yaml.safe_load((root / 'zf.yaml').read_text())
    original['self_issue'].update(automatic_detection_enabled=False,
                                 browser_capture_denied_urls=['https://example.com/private'])
    assert actual == original
    assert client.get(route, headers=USER).json()['values'] == saved.json()['values']
    # No fallback to an older action path is allowed for a failed configuration write.
    conflict = client.patch(route, headers=USER, json={
        'config_digest': initial['config_digest'], 'changes': {'enabled': False},
    })
    assert conflict.status_code == 409
    assert client.get(route, headers=USER).json()['values']['enabled'] is True


def test_feishu_issue_merge_settings_is_cas_and_does_not_echo_secret(tmp_path, monkeypatch):
    root, config, client = setup(tmp_path, monkeypatch)
    url = '/api/projects/test/settings/feishu-issue-merge'
    before = client.get(url, headers=USER)
    assert before.status_code == 200
    snapshot = before.json()
    assert snapshot['configured'] is False
    saved = client.patch(url, headers=USER, json={
        'config_digest': snapshot['config_digest'],
        'changes': {
            'enabled': True, 'app_id': 'cli_test', 'app_secret': 'secret_12345',
            'bot_open_id': 'ou_bot',
            'chat_id': 'oc_test', 'operator_open_id': 'ou_test',
            'bitable_app_token': 'app_test', 'bitable_table_id': 'tbl_test',
            'bitable_view_id': 'vew_test',
        },
    })
    assert saved.status_code == 200, saved.text
    assert 'secret_12345' not in saved.text
    assert saved.json()['values']['bot_open_id'] == 'ou_bot'
    assert saved.json()['values']['bitable_view_id'] == 'vew_test'
    persisted = yaml.safe_load((root / 'zf.yaml').read_text())
    assert persisted['runtime']['feishu_inbound'] == {
        'enabled': True, 'mode': 'bridge', 'require_routing': True,
    }
    assert client.get(url, headers=USER).json()['secrets']['app_secret_configured'] is True
    assert 'secret_12345' not in client.get(url, headers=USER).text
    conflict = client.patch(url, headers=USER, json={
        'config_digest': snapshot['config_digest'], 'changes': {'enabled': False},
    })
    assert conflict.status_code == 409
    assert 'secret_12345' not in (root / 'zf.yaml').read_text()
    assert 'secret_12345' in (root / '.env').read_text()
    assert (root / '.env').stat().st_mode & 0o077 == 0

    disabled = client.get(url, headers=USER).json()
    result = client.patch(url, headers=USER, json={
        'config_digest': disabled['config_digest'], 'changes': {'enabled': False},
    })
    assert result.status_code == 200, result.text
    assert yaml.safe_load((root / 'zf.yaml').read_text())['runtime']['feishu_inbound']['enabled'] is False


def test_feishu_issue_merge_settings_requires_connection_inputs(tmp_path, monkeypatch):
    _, _, client = setup(tmp_path, monkeypatch)
    url = '/api/projects/test/settings/feishu-issue-merge'
    result = client.post(url + '/test', headers=USER)
    assert result.status_code == 422
    assert result.json()['status'] == 'incomplete'


def test_feishu_issue_merge_settings_returns_validation_errors_as_422(tmp_path, monkeypatch):
    root, _, client = setup(tmp_path, monkeypatch)
    url = '/api/projects/test/settings/feishu-issue-merge'
    before = client.get(url, headers=USER).json()
    result = client.patch(url, headers=USER, json={
        'config_digest': before['config_digest'],
        'changes': {'enabled': True, 'app_id': 'invalid'},
    })
    assert result.status_code == 422
    assert 'app_id' in result.json()['reason']
    assert 'invalid' not in (root / 'zf.yaml').read_text()
