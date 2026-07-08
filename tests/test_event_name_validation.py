"""E sprint — loader validates role.triggers against known event names.

Goal: catch silent typos like ``triggers: [task.assigend]`` at config
load time instead of after a 30-minute hang where the role never wakes.

Validation policy:
  - publishes are NOT validated (users extend the event vocabulary by
    publishing new names — that's how new contracts are introduced)
  - triggers that don't match KNOWN_EVENT_TYPES AND aren't in any
    role's publishes → stderr warning, but no fail-fast
"""

from __future__ import annotations

from pathlib import Path

import yaml

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.events.known_types import (
    KNOWN_EVENT_TYPES,
    validate_role_event_names,
)


class TestKnownEventTypes:
    def test_canonical_lifecycle_events_present(self):
        for name in (
            "user.message", "task.created", "task.assigned",
            "loop.started", "session.started",
        ):
            assert name in KNOWN_EVENT_TYPES

    def test_worker_contract_events_present(self):
        for name in (
            "dev.build.done", "review.approved", "verify.passed",
            "verify.failed", "test.passed", "judge.passed",
        ):
            assert name in KNOWN_EVENT_TYPES

    def test_p0_1_signal_events_present(self):
        # P0.1 added these — make sure they're in the whitelist
        for name in ("agent.api_blocked", "agent.timeout",
                     "orchestrator.dispatch_skipped"):
            assert name in KNOWN_EVENT_TYPES

    def test_run_lifecycle_events_present(self):
        for name in (
            "run.started",
            "run.heartbeat",
            "run.stalled",
            "run.cancelled",
            "run.completed",
            "run.archived",
            "run.abandoned",
        ):
            assert name in KNOWN_EVENT_TYPES

    def test_autoresearch_trigger_events_present(self):
        for name in (
            "autoresearch.trigger.accepted",
            "autoresearch.trigger.skipped",
            "autoresearch.invocation.requested",
            "autoresearch.invocation.accepted",
            "autoresearch.invocation.rejected",
            "autoresearch.loop.requested",
            "autoresearch.loop.accepted",
            "autoresearch.loop.skipped",
            "autoresearch.loop.started",
            "autoresearch.loop.completed",
            "autoresearch.loop.failed",
            "autoresearch.review_gate.requested",
            "autoresearch.review_gate.accepted",
            "autoresearch.review_gate.started",
            "autoresearch.review_gate.completed",
            "autoresearch.review_gate.failed",
            "autoresearch.review_gate.skipped",
        ):
            assert name in KNOWN_EVENT_TYPES

    def test_stage_backedge_kernel_events_present(self):
        assert "impl.rework.requested" in KNOWN_EVENT_TYPES

    def test_worker_policy_applied_event_present(self):
        assert "worker.policy.applied" in KNOWN_EVENT_TYPES

    def test_repair_projection_rebuild_event_present(self):
        assert "projection.rebuild.requested" in KNOWN_EVENT_TYPES

    def test_identity_binding_request_event_present(self):
        assert "identity.binding.requested" in KNOWN_EVENT_TYPES

    def test_task_map_amended_event_present(self):
        for name in (
            "verify.parity_scan.requested",
            "module.parity.scan.completed",
            "module.parity.scan.failed",
            "cangjie.module.parity.scan.completed",
            "cangjie.module.parity.scan.failed",
            "module.parity.closed",
            "module.parity.blocked",
            "gap_plan.ready",
            "task_map.amend.requested",
            "task_map.amended",
            "task_map.amend.failed",
            "flow.discovery.requested",
            "flow.discovery.completed",
            "flow.discovery.failed",
            "flow.gap_plan.ready",
            "flow.goal.closed",
            "flow.goal.blocked",
        ):
            assert name in KNOWN_EVENT_TYPES


class TestValidateRoleEventNames:
    def test_canonical_trigger_passes(self):
        roles = [RoleConfig(name="dev", triggers=["task.assigned"])]
        assert validate_role_event_names(roles) == []

    def test_typo_trigger_warned(self):
        roles = [RoleConfig(name="dev", triggers=["task.assigend"])]  # typo
        warnings = validate_role_event_names(roles)
        assert len(warnings) == 1
        assert "task.assigend" in warnings[0]
        assert "dev" in warnings[0]

    def test_publishes_not_validated(self):
        # publishes are user-extensible; never warned about
        roles = [RoleConfig(name="dev", publishes=["totally.made.up.event"])]
        assert validate_role_event_names(roles) == []

    def test_trigger_matching_other_role_publishes_passes(self):
        # role A publishes "custom.event"; role B triggers on it → ok
        roles = [
            RoleConfig(name="a", publishes=["custom.event"]),
            RoleConfig(name="b", triggers=["custom.event"]),
        ]
        assert validate_role_event_names(roles) == []

    def test_autoresearch_trigger_subscription_is_known(self):
        roles = [
            RoleConfig(
                name="orchestrator",
                triggers=[
                    "autoresearch.trigger.accepted",
                    "autoresearch.invocation.requested",
                    "autoresearch.loop.requested",
                ],
            ),
        ]
        assert validate_role_event_names(roles) == []


class TestLoaderEmitsWarning:
    def _yaml(self, tmp_path: Path, roles_data: list[dict]) -> Path:
        cfg = {
            "version": "1.0",
            "project": {"name": "t"},
            "roles": roles_data,
        }
        p = tmp_path / "zf.yaml"
        p.write_text(yaml.dump(cfg))
        return p

    def test_load_with_typo_emits_stderr_warning(self, tmp_path: Path, capsys):
        p = self._yaml(tmp_path, [
            {"name": "dev", "backend": "claude-code",
             "triggers": ["task.assigend"]},  # typo
        ])
        load_config(p)
        err = capsys.readouterr().err
        assert "task.assigend" in err
        assert "typo" in err

    def test_load_with_clean_yaml_no_event_warning(self, tmp_path: Path, capsys):
        p = self._yaml(tmp_path, [
            {"name": "dev", "backend": "claude-code",
             "triggers": ["task.assigned"], "publishes": ["dev.build.done"]},
        ])
        load_config(p)
        err = capsys.readouterr().err
        assert "typo" not in err

    def test_load_does_not_fail_on_typo(self, tmp_path: Path):
        # Validation is a warning, not an error — config still loads.
        p = self._yaml(tmp_path, [
            {"name": "dev", "backend": "claude-code",
             "triggers": ["clearly.not.an.event"]},
        ])
        cfg = load_config(p)
        assert len(cfg.roles) == 1
        assert cfg.roles[0].triggers == ["clearly.not.an.event"]
