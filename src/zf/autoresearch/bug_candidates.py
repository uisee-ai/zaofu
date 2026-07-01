"""Bug candidate aggregation and backlog export for autoresearch."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zf.autoresearch.failure_signals import FailureSignal, severity_rank


@dataclass(frozen=True)
class BugCandidate:
    bug_id: str
    dedupe_key: str
    source_signals: list[str] = field(default_factory=list)
    status: str = "proposed"
    priority: str = "P1"
    failure_class: str = ""
    title: str = ""
    summary: str = ""
    repro_command: str = ""
    expected: str = ""
    actual: str = ""
    evidence_paths: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    metric_sources: list[str] = field(default_factory=list)
    metric_impacts: dict[str, float] = field(default_factory=dict)
    # B13 (doc 92 §2): scheduling fields so a candidate can be ingested
    # into a TaskContract without re-triage. All optional — runtime
    # detectors may not know scope; the shepherd/operator fills them.
    severity: str = ""
    source_kind: str = "runtime"  # runtime | supervisor | external | quarantine
    allowed_paths: list[str] = field(default_factory=list)
    root_owner_class: str = "none"  # slice | assembly | scaffold | none
    affinity_tag: str = ""
    lane_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacklogExportResult:
    candidate: BugCandidate
    path: Path
    created: bool
    reason: str = ""


def _priority_for_signals(signals: list[FailureSignal]) -> str:
    max_rank = max((severity_rank(signal.severity) for signal in signals), default=0)
    if max_rank >= severity_rank("critical"):
        return "P0"
    if max_rank >= severity_rank("high"):
        return "P0"
    if max_rank >= severity_rank("medium"):
        return "P1"
    return "P2"


def _stable_bug_id(dedupe_key: str) -> str:
    digest = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:8].upper()
    return f"ZF-AR-BUG-{digest}"


def candidates_from_signals(signals: Iterable[FailureSignal]) -> list[BugCandidate]:
    grouped: dict[str, list[FailureSignal]] = {}
    for signal in signals:
        key = signal.fingerprint or f"{signal.category}:{signal.summary}"
        grouped.setdefault(key, []).append(signal)

    candidates: list[BugCandidate] = []
    for key in sorted(grouped):
        group = grouped[key]
        primary = sorted(
            group,
            key=lambda signal: (-severity_rank(signal.severity), signal.signal_id),
        )[0]
        evidence = sorted({
            path for signal in group for path in signal.evidence_paths
        })
        event_ids = sorted({
            event_id for signal in group for event_id in signal.event_ids
        })
        impacts: dict[str, float] = {}
        for signal in group:
            for metric, value in signal.metric_impacts.items():
                impacts[metric] = impacts.get(metric, 0.0) + float(value)
        candidates.append(BugCandidate(
            bug_id=_stable_bug_id(key),
            dedupe_key=key,
            source_signals=[signal.signal_id for signal in group],
            priority=_priority_for_signals(group),
            failure_class=primary.category,
            title=primary.summary,
            summary=primary.summary,
            repro_command=primary.repro_command,
            expected=primary.expected,
            actual=primary.actual,
            evidence_paths=evidence,
            event_ids=event_ids,
            metric_sources=[
                "docs/design/51-autoresearch-enhanced-self-evolution-design.md:145-198",
                "docs/design/51-autoresearch-enhanced-self-evolution-design.md:285-299",
            ],
            metric_impacts=impacts,
        ))
    return candidates


def _slug(text: str) -> str:
    chars: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug[:72] or "autoresearch-bug"


def render_candidate_markdown(candidate: BugCandidate) -> str:
    source_signals = "\n".join(f"- `{sid}`" for sid in candidate.source_signals) or "- none"
    event_ids = "\n".join(f"- `{event_id}`" for event_id in candidate.event_ids) or "- none"
    evidence = "\n".join(f"- `{path}`" for path in candidate.evidence_paths) or "- none"
    metric_sources = "\n".join(f"- `{src}`" for src in candidate.metric_sources) or "- none"
    impacts = (
        "\n".join(f"- `{key}`: {value:+.3f}" for key, value in sorted(candidate.metric_impacts.items()))
        or "- none"
    )
    repro = candidate.repro_command or "从 evidence_paths 中的 run archive / events.jsonl 复现"
    # B11: YAML frontmatter = 机器面(zf issue ingest 消费),正文 = 人读面。
    fm = {
        "schema": "issue-candidate.v1",
        "bug_id": candidate.bug_id,
        "dedupe_key": candidate.dedupe_key,
        "title": candidate.title,
        "status": candidate.status,
        "priority": candidate.priority,
        "severity": candidate.severity,
        "source_kind": candidate.source_kind,
        "failure_class": candidate.failure_class,
        "repro_command": candidate.repro_command,
        "expected": candidate.expected,
        "actual": candidate.actual,
        "allowed_paths": list(candidate.allowed_paths),
        "root_owner_class": candidate.root_owner_class,
        "affinity_tag": candidate.affinity_tag,
        "lane_hint": candidate.lane_hint,
        "event_ids": list(candidate.event_ids),
        "evidence_paths": list(candidate.evidence_paths),
    }
    import yaml as _yaml

    frontmatter = "---\n" + _yaml.safe_dump(
        fm, allow_unicode=True, sort_keys=False,
    ) + "---\n"
    return frontmatter + "\n".join([
        f"# {candidate.priority}: {candidate.title}",
        "",
        f"> 状态: {candidate.status}",
        f"> 日期: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC",
        "> 来源: autoresearch FailureSignalDetector",
        f"> Dedupe: {candidate.dedupe_key}",
        f"> Source-kind: {candidate.source_kind or 'runtime'}"
        + (f" / Severity: {candidate.severity}" if candidate.severity else ""),
        "> Owner surface: `src/zf/autoresearch/`, focused runtime/autoresearch tests",
        "",
        "## 问题",
        "",
        candidate.summary,
        "",
        "## Failure class",
        "",
        f"`{candidate.failure_class}`",
        "",
        "## 来源信号",
        "",
        source_signals,
        "",
        "## 事件与证据",
        "",
        "Event ids:",
        "",
        event_ids,
        "",
        "Evidence paths:",
        "",
        evidence,
        "",
        "## 指标来源",
        "",
        metric_sources,
        "",
        "Metric impacts:",
        "",
        impacts,
        "",
        "## Scheduling (doc 92 ingest)",
        "",
        f"- allowed_paths: {candidate.allowed_paths or ['<shepherd/operator fills>']}",
        f"- root_owner_class: {candidate.root_owner_class or 'none'}",
        f"- affinity_tag: {candidate.affinity_tag or '<unset>'}"
        + (f" / lane_hint: {candidate.lane_hint}" if candidate.lane_hint else ""),
        "",
        "## Repro",
        "",
        f"`{repro}`",
        "",
        "## 期望 / 实际",
        "",
        f"- Expected: {candidate.expected or 'not_collected'}",
        f"- Actual: {candidate.actual or 'not_collected'}",
        "",
        "## Acceptance Criteria",
        "",
        "1. 复现 failure signal -> verify: 能从记录的 event ids / evidence paths 重新生成同一 dedupe key。",
        "2. 修复候选实现 -> verify: 对应 detector 不再输出该 FailureSignal。",
        "3. autoresearch replay -> verify: report.md / events-summary.json 不再包含该 failure class。",
        "4. validity triage -> verify: 没有 unsupported claim、protected evaluator 修改或缺证据 keep。",
        "",
    ])


def _existing_backlog_for_dedupe(out_dir: Path, dedupe_key: str) -> Path | None:
    if not out_dir.exists():
        return None
    needle = f"> Dedupe: {dedupe_key}"
    for path in sorted(out_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle in text:
            return path
    return None


def write_candidate_backlogs(
    candidates: Iterable[BugCandidate],
    *,
    out_dir: Path,
    now: datetime | None = None,
) -> list[BacklogExportResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H%M")
    results: list[BacklogExportResult] = []
    for candidate in candidates:
        existing = _existing_backlog_for_dedupe(out_dir, candidate.dedupe_key)
        if existing is not None:
            results.append(BacklogExportResult(
                candidate=candidate,
                path=existing,
                created=False,
                reason="existing_dedupe",
            ))
            continue
        filename = f"{stamp}-{candidate.priority}-{_slug(candidate.failure_class)}-{_slug(candidate.title)}.md"
        path = out_dir / filename
        suffix = 1
        while path.exists():
            path = out_dir / f"{stamp}-{candidate.priority}-{_slug(candidate.failure_class)}-{suffix}.md"
            suffix += 1
        path.write_text(render_candidate_markdown(candidate), encoding="utf-8")
        results.append(BacklogExportResult(candidate=candidate, path=path, created=True))
    return results


__all__ = [
    "BugCandidate",
    "BacklogExportResult",
    "candidates_from_signals",
    "render_candidate_markdown",
    "write_candidate_backlogs",
]
