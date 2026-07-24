"""Admission guards for writer fanout task_map (doc 78 W1)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    WorkflowSplitQualityConfig,
    WorkflowWorkUnitsConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.workflow.lane_pipeline import (
    parse_lane_pipeline,
    validate_lane_pipeline_admission,
)
from zf.runtime.writer_fanout_admission import (
    load_writer_task_map,
    validate_writer_task_items,
    writer_task_items,
)


def _item(task_id: str, allowed: list[str], scope: str | None = None) -> dict:
    return {
        "task_id": task_id,
        "scope": scope if scope is not None else task_id,
        "allowed_paths": allowed,
    }


def test_sibling_paths_disjoint_ok():
    # Two slices owning distinct sub-trees — no overlap, must pass.
    validate_writer_task_items([
        _item("state", ["cj-min/packages/state/**"]),
        _item("provider", ["cj-min/packages/provider/**"]),
    ])


def test_distinct_top_dirs_ok():
    validate_writer_task_items([
        _item("impl", ["packages/**"]),
        _item("tests", ["tests/**"]),
    ])


def test_parent_child_prefix_overlap_rejected():
    # T1 root cause: pi-core owns the whole packages/ tree while a child owns a
    # sub-path of it. Parent/child prefix overlap must be rejected at admission.
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("pi-core", ["cj-min/packages/**"]),
            _item("state", ["cj-min/packages/state/**"]),
        ])


def test_exact_path_reuse_rejected():
    # Pre-existing behavior preserved: identical allowed path across slices.
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("a", ["packages/state/**"]),
            _item("b", ["packages/state/**"]),
        ])


def test_whole_repo_glob_conflicts_with_any_slice():
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("scaffold", ["**"]),
            _item("state", ["packages/state/**"]),
        ])


def test_sibling_prefix_not_component_confused():
    # packages/state vs packages/stateful are siblings, NOT ancestor/descendant.
    validate_writer_task_items([
        _item("state", ["packages/state/**"]),
        _item("stateful", ["packages/stateful/**"]),
    ])


def test_root_file_glob_disjoint_from_dir_glob_ok():
    # Regression: a root-level file glob like *.md must NOT normalize to the
    # empty tuple (whole-repo), which would falsely reject a slice owning a
    # disjoint subtree.
    validate_writer_task_items([
        _item("docs", ["*.md"]),
        _item("impl", ["src/**"]),
    ])


def test_leading_dot_slash_overlap_still_detected():
    # ./src/** and src/state/** overlap; a leading ./ must not defeat the check.
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("a", ["./src/**"]),
            _item("b", ["src/state/**"]),
        ])


def test_within_slice_parent_child_allowed():
    # Same owner may declare both a dir and its sub-path; only CROSS-slice
    # overlap is a conflict.
    validate_writer_task_items([
        _item("state", ["packages/state/**", "packages/state/db/**"]),
        _item("provider", ["packages/provider/**"]),
    ])


def test_writer_task_items_drops_placeholder_paths_owned_by_subtree_slice():
    items = writer_task_items({
        "tasks": [
            {
                "task_id": "scaffold",
                "allowed_paths": [
                    "app/package.json",
                    "app/test/.gitkeep",
                ],
            },
            {
                "task_id": "cli-tests",
                "allowed_paths": ["app/test/**"],
            },
        ],
    })

    by_id = {item["task_id"]: item for item in items}
    assert by_id["scaffold"]["allowed_paths"] == ["app/package.json"]
    validate_writer_task_items(items)


def test_writer_task_items_rejects_delegated_placeholder_still_required_by_contract():
    with pytest.raises(RuntimeError, match="delegated placeholder"):
        writer_task_items({
            "tasks": [
                {
                    "task_id": "scaffold",
                    "allowed_paths": [
                        "app/pyproject.toml",
                        "app/static/.gitkeep",
                    ],
                    "acceptance_criteria": [
                        "SCAFFOLD-AC1: app/static/.gitkeep exists",
                    ],
                    "verification": [
                        "test -f app/static/.gitkeep",
                    ],
                },
                {
                    "task_id": "server",
                    "allowed_paths": ["app/static/**"],
                },
            ],
        })


def test_writer_task_items_keeps_real_cross_task_file_subtree_overlap_rejected():
    items = writer_task_items({
        "tasks": [
            {
                "task_id": "scaffold",
                "allowed_paths": [
                    "app/test/cli.test.js",
                ],
            },
            {
                "task_id": "cli-tests",
                "allowed_paths": ["app/test/**"],
            },
        ],
    })

    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items(items)


def test_writer_task_items_dedupes_same_task_redundant_subtree_files():
    items = writer_task_items({
        "tasks": [{
            "task_id": "tests",
            "allowed_paths": ["app/test/**", "app/test/cli.test.js"],
        }],
    })

    assert items[0]["allowed_paths"] == ["app/test/**"]
    validate_writer_task_items(items)


def test_midpath_recursion_glob_overlap_rejected():
    # 2026-06-10 review P1-5: `src/foo/**/*.py` previously kept the literal
    # `**` component (only TRAILING globs were stripped), so it compared as
    # disjoint from `src/foo/bar.py` and both slices were admitted into
    # concurrent worktrees writing the same file.
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("a", ["src/foo/**/*.py"]),
            _item("b", ["src/foo/bar.py"]),
        ])


def test_midpath_recursion_glob_overlap_rejected_ts_shape():
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("core", ["packages/core/**/*.ts"]),
            _item("entry", ["packages/core/index.ts"]),
        ])


def test_midpath_glob_disjoint_subtrees_still_admitted():
    # The concrete prefix before the glob is what matters; disjoint
    # prefixes stay admitted.
    validate_writer_task_items([
        _item("a", ["src/foo/**/*.py"]),
        _item("b", ["src/bar/baz.py"]),
    ])


def test_directory_position_glob_truncates_prefix():
    # `src/foo*/bar` matches arbitrary sibling dirs of foo*; its concrete
    # prefix is `src`, which overlaps `src/anything`.
    with pytest.raises(RuntimeError, match="overlap"):
        validate_writer_task_items([
            _item("a", ["src/foo*/bar/**"]),
            _item("b", ["src/foonew/other.py"]),
        ])


def test_writer_task_items_preserves_lane_pipeline_contract_fields():
    items = writer_task_items({
        "tasks": [{
            "task_id": "web-tui",
            "root_owner_class": "assembly",
            "affinity_tag": "web-tui",
            "context_group": "cj-min-node-web-tui",
            "pipeline_declared_task_id": "CJMIN-ASSEMBLY-001",
            "preferred_impl_role": "dev-web-tui-assembly",
            "preferred_review_role": "review-web-tui",
            "preferred_verify_role": "verify-web-tui",
            "depends_on": ["pi-core"],
            "exclusive_files": ["package.json"],
            "git_fact_anchors": ["main@abc", "web/src/pages/ChatPage.tsx:1"],
            "verification_tiers": ["static", "judge"],
            "acceptance_criteria": ["root build passes"],
            "allowed_paths": ["package.json"],
        }],
    })

    item = items[0]
    assert item["root_owner_class"] == "assembly"
    assert item["affinity_tag"] == "web-tui"
    assert item["context_group"] == "cj-min-node-web-tui"
    assert item["pipeline_declared_task_id"] == "CJMIN-ASSEMBLY-001"
    assert item["preferred_impl_role"] == "dev-web-tui-assembly"
    assert item["preferred_review_role"] == "review-web-tui"
    assert item["preferred_verify_role"] == "verify-web-tui"
    assert item["depends_on"] == ["pi-core"]
    assert item["exclusive_files"] == ["package.json"]
    assert "web/src/pages/ChatPage.tsx:1" in item["git_fact_anchors"]
    assert item["owner_instance"] == "dev-web-tui-assembly"
    assert item["verification_tiers"] == ["static", "manual_evidence"]
    assert item["raw_verification_tiers"] == ["static", "judge"]
    assert item["acceptance_criteria"] == ["root build passes"]


def test_writer_task_items_normalizes_verification_command_list():
    items = writer_task_items({
        "tasks": [{
            "task_id": "light-deliver",
            "allowed_paths": ["app/**"],
            "verification": ["python app/verify.py", "git diff --check"],
        }],
    })

    assert items[0]["verification"] == "python app/verify.py"
    assert [item["command"] for item in items[0]["validation"]["commands"]] == [
        "python app/verify.py",
        "git diff --check",
    ]
    assert items[0]["raw_task"]["verification"] == [
        "python app/verify.py",
        "git diff --check",
    ]


def test_writer_task_items_accepts_issue_style_path_field():
    items = writer_task_items({
        "tasks": [{
            "id": "T1",
            "title": "Fix list command",
            "path": "app/src/index.js",
        }],
    })

    assert items[0]["task_id"] == "T1"
    assert items[0]["allowed_paths"] == ["app/src/index.js"]


def test_lane_pipeline_admission_allows_single_nested_bugfix_without_root_owner():
    spec = parse_lane_pipeline({
        "id": "issue-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{"id": "impl"}, {"id": "verify"}],
        "final": {"when": "all_tasks_verified", "role": "judge"},
    })
    items = writer_task_items({
        "tasks": [{
            "id": "T1",
            "title": "Fix list command",
            "path": "app/src/index.js",
        }],
    })

    assert validate_lane_pipeline_admission(spec, items) == []


def test_lane_pipeline_admission_rejects_nested_bugfix_when_root_owner_is_required():
    spec = parse_lane_pipeline({
        "id": "issue-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{"id": "impl"}, {"id": "verify"}],
        "final": {"when": "all_tasks_verified", "role": "judge"},
    })
    task_map = {
        "workspace_root_owner_required": True,
        "tasks": [{
            "id": "T1",
            "title": "Fix list command",
            "path": "app/src/index.js",
        }],
    }
    items = writer_task_items(task_map)

    problems = validate_lane_pipeline_admission(
        spec,
        items,
        task_map_payload=task_map,
    )

    assert "no task in the task_map owns workspace-root paths" in problems[0]


def test_writer_task_items_derives_wave_from_top_level_waves_and_keeps_blocked_by():
    items = writer_task_items({
        "waves": [
            {"wave": 1, "tasks": ["TASK-SCAFFOLD"]},
            {"wave": 2, "tasks": ["TASK-CLI"]},
        ],
        "tasks": [
            {
                "task_id": "TASK-SCAFFOLD",
                "allowed_paths": ["app/package.json"],
            },
            {
                "task_id": "TASK-CLI",
                "allowed_paths": ["app/src/index.js"],
                "blocked_by": ["TASK-SCAFFOLD"],
            },
        ],
    })

    by_id = {item["task_id"]: item for item in items}
    assert by_id["TASK-SCAFFOLD"]["wave"] == 1
    assert by_id["TASK-CLI"]["wave"] == 2
    assert by_id["TASK-CLI"]["blocked_by"] == ["TASK-SCAFFOLD"]


def test_load_writer_task_map_rejects_bad_verification_before_dispatch(tmp_path):
    task_map = tmp_path / "task_map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "allowed_paths": ["package.json"],
                "verification": (
                    "bash -lc 'set -euo pipefail; "
                    "pnpm --filter @cj-min/contracts exec node -e "
                    "\"const fs=require('node:fs');"
                    "JSON.parse(fs.readFileSync('package.json','utf8'))\"'"
                ),
            }],
        }),
        encoding="utf-8",
    )
    event = ZfEvent(
        type="task_map.ready",
        actor="kernel",
        payload={"task_map_ref": str(task_map), "pdd_id": "CJMIN-R37"},
    )

    with pytest.raises(ValueError, match="writer fanout task_map validation failed"):
        load_writer_task_map(
            stage=SimpleNamespace(task_map=""),
            event=event,
            pdd_id="CJMIN-R37",
            state_dir=tmp_path / ".zf",
            project_root=tmp_path,
        )


def test_load_writer_task_map_preserves_complete_plan_package_identity(tmp_path):
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-A",
            "title": "A",
            "allowed_paths": ["src/a.py"],
            "verification": "true",
        }],
    }), encoding="utf-8")

    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map=""),
        event=ZfEvent(
            type="task_map.ready",
            actor="kernel",
            payload={
                "task_map_ref": str(task_map),
                "plan_artifact_package_id": "planpkg-abc",
                "plan_artifact_package_ref": "artifacts/plan-packages/abc.json",
                "plan_artifact_package_digest": "abc",
            },
        ),
        pdd_id="PDD-A",
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )

    assert loaded.plan_artifact_package_id == "planpkg-abc"
    assert loaded.plan_artifact_package_ref == "artifacts/plan-packages/abc.json"
    assert loaded.plan_artifact_package_digest == "abc"


def test_load_writer_task_map_requires_run_scoped_verification(tmp_path):
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-A",
            "title": "A",
            "allowed_paths": ["src/a.py"],
            "acceptance": ["A works"],
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="task_contract_required.*TASK-A"):
        load_writer_task_map(
            stage=SimpleNamespace(task_map=""),
            event=ZfEvent(
                type="task_map.ready",
                actor="planner",
                payload={"task_map_ref": str(task_map)},
            ),
            pdd_id="PDD-A",
            state_dir=tmp_path / ".zf",
            project_root=tmp_path,
            candidate_quality_source="task_contract_required",
        )


@pytest.mark.parametrize(
    ("task", "match"),
    [
        ({
            "task_id": "TASK-SCOPE",
            "allowed_paths": ["a", "b", "c"],
            "verification": "true",
        }, "scope has 3 files, max is 2"),
        ({
            "task_id": "TASK-AC",
            "allowed_paths": ["a"],
            "acceptance": ["one", "two", "three"],
            "verification": "true",
        }, "3 acceptance criteria, max is 2"),
    ],
)
def test_load_writer_task_map_blocks_oversized_work_unit_before_dispatch(
    tmp_path,
    task,
    match,
):
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [task],
    }), encoding="utf-8")
    policy = WorkflowWorkUnitsConfig(
        enabled=True,
        split_quality=WorkflowSplitQualityConfig(
            mode="blocking",
            max_scope_files=2,
            max_acceptance_criteria=2,
        ),
    )

    with pytest.raises(ValueError, match=match):
        load_writer_task_map(
            stage=SimpleNamespace(task_map=""),
            event=ZfEvent(
                type="task_map.ready",
                actor="planner",
                payload={"task_map_ref": str(task_map)},
            ),
            pdd_id="PDD-A",
            state_dir=tmp_path / ".zf",
            project_root=tmp_path,
            work_units_config=policy,
        )
