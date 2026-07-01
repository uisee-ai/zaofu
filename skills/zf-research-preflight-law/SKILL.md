---
name: zf-research-preflight-law
description: "Use before and after ZaoFu research, autoresearch, project suggestion, competitive analysis, codebase reference review, or source-dependent planning to record research preflight, source freshness, degraded-run status, and LAW sections: Limits, Assumptions, Warnings."
---

# ZaoFu Research Preflight LAW

## Purpose

Make research usable as plan evidence without turning stale or degraded
retrieval into strong facts. This skill shapes research artifacts; it does not
force every research task to use the network.

## Hard Rules

- If current information may have changed, verify freshness or mark the run
  degraded.
- Do not output strong recommendations from degraded evidence.
- Do not write runtime truth files.
- Do not hide source conflicts; preserve them in `Warnings`.
- Prefer primary sources for technical claims.

## Research Preflight

Record this before research synthesis:

```json
{
  "schema_version": "zf.research_preflight.v1",
  "research_question": "What can ZaoFu borrow from project X?",
  "required_freshness": "current|last_30_days|repo_head|stable_docs|not_time_sensitive",
  "source_classes": ["official_docs", "code", "issues", "releases", "reports"],
  "retrieval_status": "complete|partial|blocked|offline|not_needed",
  "degraded_reason": "",
  "planned_sources": ["https://example.com/docs", "<project-root>"],
  "captured_at": "2026-06-22T00:00:00Z"
}
```

## Source Freshness Manifest

Record source freshness in the research artifact or sidecar:

```json
{
  "schema_version": "zf.source_freshness_manifest.v1",
  "sources": [
    {
      "source_ref": "https://github.com/example/project",
      "source_kind": "official_docs|code|issue|release|report|local_file",
      "captured_at": "2026-06-22T00:00:00Z",
      "published_at": "2026-06-10T00:00:00Z",
      "commit": "abc1234",
      "freshness_verdict": "fresh|stale|unknown|not_time_sensitive",
      "confidence": "high|medium|low",
      "notes": "Repo head inspected locally."
    }
  ]
}
```

## LAW Section

Every research synthesis must include:

- `Limits`: what was not covered, inaccessible, or out of scope.
- `Assumptions`: temporary beliefs that downstream plan must not treat as
  proven.
- `Warnings`: stale, conflicting, low-confidence, or high-risk facts.

If `retrieval_status` is not `complete`, mark recommendations as
`provisional` unless the source class is not needed for the conclusion.

## Output Summary

Return:

- research question
- retrieval status and degraded reason
- freshness verdict summary
- LAW bullets
- source manifest path
- recommendations marked as confirmed or provisional
