from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import (
    ChannelAgentProfileConfig,
    ChannelConfig,
    ProjectConfig,
    ZfConfig,
)
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_contract_artifacts import normalize_synthesis_next_round
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.channel_synthesis_reactor import (
    react_channel_consensus_proposed,
)
from zf.runtime.channel_templates import (
    TEMPLATE_VERSION,
    materialize_channel_template,
    template_digest,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.control_actions_plan_apply_helpers import (
    channel_plan_discussion_seed,
)
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_REQUESTED_EVENT,
    plan_requirement_digest,
)
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.web.channel_setup_plan import normalize_channel_setup_submit_payload
from zf.web.plan_extraction import extract_plan_request


def _runtime(tmp_path: Path, *, config: ZfConfig | None = None):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
        config=config,
    )
    return state_dir, log, writer, service


def _execute(service, writer, action: str, payload: dict):
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_legacy_channel_setup_plan_uses_explicit_origin_fallback():
    seed, legacy, error = channel_plan_discussion_seed(
        {"subject_type": "channel_setup"},
        "Legacy canonical requirement.",
    )

    assert seed == "Legacy canonical requirement."
    assert legacy is True
    assert error == ""


def test_template_role_skill_mappings_are_method_scoped():
    participant = "skills/zf-channel-discussion-participant/SKILL.md"
    synthesizer = "skills/zf-channel-discussion-synthesizer/SKILL.md"
    for template_id in (
        "prd-clarification",
        "research-review",
        "architecture-review",
        "quick-change",
        "incident-triage",
    ):
        materialized, error = materialize_channel_template(
            template_id,
            overrides={"backend": "fake"},
        )
        assert error == ""
        assert materialized is not None
        actual = {
            member["channel_role"]: member["skill_refs"]
            for member in materialized["members"]
        }
        assert all(participant in refs for refs in actual.values())
        assert all(
            "skills/grill/SKILL.md" not in refs
            for refs in actual.values()
        )
        synth_role = materialized["discussion"]["synthesizer"]
        assert synthesizer in actual[synth_role]

    research, error = materialize_channel_template(
        "research-review",
        overrides={"backend": "fake"},
    )
    assert error == ""
    assert research is not None
    research_refs = {
        ref
        for member in research["members"]
        for refs in [member["skill_refs"]]
        for ref in refs
    }
    assert "skills/zf-research-fanout-trigger/SKILL.md" not in research_refs
    assert "skills/zf-refactor-plan-synth/SKILL.md" not in research_refs


def test_provider_prompts_filter_phase_skills_by_discussion_mode() -> None:
    from zf.runtime.channel_adapter import (
        _build_channel_prompt,
        _build_channel_system_prompt,
    )

    participant = "skills/zf-channel-discussion-participant/SKILL.md"
    synthesizer = "skills/zf-channel-discussion-synthesizer/SKILL.md"
    generic = "skills/zf-fmea-risk-gate/SKILL.md"
    member = {
        "member_id": "product_pm",
        "channel_role": "product_pm",
        "backend": "codex",
        "permission_profile": "read_only",
        "skill_refs": [participant, synthesizer, generic],
        "resolved_skill_refs": [
            {"logical_ref": ref, "resolved_path": f"/tmp/{index}/SKILL.md"}
            for index, ref in enumerate((participant, synthesizer, generic))
        ],
    }
    message = {"text": "Review the requirement."}
    request = {"thread_id": "main", "target_member_id": "product_pm"}
    conversation = {"channel_id": "ch-prd", "discussion": {"mode": "conversation"}}
    multi_lens = {"channel_id": "ch-prd", "discussion": {"mode": "multi_lens"}}

    conversation_prompt = _build_channel_prompt(
        channel=conversation,
        member=member,
        message=message,
        request=request,
    )
    conversation_system = _build_channel_system_prompt(
        member,
        channel=conversation,
    )
    multi_lens_prompt = _build_channel_prompt(
        channel=multi_lens,
        member=member,
        message=message,
        request=request,
    )

    assert participant not in conversation_prompt
    assert synthesizer not in conversation_prompt
    assert participant not in conversation_system
    assert generic in conversation_prompt
    assert generic in conversation_system
    assert participant in multi_lens_prompt
    assert synthesizer in multi_lens_prompt


def test_prd_template_persists_version_digest_roles_and_discussion(
    tmp_path: Path,
):
    state_dir, _, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "prd-clarification",
            "channel_id": "ch-prd",
            "overrides": {
                "backend": "fake",
                "role_overrides": {
                    "security_reviewer": {"enabled": False},
                },
            },
        },
    )

    assert result["ok"] is True
    assert result["template_version"] == TEMPLATE_VERSION
    assert result["template_digest"] == template_digest("prd-clarification")
    assert len(result["materialization_digest"]) == 64
    channel = project_channel(state_dir, "ch-prd") or {}
    template = channel["scope"]["template"]
    assert template == {
        "id": "prd-clarification",
        "version": TEMPLATE_VERSION,
        "digest": template_digest("prd-clarification"),
        "materialization_digest": result["materialization_digest"],
        "writer_role": "product_pm",
        "writer_scope": ["docs/design/**", "docs/impl/**"],
    }
    members = {member["channel_role"]: member for member in channel["members"]}
    assert set(members) == {"product_pm", "arch", "critic", "synthesizer"}
    assert all(
        member["permission_profile"] == "read_only"
        for member in members.values()
    )
    assert channel["leader_member_id"] == "product_pm"
    assert "propose_workflow" in members["product_pm"]["permissions"]
    assert all(
        "propose_workflow" not in member["permissions"]
        for role, member in members.items()
        if role != "product_pm"
    )
    assert members["synthesizer"]["skill_refs"] == [
        "skills/zf-channel-discussion-participant/SKILL.md",
        "skills/zf-channel-discussion-synthesizer/SKILL.md",
    ]
    assert channel["discussion"]["mode"] == "conversation"
    assert channel["discussion"]["synthesizer"] == "synthesizer"

    conflict = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "prd-clarification",
            "channel_id": "ch-prd",
            "overrides": {
                "backend": "fake",
                "model": "different-model",
                "role_overrides": {
                    "security_reviewer": {"enabled": False},
                },
            },
        },
    )
    assert conflict["status"] == "conflict"


def test_template_default_uses_synthesis_adaptive_round_policy(
    tmp_path: Path,
) -> None:
    materialized, error = materialize_channel_template(
        "quick-change",
        overrides={"backend": "fake"},
    )

    assert error == ""
    assert materialized is not None
    assert materialized["discussion"]["max_rounds"] == 0
    assert materialized["discussion"]["max_rounds_explicit"] is False

    state_dir, _, writer, service = _runtime(tmp_path)
    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-adaptive-default",
            "mode": "multi_lens",
            "overrides": {"backend": "fake"},
        },
    )

    assert result["ok"] is True
    assert result["round_policy"] == "synthesis_adaptive"
    detail = project_channel(state_dir, "ch-adaptive-default") or {}
    assert detail["discussion"]["max_rounds_explicit"] is False


def test_template_profile_selection_is_slot_scoped_and_plan_pinned(
    tmp_path: Path,
) -> None:
    profile_skills = [
        "skills/zf-channel-discussion-participant/SKILL.md",
        "skills/zf-cr/SKILL.md",
    ]
    config = ZfConfig(
        project=ProjectConfig(name="channel-profile-selection"),
        channel=ChannelConfig(agent_profiles={
            "arch-specialist": ChannelAgentProfileConfig(
                revision=2,
                persona="Architecture specialist",
                display_name="Architecture specialist",
                channel_role="arch",
                provider="fake",
                backend="fake",
                skill_refs=profile_skills,
                visibility_ceiling="minimal",
                permission_ceiling="read_only",
            ),
            "critic-specialist": ChannelAgentProfileConfig(
                revision=1,
                persona="Critic specialist",
                display_name="Critic specialist",
                channel_role="critic",
                provider="fake",
                backend="fake",
                skill_refs=[
                    "skills/zf-channel-discussion-participant/SKILL.md"
                ],
                visibility_ceiling="reviewer",
                permission_ceiling="read_only",
            ),
        }),
    )
    raw_payload = {
        "template_id": "prd-clarification",
        "mode": "multi_lens",
        "overrides": {
            "backend": "fake",
            "role_overrides": {
                "arch": {"profile_id": "arch-specialist"},
            },
        },
    }
    submit_payload, details, error = normalize_channel_setup_submit_payload(
        raw_payload,
        config=config,
    )

    assert error == ""
    assert submit_payload["expected_profile_selection_digest"] == (
        details["profile_selection_digest"]
    )
    arch_profile = next(
        profile
        for profile in details["profiles"]
        if profile["member_id"] == "arch"
    )
    assert arch_profile["profile_id"] == "arch-specialist"
    assert arch_profile["skill_refs"] == profile_skills

    state_dir, _, writer, service = _runtime(tmp_path, config=config)
    created = _execute(
        service,
        writer,
        "channel-create-from-template",
        {**submit_payload, "channel_id": "ch-selected-profile"},
    )
    assert created["ok"] is True
    detail = project_channel(state_dir, "ch-selected-profile") or {}
    arch = next(
        member for member in detail["members"]
        if member["member_id"] == "arch"
    )
    assert arch["profile_id"] == "arch-specialist"
    assert arch["display_name"] == "Architecture specialist"
    assert arch["skill_refs"] == profile_skills
    assert arch["visibility_profile"] == "minimal"

    stale = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            **submit_payload,
            "channel_id": "ch-profile-stale",
            "expected_profile_selection_digest": "0" * 64,
        },
    )
    assert stale["status"] == "profile_selection_superseded"
    assert project_channel(state_dir, "ch-profile-stale") is None

    _, _, mismatch_error = normalize_channel_setup_submit_payload(
        {
            **raw_payload,
            "overrides": {
                "backend": "fake",
                "role_overrides": {
                    "arch": {"profile_id": "critic-specialist"},
                },
            },
        },
        config=config,
    )
    assert mismatch_error == "member channel_role cannot override its profile"


def test_channel_setup_plan_normalizes_optional_disabled_roles_alias() -> None:
    payload, details, error = normalize_channel_setup_submit_payload({
        "template_id": "prd-clarification",
        "mode": "multi_lens",
        "overrides": {
            "backend": "fake",
            "disabled_roles": ["security_reviewer", "security_reviewer"],
            "role_overrides": {
                "arch": {"backend": "fake"},
            },
        },
    })

    assert error == ""
    assert payload["overrides"] == {
        "backend": "fake",
        "role_overrides": {
            "arch": {"backend": "fake"},
            "security_reviewer": {"enabled": False},
        },
    }
    assert "disabled_roles" not in payload["overrides"]
    assert details["member_count"] == 4
    _, _, required_error = normalize_channel_setup_submit_payload({
        "template_id": "prd-clarification",
        "mode": "multi_lens",
        "overrides": {
            "backend": "fake",
            "disabled_roles": ["arch"],
        },
    })
    assert required_error == "required template role cannot be disabled: arch"


def test_synthesis_next_round_contract_is_explicit_and_bounded() -> None:
    default, default_error = normalize_synthesis_next_round(None)
    assert default_error == ""
    assert default == {
        "action": "finalize",
        "reason": "",
        "objective": "",
        "target_member_ids": [],
    }
    continued, continued_error = normalize_synthesis_next_round({
        "action": "continue",
        "reason": "Architecture and critic findings conflict.",
        "objective": "Resolve the replay boundary before finalizing.",
        "target_member_ids": ["arch", "critic"],
    })
    assert continued_error == ""
    assert continued["target_member_ids"] == ["arch", "critic"]
    _, invalid_error = normalize_synthesis_next_round({
        "action": "finalize",
        "reason": "This must be empty.",
    })
    assert invalid_error == (
        "next_round.finalize must not include reason, objective, or targets"
    )


def test_template_preflight_rejects_missing_skill_without_partial_channel(
    tmp_path: Path,
    monkeypatch,
):
    state_dir, log, writer, service = _runtime(tmp_path)
    monkeypatch.setattr(
        "zf.runtime.control_actions_channel_admin.resolve_builtin_skill_source",
        lambda _name: None,
    )

    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-missing-skill",
            "overrides": {"backend": "fake"},
        },
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_template"
    assert "could not be resolved" in result["reason"]
    assert project_channel(state_dir, "ch-missing-skill") is None
    assert not [
        event for event in log.read_all()
        if event.type == "channel.created"
        and event.payload.get("channel_id") == "ch-missing-skill"
    ]


def test_all_templates_materialize_every_skill_ref(tmp_path: Path):
    state_dir, _, writer, service = _runtime(tmp_path)

    for template_id in (
        "prd-clarification",
        "research-review",
        "architecture-review",
        "quick-change",
        "incident-triage",
    ):
        result = _execute(
            service,
            writer,
            "channel-create-from-template",
            {
                "template_id": template_id,
                "channel_id": f"ch-{template_id}",
                "overrides": {"backend": "fake"},
            },
        )
        assert result["ok"] is True, result
        materialized, error = materialize_channel_template(
            template_id,
            overrides={"backend": "fake"},
        )
        assert error == ""
        assert materialized is not None
        channel = project_channel(state_dir, f"ch-{template_id}")
        by_role = {
            member["channel_role"]: member
            for member in channel["members"]
        }
        for member in materialized["members"]:
            resolved = by_role[member["channel_role"]][
                "resolved_skill_refs"
            ]
            assert len(resolved) == len(member["skill_refs"])
            for descriptor in resolved:
                assert Path(descriptor["resolved_path"]).is_file()
                assert str(Path(descriptor["resolved_path"])).startswith(
                    str(state_dir / "runtime-skills")
                )
        assert not (tmp_path / "skills").exists()


def test_template_preflight_rejects_unknown_override_without_partial_channel(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-invalid",
            "overrides": {"arbitrary_yaml": {"roles": ["root"]}},
        },
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_template"
    assert project_channel(state_dir, "ch-invalid") is None
    assert not [
        event for event in log.read_all()
        if event.type == "channel.created"
        and event.payload.get("channel_id") == "ch-invalid"
    ]

    stale = _execute(
        service,
        writer,
        "channel-create-and-start",
        {
            "template_id": "quick-change",
            "channel_id": "ch-stale-plan",
            "message": "Do not run a changed template.",
            "expected_materialization_digest": "0" * 64,
            "overrides": {"backend": "fake"},
        },
    )
    assert stale["status"] == "template_superseded"
    assert project_channel(state_dir, "ch-stale-plan") is None


def test_quick_change_is_read_only_and_grants_only_leader_proposal():
    materialized, error = materialize_channel_template(
        "quick-change",
        overrides={"backend": "fake"},
    )

    assert error == ""
    assert materialized is not None
    assert all(
        member["permission_profile"] == "read_only"
        for member in materialized["members"]
    )
    assert materialized["leader_member_id"] == "tech_leader"
    workflow_proposers = [
        member["member_id"]
        for member in materialized["members"]
        if "propose_workflow" in member["permissions"]
    ]
    assert workflow_proposers == ["tech_leader"]

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={"writer_scope": []},
    )
    assert invalid is None
    assert "non-empty string list" in invalid_error

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={"budget": {"max_rounds": "unbounded"}},
    )
    assert invalid is None
    assert invalid_error == "budget.max_rounds must be an integer"

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={
            "budget": {
                "phase_deadline_seconds": {"unknown_phase": 60},
            },
        },
    )
    assert invalid is None
    assert invalid_error == "unsupported phase deadline: unknown_phase"


def test_template_discussion_mode_override_is_explicit_and_bounded():
    materialized, error = materialize_channel_template(
        "prd-clarification",
        overrides={"discussion_mode": "multi_lens"},
    )
    assert error == ""
    assert materialized is not None
    assert materialized["discussion"]["mode"] == "multi_lens"

    invalid, error = materialize_channel_template(
        "prd-clarification",
        overrides={"discussion_mode": "auto"},
    )
    assert invalid is None
    assert error == "discussion_mode must be conversation, clarification, or multi_lens"


def test_create_and_start_is_one_action_with_seeded_requirement_and_followup(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-and-start",
        {
            "template_id": "quick-change",
            "channel_id": "ch-auto",
            "thread_id": "main",
            "message": "Review and implement the requested API change.",
            "mode": "multi_lens",
            "overrides": {
                "backend": "fake",
                "budget": {"max_rounds": 4},
            },
        },
    )

    assert result["ok"] is True
    assert result["status"] == "started"
    assert result["channel_id"] == "ch-auto"
    assert result["member_count"] == 3
    assert result["participants"] == [
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    ]
    assert result["max_rounds"] == 4
    assert result["mode"] == "multi_lens"
    assert result["engine_mode"] == "fanout_then_synthesis"
    channel = project_channel(state_dir, "ch-auto") or {}
    assert channel["discussion"]["default_responder_id"] == "tech_leader"
    assert channel["discussion"]["mode"] == "multi_lens"
    assert channel["discussion"]["engine_mode"] == "fanout_then_synthesis"
    assert channel["discussions"]["main"]["state"] == "phase1_blind"
    reply_targets = {
        str(event.payload.get("target_member_id") or "")
        for event in log.read_all()
        if event.type == "channel.agent.reply.requested"
        and event.payload.get("message_id") == result["message_id"]
    }
    assert reply_targets == {
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    }
    assert result["reply_request_count"] == 3
    requirement = next(
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == result["message_id"]
    )
    assert hydrate_channel_message_text(
        state_dir,
        requirement.payload,
        strict=True,
    ) == "Review and implement the requested API change."
    completions = [
        event
        for event in log.read_all()
        if event.type == "runtime.action.completed"
        and event.payload.get("action") == "channel-create-and-start"
    ]
    assert len(completions) == 1
    assert completions[0].payload["mode"] == "multi_lens"
    assert completions[0].payload["engine_mode"] == (
        "fanout_then_synthesis"
    )
    assert completions[0].payload["reply_request_count"] == 3

    followup = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-auto",
            "thread_id": "main",
            "message": "Please narrow the implementation scope.",
        },
    )
    assert followup["ok"] is True
    assert followup["route"]["targets"] == ["tech_leader"]


def test_controlled_question_resolution_and_consensus_confirmation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    created = _execute(service, writer, "channel-create-from-template", {
        "template_id": "quick-change",
        "channel_id": "ch-owner-controls",
        "overrides": {"backend": "fake"},
    })
    assert created["ok"] is True
    writer.emit(
        "channel.question.opened",
        actor="tech_leader",
        correlation_id="ch-owner-controls",
        payload={
            "channel_id": "ch-owner-controls",
            "thread_id": "main",
            "question_id": "q-scope",
            "question": "Should the API remain backward compatible?",
            "category": "clarification",
            "asked_by": "tech_leader",
            "source": "test",
        },
    )

    resolved = _execute(service, writer, "channel-question-resolve", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "question_id": "q-scope",
        "resolution": "answered",
        "answer": "Yes, preserve the current API.",
    })

    assert resolved["ok"] is True
    question = project_channel(
        state_dir,
        "ch-owner-controls",
    )["open_questions"][0]
    assert question["status"] == "resolved"
    assert question["answer"] == "Yes, preserve the current API."

    writer.emit(
        "channel.consensus.proposed",
        actor="tech_leader",
        correlation_id="ch-owner-controls",
        payload={
            "channel_id": "ch-owner-controls",
            "thread_id": "main",
            "artifact_ref": "channel-artifacts/ch-owner-controls/prd.md",
            "artifact_digest": "b" * 64,
            "proposed_by": "tech_leader",
            "required_signers": ["tech_leader"],
            "source": "test",
        },
    )
    stale = _execute(service, writer, "channel-consensus-confirm", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "artifact_digest": "c" * 64,
    })
    confirmed = _execute(service, writer, "channel-consensus-confirm", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "artifact_digest": "b" * 64,
    })

    assert stale["status"] == "consensus_stale"
    assert confirmed["status"] == "confirmed"
    signed = [
        event
        for event in log.read_all()
        if event.type == "channel.consensus.signed"
        and event.payload.get("member_id")
        == project_channel(state_dir, "ch-owner-controls")["owner_actor_ref"]
    ]
    assert len(signed) == 1


def test_controlled_question_resolution_waits_for_canonical_dedup(
    tmp_path: Path,
) -> None:
    state_dir, _, writer, service = _runtime(tmp_path)
    created = _execute(service, writer, "channel-create-from-template", {
        "template_id": "quick-change",
        "channel_id": "ch-owner-dedup-gate",
        "overrides": {"backend": "fake"},
    })
    assert created["ok"] is True
    writer.emit(
        "channel.discussion.started",
        actor="runtime",
        correlation_id="ch-owner-dedup-gate",
        payload={
            "channel_id": "ch-owner-dedup-gate",
            "thread_id": "main",
            "state": "phase2_relay",
            "source": "test",
        },
    )
    writer.emit(
        "channel.discussion.phase.changed",
        actor="runtime",
        correlation_id="ch-owner-dedup-gate",
        payload={
            "channel_id": "ch-owner-dedup-gate",
            "thread_id": "main",
            "phase": "phase2_relay",
            "source": "test",
        },
    )
    writer.emit(
        "channel.question.opened",
        actor="tech_leader",
        correlation_id="ch-owner-dedup-gate",
        payload={
            "channel_id": "ch-owner-dedup-gate",
            "thread_id": "main",
            "question_id": "q-scope",
            "question": "Should the API remain backward compatible?",
            "category": "clarification",
            "asked_by": "tech_leader",
            "source": "test",
        },
    )
    writer.emit(
        "channel.question.dedup.requested",
        actor="runtime",
        correlation_id="ch-owner-dedup-gate",
        payload={
            "channel_id": "ch-owner-dedup-gate",
            "thread_id": "main",
            "request_id": "dedup-1",
            "source": "test",
        },
    )

    pending = _execute(service, writer, "channel-question-resolve", {
        "channel_id": "ch-owner-dedup-gate",
        "thread_id": "main",
        "question_id": "q-scope",
        "resolution": "answered",
        "answer": "Yes, preserve the current API.",
    })

    assert pending["ok"] is False
    assert pending["status"] == "question_consolidation_pending"
    assert project_channel(
        state_dir,
        "ch-owner-dedup-gate",
    )["open_questions"][0]["status"] == "open"

    writer.emit(
        "channel.question.dedup.applied",
        actor="runtime",
        correlation_id="ch-owner-dedup-gate",
        payload={
            "channel_id": "ch-owner-dedup-gate",
            "thread_id": "main",
            "request_id": "dedup-1",
            "source": "test",
        },
    )
    resolved = _execute(service, writer, "channel-question-resolve", {
        "channel_id": "ch-owner-dedup-gate",
        "thread_id": "main",
        "question_id": "q-scope",
        "resolution": "answered",
        "answer": "Yes, preserve the current API.",
    })

    assert resolved["status"] == "resolved"


def test_action_bound_plan_selection_creates_channel_members_and_starts(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)
    requirement_seed = writer.emit(
        "user.message",
        actor="web",
        task_id="TASK-PLAN",
        payload={
            "message": (
                "The migration must preserve Task contract refs and "
                "provider session continuity."
            ),
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    origin = writer.emit(
        "user.message",
        actor="web",
        task_id="TASK-PLAN",
        payload={
            "message": "基于上面的需求创建评审 Channel。",
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    request = extract_plan_request(
        """
        {
          "plan_request": {
            "header": "Channel setup",
            "id": "channel-setup",
            "question": "Which team should collaborate?",
            "discussion_seed": "The migration must preserve Task contract refs and provider session continuity.",
            "submit_action": "channel-create-and-start",
            "submit_label": "Create & start",
            "options": [
              {
                "id": "quick",
                "label": "Quick change (Recommended)",
                "recommended": true,
                "description": "Three implementation roles and four rounds.",
                  "submit_payload": {
                    "template_id": "quick-change",
                    "mode": "conversation",
                    "name": "Session registry migration",
                  "overrides": {
                    "backend": "fake",
                    "budget": {"max_rounds": 4}
                  }
                }
              },
              {
                "id": "architecture",
                "label": "Architecture review",
                "description": "Four review roles and six rounds.",
                  "submit_payload": {
                    "template_id": "architecture-review",
                    "mode": "multi_lens",
                    "overrides": {
                    "backend": "fake",
                    "budget": {"max_rounds": 6}
                  }
                }
              }
            ],
            "allow_other": false,
            "reason": "The role mix changes the review."
          }
        }
        """,
        plan_context={
            "project_id": "zaofu",
            "task_id": "TASK-PLAN",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
            "originating_message_event_id": origin.id,
            "originating_message_event_ids": [
                requirement_seed.id,
                origin.id,
            ],
            "requirement_digest": plan_requirement_digest([
                (
                    requirement_seed.id,
                    requirement_seed.payload["message"],
                ),
                (origin.id, origin.payload["message"]),
            ]),
        },
    )
    assert request is not None and request["valid"] is True
    plan_event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        task_id="TASK-PLAN",
        correlation_id="kanban:zaofu",
    )
    request["request_event_id"] = plan_event.id
    plan_event.payload = {
        "plan_request": request,
        "request": request,
    }
    writer.append(plan_event)

    result = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": plan_event.id,
                "request_id": request["request_id"],
                "revision": request["revision"],
                "question_id": request["question_id"],
                "option_id": "quick",
                "answer": "Quick change (Recommended)",
            },
        },
    )

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["applied_action"] == "channel-create-and-start"
    assert result["member_count"] == 3
    assert result["max_rounds"] == 4
    assert result["mode"] == "conversation"
    assert result["engine_mode"] == "manual_mention"
    assert result["reply_request_count"] == 1
    channel = project_channel(state_dir, result["channel_id"]) or {}
    assert channel["name"] == "Session registry migration"
    assert {member["channel_role"] for member in channel["members"]} == {
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    }
    requirement = next(
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("channel_id") == result["channel_id"]
    )
    assert hydrate_channel_message_text(
        state_dir,
        requirement.payload,
        strict=True,
    ) == "The migration must preserve Task contract refs and provider session continuity."
    assert requirement.payload["refs"]["plan_requirement_event_ids"] == [
        requirement_seed.id,
        origin.id,
    ]
    assert requirement.payload["refs"]["plan_requirement_digest"] == (
        request["requirement_digest"]
    )
    assert requirement.payload["refs"]["discussion_seed_legacy_fallback"] is False
    assert [
        event
        for event in log.read_all()
        if event.type == PLAN_ANSWERED_EVENT
        and event.payload.get("request_event_id") == plan_event.id
    ]

    repeated = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": plan_event.id,
                "request_id": request["request_id"],
                "revision": request["revision"],
                "question_id": request["question_id"],
                "option_id": "quick",
                "answer": "Quick change (Recommended)",
            },
        },
    )
    assert repeated["status"] == "already_applied"
    assert len([
        event
        for event in log.read_all()
        if event.type == "channel.created"
        and event.payload.get("channel_id") == result["channel_id"]
    ]) == 1


def test_unstructured_channel_reply_cannot_freeze_or_propose_synthesis(
    tmp_path: Path,
):
    state_dir, log, writer, _ = _runtime(tmp_path)
    channel = {
        "channel_id": "ch-strict",
        "scope": {"template": {"id": "quick-change"}},
        "discussions": {
            "main": {
                "state": "phase1_blind",
                "requirement_message_id": "msg-requirement",
            },
        },
    }
    request = {
        "thread_id": "main",
        "message_id": "msg-requirement",
        "target_member_id": "tech_leader",
    }
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request=request,
        message={"message_id": "msg-requirement", "refs": {}},
        reply="Plain prose without the required JSON contract.",
        reply_event_id="evt-reply-plain",
        actor="test",
        source="test",
    )
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            **request,
            "message_id": "msg-synthesis",
            "target_member_id": "tech_leader",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-strict"},
        },
        reply="Plain synthesis prose without the required JSON contract.",
        reply_event_id="evt-synth-plain",
        actor="test",
        source="test",
    )

    events = log.read_all()
    assert not [
        event for event in events
        if event.type in {
            "channel.questions.frozen",
            "channel.synthesis.proposed",
        }
    ]
    assert {
        event.payload.get("contract_status")
        for event in events
        if event.type == "channel.finding.recorded"
    } == {
        "invalid_missing_channel_contribution",
        "invalid_missing_channel_synthesis",
    }


def test_synthesis_open_questions_block_consensus_and_preserve_requirement(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, _ = _runtime(tmp_path)
    channel = {
        "channel_id": "ch-open-prd",
        "name": "Open PRD",
        "messages": [{
            "message_id": "msg-requirement",
            "thread_id": "main",
            "text": "SCENARIO-WITNESS original requirement",
        }],
        "discussions": {
            "main": {
                "requirement_message_id": "msg-requirement",
            },
        },
        "open_questions": [],
        "linked_events": [],
    }

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "message_id": "msg-synthesis",
            "target_member_id": "synthesizer",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-open"},
        },
        reply=(
            '{"channel_synthesis":{"title":"Draft PRD",'
            '"summary":"A draft with an owner gap.",'
            '"open_questions":["Which launch threshold is approved?"],'
            '"risks":[],"recommended_workflow":{},"confidence":"medium"}}'
        ),
        reply_event_id="evt-synth-open",
        actor="test",
        source="test",
    )

    events = log.read_all()
    synthesis = next(
        event
        for event in events
        if event.type == "channel.synthesis.proposed"
    )
    assert any(
        event.type == "channel.question.opened"
        and event.payload["question"]
        == "Which launch threshold is approved?"
        for event in events
    )
    assert not any(
        event.type == "channel.consensus.proposed"
        for event in events
    )
    artifact = (
        state_dir / synthesis.payload["artifact_ref"]
    ).read_text(encoding="utf-8")
    assert "SCENARIO-WITNESS original requirement" in artifact
    assert "- Which launch threshold is approved?" in artifact


def test_synthesis_continue_emits_a_sidecar_bound_selective_round(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, _ = _runtime(tmp_path)
    channel = {
        "channel_id": "ch-selective",
        "name": "Selective pass",
        "messages": [{
            "message_id": "msg-requirement",
            "thread_id": "main",
            "text": "Preserve deterministic replay across the next pass.",
        }],
        "discussions": {
            "main": {
                "discussion_id": "discussion-selective",
                "revision": 3,
                "context_digest": "ctx-selective",
                "requirement_message_id": "msg-requirement",
            },
        },
        "open_questions": [],
        "linked_events": [],
    }

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "message_id": "msg-synthesis",
            "target_member_id": "synthesizer",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-selective"},
        },
        reply=(
            '{"channel_synthesis":{"title":"Selective pass",'
            '"summary":"The architecture and critic evidence disagree.",'
            '"open_questions":[],"risks":[],"recommended_workflow":{},'
            '"confidence":"medium","next_round":{"action":"continue",'
            '"reason":"The two replay analyses conflict.",'
            '"objective":"Resolve replay invariants with exact evidence.",'
            '"target_member_ids":["arch","critic"]}}}'
        ),
        reply_event_id="evt-synth-selective",
        actor="test",
        source="test",
    )

    events = log.read_all()
    synthesis = next(
        event for event in events
        if event.type == "channel.synthesis.proposed"
    )
    proposed = next(
        event for event in events
        if event.type == "channel.discussion.next_round.proposed"
    )
    assert synthesis.payload["next_round"]["action"] == "continue"
    assert proposed.causation_id == synthesis.id
    assert proposed.payload["discussion_id"] == "discussion-selective"
    assert proposed.payload["expected_revision"] == 3
    assert proposed.payload["target_member_ids"] == ["arch", "critic"]
    assert not [
        event for event in events
        if event.type == "channel.consensus.proposed"
    ]


def test_discussion_start_dispatches_participants_and_synthesis_once(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)
    created = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-quick",
            "overrides": {"backend": "fake"},
        },
    )
    assert created["ok"] is True

    started = _execute(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": "ch-quick",
            "thread_id": "review-1",
            "message": "Review the requested change.",
            "mode": "multi_lens",
        },
    )

    assert started["status"] == "started"
    assert started["participants"] == ["tech_leader", "dev_reviewer", "qa_analyst"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        events = log.read_all()
        completed = [
            event for event in events
            if event.type == "channel.agent.reply.completed"
            and event.payload.get("thread_id") == "review-1"
        ]
        findings = [
            event for event in events
            if event.type == "channel.finding.recorded"
            and event.payload.get("thread_id") == "review-1"
        ]
        if len(completed) == 3 and len(findings) == 3:
            break
        time.sleep(0.02)
    assert len(completed) == 3
    assert len(findings) == 3
    assert {
        event.payload["target_member_id"] for event in completed
    } == {"tech_leader", "dev_reviewer", "qa_analyst"}

    advance_discussion(
        state_dir,
        writer,
        channel_id="ch-quick",
        thread_id="review-1",
        project_root=tmp_path,
    )
    advance_discussion(
        state_dir,
        writer,
        channel_id="ch-quick",
        thread_id="review-1",
        project_root=tmp_path,
    )
    synthesis_request = [
        event for event in log.read_all()
        if event.type == "channel.synthesis.requested"
        and event.payload.get("thread_id") == "review-1"
    ][-1]
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=tmp_path,
        config=None,
        openclaw_client=None,
    )
    EventReactorMixin._on_channel_synthesis_requested(host, synthesis_request)
    EventReactorMixin._on_channel_synthesis_requested(host, synthesis_request)

    events = log.read_all()
    synthesis_messages = [
        event for event in events
        if event.type == "channel.message.posted"
        and isinstance(event.payload.get("refs"), dict)
        and event.payload["refs"].get("synthesis_request_id")
        == synthesis_request.payload["request_id"]
    ]
    assert len(synthesis_messages) == 1
    syntheses = [
        event for event in events
        if event.type == "channel.synthesis.proposed"
        and event.payload.get("request_id") == synthesis_request.payload["request_id"]
    ]
    assert len(syntheses) == 1
    assert (
        state_dir / syntheses[0].payload["artifact_ref"]
    ).is_file()
    assert syntheses[0].payload["artifact_digest"]
    artifact = (
        state_dir / syntheses[0].payload["artifact_ref"]
    ).read_text(encoding="utf-8")
    for heading in (
        "## Source Requirement",
        "## Requirement",
        "## Decisions",
        "## Assumptions",
        "## Out of Scope",
        "## Acceptance Criteria",
        "## Risks",
        "## Open Questions",
        "## Recommended Workflow",
        "## Provenance",
    ):
        assert heading in artifact
    assert "Review the requested change." in artifact
    consensus = [
        event for event in events
        if event.type == "channel.consensus.proposed"
        and event.payload.get("artifact_digest")
        == syntheses[0].payload["artifact_digest"]
    ]
    assert len(consensus) == 1
    assert consensus[0].payload["required_signers"] == [
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    ]
    assert any(
        event.type == "channel.consensus.signed"
        and event.payload.get("member_id") == "tech_leader"
        for event in events
    )
    react_channel_consensus_proposed(host, consensus[0])
    react_channel_consensus_proposed(host, consensus[0])
    signed = {
        event.payload.get("member_id")
        for event in log.read_all()
        if event.type == "channel.consensus.signed"
        and event.payload.get("artifact_digest")
        == syntheses[0].payload["artifact_digest"]
    }
    assert signed == {"tech_leader", "dev_reviewer", "qa_analyst"}
    review_messages = [
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and isinstance(event.payload.get("refs"), dict)
        and event.payload["refs"].get("consensus_review_id")
    ]
    assert len(review_messages) == 2


def test_discussion_restart_closes_active_session_and_uses_fresh_trigger(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    created = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-restart",
            "overrides": {"backend": "fake"},
        },
    )
    assert created["ok"] is True
    first = _execute(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": "ch-restart",
            "thread_id": "main",
            "message": "Review the first requirement.",
            "mode": "conversation",
        },
    )
    assert first["status"] == "started"
    before = project_channel(state_dir, "ch-restart") or {}
    old_discussion_id = before["discussions"]["main"]["discussion_id"]

    restarted = _execute(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": "ch-restart",
            "thread_id": "main",
            "message": "Review the exact revised artifact.",
            "requirement_message_id": first["message_id"],
            "mode": "multi_lens",
            "restart": True,
        },
    )

    assert restarted["status"] == "restarted"
    assert restarted["message_id"] != first["message_id"]
    events = log.read_all()
    closed = [
        event
        for event in events
        if event.type == "channel.discussion.closed"
        and event.payload.get("reason") == "explicit_restart"
    ]
    assert len(closed) == 1
    assert closed[0].payload["discussion_id"] == old_discussion_id
    starts = [
        event
        for event in events
        if event.type == "channel.discussion.started"
        and event.payload.get("product_mode") == "multi_lens"
    ]
    assert len(starts) == 1
    assert starts[0].payload["discussion_id"] != old_discussion_id
    assert events.index(closed[0]) < events.index(starts[0])
    trigger = next(
        event
        for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == restarted["message_id"]
    )
    assert (
        trigger.payload["refs"]["source_requirement_message_id"]
        == first["message_id"]
    )
    detail = project_channel(state_dir, "ch-restart") or {}
    assert detail["discussions"]["main"]["state"] == "phase1_blind"
    assert detail["discussions"]["main"]["product_mode"] == "multi_lens"
    assert set(restarted["route"]["targets"]) == {
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    }
