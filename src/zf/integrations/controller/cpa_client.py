"""Launch the image's native CLI with the selected Workspace inference credential."""

import json
import os
import sys
from pathlib import Path

NATIVE_BIN = Path('/opt/zaofu/providers/node_modules/.bin')
WRAPPER_BIN = Path('/opt/zaofu/cloud-bin')
KEY_FILE = Path('/run/secrets/zaofu-cpa/inference-key')
URL_VARIABLE = {'codex': 'ZF_CPA_CODEX_URL', 'claude': 'ZF_CPA_CLAUDE_URL'}


def read_key(path):
    key = path.read_text().strip()
    if not key or any(char.isspace() for char in key) or not key.isascii() or not key.isprintable():
        raise ValueError('invalid Workspace inference credential')
    return key


def native_command(client, args, env, key_file=KEY_FILE):
    argv = [str(NATIVE_BIN / client)]
    env = dict(env)
    url = env.get(URL_VARIABLE[client])
    if url:
        key = read_key(key_file)
        if client == 'claude':
            for name in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
                         'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_OAUTH_TOKEN'):
                env.pop(name, None)
            env.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_AUTH_TOKEN=key)
        else:
            env['ZF_CPA_INFERENCE_KEY'] = key
            settings = {'model_provider': 'zaofu_cpa', 'model_providers.zaofu_cpa.name': 'ZaoFu CPA',
                        'model_providers.zaofu_cpa.base_url': url, 'model_providers.zaofu_cpa.wire_api': 'responses',
                        'model_providers.zaofu_cpa.env_key': 'ZF_CPA_INFERENCE_KEY'}
            for name, value in settings.items():
                argv.extend(['-c', name + '=' + json.dumps(value)])
    return argv + list(args), env


def main():
    client = Path(sys.argv[0]).name
    try:
        argv, env = native_command(client, sys.argv[1:], os.environ)
    except (OSError, ValueError):
        raise SystemExit('Workspace CPA inference credential unavailable') from None
    os.execve(argv[0], argv, env)
