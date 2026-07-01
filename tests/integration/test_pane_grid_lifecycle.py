"""1206 Phase C — pane_grid lifecycle integration.

C-T1: respawn path kills a pane and re-allocates one (possibly with
the same pane index via LRU reuse).

C-T2: Recycle exercises the same kill + spawn cycle; pane index is
reclaimable so a long-running session doesn't leak indices.
"""

from __future__ import annotations

from zf.core.config.schema import RoleConfig
from zf.runtime.tmux import TmuxSession
from zf.runtime.tmux_layout import PaneGridLayout
from zf.runtime.transport import TmuxTransport


def _tx(session_name: str = "zf-t") -> TmuxTransport:
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name=session_name, dry_run=True, layout=layout)
    return TmuxTransport(tmux)


def test_respawn_kills_pane_and_allocates_new():
    tx = _tx()
    role = RoleConfig(name="dev", backend="codex")
    tx.spawn(role, ["codex"])
    assert tx.tmux.layout._panes["dev"] == 0  # type: ignore[attr-defined]

    tx.tmux.command_log.clear()
    tx.terminate("dev")
    # kill-pane issued
    assert any("kill-pane" in c for c in tx.tmux.command_log)

    # Respawn: create_slot again. After last-pane kill, the shared
    # window was destroyed → next create issues new-window, not split.
    tx.tmux.command_log.clear()
    tx.spawn(role, ["codex"])
    assert any("new-window" in c for c in tx.tmux.command_log), (
        f"respawn after sole-role kill should new-window, got: "
        f"{tx.tmux.command_log}"
    )
    # New pane index starts from 0 again
    assert tx.tmux.layout._panes["dev"] == 0  # type: ignore[attr-defined]


def test_respawn_one_of_many_reuses_pane_index():
    tx = _tx()
    roles = [
        RoleConfig(name="orchestrator", backend="claude-code"),
        RoleConfig(name="dev", backend="codex"),
        RoleConfig(name="review", backend="claude-code"),
    ]
    for r in roles:
        tx.spawn(r, ["cmd"])

    # dev has pane 1
    assert tx.tmux.layout._panes["dev"] == 1  # type: ignore[attr-defined]

    # Terminate dev; pane 1 freed
    tx.terminate("dev")
    # pane_grid has 'free' list with 1
    assert 1 in tx.tmux.layout._free  # type: ignore[attr-defined]

    # Re-spawn: new slot should reclaim index 1
    tx.tmux.command_log.clear()
    tx.spawn(RoleConfig(name="dev", backend="codex"), ["codex"])
    assert tx.tmux.layout._panes["dev"] == 1  # type: ignore[attr-defined]
    # And should split-window (not new-window — window still alive)
    assert any("split-window" in c for c in tx.tmux.command_log)
    assert not any("new-window" in c for c in tx.tmux.command_log)


def test_recycle_does_not_leak_pane_indices():
    """Simulate 10 context-recycle cycles on dev and ensure the layout's
    internal bookkeeping stays bounded."""
    tx = _tx()
    dev = RoleConfig(name="dev", backend="codex")
    other = RoleConfig(name="review", backend="claude-code")

    # Start with two roles so the shared window survives between cycles
    tx.spawn(dev, ["codex"])
    tx.spawn(other, ["claude"])

    for _ in range(10):
        tx.terminate("dev")
        tx.spawn(dev, ["codex"])

    # dev must still land at the first-available index (1 or 2 depending
    # on LRU reuse) — never higher than 2 (the total role count)
    assert tx.tmux.layout._panes["dev"] <= 2  # type: ignore[attr-defined]
    # free list should be empty (just spawned one)
    assert tx.tmux.layout._free == []  # type: ignore[attr-defined]


def test_pane_grid_send_keys_targets_right_pane_after_respawn():
    tx = _tx()
    a = RoleConfig(name="a", backend="claude-code")
    b = RoleConfig(name="b", backend="codex")
    tx.spawn(a, ["claude"])
    tx.spawn(b, ["codex"])

    # Kill a, then re-spawn a — it should get index 0 back and subsequent
    # send_keys should land on zf-t:roles.0, not b's pane.
    tx.terminate("a")
    tx.spawn(a, ["claude"])

    tx.tmux.command_log.clear()
    tx.send_task("a", __import__("pathlib").Path("/tmp/brief"), "hello",
                 )
    send_cmds = [c for c in tx.tmux.command_log if "send-keys" in c]
    assert any("zf-t:roles.0" in c for c in send_cmds), (
        f"send_task to respawned 'a' should target roles.0, got: {send_cmds}"
    )
