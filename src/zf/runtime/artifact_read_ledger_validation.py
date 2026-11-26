"""Validate sealed artifact read evidence without loading whole sidecars."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.runtime.sidecar_refs import (
    SidecarRefError, hydrate_sidecar_ref, normalize_sidecar_ref_descriptor, sidecar_path,
)

ARTIFACT_READ_SCHEMA = "artifact-read.v1"


class ArtifactReadError(ValueError):
    """A controlled artifact read or its evidence contract failed."""


def validate_required_reads(
    state_dir: Path,
    *,
    policy: Mapping[str, Any],
    ledger_descriptor: Mapping[str, Any],
) -> list[dict[str, str]]:
    attempt_id = str(policy.get("attempt_id") or "")
    try:
        descriptor = normalize_sidecar_ref_descriptor(dict(ledger_descriptor))
        ref = str(descriptor["ref"])
        encoding = str(descriptor.get("encoding") or "utf-8")
        if (descriptor.get("content_type") == "application/x-ndjson"
                and encoding.lower().replace("_", "-") == "utf-8" and not ref.endswith(".json")):
            digest = hashlib.sha256()
            size = 0
            path = sidecar_path(state_dir, ref)
            with path.open("rb") as handle:
                def decoded_lines() -> Iterable[str]:
                    nonlocal size
                    for line in handle:
                        digest.update(line)
                        size += len(line)
                        yield line.decode("utf-8", errors="replace")

                rows = _parse_ledger_lines(decoded_lines(), expected_attempt_id=attempt_id)
            expected_bytes = int(descriptor.get("byte_count") or 0)
            if expected_bytes and expected_bytes != size:
                raise SidecarRefError("byte_count_mismatch", f"sidecar byte_count mismatch for {ref}", ref=ref)
            if descriptor.get("sha256") and descriptor["sha256"] != digest.hexdigest():
                raise SidecarRefError("hash_mismatch", f"sidecar hash mismatch for {ref}", ref=ref)
        else:
            hydrated = hydrate_sidecar_ref(state_dir, descriptor)
            if not isinstance(hydrated.payload, str):
                return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ledger"}]
            rows = _parse_ledger(hydrated.payload.encode("utf-8"), expected_attempt_id=attempt_id)
    except ArtifactReadError as exc:
        return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ledger", "message": str(exc)}]
    except Exception as exc:  # descriptor errors become admission issues
        return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ref", "message": str(exc)}]
    return validate_read_rows(state_dir, policy=policy, rows=rows)


def validate_read_rows(state_dir: Path, *, policy: Mapping[str, Any],
                       rows: list[dict[str, Any]],
                       allow_ephemeral_observations: bool = False) -> list[dict[str, str]]:
    """Shared pure coverage check for preflight and sealed result admission."""
    issues: list[dict[str, str]] = []
    delivery = policy.get("delivery_evidence")
    if delivery:
        try:
            if not isinstance(delivery, Mapping) or delivery.get("mode") != "observed":
                raise ValueError("invalid input delivery evidence policy")
            from zf.runtime.input_delivery_evidence import delivered_rows

            rows = delivered_rows(state_dir, dict(policy), rows,
                                  allow_ephemeral_observations=allow_ephemeral_observations)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [{"field": "input_consumption.delivery", "code": "input_delivery_unavailable",
                     "message": str(exc)}]
    required = policy.get("required_reads")
    for index, item in enumerate(required if isinstance(required, list) else []):
        if not isinstance(item, Mapping):
            issues.append({"field": f"required_reads[{index}]", "code": "invalid_policy"})
            continue
        if not required_read_satisfied(rows, item):
            message = f"{item.get('source_id')}/{item.get('artifact_id')} {item.get('json_path') or '$'}"
            if delivery:
                import shlex
                from zf.runtime.cli_command import zf_cli_cmd

                command = shlex.join(["artifact", "read", "--attempt", str(policy["attempt_id"]),
                    "--source", str(item.get("source_id") or ""),
                    "--artifact", str(item.get("artifact_id") or ""),
                    "--json-path", str(item.get("json_path") or "$"),
                    "--page-start", "0", "--page-size", "2048", "--reason", "recovery"])
                message += (f" lacks complete provider delivery evidence. Re-read this selection with "
                            f"{zf_cli_cmd()} {command}; return the full tool body and follow next_page_start "
                            "until null. Host read counts alone do not prove delivery. Revalidate in this attempt.")
            issues.append({
                "field": f"required_reads[{index}]",
                "code": "required_input_delivery_missing" if delivery else "required_read_missing",
                "message": message,
            })
    return issues


def _parse_ledger(raw: bytes, *, expected_attempt_id: str) -> list[dict[str, Any]]:
    return _parse_ledger_lines(raw.decode("utf-8").splitlines(), expected_attempt_id=expected_attempt_id)


def _parse_ledger_lines(lines: Iterable[str], *, expected_attempt_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactReadError(f"invalid read ledger line {index}") from exc
        if not isinstance(row, dict) or row.get("schema_version") != ARTIFACT_READ_SCHEMA:
            raise ArtifactReadError(f"invalid read ledger row {index}")
        if expected_attempt_id and str(row.get("attempt_id") or "") != expected_attempt_id:
            raise ArtifactReadError(f"attempt identity mismatch on ledger line {index}")
        rows.append(row)
    return rows


def _read_matches(row: Mapping[str, Any], requirement: Mapping[str, Any]) -> bool:
    if "page_start" in row:
        return False
    if str(row.get("status") or "read") != "read":
        return False
    for field in ("source_id", "artifact_id"):
        if str(requirement.get(field) or "") != str(row.get(field) or ""):
            return False
    digest = str(requirement.get("artifact_sha256") or requirement.get("sha256") or "")
    if digest and digest != str(row.get("artifact_sha256") or ""):
        return False
    if str(requirement.get("json_path") or "$") != str(row.get("json_path") or "$"):
        return False
    if int(row.get("returned_bytes") or 0) < int(requirement.get("min_returned_bytes", 1)):
        return False
    if bool(row.get("truncated")) and not bool(requirement.get("allow_truncated", False)):
        return False
    for field in ("max_items", "max_chars"):
        required_value = int(requirement.get(field) or 0)
        if required_value and int(row.get(field) or 0) != required_value:
            return False
    return True


def required_read_satisfied(rows: Iterable[Mapping[str, Any]], requirement: Mapping[str, Any]) -> bool:
    """Accept a whole read or complete, gap-free pages of one selected value."""
    binding = requirement.get("consumer_binding")
    if binding:
        if not isinstance(binding, Mapping):
            return False
        rows = [row for row in rows if row.get("production_status") in {"mounted_file_read", "stdout_flushed"}
                and all(row.get(key) == value for key, value in binding.items())]
    else:
        rows = list(rows)
    if any(_read_matches(row, requirement) for row in rows):
        return True
    if int(requirement.get("max_items") or 0) or int(requirement.get("max_chars") or 0):
        return False
    groups: dict[tuple[str, int, int], list[tuple[int, int]]] = {}
    for row in rows:
        if "page_start" not in row or str(row.get("status") or "") != "read":
            continue
        if any(str(row.get(key) or "") != str(requirement.get(key) or "")
               for key in ("source_id", "artifact_id")):
            continue
        digest_expected = str(requirement.get("artifact_sha256") or requirement.get("sha256") or "")
        if digest_expected and digest_expected != str(row.get("artifact_sha256") or ""):
            continue
        if str(row.get("json_path") or "$") != str(requirement.get("json_path") or "$"):
            continue
        try:
            start, end, total = (int(row[key]) for key in ("page_start", "page_end", "page_total"))
            size = int(row.get("returned_bytes") or 0)
            selected_bytes = int(row.get("selected_bytes") or 0)
        except (ValueError, TypeError, KeyError):
            continue
        digest = str(row.get("selected_sha256") or "")
        if (requirement.get("min_returned_bytes") == 0 and start == end == total == size == selected_bytes == 0
                and digest == hashlib.sha256(b"").hexdigest() and not row.get("truncated")):
            return True  # A delivered empty JSON string is still a bound read, not missing evidence.
        if len(digest) != 64 or start < 0 or end <= start or end > total or size <= 0 or selected_bytes < total:
            continue
        groups.setdefault((digest, total, selected_bytes), []).append((start, end))
    for (_, total, selected_bytes), pages in groups.items():
        end = 0
        for start, stop in sorted(pages):
            if start > end:
                break
            end = max(end, stop)
        if end == total and selected_bytes >= int(requirement.get("min_returned_bytes", 1)):
            return True
    return False
