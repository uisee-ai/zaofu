"""Keep harness repair subprocesses outside the resident project's authority."""

from __future__ import annotations

import os
import sys


def isolated_repair_environment(workspace: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(('ZF_', 'GIT_', 'TMUX'))
        and key not in {'PYTHONPATH', 'PYTHONHOME', 'PYTEST_ADDOPTS', 'PYTEST_CURRENT_TEST'}
    }
    environment.update(ZF_WORKSPACE_HOME=workspace, ZF_PYTHON=sys.executable, PYTHONPATH='src')
    return environment
