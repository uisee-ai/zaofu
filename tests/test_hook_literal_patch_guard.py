"""Literal shell patches share native patch fencing without scanning prose."""

import pytest

from tests.test_hook_recv_codex import _invoke, _seed_scoped_worker_task
from zf.core.events.log import EventLog


def _hook(tmp_path, monkeypatch, patch, *, header="apply_patch <<'PATCH'", tail="PATCH", tool="Bash"):
    state = tmp_path / ".zf"
    state.mkdir(exist_ok=True)
    transcript = _seed_scoped_worker_task(state, ["docs/report.md"])
    workdir = state / "workdirs/dev-1/project"
    workdir.mkdir(parents=True, exist_ok=True)
    code = _invoke(
        state, event="codex.hook.pre_tool_use", backend="codex",
        payload={
            "session_id": "literal-patch", "transcript_path": str(transcript),
            "cwd": str(workdir), "tool_name": tool,
            "tool_input": {"command": f"{header}\n{patch}\n{tail}"},
        }, monkeypatch=monkeypatch,
    )
    return code, EventLog(state / "events.jsonl").read_all()


@pytest.mark.parametrize("tool", ["Bash", "Shell"])
@pytest.mark.parametrize("quote", ["'", '"'])
def test_literal_report_body_is_data(tmp_path, monkeypatch, tool, quote):
    patch = (
        "*** Begin Patch\n*** Add File: docs/report.md\n"
        f"+Evidence: `{tmp_path}-archive/report.md`; read {tmp_path}/.zf/events.jsonl.\n"
        "+Never run zf task-doc ingest or $(touch unrelated).\n*** End Patch"
    )
    code, events = _hook(tmp_path, monkeypatch, patch, tool=tool,
                         header=f"apply_patch <<{quote}PATCH{quote}")
    assert code == 0
    assert not any(e.type.endswith("write.rejected") for e in events)


@pytest.mark.parametrize("operation", ["Add File", "Update File", "Delete File", "Move to"])
@pytest.mark.parametrize("target_kind", ["runtime", "outside_workdir", "outside_scope", "scratch_sibling"])
def test_literal_patch_actual_targets_stay_fenced(tmp_path, monkeypatch, operation, target_kind):
    target = {
        "runtime": str(tmp_path / ".zf/events.jsonl"),
        "outside_workdir": str(tmp_path / "docs/report.md"),
        "outside_scope": "app/unrelated.py",
        "scratch_sibling": str(tmp_path / ".zf/tmp/result-submit/op/report.md"),
    }[target_kind]
    prefix = "*** Update File: docs/report.md\n" if operation == "Move to" else ""
    patch = f"*** Begin Patch\n{prefix}*** {operation}: {target}\n*** End Patch"
    code, events = _hook(tmp_path, monkeypatch, patch)
    assert code == 2
    kind = "runtime" if target_kind == "runtime" else "scope"
    assert any(e.type == f"worker.{kind}_write.rejected" for e in events)


@pytest.mark.parametrize("header,tail", [
    ("apply_patch <<PATCH", "PATCH"),
    ("apply_patch <<'PATCH'; touch outside", "PATCH"),
    ("apply_patch <<'PATCH' > outside", "PATCH"),
    ("$(which apply_patch) <<'PATCH'", "PATCH"),
    ("apply_patch <<'PATCH'", "PATCH\ntouch outside"),
    ("apply_patch <<'PATCH'", "PATCH\ntouch outside\nPATCH"),
])
def test_composed_or_expandable_shell_keeps_conservative_guard(tmp_path, monkeypatch, header, tail):
    patch = f"*** Begin Patch\n*** Add File: docs/report.md\n+`{tmp_path}/evidence`\n*** End Patch"
    code, events = _hook(tmp_path, monkeypatch, patch, header=header, tail=tail)
    assert code == 2
    assert any(e.type == "worker.scope_write.rejected" for e in events)
