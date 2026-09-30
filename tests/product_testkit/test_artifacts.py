from pathlib import Path

import pytest

from .artifacts import (ArtifactError, build_artifact_index, capture_artifact,
                        verify_artifact, verify_artifact_index, write_artifact_index)


def test_artifact_capture_and_digest_verification(tmp_path: Path):
    root = tmp_path / "run"
    root.mkdir()
    artifact = root / "report.txt"
    artifact.write_text("ok\n", encoding="utf-8")
    record = capture_artifact(artifact, root=root)
    verify_artifact(record, root=root)
    artifact.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="digest"):
        verify_artifact(record, root=root)


def test_artifact_escape_and_symlink_are_rejected(tmp_path: Path):
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ArtifactError):
        capture_artifact(outside, root=root)
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(ArtifactError):
        capture_artifact(link, root=root)


def test_build_artifact_index_is_bounded_and_digestable(tmp_path: Path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    index = build_artifact_index([first, second], root=tmp_path)
    assert [item["ref"] for item in index] == ["first.log", "second.log"]
    assert all(len(item["digest"]) == 64 for item in index)


def test_build_artifact_index_rejects_path_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-product-evidence.log"
    outside.write_text("secret", encoding="utf-8")
    try:
        with pytest.raises(ArtifactError, match="escapes root"):
            build_artifact_index([outside], root=tmp_path)
    finally:
        outside.unlink(missing_ok=True)


def test_verify_artifact_index_detects_evidence_drift(tmp_path: Path):
    evidence = tmp_path / "evidence.log"
    evidence.write_text("stable", encoding="utf-8")
    index = build_artifact_index([evidence], root=tmp_path)
    verify_artifact_index(index, root=tmp_path)
    evidence.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactError, match="digest"):
        verify_artifact_index(index, root=tmp_path)


def test_write_artifact_index_round_trips_atomically(tmp_path: Path):
    evidence = tmp_path / "evidence.log"
    evidence.write_text("stable", encoding="utf-8")
    index = build_artifact_index([evidence], root=tmp_path)
    destination = tmp_path / "out" / "artifact-index.json"
    write_artifact_index(index, destination)
    assert __import__("json").loads(destination.read_text(encoding="utf-8")) == index
    assert not destination.with_name(destination.name + ".tmp").exists()
