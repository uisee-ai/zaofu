"""SHA-256 helpers used by attestation, skills provenance, and LKG config.

Consolidates two near-duplicate implementations that previously lived in
``zf.core.skills.provenance._sha256`` (file hashing) and
``zf.core.config.lkg._sha256_text`` (text hashing). PWF-ATTEST-001 (sprint
``2026-05-18-0843``) extended this to a third use case (artifact attestation),
making the duplication painful — hence this module.

This module is *not* the kernel-mandatory hash chain for ``events.jsonl``;
that is ``zf.core.security.signing.EventSigner`` (HMAC-SHA256, different
purpose: integrity of the append-only event stream). These helpers compute
plain SHA-256 of content for artifact attestation.
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path


_FILE_CHUNK_BYTES = 65536


def sha256_text(text: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file's content. Streams in 64 KiB chunks
    so large artifacts (transcripts, manifests) don't allocate the full
    file in memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_FILE_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: object) -> str:
    """SHA-256 hex digest of a canonical JSON serialization of ``obj``.

    Uses ``sort_keys=True`` so semantically equal dicts hash identically
    regardless of insertion order, and ``ensure_ascii=False`` so non-ASCII
    content is encoded once (UTF-8) rather than escaped twice.
    """
    return sha256_text(_json.dumps(obj, sort_keys=True, ensure_ascii=False))
