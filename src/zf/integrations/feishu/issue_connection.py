"""Isolated Issue-only runtime context; legacy bots keep their own routes."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.core.config.loader import _load_dotenv
from zf.core.config.schema import FeishuIdentityConfig, FeishuIdentityUserConfig, FeishuRouteConfig
from zf.integrations.feishu.transport import FeishuHttpTransport

APP_SECRET = 'FEISHU_ISSUE_MERGE_APP_SECRET'
ACTION_SECRET = 'ZF_FEISHU_ISSUE_MERGE_ACTION_SECRET'


def connection_transport(project_root: Path, issue: Any) -> FeishuHttpTransport:
    values = _load_dotenv(Path(project_root) / '.env')
    secret = values.get(APP_SECRET, '')
    if not issue.app_id or not secret:
        raise ValueError('Issue App ID and App Secret are required')
    transport = FeishuHttpTransport(
        base_url='https://open.feishu.cn/open-apis', app_id=issue.app_id, app_secret=secret,
    )
    # Never reuse a global tenant token belonging to a different application.
    transport.tenant_access_token = ''
    return transport


def prepare_issue_bridge(context: Any) -> Any:
    """Only called in a dedicated --issue-merge child process."""
    issue = context.config.integrations.feishu_issue_merge
    values = _load_dotenv(Path(context.project_root) / '.env')
    if not issue.enabled or not all((issue.app_id, issue.chat_id, issue.operator_open_id,
                                     values.get(APP_SECRET), values.get(ACTION_SECRET))):
        raise ValueError('Feishu Issue-merge is disabled or incomplete in Settings')
    os.environ.update(FEISHU_APP_ID=issue.app_id, FEISHU_APP_SECRET=values[APP_SECRET],
                      FEISHU_BASE_URL='https://open.feishu.cn/open-apis')
    os.environ[ACTION_SECRET] = values[ACTION_SECRET]
    os.environ.pop('FEISHU_TENANT_ACCESS_TOKEN', None)
    identity = FeishuIdentityConfig(
        enabled=True, require_signed_actions=True, action_token_secret_env=ACTION_SECRET,
        users={issue.operator_open_id: FeishuIdentityUserConfig(operator='issue-merge', level='approver')},
    )
    route = FeishuRouteConfig(target='channel', allowed_senders=[issue.operator_open_id])
    config = replace(context.config, integrations=replace(
        context.config.integrations, feishu_identity=identity,
        feishu_routing={f'{issue.app_id}:{issue.chat_id}': route},
    ))
    config._feishu_issue_only = True
    return replace(context, config=config)


def issue_scope_rejection(config: Any, event: Any, command: str) -> dict[str, Any] | None:
    if not getattr(config, '_feishu_issue_only', False):
        return None
    from zf.integrations.feishu.issue_adapter import ISSUE_COMMANDS
    issue = config.integrations.feishu_issue_merge
    event_bot_open_id = str(event.payload.get('bot_open_id') or '')
    if (event.chat_id != issue.chat_id or event.user_id != issue.operator_open_id
            or str(event.payload.get('app_id') or '') != issue.app_id
            or (issue.bot_open_id and event_bot_open_id
                and event_bot_open_id != issue.bot_open_id)):
        return {'ok': False, 'status': 'rejected', 'message': 'Issue 接入的应用、群或操作者不匹配'}
    if command not in ISSUE_COMMANDS:
        return {'ok': False, 'status': 'unsupported_in_issue_merge_mode', 'message': '当前接入只支持 Issue 流程，请发送 /issue 描述'}
    return None


def issue_projection(context: Any, transport: Any, chat_id: str) -> dict[str, int]:
    """No channel, task, plan or Run Manager cards on this connection."""
    from zf.integrations.feishu.issue_adapter import push_issue_cards_once, push_self_issue_cards_once
    issue = context.config.integrations.feishu_issue_merge
    if chat_id != issue.chat_id:
        return {}
    kwargs = {'receive_id': chat_id, 'action_secret': os.environ.get(ACTION_SECRET, '').encode()}
    counts = {}
    for name, project in (
        ('issues', lambda: push_issue_cards_once(context.state_dir, transport,
            project_root=context.project_root, config=context.config, **kwargs)),
        ('self_issues', lambda: push_self_issue_cards_once(context.state_dir, transport, **kwargs)),
    ):
        try:
            result = project()
            counts[name] = len(result.get('sent', [])) + len(result.get('updated', []))
        except Exception:
            counts[name] = 0
    return counts


def issue_bitable_environment(root: Path, issue: Any) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('FEISHU_', 'LARKSUITE_CLI_'))}
    values = _load_dotenv(Path(root) / '.env')
    if not values.get(APP_SECRET):
        raise ValueError('Issue App Secret is required')
    env.update(FEISHU_APP_ID=issue.app_id, FEISHU_APP_SECRET=values[APP_SECRET],
               FEISHU_BASE_URL='https://open.feishu.cn/open-apis')
    return env
