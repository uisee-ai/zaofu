"""Tests for zf.core.security.hash — unified SHA-256 helpers.

These cover the canonical helpers plus verify that the deprecation aliases
in ``zf.core.skills.provenance._sha256`` and ``zf.core.config.lkg._sha256_text``
still produce identical output (no behavior change for existing callers).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zf.core.security.hash import sha256_file, sha256_json, sha256_text


def test_sha256_text_empty() -> None:
    # SHA-256 of empty string is well-known
    assert sha256_text("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_text_ascii() -> None:
    assert sha256_text("hello") == hashlib.sha256(b"hello").hexdigest()


def test_sha256_text_unicode() -> None:
    # UTF-8 encoding for non-ASCII
    cn = "造福"
    expected = hashlib.sha256(cn.encode("utf-8")).hexdigest()
    assert sha256_text(cn) == expected


def test_sha256_file_small(tmp_path: Path) -> None:
    p = tmp_path / "small.txt"
    p.write_bytes(b"hello world\n")
    assert sha256_file(p) == hashlib.sha256(b"hello world\n").hexdigest()


def test_sha256_file_large_streaming(tmp_path: Path) -> None:
    # 200 KiB of repeating content — exercises the 64 KiB chunk loop
    p = tmp_path / "big.bin"
    payload = (b"abcdefgh" * 1024) * 25
    p.write_bytes(payload)
    assert sha256_file(p) == hashlib.sha256(payload).hexdigest()


def test_sha256_json_key_order_invariant() -> None:
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert sha256_json(a) == sha256_json(b)


def test_sha256_json_non_ascii() -> None:
    obj = {"name": "造福", "kind": "harness"}
    # Encoded once with ensure_ascii=False
    import json

    expected = hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert sha256_json(obj) == expected


def test_sha256_json_nested() -> None:
    obj = {"outer": {"inner": [1, 2, 3]}}
    same = {"outer": {"inner": [1, 2, 3]}}
    assert sha256_json(obj) == sha256_json(same)


def test_provenance_deprecation_alias_unchanged(tmp_path: Path) -> None:
    """provenance._sha256 must produce identical output to sha256_file."""
    from zf.core.skills.provenance import _sha256

    p = tmp_path / "skill.md"
    p.write_bytes(b"# SKILL\nfoo: bar\n")
    assert _sha256(p) == sha256_file(p)


def test_lkg_deprecation_alias_unchanged() -> None:
    """lkg._sha256_text must produce identical output to sha256_text."""
    from zf.core.config.lkg import _sha256_text

    text = "version: '1.0'\nproject:\n  name: zaofu\n"
    assert _sha256_text(text) == sha256_text(text)
