"""Preview and apply an exact, operator-approved initial Git snapshot."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from zf.core.state.locks import locked_path
from zf.runtime.workflow_git_readiness import git_baseline_diagnostics, git_environment, git_read


SCHEMA = "project-git-baseline.v1"


def _digest(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _relative(root: Path, name: str) -> Path:
    path = Path(name)
    if (not name or path.is_absolute() or path.as_posix() != name
            or any(p in {"..", ".", ".git"} for p in path.parts)
            or any(c in name for c in "\x00\n\r*?[]!\\")):
        raise ValueError("baseline paths must be literal project-relative paths")
    target = root / path
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError(f"baseline refuses symlink: {name}")
    if not target.resolve().is_relative_to(root):
        raise ValueError("baseline path escapes project")
    return target


def _file(root: Path, name: str) -> dict:
    target = _relative(root, name)
    if not target.is_file():
        raise ValueError(f"baseline requires an existing regular file: {name}")
    if target.stat().st_size > 20 * 1024 * 1024:
        raise ValueError(f"baseline file exceeds 20 MiB; exclude data/build output: {name}")
    if (target.name == ".env" or target.name.startswith(".env.") and target.suffix not in {".example", ".sample"}
            or target.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            or target.name in {"id_rsa", "id_ed25519", ".netrc", "credentials.json"}):
        raise ValueError(f"baseline refuses credential file: {name}")
    content = target.read_bytes()
    if re.search(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}", content):
        raise ValueError(f"baseline detects secret material: {name}")
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content), "executable": bool(target.stat().st_mode & 0o111)}


def preview_git_baseline(
    root: Path, state_dir: Path, *, paths: list[str], exclude_paths: list[str], branch: str = "main",
) -> dict:
    root, state_dir = root.resolve(), state_dir.resolve()
    if git_read(root, "check-ref-format", "--branch", branch).returncode or branch.startswith("-"):
        raise ValueError("invalid baseline branch")
    diagnostics = git_baseline_diagnostics(root, require_root=True)
    kinds = {d["kind"] for d in diagnostics}
    if not kinds <= {"git_repository_missing", "git_head_missing"} or not kinds:
        raise ValueError("initial baseline requires a non-Git project or an unborn named branch")
    current_branch = git_read(root, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if current_branch and current_branch != branch:
        raise ValueError("baseline branch differs from the existing unborn branch")
    if current_branch and git_read(root, "ls-files", "--stage").stdout.strip():
        raise ValueError("baseline refuses an existing nonempty Git index")
    excludes = sorted(set(exclude_paths))
    if state_dir.is_relative_to(root):
        excludes = sorted(set([*excludes, state_dir.relative_to(root).as_posix()]))
    for name in excludes:
        _relative(root, name)
    names = sorted(set(paths) - {".gitignore"})
    if "zf.yaml" not in names:
        raise ValueError("baseline must include the project zf.yaml")
    for name in names:
        if any(name == x or name.startswith(x + "/") for x in excludes):
            raise ValueError(f"baseline includes an excluded path: {name}")
    ignore = _relative(root, ".gitignore")
    before = ignore.read_text() if ignore.exists() else ""
    additions = ["/" + x for x in excludes if "/" + x not in before.splitlines()]
    after = before + ("\n" if before and not before.endswith("\n") else "")
    if additions:
        after += "# Approved local data and runtime exclusions\n" + "\n".join(additions) + "\n"
    files = [_file(root, name) for name in names]
    files.append({"path": ".gitignore", "sha256": hashlib.sha256(after.encode()).hexdigest(),
                  "bytes": len(after.encode()), "executable": bool(ignore.exists() and ignore.stat().st_mode & 0o111)})
    body = {"schema_version": SCHEMA, "project_root": str(root), "state_dir": str(state_dir),
            "branch": branch, "files": files, "exclude_paths": excludes,
            "ignore_before_sha256": hashlib.sha256(before.encode()).hexdigest(),
            "ignore_after": after}
    return {**body, "manifest_digest": _digest(body)}


def apply_git_baseline(root: Path, state_dir: Path, manifest: dict) -> dict:
    root, state_dir = root.resolve(), state_dir.resolve()
    body = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    digest = manifest.get("manifest_digest")
    if (body.get("schema_version") != SCHEMA or body.get("project_root") != str(root)
            or body.get("state_dir") != str(state_dir) or digest != _digest(body)):
        raise ValueError("baseline manifest identity or digest is invalid")
    with locked_path(state_dir / "locks" / "project-git-baseline"):
        head = git_read(root, "rev-parse", "--verify", "HEAD^{commit}")
        if head.returncode == 0:
            # A repeated approved action only succeeds for its exact prior commit.
            message = git_read(root, "log", "-1", "--format=%B").stdout
            if (f"Baseline-Manifest: {digest}" in message.splitlines()
                    and not git_baseline_diagnostics(root, require_root=True)
                    and git_read(root, "symbolic-ref", "--short", "HEAD").stdout.strip() == body["branch"]
                    and git_read(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"):
                _verify_tree(root, git_environment(), head.stdout.strip(), body["files"])
                return {"status": "already_applied", "commit": head.stdout.strip(), "manifest_digest": digest}
            raise ValueError("project already has a commit; initial baseline cannot replace it")
        names = [x["path"] for x in body["files"]]
        current = preview_git_baseline(root, state_dir, paths=names,
                                       exclude_paths=body["exclude_paths"], branch=body["branch"])
        if current != manifest:
            raise ValueError("baseline preview is stale; inspect changed files and prepare a new manifest")
        if not (root / ".git").exists():
            _run(root, git_environment(), "init", "--initial-branch=" + body["branch"])
        git_dir = Path(_run(root, git_environment(), "rev-parse", "--absolute-git-dir").strip())
        index = git_dir / "index"
        lock = git_dir / "index.lock"
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            if git_read(root, "ls-files", "--stage").stdout.strip():
                raise ValueError("Git index changed since baseline preview")
            (root / ".gitignore").write_text(body["ignore_after"])
            with tempfile.TemporaryDirectory(prefix="zf-baseline-", dir=git_dir) as tmp:
                private_index = Path(tmp) / "index"
                env = {**git_environment(), "GIT_INDEX_FILE": str(private_index)}
                for offset in range(0, len(names), 100):
                    _run(root, env, "add", "--", *names[offset:offset + 100])
                staged = set(_run(root, env, "diff", "--cached", "--name-only", "-z").split("\0")) - {""}
                if staged != set(names):
                    raise ValueError("baseline staged paths differ from the approved manifest")
                for item in body["files"]:
                    if _file(root, item["path"]) != item:
                        raise ValueError("baseline source changed while staging")
                _verify_tree(root, env, _run(root, env, "write-tree").strip(), body["files"])
                if not git_read(root, "rev-parse", "--verify", "HEAD^{commit}").returncode:
                    raise ValueError("Git HEAD changed while preparing baseline")
                _run(root, env, "-c", "user.name=ZaoFu Project Init", "-c", "user.email=zf-init@zaofu.local",
                     "commit", "-m", "chore: initialize approved project baseline",
                     "-m", f"Baseline-Manifest: {digest}", "--only", "--", *names)
                commit = _run(root, env, "rev-parse", "HEAD").strip()
                _verify_tree(root, env, commit, body["files"])
                os.replace(private_index, index)
        finally:
            lock.unlink(missing_ok=True)
        return {"status": "applied", "commit": commit, "branch": body["branch"],
                "manifest_digest": digest, "paths": names}


def _verify_tree(root: Path, env: dict[str, str], tree: str, files: list[dict]) -> None:
    actual = {}
    for entry in _run(root, env, "ls-tree", "-r", "-z", tree).split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        blob = subprocess.run(["git", "-C", str(root), "cat-file", "blob", oid],
                              env=env, capture_output=True, timeout=15, check=True).stdout
        if kind != "blob":
            raise ValueError("baseline tree contains a non-file entry")
        actual[name] = {"path": name, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                        "executable": mode == "100755"}
    if actual != {item["path"]: item for item in files}:
        raise ValueError("Git tree content differs from the approved baseline manifest")


def _run(root: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args], env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        # Git may echo file contents or hook output; keep diagnostics path-only.
        raise ValueError(f"Git baseline command failed ({args[0]}, exit {result.returncode})")
    return result.stdout
