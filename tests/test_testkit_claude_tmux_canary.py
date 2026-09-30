"""Deterministic guards for the opt-in Claude tmux canary's verdict."""

import json
from types import SimpleNamespace

import pytest

from tests.conformance import test_zaofu_testkit_s5_real_tmux as canary


@pytest.mark.parametrize("pane, expected", [
    ('echo \'{"type":"result","is_error":false}\'\n', None),
    ('{"type":"result",', None),
    ('{"type":"system"}\n[]\n', None),
    ('{"type":"result","result":" ZF TMUX "}\n',
     {"type": "result", "result": " ZF TMUX "}),
])
def test_terminal_parser_does_not_accept_echo_or_change_payload(pane, expected):
    assert canary._claude_result(pane) == expected


@pytest.mark.parametrize("terminal, exit_code", [
    ({"type": "result", "is_error": True, "result": "Not logged in"}, 1),
    ({"type": "result", "is_error": False, "result": "ZF TMUX CLAUDE OK"}, 0),
    ({"type": "result", "is_error": False, "result": "ZF_TMUX_CLAUDE_OK"}, 7),
])
def test_error_wrong_marker_or_nonzero_exit_fails_and_cleans(
    tmp_path, monkeypatch, terminal, exit_code,
):
    pane = json.dumps(terminal) + f"\nZF_CLAUDE_EXIT={exit_code}\n"
    effects = []
    properties = {}
    tmux = SimpleNamespace(
        create_session=lambda: effects.append("create"),
        create_window=lambda _: effects.append("window"),
        send_keys=lambda *a, **kw: effects.append("send"),
        capture_pane=lambda *a, **kw: pane,
        kill_session=lambda: effects.append("kill"),
        has_session=lambda: effects[-1] != "kill",
    )
    monkeypatch.setattr(canary, "_require_claude_canary", lambda: "claude")
    monkeypatch.setattr(canary, "TmuxSession", lambda **kw: tmux)
    monkeypatch.setattr(canary.subprocess, "check_output", lambda *a, **kw: pane)
    monkeypatch.setattr(canary.time, "sleep", lambda _: pytest.fail("terminal must fail fast"))
    with pytest.raises(AssertionError):
        canary.test_real_tmux_claude_print_emits_result_and_cleans_session(
            tmp_path, properties.__setitem__,
        )
    assert effects == ["create", "window", "send", "kill"]
    evidence = json.loads(properties["claude_tmux"])
    assert evidence["result"] == terminal
    assert evidence["exit_code"] == exit_code
