# Canonical Delivery Fixture Plan

## Task 1: Parse checklist rows

Implement `parse_checklist(text)` in `checklist_analyzer.py`. It must treat
`- [x] item` and `- [X] item` as done, treat `- [ ] item` as open, and ignore
non-checklist lines. Add focused pytest coverage for done, open, and ignored
lines.

## Task 2: Summarize checklist progress

Implement `summarize_checklist(text)` on top of the parser. It must return
`total`, `done`, `open`, and `completion_ratio`. The ratio is `0.0` when
there are no checklist rows.
