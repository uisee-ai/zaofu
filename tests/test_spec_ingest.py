"""Tests for ``zf spec ingest`` CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zf.cli import spec as spec_cli


def _make_args(path: Path, state_dir: Path, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        path=str(path),
        state_dir=str(state_dir),
        dry_run=dry_run,
    )


def _write_zf_yaml(root: Path) -> None:
    (root / "zf.yaml").write_text(
        "project:\n  name: spec-ingest-test\n  state_dir: .zf\nroles: []\n",
        encoding="utf-8",
    )


def test_spec_ingest_creates_feature_and_tasks(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        """---
spec: vs1-runtime
feature_key: cangjie-vs1
phase: P1
tasks:
  - id: TASK-VS1A
    title: runtime bootstrap
    owner_role: dev
    scope: [packages/core/runtime/index.ts]
    acceptance:
      - test -f packages/core/runtime/index.ts
    verification_tiers: [static, runtime]
    behavior: |
      bootstrap the runtime kernel.
    blocked_by: []
    wave: 1
    shared_files: [packages/core/runtime/types.ts]
    exclusive_files: [packages/core/runtime/index.ts]
    handoff_artifacts: [packages/core/runtime/index.ts]
  - id: TASK-VS1B
    title: runtime wire-up
    scope: [packages/core/runtime/wire.ts]
    acceptance:
      - test -f packages/core/runtime/wire.ts
    blocked_by: [TASK-VS1A]
    wave: wave-2
    shared_files: [packages/core/runtime/types.ts]
    exclusive_files: [packages/core/runtime/wire.ts]
---

# Body ignored
""",
        encoding="utf-8",
    )

    args = _make_args(spec_path, state_dir)
    rc = spec_cli._run_ingest(args)
    assert rc == 0

    feature_list = json.loads((state_dir / "feature_list.json").read_text())
    assert len(feature_list) == 1
    feat = feature_list[0]
    assert feat["title"] == "cangjie-vs1"
    assert feat["status"] == "active"

    kanban = json.loads((state_dir / "kanban.json").read_text())
    ids = sorted(t["id"] for t in kanban)
    assert ids == ["TASK-VS1A", "TASK-VS1B"]

    by_id = {t["id"]: t for t in kanban}
    assert by_id["TASK-VS1A"]["contract"]["feature_id"] == feat["id"]
    assert by_id["TASK-VS1A"]["contract"]["phase"] == "P1"
    assert by_id["TASK-VS1A"]["contract"]["verification_tiers"] == ["static", "runtime"]
    assert by_id["TASK-VS1A"]["contract"]["owner_role"] == "dev"
    assert by_id["TASK-VS1A"]["blocked_by"] == []
    assert by_id["TASK-VS1A"]["contract"]["wave"] == 1
    assert by_id["TASK-VS1A"]["contract"]["shared_files"] == [
        "packages/core/runtime/types.ts",
    ]
    assert by_id["TASK-VS1A"]["contract"]["exclusive_files"] == [
        "packages/core/runtime/index.ts",
    ]
    assert by_id["TASK-VS1B"]["contract"]["owner_role"] == "dev"  # default
    assert by_id["TASK-VS1B"]["blocked_by"] == ["TASK-VS1A"]
    assert by_id["TASK-VS1B"]["contract"]["wave"] == 2
    assert by_id["TASK-VS1B"]["contract"]["shared_files"] == [
        "packages/core/runtime/types.ts",
    ]
    assert by_id["TASK-VS1B"]["contract"]["exclusive_files"] == [
        "packages/core/runtime/wire.ts",
    ]
    for task_id in ("TASK-VS1A", "TASK-VS1B"):
        assert (state_dir / "task_docs" / task_id / "task.md").exists()
        assert (state_dir / "task_docs" / task_id / "source.md").exists()
        assert (state_dir / "task_docs" / task_id / "manifest.json").exists()
        contract = by_id[task_id]["contract"]
        assert contract["task_doc_ref"].endswith(f"task_docs/{task_id}/task.md")
        assert contract["source_revision"].startswith("source-r")
        assert contract["contract_revision"].startswith("contract-r")
        assert contract["capsule_revision"].startswith("capsule-r")

    events = [
        json.loads(ln)
        for ln in (state_dir / "events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    types = [e["type"] for e in events]
    assert "feature.created" in types
    assert types.count("task.created") == 2
    assert types.count("task.contract.update") == 2
    assert types.count("task.doc.updated") == 2


def test_spec_ingest_preserves_source_key_and_ref(tmp_path: Path, monkeypatch):
    """source_key / source_ref from the spec-bridge frontmatter must flow
    into the task contract AND the capsule source.md (doc 71 source-coverage
    anchor). Regression: the agent-skills ingest path used to drop both,
    falling back to a kernel-derived feature:task key — confirmed against
    three real yamls on 2026-06-12."""
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        """---
spec: cart
feature_key: shopping-cart
tasks:
  - title: cart store
    owner_role: dev
    scope: [packages/cart/src/store.ts]
    acceptance: [test -f packages/cart/src/store.ts]
    verification: npm test -- cart/store
    source_key: cart.md#2-scope
    source_ref: docs/specs/cart.md
---
""",
        encoding="utf-8",
    )

    assert spec_cli._run_ingest(_make_args(spec_path, state_dir)) == 0

    kanban = json.loads((state_dir / "kanban.json").read_text())
    contract = kanban[0]["contract"]
    assert contract["source_key"] == "cart.md#2-scope"
    assert contract["source_ref"] == "docs/specs/cart.md"

    source_md = (
        state_dir / "task_docs" / kanban[0]["id"] / "source.md"
    ).read_text()
    assert "cart.md#2-scope" in source_md


def test_spec_ingest_idempotent(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        """---
spec: smoke
tasks:
  - id: TASK-IDEM
    title: idempotent task
    scope: [a.ts]
    acceptance: [test -f a.ts]
---
""",
        encoding="utf-8",
    )

    rc1 = spec_cli._run_ingest(_make_args(spec_path, state_dir))
    assert rc1 == 0
    rc2 = spec_cli._run_ingest(_make_args(spec_path, state_dir))
    assert rc2 == 0

    kanban = json.loads((state_dir / "kanban.json").read_text())
    assert len(kanban) == 1  # not duplicated

    events = [
        json.loads(ln)
        for ln in (state_dir / "events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    # feature.created should fire exactly once, task.created exactly once
    assert sum(1 for e in events if e["type"] == "feature.created") == 1
    assert sum(1 for e in events if e["type"] == "task.created") == 1


def test_spec_ingest_dry_run_writes_nothing(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        """---
spec: dryrun
tasks:
  - title: dry-run task
    scope: [a.ts]
    acceptance: [test -f a.ts]
---
""",
        encoding="utf-8",
    )

    rc = spec_cli._run_ingest(_make_args(spec_path, state_dir, dry_run=True))
    assert rc == 0
    assert not (state_dir / "kanban.json").exists()
    assert not (state_dir / "feature_list.json").exists()
    assert not (state_dir / "events.jsonl").exists()


def test_spec_ingest_missing_frontmatter(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "no_front.md"
    spec_path.write_text("# Just markdown\nNothing here.\n", encoding="utf-8")

    rc = spec_cli._run_ingest(_make_args(spec_path, state_dir))
    assert rc == 2


def test_spec_ingest_invalid_task(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec_path = tmp_path / "bad.md"
    spec_path.write_text(
        """---
spec: bad
tasks:
  - title: missing scope
---
""",
        encoding="utf-8",
    )

    rc = spec_cli._run_ingest(_make_args(spec_path, state_dir))
    assert rc == 2


def test_spec_validate_rejects_shared_exclusive_overlap(
    tmp_path: Path,
    capsys,
):
    spec_path = tmp_path / "overlap.md"
    spec_path.write_text(
        """---
spec: overlap
tasks:
  - id: TASK-OVERLAP
    title: overlap
    scope: [src/a.py]
    acceptance: [test -f src/a.py]
    shared_files: [src/a.py]
    exclusive_files: [src/a.py]
---
""",
        encoding="utf-8",
    )

    rc = spec_cli._run_validate(argparse.Namespace(path=str(spec_path), strict=True))

    captured = capsys.readouterr()
    assert rc == 1
    assert "shared_files overlaps exclusive_files" in captured.err


def test_build_plan_auto_feature_id_stable():
    """uuid5 keyed by spec slug — re-running must yield same id."""
    fm = {
        "spec": "stable-id",
        "tasks": [{"title": "t", "scope": ["a"], "acceptance": ["test -f a"]}],
    }
    plan_a = spec_cli._build_ingest_plan(fm, Path("/x/spec.md"))
    plan_b = spec_cli._build_ingest_plan(fm, Path("/x/spec.md"))
    assert plan_a["feature_id"] == plan_b["feature_id"]
    assert plan_a["feature_id"].startswith("F-")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _validate_args(path: Path, strict: bool = False) -> argparse.Namespace:
    return argparse.Namespace(path=str(path), strict=strict)


def test_validate_ok(tmp_path: Path, capsys):
    spec = tmp_path / "ok.md"
    spec.write_text(
        """---
spec: ok-spec
tasks:
  - id: TASK-OK
    title: ok task
    scope: [a.ts]
    acceptance: [test -f a.ts]
---
body content
""",
        encoding="utf-8",
    )
    assert spec_cli._run_validate(_validate_args(spec)) == 0
    out = capsys.readouterr().out
    assert "OK spec=ok-spec" in out
    assert "tasks=1" in out


def test_validate_missing_frontmatter(tmp_path: Path, capsys):
    spec = tmp_path / "naked.md"
    spec.write_text("# Naked markdown\n", encoding="utf-8")
    rc = spec_cli._run_validate(_validate_args(spec))
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL no-frontmatter" in err


def test_validate_missing_scope(tmp_path: Path, capsys):
    spec = tmp_path / "bad.md"
    spec.write_text(
        """---
spec: bad
tasks:
  - title: missing scope
    acceptance: [echo ok]
---
""",
        encoding="utf-8",
    )
    rc = spec_cli._run_validate(_validate_args(spec))
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL schema" in err
    assert "scope" in err


def test_validate_duplicate_task_ids(tmp_path: Path, capsys):
    spec = tmp_path / "dup.md"
    spec.write_text(
        """---
spec: dup
tasks:
  - id: TASK-DUPE
    title: first
    scope: [a]
    acceptance: [test -f a]
  - id: TASK-DUPE
    title: second
    scope: [b]
    acceptance: [test -f b]
---
""",
        encoding="utf-8",
    )
    rc = spec_cli._run_validate(_validate_args(spec))
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL duplicate-task-ids" in err
    assert "TASK-DUPE" in err


def test_validate_warns_on_body_orphan_task_id(tmp_path: Path, capsys):
    spec = tmp_path / "orphan.md"
    spec.write_text(
        """---
spec: orphan
tasks:
  - id: TASK-DECLARED
    title: declared
    scope: [a]
    acceptance: [test -f a]
---

# Body
We also depend on TASK-FREEFLY which is not declared above.
""",
        encoding="utf-8",
    )
    # default: warning, exit 0
    rc = spec_cli._run_validate(_validate_args(spec))
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARN body-orphan-task-ids" in err
    assert "TASK-FREEFLY" in err

    # strict: warning → fail
    rc_strict = spec_cli._run_validate(_validate_args(spec, strict=True))
    assert rc_strict == 1
    err_strict = capsys.readouterr().err
    assert "FAIL strict-mode" in err_strict


def test_validate_missing_acceptance_and_verification(tmp_path: Path):
    """Task with neither acceptance nor verification — caught at build_plan."""
    spec = tmp_path / "bad.md"
    spec.write_text(
        """---
spec: bad
tasks:
  - id: TASK-NOVER
    title: no verification
    scope: [a]
---
""",
        encoding="utf-8",
    )
    rc = spec_cli._run_validate(_validate_args(spec))
    assert rc == 1


def test_validate_does_not_write_state(tmp_path: Path, monkeypatch):
    """validate must NEVER touch state_dir."""
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: t\n  state_dir: .zf\nroles: []\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec = tmp_path / "ok.md"
    spec.write_text(
        """---
spec: ok
tasks:
  - id: TASK-X
    title: t
    scope: [a]
    acceptance: [test -f a]
---
""",
        encoding="utf-8",
    )
    spec_cli._run_validate(_validate_args(spec))

    # Nothing should be written
    assert not (state_dir / "kanban.json").exists()
    assert not (state_dir / "feature_list.json").exists()
    assert not (state_dir / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def _prompt_args(path: Path | None, system_only: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        path=str(path) if path else None,
        system_only=system_only,
    )


def test_prompt_outputs_system_and_user_sections(tmp_path: Path, capsys):
    spec = tmp_path / "naked.md"
    spec.write_text(
        "# Foo\n\nWork on `packages/ai/foo.ts`.\n",
        encoding="utf-8",
    )
    rc = spec_cli._run_prompt(_prompt_args(spec))
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== SYSTEM PROMPT ===" in out
    assert "=== USER MESSAGE ===" in out
    assert "=== INSTRUCTIONS ===" in out
    assert "packages/ai/foo.ts" in out
    assert "zf spec merge" in out
    assert "zf spec validate" in out
    assert "zf spec ingest" in out


def test_prompt_system_only(tmp_path: Path, capsys):
    rc = spec_cli._run_prompt(_prompt_args(None, system_only=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "spec → frontmatter extractor" in out
    assert "blocked_by" in out
    assert "wave" in out
    assert "shared_files" in out
    assert "exclusive_files" in out
    assert "=== USER MESSAGE ===" not in out


def test_prompt_refuses_existing_frontmatter(tmp_path: Path, capsys):
    spec = tmp_path / "already.md"
    spec.write_text("---\nspec: x\n---\nbody\n", encoding="utf-8")
    rc = spec_cli._run_prompt(_prompt_args(spec))
    assert rc == 1
    err = capsys.readouterr().err
    assert "already has frontmatter" in err


def test_prompt_missing_file(tmp_path: Path, capsys):
    rc = spec_cli._run_prompt(_prompt_args(tmp_path / "missing.md"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "file not found" in err


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _merge_args(
    path: Path,
    frontmatter: str,
    output: str = "overwrite",
    state_dir: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=str(path),
        frontmatter=frontmatter,
        output=output,
        state_dir=state_dir,
    )


def test_merge_from_file(tmp_path: Path, capsys):
    spec = tmp_path / "naked.md"
    spec.write_text("# Title\n\nbody.\n", encoding="utf-8")

    fm = tmp_path / "fm.json"
    fm.write_text(
        json.dumps({
            "spec": "merged-spec",
            "tasks": [
                {"title": "t", "scope": ["a.ts"], "acceptance": ["test -f a.ts"]}
            ],
        }),
        encoding="utf-8",
    )

    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 0
    out = spec.read_text(encoding="utf-8")
    assert out.startswith("---\n")
    assert "spec: merged-spec" in out
    assert "# Title" in out
    assert "body." in out


def test_merge_from_stdin(tmp_path: Path, monkeypatch):
    spec = tmp_path / "naked.md"
    spec.write_text("# X\n", encoding="utf-8")

    payload = json.dumps({
        "spec": "stdin-spec",
        "tasks": [{"title": "t", "scope": ["a"], "acceptance": ["test -f a"]}],
    })
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))

    rc = spec_cli._run_merge(_merge_args(spec, "-"))
    assert rc == 0
    assert "spec: stdin-spec" in spec.read_text()


def test_merge_strips_code_fences(tmp_path: Path):
    spec = tmp_path / "naked.md"
    spec.write_text("# x\n", encoding="utf-8")

    fm = tmp_path / "fm.json"
    fm.write_text(
        "```json\n"
        + json.dumps({
            "spec": "fenced",
            "tasks": [
                {"title": "t", "scope": ["a"], "acceptance": ["test -f a"]}
            ],
        })
        + "\n```",
        encoding="utf-8",
    )
    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 0
    assert "spec: fenced" in spec.read_text()


def test_merge_refuses_existing_frontmatter(tmp_path: Path, capsys):
    spec = tmp_path / "already.md"
    spec.write_text("---\nspec: a\n---\nbody\n", encoding="utf-8")
    fm = tmp_path / "fm.json"
    fm.write_text('{"spec":"new","tasks":[{"title":"t","scope":["a"],"acceptance":["x"]}]}',
                  encoding="utf-8")
    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 1
    assert "already has frontmatter" in capsys.readouterr().err


def test_merge_rejects_non_json(tmp_path: Path, capsys):
    spec = tmp_path / "x.md"
    spec.write_text("# x\n", encoding="utf-8")
    fm = tmp_path / "fm.txt"
    fm.write_text("this is not json", encoding="utf-8")
    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_merge_rejects_missing_required_keys(tmp_path: Path, capsys):
    spec = tmp_path / "x.md"
    spec.write_text("# x\n", encoding="utf-8")
    fm = tmp_path / "fm.json"
    fm.write_text('{"spec":"only-spec"}', encoding="utf-8")  # no tasks
    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 1
    assert "missing required keys" in capsys.readouterr().err


def test_merge_emits_event(tmp_path: Path, monkeypatch):
    _write_zf_yaml(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    spec = tmp_path / "naked.md"
    spec.write_text("# x\n", encoding="utf-8")
    fm = tmp_path / "fm.json"
    fm.write_text(
        json.dumps({
            "spec": "ev",
            "tasks": [{"title": "t", "scope": ["a"], "acceptance": ["test -f a"]}],
        }),
        encoding="utf-8",
    )
    rc = spec_cli._run_merge(_merge_args(spec, str(fm)))
    assert rc == 0

    events_file = state_dir / "events.jsonl"
    assert events_file.exists()
    events = [
        json.loads(ln)
        for ln in events_file.read_text().splitlines()
        if ln.strip()
    ]
    types = [e["type"] for e in events]
    assert "spec.extract.completed" in types
    evt = next(e for e in events if e["type"] == "spec.extract.completed")
    assert evt["payload"]["source"] == "merge"
    assert evt["payload"]["tasks_extracted"] == 1
