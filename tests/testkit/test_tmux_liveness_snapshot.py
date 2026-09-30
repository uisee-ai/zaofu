"""Fresh status/context snapshots preserve liveness and fallback behavior."""

import subprocess
from unittest.mock import Mock

import pytest

from zf.runtime.tmux import TmuxError, TmuxSession
from zf.runtime.tmux_layout import PaneGridLayout
from zf.runtime.transport import TmuxTransport


def session_with_output(output, *, returncode=0, stderr=""):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    session = TmuxSession("test", layout=layout)
    session._run = Mock(return_value=subprocess.CompletedProcess(
        [], returncode, stdout=output, stderr=stderr,
    ))
    return session


def test_fresh_snapshot_one_query_and_exact_context_window():
    session = session_with_output("%42\tdev\t4242\tpython\n__ZF_CAPTURE_OK__\nworking\n")
    assert session.pane_liveness_snapshot("dev") == ("4242", "python", "working\n")
    args = session._run.call_args.args[0]
    assert args[1] == "if-shell"
    assert "-120" in args[6] and "-200" not in args[6]
    assert "if-shell" in args[6]  # Capture has a second fresh owner guard.
    assert session._run.call_count == 1
    session._run.return_value.stdout = "%42\tdev\t4242\tpython\n__ZF_CAPTURE_OK__\nnew\n"
    assert session.pane_liveness_snapshot("dev")[2] == "new\n"
    assert session._run.call_count == 2  # No cached observation.


@pytest.mark.parametrize("output", [
    "", "rejected\n", "%42\tforeign\t4242\tpython\n__ZF_CAPTURE_OK__\nx",
    "%43\tdev\t4242\tpython\n__ZF_CAPTURE_OK__\nx",
    "%42\tdev\tbad-pid\tpython\n__ZF_CAPTURE_OK__\nx",
    "%42\tdev\t4242\tpython\nrejected\n",
    "%42\tdev\t4242\tpython\textra\n__ZF_CAPTURE_OK__\nx",
])
def test_malformed_or_foreign_snapshot_requests_legacy_fallback(output):
    assert session_with_output(output).pane_liveness_snapshot("dev") is None


@pytest.mark.parametrize("kind", ["exit", "stderr", "exception", "cold", "unsafe"])
def test_snapshot_failure_does_not_replace_legacy_resolver(kind):
    session = session_with_output("%42\tdev\t4242\tpython\n__ZF_CAPTURE_OK__\nx")
    role = "dev"
    if kind == "exit":
        session._run.return_value.returncode = 1
    elif kind == "stderr":
        session._run.return_value.stderr = "error"
    elif kind == "exception":
        session._run.side_effect = TmuxError("timeout")
    elif kind == "cold":
        session.layout._panes.clear()
    else:
        role = "dev,#{pid}"
        session.layout._panes[role] = "%42"
    assert session.pane_liveness_snapshot(role) is None
    if kind in {"cold", "unsafe"}:
        session._run.assert_not_called()


@pytest.mark.parametrize("command,screen,expected", [
    ("python", "working", (True, "")),
    ("bash", "working", (False, "shell")),
    ("python", "Codex ran out of room in the model's context window.",
     (False, "provider_context_exhausted")),
])
def test_transport_uses_snapshot_but_preserves_liveness_verdict(command, screen, expected):
    session = session_with_output(f"%42\tdev\t4242\t{command}\n__ZF_CAPTURE_OK__\n{screen}")
    assert TmuxTransport(session)._agent_process_liveness("dev") == expected
    assert session._run.call_count == 1


def test_transport_failed_snapshot_uses_original_status_then_capture():
    session = session_with_output("rejected\n")
    session.pane_process_status = Mock(return_value=("4242", "python"))
    session.capture_pane = Mock(return_value="working")
    assert TmuxTransport(session).is_alive("dev")
    session.pane_process_status.assert_called_once_with("dev")
    session.capture_pane.assert_called_once_with("dev", lines=120)


def test_node_snapshot_still_requires_process_probe():
    session = session_with_output("%42\tdev\t4242\tnode\n__ZF_CAPTURE_OK__\nworking")
    transport = TmuxTransport(session)
    transport._pane_process_probe = Mock(return_value={
        "available": True, "processes": [{"command": "unrelated-node"}],
    })
    assert transport._agent_process_liveness("dev") == (False, "node_without_agent_wrapper")
    transport._pane_process_probe.assert_called_once_with("dev", process_status=("4242", "node"))


def paired_response(context="working\n", log="full log\n", command="python"):
    return (
        f"%42\tdev\t4242\t{command}\n__ZF_CAPTURE_120_OK__\n{context}"
        f"__ZF_CAPTURE_200_OK__\n{log}__ZF_CAPTURE_PAIR_END__\n"
    )


def test_paired_observation_preserves_distinct_bodies_without_caching():
    context, log = "  上下文\n\n", "  older + log\n\n"
    session = session_with_output(paired_response(context, log))
    assert session.pane_liveness_and_log_snapshot("dev") == ("4242", "python", context, log)
    assert session._run.call_count == 1
    session._run.return_value.stdout = paired_response("next\n", "new log\n")
    assert TmuxTransport(session).liveness_and_capture_log("dev") == ((True, ""), "new log\n")
    assert session._run.call_count == 2


@pytest.mark.parametrize("output", [
    "", "rejected\n", paired_response().replace("%42", "%43"),
    paired_response().replace("\tdev\t", "\tforeign\t"),
    paired_response().replace("4242", "bad-pid"),
    paired_response().replace("__ZF_CAPTURE_120_OK__", "rejected"),
    paired_response().replace("__ZF_CAPTURE_200_OK__", "rejected"),
    paired_response().replace("__ZF_CAPTURE_PAIR_END__\n", ""),
    paired_response("__ZF_CAPTURE_200_OK__\n"),
    paired_response(log="__ZF_CAPTURE_200_OK__\n"),
    paired_response(log="__ZF_CAPTURE_PAIR_END__\n"),
    paired_response("__ZF_CAPTURE_120_OK__\n"),
])
def test_paired_ambiguous_malformed_foreign_response_is_not_consumed(output):
    assert session_with_output(output).pane_liveness_and_log_snapshot("dev") is None


@pytest.mark.parametrize("kind", ["exit", "stderr", "exception", "cold", "unsafe", "dry", "window"])
def test_paired_failure_or_unsupported_layout_requests_fallback(kind):
    from zf.runtime.tmux_layout import WindowPerRoleLayout

    session = session_with_output(paired_response())
    role = "dev"
    if kind == "exit":
        session._run.return_value.returncode = 1
    elif kind == "stderr":
        session._run.return_value.stderr = "error"
    elif kind == "exception":
        session._run.side_effect = TmuxError("timeout")
    elif kind == "cold":
        session.layout._panes.clear()
    elif kind == "unsafe":
        role = "dev,#{pid}"
        session.layout._panes[role] = "%42"
    elif kind == "dry":
        session.dry_run = True
    else:
        session.layout = WindowPerRoleLayout()
    assert TmuxTransport(session).liveness_and_capture_log(role) is None
    if kind in {"cold", "unsafe", "dry", "window"}:
        session._run.assert_not_called()


@pytest.mark.parametrize("command,context,log,expected", [
    ("python", "working\n", "Codex ran out of room in the model's context window.\n", (True, "")),
    ("python", "Codex ran out of room in the model's context window.\n", "normal\n", (False, "provider_context_exhausted")),
    ("/bin/bash", "working\n", "log\n", (False, "shell")),
    ("python", "", "", (True, "")),
])
def test_paired_only_120_context_controls_liveness(command, context, log, expected):
    session = session_with_output(paired_response(context, log, command))
    assert TmuxTransport(session).liveness_and_capture_log("dev") == (expected, log)


def test_paired_node_requests_original_probe_before_log_order():
    session = session_with_output(paired_response(command="/usr/bin/node"))
    transport = TmuxTransport(session)
    transport._pane_process_probe = Mock()
    assert transport.liveness_and_capture_log("dev") is None
    transport._pane_process_probe.assert_not_called()
    # The caller must follow the original is_alive -> node probe -> capture_log.
