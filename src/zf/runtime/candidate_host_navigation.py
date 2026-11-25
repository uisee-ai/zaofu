"""Preview exact, authorized host observation locators before Candidate execution guidance."""

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.candidate_host_consumption import available_host_receipts
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def render_host_navigation(
    state: Path, manifest: Mapping[str, Any], payload: Mapping[str, Any], *, role_name: str
) -> str:
    actor = str(payload.get("role_instance") or "")
    attempt = str(manifest.get("attempt_id") or "")
    if not actor or not attempt or not payload.get("operation_id"):
        return ""
    sources = [
        s
        for s in manifest.get("sources", [])
        if "$" in (s.get("allowed_paths") or [])
        and artifact_access_allowed(
            s.get("access_scope"), actor=actor, role=role_name or actor, purpose="candidate-verify"
        )
    ]
    index = next((s for s in sources if s["source_id"] == "candidate-host-execution"), None)
    if not index:
        return ""
    rows = available_host_receipts(state, {**payload, "candidate_host_evidence_ref": index})
    if not rows:
        return ""
    pinned = {(s["ref"], s["sha256"]): s for s in sources}
    # Preview media joins, which require both original execution and observation. Other commands are searchable.
    preview = [r for r in rows if r.get("media_refs")] or rows[:1]
    lines = [
        "",
        "- Shared host observations for this Candidate attempt. Start with the exact command and its signed "
        "evidence before classifying a runner failure. The receipt's approval belongs to the producer. "
        "For a new risk or explicitly fresh observation, use its separately authorized runner.",
        f"  Open the WRC host index with `{zf_cli_cmd()} artifact index --attempt {shlex.quote(attempt)} "
        "--source-id candidate-host-execution`. Use sources[].read_command, then match receipts[].command_id. "
        "Review applicable assertions and media, then cite the signed receipt_id in host_execution_receipt_ids.",
    ]
    for row in preview:
        descriptor = row.get("receipt_ref") or {}
        source = pinned.get((descriptor.get("ref"), descriptor.get("sha256")))
        if not source or not row.get("receipt_id"):
            continue
        try:
            receipt = hydrate_sidecar_ref(state, source).payload
        except (ValueError, OSError):
            continue
        if (
            not isinstance(receipt, dict)
            or receipt.get("command_id") != row.get("command_id")
            or receipt.get("target_commit") != payload.get("target_commit")
            or receipt.get("operation_id") != row.get("source_operation_id")
            or receipt.get("active_attempt_id") != row.get("source_attempt_id")
            or not receipt.get("command")
        ):
            continue
        media = [
            s["source_id"]
            for m in row.get("media_refs", [])
            if (s := pinned.get((m.get("ref"), m.get("sha256"))))
        ]
        fields = [
            f"command_id={row['command_id']}",
            f"command={receipt['command']}",
            f"receipt_id={row['receipt_id']}",
            f"origin={receipt.get('execution_origin') or 'unspecified'}",
            f"approval_ref={receipt.get('approval_ref') or 'unspecified'}",
            f"receipt_source={source['source_id']}",
        ]
        if media:
            fields.append("media_sources=" + ", ".join(media))
        lines.append("  " + " | ".join(json.dumps(field, ensure_ascii=False)[1:-1] for field in fields))
        lookup = shlex.join(["artifact", "index", "--attempt", attempt, "--source-id", source["source_id"]])
        lines.append(f"  Original receipt: `{zf_cli_cmd()} {lookup}`.")

    return "\n".join(lines)
