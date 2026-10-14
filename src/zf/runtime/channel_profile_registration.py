"""Approved project Profile registration; semantic drafting stays with Kanban."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml

from zf.core.config.loader import load_config
from zf.core.config.schema import ChannelAgentProfileConfig
from zf.core.security.redaction import redact_text
from zf.core.skills.provenance import resolve_builtin_skill_source, resolve_skill_source
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.channel_contracts import normalize_provider, normalize_channel_skill_refs
from zf.runtime.channel_member_admission import complete_member_admission
from zf.runtime.channel_member_proposals import catalog_digest
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_profiles import public_channel_profile_catalog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_roles import ROLE_CONTEXT_MAX_CHARS, normalize_role_context_ref

ACTION = "channel-profile-register"
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_ROLE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_registration_payload(payload: dict[str, Any]) -> str:
    """Pure shape validation shared by proposal extraction and controlled apply."""
    if not _ID.fullmatch(str(payload.get("profile_id") or "")):
        return "profile_id must be a valid project Profile identifier"
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return "profile must be an object"
    if set(profile) - set(ChannelAgentProfileConfig.__dataclass_fields__):
        return "profile contains unsupported fields"
    for key in ("display_name", "persona", "channel_role", "provider", "role_context_ref"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            return f"profile.{key} is required"
    if not _ROLE.fullmatch(profile["channel_role"]):
        return "profile.channel_role must be a lowercase role identifier"
    if not normalize_provider(profile["provider"]) or profile["provider"] in {"runtime-role", "openclaw"}:
        return "profile requires a supported standalone Channel provider"
    if profile.get("backend") and normalize_provider(profile["backend"]) != normalize_provider(profile["provider"]):
        return "profile.backend must match its provider"
    if profile.get("permission_ceiling", "read_only") != "read_only":
        return "generated discussion Profiles require read_only permission_ceiling"
    if profile.get("visibility_ceiling", "minimal") not in {"minimal", "planner", "reviewer"}:
        return "generated Profile visibility must be minimal, planner or reviewer"
    if profile.get("revision", 1) != 1 or profile.get("lifecycle", "persistent") != "persistent":
        return "registration creates a new persistent revision-1 Profile"
    if not normalize_role_context_ref(profile["role_context_ref"]):
        return "role_context_ref must be project-local channel_roles/<role>.md"
    skills = profile.get("skill_refs", [])
    if not isinstance(skills, list) or not all(isinstance(ref, str) for ref in skills):
        return "profile.skill_refs must be a list"
    if len(skills) > 8 or normalize_channel_skill_refs(skills, max_refs=max(1, len(skills))) != skills:
        return "profile.skill_refs must be unique valid project Skill references"
    role = payload.get("role_markdown")
    if not isinstance(role, str) or not role.strip() or len(role) > ROLE_CONTEXT_MAX_CHARS:
        return f"role_markdown must contain 1..{ROLE_CONTEXT_MAX_CHARS} characters"
    if redact_text(json.dumps(profile, ensure_ascii=False) + role) != json.dumps(profile, ensure_ascii=False) + role:
        return "Profile proposal must not contain credentials"
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("base_config_digest") or "")):
        return "base_config_digest must bind the current project config"
    if not str(payload.get("reason") or "").strip():
        return "reason is required"
    refs = payload.get("source_refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r.strip() for r in refs):
        return "source_refs must contain the requirement evidence"
    if payload.get("channel_id"):
        for key in ("origin_thread_id", "member_id", "objective", "leader_member_id"):
            if not str(payload.get(key) or "").strip():
                return f"{key} is required for register-and-admit"
        if not _ID.fullmatch(str(payload["member_id"])):
            return "member_id must be a valid identifier"
        if type(payload.get("leader_revision")) is not int or payload["leader_revision"] < 1:
            return "leader_revision must bind the current Channel leader"
        if type(payload.get("required_reviewer")) is not bool:
            return "required_reviewer must be explicit"
    return ""


def registration_context(project_root: Path, state_dir: Path, config: Any) -> dict[str, Any]:
    """Read-only drafting facts. This is not a second Profile catalog."""
    path = project_root / "zf.yaml"
    if not path.is_file():
        return {"available": False}
    names = {"zf-channel-discussion-participant"}
    names.update(ref.split("/")[1] for p in config.channel.agent_profiles.values() for ref in p.skill_refs)
    for role in config.roles:
        names.update(role.skills)
    skills = []
    for name in sorted(names):
        if _skill_source(project_root, state_dir, config, name) is not None:
            skills.append(f"skills/{name}/SKILL.md")
    providers = {normalize_provider(p.provider or p.backend) for p in config.channel.agent_profiles.values()}
    providers.update(normalize_provider(role.backend) for role in config.roles)
    return {"base_config_digest": digest_text(path.read_text()), "skill_refs": skills,
            "providers": sorted(providers - {"", "runtime-role", "openclaw"}),
            "role_markdown_max_chars": ROLE_CONTEXT_MAX_CHARS,
            "action": ACTION, "permission_ceiling": "read_only"}


def _skill_source(root: Path, state: Path, config: Any, name: str) -> Path | None:
    return resolve_skill_source(project_root=root, state_dir=state, name=name, config=config) or resolve_builtin_skill_source(name)


def _child(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("config registration requires mapping nodes")
    return next((value for name, value in node.value if name.value == key), None)


def add_profile_yaml(text: str, profile_id: str, profile: dict[str, Any]) -> str:
    """Insert only the new config subtree, preserving unrelated YAML bytes."""
    documents = list(yaml.compose_all(text))
    roots = [d for d in documents if d is not None and
             (_child(d, "kind") is None or getattr(_child(d, "kind"), "value", "") == "ZfConfig")]
    if len(roots) != 1:
        raise ValueError("registration requires one unambiguous ZfConfig document")
    root = roots[0]
    spec = _child(root, "spec") if _child(root, "kind") else root
    if not isinstance(spec, yaml.MappingNode):
        raise ValueError("ZfConfig spec must be a mapping")
    channel = _child(spec, "channel")
    profiles = _child(channel, "agent_profiles") if channel else None
    node = profiles or channel or spec
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("Channel config must use a mapping")
    if profiles and _child(profiles, profile_id):
        raise ValueError("Profile identifier already exists in zf.yaml")
    addition: dict[str, Any] = {profile_id: profile}
    if not profiles:
        addition = {"agent_profiles": addition}
    if not channel:
        addition = {"channel": addition}
    if node.flow_style or not node.value:
        current = yaml.safe_load(text[node.start_mark.index:node.end_mark.index]) or {}
        current.update(addition)
        indent = node.start_mark.column
        body = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).rstrip()
        rendered = "\n" + "\n".join(" " * indent + line for line in body.splitlines()) + "\n"
        return text[:node.start_mark.index] + rendered + text[node.end_mark.index:]
    indent = node.value[0][0].start_mark.column
    pos = node.end_mark.index
    body = yaml.safe_dump(addition, allow_unicode=True, sort_keys=False)
    rendered = "".join(" " * indent + line + "\n" for line in body.rstrip().splitlines())
    return text[:pos] + ("\n" if pos and text[pos-1] != "\n" else "") + rendered + text[pos:]


def register_profile(service: Any, *, requested: Any, action: str,
                     requested_action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The sole write boundary: exact Kanban approval, config CAS, then admission."""
    try:
        if not payload.get("proposal_event_id"):
            raise ValueError("Profile registration requires an exact approved Kanban proposal")
        error = validate_registration_payload(payload)
        if error:
            raise ValueError(error)
        root = Path(service.project_root).resolve()
        path = root / "zf.yaml"
        if path.is_symlink():
            raise ValueError("zf.yaml must not be a symlink")
        with locked_path(service.state_dir / "locks" / "channel-profile-registration"):
            with locked_path(path):
                event = _apply_profile(service, requested, payload, root, path)
            # Config registration persists even if dispatch fails; retries resume admission.
            result = _admit(service, requested, payload, event)
        service._completed(requested=requested, event=event, action=action,
                           requested_action=requested_action, task_id=requested.task_id,
                           status="registered_and_admitted" if payload.get("channel_id") else "registered",
                           extra={"profile_id": payload["profile_id"], **result})
        return {"ok": True, "status": "registered_and_admitted" if payload.get("channel_id") else "registered",
                "profile_id": payload["profile_id"], "event_id": event.id, **result}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
                               task_id=requested.task_id, reason=str(exc), status_code=409,
                               status="profile_registration_failed")


def _origin(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("channel_id"):
        return {}
    channel = project_channel(service.state_dir, payload["channel_id"]) or {}
    if not channel.get("created_by_event"):
        raise ValueError("origin Channel does not exist")
    authority = channel_owner_authority_error(channel, actor=service.actor, capability="invite_member")
    if authority:
        raise ValueError(authority)
    if channel.get("leader_member_id") != payload["leader_member_id"] or int(channel.get("leader_revision") or 1) != payload["leader_revision"]:
        raise ValueError("origin Channel leader changed; regenerate the proposal")
    if payload["origin_thread_id"] not in channel.get("discussions", {}):
        raise ValueError("origin discussion thread does not exist")
    return channel


def _apply_profile(service: Any, requested: Any, payload: dict[str, Any], root: Path, path: Path):
    channel = _origin(service, payload)
    approval = payload["proposal_event_id"]
    profile_id = payload["profile_id"]
    profile = asdict(ChannelAgentProfileConfig(**payload["profile"]))
    role_path = root / profile["role_context_ref"]
    if (root / "channel_roles").is_symlink() or role_path.is_symlink() or not role_path.resolve().is_relative_to(root):
        raise ValueError("role prompt path is outside the project")
    events = service.writer.event_log.read_all()
    registered = next((e for e in events if e.type == "channel.profile.registered" and e.payload.get("approval_event_id") == approval), None)
    prepared = next((e for e in events if e.type == "channel.profile.registration.prepared" and e.payload.get("approval_event_id") == approval), None)
    old = path.read_text()
    current = load_config(path)
    if registered or (prepared and digest_text(old) == prepared.payload["target_config_digest"]):
        if profile_id not in current.channel.agent_profiles or asdict(current.channel.agent_profiles[profile_id]) != profile:
            raise ValueError("registered Profile has changed")
        if not role_path.is_file() or role_path.read_text() != payload["role_markdown"]:
            raise ValueError("registered role prompt has changed")
    else:
        if digest_text(old) != payload["base_config_digest"]:
            raise ValueError("project config changed after proposal; regenerate it")
        if profile_id in current.channel.agent_profiles:
            raise ValueError("Profile identifier already exists; reuse it instead")
        if role_path.exists() and role_path.read_text() != payload["role_markdown"]:
            raise ValueError("role prompt already exists with different content")
        if channel and any(m.get("member_id") == payload["member_id"] for m in channel.get("members", [])):
            raise ValueError("member_id already exists in the origin Channel")
        context = registration_context(root, service.state_dir, current)
        if profile["provider"] not in context["providers"]:
            raise ValueError("Provider is not configured for this project")
        for ref in profile["skill_refs"]:
            if _skill_source(root, service.state_dir, current, ref.split("/")[1]) is None:
                raise ValueError(f"Skill is unavailable: {ref}")
        candidate = add_profile_yaml(old, profile_id, profile)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", prefix=".zf-profile-check-", dir=root) as file:
            file.write(candidate)
            file.flush()
            validated = load_config(Path(file.name))
        before = asdict(current)
        after = asdict(validated)
        del after["channel"]["agent_profiles"][profile_id]
        if after != before:
            raise ValueError("Profile registration would change unrelated effective config")
        receipt = write_immutable_json_sidecar(service.state_dir, {
            "profile_id": profile_id, "profile": profile, "role_markdown": payload["role_markdown"],
            "base_config_digest": digest_text(old), "target_config_digest": digest_text(candidate),
            "approval_event_id": approval, "source_refs": payload["source_refs"],
        }, root="channel-profile-registration", kind="registration", schema_version="channel-profile-registration.v1",
            created_by=service.actor, source_event_id=requested.id)
        if prepared is None:
            prepared = service.writer.emit("channel.profile.registration.prepared", actor=service.actor, causation_id=requested.id,
                                           payload={"approval_event_id": approval, "profile_id": profile_id,
                                                    "target_config_digest": digest_text(candidate), "artifact_ref": receipt})
        atomic_write_text(role_path, payload["role_markdown"])
        atomic_write_text(path, candidate)
        current = load_config(path)
    service.config = current
    if registered:
        return registered
    return service.writer.emit("channel.profile.registered", actor=service.actor, causation_id=prepared.id,
                               payload={"approval_event_id": approval, "profile_id": profile_id,
                                        "revision": 1, "config_digest": digest_text(path.read_text()),
                                        "role_digest": digest_text(payload["role_markdown"]),
                                        "artifact_ref": prepared.payload["artifact_ref"], "source_refs": payload["source_refs"]})


def _admit(service: Any, requested: Any, payload: dict[str, Any], registered: Any) -> dict[str, Any]:
    if not payload.get("channel_id"):
        return {}
    channel = _origin(service, payload)
    proposal_id = "invite-profile-" + digest_text(payload["proposal_event_id"])[:20]
    profile = next(p for p in public_channel_profile_catalog(service.config) if p["profile_id"] == payload["profile_id"])
    artifact = registered.payload["artifact_ref"]
    proposal = {"channel_id": channel["channel_id"], "thread_id": payload["origin_thread_id"],
                "proposal_id": proposal_id, "member_id": payload["member_id"], "profile_id": payload["profile_id"],
                "leader_member_id": channel["leader_member_id"], "leader_revision": channel["leader_revision"],
                "reason": payload["objective"], "source_refs": payload["source_refs"],
                "required_reviewer": payload["required_reviewer"], "profile_revision": 1,
                "profile_catalog_digest": catalog_digest(profile), "channel_role": profile["channel_role"],
                "artifact_ref": artifact["ref"], "artifact_digest": artifact["sha256"],
                "source": "owner_approved_profile_registration"}
    events = service.writer.event_log.read_all()
    if not any(e.type == "channel.member.invitation.proposed" and e.payload.get("proposal_id") == proposal_id for e in events):
        service.writer.emit("channel.member.invitation.proposed", actor=service.actor, causation_id=registered.id, payload=proposal)
    invited = next((e for e in events if e.type == "channel.member.invited" and e.payload.get("invitation_proposal_id") == proposal_id), None)
    if invited:
        complete_member_admission(service, requested=requested, proposal=proposal, invited=invited)
    else:
        if any(m.get("member_id") == payload["member_id"] and m.get("status") != "rejected" for m in channel.get("members", [])):
            raise ValueError("member_id is already bound to another invitation")
        result = service._channel_invite_member(requested=requested, action=ACTION, requested_action=ACTION,
            invitation=proposal, payload={"channel_id": channel["channel_id"], "thread_id": payload["origin_thread_id"],
                "member_id": payload["member_id"], "profile_id": payload["profile_id"],
                "permission_profile": "read_only", "reason": payload["objective"]})
        if not result.get("ok"):
            raise ValueError(result.get("reason") or "Profile registered but member admission failed")
    return {"channel_id": channel["channel_id"], "thread_id": payload["origin_thread_id"],
            "member_id": payload["member_id"], "invitation_proposal_id": proposal_id}


class ChannelProfileRegistrationActionsMixin:
    def _channel_profile_register(self, **kwargs: Any) -> dict[str, Any]:
        return register_profile(self, **kwargs)
