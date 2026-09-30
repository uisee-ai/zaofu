"""S0 audit checks for the machine-readable TestKit coverage map."""

from __future__ import annotations

import json
import ast
import re
from pathlib import Path

import pytest

from tests.testkit.suites import SUITES


def _test_source(entry: str) -> str:
    return entry.split("::", 1)[0]


def _assert_test_entry(repo_root: Path, entry: str) -> None:
    path_text, *selectors = entry.split("::")
    path = repo_root / path_text
    assert path.is_file(), entry
    if not selectors:
        return
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for selector in selectors:
        name = selector.split("[", 1)[0]
        matches = [node for node in body if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name == name]
        assert len(matches) == 1, f"missing test selector: {entry}"
        body = matches[0].body


def _audited_test_paths(repo_root: Path) -> set[Path]:
    matrix = json.loads(
        (
            repo_root
            / ("awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json")
        ).read_text(encoding="utf-8")
    )
    paths = {path for suite in SUITES.values() for path in suite}
    paths.update(
        path for stage in matrix["stages"] for path in stage.get("host_tests", [])
    )
    return {repo_root / _test_source(path) for path in paths if _test_source(path).endswith(".py")}


def test_testkit_ci_workflow_runs_sentinel_and_full_runner() -> None:
    """The repository CI must execute, not merely document, the TestKit."""

    repo_root = Path(__file__).resolve().parents[2]
    workflow = repo_root / ".github/workflows/zaofu-testkit.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "uv sync --locked --extra dev --extra web" in text
    assert "npm ci --prefix web --ignore-scripts --no-audit --no-fund" in text
    assert "bash scripts/dev-premerge-gate.sh" in text
    assert "uv run python -m tests.testkit" in text
    full_runner_start = text.index("- name: Run full deterministic TestKit")
    verdict_start = text.index("- name: Enforce TestKit verdict")
    assert "if: always()" in text[full_runner_start:verdict_start]
    assert "--stage all" in text
    assert "Enforce TestKit verdict" in text
    assert 'verdict != "pass"' in text
    assert "actions/upload-artifact@v4" in text


def test_coverage_matrix_lists_every_stage_and_existing_test_entry() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    matrix_path = (
        repo_root / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
    )
    issue_path = (
        repo_root / "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    issue_text = issue_path.read_text(encoding="utf-8")

    assert matrix["schema_version"] == "zaofu-testkit-coverage-matrix.v1"
    baseline = matrix["baseline_stage"]
    assert baseline == {
        "id": "S0",
        "domain": "testkit-coverage-audit",
        "status": "done",
        "tests": ["tests/conformance/test_zaofu_testkit_s0.py"],
        "role": "审计覆盖矩阵、阶段注册、P0 节点、Issue 映射和 CI 契约；不重复纳入 stages，避免自引用",
    }
    stages = matrix["stages"]
    assert [stage["id"] for stage in stages] == [f"S{i}" for i in range(1, 11)]
    for stage in stages:
        assert stage["status"] in {"done", "partial", "blocked"}
        assert stage["tests"]
        for test_path in stage["tests"]:
            _assert_test_entry(repo_root, test_path)

    issue_ids = {
        issue_id for stage in stages for issue_id in stage.get("known_issues", [])
    }
    assert issue_ids
    for issue_id in issue_ids:
        assert f"## {issue_id}" in issue_text
    for line in issue_text.splitlines():
        reference = line.strip()
        if not reference.startswith("tests/"):
            continue
        # Reproduction blocks may append pytest selectors such as ``-k``;
        # this audit validates the referenced file, while the dedicated
        # reproduction-entry audit below validates executability.
        test_path = reference.split(None, 1)[0].split("::", 1)[0]
        assert (repo_root / test_path).exists(), reference


def test_strict_testkit_xfails_are_traceable_to_registered_issues() -> None:
    """Every intentional red case must remain visible in the issue ledger."""

    repo_root = Path(__file__).resolve().parents[2]
    issue_text = (
        repo_root / "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
    ).read_text(encoding="utf-8")
    issue_ids = set(re.findall(r"^## (ZF-TEST-\d+)\b", issue_text, re.MULTILINE))

    reasons: list[str] = []
    registered_paths = _audited_test_paths(repo_root)
    for path in sorted(registered_paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_xfail = (
                isinstance(func, ast.Attribute)
                and func.attr == "xfail"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "mark"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "pytest"
            )
            if not is_xfail:
                continue
            reason = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "reason"),
                None,
            )
            if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
                reasons.append(reason.value)

    assert reasons, "TestKit must not hide intentional xfails without reasons"
    referenced_ids = {
        match.group(1)
        for reason in reasons
        if (match := re.search(r"\b(ZF-TEST-\d+)\b", reason))
    }
    assert referenced_ids <= issue_ids


def test_strict_testkit_xfails_reference_open_issue_status() -> None:
    """A resolved Issue must not remain hidden behind a strict xfail."""

    repo_root = Path(__file__).resolve().parents[2]
    issue_text = (
        repo_root / "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
    ).read_text(encoding="utf-8")
    sections = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"^## (ZF-TEST-\d+)\b.*?\n(.*?)(?=^## |\Z)",
            issue_text,
            re.MULTILINE | re.DOTALL,
        )
    }

    referenced_ids: set[str] = set()
    registered_paths = _audited_test_paths(repo_root)
    for path in sorted(registered_paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or ast.unparse(node.func) != "pytest.mark.xfail"
            ):
                continue
            reason = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "reason"),
                None,
            )
            if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
                referenced_ids.update(re.findall(r"\bZF-TEST-\d+\b", reason.value))

    stale: list[str] = []
    for issue_id in sorted(referenced_ids):
        body = sections.get(issue_id, "")
        status = next(
            (
                line.strip()
                for line in body.splitlines()
                if line.strip().startswith("> 状态:")
            ),
            "",
        )
        if not re.match(r"> 状态:\s*open(?:（|\s|$)", status):
            stale.append(f"{issue_id}: {status or 'missing status'}")
    assert not stale, "strict xfail must reference open Issues: " + ", ".join(stale)


def test_stage_registry_and_coverage_matrix_have_the_same_test_surface() -> None:
    """A documented stage must be executable by the TestKit runner."""

    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    matrix_by_id = {stage["id"]: set(stage["tests"]) for stage in matrix["stages"]}
    assert set(SUITES) == {"S0", *{f"S{i}" for i in range(1, 11)}}
    for stage, paths in SUITES.items():
        for path in paths:
            _assert_test_entry(repo_root, path)
        if stage == "S0":
            continue
        assert set(paths) <= matrix_by_id[stage], stage


def test_all_named_testkit_files_are_registered_or_explicit_host_tier() -> None:
    """A newly added TestKit module must not silently miss the runner."""

    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    registered = {_test_source(path) for paths in SUITES.values() for path in paths}
    explicit_host = {
        path for stage in matrix["stages"] for path in stage.get("host_tests", [])
    }
    discovered = {
        str(path.relative_to(repo_root))
        for root in (repo_root / "tests").rglob("test_zaofu_testkit_*.py")
        for path in (root,)
    }
    assert discovered <= registered | explicit_host, (
        "unregistered TestKit modules: "
        + ", ".join(sorted(discovered - registered - explicit_host))
    )


def test_explicit_host_tests_are_marked_and_excluded_from_default_registry() -> None:
    """Host-only tests must remain opt-in and visibly marked as such."""

    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    registered = {_test_source(path) for paths in SUITES.values() for path in paths}
    host_paths = {
        path for stage in matrix["stages"] for path in stage.get("host_tests", [])
    }
    assert host_paths.isdisjoint(registered)
    for path_text in sorted(host_paths):
        path = repo_root / path_text
        assert path.is_file(), path_text
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_marked = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
            and (
                ast.unparse(node.value) == "pytest.mark.host"
                or (
                    isinstance(node.value, (ast.List, ast.Tuple, ast.Set))
                    and any(
                        ast.unparse(element) == "pytest.mark.host"
                        for element in node.value.elts
                    )
                )
            )
            for node in tree.body
        )
        function_marked = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                ast.unparse(decorator) == "pytest.mark.host"
                for decorator in node.decorator_list
            )
            for node in ast.walk(tree)
        )
        assert module_marked or function_marked, (
            f"{path_text}: missing pytest.mark.host"
        )


def test_open_issues_declare_executable_reproduction_paths() -> None:
    """Every open Issue must retain a concrete test or script entrypoint."""

    repo_root = Path(__file__).resolve().parents[2]
    issue_text = (
        repo_root / "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"
    ).read_text(encoding="utf-8")
    missing: list[str] = []
    for match in re.finditer(
        r"^## (ZF-TEST-\d+)\b.*?\n(.*?)(?=^## |\Z)",
        issue_text,
        re.MULTILINE | re.DOTALL,
    ):
        issue_id, body = match.group(1), match.group(2)
        status = next(
            (
                line.strip()
                for line in body.splitlines()
                if line.strip().startswith("> 状态:")
            ),
            "",
        )
        if not re.match(r"> 状态:\s*open(?:（|\s|$)", status):
            continue
        references = re.findall(
            r"((?:tests|scripts)/[A-Za-z0-9_./-]+(?:\.py|\.sh)(?:::[A-Za-z0-9_]+)?)",
            body,
        )
        existing = {
            reference.split("::", 1)[0]
            for reference in references
            if (repo_root / reference.split("::", 1)[0]).is_file()
        }
        if not existing:
            missing.append(issue_id)
    assert not missing, (
        "open Issues without executable reproduction paths: " + ", ".join(missing)
    )


def test_registered_xfails_are_strict_and_mapped_to_each_executing_stage() -> None:
    """A detector shared by stages must be visible in every stage's issue list."""
    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    stages = {stage["id"]: stage for stage in matrix["stages"]}
    violations: list[str] = []
    for stage_id, paths in SUITES.items():
        if stage_id == "S0":
            continue
        declared = set(stages[stage_id].get("known_issues", []))
        for path_text in sorted(
            set(paths) | set(stages[stage_id].get("host_tests", []))
        ):
            tree = ast.parse((repo_root / _test_source(path_text)).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if ast.unparse(node.func) != "pytest.mark.xfail":
                    continue
                keywords = {item.arg: item.value for item in node.keywords}
                location = f"{stage_id}:{path_text}:{node.lineno}"
                strict = keywords.get("strict")
                if not isinstance(strict, ast.Constant) or strict.value is not True:
                    violations.append(f"{location}: requires explicit strict=True")
                reason = keywords.get("reason")
                reason_text = reason.value if isinstance(reason, ast.Constant) else ""
                ids = set(re.findall(r"\bZF-TEST-\d+\b", str(reason_text)))
                if not ids:
                    violations.append(f"{location}: requires a literal Issue reason")
                for issue_id in sorted(ids - declared):
                    violations.append(
                        f"{location}: {issue_id} missing from known_issues"
                    )
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "damage", [None, "unknown_issue", "closed_issue", "non_strict", "unmapped"]
)
def test_host_issue_audit_rejects_corrupt_metadata(monkeypatch, damage: str | None) -> None:
    """Exercise host-only detector admission without executing provider tests."""
    root = Path(__file__).resolve().parents[2]
    base = root / "awesome-zaofu/New_function/Test ZaoFu_Project"
    matrix_path = base / "coverage_matrix.json"
    issue_path = base / "ZaoFu_issue.md"
    host_path = root / "tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py"
    source = host_path.read_text(encoding="utf-8")
    issues = issue_path.read_text(encoding="utf-8")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    # AST-only Given: independent of whether a real production Issue was fixed.
    source += '\n@pytest.mark.xfail(strict=True, reason="ZF-TEST-999998: audit fixture")\ndef test_audit_fixture(): pass\n'
    issues += '\n## ZF-TEST-999998：audit fixture\n\n> 状态: open\n'
    stage = next(s for s in matrix["stages"] if s["id"] == "S5")
    stage["known_issues"].append("ZF-TEST-999998")
    replacements = {host_path: source, issue_path: issues, matrix_path: json.dumps(matrix)}
    original_read = Path.read_text

    def read_with_damage(path, *args, **kwargs):
        return replacements[path] if path in replacements else original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_with_damage)
    checks = (test_strict_testkit_xfails_are_traceable_to_registered_issues,
              test_strict_testkit_xfails_reference_open_issue_status,
              test_registered_xfails_are_strict_and_mapped_to_each_executing_stage)
    for check in checks:
        check()
    if damage is None:
        return
    if damage == "unknown_issue":
        target, replacement = host_path, source.replace("ZF-TEST-999998", "ZF-TEST-999999")
        check = test_strict_testkit_xfails_are_traceable_to_registered_issues
    elif damage == "closed_issue":
        target = issue_path
        replacement, count = re.subn(
            r"(^## ZF-TEST-999998[^\n]*\n\n> 状态:) open",
            r"\1 done",
            issues,
            count=1,
            flags=re.MULTILINE,
        )
        assert count == 1
        check = test_strict_testkit_xfails_reference_open_issue_status
    elif damage == "non_strict":
        target, replacement = host_path, source.replace("strict=True", "strict=False")
        check = test_registered_xfails_are_strict_and_mapped_to_each_executing_stage
    else:
        target = matrix_path
        stage["known_issues"].remove("ZF-TEST-999998")
        replacement = json.dumps(matrix)
        check = test_registered_xfails_are_strict_and_mapped_to_each_executing_stage
    replacements[target] = replacement
    with pytest.raises(AssertionError):
        check()


@pytest.mark.parametrize("selector", [
    "TestClaudeRespawn::test_respawn_finds_transcript_in_actual_spawn_cwd",
    "TestClaudeRespawn::test_missing_probe", "MissingClass::test_probe",
])
def test_registry_node_selector_is_checked_not_treated_as_a_filename(selector):
    root = Path(__file__).resolve().parents[2]
    entry = "tests/test_spawn_coordinator.py::" + selector
    if "missing" in selector.lower():
        with pytest.raises(AssertionError, match="missing test selector"):
            _assert_test_entry(root, entry)
    else:
        _assert_test_entry(root, entry)


def test_p0_case_registry_points_to_real_test_nodes() -> None:
    """Every P0 invariant must have an executable, reviewable test node."""
    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    cases = matrix.get("p0_cases")
    assert isinstance(cases, list) and len(cases) == 12
    ids = [case.get("id") for case in cases]
    assert len(ids) == len(set(ids))
    assert set(ids) == {
        "config-invalid-rejected",
        "state-dir-respected-by-all-entrances",
        "event-append-schema-and-replay",
        "duplicate-event-id-is-detected",
        "duplicate-wakeup-does-not-redispatch",
        "stale-attempt-cannot-overwrite-current",
        "missing-evidence-blocks-terminal",
        "fanin-waits-for-all-children",
        "rework-retry-is-bounded",
        "restart-resumes-without-duplicate-side-effect",
        "unauthorized-controlled-action-is-rejected",
        "truth-chain-store-api-sse-dom-converges",
    }
    for case in cases:
        assert set(case) == {"id", "tier", "test"}
        path_text, separator, function_name = str(case["test"]).partition("::")
        assert separator and function_name.startswith("test_")
        path = repo_root / path_text
        assert path.is_file(), path
        source = path.read_text(encoding="utf-8")
        assert re.search(
            rf"(?m)^\s*def {re.escape(function_name)}\s*\(",
            source,
        ), case["test"]


def test_p0_case_paths_are_registered_for_execution_or_explicit_host_tier() -> None:
    """P0 declarations must be runnable, not merely documented references."""
    repo_root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (
            repo_root
            / "awesome-zaofu/New_function/Test ZaoFu_Project/coverage_matrix.json"
        ).read_text(encoding="utf-8")
    )
    registered_paths = {
        _test_source(path) for paths in SUITES.values() for path in paths
        if _test_source(path).endswith(".py")
    }
    missing: list[str] = []
    for case in matrix["p0_cases"]:
        path = str(case["test"]).split("::", 1)[0]
        if (
            path not in registered_paths
            and "host" not in str(case.get("tier", "")).lower()
        ):
            missing.append(str(case["test"]))
    assert not missing, "P0 cases absent from an executable registry: " + ", ".join(
        missing
    )
