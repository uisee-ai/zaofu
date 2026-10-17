"""Expand agent-authored exact edits against a bound, complete PRD source."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.channel_sidecar import hydrate_channel_context_pack_payload
from zf.runtime.channel_prd_quality import contribution_ledgers
from zf.runtime.sidecar_refs import SidecarRefError


def expand_prd_composition(
    synthesis: dict[str, Any], *, channel: dict[str, Any],
    request: dict[str, Any], state_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Resolve only this request's immutable source; never read an author path."""
    if "prd_composition" not in synthesis:
        return synthesis, ""
    if "prd_markdown" in synthesis:
        return synthesis, "provide either prd_markdown or prd_composition, not both"
    recipe = synthesis["prd_composition"]
    if not isinstance(recipe, dict):
        return synthesis, "prd_composition must be an object"
    pack, error = _bound_prd_context(channel, request, state_dir)
    if error:
        return synthesis, error
    file_source = "source_ref" in recipe
    if file_source and ("source_message_id" in recipe or "source_message_digest" in recipe):
        return synthesis, "prd_composition must select exactly one source identity"
    identity_key = "artifact_ref" if file_source else "message_id"
    digest_key = "artifact_digest" if file_source else "message_body_digest"
    identity = recipe.get("source_ref" if file_source else "source_message_id")
    digest = str(recipe.get("source_digest" if file_source else "source_message_digest") or "").removeprefix("sha256:")
    kinds = {"channel_prd_source"} if file_source else {"requirement", "owner_input"}
    sources = [row for row in pack.get("semantic_source_documents") or []
               if isinstance(row, dict) and row.get(identity_key) == identity
               and row.get("kind") in kinds and row.get(digest_key) == digest and digest]
    if len(sources) != 1 or not any(
        row.get(identity_key) == identity and row.get(digest_key) == digest and row.get("kind") in kinds
        for row in pack.get("semantic_source_manifest") or [] if isinstance(row, dict)
    ):
        return synthesis, "prd_composition source identity/digest is not in the bound manifest"
    content = sources[0].get("content")
    if not isinstance(content, str):
        return synthesis, "prd_composition source body is unavailable"
    if file_source:
        # A selected file is already the product document: preserve its full
        # body, never silently select a partial range from that snapshot.
        if "start_anchor" in recipe or "end_anchor" in recipe:
            return synthesis, "file-source composition preserves the complete file; omit anchors"
        body = content
    else:
        start, end = recipe.get("start_anchor"), recipe.get("end_anchor")
        if (not isinstance(start, str) or not start or not isinstance(end, str) or not end
                or content.count(start) != 1 or content.count(end) != 1):
            return synthesis, "prd_composition anchors must each match exactly once"
        left, right = content.index(start), content.index(end) + len(end)
        if left >= right or content.index(end) < left:
            return synthesis, "prd_composition source anchors are reversed"
        body = content[left:right]
    edits = recipe.get("edits")
    appendix = recipe.get("appendix_markdown")
    if not isinstance(edits, list) or not isinstance(appendix, str):
        return synthesis, "prd_composition requires edits array and appendix_markdown string"
    replacements: list[tuple[int, int, str]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            return synthesis, "prd_composition edits must be objects"
        old, new = edit.get("old"), edit.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str) or body.count(old) != 1:
            return synthesis, "prd_composition edit.old must match exactly once in the selected source"
        pos = body.index(old)
        replacements.append((pos, pos + len(old), new))
    replacements.sort()
    if any(previous[1] > current[0] for previous, current in zip(replacements, replacements[1:])):
        return synthesis, "prd_composition edits overlap"
    for begin, finish, text in reversed(replacements):
        body = body[:begin] + text + body[finish:]
    if appendix.strip():
        body = body.rstrip() + "\n\n" + appendix.strip()
    return {**synthesis, "prd_markdown": body}, ""


def _bound_prd_context(channel: dict[str, Any], request: dict[str, Any], state_dir: Path) -> tuple[dict[str, Any], str]:
    packs = channel.get("context_packs") or {}
    rows = packs.values() if isinstance(packs, dict) else packs
    pack = next((row for row in rows if isinstance(row, dict)
                 and row.get("context_pack_id") == request.get("context_pack_id")), {})
    try:
        pack = hydrate_channel_context_pack_payload(state_dir, pack, strict=True)
    except SidecarRefError as exc:
        return {}, f"PRD context cannot be verified: {exc.code}"
    if (not pack or pack.get("semantic_source_complete") is not True
            or pack.get("channel_id") != channel.get("channel_id")
            or pack.get("thread_id") != str(request.get("thread_id") or "main")):
        return {}, "PRD requires the bound complete source context"
    return pack, ""


def expand_prd_source_attestation(
    synthesis: dict[str, Any], *, channel: dict[str, Any],
    request: dict[str, Any], state_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Expand an explicit read-all declaration for exactly one immutable pack."""
    if "source_attestation" not in synthesis:
        return synthesis, ""
    declaration = synthesis["source_attestation"]
    fields = ("consumed_message_digests", "consumed_contribution_refs", "consumed_contribution_digests")
    if not isinstance(declaration, dict) or declaration.get("all_required_sources_read") is not True:
        return synthesis, "source_attestation must explicitly declare all_required_sources_read=true"
    if any(field in synthesis for field in fields):
        return synthesis, "source_attestation replaces all three consumed_* lists; do not provide both"
    pack, error = _bound_prd_context(channel, request, state_dir)
    if error:
        return synthesis, error
    digest = str(pack.get("context_pack_sha256") or "").removeprefix("sha256:")
    if (not digest or declaration.get("context_pack_id") != request.get("context_pack_id")
            or str(declaration.get("context_pack_sha256") or "").removeprefix("sha256:") != digest):
        return synthesis, "source_attestation identity/digest must match the bound immutable context pack"
    artifacts = [row for row in pack.get("contribution_index") or []
                 if isinstance(row, dict) and row.get("contract_status") == "structured"]
    artifacts += [row for row in pack.get("cross_review_index") or []
                  if isinstance(row, dict) and row.get("status") == "completed"]
    # Use only identities from the attested pack. The existing current-source
    # gate still rejects newly required artifacts absent from this snapshot.
    consumed_refs = list(dict.fromkeys(str(row["artifact_ref"]) for row in artifacts if row.get("artifact_ref")))
    ledgers = contribution_ledgers(pack, consumed_refs)
    return {**synthesis,
            "consumed_message_digests": list(pack.get("semantic_source_required_digests") or []),
            "consumed_contribution_refs": consumed_refs,
            "consumed_contribution_digests": list(dict.fromkeys(str(row["artifact_digest"]) for row in artifacts if row.get("artifact_digest"))),
            **ledgers}, ""
