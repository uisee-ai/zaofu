---
name: zf-harness-evidence-collection
description: "Use when collecting ZaoFu gate, test, review, or judge evidence that must be machine-routable and auditable."
---

# ZaoFu Harness Evidence Collection

> Absorbs zf-transcript-evidence-package.

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

## Transcript-Sourced Evidence

When evidence comes from a transcript (provider session, channel, tmux probe,
or hook) rather than a command you ran, package the observation as **bounded**
evidence. A transcript can explain what an agent saw or did, but it must not
replace State Packet, task capsule, `events.jsonl`, or task-map truth.

### Canonical transcript sources

The primary kernel scenario is diagnosis. When the orchestrator mints
`diagnosis.requested` (verified: `src/zf/runtime/diagnosis.py`
`DIAGNOSIS_REQUESTED`), the
diagnostician stage attaches to the live scene — `logs/<role>.log` /
`.zf/logs/<role>.log` pane mirrors (verified:
`src/zf/runtime/transport_stream_json.py` `attach_handle`,
`src/zf/runtime/diagnosis.py` `plan_diagnosis_requests` `log_hints`),
the event window, and the worktree — reads the transcript, and packages what it
found as its structured `diagnosis.completed` report (verified:
`src/zf/runtime/diagnosis.py` `DIAGNOSIS_COMPLETED`). Any role that reads pane
mirrors this way
owes the same bounded-evidence discipline. Channel, hook, and provider-session
transcripts follow the same rules.

### Transcript hard rules

- Mark the evidence `do_not_treat_as_truth: true` — skill-owned 约定(无内核
  校验;`grep -rn "do_not_treat_as_truth" src/zf/` empty).
- Do not save full transcripts by default. Prefer excerpt refs, `sha256`
  hashes, line ranges, timestamps, and short excerpts.
- Redact secrets, tokens, private keys, and sensitive user data before
  publishing.
- Resume may reference the package, but must prefer State Packet / task capsule
  / events for truth.

### Redaction rules

- Replace tokens and secrets with `[REDACTED_SECRET]` — a skill-owned
  placeholder; the kernel's own redactor (`src/zf/core/security/redaction.py`)
  emits labels `[REDACTED_PRIVATE_KEY]` / `[REDACTED_JWT]` /
  `[REDACTED_API_KEY]` / `[REDACTED_SECRET]`.
- Replace sensitive local personal paths with stable short refs when possible.
- If safe redaction is not possible, mark the excerpt's redaction status
  `blocked_sensitive` and do not publish the excerpt — skill-owned 约定(无内核
  校验;`grep -rn "blocked_sensitive\|redaction_status" src/zf/` empty).
- Store a hash of the original excerpt only when the hash context does not
  itself leak sensitive material.

### One artifact convention

Package transcript evidence through the **same output shape** as the rest of
this skill (`evidence` / `checks` / `artifact_refs` / `evidence_refs` —
verified: `src/zf/runtime/completion_honesty.py` `_claimed_paths`,
`src/zf/runtime/stage_contract.py` `StageContractResult.to_dict`) rather than a
separate schema envelope.
Do **not** mint a `zf.transcript_evidence_package.v1` schema_version; it has no
kernel validator. The transcript-specific fields ride inside `evidence` as
skill-owned extensions (no `src/zf/` validator — do not treat as
kernel-checked):

- `source_kind`: `provider_session|channel|tmux_probe|hook`
- `source_ref`: e.g. `session:019e59f4`
- `time_range`: `{start, end}`
- `redaction_status`: `redacted|no_sensitive_content_found|blocked_sensitive`
- `do_not_treat_as_truth: true`
- `excerpt_refs`: `[{excerpt_id, ref: "transcript.log#L20-L35", sha256,
  short_excerpt, redacted}]`
- `observed_actions`: `[{action_id, action, excerpt_refs}]`

### Claims split

This skill packages what the transcript *shows*. To turn transcript
observations into checked supported / unsupported claims with deterministic
verdicts, hand the excerpt refs to `zf-mechanical-claim-verifier`, which owns
the claims-verdict half (claim set → `requirement_coverage_matrix` /
`gap_findings`). Do not adjudicate claims here.

### Transcript output summary

Return the evidence package path, source kind and time range, redaction status,
supported / unsupported claim ids (via the claim verifier), and a warning that
transcript evidence is not runtime truth.
