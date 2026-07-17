---
name: zf-provider-contract-parity
description: "Provider, model adapter, streaming, and function-call parity checks for agent refactors. Use when a refactor/build must prove the new agent preserves LLM provider behavior, model routing, tool-call schemas, retries, and real-provider smoke tests."
stages: [impl, verify]
tags: [parity, "parity:provider"]
---

# Provider Contract Parity

Use this skill when implementing or verifying provider/runtime modules in a
replacement agent. The goal is to prove the new provider layer can replace the
source agent instead of only returning mock chat text.

## Required Inventory

Build a source-to-target matrix before marking provider work complete:

- provider names and environment variables;
- model routing and default model selection;
- request/response schema, including streaming chunks;
- function/tool-call schema, tool choice, and replay behavior;
- retry, timeout, abort, rate-limit, and fallback behavior;
- error normalization visible to CLI, TUI, web, and gateway callers.

Every row must include a source reference, a target implementation reference,
status, and a verification command or runtime evidence reference.

## Implementation Rules

Keep provider adapters behind a stable interface. Do not leak provider-specific
payloads into higher-level agent core, memory, or web chat code.

Preserve function-call behavior as a first-class contract:

- accept and return structured tool calls, not stringified JSON;
- keep tool-call IDs stable through streaming/replay when the provider supports
  them;
- handle `tool_choice` or equivalent provider options explicitly;
- degrade with a documented fallback only when the provider does not support a
  source feature.

## Verification

Run at least one real-provider smoke test when credentials are configured. The
test should cover normal chat and one tool/function-call turn. If credentials
are absent, record the exact missing env vars and keep the row open unless the
workflow policy permits mocked evidence.

Do not emit final pass while P0/P1 provider rows remain missing or unverified.
