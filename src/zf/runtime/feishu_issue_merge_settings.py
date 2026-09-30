"""CAS configuration edits for the project-owned Issue-only Feishu connection."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import secrets
import os
from typing import Any, Mapping

import yaml

from zf.core.config.feishu_issue_merge import build_issue_merge_config
from zf.core.config.loader import _load_dotenv
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.issue_settings import _patch
from zf.runtime.self_issue_source_settings import _spec

APP_SECRET = 'FEISHU_ISSUE_MERGE_APP_SECRET'
ACTION_SECRET = 'ZF_FEISHU_ISSUE_MERGE_ACTION_SECRET'


def _files(root: Path) -> tuple[Path, Path]:
    paths = (Path(root) / 'zf.yaml', Path(root) / '.env')
    if not paths[0].is_file() or any(p.is_symlink() for p in paths):
        raise ValueError('Settings require regular project configuration files')
    return paths


def _digest(root: Path) -> str:
    # Include the adapter file too: changes elsewhere must not evade CAS.
    return hashlib.sha256(json.dumps([
        (root / name).read_text() if (root / name).exists() else ''
        for name in ('zf.yaml', '.env', 'feishu.yaml')
    ]).encode()).hexdigest()


def _config(root: Path):
    from zf.core.config.loader import load_config
    return load_config(root / 'zf.yaml').integrations.feishu_issue_merge


def read_issue_merge_settings(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    _, env_path = _files(root)
    issue = _config(root)
    env = _load_dotenv(env_path)
    missing = [k for k in ('app_id', 'chat_id', 'operator_open_id') if not getattr(issue, k)]
    if not env.get(APP_SECRET):
        missing.append('app_secret')
    return {
        'ok': True, 'enabled': issue.enabled, 'configured': not missing,
        'values': asdict(issue), 'missing': missing,
        'secrets': {'app_secret_configured': bool(env.get(APP_SECRET)),
                    'action_secret_configured': bool(env.get(ACTION_SECRET))},
        'config_digest': _digest(root), 'restart_required': False,
    }


def save_issue_merge_settings(project_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(project_root)
    path, env_path = _files(root)
    changes = payload.get('changes')
    if not isinstance(changes, dict) or not changes:
        raise ValueError('Supply a nonempty set of Feishu settings changes')
    if set(changes) - (set(asdict(build_issue_merge_config(None))) | {'app_secret'}):
        raise ValueError('Unsupported Feishu settings field')
    # This lock is shared with the other Settings YAML writers.
    with locked_path(path), locked_path(env_path):
        if payload.get('config_digest') != _digest(root):
            return {'ok': False, 'status': 'config_conflict', 'reason': 'Configuration changed. Reload before saving.'}
        original = path.read_text()
        before = list(yaml.safe_load_all(original))
        raw = _spec(before).get('integrations') or {}
        if 'feishu_issue_merge' not in raw and (root / 'feishu.yaml').exists():
            adapter = yaml.safe_load((root / 'feishu.yaml').read_text()) or {}
            if 'feishu_issue_merge' in (adapter.get('integrations') or adapter):
                raise ValueError('Move feishu_issue_merge to zf.yaml before editing it in Settings')
        current = asdict(_config(root))
        clean = {k: v.strip() if isinstance(v, str) else v for k, v in changes.items() if k != 'app_secret'}
        issue = build_issue_merge_config({**current, **clean})
        env = _load_dotenv(env_path)
        secret = changes.get('app_secret', '')
        if not isinstance(secret, str) or (secret and not re.fullmatch(r'[A-Za-z0-9_+/=.-]{8,512}', secret)):
            raise ValueError('Invalid App Secret format')
        if issue.enabled and not (secret or env.get(APP_SECRET)):
            raise ValueError('App Secret is required when enabled')
        if issue.app_id != current['app_id'] and env.get(APP_SECRET) and not secret:
            raise ValueError('Enter the matching App Secret when changing App ID')
        updated = original
        for key, value in asdict(issue).items():
            updated = _patch(updated, ['integrations', 'feishu_issue_merge', key], value)
        # The Settings toggle is the single user-facing switch for this
        # connection.  Persist the managed inbound sidecar settings together
        # with the Issue connection so a subsequent ``zf start`` actually
        # starts the Issue-only bridge.  Keep the bridge mode and routing gate
        # explicit; other runtime.feishu_inbound knobs remain untouched.
        updated = _patch(
            updated,
            ['runtime', 'feishu_inbound', 'enabled'],
            bool(issue.enabled),
        )
        updated = _patch(
            updated,
            ['runtime', 'feishu_inbound', 'mode'],
            'bridge',
        )
        updated = _patch(
            updated,
            ['runtime', 'feishu_inbound', 'require_routing'],
            True,
        )
        expected = list(yaml.safe_load_all(original))
        expected_spec = _spec(expected)
        expected_spec.setdefault('integrations', {})['feishu_issue_merge'] = asdict(issue)
        expected_runtime = expected_spec.setdefault('runtime', {})
        expected_inbound = expected_runtime.setdefault('feishu_inbound', {})
        expected_inbound.update(
            enabled=bool(issue.enabled), mode='bridge', require_routing=True,
        )
        if list(yaml.safe_load_all(updated)) != expected:
            raise ValueError('Unrelated configuration would change')
        new_secrets = {}
        if secret:
            new_secrets[APP_SECRET] = secret
        if not env.get(ACTION_SECRET):
            new_secrets[ACTION_SECRET] = secrets.token_urlsafe(32)
        if new_secrets:
            # Replace every duplicate/exported occurrence of only our keys.
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            lines = [line for line in lines if line.strip().removeprefix('export ').split('=', 1)[0].strip() not in new_secrets]
            lines.extend(f'{key}={value}' for key, value in new_secrets.items())
            atomic_write_text(env_path, '\n'.join(lines) + '\n')  # mkstemp creates mode 0600
            os.chmod(env_path, 0o600)
        atomic_write_text(path, updated)
    return {
        **read_issue_merge_settings(root),
        'status': 'saved',
        'changed_fields': sorted(changes),
        # The bridge is a zf start sidecar.  Its credentials and command line
        # are captured at process start, so a running process must be restarted
        # before a saved connection can receive messages.
        'restart_required': True,
    }


def test_issue_merge_connection(project_root: Path) -> dict[str, Any]:
    from zf.integrations.feishu.issue_connection import connection_transport
    from zf.integrations.feishu.transport import FeishuTransportError

    snapshot = read_issue_merge_settings(project_root)
    if not snapshot['configured']:
        return {'ok': False, 'status': 'incomplete', 'reason': 'Missing: ' + ', '.join(snapshot['missing']), 'checks': {}}
    issue = _config(Path(project_root))
    checks: dict[str, str] = {}
    stage = 'credentials'
    try:
        transport = connection_transport(Path(project_root), issue)
        transport.resolve_tenant_access_token()
        checks[stage] = 'completed'
        stage = 'bot'
        bot = transport._request_json('GET', '/bot/v3/info', None)
        bot_open_id = str((bot.get('bot') or {}).get('open_id') or '')
        if not bot_open_id:
            raise ValueError('Bot capability is unavailable')
        if issue.bot_open_id and issue.bot_open_id != bot_open_id:
            raise ValueError('Configured Bot Open ID does not match the Feishu app')
        checks[stage] = 'completed'
        stage = 'chat'
        transport._request_json('GET', f'/im/v1/chats/{issue.chat_id}', None)
        checks[stage] = 'completed'
        stage = 'bitable'
        if issue.bitable_app_token:
            try:
                transport._request_json('GET', f'/bitable/v1/apps/{issue.bitable_app_token}/tables/{issue.bitable_table_id}/fields?page_size=1', None)
            except (FeishuTransportError, OSError, ValueError) as exc:
                checks[stage] = 'failed'
                if '91403' in str(exc):
                    reason = (
                        '消息接入检查已通过，但当前机器人无法访问该 Base '
                        '(Feishu 91403)。请确认 base:field:read 已对当前 App 生效（权限变更后需发布应用新版本），'
                        '并在 Base 的权限设置中把当前 App/机器人加入为协作者；仅给个人账号编辑权限不等于给机器人授权。'
                    )
                else:
                    reason = '消息接入检查已通过，但 Base 只读检查失败，请检查 Base 授权和 table_id。'
                return {
                    'ok': True,
                    'status': 'message_connected_bitable_failed',
                    'checks': checks,
                    'message': reason,
                }
            checks[stage] = 'completed'
        return {'ok': True, 'status': 'read_checks_passed', 'checks': checks,
                'message': '只读检查通过；事件订阅、发送和写表权限请按接入说明配置，并在群中发送 /issue 验证。'}
    except (FeishuTransportError, OSError, ValueError) as exc:
        checks[stage] = 'failed'
        # Provider exception bodies can echo tokens/secrets: never return them.
        if stage == 'bitable' and '91403' in str(exc):
            reason = (
                '消息接入检查已通过，但当前机器人无法访问该 Base '
                '(Feishu 91403)。请确认 base:field:read 已对当前 App 生效（权限变更后需发布应用新版本），'
                '并在 Base 的权限设置中把当前 App/机器人加入为协作者；仅给个人账号编辑权限不等于给机器人授权。'
            )
        elif stage == 'chat':
            reason = '群检查失败，请确认机器人已加入该群，并检查应用的群消息权限。'
        elif stage == 'bot':
            reason = '机器人检查失败，请确认应用已启用机器人能力并已发布。'
        else:
            reason = f'{stage} 检查失败，请检查飞书应用凭证和权限。'
        return {'ok': False, 'status': 'connection_failed', 'checks': checks,
                'reason': reason}
