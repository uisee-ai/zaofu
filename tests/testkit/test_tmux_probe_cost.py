"""Deterministic observation of pane-grid liveness query expansion."""

from __future__ import annotations

import subprocess
import shlex

import pytest

from zf.runtime.tmux import TmuxSession
from zf.runtime.tmux_layout import PaneGridLayout
from zf.runtime.transport import TmuxTransport


def test_pane_grid_liveness_and_capture_keep_identity_checks_observable(record_property):
    layout = PaneGridLayout(window_name="roles")
    layout._panes["dev"] = "%42"  # type: ignore[attr-defined]
    tmux = TmuxSession("probe-cost", layout=layout)
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, check=True, capture=False):
        calls.append(args)
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            stdout = "%42\tdev\n"
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}":
            stdout = "%42\tdev\t4242\n"
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}":
            stdout = "%42\tdev\t4242\tpython\n"
        elif args[-1] == "#{pane_pid}":
            stdout = "4242\n"
        elif args[-1] == "#{pane_current_command}":
            stdout = "python\n"
        elif args[1:2] == ["capture-pane"]:
            stdout = "working\n"
        elif args[1] == "if-shell":
            stdout = (
                "%42\tdev\t4242\tpython\n__ZF_CAPTURE_OK__\nworking\n"
                if "#{pane_current_command}" in args[6]
                else "__ZF_CAPTURE_OK__\nworking\n"
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    tmux._run = fake_run  # type: ignore[method-assign]
    transport = TmuxTransport(tmux)

    assert transport.is_alive("dev") is True
    assert transport.capture_log("dev").strip() == "working"
    record_property("tmux_probe_commands", len(calls))
    identity_checks = [
        args for args in calls
        if any("#{@zf_instance_id}" in arg for arg in args)
    ]
    assert identity_checks, "pane ownership must remain observable"
    assert sum(args[1] == "if-shell" for args in calls) == 2
    assert len(calls) == 2
    record_property("tmux_identity_checks", len(identity_checks))
    assert transport.is_alive("dev") is True


@pytest.mark.parametrize("second_owner", ["%42\tforeign\n", "\t\n"])
def test_pane_owner_change_between_resolution_and_pid_read_is_not_alive(second_owner):
    """A query reduction must not discard the last ownership guard."""
    layout = PaneGridLayout(window_name="roles")
    layout._panes["dev"] = "%42"  # type: ignore[attr-defined]
    tmux = TmuxSession("probe-cost", layout=layout)
    ownership = iter(["%42\tdev\n", second_owner])
    unsafe_reads = []

    def run(args, **kwargs):
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            return subprocess.CompletedProcess(args, 0, stdout=next(ownership), stderr="")
        if args[-1].startswith("#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}"):
            command = "\tpython" if "#{pane_current_command}" in args[-1] else ""
            snapshot = next(ownership).rstrip("\n") + "\t4242" + command + "\n"
            return subprocess.CompletedProcess(args, 0, stdout=snapshot, stderr="")
        unsafe_reads.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="4242\n", stderr="")

    tmux._run = run  # type: ignore[method-assign]
    target = layout.resolve(tmux, "dev")
    assert layout.owned_pane_process_status(tmux, target, "dev") == ("", "")
    assert unsafe_reads == [], "must not follow an unowned snapshot with PID/output reads"


def test_cached_pane_status_uses_one_fresh_owned_snapshot_per_observation():
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        value = "%42\tdev\n"
        if "#{pane_current_command}" in args[-1]:
            value = "%42\tdev\t4242\tpython\n"
        return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

    tmux._run = run
    for _ in range(2):
        before = len(calls)
        assert tmux.pane_process_status("dev") == ("4242", "python")
        assert len(calls) - before == 1
        assert calls[-1][-1] == (
            "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}"
        )


@pytest.mark.parametrize("lines", [120, 200])
@pytest.mark.parametrize("output", ["working\n", "", "__ZF_CAPTURE_OK__\n\n"])
def test_guarded_capture_returns_owned_output_without_prequery(lines, output):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[1:3] == ["if-shell", "-F"]
        return subprocess.CompletedProcess(
            args, 0,
            stdout="__ZF_CAPTURE_OK__\n" + output,
            stderr="",
        )

    tmux._run = run
    assert tmux.capture_pane("dev", lines=lines) == output
    assert len(calls) == 1
    assert calls[0][3:6] == [
        "-t", "%42", "#{&&:#{==:#{pane_id},%42},#{==:#{@zf_instance_id},dev}}",
    ]
    assert shlex.split(calls[0][6]) == [
        "display-message", "-p", "__ZF_CAPTURE_OK__", ";",
        "capture-pane", "-p", "-t", "%42", "-S", str(-lines),
    ]


@pytest.mark.parametrize("output,stderr,code", [
    ("__ZF_CAPTURE_REJECTED__\n", "", 0),
    ("not-a-capture\n", "", 0),
    ("__ZF_CAPTURE_OK__", "", 0),
    ("__ZF_CAPTURE_OK__\nforeign", "no such pane", 0),
    ("__ZF_CAPTURE_OK__\nforeign", "", 1),
])
def test_guarded_capture_rejects_foreign_or_malformed_result(output, stderr, code):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, code, stdout=output, stderr=stderr,
        )

    tmux._run = run
    assert layout.capture_owned_pane(tmux, "dev", lines=200) is None

@pytest.mark.parametrize("instance,pane", [
    ("dev:unsafe", "%42"), ("x,#{pane_id}", "%42"), ("dev\n", "%42"),
    ("", "%42"), ("dev", "%42,#{pane_id}"), ("dev", 0), ("dev", None),
])
def test_guarded_capture_unsafe_identity_falls_back_without_command(instance, pane):
    layout = PaneGridLayout()
    layout._panes[instance] = pane
    tmux = TmuxSession("probe-cost", layout=layout)
    tmux._run = lambda *args, **kwargs: pytest.fail("unsafe ids must use resolver fallback")
    assert layout.capture_owned_pane(tmux, instance, lines=120) is None


@pytest.mark.parametrize("initial", ["cold", "persisted", "foreign", "missing", "malformed"])
@pytest.mark.parametrize("replacement", [True, False])
def test_capture_resolver_preserves_binding_recovery(tmp_path, initial, replacement):
    from zf.runtime.tmux import TmuxError

    layout = PaneGridLayout(binding_path=tmp_path / "panes.json")
    layout._window_id = "@1"
    tmux = TmuxSession("probe-cost", layout=layout)
    if initial != "cold":
        layout._record_binding(tmux, "dev", "%42", cwd="/old/workdir")
        if initial != "persisted":
            layout._panes["dev"] = "%42"
            layout._expected_cwds["dev"] = "/old/workdir"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        command = args[1]
        output, code = "", 0
        if command == "if-shell":
            assert args[4] == "%42"
            output = "malformed" if initial == "malformed" else "__ZF_CAPTURE_REJECTED__\n"
            code = 1 if initial == "missing" else 0
        elif command == "display-message":
            assert args[4] == "%42"
            output = "%42\tforeign\n"
            code = 1 if initial == "missing" else 0
        elif command == "list-panes":
            output = "%43\tdev\tdev\t/new/workdir\n" if replacement else ""
        elif command in ("select-pane", "set-option"):
            assert "%43" in args
        else:
            assert command == "capture-pane"
            assert args[-2:] == ["-S", "-200"]
            assert args[3] == ("%43" if replacement else "probe-cost:dev")
            if not replacement:
                raise TmuxError("can't find window: dev")
            output = "replacement output\n"
        return subprocess.CompletedProcess(args, code, stdout=output, stderr="")

    tmux._run = run
    if replacement:
        assert tmux.capture_pane("dev", lines=200) == "replacement output\n"
        assert layout._panes["dev"] == "%43"
        assert layout._binding_entry("dev")["pane"] == "%43"
    else:
        with pytest.raises(TmuxError, match="can't find window"):
            tmux.capture_pane("dev", lines=200)
        assert "dev" not in layout._panes
        assert layout._binding_entry("dev") == {}
    assert layout.expected_cwd("dev") == ""
    assert sum(args[1] == "if-shell" for args in calls) == (
        0 if initial in ("cold", "persisted") else 1
    )


def test_capture_command_error_propagates_without_hidden_retry():
    from zf.runtime.tmux import TmuxError

    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise TmuxError("tmux command timed out")

    tmux._run = run
    with pytest.raises(TmuxError, match="timed out"):
        tmux.capture_pane("dev")
    assert len(calls) == 1


def test_capture_window_layout_and_dry_run_stay_legacy():
    legacy = TmuxSession("probe-cost", dry_run=True)
    assert legacy.capture_pane("dev", lines=120) == ""
    assert legacy.command_log == ["tmux capture-pane -t probe-cost:dev -p -S -120"]
    layout = PaneGridLayout()
    layout._panes["dev"] = 0
    dry = TmuxSession("probe-cost", dry_run=True, layout=layout)
    assert dry.capture_pane("dev", lines=200) == ""
    assert dry.command_log == ["tmux capture-pane -t probe-cost:roles.0 -p -S -200"]


@pytest.mark.parametrize("replacement", [None, "%43"])
def test_cached_status_rechecks_owner_and_rediscovers_replacement(monkeypatch, replacement):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    owner = "dev"
    monkeypatch.setattr(layout, "_pane_from_binding", lambda *args: None)
    monkeypatch.setattr(layout, "_find_pane_by_instance", lambda *args: replacement)
    monkeypatch.setattr(layout, "_set_pane_identity", lambda *args: None)
    monkeypatch.setattr(layout, "_record_binding", lambda *args: None)

    def run(args, **kwargs):
        pane = args[4]
        current_owner = owner if pane == "%42" else "dev"
        value = f"{pane}\t{current_owner}"
        if "#{pane_current_command}" in args[-1]:
            value += "\t4242\tpython" if pane == "%42" else "\t4343\tclaude"
        return subprocess.CompletedProcess(args, 0, stdout=value + "\n", stderr="")

    tmux._run = run
    assert tmux.pane_process_status("dev") == ("4242", "python")
    owner = "foreign"
    expected = ("4343", "claude") if replacement else ("", "")
    assert tmux.pane_process_status("dev") == expected
    assert layout._panes.get("dev") == replacement


@pytest.mark.parametrize("snapshot,stderr,code,expected", [
    ("%42\tdev\t4242\n", "", 0, "4242"),
    ("%42\tforeign\t4242\n", "", 0, ""),
    ("%99\tdev\t4242\n", "", 0, ""),
    ("\t\t\n", "", 0, ""),
    ("%42\tdev\t\n", "", 0, ""),
    ("%42\tdev\tinvalid\n", "", 0, ""),
    ("%42\tdev\t4242\textra\n", "", 0, ""),
    ("%42\tdev\t4242\n", "no such pane", 0, ""),
    ("%42\tdev\t4242\n", "", 1, ""),
])
def test_pane_pid_is_bound_to_same_identity_snapshot(snapshot, stderr, code, expected):
    """PID cannot come from a later lookup than its final ownership check."""
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)

    def run(args, **kwargs):
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}":
            return subprocess.CompletedProcess(args, code, stdout=snapshot, stderr=stderr)
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            return subprocess.CompletedProcess(args, 0, stdout="%42\tdev\n", stderr="")
        # Represents a replacement process after an independent owner query.
        assert args[-1] == "#{pane_pid}"
        return subprocess.CompletedProcess(args, 0, stdout="9999\n", stderr="")

    tmux._run = run
    assert tmux.pane_pid("dev") == expected


@pytest.mark.parametrize("snapshot,stderr,code,expected", [
    ("%42\tdev\t4242\tpython\n", "", 0, ("4242", "python")),
    ("%42\tforeign\t4242\tpython\n", "", 0, ("", "")),
    ("%99\tdev\t4242\tpython\n", "", 0, ("", "")),
    ("%42\tdev\t\tpython\n", "", 0, ("", "")),
    ("%42\tdev\tinvalid\tpython\n", "", 0, ("", "")),
    ("%42\tdev\t4242\n", "", 0, ("", "")),
    ("%42\tdev\t4242\tpython\textra\n", "", 0, ("", "")),
    ("%42\tdev\t4242\tpython\n", "no such pane", 0, ("", "")),
    ("%42\tdev\t4242\tpython\n", "", 1, ("", "")),
])
def test_pane_process_status_binds_command_to_owned_pid(snapshot, stderr, code, expected):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)

    def run(args, **kwargs):
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            return subprocess.CompletedProcess(args, 0, stdout="%42\tdev\n", stderr="")
        assert args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}"
        return subprocess.CompletedProcess(args, code, stdout=snapshot, stderr=stderr)

    tmux._run = run
    assert tmux.pane_process_status("dev") == expected


@pytest.mark.parametrize("command,output,processes,expected", [
    ("python", "working", [], (True, "")),
    ("/bin/bash", "old prompt >", [], (False, "shell")),
    ("zsh", "old prompt >", [], (False, "shell")),
    ("node", "working", [{"command": "node /bin/codex"}], (True, "")),
    ("node", "working", [{"command": "unrelated"}], (False, "node_without_agent_wrapper")),
    ("node", "working", [], (True, "")),
    ("python", "Codex ran out of room in the model's context window.", [],
     (False, "provider_context_exhausted")),
])
def test_combined_status_preserves_liveness_verdicts(command, output, processes, expected):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    observed = []

    def run(args, **kwargs):
        observed.append(args[-1])
        if args[1] == "capture-pane":
            value = output
        elif args[1] == "if-shell":
            value = "__ZF_CAPTURE_OK__\n" + output
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            value = "%42\tdev\n"
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}":
            value = f"%42\tdev\t4242\t{command}\n"
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}":
            value = "%42\tdev\t4242\n"
        else:
            # An independent command read can observe a different process.
            assert args[-1] == "#{pane_current_command}"
            value = "bash\n" if command == "python" else "python\n"
        return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

    tmux._run = run
    tx = TmuxTransport(tmux)
    tx._pane_process_probe = lambda _, **kwargs: {"available": True, "processes": processes}
    assert tx._agent_process_liveness("dev") == expected
    assert "#{pane_current_command}" not in observed
    if expected[1] == "shell":
        assert "-120" not in observed


def test_process_status_window_layout_and_dry_run_preserve_behavior():
    tmux = TmuxSession("probe-cost")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[4] == "probe-cost:dev"
        value = "4242\n" if args[-1] == "#{pane_pid}" else "python\n"
        return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

    tmux._run = run
    assert tmux.pane_process_status("dev") == ("4242", "python")
    assert len(calls) == 2
    dry = TmuxSession("probe-dry", dry_run=True, layout=PaneGridLayout())
    assert dry.pane_process_status("dev") == ("", "")
    assert TmuxTransport(dry).is_alive("dev") is True
    assert dry.command_log == []


@pytest.mark.parametrize("process_command,expected", [
    ("node /bin/codex", (True, "")),
    ("node unrelated.js", (False, "node_without_agent_wrapper")),
])
def test_node_process_probe_consumes_owned_snapshot_without_rereading(
    monkeypatch, process_command, expected,
):
    layout = PaneGridLayout()
    layout._panes["dev"] = "%42"
    tmux = TmuxSession("probe-cost", layout=layout)
    snapshots = []

    def run(args, **kwargs):
        if args[-1] == "#{pane_id}\t#{@zf_instance_id}":
            value = "%42\tdev\n"
        elif args[-1] == "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}":
            snapshots.append(args)
            value = "%42\tdev\t4242\tnode\n"
        else:
            assert args[1] == "if-shell", "must reuse the checked PID and command"
            value = "__ZF_CAPTURE_OK__\nworking"
        return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

    def ps(args, **kwargs):
        assert args == ["ps", "-eo", "pid=,ppid=,command="]
        return subprocess.CompletedProcess(
            args, 0, stdout=f"4242 1 bash\n4243 4242 {process_command}\n", stderr="",
        )

    tmux._run = run
    monkeypatch.setattr("zf.runtime.tmux.subprocess.run", ps)
    assert TmuxTransport(tmux)._agent_process_liveness("dev") == expected
    assert len(snapshots) == 1
