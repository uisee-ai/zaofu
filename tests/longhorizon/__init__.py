"""LH-6: autoresearch-style long-horizon e2e loop + scenarios + guards.

See tasks/2026-04-19-0122-sprint-LH6-autoresearch-e2e.md for the full
design. This package contains:

  loop.py        — 8-phase iteration driver (Preflight → Review →
                   Ideate → Modify → Commit → Verify → Decide → Log)
  metrics.py     — thin wrapper around core MetricsCollector that
                   produces the TSV row format
  results_log.py — append rows to results.tsv with typed status enum
  report.py      — render health report markdown from results.tsv
  scenarios/     — YAML describing what each run does (tasks, assertions)
  guards/        — shell scripts that fail fast on invariant violation
"""
