"""Bounded raw snapshots of explicitly owned, synthetic ordinary case state.

Not a sandbox for hostile test code. Only POSIX, function-owned fixtures are
supported. Directory descriptors and O_NOFOLLOW reject symlink traversal;
cross-file snapshots are observations, not atomic filesystem transactions.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Iterator

from .evidence import sha256_file, write_json_atomic


@dataclass
class CaptureBudget:
    """Shared per pytest process, not a distributed xdist budget."""

    max_bytes: int = 64 * 1024 * 1024
    max_captures: int = 1024
    bytes_read: int = 0
    captures: int = 0


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open every absolute ancestor without following directory symlinks."""
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if part == "..":
                raise ValueError("parent traversal rejected")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


class CaseEvidence:
    SLOTS = frozenset({"registered", "setup-report", "call-report", "pre-teardown", "checkpoint", "owner-guard"})

    def __init__(
        self, *, nodeid: str, source: Path, owned_root: Path, output: Path,
        budget: CaptureBudget, on_change: Callable[[], None] | None = None,
        max_bytes: int = 4 * 1024 * 1024, max_files: int = 128,
        max_entries: int = 512, max_captures: int = 16,
    ) -> None:
        self.nodeid = nodeid
        self.source = source.absolute()
        self.source_digest = sha256_file(source)
        # Do not resolve away a symlink before the no-follow path walk.
        self.owned_root = owned_root.absolute()
        self.output = output.absolute()
        self.budget = budget
        self.on_change = on_change
        self.max_bytes, self.max_files = max_bytes, max_files
        self.max_entries, self.max_captures = max_entries, max_captures
        self.captures = 0
        self.root: Path | None = None
        self.metadata: dict[str, Any] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []
        self.retention = "last_committed_observations_not_exact_failure_instant"
        self.directory = self.output / hashlib.sha256(nodeid.encode()).hexdigest()

    def register(self, root: Path, *, metadata: dict[str, Any], synthetic_no_secrets: bool) -> None:
        if self.root is not None:
            raise ValueError("one explicitly owned root per ordinary case")
        if synthetic_no_secrets is not True:
            raise ValueError("synthetic, secret-free ownership declaration required")
        absolute = root.absolute()
        if ".." in absolute.parts or absolute == self.owned_root or not absolute.is_relative_to(self.owned_root):
            raise ValueError("root must be a private child of the fixture-owned directory")
        if self.output.is_relative_to(absolute) or absolute.is_relative_to(self.output):
            raise ValueError("evidence output and source must not overlap")
        encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 16384 or not {"given", "when", "then", "forbidden", "identity"} <= metadata.keys():
            raise ValueError("bounded Given/When/Then/Forbidden/identity metadata required")
        if not isinstance(metadata["identity"], dict) or not {"run_id", "task_id", "attempt", "generation", "operation_id"} <= metadata["identity"].keys():
            raise ValueError("explicit identity or null with reason required")
        if any(value is None for value in metadata["identity"].values()) and not metadata["identity"].get("reason"):
            raise ValueError("null identity requires reason")
        try:
            with _directory(absolute):
                pass
        except OSError as exc:
            raise ValueError("root must exist without symlink ancestors") from exc
        self.root = absolute
        self.metadata = json.loads(encoded)
        if self.on_change is not None:
            self.on_change()  # Persist ownership before the first snapshot write.
        self.checkpoint("registered")

    def _error(self, message: str) -> None:
        if message not in self.errors and len(self.errors) < 8:
            self.errors.append(message[:1024])

    def _files(self, entries: list[dict[str, Any]]) -> None:
        total = 0
        visited = 0

        def walk(fd: int, prefix: str = "", depth: int = 0) -> None:
            nonlocal total, visited
            if depth > 32:
                raise ValueError("directory depth budget exceeded")
            with os.scandir(fd) as children:
                for child in children:
                    visited += 1
                    if visited > self.max_entries:
                        raise ValueError("entry budget exceeded")
                    name = f"{prefix}{child.name}"
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        nested = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(nested, name + "/", depth + 1)
                        finally:
                            os.close(nested)
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("symlink, special file or shared hardlink rejected")
                    if len(entries) >= self.max_files:
                        raise ValueError("file budget exceeded")
                    available = min(self.max_bytes - total, self.budget.max_bytes - self.budget.bytes_read)
                    if info.st_size > available:
                        raise ValueError("run byte budget exceeded" if info.st_size > self.budget.max_bytes - self.budget.bytes_read else "case byte budget exceeded")
                    raw_fd = os.open(child.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(raw_fd, "rb") as stream:
                        before = os.fstat(stream.fileno())
                        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("file identity changed before read")
                        body = stream.read(available + 1)
                        total += len(body)
                        self.budget.bytes_read += len(body)
                        after = os.fstat(stream.fileno())
                    if len(body) > available:
                        raise ValueError("byte budget exceeded during read")
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(body) != before.st_size:
                        raise ValueError("file changed during capture")
                    entries.append({"path": name, "bytes": len(body), "digest": hashlib.sha256(body).hexdigest(),
                                    "base64": base64.b64encode(body).decode("ascii")})

        assert self.root is not None
        with _directory(self.root) as fd:
            walk(fd)
        entries.sort(key=lambda entry: entry["path"])

    def checkpoint(self, point: str = "checkpoint") -> None:
        if self.root is None:
            self._error("checkpoint requested before registration")
        elif point not in self.SLOTS:
            self._error("unknown capture slot")
        elif self.captures >= self.max_captures or self.budget.captures >= self.budget.max_captures:
            self._error("capture budget exceeded")
        else:
            self.captures += 1
            self.budget.captures += 1
            files: list[dict[str, Any]] = []
            capture_error = ""
            try:
                self._files(files)
            except Exception as exc:
                capture_error = f"{type(exc).__name__}: {exc}"
                self._error(capture_error)
            payload = {
                "schema_version": "zf-testkit-ordinary-evidence.v1", "nodeid": self.nodeid,
                "source": str(self.source), "source_digest": self.source_digest,
                "original_root": str(self.root), "metadata": self.metadata,
                "capture_point": point, "captured_at_ns": time.time_ns(),
                "complete_for_declared_scope": not capture_error, "capture_error": capture_error,
                "consistency": "per-file checked; not an atomic cross-file snapshot",
                "files": files,
            }
            try:
                with _directory(self.directory, create=True):
                    pass
                path = self.directory / f"{point}.json"
                digest = write_json_atomic(path, payload)
                self.snapshots[point] = {"path": str(path), "digest": digest, "complete": not capture_error}
            except Exception as exc:
                self._error(f"snapshot write unavailable: {type(exc).__name__}: {exc}")
        if self.on_change is not None:
            self.on_change()

    @contextmanager
    def guard(self) -> Iterator[None]:
        try:
            yield
        except BaseException as original:
            try:
                self.checkpoint("owner-guard")
            except Exception as exc:
                # A reporter failure must not replace pytest.fail/SystemExit
                # or the original assertion while an owner is unwinding.
                original.add_note(f"ordinary evidence reporter unavailable: {exc}")
            raise

    def record(self) -> dict[str, Any]:
        return {"registered": self.root is not None, "retention": self.retention,
                "snapshots": dict(self.snapshots), "errors": list(self.errors)}

    def discard_after_pass(self) -> None:
        if self.errors:
            return
        # Delete only verified, exact files created by this instance. Never
        # recursively remove a supplied output or fixture directory.
        try:
            with _directory(self.directory):
                for entry in self.snapshots.values():
                    path = Path(entry["path"])
                    if path.is_symlink() or sha256_file(path) != entry["digest"]:
                        raise ValueError("owned snapshot changed before cleanup")
                    path.unlink()
            self.snapshots.clear()
            self.retention = "discarded_after_all_phases_passed"
        except Exception as exc:
            self._error(f"snapshot cleanup unavailable: {exc}")


def read_case_snapshot(
    path: Path, *, nodeid: str, point: str, expected_digest: str | None = None,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[dict[str, Any], str, int]:
    """Read a fixed slot without following links; validate its internal bytes.

Without expected_digest the result is diagnostic only, not a report binding.
The caller owns the fixed run/case path selection.
"""
    limit = min(max_bytes, 8 * 1024 * 1024)
    with _directory(path.parent) as fd:
        raw_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(raw_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("snapshot type/size rejected")
            raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("snapshot read budget exceeded")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise ValueError("snapshot digest mismatch")
    payload = json.loads(raw)
    if payload["schema_version"] != "zf-testkit-ordinary-evidence.v1" or payload["nodeid"] != nodeid or payload["capture_point"] != point:
        raise ValueError("snapshot identity mismatch")
    seen: set[str] = set()
    for entry in payload["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or entry["path"] in seen:
            raise ValueError("invalid raw file path")
        seen.add(entry["path"])
        body = base64.b64decode(entry["base64"], validate=True)
        if len(body) != entry["bytes"] or hashlib.sha256(body).hexdigest() != entry["digest"]:
            raise ValueError("raw file digest/length mismatch")
    return payload, digest, len(raw)


def validate_case_evidence(output: Path, cases: tuple[dict[str, Any], ...]) -> tuple[list[Path], list[str]]:
    """Parent-side bounded ref/identity/raw-byte validation; never read source roots."""
    paths: list[Path] = []
    errors: list[str] = []
    for case in cases:
        if "ordinary_evidence" not in case:
            continue  # Explicitly unregistered, not a claim of state evidence.
        record = case.get("ordinary_evidence")
        if not isinstance(record, dict) or record.get("registered") is not True:
            errors.append(f"{case['nodeid']}: ordinary evidence not registered")
            continue
        snapshots = record.get("snapshots")
        if not isinstance(snapshots, dict) or (not snapshots and not (
            record.get("retention") == "discarded_after_all_phases_passed" and case.get("status") == "passed" and not record.get("errors")
        )):
            errors.append(f"{case['nodeid']}: ordinary snapshots missing or malformed")
            continue
        for point, snapshot in snapshots.items():
            try:
                expected = output / "case-evidence" / hashlib.sha256(case["nodeid"].encode()).hexdigest() / f"{point}.json"
                if point not in CaseEvidence.SLOTS or snapshot["path"] != str(expected):
                    raise ValueError("snapshot path/slot does not belong to this run/case")
                read_case_snapshot(expected, nodeid=case["nodeid"], point=point, expected_digest=snapshot["digest"])
                paths.append(expected)
            except Exception as exc:
                errors.append(f"{case['nodeid']} {point}: {type(exc).__name__}: {exc}")
    return paths, errors


def attach_case_evidence(request: Any, root: Path, *, metadata: dict[str, Any]) -> CaseEvidence | None:
    """Opt an existing ordinary test into capture when run by the TestKit.

Plain pytest still executes its original assertions; it makes no durable raw
evidence claim. The caller explicitly owns this synthetic, secret-free root.
"""
    if not request.config.getoption("--testkit-report", default=None):
        return None
    evidence = request.getfixturevalue("testkit_case_evidence")
    evidence.register(root, metadata=metadata, synthetic_no_secrets=True)
    return evidence
