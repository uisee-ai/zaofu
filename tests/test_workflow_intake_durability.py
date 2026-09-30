"""ZF-TEST-112: publish intake refs only after complete, synced file writes."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog


def _prepare(root: Path, *, json_output: bool = False) -> tuple[list[str], list[Path]]:
    (root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: durable\n  state_dir: runtime-state\n'
        "roles:\n  - name: dev\n    backend: mock\n",
        encoding="utf-8",
    )
    request_id = "durable-input"
    output = root / (
        "artifacts/intake/request.json" if json_output else "docs/intake/request.md"
    )
    workflow = root / "artifacts/workflow" / request_id
    paths = [
        output if json_output else root / "artifacts/intake" / f"{request_id}.json",
        output.with_suffix(".md"),
        workflow / "skill-adapter-plan.json",
        workflow / "workflow-input-manifest.json",
    ]
    argv = [
        "flow",
        "intake",
        "--kind",
        "workflow",
        "--request-id",
        request_id,
        "--objective",
        "恢复后仍可读取中文输入",
        "--output",
        str(output),
        "--json",
    ]
    return argv, paths


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_syncs_complete_inputs_before_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    argv, paths = _prepare(tmp_path, json_output=json_output)
    synced: dict[tuple[int, int], bytes] = {}
    created: list[str] = []
    real_sync, real_append = os.fsync, EventLog.append

    def sync(fd: int) -> None:
        stat = os.fstat(fd)
        content = Path(f"/proc/self/fd/{fd}").read_bytes()
        real_sync(fd)
        synced[(stat.st_dev, stat.st_ino)] = content

    def append(log: EventLog, event):
        if event.type == "workflow.intake.created":
            for path in paths:
                stat = path.stat()
                content = path.read_bytes()
                assert content, path
                assert synced.get((stat.st_dev, stat.st_ino)) == content, path
                if path.suffix == ".json":
                    assert json.loads(content)
            created.append(event.id)
        return real_append(log, event)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(EventLog, "append", append)
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(created) == 1
    assert Path(result["request_projection_ref"]).is_file()
    manifest = json.loads(paths[3].read_text())
    assert manifest["objective"] == "恢复后仍可读取中文输入"
    assert {str(path) for path in paths[:3]} <= set(manifest["artifact_refs"])
    assert [
        event.id
        for event in EventLog(tmp_path / "runtime-state/events.jsonl").read_all()
        if event.type == "workflow.intake.created"
    ] == created


@pytest.mark.parametrize(
    "index", range(4), ids=["json", "markdown", "skill", "manifest"]
)
@pytest.mark.parametrize(
    "operation,error", [("fsync", errno.EIO), ("replace", errno.ENOSPC)]
)
def test_cli_write_failure_preserves_old_file_and_never_registers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    index: int,
    operation: str,
    error: int,
) -> None:
    argv, paths = _prepare(tmp_path)
    target = paths[index]
    target.parent.mkdir(parents=True, exist_ok=True)
    old = b"old complete input\n"
    target.write_bytes(old)
    real_sync, real_replace = os.fsync, os.replace
    injected: list[str] = []

    def sync(fd: int) -> None:
        writing = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if (
            operation == "fsync"
            and writing.parent == target.parent
            and (writing == target or writing.name.startswith(target.name + "."))
        ):
            injected.append(operation)
            raise OSError(error, "injected intake sync failure")
        real_sync(fd)

    def replace(source, destination):
        if operation == "replace" and Path(destination) == target:
            injected.append(operation)
            raise OSError(error, "injected intake replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError) as raised:
        main(argv)
    assert raised.value.errno == error
    assert injected == [operation]
    assert target.read_bytes() == old
    assert not list(target.parent.glob(target.name + ".*.tmp"))
    assert not capsys.readouterr().out
    assert not (tmp_path / "runtime-state").exists()
