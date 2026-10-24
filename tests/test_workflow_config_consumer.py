"""Approved policy must be loaded by the consumer, not merely written to disk."""
from pathlib import Path

import pytest

from tests.test_workflow_config_apply import _proposal
from zf.core.config.loader import load_config
from zf.runtime.workflow_config_consumer import approved_config_consumer_error


@pytest.mark.parametrize("change", ["none", "roles", "gate", "ui", "missing", "corrupt"])
def test_consumer_compares_verified_snapshot(tmp_path: Path, change: str) -> None:
    proposal, _, _ = _proposal(tmp_path)
    state = tmp_path / ".zf"
    payload = {"workflow_proposal_ref": {"bound_by_caller": True},
               "effective_config_ref": proposal["effective_config_ref"]}
    config = load_config(tmp_path / "candidate.yaml")
    if change == "roles":
        config.roles[0].model = "another-model"
    elif change == "gate":
        config.workflow.plan_approval_enabled = not config.workflow.plan_approval_enabled
    elif change == "ui":
        config.runtime.web_terminal.enabled = not config.runtime.web_terminal.enabled
    elif change == "missing":
        payload.pop("effective_config_ref")
    elif change == "corrupt":
        (state / proposal["effective_config_ref"]["ref"]).write_text("{}")
    error = approved_config_consumer_error(state, config, payload)
    if change in {"none", "ui"}:
        assert error == ""
    elif change in {"roles", "gate"}:
        assert error.startswith("consumer_config_stale")
    else:
        assert error.startswith("consumer_config_snapshot_")
