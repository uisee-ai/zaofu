"""1206 Phase B — pane-aware tmux operations.

B-T1: PaneGridLayout actually issues ``tmux split-window`` from the
second create_slot onward, and ``tmux kill-pane`` on kill_slot.

B-T2: TmuxSession's target resolution routes through layout.address()
so send_keys / capture_pane / pane_alive / etc. target the right pane
when pane_grid is active.

B-T3 (attach_handle) lives in tests/test_attach_handle.py since it
lives on TmuxTransport, not TmuxSession.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zf.runtime.tmux import TmuxError, TmuxSession
from zf.runtime.tmux_layout import (
    PaneGridLayout,
    PaneTarget,
    WindowPerRoleLayout,
)


class _R:
    def __init__(self, name: str, instance_id: str | None = None) -> None:
        self.name = name
        self.instance_id = instance_id or name


# ----------------------------- B-T1 -----------------------------


def test_pane_grid_first_create_issues_new_window():
    """First role creates the shared window."""
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    layout.create_slot(tmux, _R("orchestrator"))
    assert any("new-window" in c and "roles" in c
               for c in tmux.command_log)


def test_pane_grid_second_create_issues_split_window():
    """Subsequent roles split the shared window rather than new-window."""
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    layout.create_slot(tmux, _R("orchestrator"))
    tmux.command_log.clear()
    layout.create_slot(tmux, _R("dev"))
    # After window exists, new panes come from split-window, not new-window
    assert any("split-window" in c for c in tmux.command_log), \
        f"expected split-window after first slot, got: {tmux.command_log}"
    assert not any("new-window" in c for c in tmux.command_log), \
        f"expected no new-window on second slot, got: {tmux.command_log}"


def test_pane_grid_split_targets_correct_window():
    """split-window must target the shared roles window, not an arbitrary one."""
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    layout.create_slot(tmux, _R("orchestrator"))
    tmux.command_log.clear()
    layout.create_slot(tmux, _R("dev"))
    split_calls = [c for c in tmux.command_log if "split-window" in c]
    assert split_calls
    # The -t argument should name the shared window
    assert any("zf-t:roles" in c for c in split_calls), \
        f"split-window should -t zf-t:roles, got: {split_calls}"


def test_pane_grid_split_followed_by_select_layout_tiled():
    """After split-window, re-tile the grid so pane sizes stay usable
    past 4-5 splits. Observed in the first pane-grid real run: the 6th
    split-window failed with "pane too small" without this rebalance.
    """
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    layout.create_slot(tmux, _R("orchestrator"))
    tmux.command_log.clear()
    layout.create_slot(tmux, _R("dev"))
    # split-window then select-layout tiled, in that order
    cmds = tmux.command_log
    split_idx = next(i for i, c in enumerate(cmds) if "split-window" in c)
    tile_idx = next(
        (i for i, c in enumerate(cmds)
         if "select-layout" in c and "tiled" in c),
        None,
    )
    assert tile_idx is not None, (
        f"expected select-layout tiled after split-window, got: {cmds}"
    )
    assert tile_idx > split_idx


def test_pane_grid_kill_slot_issues_kill_pane():
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    target = layout.create_slot(tmux, _R("dev"))
    tmux.command_log.clear()
    layout.kill_slot(tmux, target)
    kill_calls = [c for c in tmux.command_log if "kill-pane" in c]
    assert kill_calls, f"expected kill-pane call, got: {tmux.command_log}"


def test_pane_grid_kill_last_pane_does_not_error():
    """tmux kills the whole window when its last pane dies; Phase B
    should note this but the kill_slot call itself must still succeed
    (caller may decide to restart the window on next create_slot)."""
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    t1 = layout.create_slot(tmux, _R("orchestrator"))
    layout.kill_slot(tmux, t1)
    # After losing the last pane, a fresh create_slot should rebuild
    # the window (first-time flag gets reset).
    tmux.command_log.clear()
    layout.create_slot(tmux, _R("dev"))
    assert any("new-window" in c for c in tmux.command_log), (
        f"after killing last pane, next create_slot must new-window "
        f"again, got: {tmux.command_log}"
    )


def test_pane_grid_duplicate_create_replaces_existing_slot():
    """A repeated spawn for the same instance must not leak an orphan pane.

    Watchdog/recycle paths normally call terminate before spawn, but the
    layout should still defend itself when a respawn races or a previous
    terminate was ineffective.
    """
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles")
    layout.create_slot(tmux, _R("dev"))
    layout.create_slot(tmux, _R("review"))

    tmux.command_log.clear()
    layout.create_slot(tmux, _R("dev"))

    kill_idx = next(
        i for i, c in enumerate(tmux.command_log) if "kill-pane" in c
    )
    split_idx = next(
        i for i, c in enumerate(tmux.command_log) if "split-window" in c
    )
    assert kill_idx < split_idx
    assert layout._panes["dev"] == 0  # type: ignore[attr-defined]
    assert layout._panes["review"] == 1  # type: ignore[attr-defined]


# ----------------------------- B-T2 -----------------------------


def test_layout_address_on_window_per_role():
    """Back-compat: WindowPerRoleLayout returns session:instance_id."""
    layout = WindowPerRoleLayout()
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    assert layout.address(tmux, "orchestrator") == "zf-t:orchestrator"
    assert layout.address(tmux, "dev") == "zf-t:dev"


def test_layout_address_on_pane_grid():
    """PaneGridLayout routes instance_id → pane_idx."""
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    layout.create_slot(tmux, _R("orchestrator"))
    layout.create_slot(tmux, _R("dev"))
    assert layout.address(tmux, "orchestrator") == "zf-t:roles.0"
    assert layout.address(tmux, "dev") == "zf-t:roles.1"


def test_pane_target_accepts_stable_tmux_pane_id():
    """Real pane_grid runs store tmux `%pane_id` targets so pane index
    renumbering after kill-pane cannot redirect sends to another role.
    """
    target = PaneTarget(session="zf-t", window="roles", pane="%42")
    assert target.address() == "%42"


def test_pane_grid_persists_binding_for_restart_resolution(tmp_path: Path):
    binding_path = tmp_path / "pane_bindings.json"
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    layout = PaneGridLayout(window_name="roles", binding_path=binding_path)

    layout.create_slot(tmux, _R("dev"))
    layout.record_cwd(tmux, "dev", tmp_path / ".zf" / "workdirs" / "dev" / "project")

    restored = PaneGridLayout(window_name="roles", binding_path=binding_path)
    restored_tmux = TmuxSession(session_name="zf-t", dry_run=True)

    assert restored.address(restored_tmux, "dev") == "zf-t:roles.0"
    assert restored.expected_cwd("dev").endswith(".zf/workdirs/dev/project")


class _ProbeTmux:
    session_name = "zf-t"
    dry_run = False

    def __init__(self, stdout_by_cmd: dict[str, str]) -> None:
        self.stdout_by_cmd = stdout_by_cmd
        self.command_log: list[list[str]] = []

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.command_log.append(args)
        key = " ".join(args[:2])
        stdout = self.stdout_by_cmd.get(key, "")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


class _WindowProbeTmux:
    session_name = "zf-t"
    dry_run = False

    def __init__(self) -> None:
        self.command_log: list[list[str]] = []

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.command_log.append(args)
        if args[:2] == ["tmux", "list-windows"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="@1\troles\t12\n@2\troles\t1\n",
                stderr="",
            )
        if args[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(args, 0, stdout="%99\n", stderr="")
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%99\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class _StaleWindowTmux:
    session_name = "zf-t"
    dry_run = False

    def __init__(self, *, stale_error: str = "can't find window: @22") -> None:
        self.stale_error = stale_error
        self.command_log: list[list[str]] = []

    def create_window(self, name: str) -> None:
        self._run(["tmux", "new-window", "-t", self.session_name, "-n", name])

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.command_log.append(args)
        if args[:2] == ["tmux", "split-window"]:
            target = args[args.index("-t") + 1]
            if target == "@22":
                raise TmuxError(f"tmux command failed: {' '.join(args)}\n{self.stale_error}")
            return subprocess.CompletedProcess(args, 0, stdout="%201\n", stderr="")
        if args[:2] == ["tmux", "new-window"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:2] == ["tmux", "list-windows"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="@23\troles\t1\n",
                stderr="",
            )
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%200\n", stderr="")
        if tuple(args[:2]) in {
            ("tmux", "select-pane"),
            ("tmux", "set-option"),
            ("tmux", "select-layout"),
            ("tmux", "list-panes"),
        }:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_pane_grid_reuses_existing_shared_window_after_layout_rebuild():
    layout = PaneGridLayout(window_name="roles")
    tmux = _WindowProbeTmux()

    target = layout.create_slot(tmux, _R("review"))

    assert target.address() == "%99"
    assert not any(cmd[:2] == ["tmux", "new-window"] for cmd in tmux.command_log)
    split = next(cmd for cmd in tmux.command_log if cmd[:2] == ["tmux", "split-window"])
    assert split[split.index("-t") + 1] == "@1"


def test_pane_grid_recovers_stale_window_target_on_split(tmp_path: Path):
    binding_path = tmp_path / "pane_bindings.json"
    binding_path.write_text(json.dumps({
        "roles": {
            "critic": {"pane": "%149", "session": "zf-t", "window": "roles"},
        },
    }), encoding="utf-8")
    layout = PaneGridLayout(window_name="roles", binding_path=binding_path)
    layout._window_created = True  # type: ignore[attr-defined]
    layout._window_id = "@22"  # type: ignore[attr-defined]
    layout._panes["critic"] = "%149"  # type: ignore[attr-defined]
    tmux = _StaleWindowTmux()

    target = layout.create_slot(tmux, _R("review"))

    assert target.address() == "%200"
    assert layout._window_id == "@23"  # type: ignore[attr-defined]
    assert layout._panes == {"review": "%200"}  # type: ignore[attr-defined]
    assert any(cmd[:2] == ["tmux", "new-window"] for cmd in tmux.command_log)
    select_layout = next(
        cmd for cmd in tmux.command_log
        if cmd[:2] == ["tmux", "select-layout"]
    )
    assert select_layout[select_layout.index("-t") + 1] == "@23"
    bindings = json.loads(binding_path.read_text(encoding="utf-8"))
    assert "critic" not in bindings["roles"]
    assert bindings["roles"]["review"]["pane"] == "%200"


def test_pane_grid_does_not_swallow_non_stale_split_errors():
    layout = PaneGridLayout(window_name="roles")
    layout._window_created = True  # type: ignore[attr-defined]
    layout._window_id = "@22"  # type: ignore[attr-defined]
    tmux = _StaleWindowTmux(stale_error="pane too small")

    with pytest.raises(TmuxError, match="pane too small"):
        layout.create_slot(tmux, _R("review"))


def test_pane_grid_recovers_pane_by_instance_workdir_when_title_is_generic(
    tmp_path: Path,
):
    binding_path = tmp_path / "pane_bindings.json"
    layout = PaneGridLayout(window_name="roles", binding_path=binding_path)
    tmux = _ProbeTmux({
        "tmux list-panes": (
            "%77\t\tproject\t"
            "/path/to/example-project/.zf/workdirs/dev-1/project\n"
        ),
    })

    target = layout.resolve(tmux, "dev-1")  # type: ignore[arg-type]

    assert target.address() == "%77"
    assert any(
        cmd[:4] == ["tmux", "set-option", "-p", "-t"]
        and "@zf_instance_id" in cmd
        and "dev-1" in cmd
        for cmd in tmux.command_log
    )
    data = json.loads(binding_path.read_text(encoding="utf-8"))
    assert data["roles"]["dev-1"]["pane"] == "%77"


def test_layout_address_unknown_instance_falls_back_gracefully():
    """When instance hasn't been allocated, address should still produce
    a sensible target (fall back to session:instance_id) instead of
    crashing — the session may be sending keys during boot before
    create_slot finished."""
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    # Never created slot for 'phantom' — address must not raise
    addr = layout.address(tmux, "phantom")
    # Phantom windows land in the legacy session:window form to mimic
    # what TmuxSession did before layouts were introduced.
    assert addr == "zf-t:phantom"


def test_tmux_session_send_keys_uses_layout_address_in_pane_grid():
    """send_keys must call tmux with ``session:window.pane`` when layout
    is pane_grid and the instance has an allocated pane."""
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    # Pre-allocate pane
    layout.create_slot(tmux, _R("dev"))
    tmux.command_log.clear()
    tmux.send_keys("dev", "hello", submit_delay_s=0)
    # Target should be zf-t:roles.0
    send_cmds = [c for c in tmux.command_log if "send-keys" in c]
    assert any("zf-t:roles.0" in c for c in send_cmds), \
        f"send_keys didn't route through pane address: {send_cmds}"


def test_tmux_session_capture_pane_uses_layout_address_in_pane_grid():
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    layout.create_slot(tmux, _R("dev"))
    tmux.command_log.clear()
    tmux.capture_pane("dev")
    cap = [c for c in tmux.command_log if "capture-pane" in c]
    assert any("zf-t:roles.0" in c for c in cap), \
        f"capture_pane didn't route through pane address: {cap}"


def test_tmux_session_kill_window_routes_to_kill_pane_under_pane_grid():
    """kill_window on a pane-grid layout should kill the PANE, not the
    shared window (which would take down every role)."""
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    layout.create_slot(tmux, _R("orchestrator"))
    layout.create_slot(tmux, _R("dev"))
    tmux.command_log.clear()
    tmux.kill_window("dev")
    assert any("kill-pane" in c for c in tmux.command_log), (
        f"under pane_grid, kill_window must emit kill-pane, got: "
        f"{tmux.command_log}"
    )
    # And must NOT issue kill-window which would take down everyone
    assert not any("kill-window" in c for c in tmux.command_log), (
        f"must not destroy the shared window, got: {tmux.command_log}"
    )


def test_window_per_role_kill_window_still_uses_kill_window():
    """Regression: the default layout's kill_window behavior is unchanged."""
    tmux = TmuxSession(session_name="zf-t", dry_run=True)  # default layout
    tmux.create_window("dev")
    tmux.command_log.clear()
    tmux.kill_window("dev")
    assert any("kill-window" in c for c in tmux.command_log)
