"""Positive native process identity evidence for fast, fenced crash recovery."""
import json
from pathlib import Path

from zf.runtime.environment_resources import environment_lock_plan
from zf.runtime.environment_usage import assert_workspace_idle


def confirmed_provider_exit(workspace: Path) -> bool:
    """A missing pane alone is insufficient; require a registered exited process.

    Shared dependency consumers do not prove a provider still runs here, but
    any live/unknown workspace holder (including detached children) vetoes this.
    Ordinary transports without native identity evidence retain heartbeat grace.
    """
    try:
        plan = environment_lock_plan(workspace)
        receipts = list(Path(plan['locks'][0] + '.holders').glob('*.json'))
        if not receipts:
            return False
        for receipt in receipts:
            body = json.loads(receipt.read_text())
            pid = int(body['pid'])
            if pid <= 1 or not str(body['start_ticks']).isdigit():
                return False
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            except FileNotFoundError:
                continue
            if fields[0] != 'Z' and fields[19] == str(body['start_ticks']):
                return False
        assert_workspace_idle(workspace)
        return True
    except (OSError, ValueError, KeyError, IndexError, RuntimeError):
        return False


def active_provider_exit_confirmed(runtime, role) -> bool:
    if not runtime.config.runtime.workdirs.enabled:
        return False
    try:
        workspace = runtime._active_task_pipeline_spawn_cwd(role)
        if workspace is None:
            from zf.runtime.workdirs import WorkdirManager
            plan = WorkdirManager(state_dir=runtime.state_dir, project_root=runtime.project_root,
                                  config=runtime.config).plan(role)
            workspace = Path(plan.project_path) if plan.enabled else None
        return workspace is not None and confirmed_provider_exit(Path(workspace))
    except (OSError, ValueError, RuntimeError):
        return False
