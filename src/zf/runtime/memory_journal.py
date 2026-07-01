"""ZF-TR-JOURNAL-001 — memory journal rotation (doc 26 §5.4 P2).

Rotation policy: when ``.zf/memory/<role>.md`` exceeds a size
threshold (default 2000 lines), rotate into ``journal-N.md`` files
and rebuild ``index.md`` so historical entries stay browsable
without bloating a single file.

Pure-function design: caller passes the current text + threshold;
this module decides whether to rotate and produces the new file set.
Disk IO is left to the caller (orchestrator integration writes via
atomic_write_text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_HEADER_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class JournalRotation:
    """Result of evaluating + producing a rotation."""

    needs_rotation: bool
    current_text: str
    archived_text: str = ""
    archive_filename: str = ""
    index_text: str = ""


def should_rotate(text: str, *, max_lines: int = 2000) -> bool:
    return len(text.splitlines()) > max_lines


def plan_rotation(
    *,
    text: str,
    role: str,
    next_journal_n: int,
    max_lines: int = 2000,
    keep_recent_lines: int = 500,
) -> JournalRotation:
    """Decide if rotation is needed; produce archived + new current
    texts + an index entry for the rotated file.

    Rotation rule (line-count based):
    - if total lines > max_lines → rotate
    - newest ``keep_recent_lines`` lines stay in the current file
    - older lines move to ``journal-<N>.md``
    """
    if not should_rotate(text, max_lines=max_lines):
        return JournalRotation(needs_rotation=False, current_text=text)

    lines = text.splitlines()
    archive_lines = lines[:-keep_recent_lines] if keep_recent_lines else lines
    current_lines = lines[-keep_recent_lines:] if keep_recent_lines else []
    archive_text = "\n".join(archive_lines) + ("\n" if archive_lines else "")
    current_text = "\n".join(current_lines) + ("\n" if current_lines else "")
    archive_filename = f"journal-{next_journal_n}.md"

    # Build a 1-line index entry summarising the archived range.
    headers = _HEADER_RE.findall(archive_text)
    title_summary = " / ".join(h.strip() for h in headers[:3]) or "(no titles)"
    index_line = (
        f"- [{role} journal {next_journal_n}](./{archive_filename}) "
        f"— {len(archive_lines)} lines · {title_summary}"
    )
    return JournalRotation(
        needs_rotation=True,
        current_text=current_text,
        archived_text=archive_text,
        archive_filename=archive_filename,
        index_text=index_line,
    )


def merge_index(existing_index: str, new_line: str) -> str:
    """Append ``new_line`` to a journal index, preserving the header."""
    if not existing_index.strip():
        return f"# Journal index\n\n{new_line}\n"
    if new_line in existing_index:
        return existing_index
    if not existing_index.endswith("\n"):
        existing_index += "\n"
    return existing_index + new_line + "\n"
