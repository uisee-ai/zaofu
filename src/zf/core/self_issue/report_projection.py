"""Lossless presentation aliases, without making new assessment decisions."""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Mapping, Sequence


def report_gap(value: Any) -> Any:
    """Read old JSON/Python-repr gap objects without evaluating code."""
    if isinstance(value, str) and len(value) <= 16000 and value.lstrip().startswith("{"):
        for decoder in (json.loads, ast.literal_eval):
            try:
                decoded = decoder(value)
                if isinstance(decoded, dict):
                    return decoded
            except (ValueError, SyntaxError, TypeError, RecursionError):
                pass
    return value


def report_environment(report: Mapping[str, Any]) -> dict[str, Any]:
    raw = report.get("environment")
    env = dict(raw) if isinstance(raw, Mapping) else ({"details": raw} if raw else {})
    for target, aliases in {
        "os": ("operating_system",), "version": ("operating_system_version",),
        "arch": ("architecture",), "python": ("python_version", "python_versions"),
    }.items():
        if not env.get(target):
            for alias in aliases:
                if env.get(alias) or report.get(alias):
                    env[target] = env.get(alias) or report[alias]
                    break
    # Reports now keep affected and investigator environments separate.  Keep
    # both in the publication projection instead of dropping them when the
    # agent used the richer envelope without a top-level ``environment`` map.
    reported = report.get("reported_environment")
    if isinstance(reported, Mapping):
        for key, value in reported.items():
            if value and f"reported_{key}" not in env:
                env[f"reported_{key}"] = value
    investigator = report.get("investigator_environment")
    if isinstance(investigator, Mapping):
        for key, value in investigator.items():
            if value and f"investigator_{key}" not in env:
                env[f"investigator_{key}"] = value
    return env


def version_context(report: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    revisions = report.get("repository_revisions")
    revisions = dict(revisions) if isinstance(revisions, Mapping) else {}
    reported = report.get("wheel_reported_source_revision") or report.get("source_code_revision_reported") or report.get("source_revision_reported") or report.get("source_code_revision")
    evaluated = report.get("inspected_source_revision") or report.get("source_code_revision_inspected") or report.get("source_revision_inspected") or revisions.get("related_source_checkout")
    context = {
        **revisions,
        "issue_project_root": report.get("issue_workspace_revision") or revisions.get("issue_project_root"),
        "investigation_checkout_revision": binding.get("revision"),
        "reported_source_revision": reported,
        "evaluated_source_revision": evaluated,
        "reported_project_version": report.get("project_version"),
        "reproduction_version": report.get("reproduction_version"),
        "reproduction_artifact_source": report.get("reproduction_artifact_source") or report.get("artifact_source"),
        "source_match_status": report.get("source_match_status"),
        "inspected_source_dirty": report.get("inspected_source_dirty"),
        "reproduction_artifact_sha256": report.get("reproduction_artifact_sha256"),
    }
    # A related checkout is not necessarily the revision of the reported wheel.
    # Nor is the investigation host automatically the reproduction environment.
    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


def publication_facts(report: Mapping[str, Any]) -> dict[str, Any]:
    """Avoid leaking legacy model defaults as agent-authored conclusions."""
    return {key: report.get(key) or "" for key in (
        "severity", "reproduction_status", "assessment_confidence", "evidence_collection_mode",
    )}


def _placeholder(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return (
        not text
        or text in {"unknown", "not provided", "not provided."}
        or text.startswith("未提供")
        or "user did not provide" in text
    )



def stage_report_contract(
    report: Mapping[str, Any], fields: Mapping[str, Any], gaps: Sequence[Any],
) -> dict[str, Any]:
    """Normalize /issue output without granting it the triage gate."""
    # ``fields`` is the agent's semantic projection, while ``report`` is
    # its richer assessment envelope.  Older turns often emitted
    # placeholder values (``unknown``/``not provided``) into ``fields``;
    # blindly overlaying them erased a real component/impact/classification
    # that was already present in the report.  Merge placeholders only when
    # no meaningful value exists.
    value = dict(report)
    for key, candidate in dict(fields).items():
        current = value.get(key)
        candidate_text = str(candidate or "").strip().lower()
        current_text = str(current or "").strip().lower()
        candidate_placeholder = (
            not candidate_text
            or candidate_text in {"unknown", "not provided", "not provided."}
            or candidate_text.startswith("未提供")
            or "user did not provide" in candidate_text
        )
        current_placeholder = (
            not current_text
            or current_text in {"unknown", "not provided", "not provided."}
            or current_text.startswith("未提供")
            or "user did not provide" in current_text
        )
        if key not in value or current_placeholder or not candidate_placeholder:
            value[key] = candidate
    # The Issue Agent is intentionally allowed to use a richer report
    # vocabulary than the publication Draft.  Normalize aliases here so
    # the Draft, preview and provider body consume one semantic contract.
    if "impact" in value and _placeholder(value.get("impact_scope")):
        value["impact_scope"] = value["impact"]
    if "impact" in value and _placeholder(value.get("actual_behavior")):
        value["actual_behavior"] = value["impact"]
    if "operating_system_version" in value and "environment" not in value:
        value["environment"] = {"version": value["operating_system_version"]}
    project_version = str(value.get("project_version") or "").strip()
    if project_version.lower().startswith(("当前授权 checkout", "current authorized checkout", "head:")):
        revision = re.search(r"\b[0-9a-f]{7,64}\b", project_version, re.IGNORECASE)
        value["source_code_revision_inspected"] = revision.group(0) if revision else project_version
        value.pop("project_version", None)

    # Publication/Draft models require environment to be a string map.
    # Run this after semantic field overlay because fields.environment may
    # otherwise replace a valid report map with a provider prose string.
    environment = report_environment(value)
    if isinstance(environment, Mapping):
        value["environment"] = {
            str(key): (json.dumps(item, ensure_ascii=False) if isinstance(item, (Mapping, list, tuple)) else str(item))
            for key, item in environment.items()
            if str(key).strip() and item is not None
        }
    elif environment not in (None, ""):
        value["environment"] = {"details": json.dumps(environment, ensure_ascii=False)
                                 if isinstance(environment, (list, tuple, dict))
                                 else str(environment)}

    classification = value.get("classification")
    if isinstance(classification, (list, tuple, set)):
        labels = {str(item).strip().lower() for item in classification if str(item).strip()}
        if labels & {"bug", "python-packaging", "runtime-dependency", "cli-startup", "packaging/runtime dependency regression"}:
            value["classification"] = "bug"
        elif labels:
            value["classification"] = next(iter(labels))
    if _placeholder(value.get("component")):
        bounded_scope = value.get("suggested_components") or value.get("bounded_scope")
        if isinstance(bounded_scope, (list, tuple)):
            value["component"] = " / ".join(str(item) for item in bounded_scope if str(item).strip())
        elif bounded_scope:
            value["component"] = str(bounded_scope)
        elif str(value.get("classification") or "").lower() == "bug":
            value["component"] = "Python packaging / CLI startup"

    for key in ("investigation_gaps", "downstream_verification_tasks"):
        item = value.get(key)
        if isinstance(item, str):
            value[key] = [line.strip(" -") for line in item.splitlines() if line.strip(" -")]
        elif isinstance(item, (list, tuple)):
            value[key] = [report_gap(entry) for entry in item if str(entry).strip()]
    if isinstance(value.get("downstream_verification_tasks"), str):
        value["downstream_verification_tasks"] = [value["downstream_verification_tasks"]]
    if "source_revision_reported" in fields and "source_code_revision" not in value:
        value["source_code_revision"] = fields["source_revision_reported"]
    if "description" in fields and "bug_description" not in value:
        value["bug_description"] = fields["description"]
    # Validation is persisted in ``workflow.validation`` for routing and
    # recovery.  It is not a public Issue field and must not be copied into
    # the visible Report/Issue body as a nested implementation tree.
    for internal_key in ("validation", "checks", "field_provenance"):
        value.pop(internal_key, None)
    value["external_issue_disposition"] = "needs_triage"
    if _placeholder(value.get("severity")):
        value["severity"] = "needs_assessment"
        value.setdefault("severity_reason", "本轮报告未提供可由证据支持的严重程度；待独立分诊评估。")
        value["severity_status"] = "not_assessed"
    if "classification" in value:
        value.setdefault("classification_status", "provisional")
    if "severity" in value:
        value.setdefault("severity_status", "provisional")

    raw_gaps = list(value.get("verification_gaps") or ())
    raw_gaps.extend(report_gap(item) for item in gaps if str(item).strip())
    investigation: list[Any] = list(value.get("investigation_gaps") or ())
    downstream: list[Any] = list(value.get("downstream_verification_tasks") or ())
    for item in raw_gaps:
        item = report_gap(item)
        if isinstance(item, Mapping):
            # Route typed gaps by declared stage, not by words such as
            # "clean-install" inside an explanation of missing provenance.
            scope = item.get("next_action_scope") or item.get("scope")
            target = downstream if scope in {"triage_or_fix", "fix", "downstream"} else investigation
            if item not in target:
                target.append(item)
            continue
        text = str(item).strip()
        if not text or text in {"investigation_gaps", "downstream_verification_tasks"}:
            continue
        lowered = text.lower()
        if any(token in lowered for token in ("重新构建", "回归验证", "修复后", "clean-install", "验证修复")):
            if text not in downstream:
                downstream.append(text)
        elif text not in investigation:
            investigation.append(text)
    value["investigation_gaps"] = investigation
    value["downstream_verification_tasks"] = downstream
    value.pop("verification_gaps", None)
    if "bounded_fix_target" in value:
        value.setdefault("suggested_fix_directions", value["bounded_fix_target"])
        value.pop("bounded_fix_target", None)
    return value

