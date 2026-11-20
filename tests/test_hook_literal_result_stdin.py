"""A generated reader failure body is CLI data, never a shell program."""

import json
import shlex

import pytest

from tests.test_hook_recv_codex import _invoke, _seed_scoped_worker_task
from zf.cli.hook_workdir_guard import bash_command_looks_mutating
from zf.core.events.log import EventLog
from zf.runtime.reader_submission_guidance import verifier_failure_schema_guidance


def _command(state, quote="'", include_runtime_ref=False):
    guidance = verifier_failure_schema_guidance(
        owner="candidate_verify",
        submit_command=shlex.join([
            "/opt/harness/bin/zf", "result", "validate", "--operation", "op-1",
            "--state-dir", str(state), "--scratch", "--submit",
        ]),
    )
    command = guidance.split("```bash\n", 1)[1].split("\n```", 1)[0]
    command = command.replace("'ZF_EXECUTION_FAILURE'", f"{quote}ZF_EXECUTION_FAILURE{quote}")
    body = json.loads(command.split("\n", 1)[1].rsplit("\n", 1)[0])
    body.update(
        summary=("Probe `command -v host_verification` failed; "
                 f"$(touch {state.parent}/outside) and Path.write_text are literal evidence."),
        evidence_refs=[str(state / "events.jsonl") if include_runtime_ref else "event:evt-failed-probe"],
    )
    return command.split("\n", 1)[0] + "\n" + json.dumps(body, indent=2) + "\nZF_EXECUTION_FAILURE"


def _hook(tmp_path, monkeypatch, command, backend="codex"):
    state = tmp_path / ".zf"
    state.mkdir(exist_ok=True)
    transcript = _seed_scoped_worker_task(state, ["app/mask.py"])
    workdir = state / "workdirs/dev-1/project"
    workdir.mkdir(parents=True, exist_ok=True)
    code = _invoke(
        state, event=f"{'codex' if backend == 'codex' else 'claude'}.hook.pre_tool_use",
        backend=backend, monkeypatch=monkeypatch,
        payload={
            "session_id": "literal-result-stdin", "transcript_path": str(transcript),
            "cwd": str(workdir), "tool_name": "Bash", "tool_input": {"command": command},
        },
    )
    log = EventLog(state / "events.jsonl")
    try:
        events = log.read_all()
    finally:
        log.close()
    return code, events


@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("include_runtime_ref", [False, True])
def test_generated_failure_stdin_reaches_controlled_cli(
    tmp_path, monkeypatch, quote, backend, include_runtime_ref,
):
    command = _command(tmp_path / ".zf", quote, include_runtime_ref)
    code, events = _hook(tmp_path, monkeypatch, command, backend)
    assert code == 0
    assert not any(e.type.endswith("write.rejected") for e in events)
    # Hook approval is not result admission, completion, or filesystem execution.
    assert not any(e.type in {"workflow.call.result.admitted", "task.done"} for e in events)
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("form", [
    "unquoted", "command_substitution", "redirect", "pipe", "separator",
    "suffix", "early_delimiter", "other_program", "missing_delimiter", "without_stdin",
])
def test_composed_or_expandable_stdin_keeps_write_guard(tmp_path, monkeypatch, form):
    command = _command(tmp_path / ".zf")
    header, rest = command.split("\n", 1)
    mutation = f"printf changed > {shlex.quote(str(tmp_path / '.zf/events.jsonl'))}"
    if form == "unquoted":
        command = command.replace("<<'ZF_EXECUTION_FAILURE'", "<<ZF_EXECUTION_FAILURE")
    elif form == "command_substitution":
        command = command.replace("--operation op-1", f'--operation "$({mutation})"')
    elif form == "redirect":
        command = header + f" > {tmp_path}/outside\n" + rest
    elif form == "pipe":
        command = header + f" | tee {tmp_path}/outside\n" + rest
    elif form == "separator":
        command = header + "; " + mutation + "\n" + rest
    elif form == "suffix":
        command += "\n" + mutation
    elif form == "early_delimiter":
        command += "\n" + mutation + "\nZF_EXECUTION_FAILURE"
    elif form == "other_program":
        command = command.replace("/opt/harness/bin/zf", "python")
    elif form == "missing_delimiter":
        command = command.rsplit("\n", 1)[0]
    else:
        command = command.replace("--stdin", "--scratch")
    assert bash_command_looks_mutating(command)
    code, events = _hook(tmp_path, monkeypatch, command)
    assert code == 2
    assert any(e.type.endswith("write.rejected") for e in events)
