---
name: zf-harness-archive-contract
description: "Use when preparing ZaoFu ship/archive evidence; keeps archive creation in the deterministic runtime."
---

# ZaoFu Harness Archive Contract

Archive is a runtime action, not a text summary. This skill defines what a role
must gather before asking the runtime to archive a run.

## Required Evidence

An archive request should reference:

- run id
- task and feature ids
- event range or event snapshot
- kanban and feature projection state
- session and role session state
- skills lockfile and per-role manifests
- gate/review/test/judge evidence
- command outputs and scorecard inputs
- known unsafe or intentionally excluded artifacts

## Rules

- Do not mark ship complete only because a narrative summary exists.
- Do not manually copy runtime truth files into an archive.
- Redact secrets before including provider logs or raw command output.
- If required evidence is missing, report the missing item and stop.
