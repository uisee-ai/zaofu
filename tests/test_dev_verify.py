"""Contracts for the worktree-local development verification planner."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev-verify.py"
SPEC = importlib.util.spec_from_file_location("zf_dev_verify", SCRIPT)
assert SPEC and SPEC.loader
dev_verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dev_verify
SPEC.loader.exec_module(dev_verify)


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "zf").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "zf" / "__init__.py").write_text("")
    (tmp_path / "src" / "zf" / "leaf.py").write_text("VALUE = 1\n")
    (tmp_path / "src" / "zf" / "caller.py").write_text(
        "from zf.leaf import VALUE\n",
    )
    (tmp_path / "src" / "zf" / "untested.py").write_text("VALUE = 2\n")
    (tmp_path / "tests" / "test_leaf.py").write_text(
        "from zf.leaf import VALUE\n\ndef test_leaf(): assert VALUE == 1\n",
    )
    (tmp_path / "tests" / "test_caller.py").write_text(
        "from zf.caller import VALUE\n\ndef test_caller(): assert VALUE == 1\n",
    )
    (tmp_path / "tests" / "support.py").write_text("VALUE = 3\n")
    (tmp_path / "tests" / "test_support_user.py").write_text(
        "from tests.support import VALUE\n"
        "\ndef test_support(): assert VALUE == 3\n",
    )
    return tmp_path


def test_leaf_change_selects_direct_test_and_direct_caller_test(
    mini_repo: Path,
) -> None:
    plan = dev_verify.build_plan(mini_repo, ["src/zf/leaf.py"])

    assert plan.errors == []
    assert plan.selected_tests == [
        "tests/test_caller.py",
        "tests/test_leaf.py",
    ]
    assert plan.steps[0].tier == "deterministic"


def test_unmapped_source_change_fails_closed(mini_repo: Path) -> None:
    plan = dev_verify.build_plan(mini_repo, ["src/zf/untested.py"])

    assert any("unmapped Python change" in error for error in plan.errors)


def test_test_support_change_selects_importing_test(mini_repo: Path) -> None:
    plan = dev_verify.build_plan(mini_repo, ["tests/support.py"])

    assert plan.errors == []
    assert plan.selected_tests == ["tests/test_support_user.py"]


def test_explicit_test_is_the_operator_escape_hatch(mini_repo: Path) -> None:
    plan = dev_verify.build_plan(
        mini_repo,
        ["src/zf/untested.py"],
        explicit_tests=["tests/test_leaf.py"],
    )

    assert plan.errors == []
    assert plan.selected_tests == ["tests/test_leaf.py"]


def test_docs_only_plan_uses_instruction_contract_without_backend_full() -> None:
    plan = dev_verify.build_plan(ROOT, ["AGENTS.md"])

    assert plan.errors == []
    assert plan.domains == ["docs"]
    assert plan.selected_tests == ["tests/test_instruction_stack_contracts.py"]
    assert plan.broad_python is False
    assert all("tests" not in step.command for step in plan.steps)


def test_web_only_plan_stays_out_of_backend_pytest() -> None:
    plan = dev_verify.build_plan(
        ROOT,
        ["web/src/components/channel/ChannelPage.tsx"],
    )

    assert plan.errors == []
    assert plan.selected_tests == []
    assert [step.id for step in plan.steps] == ["web-typecheck", "web-unit"]


def test_orchestrator_change_adds_contract_and_flow_smoke() -> None:
    plan = dev_verify.build_plan(
        ROOT,
        ["src/zf/runtime/orchestrator_lifecycle.py"],
        explicit_tests=["tests/test_orchestrator_lifecycle_handlers.py"],
        parallel=False,
    )

    ids = {step.id for step in plan.steps}
    assert {"python-deterministic", "premerge-sentinels", "flow-smoke"} <= ids


def test_provider_change_never_auto_runs_real_provider() -> None:
    plan = dev_verify.build_plan(
        ROOT,
        ["src/zf/runtime/backend.py"],
        explicit_tests=["tests/test_backend_adapters.py"],
        parallel=False,
    )

    provider = next(step for step in plan.steps if step.id == "real-provider")
    assert provider.tier == "real_provider"
    assert provider.automatic is False
    assert provider.command == ()


def test_shared_boundary_adds_premerge_sentinel() -> None:
    plan = dev_verify.build_plan(
        ROOT,
        ["src/zf/core/events/known_types.py"],
        explicit_tests=["tests/test_event_contracts.py"],
        parallel=False,
    )

    assert any(step.id == "premerge-sentinels" for step in plan.steps)


def test_missing_explicit_test_is_rejected(mini_repo: Path) -> None:
    plan = dev_verify.build_plan(
        mini_repo,
        ["src/zf/untested.py"],
        explicit_tests=["tests/test_missing.py"],
    )

    assert any("explicit test path does not exist" in error for error in plan.errors)


def test_changed_file_discovery_includes_commit_and_worktree_changes(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "tracked.py").write_text("old\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "tracked.py").write_text("new\n")
    (tmp_path / "new.py").write_text("new\n")

    assert dev_verify.discover_changed_files(tmp_path, base) == [
        "new.py",
        "tracked.py",
    ]


def test_changed_file_discovery_keeps_deleted_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "deleted.py").write_text("old\n")
    subprocess.run(["git", "add", "deleted.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / "deleted.py").unlink()

    assert dev_verify.discover_changed_files(tmp_path) == ["deleted.py"]


def test_plan_json_is_versioned() -> None:
    plan = dev_verify.build_plan(ROOT, ["AGENTS.md"])

    payload = plan.to_dict()
    assert payload["schema_version"] == "zf-dev-verification-plan.v1"
    assert payload["steps"][0]["command"]


def test_json_run_captures_step_output(capsys: pytest.CaptureFixture[str]) -> None:
    plan = dev_verify.VerificationPlan(
        root=str(ROOT),
        changed_files=["AGENTS.md"],
        domains=["docs"],
        selected_tests=[],
        steps=[
            dev_verify.VerificationStep(
                id="probe",
                tier="deterministic",
                command=(sys.executable, "-c", "print('receipt-probe')"),
            ),
        ],
    )

    code, receipt = dev_verify.run_plan(plan, capture_output=True)

    assert code == 0
    assert receipt["results"][0]["stdout"] == "receipt-probe\n"
    assert capsys.readouterr().out == ""
    json.dumps(receipt)


def test_skipped_tier_is_recorded_without_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    plan = dev_verify.VerificationPlan(
        root=str(ROOT),
        changed_files=["web/package.json"],
        domains=["web_ui"],
        selected_tests=[],
        steps=[
            dev_verify.VerificationStep(
                id="web-probe",
                tier="web",
                command=(
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                ),
            ),
        ],
    )

    code, receipt = dev_verify.run_plan(
        plan,
        capture_output=True,
        skip_tiers=frozenset({"web"}),
    )

    assert code == 0
    assert marker.exists() is False
    assert receipt["results"] == [
        {
            "id": "web-probe",
            "tier": "web",
            "status": "not_run",
            "reason": "excluded by --skip-tier",
        }
    ]


def test_runner_contracts_are_worktree_relative() -> None:
    premerge = (ROOT / "scripts" / "dev-premerge-gate.sh").read_text()
    smoke = (ROOT / "scripts" / "run-flow-smoke.sh").read_text()

    assert "/path/to/zaofu/.venv" not in premerge
    assert "--no-cov" in smoke


def test_ci_uses_current_controller_and_skips_provider_auth() -> None:
    manifest = ROOT / ".gitlab-ci.yml"
    if not manifest.is_file():
        pytest.skip("Optional GitLab CI manifest is absent from this checkout")
    pipeline = manifest.read_text()

    assert "examples/safe-team.yaml" not in pipeline
    assert "examples/prod/controller/prd-light-v3.yaml" in pipeline
    assert "--skip-provider-auth" in pipeline
    assert "scripts/dev-verify.py run" in pipeline
    assert "--skip-tier web" in pipeline


def test_registered_test_tiers_cover_non_deterministic_modes() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()

    for marker in ("host:", "mock_e2e:", "real_provider:", "serial:"):
        assert marker in pyproject
    assert "not host and not real_provider" in pyproject


@pytest.mark.parametrize("support", [
    "tests/e2e/intake_live_validation.py",
    "tests/e2e/intake_live_acceptance.py",
    "tests/e2e/fixtures/intake-live/issue/counter.py",
    "tests/e2e/fixtures/intake-live/semantic/auth.py",
    "tests/e2e/fixtures/intake-live/semantic/orders.py",
])
def test_dynamically_loaded_intake_support_selects_behavior_tests(mini_repo, support):
    path = mini_repo / support
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# loaded by file path, not Python import\n")
    test = mini_repo / "tests/test_intake_live_validation.py"
    test.write_text("def test_fixture_contract(): pass\n")
    plan = dev_verify.build_plan(mini_repo, [support])
    assert plan.errors == []
    assert "tests/test_intake_live_validation.py" in plan.selected_tests


def test_missing_dynamic_consumer_does_not_count_as_coverage(mini_repo):
    plan = dev_verify.build_plan(mini_repo, ["tests/e2e/intake_live_acceptance.py"])
    assert any("unmapped Python change" in error for error in plan.errors)


@pytest.mark.parametrize("fixture", [
    "tests/e2e/fixtures/intake-live/issue/test_counter.py",
    "tests/e2e/fixtures/intake-live/semantic/test_behavior.py",
])
def test_seeded_product_tests_select_their_oracle_consumer(mini_repo, fixture):
    path = mini_repo / fixture
    path.parent.mkdir(parents=True)
    path.write_text("def test_seed_is_intentionally_red(): assert False\n")
    consumer = mini_repo / "tests/test_intake_live_validation.py"
    consumer.write_text("def test_fixture_oracle(): pass\n")
    plan = dev_verify.build_plan(mini_repo, [fixture])
    assert plan.errors == []
    assert fixture not in plan.selected_tests
    assert "tests/test_intake_live_validation.py" in plan.selected_tests


def test_unknown_seed_test_is_unmapped_instead_of_executable(mini_repo):
    path = "tests/e2e/fixtures/new-product/test_bug.py"
    fixture = mini_repo / path
    fixture.parent.mkdir(parents=True)
    fixture.write_text("def test_seed_bug(): assert False\n")
    plan = dev_verify.build_plan(mini_repo, [path])
    assert path not in plan.selected_tests
    assert any("unmapped Python change" in error for error in plan.errors)
