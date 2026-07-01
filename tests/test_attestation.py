"""ZF-PWF-ATTEST-001 §3.2-§3.5 — artifact attestation tests (doc 41 §4.3).

Covers the 6 artifact kinds, hash computation, persistence,
verification matching + tampering detection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.security.attestation import (
    ATTESTATION_KIND_CONTEXT_MANIFEST,
    ATTESTATION_KIND_RESEARCH_INDEX,
    ATTESTATION_KIND_ROLE_BRIEFING,
    ATTESTATION_KIND_SKILLS_MANIFEST,
    ATTESTATION_KIND_STATE_PACKET,
    ATTESTATION_KIND_TASK_CONTRACT,
    ATTESTATION_SCHEMA_VERSION,
    Attestation,
    KNOWN_ATTESTATION_KINDS,
    VerificationResult,
    attest_file_artifact,
    attest_object_artifact,
    attestation_path_for,
    hash_artifact_file,
    hash_artifact_json_object,
    hash_artifact_text,
    read_attestation,
    verify_file_artifact,
    verify_object_artifact,
    write_attestation,
)


# ---------------------------------------------------------------------------
# Constants & invariants
# ---------------------------------------------------------------------------


def test_six_artifact_kinds_declared() -> None:
    """Doc 41 §4.3 §6 hard requirement: exactly 6 attestation kinds."""
    assert len(KNOWN_ATTESTATION_KINDS) == 6
    assert {
        ATTESTATION_KIND_TASK_CONTRACT,
        ATTESTATION_KIND_STATE_PACKET,
        ATTESTATION_KIND_CONTEXT_MANIFEST,
        ATTESTATION_KIND_ROLE_BRIEFING,
        ATTESTATION_KIND_SKILLS_MANIFEST,
        ATTESTATION_KIND_RESEARCH_INDEX,
    } == KNOWN_ATTESTATION_KINDS


def test_schema_version_is_1_0() -> None:
    assert ATTESTATION_SCHEMA_VERSION == "1.0"


def test_attestation_is_frozen() -> None:
    a = Attestation(
        artifact_path="x.json",
        kind=ATTESTATION_KIND_STATE_PACKET,
        sha256="a" * 64,
    )
    with pytest.raises((AttributeError, TypeError)):
        a.sha256 = "b" * 64  # type: ignore[misc]


def test_attestation_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        Attestation(
            artifact_path="x.json", kind="mystery", sha256="a" * 64,
        )


def test_attestation_rejects_non_hex_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        Attestation(
            artifact_path="x.json",
            kind=ATTESTATION_KIND_STATE_PACKET,
            sha256="not-hex",
        )


def test_attestation_rejects_short_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        Attestation(
            artifact_path="x.json",
            kind=ATTESTATION_KIND_STATE_PACKET,
            sha256="abc",
        )


# ---------------------------------------------------------------------------
# Hash helpers — basic sanity
# ---------------------------------------------------------------------------


def test_hash_artifact_file_returns_hex_64(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello")
    h = hash_artifact_file(p)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_artifact_json_object_is_key_order_invariant() -> None:
    assert hash_artifact_json_object({"a": 1, "b": 2}) \
        == hash_artifact_json_object({"b": 2, "a": 1})


def test_hash_artifact_text_changes_with_content() -> None:
    assert hash_artifact_text("hello") != hash_artifact_text("world")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_write_creates_attestation_file(tmp_path: Path) -> None:
    a = Attestation(
        artifact_path=".zf/state/state-packet.json",
        kind=ATTESTATION_KIND_STATE_PACKET,
        sha256="a" * 64,
    )
    path = write_attestation(tmp_path, a)
    assert path.exists()
    assert path.parent == tmp_path / "attestations"


def test_attestation_path_for_uses_safe_segment(tmp_path: Path) -> None:
    """Slashes in artifact paths become __ so the attestation file
    lives in one flat directory."""
    p = attestation_path_for(tmp_path, ".zf/state/state-packet.json")
    assert p == tmp_path / "attestations" / "zf__state__state-packet.json.attest.json"


def test_read_attestation_round_trip(tmp_path: Path) -> None:
    original = Attestation(
        artifact_path="docs/spec.md",
        kind=ATTESTATION_KIND_TASK_CONTRACT,
        sha256="b" * 64,
        source_events=("evt-1", "evt-2"),
        task_id="TASK-X",
        dispatch_id="disp-1",
        attested_at="2026-05-18T10:00:00Z",
    )
    write_attestation(tmp_path, original)
    loaded = read_attestation(tmp_path, "docs/spec.md")
    assert loaded == original


def test_read_attestation_missing_returns_none(tmp_path: Path) -> None:
    assert read_attestation(tmp_path, "missing.md") is None


def test_read_attestation_malformed_returns_none(tmp_path: Path) -> None:
    target = attestation_path_for(tmp_path, "broken.json")
    target.parent.mkdir(parents=True)
    target.write_text("not json")
    assert read_attestation(tmp_path, "broken.json") is None


# ---------------------------------------------------------------------------
# attest_file_artifact + attest_object_artifact
# ---------------------------------------------------------------------------


def test_attest_file_artifact_writes_and_returns(tmp_path: Path) -> None:
    artifact = tmp_path / "briefing.md"
    artifact.write_text("the briefing")
    attestation = attest_file_artifact(
        tmp_path,
        artifact_path=artifact,
        kind=ATTESTATION_KIND_ROLE_BRIEFING,
        task_id="TASK-1",
        dispatch_id="disp-1",
    )
    assert attestation.kind == ATTESTATION_KIND_ROLE_BRIEFING
    assert attestation.task_id == "TASK-1"
    assert attestation.dispatch_id == "disp-1"
    # Hash matches direct computation
    assert attestation.sha256 == hash_artifact_file(artifact)
    # File on disk exists
    assert read_attestation(tmp_path, str(artifact)) is not None


def test_attest_object_artifact_writes_and_returns(tmp_path: Path) -> None:
    contract = {"behavior": "x", "verification": "y"}
    attestation = attest_object_artifact(
        tmp_path,
        obj=contract,
        artifact_path="task-contract:TASK-1",
        kind=ATTESTATION_KIND_TASK_CONTRACT,
    )
    assert attestation.kind == ATTESTATION_KIND_TASK_CONTRACT
    assert attestation.sha256 == hash_artifact_json_object(contract)


# ---------------------------------------------------------------------------
# verify_file_artifact / verify_object_artifact
# ---------------------------------------------------------------------------


def test_verify_file_matches_after_attest(tmp_path: Path) -> None:
    artifact = tmp_path / "briefing.md"
    artifact.write_text("v1")
    attest_file_artifact(
        tmp_path,
        artifact_path=artifact,
        kind=ATTESTATION_KIND_ROLE_BRIEFING,
    )
    result = verify_file_artifact(tmp_path, artifact_path=artifact)
    assert result.matched is True
    assert result.tampered is False


def test_verify_file_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "briefing.md"
    artifact.write_text("v1")
    attest_file_artifact(
        tmp_path,
        artifact_path=artifact,
        kind=ATTESTATION_KIND_ROLE_BRIEFING,
    )
    # Modify after attestation
    artifact.write_text("v2-tampered")
    result = verify_file_artifact(tmp_path, artifact_path=artifact)
    assert result.matched is False
    assert result.reason == "hash_mismatch"
    assert result.tampered is True


def test_verify_file_no_attestation_returns_false(tmp_path: Path) -> None:
    artifact = tmp_path / "x.md"
    artifact.write_text("hi")
    result = verify_file_artifact(tmp_path, artifact_path=artifact)
    assert result.matched is False
    assert result.reason == "no_attestation"
    assert result.tampered is False


def test_verify_file_missing_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "briefing.md"
    artifact.write_text("hi")
    attest_file_artifact(
        tmp_path,
        artifact_path=artifact,
        kind=ATTESTATION_KIND_ROLE_BRIEFING,
    )
    artifact.unlink()
    result = verify_file_artifact(tmp_path, artifact_path=artifact)
    assert result.matched is False
    assert result.reason == "missing_artifact"


def test_verify_object_matches_after_attest(tmp_path: Path) -> None:
    obj = {"a": 1, "b": 2}
    attest_object_artifact(
        tmp_path, obj=obj, artifact_path="task-contract:T1",
        kind=ATTESTATION_KIND_TASK_CONTRACT,
    )
    result = verify_object_artifact(
        tmp_path, obj=obj, artifact_path="task-contract:T1",
    )
    assert result.matched is True


def test_verify_object_detects_drift(tmp_path: Path) -> None:
    obj = {"a": 1}
    attest_object_artifact(
        tmp_path, obj=obj, artifact_path="task-contract:T1",
        kind=ATTESTATION_KIND_TASK_CONTRACT,
    )
    drifted = {"a": 2}
    result = verify_object_artifact(
        tmp_path, obj=drifted, artifact_path="task-contract:T1",
    )
    assert result.matched is False
    assert result.tampered is True


# ---------------------------------------------------------------------------
# 6 kinds — make sure each is usable end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(KNOWN_ATTESTATION_KINDS))
def test_each_kind_supports_object_attestation(
    tmp_path: Path, kind: str,
) -> None:
    obj = {"kind_under_test": kind}
    attestation = attest_object_artifact(
        tmp_path, obj=obj,
        artifact_path=f"synthetic:{kind}",
        kind=kind,
    )
    assert attestation.kind == kind
    loaded = read_attestation(tmp_path, f"synthetic:{kind}")
    assert loaded is not None
    assert loaded.kind == kind
