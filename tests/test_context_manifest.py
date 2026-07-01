"""ZF-TR-CTXMAN-001 — per-dispatch context manifest tests (doc 39 §2.1.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.context_manifest import (
    ContextRef,
    read_context_manifest,
    required_refs_missing,
    write_context_manifest,
)


# ---------------------------------------------------------------------------
# ContextRef invariants
# ---------------------------------------------------------------------------


def test_context_ref_is_frozen() -> None:
    ref = ContextRef(kind="spec_ref", path="x.md")
    with pytest.raises((AttributeError, TypeError)):
        ref.path = "y.md"  # type: ignore[misc]


def test_context_ref_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        ContextRef(kind="mystery", path="x.md")


def test_context_ref_accepts_all_documented_kinds() -> None:
    for kind in (
        "state_packet", "task_contract", "spec_ref", "research",
        "skill", "git", "quality_gate", "artifact",
    ):
        ContextRef(kind=kind, path=f"{kind}.md")  # must not raise


# ---------------------------------------------------------------------------
# write_context_manifest
# ---------------------------------------------------------------------------


def test_write_creates_per_dispatch_files(tmp_path: Path) -> None:
    refs = [
        ContextRef(kind="state_packet", path=".zf/state/x.json"),
        ContextRef(kind="spec_ref", path="docs/spec.md", summary="ADR"),
    ]
    jsonl, md = write_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-A",
        dispatch_id="disp-1",
        refs=refs,
    )
    assert jsonl == tmp_path / "briefings" / "TASK-A" / "disp-1" / "context.jsonl"
    assert md == tmp_path / "briefings" / "TASK-A" / "disp-1" / "context.md"
    assert jsonl.exists()
    assert md.exists()


def test_write_jsonl_one_record_per_line(tmp_path: Path) -> None:
    refs = [
        ContextRef(kind="spec_ref", path="a.md"),
        ContextRef(kind="spec_ref", path="b.md"),
        ContextRef(kind="research", path="c.md"),
    ]
    jsonl_path, _ = write_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-A",
        dispatch_id="disp-1",
        refs=refs,
    )
    lines = jsonl_path.read_text().strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert "kind" in obj
        assert "path" in obj


def test_write_empty_refs_still_produces_files(tmp_path: Path) -> None:
    jsonl, md = write_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-E",
        dispatch_id="disp-1",
        refs=[],
    )
    assert jsonl.exists()
    assert md.exists()
    assert jsonl.read_text() == ""
    assert "No context refs declared" in md.read_text()


def test_write_md_groups_by_kind(tmp_path: Path) -> None:
    refs = [
        ContextRef(kind="spec_ref", path="a.md"),
        ContextRef(kind="research", path="b.md"),
        ContextRef(kind="spec_ref", path="c.md"),
    ]
    _, md = write_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-G",
        dispatch_id="disp-1",
        refs=refs,
    )
    text = md.read_text()
    assert "## research" in text
    assert "## spec_ref" in text
    # Groups are alphabetised: research comes before spec_ref, and
    # both spec_ref entries are in the spec_ref block.
    research_block_start = text.index("## research")
    spec_block_start = text.index("## spec_ref")
    assert research_block_start < spec_block_start
    # a.md and c.md should both live in the spec_ref block (after
    # its heading, not before).
    assert text.index("a.md") > spec_block_start
    assert text.index("c.md") > spec_block_start
    # b.md belongs in the research block (between research header
    # and spec_ref header).
    assert research_block_start < text.index("b.md") < spec_block_start


def test_write_rejects_missing_task_or_dispatch_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_context_manifest(
            state_dir=tmp_path, task_id="", dispatch_id="disp",
            refs=[],
        )
    with pytest.raises(ValueError):
        write_context_manifest(
            state_dir=tmp_path, task_id="TASK", dispatch_id="",
            refs=[],
        )


# ---------------------------------------------------------------------------
# read_context_manifest round-trip + tolerance
# ---------------------------------------------------------------------------


def test_round_trip_preserves_refs(tmp_path: Path) -> None:
    refs = [
        ContextRef(kind="spec_ref", path="a.md", summary="X", required=True, role="dev"),
        ContextRef(kind="research", path="b.md", required=False),
    ]
    write_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-R",
        dispatch_id="disp-1",
        refs=refs,
    )
    loaded = read_context_manifest(
        state_dir=tmp_path,
        task_id="TASK-R",
        dispatch_id="disp-1",
    )
    assert loaded == refs


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_context_manifest(
        state_dir=tmp_path, task_id="TASK-X", dispatch_id="disp-1",
    ) == []


def test_read_skips_malformed_lines(tmp_path: Path) -> None:
    target = tmp_path / "briefings" / "TASK-M" / "disp-1"
    target.mkdir(parents=True)
    jsonl = target / "context.jsonl"
    jsonl.write_text(
        "not json\n"
        + json.dumps({
            "kind": "spec_ref",
            "path": "ok.md",
            "summary": "",
            "required": True,
            "role": "",
        }) + "\n",
        encoding="utf-8",
    )
    refs = read_context_manifest(
        state_dir=tmp_path, task_id="TASK-M", dispatch_id="disp-1",
    )
    assert len(refs) == 1
    assert refs[0].path == "ok.md"


# ---------------------------------------------------------------------------
# required_refs_missing — preflight gate helper
# ---------------------------------------------------------------------------


def test_required_refs_missing_returns_list(tmp_path: Path) -> None:
    refs = [
        ContextRef(kind="spec_ref", path="missing-1.md", required=True),
        ContextRef(kind="spec_ref", path="missing-2.md", required=False),
        ContextRef(kind="research", path="present.md", required=True),
    ]
    (tmp_path / "present.md").write_text("hi")
    missing = required_refs_missing(refs, project_root=tmp_path)
    assert "missing-1.md" in missing
    # missing-2.md is not required → skipped
    assert "missing-2.md" not in missing
    # present.md exists → not missing
    assert "present.md" not in missing


def test_required_refs_missing_with_absolute_paths(tmp_path: Path) -> None:
    real = tmp_path / "real.md"
    real.write_text("hi")
    refs = [
        ContextRef(kind="spec_ref", path=str(real), required=True),
        ContextRef(kind="spec_ref", path="/nonexistent/abs/path.md",
                    required=True),
    ]
    missing = required_refs_missing(refs, project_root=tmp_path)
    assert "/nonexistent/abs/path.md" in missing
    assert str(real) not in missing


def test_required_refs_missing_empty_path_skipped(tmp_path: Path) -> None:
    refs = [ContextRef(kind="spec_ref", path="", required=True)]
    missing = required_refs_missing(refs, project_root=tmp_path)
    assert missing == []
