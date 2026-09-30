"""Build a disclosure-minimized, read-only workspace for Self-Issue assessment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.security.redaction import redact_text


_ALLOWED_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".css", ".go", ".h", ".hpp", ".html",
    ".ini", ".java", ".js", ".jsx", ".json", ".mjs", ".php", ".py",
    ".pyi", ".rb", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx",
    ".vue", ".yaml", ".yml",
})
_ALLOWED_NAMES = frozenset({
    "AGENTS.md", "CLAUDE.md", "Dockerfile", "Makefile", "README.md",
})
_EXCLUDED_PARTS = frozenset({
    ".git", ".hg", ".svn", ".venv", "artifacts", "build", "coverage",
    "dist", "node_modules", "secrets", "vendor", "__pycache__",
})
_SENSITIVE_NAMES = frozenset({
    ".env", ".env.local", ".npmrc", ".pypirc", "credentials.json",
    "forge-credentials.json", "id_dsa", "id_ed25519", "id_rsa",
})
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx", ".pem"})
_MAX_FILE_BYTES = 512 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_FILES = 3000


@dataclass(frozen=True)
class AssessmentWorkspace:
    root: Path
    manifest: dict[str, Any]


def build_assessment_workspace(
    *,
    capsule: Path,
    project_root: Path,
    input_path: Path,
    skill_root: Path,
    harness_root: Path | None = None,
    state_dir: Path | None = None,
) -> AssessmentWorkspace:
    """Create an immutable committed-source snapshot without runtime secrets."""
    capsule = Path(capsule)
    root = capsule / "workspace"
    root.mkdir(parents=True)
    shutil.copy2(input_path, root / "evidence-input.json")
    shutil.copytree(
        skill_root,
        root / ".codex" / "skills" / "issue",
    )
    evidence_files = _copy_intake_attachments(
        root=root,
        state_dir=Path(state_dir) if state_dir is not None else None,
        input_path=input_path,
    )

    subject = _source_spec(Path(project_root))
    harness = _source_spec(
        # A missing harness is intentionally not filled with a ZaoFu checkout.
        # Host adapters may provide one explicitly; otherwise assessment is
        # over the selected project snapshot only.
        Path(harness_root) if harness_root is not None else Path(project_root),
    )
    same_source = bool(
        subject is not None
        and harness is not None
        and subject[0] == harness[0]
        and subject[1] == harness[1]
    )
    sources: list[dict[str, Any]] = []
    if same_source:
        sources.append(_copy_git_snapshot(subject, root / "repository", "repository"))
    else:
        if subject is not None:
            sources.append(_copy_git_snapshot(subject, root / "subject", "subject"))
        else:
            sources.append(_unavailable_source("subject", "no committed Git snapshot"))
        if harness is not None:
            sources.append(_copy_git_snapshot(harness, root / "harness", "harness"))
        else:
            sources.append(_unavailable_source("harness", "no committed Git snapshot"))

    manifest = {
        "schema_version": "self-issue-source-manifest.v1",
        "snapshot_policy": "committed_source_only",
        "sources": sources,
        "evidence_files": evidence_files,
        "excluded": [
            "runtime state", "secret stores", "environment files",
            "untracked files", "working-tree modifications", "provider credentials",
        ],
    }
    (root / "source-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "ASSESSMENT_WORKSPACE.md").write_text(
        _workspace_guide(sources),
        encoding="utf-8",
    )
    (root / "run-reproduction").write_text(
        _reproduction_runner(),
        encoding="utf-8",
    )
    guard = root / "assessment_guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(_network_guard(), encoding="utf-8")
    runtime = root / ".assessment-runtime"
    runtime.mkdir(mode=0o700)
    _freeze_tree(root)
    # The source capsule remains immutable. Only the Kernel-generated runner may
    # update this private, ephemeral control directory to enforce its test budget.
    os.chmod(runtime, 0o700)
    return AssessmentWorkspace(root=root, manifest=manifest)


def _copy_intake_attachments(
    *, root: Path, state_dir: Path | None, input_path: Path,
) -> list[dict[str, Any]]:
    if state_dir is None:
        return []
    try:
        body = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    descriptors = body.get("attachment_refs") if isinstance(body, dict) else []
    descriptors = descriptors if isinstance(descriptors, list) else []
    mechanical = body.get("mechanical_evidence") if isinstance(body, dict) else {}
    mechanical = mechanical if isinstance(mechanical, dict) else {}
    screenshots = mechanical.get("screenshot_refs")
    screenshots = screenshots if isinstance(screenshots, list) else []
    descriptors = [
        *descriptors[:5],
        *[
            {
                **item,
                "content_type": str(item.get("content_type") or "image/png"),
                "capture_source": str(item.get("capture_source") or ""),
            }
            for item in screenshots[:3]
            if isinstance(item, dict)
        ],
    ]
    state_root = state_dir.resolve()
    destination = root / "evidence" / "attachments"
    copied: list[dict[str, Any]] = []
    for index, descriptor in enumerate(descriptors[:8], start=1):
        if not isinstance(descriptor, dict):
            continue
        relative = Path(str(descriptor.get("ref") or ""))
        source = (state_root / relative).resolve()
        digest = str(descriptor.get("sha256") or "")
        if (
            relative.is_absolute()
            or not source.is_relative_to(state_root)
            or not source.is_file()
            or source.stat().st_size > 20 * 1024 * 1024
            or not digest
            or hashlib.sha256(source.read_bytes()).hexdigest() != digest
        ):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        name = f"{index:02d}-{source.name}"
        shutil.copy2(source, destination / name)
        copied.append({
            "workspace_path": f"evidence/attachments/{name}",
            "sha256": digest,
            "byte_count": source.stat().st_size,
            "content_type": str(descriptor.get("content_type") or ""),
            "capture_source": str(descriptor.get("capture_source") or "user"),
            "capture_kind": str(descriptor.get("capture_kind") or "user_supplied_scene"),
        })
    return copied


def _source_spec(path: Path) -> tuple[Path, Path, str] | None:
    path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    repository = Path(result.stdout.strip()).resolve()
    if not path.is_relative_to(repository):
        return None
    scope = path.relative_to(repository)
    commit = _git_text(repository, "rev-parse", "HEAD")
    return repository, scope, commit


def _copy_git_snapshot(
    spec: tuple[Path, Path, str], destination: Path, label: str,
) -> dict[str, Any]:
    repository, scope, commit = spec
    destination.mkdir(parents=True)
    scope_arg = scope.as_posix() if scope.parts else "."
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--long", "HEAD", "--", scope_arg],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if listing.returncode != 0:
        return _unavailable_source(label, "Git tree could not be enumerated")
    selected: list[tuple[PurePosixPath, str, int]] = []
    selected_bytes = 0
    omitted_files = 0
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, encoded_path = raw.split(b"\t", 1)
            mode, object_type, object_id, raw_size = metadata.decode("ascii").split()
            git_path = PurePosixPath(encoded_path.decode("utf-8"))
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError):
            omitted_files += 1
            continue
        if mode not in {"100644", "100755"} or object_type != "blob":
            omitted_files += 1
            continue
        relative = _relative_to_scope(git_path, scope)
        if (
            relative is None
            or not _safe_source_path(relative)
            or size > _MAX_FILE_BYTES
            or len(selected) >= _MAX_FILES
            or selected_bytes + size > _MAX_TOTAL_BYTES
        ):
            omitted_files += 1
            continue
        selected.append((relative, object_id, size))
        selected_bytes += size

    blobs = _read_git_blobs(repository, [item[1] for item in selected])
    copied = 0
    copied_bytes = 0
    redacted_files = 0
    for relative, object_id, _size in selected:
        body = blobs.get(object_id)
        if body is None:
            omitted_files += 1
            continue
        try:
            original = body.decode("utf-8")
        except UnicodeDecodeError:
            omitted_files += 1
            continue
        safe = _redact_source(original)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(safe, encoding="utf-8")
        copied += 1
        copied_bytes += len(safe.encode("utf-8"))
        redacted_files += int(safe != original)
    dirty = bool(_git_text(
        repository, "status", "--porcelain", "--untracked-files=no", "--", scope_arg,
    ))
    return {
        "label": label,
        "available": True,
        "workspace_path": label,
        "commit": commit,
        "working_tree_diverged": dirty,
        "file_count": copied,
        "byte_count": copied_bytes,
        "redacted_files": redacted_files,
        "omitted_files": omitted_files,
    }


def _read_git_blobs(repository: Path, object_ids: list[str]) -> dict[str, bytes]:
    """Read a bounded blob set with one Git process instead of one per file."""
    if not object_ids:
        return {}
    try:
        batch = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=repository,
            input="".join(f"{object_id}\n" for object_id in object_ids).encode("ascii"),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if batch.returncode != 0:
        return {}
    output = batch.stdout
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected in object_ids:
        newline = output.find(b"\n", offset)
        if newline < 0:
            break
        try:
            object_id, object_type, raw_size = output[offset:newline].decode("ascii").split()
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError):
            break
        start = newline + 1
        end = start + size
        if object_type != "blob" or end >= len(output):
            break
        if object_id == expected:
            blobs[expected] = output[start:end]
        offset = end + 1
    return blobs


def _relative_to_scope(path: PurePosixPath, scope: Path) -> PurePosixPath | None:
    if not scope.parts:
        return path
    prefix = PurePosixPath(scope.as_posix())
    try:
        return path.relative_to(prefix)
    except ValueError:
        return None


def _safe_source_path(path: PurePosixPath) -> bool:
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = tuple(part.lower() for part in path.parts)
    if any(part in _EXCLUDED_PARTS or part.startswith(".zf") for part in lowered[:-1]):
        return False
    name = lowered[-1]
    if name in _SENSITIVE_NAMES or name.startswith(".env"):
        return False
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in _SENSITIVE_SUFFIXES:
        return False
    return suffix in _ALLOWED_SUFFIXES or path.name in _ALLOWED_NAMES


def _git_text(repository: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _unavailable_source(label: str, reason: str) -> dict[str, Any]:
    return {
        "label": label,
        "available": False,
        "workspace_path": label,
        "commit": "",
        "working_tree_diverged": False,
        "file_count": 0,
        "byte_count": 0,
        "redacted_files": 0,
        "omitted_files": 0,
        "reason": reason,
    }


_SOURCE_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(?P<prefix>[^\n#]*\b(?:token|secret|password|passwd|api[_-]?key)\b\s*[:=]\s*)"
    r"(?P<value>[^\n]+)$",
)


def _redact_source(value: str) -> str:
    redacted = redact_text(value)
    return _SOURCE_SECRET_ASSIGNMENT.sub(
        lambda match: f'{match.group("prefix")}"[REDACTED]"',
        redacted,
    )


def _workspace_guide(sources: list[dict[str, Any]]) -> str:
    paths = ", ".join(
        f"`{source['workspace_path']}/`"
        for source in sources if source.get("available")
    ) or "none"
    return (
        "# Self-Issue evidence-assessment workspace\n\n"
        f"Committed, redacted source snapshots: {paths}.\n\n"
        "Read `evidence-input.json` and `source-manifest.json` first. Inspect only "
        "this workspace. Sanitized user attachments, when present, are listed under "
        "`evidence_files` and copied below `evidence/attachments/`. Source files and "
        "directories are immutable. You may run "
        "up to three targeted Python tests with "
        "`python ./run-reproduction <source-label> <tests/path.py::node>`. The runner "
        "mechanically rejects a fourth request before executing it, disables caches, "
        "strips credentials, blocks Python sockets, and uses `/tmp`. If three attempts "
        "remain inconclusive, stop testing and return a low-confidence assessment. "
        "Do not use network, provider, deployment, Git write, or source-edit commands. "
        "Report workspace-relative `path:line` locations.\n"
    )


def _freeze_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            os.chmod(
                path,
                0o555 if path.is_dir() or path.name == "run-reproduction" else 0o444,
            )
        except OSError:
            pass
    os.chmod(root, 0o555)


def _reproduction_runner() -> str:
    return '''#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SAFE_TARGET = re.compile(r"^[A-Za-z0-9_./:-]{1,300}$")
MAX_ATTEMPTS = 3
EVENT_PREFIX = "ZF_REPRODUCTION_EVENT "
root = Path(__file__).resolve().parent
if len(sys.argv) != 3 or sys.argv[1] not in {"repository", "subject", "harness"}:
    raise SystemExit("usage: ./run-reproduction <repository|subject|harness> <tests/path.py::node>")
source = (root / sys.argv[1]).resolve()
target = sys.argv[2]
path_part = target.split("::", 1)[0]
if (
    not SAFE_TARGET.fullmatch(target)
    or target.startswith("-")
    or ".." in Path(path_part).parts
    or not path_part.startswith("tests/")
    or not (source / path_part).is_file()
):
    raise SystemExit("reproduction target must be one existing tests/... node")

target_summary = f"{sys.argv[1]}:{target}"
state_path = root / ".assessment-runtime" / "reproductions.json"

def emit(status: str, attempt: int) -> None:
    print(EVENT_PREFIX + json.dumps({
        "attempt": attempt,
        "max_attempts": MAX_ATTEMPTS,
        "status": status,
        "target": target_summary,
    }, sort_keys=True), flush=True)

def locked_state() -> tuple[object, dict[str, object]]:
    handle = state_path.open("a+", encoding="utf-8")
    os.chmod(state_path, 0o600)
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    handle.seek(0)
    raw = handle.read().strip()
    try:
        state = json.loads(raw) if raw else {"attempts": []}
    except json.JSONDecodeError:
        state = {"attempts": [], "invalid": True}
    if not isinstance(state, dict) or not isinstance(state.get("attempts"), list):
        state = {"attempts": [], "invalid": True}
    return handle, state

def save_state(handle: object, state: dict[str, object]) -> None:
    handle.seek(0)
    handle.truncate()
    json.dump(state, handle, sort_keys=True)
    handle.write("\\n")
    handle.flush()
    os.fsync(handle.fileno())

def begin_attempt() -> tuple[int, bool]:
    handle, state = locked_state()
    try:
        attempts = state["attempts"]
        if state.get("invalid"):
            return MAX_ATTEMPTS + 1, False
        if attempts:
            pending = attempts[-1]
            if (
                isinstance(pending, dict)
                and pending.get("status") == "requested"
                and pending.get("target") == target_summary
            ):
                attempt = int(pending.get("attempt") or len(attempts))
                pending["status"] = "started"
                save_state(handle, state)
                return attempt, True
        attempt = len(attempts) + 1
        if attempt > MAX_ATTEMPTS:
            return attempt, False
        attempts.append({"attempt": attempt, "target": target_summary, "status": "started"})
        save_state(handle, state)
        return attempt, True
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

def finish_attempt(attempt: int, status: str) -> None:
    handle, state = locked_state()
    try:
        for item in state.get("attempts", []):
            if isinstance(item, dict) and item.get("attempt") == attempt:
                item["status"] = status
                break
        save_state(handle, state)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    emit(status, attempt)

attempt, allowed = begin_attempt()
if not allowed:
    emit("budget_exhausted", attempt)
    raise SystemExit(75)
emit("started", attempt)

def digest() -> str:
    result = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file():
            result.update(path.relative_to(source).as_posix().encode())
            result.update(path.read_bytes())
    return result.hexdigest()

before = digest()
scratch = Path("/tmp") / f"zf-self-issue-{os.getpid()}"
scratch.mkdir(mode=0o700)
env = {
    "HOME": str(scratch),
    "LANG": "C.UTF-8",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": os.pathsep.join([str(root / "assessment_guard"), str(source / "src")]),
    "TMPDIR": str(scratch),
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "",
}
pytest_command = shutil.which("pytest", path=env["PATH"])
if not pytest_command:
    print("REPRODUCTION_UNAVAILABLE: pytest is not installed on PATH")
    finish_attempt(attempt, "unavailable")
    raise SystemExit(69)
command = [
    pytest_command, "-q", "--no-cov", "-p", "no:cacheprovider",
    "-m", "not host and not real_provider and not real_gitlab", target,
]
try:
    completed = subprocess.run(command, cwd=source, env=env, text=True, timeout=180)
except subprocess.TimeoutExpired:
    print("REPRODUCTION_TIMEOUT: 90 seconds")
    finish_attempt(attempt, "timeout")
    shutil.rmtree(scratch, ignore_errors=True)
    raise SystemExit(124)
after = digest()
if before != after:
    print("REPRODUCTION_INVALID: source snapshot changed")
    finish_attempt(attempt, "source_mutated")
    shutil.rmtree(scratch, ignore_errors=True)
    raise SystemExit(86)
status = "passed" if completed.returncode == 0 else "failed"
finish_attempt(attempt, status)
shutil.rmtree(scratch, ignore_errors=True)
raise SystemExit(completed.returncode)
'''


def _network_guard() -> str:
    return '''"""Network guard automatically loaded by the reproduction Python process."""
import socket

def _blocked(*args, **kwargs):
    raise OSError("network disabled by Self-Issue assessment runner")

class _NoNetworkSocket(socket.socket):
    def connect(self, *args, **kwargs):
        return _blocked(*args, **kwargs)
    def connect_ex(self, *args, **kwargs):
        return _blocked(*args, **kwargs)

socket.socket = _NoNetworkSocket
socket.create_connection = _blocked
'''
