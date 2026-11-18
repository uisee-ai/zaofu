"""Applied Controller policy overlay; local zf.yaml retains identity and consent."""

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import re
import time

from zf.integrations.controller.installation import Installation, private_read
from zf.integrations.controller.ledger import Ledger


class PolicyNotApplied(ValueError):
    pass


def validate(limits):
    settings = limits.get('runtime_settings')
    if settings is None:
        return None
    keys = {'protocol', 'interval_seconds', 'batch_size', 'diagnostic_sample_rate', 'mode',
            'request_tokens', 'request_money', 'price_version', 'request_seconds'}
    if not isinstance(settings, dict) or set(settings) - {'provider_modes'} != keys:
        raise ValueError('unsupported runtime settings')
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != limits.get('runtime_settings_digest') or settings['protocol'] != 'zfc.runtime-settings.v1':
        raise ValueError('runtime settings digest or protocol mismatch')
    for key, low, high in [('interval_seconds', 1, 30), ('batch_size', 1, 32),
                          ('request_tokens', 1, 1000000000), ('request_seconds', 10, 86400)]:
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise ValueError('invalid runtime setting bounds')
    rate = settings['diagnostic_sample_rate']
    if type(rate) not in {int, float} or not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError('invalid diagnostic sampling')
    if (settings['mode'] not in {'self_managed_cli', 'self_managed_api', 'managed_cpa'}
            or not isinstance(settings['request_money'], str)
            or not re.fullmatch(r'\d{1,6}(\.\d{1,12})?', settings['request_money'])
            or not isinstance(settings['price_version'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', settings['price_version'])):
        raise ValueError('invalid runtime model access settings')
    modes = settings.get('provider_modes', {})
    if not isinstance(modes, dict) or set(modes) - {'claude-code', 'codex'} or any(
            m not in ('managed_cpa', 'self_managed_api', 'self_managed_cli') for m in modes.values()):
        raise ValueError('invalid provider access modes')
    return settings


def applied(config):
    path = Installation(config).path / 'policy.json'
    if not path.exists():
        return config
    snapshot = json.loads(private_read(path))
    return overlay(config, snapshot)


def overlay(config, snapshot):
    settings = validate(snapshot.get('limits', {}))
    if settings is None:
        return config
    if settings['mode'] not in (config.allowed_modes or (config.mode,)) or settings.get('provider_modes', {}) != config.provider_modes:
        raise ValueError('applied mode no longer has local consent')
    return replace(config, **{k: settings[k] for k in ('interval_seconds', 'batch_size',
        'request_tokens', 'request_money', 'price_version', 'request_seconds')},
        runtime_mode=settings['mode'], mode_epoch=snapshot.get('mode_epoch', 0))


def prepare(config, snapshot, previous, contexts, credentials):
    settings = validate(snapshot.get('limits', {}))
    if settings is None:
        return None
    if settings['mode'] not in (config.allowed_modes or (config.mode,)) or settings.get('provider_modes', {}) != config.provider_modes:
        return 'local_consent_required'
    old = overlay(config, previous)
    snapshot['mode_epoch'] = previous.get('mode_epoch', 0)
    if settings['mode'] == old.effective_mode:
        return None
    # Executions and all accounting obligations must drain before the attribution boundary moves.
    if any(row['status'] not in {'settled', 'not_started'} for row in Ledger(config.state_dir).rows('execution')):
        return 'execution_drain_required'
    from zf.integrations.controller.collector import Collector
    from zf.integrations.controller.outbox import Outbox
    from zf.integrations.controller.workspace import observation
    queues = [Outbox(config.state_dir, observation(old, credentials))]
    queues += [Collector(ctx.state_dir, ctx.config).outbox for ctx in contexts()]
    if any(queue.status()['pending_records'] or queue.has_reservations() for queue in queues):
        return 'execution_drain_required'
    snapshot['mode_epoch'] = time.time()
    return None


def diagnostic_rate(state_dir, fallback):
    from zf.integrations.controller.binding import managed_config
    config = managed_config()
    if config is None:
        pointer = state_dir / 'controller' / 'settings-owner.json'
        if not pointer.exists():
            return fallback
        from zf.core.config.controller_workspace import load_controller_workspace
        config = load_controller_workspace(Path(json.loads(private_read(pointer))['config']))
    from zf.integrations.controller.workspace import project_contexts
    snapshot = json.loads(private_read(Installation(config).path / 'policy.json'))
    contexts = project_contexts(applied(config), Installation(config).credentials(), snapshot['projects'])
    if not any(ctx.state_dir.resolve() == state_dir.resolve() for ctx in contexts):
        return fallback
    settings = validate(snapshot.get('limits', {}))
    return settings['diagnostic_sample_rate'] if settings else fallback
