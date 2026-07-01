"""Structured task validation helpers.

These validators give ContractD a deterministic contract surface for checks
whose semantics are easy to lose in shell snippets, especially byte-exact
artifacts where command substitution silently strips trailing newlines.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


VALIDATION_KINDS = frozenset({
    "byte_exact",
    "text_line_exact",
    "regex",
    "exists",
    "command",
})


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def coerce_validation_spec(value: object) -> dict[str, Any]:
    """Return a normalized validation spec dict, or ``{}``.

    Supported shape:
      {"kind": "byte_exact", "path": "proof.txt", "expected": "value"}

    Aliases are intentionally conservative; unknown keys are preserved so
    future callers do not lose structured evidence when Layer 1 projects the
    contract through kanban.json.
    """
    if not isinstance(value, dict):
        return {}
    spec = {str(k): v for k, v in value.items()}
    kind = str(spec.get("kind") or spec.get("type") or "").strip()
    if not kind:
        return {}
    spec["kind"] = kind
    if "path" not in spec:
        for alias in ("file", "target", "artifact"):
            if spec.get(alias):
                spec["path"] = spec[alias]
                break
    if "expected" not in spec:
        for alias in ("content", "value", "expected_text"):
            if alias in spec:
                spec["expected"] = spec[alias]
                break
    return spec


def evaluate_validation_spec(
    spec: dict[str, Any],
    *,
    workspace: Path,
) -> ValidationResult:
    """Evaluate a structured validation spec against ``workspace``."""
    spec = coerce_validation_spec(spec)
    kind = str(spec.get("kind") or "").strip()
    base_evidence: dict[str, Any] = {
        "validation_kind": kind,
        "validation_spec": spec,
    }
    if not spec:
        return ValidationResult(passed=True, evidence={"validation_empty": True})
    if kind not in VALIDATION_KINDS:
        return ValidationResult(
            passed=False,
            evidence={
                **base_evidence,
                "unknown_validation_kind": kind,
                "valid_validation_kinds": sorted(VALIDATION_KINDS),
            },
            reason=f"unknown validation kind: {kind}",
        )
    if kind == "command":
        if not str(spec.get("command") or "").strip():
            return ValidationResult(
                passed=False,
                evidence={**base_evidence, "validation_command_missing": True},
                reason="validation.command is required for command validation",
            )
        return ValidationResult(
            passed=True,
            evidence={**base_evidence, "validation_delegated_to_command": True},
            reason="validation command is executed by ContractD shell path",
        )

    target_result = _resolve_target(spec, workspace)
    if isinstance(target_result, ValidationResult):
        return target_result
    target = target_result
    rel_path = str(spec.get("path") or "")
    base_evidence.update({
        "validation_path": rel_path,
        "validation_resolved_path": str(target),
    })

    if kind == "exists":
        passed = target.exists()
        return ValidationResult(
            passed=passed,
            evidence={**base_evidence, "validation_passed": passed},
            reason="" if passed else f"validation path does not exist: {rel_path}",
        )

    try:
        actual = target.read_bytes()
    except OSError as exc:
        return ValidationResult(
            passed=False,
            evidence={**base_evidence, "error": str(exc)},
            reason=f"validation path unreadable: {rel_path}",
        )

    if kind == "byte_exact":
        try:
            expected = _expected_bytes(spec)
        except Exception as exc:
            return ValidationResult(
                passed=False,
                evidence={**base_evidence, "error": str(exc)},
                reason=f"byte_exact expected bytes are invalid: {exc}",
            )
        passed = actual == expected
        evidence = {
            **base_evidence,
            "validation_passed": passed,
            "actual_bytes": _short_bytes_repr(actual),
            "expected_bytes": _short_bytes_repr(expected),
            "actual_len": len(actual),
            "expected_len": len(expected),
        }
        return ValidationResult(
            passed=passed,
            evidence=evidence,
            reason=(
                ""
                if passed
                else (
                    "byte_exact validation failed: "
                    f"actual bytes {evidence['actual_bytes']} "
                    f"!= expected bytes {evidence['expected_bytes']}"
                )
            ),
        )

    if kind == "text_line_exact":
        expected_text = str(spec.get("expected") or "")
        try:
            actual_text = actual.decode(str(spec.get("encoding") or "utf-8"))
        except UnicodeDecodeError as exc:
            return ValidationResult(
                passed=False,
                evidence={**base_evidence, "error": str(exc)},
                reason="text_line_exact validation failed: file is not valid text",
            )
        normalized = _strip_single_final_newline(actual_text)
        multiline = "\n" in normalized or "\r" in normalized
        passed = normalized == expected_text and not multiline
        evidence = {
            **base_evidence,
            "validation_passed": passed,
            "actual_text": normalized,
            "expected_text": expected_text,
            "actual_bytes": _short_bytes_repr(actual),
            "actual_len": len(actual),
            "allowed_final_newline": True,
            "multiline": multiline,
        }
        return ValidationResult(
            passed=passed,
            evidence=evidence,
            reason=(
                ""
                if passed
                else (
                    "text_line_exact validation failed: "
                    f"actual {normalized!r} != expected {expected_text!r}"
                )
            ),
        )

    pattern = str(spec.get("pattern") or spec.get("expected") or "")
    flags = re.MULTILINE
    try:
        text = actual.decode(str(spec.get("encoding") or "utf-8"))
        matched = re.search(pattern, text, flags) is not None
    except (UnicodeDecodeError, re.error) as exc:
        return ValidationResult(
            passed=False,
            evidence={**base_evidence, "error": str(exc), "pattern": pattern},
            reason=f"regex validation failed before match: {exc}",
        )
    return ValidationResult(
        passed=matched,
        evidence={
            **base_evidence,
            "validation_passed": matched,
            "pattern": pattern,
        },
        reason="" if matched else "regex validation did not match",
    )


def _resolve_target(
    spec: dict[str, Any],
    workspace: Path,
) -> Path | ValidationResult:
    raw_path = str(spec.get("path") or "").strip()
    kind = str(spec.get("kind") or "").strip()
    if not raw_path:
        return ValidationResult(
            passed=False,
            evidence={"validation_kind": kind, "validation_path_missing": True},
            reason="validation.path is required",
        )
    path = Path(raw_path)
    if path.is_absolute():
        target = path
    else:
        target = workspace / path
    try:
        resolved = target.resolve()
        root = workspace.resolve()
        resolved.relative_to(root)
    except ValueError:
        return ValidationResult(
            passed=False,
            evidence={
                "validation_kind": kind,
                "validation_path": raw_path,
                "validation_path_escaped_workspace": True,
            },
            reason=f"validation path escapes workspace: {raw_path}",
        )
    except OSError:
        resolved = target
    return resolved


def _expected_bytes(spec: dict[str, Any]) -> bytes:
    if spec.get("expected_hex") is not None:
        return bytes.fromhex(str(spec.get("expected_hex") or ""))
    if spec.get("expected_base64") is not None:
        return base64.b64decode(str(spec.get("expected_base64") or ""))
    encoding = str(spec.get("encoding") or "utf-8")
    return str(spec.get("expected") or "").encode(encoding)


def _short_bytes_repr(value: bytes, *, limit: int = 80) -> str:
    shown = value[:limit]
    suffix = b"" if len(value) <= limit else b"..."
    return repr(shown + suffix)


def _strip_single_final_newline(value: str) -> str:
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value
