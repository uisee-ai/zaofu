"""Evidence-bound synthesis portions; the existing event ledger owns receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import EventLog
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.channel_synthesis_parts import PARTS, SynthesisPart, part_error

SCHEMA = "channel.synthesis-part.v1"


def binding_for(pack: dict, member: dict, message: dict) -> dict:
    refs = message.get("refs") or {}
    return {"channel_id": pack.get("channel_id"), "thread_id": pack.get("thread_id") or "main",
            "member_id": member.get("member_id"), "input_sha256": (refs.get("prd_input_snapshot") or {}).get("sha256"),
            "source_manifest_digest": pack.get("semantic_source_manifest_digest"),
            "question_ledger_digest": pack.get("question_ledger_digest"),
            "profile_digest": pack.get("profile_digest"), "permission_digest": pack.get("permission_digest"),
            "skill_set_digest": pack.get("skill_set_digest"),
            "method_digest": method_digest(pack),
            "role": {key: member.get(key) for key in ("backend", "channel_role", "permission_profile", "thinking_level")},
            "selected_source": {k: (refs.get("prd_source_file") or {}).get(k) for k in ("ref", "sha256")},
            "repair": refs.get("synthesis_contract_error", ""), "version": SCHEMA}


def fingerprint(binding: dict) -> str:
    return hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def method_digest(pack: dict) -> str:
    return fingerprint({key: pack.get(key) for key in ("role_definition", "role_context_ref", "skill_guidance")})


def request_lineage(events: list, *, channel_id: str, thread_id: str, request_id: str) -> set[str]:
    requests = {e.payload.get("request_id"): e for e in events
                if e.type == "channel.synthesis.requested" and e.payload.get("channel_id") == channel_id
                and str(e.payload.get("thread_id") or "main") == thread_id}
    lineage: set[str] = set()
    cursor = request_id
    original_input = None
    while cursor and cursor not in lineage and len(lineage) < 32:
        event = requests.get(cursor)
        if event is None:
            break
        value = (event.payload.get("prd_input_snapshot") or {}).get("sha256")
        if original_input is None:
            original_input = value
        if not value or value != original_input:
            break
        lineage.add(cursor)
        cursor = str(event.payload.get("retry_of_request_id") or "")
    return lineage


def load_parts(state_dir: Path, *, binding: dict, request_id: str, context_digest: str) -> dict[str, dict]:
    events = EventLog(state_dir / "events.jsonl").read_all()
    lineage = request_lineage(events, channel_id=binding["channel_id"], thread_id=binding["thread_id"], request_id=request_id)
    retired: set[str] = set()
    candidates: dict[str, dict] = {}
    generations: dict[str, int] = {}
    spec_by_name = {part.name: part for part in PARTS}
    legacy_binding = {key: value for key, value in binding.items() if key != "method_digest"}
    context_receipts = {e.payload.get("context_pack_sha256"): e.payload for e in events
                        if e.type == "channel.context_pack.built" and e.payload.get("channel_id") == binding["channel_id"]
                        and str(e.payload.get("thread_id") or "main") == binding["thread_id"]}
    for event in events:
        payload = event.payload or {}
        if payload.get("channel_id") != binding["channel_id"]:
            continue
        # Agent session thread_id is the provider conversation, not the Channel
        # thread. Its business scope is verified below through the durable
        # synthesis lineage and the complete checkpoint binding.
        if not event.type.startswith("agent.session.") and str(payload.get("thread_id") or "main") != binding["thread_id"]:
            continue
        run_id = str(payload.get("run_id") or "")
        generation = int(payload.get("run_generation") or 1)
        if event.type == "agent.session.run.started":
            generations[run_id] = max(generations.get(run_id, 1), generation)
        if event.type in {"channel.synthesis.cancelled", "channel.synthesis.recovered"}:
            retired.add(str(payload.get("request_id") or ""))
        activity = ((payload.get("refs") or {}).get("raw") or {}).get("activity") or {}
        origin = str(activity.get("synthesis_request_id") or "")
        descriptor = activity.get("checkpoint")
        if (event.type != "agent.session.part.delta" or payload.get("source") != "channel.headless"
                or origin not in lineage or origin in retired or not isinstance(descriptor, dict)
                or payload.get("target_member_id") != binding["member_id"]
                or generation < generations.get(run_id, 1)
                or activity.get("input_fingerprint") not in {fingerprint(binding), fingerprint(legacy_binding)}):
            continue
        body = hydrate_sidecar_ref(state_dir, descriptor, purpose="synthesis-part-resume", actor="channel-runtime").payload
        if isinstance(body, dict) and body.get("binding") == legacy_binding:
            # Earlier in-flight v1 portions did not include the method digest.
            # Recover it only from that portion's original verified context,
            # never from today's role file or a self-declared checkpoint field.
            from zf.runtime.channel_sidecar import hydrate_channel_context_pack_payload
            original = context_receipts.get(body.get("context_digest"))
            if not original:
                continue
            pack = hydrate_channel_context_pack_payload(state_dir, original, strict=True)
            if (pack.get("semantic_source_complete") is not True or method_digest(pack) != binding["method_digest"]):
                continue
            stored_binding = legacy_binding
        else:
            stored_binding = binding
        if (not isinstance(body, dict) or body.get("schema_version") != SCHEMA or body.get("binding") != stored_binding
                or body.get("origin_request_id") not in lineage):
            raise SidecarRefError("scope_mismatch", "Synthesis checkpoint does not match the frozen inputs")
        name = body.get("part")
        if name not in spec_by_name:
            raise SidecarRefError("payload_invalid", "Synthesis checkpoint has invalid fields")
        if part_error(spec_by_name[name], body.get("fields")):
            # Intact but invalid under the current schema: regenerate this
            # portion only. A content digest never turns an invalid draft valid.
            continue
        if name == "finalize" and (body.get("origin_request_id") != request_id or body.get("context_digest") != context_digest):
            continue  # The final read attestation must be authored for THIS context.
        if body.get("context_digest") != context_digest:
            origin = context_receipts.get(body.get("context_digest")) or {}
            identities = (body.get("context_digest"), origin.get("context_pack_id"))
            serialized = json.dumps(body["fields"], ensure_ascii=False)
            if any(identity and identity in serialized for identity in identities):
                # A business portion may carry an active context-bound baseline
                # too. Regenerate it; never rewrite the Agent's old claims.
                continue
        if name in candidates and candidates[name] != body["fields"]:
            raise SidecarRefError("hash_mismatch", "Conflicting synthesis checkpoint receipts")
        candidates[name] = body["fields"]
    return candidates


def save_part(state_dir: Path, *, spec: SynthesisPart, fields: dict, binding: dict,
              request_id: str, context_digest: str) -> dict:
    error = part_error(spec, fields)
    if error:
        raise ValueError(error)
    body = {"schema_version": SCHEMA, "binding": binding, "origin_request_id": request_id,
            "context_digest": context_digest, "part": spec.name, "fields": fields}
    digest = fingerprint(body)
    owner = fingerprint({"channel": binding["channel_id"], "thread": binding["thread_id"], "request": request_id})
    return write_sidecar_json(state_dir, f"channels/synthesis-parts/{owner}/{spec.name}-{digest}.json", body,
                              kind="channel_synthesis_part", schema_version=SCHEMA, created_by="channel-runtime",
                              access_scope={"channel_id": binding["channel_id"], "thread_id": binding["thread_id"],
                                            "request_id": request_id}, required=True)
