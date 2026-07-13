"""TR-EVENT-SCHEMA-LOCK-001 step 1/3 — workflow event payload schema validator.

Source of truth: ``zf.yaml workflow.dag.event_schemas``. Used by
ContractD (step 2/3) before EventWriter append and by orchestrator
dispatch (step 2/3) before routing to a downstream role.

Yaml shape (per rule):

::

    arch.proposal.done:
      required: [feature_id, proposal_ref, contract_draft]
      optional: [wave]                       # informational only
      enum:
        verdict: [approve, reject]           # field → allowed values
      nested:
        contract_draft:
          required: [behavior, verification, scope]
      when:                                  # conditional sub-rule
        if:
          verdict: reject                    # trigger: payload[verdict] == "reject"
        then:
          required: [gate_ref, findings]     # additional rule body
      list_item:
        findings:                            # for list[dict] fields
          required: [axis, severity, issue]
          enum:
            severity: [low, medium, high, critical]

The two-section ``if:/then:`` shape replaced the v1 flat layout where
trigger pair and sub-rule keys shared the same map level. The flat
form was rejected because adding a new sub-rule keyword required
extending the parser's keyword whitelist, and a user typo in the
trigger field name silently degraded to "always-on conditional"
(no key matched the whitelist, so the whole map became sub-rule body).
``if:/then:`` removes that ambiguity at the cost of two extra lines.

Backward compat:
- Old zf.yaml without ``event_schemas`` → empty registry → every
  event_type is "loose" (no validation) → no behavioral change.
- Unknown event_type (not in registry) → also loose → validate() returns [].

Out of scope (deferred to step 2/3 + 3/3):
- ContractD wire-up (step 2/3)
- orchestrator_dispatch wire-up (step 2/3)
- rework_routing granular keys (step 3/3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SchemaViolation:
    """One schema violation. Multiple may be returned per event."""

    event_type: str
    field_path: str  # e.g. "payload.contract_draft.behavior"
    code: str        # "missing_required" | "enum_mismatch" | "type_mismatch"
    expected: str
    actual: str


@dataclass
class EventSchemaRule:
    """Validation rule for a single event type.

    All fields default to empty / no-op so partial yaml rules work.
    """

    event_type: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    enum_constraints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    nested_rules: dict[str, "EventSchemaRule"] = field(default_factory=dict)
    # Conditional sub-rule applied when a trigger field equals a trigger value.
    # Example: design.critique.done verdict=reject also requires findings.
    conditional_trigger_field: str | None = None
    conditional_trigger_value: str | None = None
    conditional_rule: "EventSchemaRule | None" = None
    # For ``payload[<field>]`` being a list-of-dicts, validate each item.
    # Keyed by field name → rule applied to each item.
    list_item_rules: dict[str, "EventSchemaRule"] = field(default_factory=dict)
    # FIX-14(bizsim r4 F14):字段必须非空(list/str)。r4 全轮 9 份 verify
    # report 的 requirement_coverage_matrix 全 0 行——required 只保证键在,
    # 空转合约需要 non_empty 档位。
    non_empty: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, event_type: str, raw: Mapping[str, Any]) -> "EventSchemaRule":
        required = tuple(str(x) for x in raw.get("required", []) or [])
        optional = tuple(str(x) for x in raw.get("optional", []) or [])
        non_empty = tuple(str(x) for x in raw.get("non_empty", []) or [])

        enum_raw = raw.get("enum") or {}
        enum_constraints: dict[str, tuple[str, ...]] = {
            str(k): tuple(str(v) for v in vals)
            for k, vals in enum_raw.items()
        }

        nested_raw = raw.get("nested") or {}
        nested_rules: dict[str, EventSchemaRule] = {
            str(k): cls.from_dict(f"{event_type}.{k}", v)
            for k, v in nested_raw.items()
            if isinstance(v, Mapping)
        }

        list_item_raw = raw.get("list_item") or {}
        list_item_rules: dict[str, EventSchemaRule] = {
            str(k): cls.from_dict(f"{event_type}.{k}[]", v)
            for k, v in list_item_raw.items()
            if isinstance(v, Mapping)
        }

        # Conditional sub-rule. Yaml shape:
        #   when:
        #     if:   {<field>: <value>}
        #     then: {<sub-rule body, same keys as top-level rule>}
        # `if:` carries exactly one trigger pair; extra keys in `if:`
        # are ignored. `then:` is a normal rule body parsed recursively.
        cond_field: str | None = None
        cond_value: str | None = None
        cond_rule: EventSchemaRule | None = None
        when_raw = raw.get("when")
        if isinstance(when_raw, Mapping):
            if_raw = when_raw.get("if")
            then_raw = when_raw.get("then")
            if isinstance(if_raw, Mapping) and isinstance(then_raw, Mapping):
                # First (and only) trigger pair from `if:`
                for k, v in if_raw.items():
                    cond_field = str(k)
                    cond_value = str(v) if v is not None else ""
                    break
                if cond_field is not None:
                    cond_rule = cls.from_dict(
                        f"{event_type}|when:{cond_field}={cond_value}",
                        then_raw,
                    )

        return cls(
            event_type=event_type,
            required=required,
            optional=optional,
            non_empty=non_empty,
            enum_constraints=enum_constraints,
            nested_rules=nested_rules,
            conditional_trigger_field=cond_field,
            conditional_trigger_value=cond_value,
            conditional_rule=cond_rule,
            list_item_rules=list_item_rules,
        )


class EventSchemaRegistry:
    """Maps event_type → rule. Built once at startup from zf.yaml.

    Use ``from_dict`` for raw yaml maps or ``from_config`` to read directly
    from a ``ZfConfig``.
    """

    def __init__(self, rules: dict[str, EventSchemaRule] | None = None) -> None:
        self._rules: dict[str, EventSchemaRule] = dict(rules or {})

    # ---------------------------------------------------------- constructors

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "EventSchemaRegistry":
        """Build a registry from the raw event_schemas yaml map.

        ``None`` or empty → empty registry (every event_type loose).
        """
        if not raw:
            return cls({})
        rules: dict[str, EventSchemaRule] = {}
        for event_type, body in raw.items():
            if not isinstance(body, Mapping):
                continue
            rules[str(event_type)] = EventSchemaRule.from_dict(
                str(event_type), body
            )
        return cls(rules)

    @classmethod
    def from_config(cls, config: Any) -> "EventSchemaRegistry":
        """Convenience: read ``config.workflow.dag.event_schemas``.

        Robust to missing intermediate attrs (returns empty registry).
        """
        try:
            schemas = config.workflow.dag.event_schemas
        except AttributeError:
            return cls({})
        return cls.from_dict(schemas)

    # -------------------------------------------------------------- queries

    def is_loose(self, event_type: str) -> bool:
        """No rule registered for this event type — caller must accept it."""
        return event_type not in self._rules

    def rule_for(self, event_type: str) -> "EventSchemaRule | None":
        return self._rules.get(event_type)

    def has_rule(self, event_type: str) -> bool:
        return event_type in self._rules

    def get_rule(self, event_type: str) -> EventSchemaRule | None:
        return self._rules.get(event_type)

    def rule_count(self) -> int:
        return len(self._rules)

    # ----------------------------------------------------------- validation

    def validate(self, event: Any) -> list[SchemaViolation]:
        """Return list of violations (empty list = pass).

        Loose event types return []. The event must have ``type`` and
        ``payload`` (a dict-like) attributes; payload missing or not a
        dict is treated as ``{}`` for validation purposes (a required
        field will still surface as missing_required).
        """
        event_type = getattr(event, "type", "")
        rule = self._rules.get(event_type)
        if rule is None:
            return []
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            payload = {}
        violations: list[SchemaViolation] = []
        _validate_against_rule(
            rule=rule,
            data=payload,
            path="payload",
            violations=violations,
            event_type=event_type,
        )
        return violations


def context_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contract for context warning/critical events.

    The runtime still loads schemas from ``zf.yaml`` so existing projects
    remain loose by default. This helper gives tests and presets one source
    for the strict context-event contract introduced by long-horizon resume
    routing.
    """
    required = [
        "task_id",
        "dispatch_id",
        "role",
        "instance_id",
        "backend",
        "context_usage_ratio",
        "session_ref",
        "source",
        "reason",
    ]
    optional = [
        "ratio",
        "effective_tokens",
        "window",
        "model_context_window",
        "idle",
        "hard_cap",
        "action",
        "snapshot_ref",
        "previous_snapshot_ref",
        "recovery_snapshot_ref",
    ]
    return {
        "worker.context.warning": {
            "required": required,
            "optional": optional,
        },
        "worker.context.critical": {
            "required": required,
            "optional": optional,
        },
    }


def progress_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contract for typed progress events."""
    progress_required = [
        "task_id",
        "dispatch_id",
        "role",
        "instance_id",
        "phase",
        "message",
        "source",
    ]
    progress_optional = [
        "current_subtask",
        "percent",
        "source_event_id",
        "context_usage_ratio",
    ]
    phase_required = [
        "task_id",
        "dispatch_id",
        "role",
        "instance_id",
        "phase",
        "source",
    ]
    return {
        "worker.progress": {
            "required": progress_required,
            "optional": progress_optional,
        },
        "phase.progressed": {
            "required": phase_required,
            "optional": ["message", "source_event_id", "context_usage_ratio"],
        },
    }


def user_message_unrouted_schema_rules() -> dict[str, dict[str, Any]]:
    """Payload contract for ``user.message.unrouted`` observability event.

    Emitted when the orchestrator receives a ``user.message`` from a
    human actor but no downstream handler (inline override, task
    creation) consumed it. Required fields keep the signal minimally
    debuggable; optional fields carry context for triage.
    """
    return {
        "user.message.unrouted": {
            "required": ["message_id", "reason"],
            "optional": ["scanned_patterns", "actor_hint", "text_excerpt"],
        },
    }


def runtime_snapshot_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Payload contract for runtime snapshot ledger events."""
    base_required = ["schema_version", "snapshot_id", "snapshot_ref", "source"]
    base_optional = [
        "task_id",
        "dispatch_id",
        "run_id",
        "trace_id",
        "fanout_id",
        "fanout_child_id",
        "role",
        "instance_id",
        "reason",
        "previous_snapshot_ref",
        "recovery_snapshot_ref",
    ]
    return {
        "runtime.snapshot.recorded": {
            "required": base_required,
            "optional": base_optional,
        },
        "runtime.snapshot.superseded": {
            "required": base_required,
            "optional": base_optional,
        },
        "runtime.snapshot.rehydrated": {
            "required": base_required,
            "optional": base_optional,
        },
        "runtime.snapshot.invalid": {
            "required": ["source", "reason"],
            "optional": [
                "schema_version",
                "snapshot_id",
                "snapshot_ref",
                *base_optional,
            ],
        },
    }


def fanout_request_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contract for worker-mediated fanout requests."""
    return {
        "task.fanout.requested": {
            "required": [
                "task_id",
                "dispatch_id",
                "requested_by",
                "reason",
                "scope",
                "requested_specialists",
                "expected_output",
                "risk",
            ],
            "optional": ["target_ref", "source_event_id"],
        },
    }


def channel_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contracts for Agent Channel events."""
    base = ["channel_id", "thread_id", "source"]
    return {
        "channel.created": {
            "required": ["channel_id", "name", "source"],
            "optional": ["scope"],
        },
        "channel.archived": {
            "required": ["channel_id", "source"],
            "optional": ["thread_id", "reason"],
        },
        "channel.member.invited": {
            "required": ["channel_id", "member_id", "persona", "source"],
            "optional": [
                "thread_id",
                "display_name",
                "role",
                "channel_role",
                "member_type",
                "legacy_member_type",
                "provider",
                "backend",
                "provider_binding_id",
                "remote_agent_id",
                "provider_session_id",
                "visibility_profile",
                "permission_profile",
                "write_policy",
                "role_context_ref",
                "scope",
                "permissions",
                "reason",
                "backing_worker_session_id",
                "workflow_role_binding",
                "discussion_policy",
                "output_contract",
                "capabilities",
            ],
        },
        "channel.member.added": {
            "required": ["channel_id", "member_id", "source"],
            "optional": [
                "thread_id",
                "persona",
                "display_name",
                "role",
                "channel_role",
                "member_type",
                "legacy_member_type",
                "provider",
                "backend",
                "provider_binding_id",
                "remote_agent_id",
                "provider_session_id",
                "visibility_profile",
                "permission_profile",
                "write_policy",
                "role_context_ref",
                "scope",
                "permissions",
                "reason",
                "backing_worker_session_id",
                "workflow_role_binding",
                "discussion_policy",
                "output_contract",
                "capabilities",
            ],
        },
        "channel.member.add.rejected": {
            "required": ["channel_id", "member_id", "reason", "source"],
            "optional": [
                "thread_id",
                "member_type",
                "provider",
                "backend",
                "provider_binding_id",
                "channel_role",
                "visibility_profile",
                "permission_profile",
                "write_policy",
                "scope",
                "permissions",
            ],
        },
        "channel.member.permission_profile.audit": {
            "required": ["channel_id", "member_id", "permission_profile", "source"],
            "optional": [
                "thread_id",
                "provider",
                "backend",
                "channel_role",
                "write_policy",
                "dangerous_ack",
                "reason",
                "snapshot_ref",
                "runtime_snapshot_ref",
            ],
        },
        "agent.session.run.cancelled": {
            "required": ["thread_id", "run_id", "source"],
            "optional": [
                "project_id",
                "conversation_id",
                "channel_id",
                "request_id",
                "message_id",
                "member_id",
                "target_member_id",
                "provider",
                "backend",
                "provider_session_id",
                "status",
                "reason",
                "snapshot_ref",
                "runtime_snapshot_ref",
            ],
        },
        "agent.session.run.started": {
            "required": ["thread_id", "run_id", "source"],
            "optional": [
                "project_id",
                "conversation_id",
                "channel_id",
                "request_id",
                "message_id",
                "member_id",
                "target_member_id",
                "provider",
                "backend",
                "provider_session_id",
                "status",
                "permission_snapshot",
                "permission_drift",
                "snapshot_ref",
                "runtime_snapshot_ref",
            ],
        },
        "agent.session.run.completed": {
            "required": ["thread_id", "run_id", "source"],
            "optional": [
                "project_id",
                "conversation_id",
                "channel_id",
                "request_id",
                "message_id",
                "member_id",
                "target_member_id",
                "provider",
                "backend",
                "provider_session_id",
                "status",
                "reason",
                "usage",
                "permission_snapshot",
                "permission_drift",
                "snapshot_ref",
                "runtime_snapshot_ref",
            ],
        },
        "agent.session.run.failed": {
            "required": ["thread_id", "run_id", "source", "reason"],
            "optional": [
                "project_id",
                "conversation_id",
                "channel_id",
                "request_id",
                "message_id",
                "member_id",
                "target_member_id",
                "provider",
                "backend",
                "provider_session_id",
                "status",
                "usage",
                "permission_snapshot",
                "permission_drift",
                "snapshot_ref",
                "runtime_snapshot_ref",
            ],
        },
        "agent.session.part.started": {
            "required": ["thread_id", "run_id", "part_id", "source"],
            "optional": [
                "project_id", "conversation_id", "channel_id", "request_id",
                "message_id", "member_id", "target_member_id", "provider",
                "backend", "provider_session_id", "kind", "state", "title",
                "summary", "content", "seq", "refs",
            ],
        },
        "agent.session.part.delta": {
            "required": ["thread_id", "run_id", "part_id", "source"],
            "optional": [
                "project_id", "conversation_id", "channel_id", "request_id",
                "message_id", "member_id", "target_member_id", "provider",
                "backend", "provider_session_id", "kind", "state", "delta",
                "content", "seq", "refs",
            ],
        },
        "agent.session.part.completed": {
            "required": ["thread_id", "run_id", "part_id", "source"],
            "optional": [
                "project_id", "conversation_id", "channel_id", "request_id",
                "message_id", "member_id", "target_member_id", "provider",
                "backend", "provider_session_id", "kind", "state", "title",
                "summary", "content", "seq", "refs",
            ],
        },
        "agent.session.part.failed": {
            "required": ["thread_id", "run_id", "part_id", "source", "reason"],
            "optional": [
                "project_id", "conversation_id", "channel_id", "request_id",
                "message_id", "member_id", "target_member_id", "provider",
                "backend", "provider_session_id", "kind", "state", "title",
                "summary", "content", "seq", "refs",
            ],
        },
        "provider.permission.snapshot.recorded": {
            "required": ["run_id", "thread_id", "backend", "permission_profile", "snapshot", "source"],
            "optional": [
                "project_id",
                "conversation_id",
                "provider_session_id",
                "drift",
                "runtime_snapshot_ref",
                "snapshot_ref",
            ],
        },
        "provider.permission.snapshot.drift": {
            "required": ["run_id", "thread_id", "backend", "status", "items", "source"],
            "optional": ["provider_session_id"],
        },
        "channel.member.connected": {
            "required": ["channel_id", "member_id", "source"],
            "optional": [
                "thread_id",
                "provider_session_id",
                "provider_binding_id",
                "remote_agent_id",
                "worker_session_id",
                "backing_worker_session_id",
                "provider",
                "backend",
                "channel_role",
                "visibility_profile",
                "permission_profile",
                "capabilities",
            ],
        },
        "channel.member.resumed": {
            "required": ["channel_id", "member_id", "source"],
            "optional": ["thread_id", "backing_worker_session_id"],
        },
        "channel.member.suspended": {
            "required": ["channel_id", "member_id", "source"],
            "optional": ["thread_id", "reason"],
        },
        "channel.member.removed": {
            "required": ["channel_id", "member_id", "source"],
            "optional": ["thread_id", "reason"],
        },
        "channel.member.permissions.updated": {
            "required": ["channel_id", "member_id", "permissions", "source"],
            "optional": ["thread_id", "reason"],
        },
        "channel.member.visibility.updated": {
            "required": ["channel_id", "member_id", "visibility_profile", "source"],
            "optional": ["thread_id", "channel_role", "role_context_ref", "reason"],
        },
        "channel.message.posted": {
            "required": [*base, "message_id", "text"],
            "optional": [
                "schema_version",
                "member_id",
                "role",
                "mentions",
                "mention_tokens",
                "refs",
                "text_preview",
                "body_ref",
                "body_sha256",
                "body_byte_count",
            ],
        },
        "channel.attachment.uploaded": {
            "required": [*base, "attachment_id", "message_id"],
            "optional": [
                "member_id",
                "artifact_id",
                "name",
                "filename",
                "mime",
                "content_type",
                "size",
                "bytes",
                "hash",
                "sha256",
                "uri",
                "refs",
            ],
        },
        "channel.artifact.proposed": {
            "required": [*base, "artifact_id"],
            "optional": [
                "message_id",
                "member_id",
                "target_member_id",
                "run_id",
                "request_id",
                "task_id",
                "name",
                "filename",
                "kind",
                "path",
                "uri",
                "hash",
                "sha256",
                "mime",
                "content_type",
                "size",
                "bytes",
                "summary",
                "provenance",
                "refs",
                "reason",
            ],
        },
        "channel.artifact.attached": {
            "required": [*base, "artifact_id"],
            "optional": [
                "message_id",
                "member_id",
                "target_member_id",
                "run_id",
                "request_id",
                "task_id",
                "name",
                "filename",
                "kind",
                "path",
                "uri",
                "hash",
                "sha256",
                "mime",
                "content_type",
                "size",
                "bytes",
                "summary",
                "provenance",
                "refs",
            ],
        },
        "channel.artifact.rejected": {
            "required": [*base, "artifact_id", "reason"],
            "optional": [
                "message_id",
                "member_id",
                "target_member_id",
                "run_id",
                "request_id",
                "task_id",
                "name",
                "filename",
                "kind",
                "path",
                "uri",
                "hash",
                "sha256",
                "mime",
                "content_type",
                "size",
                "bytes",
                "summary",
                "provenance",
                "refs",
            ],
        },
        "channel.message.delivered": {
            "required": [*base, "message_id", "member_id"],
            "optional": ["worker_session_id", "provider_session_id"],
        },
        "channel.message.failed": {
            "required": [*base, "message_id", "member_id", "reason"],
            "optional": ["worker_session_id", "provider_session_id"],
        },
        "channel.message.read": {
            "required": [*base, "message_id", "member_id"],
            "optional": [],
        },
        "channel.history.cleared": {
            "required": ["channel_id", "thread_id", "source"],
            "optional": ["reason"],
        },
        "channel.mention.detected": {
            "required": [*base, "message_id", "target_member_id"],
            "optional": ["member_id"],
        },
        "channel.spine_review.requested": {
            "required": [
                *base,
                "request_id",
                "message_id",
                "target_member_id",
                "intent",
            ],
            "optional": [
                "schema_version",
                "member_id",
                "status",
                "allowed_outputs",
                "context_pack_id",
                "member_type",
                "backend",
                "provider",
                "channel_role",
                "visibility_profile",
                "permission_profile",
            ],
        },
        "channel.agent.reply.requested": {
            "required": [*base, "request_id", "message_id", "target_member_id"],
            "optional": [
                "member_id",
                "status",
                "queue_state",
                "context_pack_id",
                "member_type",
                "backend",
                "provider",
                "provider_binding_id",
                "channel_role",
                "visibility_profile",
                "permission_profile",
                "worker_session_id",
                "provider_session_id",
                "run_id",
                "provider_run_id",
                "run_generation",
            ],
        },
        "channel.agent.reply.started": {
            "required": [*base, "request_id", "target_member_id"],
            "optional": [
                "message_id",
                "provider_session_id",
                "provider_binding_id",
                "remote_agent_id",
                "worker_session_id",
                "context_pack_id",
                "run_id",
                "provider_run_id",
                "run_generation",
            ],
        },
        "channel.agent.reply.completed": {
            "required": [*base, "request_id", "target_member_id"],
            "optional": [
                "message_id",
                "provider_session_id",
                "provider_binding_id",
                "remote_agent_id",
                "worker_session_id",
                "context_pack_id",
                "reason",
                "run_id",
                "provider_run_id",
                "run_generation",
            ],
        },
        "channel.agent.reply.failed": {
            "required": [*base, "request_id", "target_member_id", "reason"],
            "optional": [
                "message_id",
                "provider_session_id",
                "provider_binding_id",
                "remote_agent_id",
                "worker_session_id",
                "context_pack_id",
                "run_id",
                "provider_run_id",
                "run_generation",
            ],
        },
        "channel.typing.started": {
            "required": [*base, "member_id"],
            "optional": ["request_id", "target_member_id", "run_id", "provider_run_id", "reason"],
        },
        "channel.typing.stopped": {
            "required": [*base, "member_id"],
            "optional": ["request_id", "target_member_id", "run_id", "provider_run_id", "reason"],
        },
        "channel.message.stream.started": {
            "required": [*base, "run_id"],
            "optional": ["request_id", "target_member_id", "part_id", "kind", "state", "content", "seq", "refs"],
        },
        "channel.message.stream.delta": {
            "required": [*base, "run_id"],
            "optional": ["request_id", "target_member_id", "part_id", "kind", "state", "delta", "content", "seq", "refs"],
        },
        "channel.message.stream.ended": {
            "required": [*base, "run_id"],
            "optional": ["request_id", "target_member_id", "part_id", "kind", "state", "content", "seq", "refs", "reason"],
        },
        "channel.context_pack.built": {
            "required": [*base, "context_pack_id", "target_member_id", "trigger_message_id"],
            "optional": [
                "summary",
                "message_refs",
                "artifact_refs",
                "report_refs",
                "visibility_profile",
                "permission_profile",
                "channel_role",
                "role_context_ref",
                "role_definition",
                "limits",
                "schema_version",
                "skill_refs",
                "routing_reason",
                "source",
                "context_pack_ref",
                "context_pack_sha256",
                "context_pack_byte_count",
                "message_ref_count",
                "artifact_ref_count",
                "report_ref_count",
                "refs",
            ],
        },
        "channel.context_pack.rejected": {
            "required": [*base, "context_pack_id", "target_member_id", "trigger_message_id", "reason"],
            "optional": ["visibility_profile", "channel_role", "limits"],
        },
        "channel.handoff.requested": {
            "required": [*base, "message_id", "member_id", "target_member_id", "reason"],
            "optional": ["depth", "round"],
        },
        "channel.handoff.accepted": {
            "required": [*base, "message_id", "member_id", "target_member_id"],
            "optional": ["reason", "depth", "round"],
        },
        "channel.handoff.rejected": {
            "required": [*base, "message_id", "member_id", "target_member_id", "reason"],
            "optional": ["depth", "round"],
        },
        "channel.state_update.posted": {
            "required": [*base, "status", "summary"],
            "optional": ["task_id", "run_id", "refs"],
        },
        "channel.discussion.mode.set": {
            "required": ["channel_id", "mode", "source"],
            "optional": [
                "thread_id",
                "max_rounds",
                "speaker_policy",
                "provider_capabilities",
                "default_responder_id",
            ],
        },
        "channel.synthesis.proposed": {
            "required": [
                "channel_id",
                "thread_id",
                "decision",
                "summary",
                "source",
            ],
            "optional": [
                "open_questions",
                "risks",
                "recommended_workflow",
                "evidence_refs",
                "confidence",
            ],
        },
        "channel.owner_report.requested": {
            "required": [*base, "owner_id"],
            "optional": ["member_id", "report_id", "period", "reason"],
        },
        "channel.owner_report.generated": {
            "required": [*base, "owner_id", "report_id", "summary"],
            "optional": [
                "member_id",
                "period",
                "decisions",
                "risks",
                "blockers",
                "workflow_status",
                "recommended_actions",
                "refs",
            ],
        },
        "channel.owner_report.delivered": {
            "required": [*base, "owner_id", "report_id"],
            "optional": ["member_id", "destination", "summary"],
        },
        "channel.automation_report.ingested": {
            "required": [*base, "report_id"],
            "optional": ["automation_id", "run_id", "summary", "artifact_ref", "refs"],
        },
    }


def workflow_invoke_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contracts for channel/workflow invocation."""
    return {
        "workflow.submit.requested": {
            "required": [
                "request_id",
                "kind",
                "task_id",
                "pattern_id",
                "config_ref",
                "workflow_prompt_ref",
                "workflow_input_manifest_ref",
                "requested_by",
                "source_refs",
            ],
            "optional": [
                "schema_version",
                "workflow_preflight_ref",
                "reason",
                "artifact_refs",
                "dry_run",
                "preflight_status",
            ],
        },
        "workflow.submit.accepted": {
            "required": ["request_id", "source_event_id"],
            "optional": [
                "run_id",
                "kind",
                "request_kind",
                "workflow_tier",
                "workflow_preflight_ref",
                "workflow_input_manifest_ref",
                "workflow_prompt_ref",
                "config_ref",
            ],
        },
        "workflow.submit.rejected": {
            "required": ["request_id", "source_event_id", "reason"],
            "optional": ["preflight_ref", "blockers"],
        },
        "workflow.invoke.requested": {
            "required": ["task_id", "pattern_id", "requested_by", "reason", "source", "source_refs"],
            "optional": [
                "channel_id",
                "thread_id",
                "dispatch_id",
                "scope",
                "target_ref",
                "expected_output",
                "risk",
                "synthesis_event_id",
                "open_questions",
                "workflow_run_id",
                "request_id",
                "run_id",
                "kind",
                "request_kind",
                "workflow_tier",
                "workflow_input_manifest_ref",
                "workflow_prompt_ref",
                "prompt_kind",
                "artifact_refs",
            ],
        },
        "workflow.invoke.accepted": {
            "required": ["task_id", "pattern_id", "source_event_id"],
            "optional": [
                "channel_id",
                "thread_id",
                "fanout_request_event_id",
                "source_refs",
                "workflow_run_id",
                "workflow_input_manifest_ref",
                "workflow_prompt_ref",
                "prompt_kind",
                "artifact_refs",
            ],
        },
        "workflow.invoke.rejected": {
            "required": ["task_id", "pattern_id", "source_event_id", "reason"],
            "optional": ["channel_id", "thread_id"],
        },
    }


def automation_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contracts for Project Automation projections."""
    base = ["automation_id", "project_id", "source"]
    return {
        "automation.run.started": {
            "required": [*base, "run_id", "trigger"],
            "optional": ["window", "cursor"],
        },
        "automation.run.completed": {
            "required": [*base, "run_id", "status"],
            "optional": ["window", "outputs", "source_events", "duration_seconds"],
        },
        "automation.run.failed": {
            "required": [*base, "run_id", "reason"],
            "optional": ["window", "source_events"],
        },
        "automation.run.skipped": {
            "required": [*base, "run_id", "reason"],
            "optional": ["window"],
        },
        "automation.alert.raised": {
            "required": [*base, "alert_id", "severity", "summary"],
            "optional": ["run_id", "source_events", "proposal_id"],
        },
        "automation.alert.resolved": {
            "required": [*base, "alert_id", "reason"],
            "optional": ["run_id"],
        },
        "automation.proposal.created": {
            "required": [*base, "proposal_id", "output_mode", "summary"],
            "optional": ["run_id", "action", "payload", "evidence_refs"],
        },
        "automation.proposal.accepted": {
            "required": [*base, "proposal_id"],
            "optional": ["run_id", "accepted_by"],
        },
        "automation.proposal.rejected": {
            "required": [*base, "proposal_id", "reason"],
            "optional": ["run_id", "rejected_by"],
        },
        "automation.proposal.applied": {
            "required": [*base, "proposal_id", "action_event_id"],
            "optional": ["run_id"],
        },
        "automation.proposal.failed": {
            "required": [*base, "proposal_id", "reason"],
            "optional": ["run_id"],
        },
    }


def assignment_event_schema_rules() -> dict[str, dict[str, Any]]:
    """Canonical payload contracts for assignment intent proposals."""
    return {
        "assignment.intent.proposed": {
            "required": ["proposal_id", "task_id", "dispatches"],
            "optional": [
                "project_id",
                "assignee_type",
                "assignee_id",
                "assignee_label",
                "role",
                "backend",
                "channel_id",
                "supervisor",
                "reason",
                "request",
            ],
        },
    }


# ---------------------------------------------------------------------------
# Internal validation walker
# ---------------------------------------------------------------------------


def _validate_against_rule(
    *,
    rule: EventSchemaRule,
    data: Mapping[str, Any],
    path: str,
    violations: list[SchemaViolation],
    event_type: str,
) -> None:
    """Mutating: appends SchemaViolation entries to ``violations``."""

    # 1. Required fields
    for key in rule.required:
        if key not in data:
            violations.append(SchemaViolation(
                event_type=event_type,
                field_path=f"{path}.{key}",
                code="missing_required",
                expected=f"{key} present",
                actual="missing",
            ))

    # 1.5 Non-empty fields (FIX-14)
    for key in rule.non_empty:
        if key not in data:
            continue  # missing 由 required 档负责
        value = data[key]
        empty = (
            (isinstance(value, (list, tuple, str)) and len(value) == 0)
            or value is None
        )
        if empty:
            violations.append(SchemaViolation(
                event_type=event_type,
                field_path=f"{path}.{key}",
                code="empty_required",
                expected=f"{key} non-empty",
                actual="empty",
            ))

    # 2. Enum constraints
    for field_name, allowed in rule.enum_constraints.items():
        if field_name not in data:
            continue  # missing — already covered by required (or optional)
        value = data[field_name]
        if not isinstance(value, str) or value not in allowed:
            violations.append(SchemaViolation(
                event_type=event_type,
                field_path=f"{path}.{field_name}",
                code="enum_mismatch",
                expected=f"one of {list(allowed)}",
                actual=str(value),
            ))

    # 3. Nested rules
    for nested_key, nested_rule in rule.nested_rules.items():
        if nested_key not in data:
            continue
        nested_data = data[nested_key]
        if not isinstance(nested_data, Mapping):
            violations.append(SchemaViolation(
                event_type=event_type,
                field_path=f"{path}.{nested_key}",
                code="type_mismatch",
                expected="object",
                actual=type(nested_data).__name__,
            ))
            continue
        _validate_against_rule(
            rule=nested_rule,
            data=nested_data,
            path=f"{path}.{nested_key}",
            violations=violations,
            event_type=event_type,
        )

    # 4. List item rules
    for list_key, item_rule in rule.list_item_rules.items():
        if list_key not in data:
            continue
        items = data[list_key]
        if not isinstance(items, list):
            violations.append(SchemaViolation(
                event_type=event_type,
                field_path=f"{path}.{list_key}",
                code="type_mismatch",
                expected="list",
                actual=type(items).__name__,
            ))
            continue
        for i, item in enumerate(items):
            if not isinstance(item, Mapping):
                violations.append(SchemaViolation(
                    event_type=event_type,
                    field_path=f"{path}.{list_key}[{i}]",
                    code="type_mismatch",
                    expected="object",
                    actual=type(item).__name__,
                ))
                continue
            _validate_against_rule(
                rule=item_rule,
                data=item,
                path=f"{path}.{list_key}[{i}]",
                violations=violations,
                event_type=event_type,
            )

    # 5. Conditional rule
    if (
        rule.conditional_trigger_field is not None
        and rule.conditional_rule is not None
    ):
        actual = data.get(rule.conditional_trigger_field)
        if isinstance(actual, str) and actual == rule.conditional_trigger_value:
            _validate_against_rule(
                rule=rule.conditional_rule,
                data=data,
                path=path,
                violations=violations,
                event_type=event_type,
            )


__all__ = [
    "EventSchemaRule",
    "EventSchemaRegistry",
    "SchemaViolation",
    "context_event_schema_rules",
    "progress_event_schema_rules",
    "fanout_request_schema_rules",
    "channel_event_schema_rules",
    "user_message_unrouted_schema_rules",
    "workflow_invoke_schema_rules",
    "automation_event_schema_rules",
    "assignment_event_schema_rules",
]
