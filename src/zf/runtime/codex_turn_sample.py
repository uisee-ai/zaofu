"""Extract a native usage endpoint only from the registered lane-local rollout."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from zf.runtime.codex_turn_boundary import CodexTurnBoundary, timestamp


def native_uuid(text: object) -> bool:
    try:
        return isinstance(text, str) and str(UUID(text)) == text
    except ValueError:
        return False


def scoped_rollout(state: Path, role: str, session: str, path: Path) -> bool:
    if not native_uuid(session) or not role or Path(role).name != role or role in {".", ".."}:
        return False
    try:
        relative = path.relative_to(state / "workdirs" / role / "codex-home" / "sessions")
    except ValueError:
        return False
    return re.fullmatch(
        rf"\d{{4}}/\d{{2}}/\d{{2}}/rollout-\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}}-{session}\.jsonl",
        relative.as_posix(),
    ) is not None


@contextmanager
def open_rollout(state: Path, path: Path):
    """The lexical scope has been checked; pin every descent to avoid link swaps."""
    directory = os.open(state, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.relative_to(state).parts[:-1]:
            child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as stream:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe registered provider rollout")
            yield stream
    finally:
        os.close(directory)


class CodexUsageTurn:
    """Attribute a token record within one turn after a native process restart.

    A stopped process may leave an earlier turn open. A new, unique start and
    matching context can identify later samples without certifying the old
    turn's completion or a continuous native execution interval.
    """

    def __init__(self, session: str):
        self.session = session
        self.valid_header = False
        self.first_row = True
        self.started = 0.0
        self.turn = self.context = self.sample = ""
        self.seen: set[str] = set()

    def consume(self, row: dict) -> None:
        p = row.get("payload") or {}
        kind = row.get("type")
        if kind == "session_meta":
            self.valid_header = self.first_row and p.get("id") == self.session
        elif kind == "turn_context":
            self.context = p.get("turn_id") if p.get("turn_id") == self.turn else ""
        elif kind == "event_msg":
            event = p.get("type")
            if event == "task_started":
                turn = p.get("turn_id")
                self.turn = turn if native_uuid(turn) and turn not in self.seen else ""
                if self.turn:
                    self.seen.add(self.turn)
                self.started = timestamp(row.get("timestamp"))
                self.context = self.sample = ""
            elif event in {"task_complete", "turn_aborted", "task_aborted"}:
                self.turn = self.context = ""
            elif event == "token_count" and isinstance(p.get("info"), dict):
                self.sample = self.turn if (
                    self.valid_header and self.context == self.turn
                    and p.get("turn_id", self.turn) == self.turn
                    and 0 < self.started <= timestamp(row.get("timestamp"))
                ) else ""
        self.first_row = False


class CodexTurnSample:
    def __init__(self, session: str):
        self.session = session
        self.boundary = CodexTurnBoundary()
        self.contexts: set[tuple[str, str]] = set()
        self.first = True
        self.valid_sample = False

    def consume(self, row: dict) -> None:
        kind = row.get("type")
        p = row.get("payload")
        if not isinstance(p, dict):
            return
        if self.first:
            self.first = False
            if kind != "session_meta" or p.get("id") != self.session:
                self.boundary.valid = False
        self.boundary.consume(row)
        if kind == "event_msg" and p.get("type") == "task_started":
            self.contexts.clear()
            self.valid_sample = False
        if kind == "turn_context" and self.boundary.state == "running":
            if p.get("turn_id") != self.boundary.turn:
                self.boundary.valid = False
            else:
                self.contexts.add((str(p.get("cwd") or ""), str(p.get("model") or "")))
        if kind == "event_msg" and p.get("type") == "task_aborted":
            self.boundary.valid = False
        if kind == "event_msg" and p.get("type") == "token_count" and isinstance(p.get("info"), dict):
            usage = p["info"].get("last_token_usage")
            self.valid_sample = bool(
                self.boundary.state == "running" and len(self.contexts) == 1
                and p.get("turn_id", self.boundary.turn) == self.boundary.turn
                and isinstance(usage, dict) and any(type(usage.get(k)) is int and usage[k] > 0
                    for k in ("input_tokens", "output_tokens", "total_tokens"))
            )

    def receipt(self, sampled_at: str, model: str) -> dict:
        b = self.boundary
        if (not b.valid or b.session != self.session or not self.valid_sample or not native_uuid(b.turn)
                or not 0 < b.started < b.usage_at or b.usage_at != timestamp(sampled_at)
                or len(self.contexts) != 1):
            return {}
        cwd, native_model = next(iter(self.contexts))
        if not Path(cwd).is_absolute() or not native_model or native_model != model:
            return {}
        return {"schema_version": "native-turn-sample.v1", "session_id": self.session, "turn_id": b.turn,
                "workspace_sha256": hashlib.sha256(cwd.encode()).hexdigest(),
                "native_started_at": datetime.fromtimestamp(b.started, timezone.utc).isoformat(),
                "native_usage_at": sampled_at}
