"""B-93-03 (doc 93 §4): plan-digest 投影 + checklist 单测。"""

from __future__ import annotations

from zf.runtime.plan_digest import plan_digest_checklist, render_plan_digest

_GOOD = [
    {"task_id": "CJMIN-ASSEMBLY-001", "root_owner_class": "assembly", "owner_role": "assembly",
     "allowed_paths": ["package.json", "tsconfig.json"], "verification": "pnpm build", "wave": 1},
    {"task_id": "CJMIN-PROVIDER-001", "owner_role": "dev", "allowed_paths": ["packages/provider/**"],
     "verification": "pnpm -F provider test", "wave": 1},
]


def _map(items):
    return {c["key"]: c["ok"] for c in plan_digest_checklist(items)}


def test_checklist_all_green_on_well_formed_map():
    m = _map(_GOOD)
    assert m == {"assembly_present": True, "root_owner_unique": True,
                 "verification_present": True, "scope_no_overlap": True}


def test_digest_reads_canonical_validation_commands():
    item = {**_GOOD[0], "validation": {"commands": [
        {"id": "local", "command": "node --test tests/feature.test.mjs"},
    ]}}
    item.pop("verification")
    assert _map([item])["verification_present"] is True
    assert "node --test tests/feature.test.mjs" in render_plan_digest([item])


def test_digest_does_not_mask_invalid_canonical_command_with_legacy_value():
    item = {**_GOOD[0], "validation": {"commands": [{"id": "missing-command"}]}}
    assert _map([item])["verification_present"] is False


def test_checklist_flags_missing_assembly_and_root_owner_dup():
    # R21/R29 形态:无 assembly,两个任务都持根文件
    bad = [
        {"task_id": "A", "owner_role": "dev-a", "allowed_paths": ["package.json"], "verification": "x"},
        {"task_id": "B", "owner_role": "dev-b", "allowed_paths": ["package.json"], "verification": "y"},
    ]
    m = _map(bad)
    assert m["assembly_present"] is False      # 缺 assembly
    assert m["root_owner_unique"] is False      # 两任务持根
    assert m["scope_no_overlap"] is False        # package.json 重叠


def test_distinct_root_files_do_not_claim_an_owner_conflict():
    items = [
        {"task_id": "COMPOSE", "allowed_paths": ["compose.yaml"], "verification": "check"},
        {"task_id": "IMAGE", "allowed_paths": ["Dockerfile"], "verification": "check"},
    ]
    assert _map(items)["root_owner_unique"] is True
    assert _map(items)["scope_no_overlap"] is True
    assert "不替代计划准入" in render_plan_digest(items)


def test_checklist_allows_simple_serial_prd_task_without_assembly():
    checks = {
        c["key"]: c
        for c in plan_digest_checklist([{
            "task_id": "FMT-TASK-001",
            "owner_role": "dev-runtime",
            "allowed_paths": ["src/todo.py", "tests/test_format_task.py"],
            "verification": "uv run pytest tests/test_format_task.py -q",
            "wave": 1,
        }])
    }

    assert checks["assembly_present"]["ok"] is True
    assert "不要求独立 assembly" in checks["assembly_present"]["detail"]
    assert checks["verification_present"]["ok"] is True
    assert checks["scope_no_overlap"]["ok"] is True


def test_checklist_keeps_parallel_bundles_without_assembly_red():
    checks = {
        c["key"]: c
        for c in plan_digest_checklist([
            {"task_id": "ISSUE-A", "owner_role": "dev-a", "allowed_paths": ["src/calc.py"], "verification": "pytest"},
            {"task_id": "ISSUE-B", "owner_role": "dev-b", "allowed_paths": ["tests/test_calc.py"], "verification": "pytest"},
        ])
    }

    assert checks["assembly_present"]["ok"] is False
    assert "缺 assembly" in checks["assembly_present"]["detail"]


def test_checklist_flags_missing_verification():
    m = _map([{"task_id": "A", "root_owner_class": "assembly",
               "allowed_paths": ["package.json"]}])
    assert m["verification_present"] is False


def test_checklist_flags_owner_role_config_and_duplicates():
    bad = [
        {"task_id": "A", "owner_role": "dev-runtime",
         "allowed_paths": ["src/a.js"], "verification": "npm test", "wave": 1},
        {"task_id": "B", "owner_role": "dev-runtime",
         "allowed_paths": ["src/b.js"], "verification": "npm test", "wave": 1},
        {"task_id": "C", "owner_role": "dev-missing",
         "allowed_paths": ["src/c.js"], "verification": "npm test", "wave": 1},
    ]

    checks = {
        c["key"]: c
        for c in plan_digest_checklist(
            bad,
            allowed_owner_roles=["dev-runtime", "dev-web"],
            require_unique_owner_roles=True,
        )
    }

    assert checks["owner_role_configured"]["ok"] is False
    assert "C:dev-missing" in checks["owner_role_configured"]["detail"]
    assert checks["owner_role_unique"]["ok"] is False
    assert "dev-runtime@wave 1: A, B" in checks["owner_role_unique"]["detail"]


def test_checklist_allows_owner_role_reuse_across_waves():
    tasks = [
        {"task_id": "STATE", "owner_role": "dev-lane-0",
         "allowed_paths": ["src/state.js"], "verification": "npm test", "wave": 1},
        {"task_id": "RENDER", "owner_role": "dev-lane-1",
         "allowed_paths": ["src/render.js"], "verification": "npm test", "wave": 1},
        {"task_id": "ASSEMBLY", "owner_role": "dev-lane-0",
         "root_owner_class": "assembly", "allowed_paths": ["package.json"],
         "verification": "npm test", "wave": 3},
    ]

    checks = {
        c["key"]: c
        for c in plan_digest_checklist(
            tasks,
            allowed_owner_roles=["dev-lane-0", "dev-lane-1"],
            require_unique_owner_roles=True,
        )
    }

    assert checks["owner_role_configured"]["ok"] is True
    assert checks["owner_role_unique"]["ok"] is True


def test_render_contains_table_and_checklist():
    md = render_plan_digest(_GOOD, plan_id="evt-1", task_map_ref="ref/x")
    assert "# Plan Digest — evt-1" in md
    assert "task_map_ref: `ref/x`" in md
    assert "CJMIN-ASSEMBLY-001" in md and "CJMIN-PROVIDER-001" in md
    assert "## Checklist" in md
    assert "🟢" in md  # 全绿


def test_serial_root_owners_use_admission_topology_without_dedicated_assembly():
    tasks = [
        {"task_id": "UPPER", "owner_role": "dev-0", "allowed_paths": ["upper.py"], "blocked_by": []},
        {"task_id": "COUNT", "owner_role": "dev-0", "allowed_paths": ["count.py"], "blocked_by": ["UPPER"]},
        {"task_id": "LINES", "owner_role": "dev-0", "allowed_paths": ["lines.py"], "blocked_by": ["UPPER", "COUNT"]},
    ]
    assert _map(tasks)["assembly_present"] is True
    assert "缺 assembly" not in render_plan_digest(tasks)
    # Switching owners does not create parallel bundles when dependencies remain.
    tasks[1]["owner_role"] = "dev-1"
    assert _map(tasks)["assembly_present"] is True
    tasks[1]["blocked_by"] = []
    assert _map(tasks)["assembly_present"] is False


def test_unknown_topology_is_not_a_false_failure_or_success():
    md = render_plan_digest([{"task_id": "A", "allowed_paths": ["a.py"]}])
    assert "⚪ **assembly/根 owner 在位**" in md
    assert "无法确认" in md


def test_assembly_name_does_not_override_explicit_task_class_or_self_lock():
    tasks = [{"task_id": "ASSEMBLY-NAMED", "owner_role": "dev-a", "root_owner_class": "bundle"},
             {"task_id": "B", "owner_role": "dev-b"}]
    assert _map(tasks)["assembly_present"] is False
    tasks[0].update(root_owner_class="assembly", blocked_by=["B"], owner_role="dev-b")
    assert _map(tasks)["assembly_present"] is False
