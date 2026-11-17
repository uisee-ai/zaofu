"""Stop cleanup holds the preparation mutex through preservation checks and removal."""
from pathlib import Path
import subprocess
import sys

from zf.runtime.environment_usage import environment_mutation
from zf.runtime.workspace_retention import workspace_retention_reason


def remove_stopped_worktree(project_root: Path, path: Path) -> bool:
    def git(*args):
        return subprocess.run(["git", *args], cwd=project_root, capture_output=True, text=True, check=False)
    try:
        with environment_mutation(path):
            retained = workspace_retention_reason(path)
            head = git("-C", str(path), "rev-parse", "HEAD")
            integrated = head.returncode == 0 and git("merge-base", "--is-ancestor", head.stdout.strip(), "HEAD").returncode == 0
            if retained or not integrated:
                reason = retained or "candidate commits are not merged into the current checkout"
                print(f"Warning: retaining worktree {path}: {reason}", file=sys.stderr)
                return False
            result = git("worktree", "remove", str(path))
            if result.returncode:
                print(f"Warning: could not remove worktree {path}: {result.stderr.strip()}", file=sys.stderr)
                return False
            return True
    except (TimeoutError, OSError) as exc:
        print(f"Warning: retaining worktree {path}: active environment holder or unavailable lock ({exc})", file=sys.stderr)
        return False
