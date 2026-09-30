"""Recover committed but unbound snapshots as non-authoritative diagnostics."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .case_evidence import CaseEvidence, _directory, read_case_snapshot


def recover_unbound_snapshots(
    output: Path, cases: tuple[dict[str, Any], ...], validated_paths: list[Path],
    *, max_files: int = 128, max_bytes: int = 64 * 1024 * 1024,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Only probe known slots of report-declared case owners, never source roots.

Newly computed SHA proves diagnostic copy integrity, not the lost producer
binding. Do not mutate case reports or remove their original expected digests.
"""
    diagnostics: list[dict[str, Any]] = []
    errors: list[str] = []
    accepted = set(validated_paths)
    consumed = 0
    attempts = 0
    for case in cases:
        record = case.get("ordinary_evidence")
        if not isinstance(record, dict) or record.get("registered") is not True:
            continue
        snapshots = record.get("snapshots")
        if not isinstance(snapshots, dict):
            continue
        directory = output / "case-evidence" / hashlib.sha256(case["nodeid"].encode()).hexdigest()
        for point in sorted(CaseEvidence.SLOTS):
            path = directory / f"{point}.json"
            if path in accepted:
                continue
            try:
                # No glob/rglob: a .tmp, unknown slot or foreign case directory
                # can never become an accepted snapshot via this path.
                with _directory(directory) as fd:
                    info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                if attempts >= max_files or consumed + info.st_size > max_bytes:
                    errors.append(f"{case['nodeid']} {point}: unbound diagnostic budget exceeded")
                    return diagnostics, errors
                attempts += 1
                available = max_bytes - consumed
                consumed += info.st_size  # Failed parses/reads also consume the budget.
                payload, digest, size = read_case_snapshot(
                    path, nodeid=case["nodeid"], point=point, max_bytes=available,
                )
                original = snapshots.get(point)
                reported_digest = original.get("digest") if isinstance(original, dict) else None
                diagnostics.append({
                    "nodeid": case["nodeid"], "capture_point": point,
                    "path": str(path), "digest": digest, "bytes": size,
                    "reported_digest": reported_digest,
                    "complete_for_declared_scope": payload["complete_for_declared_scope"],
                    "classification": "unbound_diagnostic_not_verdict_evidence",
                })
            except FileNotFoundError:
                continue
            except Exception as exc:
                errors.append(f"{case['nodeid']} {point}: diagnostic unavailable: {type(exc).__name__}: {exc}")
    return diagnostics, errors
