---
name: zf-harness-evidence-collection
description: "Use when collecting ZaoFu gate, test, review, or judge evidence that must be machine-routable and auditable."
---

# ZaoFu Harness Evidence Collection

This skill adapts yoke evidence-collection discipline to ZaoFu runtime truth.
It shapes role output; it does not authorize direct state-file edits.

## Required Evidence

Collect:

- task id or feature id
- role and instance id
- command or action performed
- exit code or pass/fail verdict
- short stdout/stderr summary when command-backed
- artifact paths or event ids
- replayable `artifact_refs`
- `evidence_refs` that link to commands, events, files, or reports
- verification tier for each check when the task declares
  `verification_tiers`
- skipped checks and reason
- suspected owner when evidence indicates rework

## Rules

- Prefer command evidence over narrative claims.
- Preserve exact command strings and exit codes.
- Keep summaries short enough for event payloads and review.
- Do not write `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml` by hand.
- If evidence is missing, report the missing field instead of approving.

## Output Shape

Use:

- `evidence`: structured key/value evidence
- `checks`: list of command or artifact checks with `command`, `exit_code`
  or `passed`, and optional `tier`
- `summary`: concise human-readable result
- `artifact_refs`: paths to replayable artifacts; in lifecycle events this is
  a string path list, not structured manifest objects
- `evidence_refs`: paths or event ids supporting the verdict
- `risks`: residual risk or coverage gaps

If a role uses another skill pack that naturally produces structured artifact
objects, publish those through `artifact.manifest.published` and reference the
manifest event id from the lifecycle payload. ZaoFu runtime may normalize
`{"path": "..."}` maps for compatibility, but canonical role output should stay
string-based so provider/skill replacement does not change gate semantics.
