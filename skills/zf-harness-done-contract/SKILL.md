---
name: zf-harness-done-contract
description: "Use before a ZaoFu role claims done; requires concrete evidence and preserves terminal state decisions for the deterministic runtime."
---

# ZaoFu Harness Done Contract

Use this before emitting any completion claim. The skill defines the report
shape; ZaoFu runtime decides whether a task can enter a terminal state.

## Done Evidence

A done report must include:

- task id
- role and instance id
- dispatch id from the current briefing
- changed files or produced artifact paths
- commands run with exit codes
- verification tiers with passing command or artifact evidence
- relevant output summary
- `artifact_refs` and `evidence_refs` when this is terminal gate evidence
- acceptance criteria covered
- known risks, skipped checks, or follow-up work
- next expected role or gate

For fanout writer / affinity lane work, also include:

- `fanout_id`, `stage_id`, `child_id`, and `run_id`
- `lane_id`, `stage_slot`, and `affinity_tag` when present
- `task_map_ref` and `source_index_ref`
- assigned `workdir`, `source_branch`, and `source_commit`
- `files_touched` proven inside `allowed_paths` / `exclusive_files`

For rework, also include the delta from the failed attempt:

- trigger event or rework request id
- required rework items addressed
- files, tests, docs, artifacts, or command evidence changed this attempt
- if no code change was required, a concrete no-code rationale and evidence

## Evidence Trace Back to Backlog (6 required_backlog_refs)

When the task contract was synthesized by orchestrator at stage ④ backlog
(per `docs/impl/22-zaofu-canonical-dag.md`), the kanban entry carries 6
upstream pointers: `spec_ref`, `plan_ref`, `tdd_ref`, `critic_event_id`,
`critic_gate_ref`, `evidence_contract`.

A complete done report SHOULD echo back at least two of these in its
`evidence_refs` array, so review / test / judge can audit the implementation
against the original design audit trail without re-discovering it:

Lifecycle completion events use `artifact_refs` as replayable **string paths**.
Structured manifest refs belong in `artifact.manifest.published` or in a
separate `artifact_manifest_refs` diagnostic field emitted by the kernel. Do not
put object refs into role terminal claims.

```json
{
  "dispatch_id": "<from briefing>",
  "state": "DONE",
  "summary": "<one-sentence>",
  "artifact_refs": ["<files you produced>"],
  "evidence_refs": [
    "git:<commit-sha>",
    "branch:worker/<role>",
    "spec:<contract.spec_ref>",                  ← echo backlog.spec_ref
    "critic-audit:<contract.critic_event_id>"    ← echo backlog.critic_event_id
  ]
}
```

When the contract has `evidence_contract: {"runtime": "<cmd>"}` and your
verification ran that command, include its exit code + output snippet under
that key:

```json
{
  "evidence": {
    "runtime": {"command": "<as declared>", "exit_code": 0, "passed": true}
  }
}
```

This trace-back lets the downstream chain answer: "does the dev's delivery
match what arch promised + critic approved?" without re-parsing 6 events.
If you skipped any required ref because the backlog itself was incomplete,
flag it as a risk so orchestrator's P2 preflight catches the gap.

## Cannot Claim Done

Do not claim done when:

- tests or required checks were not run and no reason is provided
- review/test/judge gates are still pending
- the implementation changed files outside the allowed scope
- the lane claim omits required fanout metadata or tries to claim global feature
  / product completion
- evidence exists only as a narrative summary
- declared `verification_tiers` are not covered by passing structured evidence
- the current briefing has a dispatch id but the completion event/report omits it
- this is a rework attempt and no concrete delta from the failed attempt is shown
- there is an unresolved blocker, ambiguity, or failing command

Use `DONE_WITH_CONCERNS` when work is complete but evidence is partial or a
risk remains. Use `BLOCKED` when deterministic progress requires another
input or runtime action.
