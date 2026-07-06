---
name: zf-memory-context-parity
description: "Memory, context engine, prompt builder, compression, background review, and curator parity checks for agent refactors. Use when a replacement agent must preserve long-horizon context and memory behavior."
---

# Memory And Context Parity

Use this skill when implementing or verifying long-horizon agent behavior. The
replacement must preserve how the source agent builds prompts, remembers work,
compresses history, and keeps background review or curator state.

## Required Inventory

Create a memory/context matrix covering:

- system prompt and prompt builder layers;
- conversation/session memory persistence and retrieval;
- working memory, summaries, checkpoints, and resume context;
- context ranking, truncation, compression, and token budget policy;
- background review, curator, reflection, or self-improvement loops;
- cache/storage format and migration from source artifacts if required.

Each row must include source refs, target refs, status, and a verification
command or artifact reference.

## Implementation Rules

Keep prompt construction deterministic enough to test. Separate:

- immutable system/developer instructions;
- project and user goal context;
- retrieved memory/context snippets;
- runtime tool/provider results.

Compression must preserve executable facts: current goal, task IDs, source refs,
open risks, pending verification, and provider/tool state. Do not summarize away
handoff-critical IDs.

## Verification

Test at least one resume or long-context scenario: start a session, create
state, compact or reload, then prove the agent can continue with the required
memory and prompt facts intact.

Do not pass final verify while memory/context behavior is stubbed, in-memory
only, or disconnected from CLI/web/TUI sessions.
