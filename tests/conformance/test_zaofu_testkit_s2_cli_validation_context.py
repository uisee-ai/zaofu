"""CFG-08/CLI-02: validation identity must match its actual configuration root."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


class ValidationContextMismatch(AssertionError):
    """The validator writes a missing-CWD report despite a valid parent project."""


@pytest.mark.parametrize("location,explicit", [
    ("root", False), ("root", True), ("nested", True),
    pytest.param("nested", False, marks=pytest.mark.xfail(
        strict=True, raises=ValidationContextMismatch,
        reason="ZF-TEST-043: validate ignores ancestor config and writes a child state report",
    )),
])
@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_validate_reads_and_reports_the_same_project_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], location: str, explicit: bool, json_output: bool,
) -> None:
    root = tmp_path / "project"
    nested = root / "src" / "worker"
    nested.mkdir(parents=True)
    state = root / "custom-state"
    state.mkdir()
    config = root / "zf.yaml"
    config.write_text(
        "version: '1.0'\nproject:\n  name: validation-context\n"
        "  state_dir: custom-state\nroles: []\n", encoding="utf-8",
    )
    config_before = config.read_bytes()
    TaskStore(state / "kanban.json").add(Task(id="SEED", title="must survive validation"))
    before = {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}
    for name in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.chdir(root if location == "root" else nested)
    context = resolve_project_context(require_config=True)
    assert (context.project_root, context.state_dir, context.config_path) == (root, state, config)
    argv = ["validate", *(["--path", str(config)] if explicit else []),
            *(["--json"] if json_output else [])]
    observed: dict = {}
    evidence = ModelEvidence(
        root=tmp_path, model_name="cli-validation-context", source=Path(__file__),
        reference=lambda: {"expected_config": str(config), "expected_state": str(state),
                           "expected_exit": 0, "observed": observed},
        settings="fixed CLI case; no generated input or real provider",
    )
    with evidence.guard("validate", {"argv": argv, "cwd": str(Path.cwd())}):
        result = main(argv)
        output = capsys.readouterr()
        observed.update(exit_code=result, stdout=output.out, stderr=output.err)
        payload = json.loads(output.out) if json_output else None
        reports = {str(path.relative_to(root)): json.loads(path.read_text())
                   for path in root.rglob("validation-report.json")}
        observed.update(reports=reports, payload=payload)
        write_json_atomic(tmp_path / "cli-observation.json", observed)
        assert {path: path.read_bytes() for path in before} == before
        assert config.read_bytes() == config_before
        if payload is not None:
            assert payload["identity"]["project_root"] == str(root)
            assert payload["identity"]["state_dir"] == str(state)
            assert payload["ok"] is (result == 0)

        wrong_report = reports.get("src/worker/.zf/config/validation-report.json", {})
        if (location == "nested" and not explicit and result == 1
                and wrong_report.get("status") == "invalid"
                and wrong_report.get("config_path") == str(nested / "zf.yaml")
                and wrong_report.get("errors") == [f"Config file not found: {nested / 'zf.yaml'}"]
                and set(reports) == {"src/worker/.zf/config/validation-report.json"}):
            raise ValidationContextMismatch(
                "valid ancestor config ignored; missing child config report written to child .zf; "
                "JSON identity, when requested, names the different parent custom-state"
            )

        assert result == 0
        assert set(reports) == {"custom-state/config/validation-report.json"}
        report = reports["custom-state/config/validation-report.json"]
        assert report["status"] == "valid" and report["errors"] == []
        assert report["config_path"] == str(config)
        assert report["sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
        assert (state / "config/last-known-good.yaml").read_bytes() == config.read_bytes()
        assert not (root / ".zf").exists() and not (nested / ".zf").exists()
