"""feishu-S5: `zf channel say` — agent outbound via ControlledAction, no MCP/token."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.config.schema import OpenClawFeishuBridgeOutboundConfig
from zf.core.events.log import EventLog


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "say-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _events(sd: Path):
    return EventLog(sd / "events.jsonl").read_all()


def test_channel_say_posts_via_controlled_action(project: Path, capsys):
    rc = main(["channel", "say", "ch-dev", "--text", "build done",
               "--member-id", "dev", "--mention", "arch"])
    assert rc == 0
    posted = [e for e in _events(project / ".zf")
              if e.type == "channel.message.posted"]
    assert posted and posted[-1].payload["channel_id"] == "ch-dev"
    assert posted[-1].payload["text"] == "build done"
    # audited as an agent acting through the gate (actor/source), not raw transport
    assert posted[-1].actor == "agent:dev"
    assert posted[-1].payload["source"] == "cli"


def test_channel_message_posted_is_in_default_feishu_outbound():
    # The invariant that makes §3 work without the agent holding a token: the
    # bridge's outbound projection already includes channel.message.posted, so a
    # `say` reaches Feishu via the sidecar edge, not an agent-side API call.
    assert "channel.message.posted" in \
        OpenClawFeishuBridgeOutboundConfig().include_event_types


def test_channel_say_requires_text(project: Path):
    with pytest.raises(SystemExit):
        main(["channel", "say", "ch-dev"])
