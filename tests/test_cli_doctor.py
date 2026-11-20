"""Tests for zf doctor runtime diagnostics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.cli import doctor
from zf.cli.main import main
from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_text


def _write_project(path: Path) -> None:
    (path / ".zf").mkdir()
    (path / ".zf" / "events.jsonl").write_text("")
    (path / ".zf" / "kanban.json").write_text("[]\n")
    (path / "zf.yaml").write_text(
        "project:\n"
        "  name: t\n"
        "  state_dir: .zf\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
    )


def test_doctor_reports_clean_runtime(tmp_path: Path, monkeypatch, capsys):
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = main(["doctor"])

    assert result == 0
    out = capsys.readouterr().out
    assert "ZF Doctor" in out
    assert "OK: runtime diagnostics clean" in out


def test_doctor_flags_malformed_event(tmp_path: Path, monkeypatch, capsys):
    _write_project(tmp_path)
    (tmp_path / ".zf" / "events.jsonl").write_text('{"payload":[]}\n')
    monkeypatch.chdir(tmp_path)

    result = main(["doctor"])

    assert result == 1
    out = capsys.readouterr().out
    assert "event.malformed" in out


def test_doctor_provider_reports_missing_codex(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    result = main(["doctor", "provider", "--backend", "codex"])

    assert result == 1
    out = capsys.readouterr().out
    assert "ZF Provider Doctor" in out
    assert "codex command not found" in out


def test_doctor_provider_flags_unsupported_network_namespace(monkeypatch, capsys):
    def fake_which(name: str) -> str | None:
        return {
            "codex": "/usr/bin/codex",
            "unshare": "/usr/bin/unshare",
        }.get(name)

    def fake_run(argv, **kwargs):
        if argv[:2] == ["/usr/bin/codex", "--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="codex 1.2.3\n", stderr="")
        if argv == ["/usr/bin/codex", "sandbox", "--help"]:
            return subprocess.CompletedProcess(argv, 0, stdout="--permission-profile --include-managed-config", stderr="")
        if argv[:2] == ["/usr/bin/codex", "sandbox"]:
            return subprocess.CompletedProcess(
                argv,
                1,
                stdout="",
                stderr="bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n",
            )
        raise AssertionError(argv)

    monkeypatch.setattr(doctor.shutil, "which", fake_which)
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    result = main(["doctor", "provider", "--backend", "codex"])

    assert result == 1
    out = capsys.readouterr().out
    assert "codex_version: codex 1.2.3" in out
    assert "sandbox: unsupported" in out
    assert "restricted sandbox preflight failed" in out


def test_doctor_uses_actual_sandbox_even_when_unshare_would_fail(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(argv, **kwargs):
        assert "unshare" not in argv[0]
        output = "--permission-profile --include-managed-config" if argv[-1] == "--help" else "codex 0.164.0"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
    monkeypatch.setattr(doctor.subprocess, "run", run)
    assert main(["doctor", "provider", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sandbox"]["status"] == "ok"
    assert report["sandbox"]["qualification"] == "command_startup_only"
    assert report["sandbox"]["probe"]["command"][-1] == "/usr/bin/true"


def test_doctor_unknown_cli_cannot_report_success(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(doctor.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 0, stdout="unrecognized interface", stderr=""))
    assert main(["doctor", "provider", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["sandbox"]["status"] == "unknown" and not report["ok"]


def test_doctor_sidecar_json_reports_clean_refs(tmp_path: Path, monkeypatch, capsys):
    _write_project(tmp_path)
    descriptor = write_sidecar_text(
        tmp_path / ".zf",
        "diagnostics/run-1/report.txt",
        "ok",
        kind="diagnostic_trace",
        schema_version="diagnostic.v1",
        created_by="test",
    )
    event = ZfEvent(type="diagnostic.ready", payload={"refs": {"diagnostic": descriptor}})
    with (tmp_path / ".zf" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(event.to_json() + "\n")
    monkeypatch.chdir(tmp_path)

    result = main(["doctor", "sidecar", "--json"])

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["checked_ref_count"] == 1


def test_doctor_sidecar_flags_missing_ref(tmp_path: Path, monkeypatch, capsys):
    _write_project(tmp_path)
    descriptor = {
        "kind": "diagnostic_trace",
        "ref": "diagnostics/run-1/missing.txt",
        "sha256": "abc",
        "byte_count": 3,
        "content_type": "text/plain",
        "schema_version": "diagnostic.v1",
    }
    event = ZfEvent(type="diagnostic.ready", payload={"refs": {"diagnostic": descriptor}})
    (tmp_path / ".zf" / "events.jsonl").write_text(event.to_json() + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = main(["doctor", "sidecar", "--json"])

    assert result == 1
    report = json.loads(capsys.readouterr().out)
    assert report["issues"][0]["code"] == "ref_missing"
