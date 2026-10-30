"""Commit only an approved config, with an isolated index and a CAS branch update."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


class ConfigBaselineError(ValueError):
    """The approved config cannot safely establish a Git delivery baseline."""


def establish_config_baseline(
    target: Path, *, base_digest: str, target_digest: str, proposal_digest: str,
) -> dict[str, Any]:
    env = {key: value for key, value in os.environ.items() if key not in {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    }}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"

    git_cwd = target.parent

    def git(*args: str, data: bytes | None = None) -> bytes:
        result = subprocess.run(
            ["git", *args], cwd=git_cwd, env=env, input=data,
            capture_output=True, timeout=20,
        )
        if result.returncode:
            raise ConfigBaselineError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    try:
        root_text = git("rev-parse", "--show-toplevel").decode().strip()
    except ConfigBaselineError as exc:
        if "not a git repository" in str(exc):
            return {"status": "not_required", "reason": "project is not in Git"}
        raise
    root = Path(root_text).resolve()
    git_cwd = root
    relative = target.relative_to(root).as_posix()
    index = Path(git("rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
    lock = index.with_name(index.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ConfigBaselineError("Git index is busy; retry after the current Git operation") from exc
    os.close(fd)
    try:
        head = git("rev-parse", "HEAD").decode().strip()
        branch = git("symbolic-ref", "-q", "HEAD").decode().strip()

        def dirty_paths() -> set[str]:
            # Compare both layers: staged work may be undone only in the worktree.
            paths = git("diff", "--cached", "--name-only", "-z", head, "--")
            paths += git("diff", "--name-only", "-z", "--")
            return set(paths.decode().split("\0")) - {""}

        # All repo paths are checked: no staged or unstaged user work is adopted.
        dirty = dirty_paths()
        if dirty - {relative}:
            raise ConfigBaselineError("Resolve other tracked changes before starting: " + ", ".join(sorted(dirty - {relative})))
        entry = git("ls-tree", head, "--", relative).decode().split()
        if len(entry) < 3 or entry[0] not in {"100644", "100755"}:
            raise ConfigBaselineError("The approved zf.yaml must already be a regular tracked file")
        staged_mode = git("ls-files", "--stage", "--", relative).decode().split()[0]
        working_mode = "100755" if target.stat().st_mode & 0o111 else "100644"
        if staged_mode != entry[0] or working_mode != entry[0]:
            raise ConfigBaselineError("Unapproved zf.yaml file mode change")
        head_content = git("show", f"{head}:{relative}")
        head_digest = hashlib.sha256(head_content).hexdigest()
        if head_digest not in {base_digest, target_digest}:
            raise ConfigBaselineError("Committed zf.yaml does not match the approved base or target")
        staged_digest = hashlib.sha256(git("show", f":{relative}")).hexdigest()
        if staged_digest not in {base_digest, target_digest}:
            raise ConfigBaselineError("The index contains an unapproved zf.yaml change")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != target_digest:
            raise ConfigBaselineError("Applied zf.yaml has drifted from the approved target")
        with tempfile.TemporaryDirectory(prefix="zf-config-index-", dir=index.parent) as temporary:
            env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
            git("read-tree", head)
            blob = git("hash-object", "-w", "--stdin", data=content).decode().strip()
            git("update-index", "--add", "--cacheinfo", entry[0], blob, relative)
            tree = git("write-tree").decode().strip()
            commit = head
            if head_digest != target_digest:
                # No hooks run and no unrelated paths enter this dedicated config commit.
                for kind in ("AUTHOR", "COMMITTER"):
                    env.setdefault(f"GIT_{kind}_NAME", "ZaoFu")
                    env.setdefault(f"GIT_{kind}_EMAIL", "zaofu@localhost")
                message = f"chore: establish approved workflow config baseline\n\nWorkflow-Proposal-Digest: {proposal_digest}\n"
                commit = git("-c", "commit.gpgSign=false", "commit-tree", tree, "-p", head, data=message.encode()).decode().strip()
            if hashlib.sha256(target.read_bytes()).hexdigest() != target_digest:
                raise ConfigBaselineError("Applied zf.yaml changed while establishing its baseline")
            # Recheck the real index/worktree while holding its lock, then CAS HEAD.
            isolated_index = env.pop("GIT_INDEX_FILE")
            dirty = dirty_paths()
            if dirty - {relative}:
                raise ConfigBaselineError("Tracked files changed while establishing the baseline")
            git("update-ref", "-m", "workflow approved config baseline", branch, commit, head)
            # An interrupted index replacement is repaired on retry once the Git lock is released:
            # HEAD already matches target; the old index still matches the approved base.
            os.replace(isolated_index, index)
        return {"status": "ready", "commit": commit, "config_path": relative, "proposal_digest": proposal_digest}
    finally:
        lock.unlink(missing_ok=True)
