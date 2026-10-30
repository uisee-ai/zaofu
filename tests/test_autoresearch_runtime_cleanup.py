import subprocess

import pytest

from zf.autoresearch.runtime_cleanup import stop_experiment_runtime
from zf.core.state.session import SessionStore


@pytest.mark.parametrize("fault", ["", "foreign_owner", "external_state", "invalid_config", "stop_timeout"])
def test_cleanup_respects_state_config_and_owned_runtime(tmp_path, monkeypatch, fault):
    root = tmp_path / "experiment"
    root.mkdir()
    state = tmp_path / "outside" if fault == "external_state" else root / "custom-runtime"
    (root / "zf.yaml").write_text(
        "invalid: [" if fault == "invalid_config" else
        f'project:\n  name: test\n  state_dir: {state}\nroles: []\n'
    )
    SessionStore(state / "session.yaml").create(str(tmp_path if fault == "foreign_owner" else root))
    candidate = root / "candidate.py"
    candidate.write_text("# candidate must survive teardown\n")
    monkeypatch.setenv("ZF_STATE_DIR", "/parent/real-dev")
    monkeypatch.setenv("ZF_PROJECT_ROOT", "/parent/real-dev")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == root
        assert "ZF_STATE_DIR" not in kwargs["env"] and "ZF_PROJECT_ROOT" not in kwargs["env"]
        assert command[-3:] == ["stop", "--fast", "--include-run-manager"]
        if fault == "stop_timeout":
            raise subprocess.TimeoutExpired(command, 90)
        return subprocess.CompletedProcess(command, 0)

    result = stop_experiment_runtime(root, runner=runner)
    assert result["status"] == ("stopped" if not fault else "retained")
    assert bool(calls) is (fault in {"", "stop_timeout"})
    assert candidate.read_text() == "# candidate must survive teardown\n"


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "exception"])
def test_resident_tears_down_child_on_all_runner_exit_paths(tmp_path, monkeypatch, failure):
    from zf.autoresearch import resident
    from zf.autoresearch.loop_requests import build_loop_request_payload, LOOP_REQUESTED
    from zf.core.events import EventLog, ZfEvent

    state = tmp_path / "parent-runtime"
    payload = build_loop_request_payload({"trigger_id": "cleanup"}, source_event_id="source")
    EventLog(state / "events.jsonl").append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    cleaned = []
    monkeypatch.setattr("zf.autoresearch.runtime_cleanup.stop_experiment_runtime",
                        lambda path, **kw: cleaned.append(path) or {"status": "stopped"})

    def runner(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "exception":
            raise RuntimeError("interrupted runner")
        return subprocess.CompletedProcess(command, -15, stdout="", stderr="")

    kwargs = dict(state_dir=state, worktree_root=tmp_path / "worktrees", output_root=tmp_path / "out",
                  execute=True, env={"ZF_AUTORESEARCH_RESIDENT": "authorized"}, runner=runner)
    if failure == "exception":
        with pytest.raises(RuntimeError, match="interrupted runner"):
            resident.run_resident_once(**kwargs)
    else:
        resident.run_resident_once(**kwargs)
    assert len(cleaned) == 1
    assert cleaned[0].parent == tmp_path / "worktrees"
