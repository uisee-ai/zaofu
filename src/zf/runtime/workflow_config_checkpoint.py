"""Checkpoint exactly the approved tracked zf.yaml, without sweeping the index."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from zf.runtime.git_capture import git_env


def checkpoint_config(root: Path, *, base_digest: str, target_digest: str, proposal_digest: str) -> str:
    def run(*args: str, required: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(["git", "-C", str(root), *args], env=git_env(),
                                    capture_output=True, timeout=30)
        except subprocess.SubprocessError as exc:
            raise ValueError("config checkpoint Git command did not complete") from exc
        if required and result.returncode:
            raise ValueError("config checkpoint failed: " + result.stderr.decode(errors="replace")[:500])
        return result

    top = run("rev-parse", "--show-toplevel", required=False)
    if top.returncode:
        return ""  # Non-Git projects retain the existing config-only apply path.
    if Path(top.stdout.decode().strip()).resolve() != root.resolve():
        raise ValueError("config checkpoint requires the project Git root")
    if run("ls-files", "--error-unmatch", "--", "zf.yaml", required=False).returncode:
        return ""  # An untracked config does not dirty the tracked delivery baseline.
    if run("diff", "--cached", "--name-only").stdout.strip():
        raise ValueError("config checkpoint requires a clean index; staged work is preserved")
    dirty = set(run("diff", "--name-only", "HEAD").stdout.decode().splitlines())
    if dirty - {"zf.yaml"}:
        raise ValueError("config checkpoint refuses unrelated tracked changes")
    target = root / "zf.yaml"
    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != target_digest:
        raise ValueError("approved config changed before checkpoint")
    committed = hashlib.sha256(run("show", "HEAD:zf.yaml").stdout).hexdigest()
    if committed == target_digest:
        return run("log", "-1", "--format=%H", "--", "zf.yaml").stdout.decode().strip()
    if committed != base_digest:
        raise ValueError("config Git baseline differs from the approved proposal")
    head = run("rev-parse", "HEAD").stdout.decode().strip()
    run("commit", "--only", "-m", "chore: apply approved workflow configuration",
        "-m", f"Workflow-Proposal: {proposal_digest}", "--", "zf.yaml")
    if (run("rev-parse", "HEAD^").stdout.decode().strip() != head
            or hashlib.sha256(run("show", "HEAD:zf.yaml").stdout).hexdigest() != target_digest
            or run("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.decode().splitlines() != ["zf.yaml"]):
        raise ValueError("config checkpoint changed concurrently; inspect Git before retrying")
    return run("rev-parse", "HEAD").stdout.decode().strip()
