"""Read-only audit of explicit requirements, not an inference of overall coverage."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .evidence import write_json_atomic
from .source_observation import SOURCE_PROFILES
from .native_acceptance import (
    NATIVE_CASES,
    NATIVE_CLASS,
    _bound_mainline,
    _bound_native,
    _bound_premerge,
    _json,
    _require,
)


def build_requirement_audit(*, manifest: Path, output: Path) -> dict:
    raw = manifest.read_bytes()
    plan = _json(raw)
    _require(plan["schema_version"] == "zf-requirement-audit-input.v1", "unknown audit schema")
    rows = plan["requirements"]
    _require(isinstance(rows, list) and bool(rows), "requirements must be nonempty")
    ids = []
    for row in rows:
        _require(isinstance(row, dict), "requirement must be an object")
        _require(set(row) <= {"id", "description", "nodeids", "report", "reason", "format", "role", "selection", "source_profile"},
                 "unknown requirement fields")
        identity = row["id"]
        _require(isinstance(identity, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", identity)),
                 "invalid requirement id")
        ids.append(identity)
        nodes = row["nodeids"]
        _require(isinstance(nodes, list) and all(isinstance(n, str) and "::" in n for n in nodes),
                 "nodeids must contain exact test identities")
        _require(len(nodes) == len(set(nodes)), "duplicate required node")
        fmt = row.get("format", "testkit-run")
        _require(fmt in {"testkit-run", "mainline-run", "native-run", "premerge-run"},
                 "unknown report format")
        source_profile = row.get("source_profile", "backend")
        _require(source_profile in SOURCE_PROFILES, "unknown source profile")
        _require(fmt in {"testkit-run", "mainline-run"} or "source_profile" not in row,
                 "native/premerge use their own fixed source scope")
        selection = row.get("selection", "current")
        if fmt == "mainline-run":
            _require(selection in {"current", "historical"}, "unknown mainline selection")
            if selection == "current":
                _require(not nodes, "current mainline uses the complete registry, not nodeids")
            else:
                _require(bool(nodes), "historical mainline needs an explicit selection snapshot")
        else:
            _require(selection == "current", "selection is only valid for mainline-run")
        if fmt == "native-run":
            role = row.get("role")
            _require(role in NATIVE_CASES, "native-run needs a native role")
            _require(nodes == [NATIVE_CLASS.replace(".", "/") + ".py::" + NATIVE_CASES[role]],
                     "native role/node mismatch")
        elif fmt == "premerge-run":
            _require(row.get("role") == "premerge" and not nodes,
                     "premerge requires full script selection, not nodeids")
        else:
            _require(row.get("role") is None, "role is only valid for native/premerge")
        if row.get("report") is None:
            _require(isinstance(row.get("reason"), str) and bool(row["reason"].strip()),
                     "missing evidence needs an explicit reason")
        else:
            if fmt == "testkit-run":
                _require(bool(nodes), "testkit report needs exact nodeids")
            _require(isinstance(row["report"], dict), "report reference must be an object")
            ref = row["report"]
            _require(set(ref) == {"ref", "sha256"} and isinstance(ref["ref"], str) and bool(ref["ref"]),
                     "invalid report reference")
            _require(isinstance(ref["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])),
                     "invalid report digest")
    _require(len(ids) == len(set(ids)), "duplicate requirement id")
    output.mkdir(parents=True, exist_ok=False)
    (output / "input-manifest.json").write_bytes(raw)
    results = []
    for row in rows:
        result = {"id": row["id"], "nodeids": row["nodeids"],
                  "description": row.get("description", "")}
        ref = row.get("report")
        if ref is None:
            result.update(status="missing", reason=row["reason"])
        else:
            try:
                path = (manifest.parent / ref["ref"]).resolve()
                data = path.read_bytes()
                _require(hashlib.sha256(data).hexdigest() == ref["sha256"], "report digest mismatch")
                folder = output / row["id"]
                folder.mkdir()
                (folder / "testkit-run.json").write_bytes(data)
                fmt = row.get("format", "testkit-run")
                if fmt == "native-run":
                    bound = _bound_native(data, path, row["role"], folder)
                elif fmt == "premerge-run":
                    bound = _bound_premerge(data, path, folder)
                elif fmt == "mainline-run":
                    if row.get("selection", "current") == "historical":
                        report = _json(data)
                        _require(report["stage"] == "MAINLINE-DETERMINISTIC",
                                 "wrong historical deterministic profile")
                        bound = _bound_mainline(
                            data, path, folder, expected_nodes=tuple(row["nodeids"]),
                            source_profile=row.get("source_profile", "backend"),
                        )
                    else:
                        bound = _bound_mainline(data, path, folder,
                                                source_profile=row.get("source_profile", "backend"))
                else:
                    bound = _bound_mainline(data, path, folder, expected_nodes=tuple(row["nodeids"]),
                                            source_profile=row.get("source_profile", "backend"))
                source = bound.get("shared_source_observation", bound["source_observation"])
                historical = (
                    fmt == "mainline-run"
                    and row.get("selection", "current") == "historical"
                )
                result.update(status="historical" if historical else bound["verdict"],
                              observed_verdict=bound["verdict"], selection=row.get("selection", "current"),
                              source=source,
                              full_source=bound["source_observation"],
                              scope_digests=bound.get("scope_digests", {}),
                              report_ref=str(path), report_sha256=ref["sha256"],
                              snapshot=f"{row['id']}/testkit-run.json",
                              format=fmt, role=row.get("role"),
                              nonpassing=bound.get("nonpassing", []), source_verified=True,
                              bound_evidence=[{**entry, "snapshot": f"{row['id']}/{entry['snapshot']}"}
                                              for entry in bound["bound_evidence"]])
            except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as exc:
                result.update(status="harness_failure", reason=f"{type(exc).__name__}: {exc}")
        results.append(result)
    sources = {(r["source"]["git_head"], r["source"]["files_sha256"])
               for r in results if r.get("source_verified")}
    all_bound = all(r.get("source_verified") is True for r in results)
    scopes: dict[str, set[str]] = {}
    for result in results:
        for prefix, digest in result.get("scope_digests", {}).items():
            scopes.setdefault(prefix, set()).add(digest)
    conflicts = sorted(prefix for prefix, digests in scopes.items() if len(digests) > 1)
    same_revision = all_bound and len(sources) == 1 and not conflicts
    statuses = {r["status"] for r in results}
    historical_fail = any(
        r.get("status") == "historical" and r.get("observed_verdict") != "pass"
        for r in results
    )
    verdict = ("harness_failure" if "harness_failure" in statuses else
               "fail" if "fail" in statuses or historical_fail else "incomplete"
               if "missing" in statuses or "historical" in statuses else
               "source_mismatch" if not same_revision else "pass")
    payload = {
        "schema_version": "zf-requirement-audit.v1", "verdict": verdict,
        "gate_exit": int(verdict != "pass"), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "same_revision_verified": same_revision, "source_variant_count": len(sources),
        "source_scope_conflicts": conflicts,
        "requirements": results, "release_authorized": False,
        "scope": "Only explicitly listed requirements; no overall coverage or current-checkout assertion.",
    }
    write_json_atomic(output / "requirement-audit.json", payload)
    return payload
