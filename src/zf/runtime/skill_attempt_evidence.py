"""Bounded evidence for Skill evaluations. Only project-visible facts reach cards."""

from __future__ import annotations

import os
import re
from pathlib import Path

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, sidecar_path
from zf.runtime.skill_invocation_projection import (
    _is_controlled_skill_call,
    _mapping_input,
    _materialized_skill_paths,
    _tool_call,
    _tool_reads_target,
)


def entries(value: object) -> list:
    return value if isinstance(value, list) else []


def pinned(ref, sha):
    return {"ref": ref, "sha256": sha}


def file_version(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


class EvidenceReader:
    def __init__(self, state: Path):
        self.state = state
        self.pending: dict[Path, tuple[int, int, int] | None] = {}

    def hydrate(self, ref: object):
        if not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str):
            return None
        if not re.fullmatch("[0-9a-f]{64}", ref["sha256"]) or not artifact_access_allowed(
            ref.get("access_scope")
        ):
            return None
        try:
            value = hydrate_sidecar_ref(self.state, ref).payload
            if not isinstance(value, dict) or artifact_access_allowed(value.get("access_scope")):
                return value
        except SidecarRefError as exc:
            if (
                exc.code in {"ref_missing", "hash_mismatch", "byte_count_mismatch", "schema_unsupported"}
                and exc.ref
            ):
                path = sidecar_path(self.state, exc.ref)
                self.pending[path] = None if exc.code == "ref_missing" else file_version(path)
        except (ValueError, TypeError, OSError, KeyError, RecursionError):
            pass
        return None

    def object(self, ref: object) -> dict:
        value = self.hydrate(ref)
        return value if isinstance(value, dict) else {}


def skill_action(event):
    """Keep just the read/invocation parameters and returned frontmatter names."""
    tool, args = _tool_call(event)
    args = _mapping_input(args)
    command = args.get("command") or args.get("cmd") or ""
    direct = str(args.get("file_path") or args.get("path") or args.get("file") or args.get("filename") or "")
    if (
        tool.lower() not in {"skill", "skills", "skill.invoke"}
        and "SKILL.md" not in direct
        and "SKILL.md" not in str(command)
    ):
        return None
    response = event.payload.get("tool_response")
    returned = (
        set(re.findall(r"(?m)^name:[ \t]*['\"]?([\w-]+)['\"]?[ \t]*$", response))
        if isinstance(response, str) and event.type.endswith(".post_tool_use")
        else set()
    )
    return (
        tool,
        {
            key: args[key]
            for key in ("command", "cmd", "file_path", "path", "file", "filename", "name", "skill", "workdir")
            if key in args
        },
        returned,
    )


def skill_rows(reader, root, dispatch, materialization, actions, sid, ended):
    role = dispatch["role_instance"]
    ref = pinned(
        materialization.get("skill_dispatch_treatment_ref"),
        materialization.get("skill_dispatch_treatment_digest"),
    )
    pack = reader.object(ref)
    if pack.get("schema_version") != "skill-dispatch-treatment.v1" or (
        pack.get("role_instance"),
        pack.get("task_id"),
    ) != (role, dispatch.get("task_id")):
        pack = {}
    meta = {
        m["name"]: m
        for m in entries(materialization.get("skills"))
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    }
    members = {
        m["skill_name"]: m
        for m in entries(pack.get("materialized_skills"))
        if isinstance(m, dict) and isinstance(m.get("skill_name"), str)
    }
    rows = []
    declared = {name for name in entries(dispatch.get("skills")) if isinstance(name, str)}
    for name in sorted(declared | members.keys()):
        member = members.get(name, {})
        paths = _materialized_skill_paths(
            member.get("materialized_to") if isinstance(member.get("materialized_to"), str) else "",
            role_instance=role,
            state_dir=reader.state,
            project_root=root,
        )
        absolute_paths = {path for path in paths if Path(path).is_absolute()}
        invoked, read = [], []
        for ev, tool, args, returned in actions:
            p = ev.payload
            if any(
                p[k] != dispatch[k]
                for k in ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id", "task_id")
                if p.get(k)
            ):
                continue
            exact = (
                p.get("dispatch_id") == dispatch["dispatch_id"]
                or p.get("attempt_id") == dispatch["attempt_id"]
            )
            session = sid and p.get("session_id") == sid and dispatch["_ts"] <= ev.ts <= ended
            if not (exact or session or ev.causation_id == dispatch["_event_id"]):
                continue
            if _is_controlled_skill_call(tool, args, name):
                invoked.append(ev.id)
            command = str(args.get("command") or args.get("cmd") or "")
            commands = re.split(r"&&|\|\||[;\n|]", command) if command else []
            cwd = args.get("workdir") or p.get("cwd")
            targets = set(absolute_paths)
            if isinstance(cwd, str) and Path(cwd).is_absolute():
                targets.update(os.path.relpath(path, cwd) for path in absolute_paths)
            targeted = (
                any(_tool_reads_target(tool, {"command": cmd}, targets) for cmd in commands)
                if commands
                else _tool_reads_target(tool, args, targets)
            )
            if targeted and name in returned:
                read.append(ev.id)
        rows.append(
            {
                "name": name,
                "digest": member.get("digest") or None,
                "auto_inject": meta.get(name, {}).get("auto_inject") if member else None,
                "materialization": member.get("status", "unknown"),
                "read_status": "observed_header" if read else "unknown",
                "invocation": "observed" if invoked else "unknown",
                "load_on_demand": meta.get(name, {}).get("load_on_demand") if member else None,
                "invocation_event_ids": sorted(set(invoked)),
                "read_event_ids": sorted(set(read)),
                "applicability": "unknown",
                "lift": "unmeasured",
            }
        )
    return rows, ref if pack else None


def probe_agreement(reader, dispatch, result):
    evidence = []
    for probe in entries(result.get("probe_receipts")):
        if not isinstance(probe, dict):
            continue
        for path in entries(probe.get("evidence_refs")):
            if not isinstance(path, str) or not re.fullmatch(
                r"artifacts/task-handoffs/tool-output/[0-9a-f]{64}\.json", path
            ):
                continue
            proof = reader.object(pinned(path, Path(path).stem))
            binding = proof.get("binding") or {}
            if not isinstance(binding, dict) or any(
                binding.get(k) != dispatch[k] for k in ("workflow_run_id", "task_id")
            ):
                continue
            source = proof.get("source_ref") or {}
            if reader.hydrate(source) is None:
                continue
            matches = (
                bool(dispatch.get("target_commit"))
                and dispatch["target_commit"] == proof.get("target_commit") == probe.get("target_commit")
                and bool(proof.get("command"))
                and proof["command"] == probe.get("command")
                and type(proof.get("exit_code")) is int
                and type(probe.get("exit_code")) is int
                and proof["exit_code"] == probe["exit_code"]
            )
            evidence.append((path, matches))
    return evidence
