"""Validation for the opt-in, project-owned Issue connection."""
from __future__ import annotations

from dataclasses import asdict
import re

from zf.core.config.schema import FeishuIssueMergeConfig


def build_issue_merge_config(data: object) -> FeishuIssueMergeConfig:
    from zf.core.config.loader import ConfigError

    raw = {} if data is None else data
    defaults = asdict(FeishuIssueMergeConfig())
    if not isinstance(raw, dict) or set(raw) - set(defaults):
        raise ConfigError("Invalid integrations.feishu_issue_merge fields")
    values = {**defaults, **raw}
    if type(values['enabled']) is not bool:
        raise ConfigError("feishu_issue_merge.enabled must be boolean")
    for key, prefix in [('app_id', 'cli_'), ('bot_open_id', 'ou_'),
                        ('chat_id', 'oc_'), ('operator_open_id', 'ou_'),
                        ('bitable_app_token', ''), ('bitable_table_id', 'tbl'),
                        ('bitable_view_id', 'vew')]:
        value = values[key]
        if not isinstance(value, str) or (value and not re.fullmatch(prefix + r'[A-Za-z0-9_-]+', value)):
            raise ConfigError(f"Invalid feishu_issue_merge.{key}")
    if values['enabled']:
        for key in ('app_id', 'chat_id', 'operator_open_id'):
            if not values[key]:
                raise ConfigError(f"feishu_issue_merge.{key} is required when enabled")
    if bool(values['bitable_app_token']) != bool(values['bitable_table_id']):
        raise ConfigError("Base app_token and table_id must be supplied together")
    return FeishuIssueMergeConfig(**values)
