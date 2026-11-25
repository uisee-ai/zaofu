"""Validate complete historical tool delivery, never model comprehension."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from zf.runtime.artifact_output_match import complete_output_observed, observable_outputs
from zf.runtime.artifact_source_versions import read_source_version
from zf.runtime.artifact_text_codec import decode_artifact_text, encode_selected
from zf.runtime.artifact_text_page import parse_artifact_page
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def delivered_rows(state_dir: Path, policy: dict, rows: list[dict], *,
                   allow_ephemeral_observations: bool = False) -> list[dict]:
    """Only verified rows contribute coverage; a later full read can fill gaps."""
    from zf.runtime.artifact_read_ledger import _manifest_ref_path, _select_json_path

    binding = policy["delivery_evidence"]
    manifest = hydrate_sidecar_ref(state_dir, binding["source_manifest_ref"]).payload
    if (manifest.get("attempt_id") != policy.get("attempt_id")
            or manifest.get("workflow_run_id") != policy.get("workflow_run_id")
            or manifest.get("dispatch_id") != binding.get("dispatch_id")):
        raise ValueError("input delivery manifest identity mismatch")
    sources = {(s["source_id"], s["artifact_id"]): s for s in manifest["sources"]}
    selected: dict[tuple[str, str, str], str] = {}
    cache: dict[tuple[str, str], Any] = {}

    def load(ref: dict) -> Any:
        key = (ref["ref"], ref["sha256"])
        if key not in cache:
            cache[key] = hydrate_sidecar_ref(state_dir, ref).payload
        return cache[key]

    result = []
    for row in rows:
        try:
            if (row.get("production_status") not in {"stdout_flushed", "mounted_file_read"}
                    or row.get("provider_delivery_status") != "observed_in_transcript"
                    or row.get("consumer_provider") != "codex"
                    or row.get("consumer_actor") != binding.get("consumer_actor")
                    or any(row.get(k) != policy.get(k) for k in ("workflow_run_id", "attempt_id"))
                    or any(row.get(k) != binding.get(k) for k in ("operation_id", "dispatch_id"))
                    or row.get("task_id", "") != manifest.get("task_id", "")):
                continue
            source = sources[(row["source_id"], row["artifact_id"])]
            if any(row.get(a) != source.get(b) for a, b in (
                ("artifact_ref", "ref"), ("artifact_sha256", "sha256"), ("occurrence_id", "occurrence_id"))):
                continue
            path = row.get("json_path") or "$"
            if "$" not in source["allowed_paths"] and path not in source["allowed_paths"]:
                continue
            key = (source["source_id"], source["artifact_id"], path)
            if key not in selected:
                raw, _ = read_source_version(state_dir, source,
                    path=_manifest_ref_path(state_dir, source["ref"]))
                selected[key] = encode_selected(_select_json_path(decode_artifact_text(raw, path), path))
            text = selected[key]
            if row["production_status"] == "mounted_file_read":
                from zf.runtime.artifact_native_delivery import verified_native
                result.extend(verified_native(state_dir, row, text, ephemeral=allow_ephemeral_observations))
                continue
            output = load(row["output_ref"])
            body = output["body"]
            if (output.get("schema_version") != "artifact-output.v1"
                    or hashlib.sha256(body.encode()).hexdigest() != row.get("output_sha256")):
                continue
            if "page_start" in row:
                page = parse_artifact_page(body)
                if page.get("schema_version") == "required-artifact-page.v2":
                    if (page.get("attempt_id") != policy["attempt_id"]
                            or not row.get("delivery_id") or page.get("delivery_id") != row["delivery_id"]
                            or not row.get("bundle_digest") or page.get("bundle_digest") != row["bundle_digest"]):
                        continue
                    matches = [item for item in page.get("items", []) if any(
                        all(alias.get(k) == row.get(k) for k in
                            ("source_id", "artifact_id", "artifact_sha256", "json_path"))
                        for alias in [item, *item.get("aliases", [])])]
                    if len(matches) != 1:
                        continue
                    item = matches[0]
                else:
                    item = page
                start, end = row["page_start"], row["page_end"]
                if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text)
                        or row.get("page_total") != len(text)
                        or any(item.get(k) != row.get(k) for k in ("page_start", "page_end", "page_total"))
                        or row.get("selected_sha256") != hashlib.sha256(text.encode()).hexdigest()
                        or row.get("selected_bytes") != len(text.encode())
                        or item.get("content") != text[start:end]
                        or row.get("returned_bytes") != len(text[start:end].encode())):
                    continue
            elif body != text or row.get("returned_bytes") != len(text.encode()) or row.get("truncated"):
                continue
            witness = row.get("_provider_observation") if allow_ephemeral_observations else None
            witness = witness or load(row["provider_observation_ref"])
            sid = witness["session_id"]
            if (witness.get("schema_version") != "provider-output-observation.v1"
                    or str(UUID(sid)) != sid or sid != row.get("provider_session_id")
                    or not witness.get("call_id") or witness["call_id"] != row.get("provider_call_id")
                    or datetime.fromisoformat(witness["timestamp"]) < datetime.fromisoformat(row["read_at"])
                    or witness["output"] not in observable_outputs(witness.get("raw_output", witness["output"]))
                    or not complete_output_observed(body, witness["output"])):
                continue
            result.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            # Unknown/corrupt delivery is not a semantic verdict or success.
            continue
    return result
