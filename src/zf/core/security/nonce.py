"""Nonce manager — issue, validate, consume, cleanup."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class NonceManager:
    """Manage single-use nonces with TTL."""

    def __init__(self, nonces_dir: Path, *, ttl: float = 300.0) -> None:
        self.nonces_dir = nonces_dir
        self.ttl = ttl

    def issue(self, role: str) -> str:
        """Issue a new nonce for a role. File lives in `unused/` until consumed."""
        unused_dir = self.nonces_dir / "unused"
        unused_dir.mkdir(parents=True, exist_ok=True)
        (self.nonces_dir / "used").mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        meta = {"role": role, "issued_at": time.time(), "used": False}
        (unused_dir / nonce).write_text(json.dumps(meta))
        return nonce

    def _path(self, nonce: str) -> Path:
        # Backwards compat: legacy nonces sit at the root of nonces_dir.
        new_path = self.nonces_dir / "unused" / nonce
        if new_path.exists():
            return new_path
        legacy = self.nonces_dir / nonce
        return legacy if legacy.exists() else new_path

    def validate_and_consume(self, nonce: str) -> bool:
        """Atomically validate AND consume a nonce. Returns True only if the
        nonce was unused, valid, and now marked used as part of the same call.

        Implementation: os.rename from unused/<nonce> -> used/<nonce>. POSIX
        rename is atomic; the second concurrent caller observes ENOENT and
        returns False. Closes review M6 (TOCTOU on validate+consume).
        """
        src = self._path(nonce)
        if not src.exists():
            return False
        try:
            meta = json.loads(src.read_text())
        except json.JSONDecodeError:
            return False
        if meta.get("used"):
            return False
        if time.time() - meta["issued_at"] > self.ttl:
            return False
        used_dir = self.nonces_dir / "used"
        used_dir.mkdir(parents=True, exist_ok=True)
        dst = used_dir / nonce
        try:
            os.rename(src, dst)
        except (FileNotFoundError, OSError):
            return False
        meta["used"] = True
        dst.write_text(json.dumps(meta))
        return True

    def validate(self, nonce: str) -> bool:
        """Check if nonce is valid (exists, not expired, not used).

        NOTE: prefer validate_and_consume() — this method is racy and only
        kept for backwards compatibility with existing callers.
        """
        path = self._path(nonce)
        if not path.exists():
            return False
        try:
            meta = json.loads(path.read_text())
        except json.JSONDecodeError:
            return False
        if meta.get("used"):
            return False
        if time.time() - meta["issued_at"] > self.ttl:
            return False
        return True

    def consume(self, nonce: str) -> None:
        """Mark a nonce as used. Prefer validate_and_consume() for new code."""
        path = self._path(nonce)
        if not path.exists():
            return
        try:
            meta = json.loads(path.read_text())
        except json.JSONDecodeError:
            return
        meta["used"] = True
        path.write_text(json.dumps(meta))

    def cleanup(self) -> int:
        """Remove expired nonces from both unused/ and used/ subdirs and any
        legacy nonces sitting at the root. Returns count removed.
        """
        if not self.nonces_dir.exists():
            return 0
        removed = 0
        now = time.time()
        candidates: list[Path] = []
        for sub in ("unused", "used"):
            d = self.nonces_dir / sub
            if d.exists():
                candidates.extend(p for p in d.iterdir() if p.is_file())
        # Legacy: nonces at the root (pre-A8 layout)
        for p in self.nonces_dir.iterdir():
            if p.is_file():
                candidates.append(p)
        for path in candidates:
            try:
                meta = json.loads(path.read_text())
                if now - meta["issued_at"] > self.ttl:
                    path.unlink()
                    removed += 1
            except (json.JSONDecodeError, KeyError, FileNotFoundError):
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed
