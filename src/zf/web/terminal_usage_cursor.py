"""Resume complete provider JSONL records within a pinned file version."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import BinaryIO, Iterator


@dataclass
class UsageCursor:
    version: tuple[int, int, int, int, int] | None = None
    offset: int = 0
    anchor: bytes = b""

    @staticmethod
    def file_version(handle: BinaryIO) -> tuple[int, int, int, int, int]:
        st = os.fstat(handle.fileno())
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    def fingerprint(self, handle: BinaryIO) -> bytes:
        # Validate both the header and the last committed bytes without retaining text.
        handle.seek(0)
        head = handle.read(min(4096, self.offset))
        start = max(0, self.offset - 4096)
        handle.seek(start)
        return hashlib.sha256(head + handle.read(self.offset - start)).digest()

    def continues(self, handle: BinaryIO, version: tuple[int, int, int, int, int]) -> bool:
        return (self.version is not None and version[:2] == self.version[:2]
                and version[2] > self.version[2] and self.fingerprint(handle) == self.anchor)

    def records(self, handle: BinaryIO, size: int) -> Iterator[dict]:
        handle.seek(self.offset)
        while self.offset < size:
            line = handle.readline(size - self.offset)
            if not line.endswith(b"\n"):
                break
            self.offset += len(line)
            try:
                value = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value
