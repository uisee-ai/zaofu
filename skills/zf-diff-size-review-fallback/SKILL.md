---
name: zf-diff-size-review-fallback
description: "Use during ZaoFu review, refactor, closeout, or commit preparation when a change may be too broad, risky, generated-heavy, or sensitive for normal review. Produces diff risk classification, split/sub-review/scope-audit recommendations, and stronger verification requirements while preserving explicit pathspec commit discipline."
---

# ZaoFu Diff Size Review Fallback

## Purpose

Treat large or risky diffs as a harness signal. This skill extends
`zf-harness-commit-push`, `zf-harness-dual-axis-review`, and
`zf-refactor-plan-synth`; it does not auto-split commits or override operator
approval.

## Hard Rules

- Never use `git add -A`, `git add .`, `git commit -a`, or force push.
- Do not classify runtime truth, generated reports, or large artifacts as
  source changes without noting their type.
- Large diff does not mean wrong, but it must trigger stronger review evidence.
- If the diff is too broad to understand, stop and ask for operator
  confirmation before commit.

## Diff Risk Policy

Use these default thresholds unless the project has stricter rules:

```json
{
  "schema_version": "zf.diff_risk_policy.v1",
  "thresholds": {
    "changed_files_warn": 12,
    "changed_files_high": 25,
    "changed_lines_warn": 600,
    "changed_lines_high": 1500,
    "sensitive_paths_any": [
      "src/zf/core/**",
      "src/zf/runtime/orchestrator*.py",
      "src/zf/core/config/**",
      "zf.yaml",
      "examples/*.yaml",
      "pyproject.toml",
      "package-lock.json"
    ]
  },
  "excluded_or_separate_classes": [
    "runtime_state",
    "reports",
    "screenshots",
    "generated_fixtures",
    "vendored_assets"
  ]
}
```

## Review Output

Produce:

```json
{
  "schema_version": "zf.diff_review_fallback.v1",
  "changed_files": 18,
  "changed_lines": 820,
  "sensitive_paths_touched": ["src/zf/runtime/orchestrator.py"],
  "generated_or_runtime_paths": ["reports/example.md"],
  "risk_level": "normal|warn|high|operator_confirm",
  "recommendations": [
    "split_commit",
    "request_sub_review",
    "run_scope_drift_audit",
    "require_stronger_verification"
  ],
  "required_verification": [
    "focused pytest for changed runtime module",
    "schema/config validation",
    "manual scope classification"
  ],
  "operator_confirmation_required": true
}
```

## Classification Rules

- `normal`: below warning thresholds and no sensitive paths.
- `warn`: warning threshold crossed, generated/report paths isolated, or one
  low-risk sensitive path touched.
- `high`: high threshold crossed, multiple ownership surfaces touched, or
  refactor spans source and tests without a slice plan.
- `operator_confirm`: secret-like files, runtime truth files, broad unreviewable
  diff, or conflicting staged changes.

## Output Summary

Return:

- diff risk level
- threshold crossed
- paths needing sub-review
- split or verification recommendation
- whether commit may proceed under explicit pathspec discipline
