"""TmuxTransport liveness handling for provider context exhaustion."""

from __future__ import annotations

from zf.runtime.transport import TmuxTransport


class _FakeTmux:
    dry_run = False

    def __init__(self, output: str) -> None:
        self.output = output

    def pane_alive(self, role_name: str) -> bool:
        return True

    def pane_current_command(self, role_name: str) -> str:
        return "node"

    def capture_pane(self, role_name: str, *, lines: int = 3000) -> str:
        return self.output


def test_context_exhausted_codex_tui_is_not_alive():
    tx = TmuxTransport(_FakeTmux(
        "■ Codex ran out of room in the model's context window. "
        "Start a new thread or clear earlier history before retrying."
    ))  # type: ignore[arg-type]

    assert tx.is_alive("judge") is False


def test_normal_node_tui_stays_alive():
    tx = TmuxTransport(_FakeTmux("› waiting for input"))  # type: ignore[arg-type]

    assert tx.is_alive("judge") is True
