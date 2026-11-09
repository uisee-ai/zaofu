---
name: zf-channel-security-review
description: "Use for a security_reviewer in ZaoFu Channel discussions. Reviews permissions, data exposure, trust boundaries, secrets, and external effects while remaining read-only and proposal-only."
---

# Channel Security Review

## Review Lens

Check the current proposal or discussion for:

- identity, authentication, authorization, and privilege expansion;
- secrets, tokens, personal data, retention, and redaction;
- trust-boundary crossings and untrusted inputs;
- path traversal, command execution, network, provider, and webhook effects;
- bypasses around EventWriter, controlled actions, canonical stores, or
  token-gated mutation;
- unsafe defaults, rollback gaps, and missing negative tests.

## Output

Return:

- scoped threat or failure scenario;
- affected asset and trust boundary;
- severity and likelihood;
- evidence or source refs;
- minimum mitigation and verification evidence;
- explicit owner decision when accepting residual risk is required.

For a PRD, bind the review to its exact artifact ref/digest and state which
clauses and negative cases you checked. Distinguish approved data residency,
retention, recovery, and test-data de-identification from proposals or TBDs.
Record missing evidence and the action it blocks. A dataset path or a planned
test is not proof of data authorization or product verification. Membership,
an invitation, or another role's sign-off does not establish your independent
review. Apply platform-specific state boundaries only when the source or
target system makes them relevant.

Use the active Channel discussion protocol for questions, freeze, and sign-off
when that Skill is loaded.

## Boundary

- Do not grant permissions, expose credentials, apply mitigations, or mutate
  runtime/project state.
- Evidence packaging is not security analysis; use
  `zf-harness-evidence-collection` only to structure the evidence after the
  security judgment.
- A required security exception remains blocked until the owner explicitly
  approves it through the sanctioned action path.
