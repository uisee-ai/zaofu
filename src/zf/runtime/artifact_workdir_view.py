"""Read-only virtual files. The mount never stores selected bodies in a checkout."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_input_index import MEDIA_SUFFIXES, binary_input
from zf.runtime.artifact_read_snapshot import open_selection
from zf.runtime.call_result_envelope import canonical_json_sha256


def configured_input_cgroup(config, actor):
    from zf.core.config.artifact_worker_cgroups import artifact_worker_cgroups

    group = artifact_worker_cgroups(config).get(actor, "")
    if not group:
        return ""
    role = next(
        (r for r in getattr(config, "roles", ()) if r.instance_id == actor), None
    )
    return group if getattr(role, "backend", "") == "codex" else ""


def available_input_cgroup(config, actor):
    from zf.runtime.artifact_mount import mounted_scope

    if not configured_input_cgroup(config, actor):
        return ""
    try:
        return mounted_scope(config, actor)
    except (OSError, ValueError):
        return ""


def pinned_input_cgroup(state_dir, event_log, operation_id, actor, attempt):
    """Look up the immutable operator opt-in for this dispatch only."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import load_workflow_operation

    operation = load_workflow_operation(event_log, operation_id) or {}
    if (
        operation.get("active_attempt_id") != attempt
        or operation.get("role_instance") != actor
        or not operation.get("request_ref")
    ):
        return ""
    request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload[
        "request"
    ]
    if (
        request.get("role_instance") != actor
        or request.get("active_attempt_id") != attempt
    ):
        return ""
    return request.get("native_input_cgroup", "")


def recovery_hint(runtime, actor, attempt, operation_id):
    from zf.runtime.cli_command import zf_cli_cmd

    if not operation_id or not attempt:
        return ""
    group = available_input_cgroup(runtime.config, actor)
    if (
        not group
        or pinned_input_cgroup(
            runtime.state_dir, runtime.event_log, operation_id, actor, attempt
        )
        != group
    ):
        return ""
    return (
        f" Open the mounted RESUME.md first. To obtain its path, run "
        f"`{zf_cli_cmd()} artifact workspace --attempt {shlex.quote(attempt)}` "
        "in a persistent tool session. Use the authenticated artifact recover command if the mount is unavailable."
    )


class AttemptFiles:
    def __init__(self, state: Path, manifest: dict, identity: dict, required, recovery):
        self.state, self.manifest, self.identity = state, manifest, identity
        self.recovery = recovery
        self.files = {}
        self._listing = ["# INPUTS", "", f"Attempt: {manifest['attempt_id']}", ""]
        for source in manifest["sources"]:
            if not artifact_access_allowed(
                source.get("access_scope"),
                actor=identity["actor"],
                role=identity["role"],
                purpose=identity["purpose"],
            ):
                continue
            for selector in source.get("allowed_paths") or ["$"]:
                self._add(source, selector, required)
            if "$" in (source.get("allowed_paths") or ["$"]):
                for row in required or []:
                    if (
                        row["source_id"] == source["source_id"]
                        and row["artifact_id"] == source["artifact_id"]
                        and row.get("artifact_sha256", source["sha256"])
                        == source["sha256"]
                    ):
                        self._add(source, row.get("json_path", "$"), required)

    def _add(self, source, selector, required):
        row = {
            "source_id": source["source_id"],
            "artifact_id": source["artifact_id"],
            "json_path": selector,
            "artifact_sha256": source["sha256"],
        }
        key = canonical_json_sha256([source.get("occurrence_id"), row])
        slug = re.sub("[^a-zA-Z0-9_-]", "-", row["source_id"])[:40] or "source"
        suffix = ".txt"
        if binary_input(source):
            media_suffix = Path(source["ref"]).suffix.lower()
            suffix = media_suffix if media_suffix in MEDIA_SUFFIXES else ".bin"
        name = f"inputs/{slug}-{key}{suffix}"
        if name in self.files:
            return
        if binary_input(source) and selector != "$":
            return
        self.files[name] = (row, source)
        reason = "policy unknown" if required is None else "optional"
        if any(
            all(
                r.get(k, "$" if k == "json_path" else None) == row[k]
                for k in ("source_id", "artifact_id", "json_path")
            )
            and r.get("artifact_sha256", row["artifact_sha256"])
            == row["artifact_sha256"]
            for r in required or []
        ):
            reason = "required"
        snap = next(
            (
                s
                for s in source.get("read_selections", [])
                if s["json_path"] == selector
            ),
            {},
        )
        self._listing.append(
            json.dumps(
                {
                    "path": name,
                    **row,
                    "occurrence": source.get("occurrence_id"),
                    "reason": reason,
                    "type": suffix[1:],
                    "selected_sha256": snap.get("selected_sha256"),
                    "selected_bytes": snap.get("selected_bytes"),
                    "characters": [0, snap.get("selected_chars")],
                },
                ensure_ascii=False,
            )
        )

    def read(
        self, name: str, bounds: tuple[int, int] | None = None
    ) -> tuple[bytes, dict | None]:
        if name == "INPUTS.md":
            return ("\n".join(self._listing) + "\n").encode(), None
        if name == "RESUME.md":
            value = dict(self.recovery())
            host = next(
                (
                    p
                    for p, (r, _) in self.files.items()
                    if r["source_id"] == "candidate-host-execution"
                    and r["json_path"] == "$"
                ),
                None,
            )
            if host:
                summary = json.loads(self.read(host)[0])
                keys = ("workflow_run_id", "attempt_id", "operation_id")
                consumer = summary.get("consumer", {})
                expected = {**self.manifest, **self.identity}
                if (
                    summary.get("schema_version") == "candidate-host-evidence.v1"
                    and all(consumer.get(k) == expected.get(k) for k in keys)
                    and consumer.get("role_instance") == self.identity["actor"]
                ):
                    rows = summary["receipts"]
                    original = next(
                        (
                            p
                            for p, (r, _) in self.files.items()
                            if r["source_id"] == "contract" and r["json_path"] == "$"
                        ),
                        None,
                    )
                    commands = {}
                    if original and any(
                        r["status"] != "available" and not r.get("declared_paths")
                        for r in rows
                    ):
                        contract = json.loads(self.read(original)[0])
                        commands = {
                            c["command_id"]: c.get("producer_paths")
                            for c in contract.get("verification_commands", [])
                        }
                    value["host_evidence"] = {
                        "path": host,
                        "total": len(rows),
                        "available": sum(r["status"] == "available" for r in rows),
                        "available_receipt_locators": [
                            {key: r[key] for key in ("command_id", "receipt_id") if key in r}
                            for r in rows if r["status"] == "available"
                        ],
                        "gaps": [
                            {
                                **{
                                    k: r.get(k)
                                    for k in (
                                        "command_id",
                                        "source_operation_id",
                                        "receipt_id",
                                        "receipt_ref",
                                        "media_refs",
                                        "reason",
                                    )
                                },
                                "declared_paths": r.get("declared_paths")
                                or commands.get(r["command_id"]),
                            }
                            for r in rows
                            if r["status"] != "available"
                        ],
                        "diagnosis_owner": "run_manager",
                        "authorizes_retry": False,
                    }

            return (
                "# RESUME\n\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n"
            ).encode(), None
        if name not in self.files:
            raise FileNotFoundError(name)
        row, source = self.files[name]
        if binary_input(source):
            from zf.runtime.artifact_source_versions import read_source_version
            from zf.runtime.sidecar_refs import sidecar_path

            data, _ = read_source_version(
                self.state, source, path=sidecar_path(self.state, source["ref"])
            )
            return data, None
        selection = open_selection(
            self.state, manifest=self.manifest, row=row, identity=self.identity
        )
        start, end = bounds or (0, selection.chars)
        if not 0 <= start <= end <= selection.chars:
            raise ValueError("invalid native character range")
        data = selection.read(start, end).encode()
        if (
            start == 0
            and end == selection.chars
            and (
                hashlib.sha256(data).hexdigest() != selection.digest
                or len(data) != selection.byte_count
            )
        ):
            raise ValueError("frozen selection digest or length changed")
        return data, {
            **selection.receipt,
            "operation_id": self.identity["operation_id"],
            "selected_sha256": selection.digest,
            "selected_bytes": selection.byte_count,
            "page_start": start,
            "page_end": end,
            "page_total": selection.chars,
            "returned_bytes": len(data),
        }

    def size(self, name: str) -> int:
        if name in self.files:
            row, source = self.files[name]
            snap = next(
                (
                    s
                    for s in source.get("read_selections", [])
                    if s["json_path"] == row["json_path"]
                ),
                {},
            )
            if type(snap.get("selected_bytes")) is int:
                return snap["selected_bytes"]
        return len(self.read(name)[0])
