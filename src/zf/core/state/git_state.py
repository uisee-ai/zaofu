"""GitState dataclass — pure schema, no I/O.

The actual git capture lives in zf.runtime.git_capture (subprocess is a
side effect and stays out of core/ per the deterministic kernel rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GitState:
    branch: str | None = None
    head: str | None = None
    dirty_files: list[str] = field(default_factory=list)
    last_commit_msg: str = ""
    ts: str = ""
