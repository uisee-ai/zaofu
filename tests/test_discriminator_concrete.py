"""Tests for G-DISC-2 + G-DISC-3: ContractD and FunctionalD concrete D classes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from zf.core.config.schema import QualityGateConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.verification.discriminator import (
    ArchitectureRulesD,
    ContractQualityD,
    ContractD,
    FunctionalD,
    PromotedRulesD,
)
from zf.core.verification.promoted_rules import PromotedRulesStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def event_log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl")


def _write_python_import_marker(root: Path, value: str) -> Path:
    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "zf_env_marker.py").write_text(
        f"VALUE = {value!r}\n",
        encoding="utf-8",
    )
    return src_dir


# ---- ContractD ----


class TestContractDEmpty:
    def test_empty_contract_passes_for_backward_compat(self, workspace, event_log):
        task = Task(id="T1", title="x")  # no contract
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is True
        assert result.evidence.get("contract_empty") is True

    def test_empty_contract_fails_when_required(self, workspace, event_log):
        task = Task(id="T1", title="x")
        d = ContractD(require_contract=True)
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is False
        assert result.evidence.get("require_contract") is True
        assert "required" in result.reason


class TestContractDFullyFilled:
    def test_passes_when_command_returns_zero(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="login works",
                verification="true",  # always succeeds
            ),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is True
        assert result.evidence["verification_returncode"] == 0

    def test_fails_when_command_returns_nonzero(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="login works",
                verification="false",  # always fails
            ),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is False
        assert result.evidence["verification_returncode"] != 0
        assert "failed" in result.reason.lower()

    def test_nonzero_command_passes_with_matching_expected_red_gate_evidence(
        self, workspace, event_log,
    ):
        command = "false"
        task = Task(
            id="T1", title="red tests",
            contract=TaskContract(
                behavior="RED tests fail before implementation",
                verification=command,
                verification_tiers=["runtime", "manual_evidence"],
            ),
        )
        event_log.append(ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload={
                "checks": [
                    {
                        "command": command,
                        "exit_code": 1,
                        "status": "RED_expected",
                    }
                ]
            },
        ))

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_returncode"] == 1
        assert result.evidence["verification_passed"] is False
        assert result.evidence["verification_expected_red"] is True
        assert (
            result.evidence["expected_red_evidence"]["source_event_type"]
            == "judge.passed"
        )

    def test_nonzero_command_still_fails_without_matching_expected_red_command(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1", title="red tests",
            contract=TaskContract(
                behavior="RED tests fail before implementation",
                verification="false",
            ),
        )
        event_log.append(ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload={
                "checks": [
                    {
                        "command": "different-command",
                        "exit_code": 1,
                        "status": "RED_expected",
                    }
                ]
            },
        ))

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is False
        assert result.evidence["verification_expected_red"] is False

    def test_declared_expected_red_requires_matching_terminal_evidence(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1",
            title="red validation",
            contract=TaskContract(
                behavior="RED command is intentionally failing",
                validation={
                    "kind": "command",
                    "command": "false",
                    "expected_result": "red",
                },
                verification_tiers=["runtime", "manual_evidence"],
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is False
        assert result.evidence["verification_expected_red_declared"] is True
        assert result.evidence["verification_expected_red"] is False
        assert "expected-red verification lacks matching terminal evidence" in result.reason

    def test_includes_stdout_in_evidence(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="echo hello",
            ),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert "hello" in result.evidence["verification_stdout_tail"]

    def test_unknown_verification_tier_fails(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="true",
                verification_tiers=["e2e", "mystery"],
            ),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is False
        assert "mystery" in result.evidence["unknown_tiers"]

    def test_verification_command_can_resolve_nvm_pnpm(
        self, workspace, event_log, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        pnpm = tmp_path / ".nvm" / "versions" / "node" / "v99.0.0" / "bin" / "pnpm"
        pnpm.parent.mkdir(parents=True)
        pnpm.write_text("#!/bin/sh\nprintf nvm-pnpm\n", encoding="utf-8")
        pnpm.chmod(0o755)
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="pnpm",
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_stdout_tail"] == "nvm-pnpm"

    def test_verification_command_can_resolve_codex_vendor_rg(
        self, workspace, event_log, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        rg = (
            tmp_path
            / ".nvm"
            / "versions"
            / "node"
            / "v99.0.0"
            / "lib"
            / "node_modules"
            / "@openai"
            / "codex"
            / "node_modules"
            / "@openai"
            / "codex-linux-x64"
            / "vendor"
            / "x86_64-unknown-linux-musl"
            / "path"
            / "rg"
        )
        rg.parent.mkdir(parents=True)
        rg.write_text("#!/bin/sh\ngrep \"$1\" \"$2\"\n", encoding="utf-8")
        rg.chmod(0o755)
        (workspace / "proof.txt").write_text("reference_signals\n")
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="rg reference_signals proof.txt",
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert "reference_signals" in result.evidence["verification_stdout_tail"]

    def test_verification_command_prefers_workspace_src_over_inherited_pythonpath(
        self, workspace, event_log, monkeypatch,
    ):
        inherited_src = _write_python_import_marker(
            workspace / "inherited",
            "inherited",
        )
        _write_python_import_marker(workspace, "workspace")
        monkeypatch.setenv("PYTHONPATH", str(inherited_src))
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification=(
                    "python -c 'import sys, zf_env_marker; "
                    "sys.stdout.write(zf_env_marker.VALUE)'"
                ),
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_stdout_tail"] == "workspace"

    def test_verification_command_drops_foreign_virtualenv(
        self, workspace, event_log, monkeypatch,
    ):
        foreign_venv = workspace / "foreign" / ".venv"
        foreign_venv.mkdir(parents=True)
        monkeypatch.setenv("VIRTUAL_ENV", str(foreign_venv))
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification=(
                    "python -c 'import os, sys; "
                    "sys.stdout.write(os.environ.get(\"VIRTUAL_ENV\", \"\"))'"
                ),
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_stdout_tail"] == ""

    def test_verification_command_links_web_node_modules_from_project_root(
        self, workspace, event_log, tmp_path, monkeypatch,
    ):
        root = tmp_path / "repo"
        source_bin = root / "web" / "node_modules" / ".bin"
        source_bin.mkdir(parents=True)
        tsc = source_bin / "tsc"
        tsc.write_text("#!/bin/sh\nprintf linked-tsc\n", encoding="utf-8")
        tsc.chmod(0o755)
        monkeypatch.setenv("ZF_PROJECT_ROOT", str(root))

        web_dir = workspace / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}\n", encoding="utf-8")
        (web_dir / "package-lock.json").write_text("{}\n", encoding="utf-8")
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="web/node_modules/.bin/tsc",
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["web_dependencies"]["status"] == "linked"
        assert (web_dir / "node_modules").is_symlink()
        assert result.evidence["verification_stdout_tail"] == "linked-tsc"

    def test_verification_command_prepares_web_python_extra(
        self, workspace, event_log, tmp_path, monkeypatch,
    ):
        uv_log = tmp_path / "uv.log"
        uv = tmp_path / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$UV_LOG\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)
        monkeypatch.setenv("UV_LOG", str(uv_log))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
        (workspace / "pyproject.toml").write_text(
            "[project.optional-dependencies]\nweb = []\n",
            encoding="utf-8",
        )
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="echo tests/e2e/full_stack_validation.py",
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["web_python_dependencies"]["status"] == "ready"
        assert "run --extra web python -c" in uv_log.read_text(encoding="utf-8")

    def test_mixed_verification_prose_tail_uses_runtime_gate_evidence(
        self, workspace, event_log,
    ):
        command = "true && run a scope-local runtime probe for artifacts"
        task = Task(
            id="T1",
            title="runtime artifacts",
            contract=TaskContract(
                behavior="Artifact builders compile and emit runtime evidence",
                verification=command,
                verification_tiers=["static", "runtime"],
            ),
        )
        event_log.append(ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload={
                "checks": [
                    {
                        "command": (
                            "scope-local compiled Node runtime probe for "
                            "artifacts"
                        ),
                        "exit_code": 0,
                        "tier": "runtime",
                        "summary": "Probe emitted the required artifacts.",
                    }
                ]
            },
        ))

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_returncode"] == 0
        assert result.evidence["verification_shell_command"] == "true"
        assert result.evidence["verification_prose_tail"].startswith("run ")
        assert (
            result.evidence["verification_prose_tail_evidence"]["source_event_type"]
            == "judge.passed"
        )

    def test_fullwidth_semicolon_prose_tail_does_not_enter_shell(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1",
            title="manual docs evidence",
            contract=TaskContract(
                behavior="Document the smoke observation",
                verification=(
                    "printf ok；并用 scoped grep/status 类命令确认目标文档包含观测记录"
                ),
                verification_tiers=["manual_evidence"],
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["verification_shell_command"] == "printf ok"
        assert result.evidence["verification_stdout_tail"] == "ok"
        assert result.evidence["verification_prose_tail"].startswith("并用")
        assert result.evidence["verification_prose_tail_requires_evidence"] is False

    def test_mixed_verification_prose_tail_fails_without_runtime_gate_evidence(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1",
            title="runtime artifacts",
            contract=TaskContract(
                behavior="Artifact builders compile and emit runtime evidence",
                verification="true && run a scope-local runtime probe for artifacts",
                verification_tiers=["static", "runtime"],
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is False
        assert result.evidence["verification_returncode"] == 0
        assert result.evidence["verification_prose_tail_evidence"] == {}
        assert "prose" in result.reason

    def test_byte_exact_validation_rejects_trailing_newline(
        self, workspace, event_log,
    ):
        (workspace / "proof.txt").write_bytes(b"autoresearch-proof\n")
        task = Task(
            id="T1",
            title="byte exact proof",
            contract=TaskContract(
                behavior="Create byte-exact proof content.",
                verification='[ "$(cat proof.txt)" = "autoresearch-proof" ]',
                validation={
                    "kind": "byte_exact",
                    "path": "proof.txt",
                    "expected": "autoresearch-proof",
                },
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is False
        assert result.evidence["validation_kind"] == "byte_exact"
        assert result.evidence["validation"]["actual_bytes"] == (
            "b'autoresearch-proof\\n'"
        )
        assert "expected bytes" in result.reason

    def test_text_line_exact_validation_accepts_one_final_newline(
        self, workspace, event_log,
    ):
        (workspace / "proof.txt").write_text("autoresearch-proof\n")
        task = Task(
            id="T1",
            title="line exact proof",
            contract=TaskContract(
                behavior="Create a one-line proof.",
                validation={
                    "kind": "text_line_exact",
                    "path": "proof.txt",
                    "expected": "autoresearch-proof",
                },
            ),
        )

        result = ContractD().evaluate(task, workspace, event_log)

        assert result.passed is True
        assert result.evidence["validation_kind"] == "text_line_exact"
        assert result.evidence["verification_passed"] is None


class TestContractDPartiallyFilled:
    def test_behavior_set_but_verification_empty_fails(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(behavior="login works"),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is False
        assert "verification" in result.reason.lower()

    def test_verification_set_but_behavior_empty_fails(self, workspace, event_log):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(verification="true"),
        )
        d = ContractD()
        result = d.evaluate(task, workspace, event_log)
        assert result.passed is False
        assert "behavior" in result.reason.lower()


class TestContractQualityD:
    def test_generic_contract_fails(self, workspace, event_log):
        task = Task(
            id="T1",
            title="x",
            contract=TaskContract(
                behavior="fix bug",
                verification="true",
                scope=[],
                acceptance="exit_code=0",
            ),
        )
        result = ContractQualityD().evaluate(task, workspace, event_log)
        assert result.passed is False
        assert "generic" in result.reason

    def test_specific_contract_passes(self, workspace, event_log):
        task = Task(
            id="T1",
            title="x",
            contract=TaskContract(
                behavior="JWT refresh endpoint returns a rotated access token",
                verification="python3 -m pytest tests/test_auth.py -q",
                scope=["src/auth.py", "tests/test_auth.py"],
                acceptance=(
                    "Given a valid refresh token, the API returns a rotated "
                    "access token and the focused pytest case passes"
                ),
            ),
        )
        result = ContractQualityD().evaluate(task, workspace, event_log)
        assert result.passed is True


# ---- FunctionalD ----


class TestFunctionalDNoGates:
    def test_no_gates_configured_passes(self, workspace, event_log):
        d = FunctionalD(quality_gates={})
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        assert result.passed is True


class TestFunctionalDPassingGates:
    def test_all_gates_pass(self, workspace, event_log):
        gates = {
            "lint": QualityGateConfig(enabled=True, required_checks=["true"]),
            "test": QualityGateConfig(enabled=True, required_checks=["true"]),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        assert result.passed is True
        assert "lint" in result.evidence["gates_passed"]
        assert "test" in result.evidence["gates_passed"]

    def test_gate_command_evidence_records_output_and_exit_code(
        self, workspace, event_log,
    ):
        gates = {
            "lint": QualityGateConfig(
                enabled=True,
                required_checks=["printf gate-ok"],
            ),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        check = result.evidence["gate_checks"]["lint"][0]
        assert result.passed is True
        assert check["command"] == "printf gate-ok"
        assert check["exit_code"] == 0
        assert check["passed"] is True
        assert check["stdout_tail"] == "gate-ok"

    def test_gate_command_can_resolve_nvm_pnpm(
        self, workspace, event_log, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        pnpm = tmp_path / ".nvm" / "versions" / "node" / "v99.0.0" / "bin" / "pnpm"
        pnpm.parent.mkdir(parents=True)
        pnpm.write_text("#!/bin/sh\nprintf gate-pnpm\n", encoding="utf-8")
        pnpm.chmod(0o755)
        gates = {
            "static": QualityGateConfig(enabled=True, required_checks=["pnpm"]),
        }

        result = FunctionalD(quality_gates=gates).evaluate(
            Task(id="T1", title="x"), workspace, event_log,
        )

        check = result.evidence["gate_checks"]["static"][0]
        assert result.passed is True
        assert check["stdout_tail"] == "gate-pnpm"

    def test_gate_command_prefers_workspace_src_over_inherited_pythonpath(
        self, workspace, event_log, monkeypatch,
    ):
        inherited_src = _write_python_import_marker(
            workspace / "inherited",
            "inherited",
        )
        _write_python_import_marker(workspace, "workspace")
        monkeypatch.setenv("PYTHONPATH", str(inherited_src))
        gates = {
            "static": QualityGateConfig(
                enabled=True,
                required_checks=[
                    "python -c 'import sys, zf_env_marker; "
                    "sys.stdout.write(zf_env_marker.VALUE)'",
                ],
            ),
        }

        result = FunctionalD(quality_gates=gates).evaluate(
            Task(id="T1", title="x"), workspace, event_log,
        )

        check = result.evidence["gate_checks"]["static"][0]
        assert result.passed is True
        assert check["stdout_tail"] == "workspace"

    def test_gate_command_drops_foreign_virtualenv(
        self, workspace, event_log, monkeypatch,
    ):
        foreign_venv = workspace / "foreign" / ".venv"
        foreign_venv.mkdir(parents=True)
        monkeypatch.setenv("VIRTUAL_ENV", str(foreign_venv))
        gates = {
            "static": QualityGateConfig(
                enabled=True,
                required_checks=[
                    "python -c 'import os, sys; "
                    "sys.stdout.write(os.environ.get(\"VIRTUAL_ENV\", \"\"))'",
                ],
            ),
        }

        result = FunctionalD(quality_gates=gates).evaluate(
            Task(id="T1", title="x"), workspace, event_log,
        )

        check = result.evidence["gate_checks"]["static"][0]
        assert result.passed is True
        assert check["stdout_tail"] == ""


class TestFunctionalDFailingGates:
    def test_one_failing_gate_blocks(self, workspace, event_log):
        gates = {
            "lint": QualityGateConfig(enabled=True, required_checks=["true"]),
            "test": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        assert result.passed is False
        assert "lint" in result.evidence["gates_passed"]
        assert "test" in result.evidence["gates_failed"]
        assert "test" in result.reason

    def test_failed_gate_command_evidence_records_nonzero_exit(
        self, workspace, event_log,
    ):
        gates = {
            "test": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        check = result.evidence["gate_checks"]["test"][0]
        assert result.passed is False
        assert check["command"] == "false"
        assert check["exit_code"] != 0
        assert check["passed"] is False


class TestFunctionalDDisabledGates:
    def test_disabled_gate_skipped(self, workspace, event_log):
        gates = {
            "lint": QualityGateConfig(enabled=False, required_checks=["false"]),
            "test": QualityGateConfig(enabled=True, required_checks=["true"]),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        assert result.passed is True
        assert "lint" not in result.evidence["gates_run"]
        assert "test" in result.evidence["gates_run"]


class TestFunctionalDFocusedTiers:
    def test_task_verification_tiers_skip_unrelated_global_gates(
        self, workspace, event_log,
    ):
        gates = {
            "static": QualityGateConfig(enabled=True, required_checks=["false"]),
            "test": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        task = Task(
            id="T1",
            title="focused runtime task",
            contract=TaskContract(
                behavior="focused runtime verification",
                verification="true",
                verification_tiers=["runtime", "manual_evidence"],
            ),
        )

        result = FunctionalD(quality_gates=gates).evaluate(
            task, workspace, event_log,
        )

        assert result.passed is True
        assert result.evidence["gates_run"] == []
        assert set(result.evidence["gates_skipped_by_tier"]) == {
            "static",
            "test",
        }

    def test_matching_tier_gate_still_runs(self, workspace, event_log):
        gates = {
            "static": QualityGateConfig(enabled=True, required_checks=["false"]),
            "e2e": QualityGateConfig(enabled=True, required_checks=["true"]),
        }
        task = Task(
            id="T1",
            title="e2e task",
            contract=TaskContract(
                behavior="e2e verification",
                verification="true",
                verification_tiers=["e2e"],
            ),
        )

        result = FunctionalD(quality_gates=gates).evaluate(
            task, workspace, event_log,
        )

        assert result.passed is True
        assert result.evidence["gates_run"] == ["e2e"]
        assert result.evidence["gates_skipped_by_tier"] == ["static"]

    def test_docs_manual_evidence_profile_skips_global_code_gates(
        self, workspace, event_log,
    ):
        gates = {
            "static": QualityGateConfig(enabled=True, required_checks=["false"]),
            "test": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        task = Task(
            id="T1",
            title="docs-only smoke",
            contract=TaskContract(
                behavior="Record a docs-only smoke observation",
                verification="git diff --check",
                verification_tiers=["static", "manual_evidence"],
                scope=["docs/records/smoke.md"],
            ),
        )

        result = FunctionalD(quality_gates=gates).evaluate(
            task, workspace, event_log,
        )

        assert result.passed is True
        assert result.evidence["gates_run"] == []
        assert set(result.evidence["gates_skipped_by_profile"]) == {
            "static",
            "test",
        }
        assert result.evidence["gates_skipped_by_tier"] == []


class TestFunctionalDEvidence:
    def test_evidence_lists_run_passed_failed(self, workspace, event_log):
        gates = {
            "lint": QualityGateConfig(enabled=True, required_checks=["true"]),
            "test": QualityGateConfig(enabled=True, required_checks=["false"]),
        }
        d = FunctionalD(quality_gates=gates)
        result = d.evaluate(Task(id="T1", title="x"), workspace, event_log)
        ev = result.evidence
        assert set(ev["gates_run"]) == {"lint", "test"}
        assert ev["gates_passed"] == ["lint"]
        assert ev["gates_failed"] == ["test"]


class TestRuntimeRuleDiscriminators:
    def test_architecture_rules_pass_when_file_absent(self, workspace, event_log):
        result = ArchitectureRulesD().evaluate(Task(id="T1"), workspace, event_log)
        assert result.passed is True
        assert result.evidence["rules_run"] == []

    def test_architecture_rules_run_and_fail(self, workspace, event_log):
        (workspace / "ARCHITECTURE_RULES.md").write_text(
            '## Rule: no-bad\n- check: `test ! -f bad.txt`\n'
            '- fix: "remove bad.txt"\n- why: "bad is forbidden"\n'
        )
        (workspace / "bad.txt").write_text("bad")

        result = ArchitectureRulesD().evaluate(Task(id="T1"), workspace, event_log)

        assert result.passed is False
        assert result.evidence["rules_failed"] == ["no-bad"]
        assert result.evidence["checks"][0]["command"] == "test ! -f bad.txt"

    def test_promoted_rules_run_and_fail(self, workspace, event_log):
        store = PromotedRulesStore(event_log.path.parent / "promoted_rules.jsonl")
        store.add("no-bad", "test ! -f bad.txt", "remove bad.txt")
        (workspace / "bad.txt").write_text("bad")

        result = PromotedRulesD().evaluate(Task(id="T1"), workspace, event_log)

        assert result.passed is False
        assert result.evidence["rules_failed"] == ["no-bad"]


# ---------------------------------------------------------------------------
# P2 #3 (backlog 2026-05-14): ContractD verification syntax pre-check.
#
# r4 hit a contract with verification ending in an unbalanced `)`. /bin/sh
# returned exit 2 with "Syntax error: ")" unexpected", which discriminator
# treated as a worker fault → triggered a useless dev rework round. The
# pre-check (`sh -n`) catches the bug as a CONTRACT problem with a clear
# tag in evidence so rework_triage can route to arch instead of dev.
# ---------------------------------------------------------------------------


class TestContractDSyntaxPreCheck:
    def test_unbalanced_paren_fails_with_contract_invalid_marker(
        self, workspace, event_log,
    ):
        """The exact r4 pitfall: trailing `)` in verification."""
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="echo ok || echo also-ok )",  # trailing ) → sh syntax error
            ),
        )
        result = ContractD().evaluate(task, workspace, event_log)
        assert result.passed is False
        assert result.evidence.get("contract_syntax_invalid") is True
        assert result.evidence["verification_syntax_check"]["exit_code"] != 0
        # The pre-check must catch it WITHOUT executing the real command
        assert "verification_returncode" not in result.evidence
        # Reason mentions syntax explicitly so rework_triage can route on it
        assert "syntax error" in result.reason.lower()

    def test_unterminated_quote_fails_with_contract_invalid_marker(
        self, workspace, event_log,
    ):
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification='echo "unclosed',
            ),
        )
        result = ContractD().evaluate(task, workspace, event_log)
        assert result.passed is False
        assert result.evidence.get("contract_syntax_invalid") is True

    def test_valid_command_passes_through_syntax_check(
        self, workspace, event_log,
    ):
        """Healthy commands still reach the real exec path."""
        task = Task(
            id="T1", title="x",
            contract=TaskContract(
                behavior="x",
                verification="true",
            ),
        )
        result = ContractD().evaluate(task, workspace, event_log)
        assert result.passed is True
        # Real exec happened (returncode present)
        assert "verification_returncode" in result.evidence

    def test_syntax_check_skipped_for_empty_command(self, workspace, event_log):
        from zf.core.verification.discriminator import _verification_syntax_check
        assert _verification_syntax_check("") is None
        assert _verification_syntax_check("   ") is None
        assert _verification_syntax_check(None or "") is None  # noqa: SIM222

    def test_syntax_check_detects_unbalanced_paren_directly(self):
        from zf.core.verification.discriminator import _verification_syntax_check
        result = _verification_syntax_check("echo ok )")
        assert result is not None
        assert result["passed"] is False
        assert ")" in result["stderr"] or "unexpected" in result["stderr"]
