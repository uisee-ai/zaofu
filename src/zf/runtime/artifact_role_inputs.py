"""Explain existing input profiles without creating a new selection policy."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


PROFILES = {
    'implementation': ('task', '本任务行为、共享约束、依赖接口和验证要求'),
    'task-verify': ('task', '独立读取本任务依据、候选代码和实现证据'),
    'integration-acceptance-review': ('task_integration', '本任务合同、target、独立 Verify 结论及残余集成风险'),
    'candidate-verify': ('candidate', '候选任务集合、整体矩阵与跨任务约束'),
    'thin-judge-goal-closure': ('goal', '当前 Goal/Claim、覆盖与闭环证据'),
    'plan-synth': ('plan', '完整来源覆盖、任务映射与跨任务义务'),
}


def role_input_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    profile = str((manifest.get('context_policy') or {}).get('output_profile_id') or '')
    scope, reason = PROFILES.get(profile, ('declared_manifest', '按此派发 manifest 和必读政策读取；角色范围未注册'))
    return {'profile': profile, 'scope': scope, 'reason': reason,
            'selection_supported': profile in {'implementation', 'task-verify'},
            'independent_consumption': True, 'authorizes_scope_change': False}


def render_role_input_entry(payload: Mapping[str, Any], state_dir: Path | None) -> str:
    if state_dir is None:
        return ''
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    try:
        digest = payload.get('attempt_source_manifest_sha256') or payload.get('attempt_source_manifest_digest')
        if not digest:
            raise ValueError('manifest digest required')
        manifest = hydrate_sidecar_ref(state_dir, {
            'ref': payload['attempt_source_manifest_ref'],
            'sha256': digest,
        }).payload
        attempt = str(manifest['attempt_id'])
        if attempt != str(payload.get('attempt_id') or payload.get('run_id') or ''):
            return '- Role input scope unavailable: attempt binding mismatch.'
        view = role_input_view(manifest)
    except (KeyError, ValueError, OSError, TypeError):
        return '- Role input scope unavailable; use the pinned manifest and required reads.'
    return (f"- `{view['profile'] or 'unknown'}` / `{view['scope']}`; independent. "
            '`artifact recover`, `artifact costs`.')
