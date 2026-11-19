"""Image qualification: the default Web controller and its assets must initialize."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from zf.cli.project import init_flow_project
from zf.cli.project_controller import DEFAULT_PROJECT_CONTROLLER
from zf.core.config.loader import load_config
from zf.core.skills import validate_skill_sources
from zf.web.terminal_backend_herdr import HerdrTerminalBackend


def main():
    prefix = 'zf-cloud-image-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-')
    with TemporaryDirectory(prefix=prefix) as temporary:
        root = Path(temporary)
        os.environ['HOME'] = str(root / 'home')
        os.environ['ZF_WORKSPACE_HOME'] = str(root / 'registry')
        capability = HerdrTerminalBackend().probe()
        assert capability.available and capability.tab_rename, capability.reason
        codex = subprocess.check_output(['codex', '--version'], text=True, timeout=15).strip()
        assert codex == 'codex-cli 0.166.0', codex
        claude = subprocess.check_output(['claude', '--version'], text=True, timeout=15).strip()
        assert claude == '2.1.300 (Claude Code)', claude
        project = root / 'project'
        result = init_flow_project(kind='prd', controller_id=DEFAULT_PROJECT_CONTROLLER,
            name='image-check', backend='codex', project_root=project, create_root=True,
            git_init=True, workspace_register=False)
        config = load_config(project / 'zf.yaml')
        assert config.version == '5.0.1'
        assert result['git_readiness']['created']
        issues = validate_skill_sources(config=config, project_root=project)
        assert not issues, issues
        assert all((project / 'skills' / skill / 'SKILL.md').is_file()
                   for role in config.roles for skill in role.skills)
        assert (Path(result['state_dir']) / 'session.yaml').is_file()
    print('Default controller, skills, project init, Herdr protocol and Codex binary: passed')


if __name__ == '__main__':
    main()
