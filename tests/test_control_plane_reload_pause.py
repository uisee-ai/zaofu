"""Control-plane reload must preserve operator-owned recovery gates."""
from unittest.mock import patch

import pytest

from tests.test_cli_start import project_dir  # noqa: F401
from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.pause_lifecycle import is_dispatch_paused


@pytest.mark.parametrize("control_plane_only", [True, False])
def test_start_preserves_recovery_gates_only_for_control_plane_reload(
    project_dir, control_plane_only,
):
    state_dir = project_dir / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="worker.state.changed", actor="dev", payload={
        "from": "idle", "to": "blocked_human", "reason": "provider blocked",
    }))
    log.append(ZfEvent(type="runtime.safe_halted", actor="dev"))
    log.append(ZfEvent(type="dispatch.paused", actor="dev", payload={
        "source": "remediation_cascade", "reason": "safe_halt",
    }))
    args = ["start", "--dry-run"]
    if control_plane_only:
        args.append("--control-plane-only")
    with patch("zf.runtime.transport.TmuxTransport.is_session_running", return_value=True):
        assert main(args) == 0
    events = log.read_all()
    assert is_dispatch_paused(state_dir, events=events) is control_plane_only
    states = [e.payload["to"] for e in events
              if e.type == "worker.state.changed" and e.actor == "dev"]
    assert states[-1] == ("blocked_human" if control_plane_only else "idle")
    assert len([e for e in events if e.type == "dispatch.resumed"]) == (
        0 if control_plane_only else 1
    )
