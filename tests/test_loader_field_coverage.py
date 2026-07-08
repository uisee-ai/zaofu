"""Loader field-coverage test — prevent B-NEW-3 silent-drop regression.

For every ``@dataclass *Config`` field declared in
``src/zf/core/config/schema.py``, verify the field name appears as a
string literal in ``src/zf/core/config/loader.py``. If not, the loader
is silently dropping the field and the dataclass default wins — exactly
the B-NEW-3 bug (auto_ship_on_candidate_complete dropped → silent
ship stall) and its relapse (RoleConfig.spawn_ready_timeout_seconds
dropped → operator's yaml override silently ignored).

See ``docs/impl/22-zaofu-canonical-dag.md §9`` for the full lesson and
context.

A field can be exempted by adding it to ``_ALLOWLIST`` below with a
``# justification: ...`` comment explaining why the field name doesn't
appear literally in loader.py (e.g., it's read via a nested yaml key).
Entries MUST cite the loader line where the field is actually wired,
so future readers can verify the exemption is still valid.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_CONFIG_DIR = Path(__file__).parent.parent / "src" / "zf" / "core" / "config"
_SCHEMA_PATH = _CONFIG_DIR / "schema.py"
_LOADER_PATH = _CONFIG_DIR / "loader.py"


# Fields whose name does NOT appear literally in loader.py but which ARE
# correctly wired through alternative means. Every entry must cite the
# loader line that actually wires the field, so reviewers can verify.
#
# Format: ("ConfigClass", "field_name"): "justification — loader.py:LINE"
_ALLOWLIST: dict[tuple[str, str], str] = {
    # doc 90 A1/A2: 这两个字段不是 yaml 表面 —— 它们是 loader 在
    # lane_pipeline 展开/schema merge 后落盘的派生投影(inspect 直读),
    # 以属性赋值写入而非 data.get() 读取,故名字不以 yaml 键字面量出现。
    ("WorkflowConfig", "pipelines_role_meta"): (
        "loader-written projection (generate_lane_roles meta) — "
        "loader.py:1388 pipelines_role_meta=pipelines_role_meta (WorkflowConfig ctor)"
    ),
    ("WorkflowConfig", "pipelines_schema_sources"): (
        "loader-written projection (merge_event_schemas sources) — "
        "loader.py:1449 cfg.workflow.pipelines_schema_sources = sources"
    ),
    ("SafetyConfig", "tool_closure_enabled"): (
        "read via nested yaml key safety.tool_closure.enabled — "
        "loader.py:732 does tool_closure.get('enabled', True), so the "
        "schema field name doesn't appear as a literal string"
    ),
    ("ProjectConfig", "setup_script"): (
        "read via nested yaml key project.scripts.setup — "
        "loader.py:283 does scripts.get('setup', ''), so the schema field "
        "name doesn't appear as a data.get literal; parse covered by "
        "test_config_loader.py::test_project_scripts_setup_parsed"
    ),
    ("WorkflowConfig", "plan_approval_enabled"): (
        "B14 (doc 93 §8): read via nested yaml key workflow.plan_approval "
        "(bool or {enabled: bool}) through _parse_plan_approval_enabled — "
        "loader.py:354 wires it into the WorkflowConfig ctor"
    ),
    ("WorkflowConfig", "flow_metadata"): (
        "doc-125 flow intake: read via renamed yaml key workflow._flow_metadata "
        "(underscore-prefixed reserved key) — loader.py:1862 does "
        "flow_metadata=workflow_data.get('_flow_metadata', {}), so the field "
        "name doesn't appear as a literal string"
    ),
}


def _config_dataclasses() -> list[tuple[str, list[str]]]:
    """Return [(ClassName, [field_name, ...]), ...] for every
    @dataclass *Config in schema.py."""
    tree = ast.parse(_SCHEMA_PATH.read_text())
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Config"):
            continue
        # Must be decorated with @dataclass (any variant)
        if not any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                and d.func.id == "dataclass")
            for d in node.decorator_list
        ):
            continue
        fields = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append(stmt.target.id)
        if fields:
            result.append((node.name, fields))
    return result


@pytest.fixture(scope="module")
def loader_text() -> str:
    return _LOADER_PATH.read_text()


@pytest.mark.parametrize(
    ("cls_name", "field_name"),
    [
        (cls, f)
        for cls, fields in _config_dataclasses()
        for f in fields
    ],
)
def test_loader_reads_config_field(
    cls_name: str, field_name: str, loader_text: str,
) -> None:
    """Each schema field must appear as a string literal in loader.py.

    Fail = a probable B-NEW-3 silent drop: schema declares the field,
    yaml can carry it, but the loader never wires it through, so the
    dataclass default silently wins. Fix by either:

      (1) Adding ``<field>=...`` kwarg to the loader's dataclass
          constructor with a ``data.get("<field>", <default>)`` read; or

      (2) If the field is intentionally read via a nested yaml key or
          builder helper, add an entry to ``_ALLOWLIST`` in this test
          file with a justification citing the loader line that wires
          it.
    """
    if (cls_name, field_name) in _ALLOWLIST:
        pytest.skip(
            f"allowlisted: {_ALLOWLIST[(cls_name, field_name)]}"
        )
    pattern = re.compile(rf'["\']{re.escape(field_name)}["\']')
    if not pattern.search(loader_text):
        pytest.fail(
            f"\nLoader silent-drop candidate (B-NEW-3 pattern):\n"
            f"  schema.py declares {cls_name}.{field_name}\n"
            f"  loader.py does NOT mention '{field_name}' as a string "
            f"literal\n"
            f"\n"
            f"Likely cause: dataclass default silently wins over yaml. "
            f"Operators configuring '{field_name}' in zf.yaml will see "
            f"no effect.\n"
            f"\n"
            f"Fix: add kwarg to the loader's {cls_name}(...) "
            f"constructor, e.g.:\n"
            f"    {field_name}=data.get('{field_name}', <default>),\n"
            f"\n"
            f"If this is a false positive (e.g. field read via nested "
            f"yaml key), add to _ALLOWLIST in this test file with a "
            f"justification."
        )


def test_audit_finds_all_config_classes() -> None:
    """Smoke test: schema.py has the expected number of *Config
    dataclasses. If this drops sharply, something is wrong with the
    AST walker."""
    classes = _config_dataclasses()
    assert len(classes) >= 20, (
        f"Expected ≥20 *Config dataclasses, found {len(classes)}: "
        f"{[c for c, _ in classes]}"
    )


def test_allowlist_entries_are_well_formed() -> None:
    """Each allowlist entry must cite a loader.py line in its
    justification, so reviewers can verify the exemption."""
    loader_lines = _LOADER_PATH.read_text().splitlines()
    for (cls, field), justification in _ALLOWLIST.items():
        assert justification.strip(), (
            f"Allowlist entry {cls}.{field} has empty justification"
        )
        # Must cite a loader.py line — pattern "loader.py:NNN"
        line_match = re.search(r"loader\.py:(\d+)", justification)
        assert line_match, (
            f"Allowlist entry {cls}.{field} justification must cite a "
            f"loader.py:LINE: got {justification!r}"
        )
        line_no = int(line_match.group(1))
        assert 1 <= line_no <= len(loader_lines), (
            f"Allowlist entry {cls}.{field} cites loader.py:{line_no} "
            f"but loader.py has only {len(loader_lines)} lines"
        )
