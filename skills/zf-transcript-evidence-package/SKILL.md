---
name: zf-transcript-evidence-package
description: "Use when converting provider, channel, tmux probe, or hook transcripts into ZaoFu evidence packages for review, synthesis, resume, or audit. Produces redacted excerpt refs and supported/unsupported claims while explicitly marking transcript evidence as not runtime truth."
---

# ZaoFu Transcript Evidence Package

## Purpose

Package transcript observations as bounded evidence. Transcript can explain
what an agent saw or did, but it must not replace State Packet, task capsule,
events, or task-map truth.

## Hard Rules

- Include `do_not_treat_as_truth: true`.
- Do not save full transcripts by default.
- Redact secrets, tokens, private keys, and sensitive user data.
- Prefer excerpt refs, hashes, line ranges, timestamps, and short excerpts.
- Resume may reference this package, but must prefer State Packet / task
  capsule / events for truth.

## Evidence Package Artifact

Write a package such as
`docs/impl/<work>/transcript-evidence-package.json`:

```json
{
  "schema_version": "zf.transcript_evidence_package.v1",
  "package_id": "tep-001",
  "source_kind": "provider_session|channel|tmux_probe|hook",
  "source_ref": "session:019e59f4",
  "time_range": {
    "start": "2026-06-22T00:00:00Z",
    "end": "2026-06-22T00:05:00Z"
  },
  "redaction_status": "redacted|no_sensitive_content_found|blocked_sensitive",
  "do_not_treat_as_truth": true,
  "excerpt_refs": [
    {
      "excerpt_id": "EX-001",
      "ref": "transcript.log#L20-L35",
      "sha256": "sha256:...",
      "short_excerpt": "pytest tests/example passed",
      "redacted": false
    }
  ],
  "observed_actions": [
    {
      "action_id": "ACT-001",
      "action": "ran test command",
      "excerpt_refs": ["EX-001"]
    }
  ],
  "claims_supported": [
    {
      "claim_id": "CLM-001",
      "excerpt_refs": ["EX-001"]
    }
  ],
  "claims_not_supported": [
    {
      "claim_id": "CLM-002",
      "reason": "No command output or artifact ref observed."
    }
  ]
}
```

## Redaction Rules

- Replace tokens and secrets with `[REDACTED_SECRET]`.
- Replace sensitive local personal paths with stable short refs when possible.
- If safe redaction is not possible, set `redaction_status` to
  `blocked_sensitive` and do not publish excerpts.
- Store a hash of the original excerpt only when it does not leak sensitive
  material through the hash context.

## Output Summary

Return:

- package path
- source kind and time range
- redaction status
- supported and unsupported claim ids
- warning that transcript evidence is not truth
