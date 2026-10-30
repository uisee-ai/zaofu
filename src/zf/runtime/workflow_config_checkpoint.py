"""Checkpoint exactly the approved tracked zf.yaml, without sweeping the index."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from zf.runtime.git_capture import git_env


def checkpoint_config(root: Path, *, base_digest: str, target_digest: str, proposal_digest: str) -> str:
    environment = {key: value for key, value in git_env().items() if key not in {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    }}
    environment["GIT_OPTIONAL_LOCKS"] = "0"

    def run(*args: str, required: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(["git", "-C", str(root), *args], env=environment,
                                    capture_output=True, timeout=30)
        except subprocess.SubprocessError as exc:
            raise ValueError("config checkpoint Git command did not complete") from exc
        if required and result.returncode:
            raise ValueError("config checkpoint failed: " + result.stderr.decode(errors="replace")[:500])
        return result

    top = run("rev-parse", "--show-toplevel", required=False)
    if top.returncode:
        return ""  # Non-Git projects retain the existing config-only apply path.
    target = root / "zf.yaml"
    repository = Path(top.stdout.decode().strip()).resolve()
    relative = target.relative_to(repository).as_posix()
    root = repository
    if run("ls-files", "--error-unmatch", "--", relative, required=False).returncode:
        return ""  # An untracked config does not dirty the tracked delivery baseline.
    head = run("rev-parse", "HEAD").stdout.decode().strip()
    run("symbolic-ref", "-q", "HEAD")  # A detached worker must not checkpoint config.
    index = Path(run("rev-parse", "--path-format=absolute", "--git-path", "index").stdout.decode().strip())
    if index.with_name(index.name + ".lock").exists():
        raise ValueError("config checkpoint Git index is busy; retry after its owner")
    index_before = run("ls-files", "--stage", "-z").stdout
    staged = set(run("diff", "--cached", "--name-only", "-z", head).stdout.decode().split("\0")) - {""}
    if staged - {relative}:
        raise ValueError("config checkpoint requires a clean index; staged work is preserved")
    dirty = set(run("diff", "--name-only", "-z").stdout.decode().split("\0")) - {""}
    if dirty - {relative}:
        raise ValueError("config checkpoint refuses unrelated tracked changes")
    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != target_digest:
        raise ValueError("approved config changed before checkpoint")
    entry = run("ls-tree", head, "--", relative).stdout.decode().split()
    staged_entry = run("ls-files", "--stage", "--", relative).stdout.decode().split()
    working_mode = "100755" if target.stat().st_mode & 0o111 else "100644"
    if (len(entry) < 3 or entry[0] not in {"100644", "100755"}
            or len(staged_entry) < 4 or staged_entry[2] != "0"
            or staged_entry[0] != entry[0] or working_mode != entry[0]):
        raise ValueError("config checkpoint refuses an unapproved zf.yaml file mode")
    committed = hashlib.sha256(run("show", f"{head}:{relative}").stdout).hexdigest()
    if committed not in {base_digest, target_digest}:
        raise ValueError("config Git baseline differs from the approved base or target")
    indexed = hashlib.sha256(run("show", f":{relative}").stdout).hexdigest()
    if indexed not in {base_digest, target_digest}:
        raise ValueError("config checkpoint refuses an unapproved zf.yaml index change")
    if committed == target_digest:
        if indexed != target_digest:
            # Recover a crash after the ref update but before index replacement.
            # Hold the real index lock and replace only the approved config entry.
            lock = index.with_name(index.name + ".lock")
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            try:
                if run("ls-files", "--stage", "-z").stdout != index_before:
                    raise ValueError("config checkpoint index changed during recovery")
                with tempfile.TemporaryDirectory(prefix="zf-config-index-", dir=index.parent) as temporary:
                    isolated = Path(temporary) / "index"
                    isolated.write_bytes(index.read_bytes())
                    environment["GIT_INDEX_FILE"] = str(isolated)
                    run("update-index", "--cacheinfo", entry[0], entry[2], relative)
                    if run("rev-parse", "HEAD").stdout.decode().strip() != head:
                        raise ValueError("config checkpoint Git HEAD changed during recovery")
                    os.replace(isolated, index)
            finally:
                environment.pop("GIT_INDEX_FILE", None)
                lock.unlink(missing_ok=True)
        return run("log", "-1", "--format=%H", "--", relative).stdout.decode().strip()
    if run("rev-parse", "HEAD").stdout.decode().strip() != head:
        raise ValueError("config checkpoint Git HEAD changed before commit")
    run("commit", "--only", "-m", "chore: apply approved workflow configuration",
        "-m", f"Workflow-Proposal: {proposal_digest}", "--", relative)
    if (run("rev-parse", "HEAD^").stdout.decode().strip() != head
            or hashlib.sha256(run("show", f"HEAD:{relative}").stdout).hexdigest() != target_digest
            or run("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.decode().splitlines() != [relative]):
        raise ValueError("config checkpoint changed concurrently; inspect Git before retrying")
    return run("rev-parse", "HEAD").stdout.decode().strip()
