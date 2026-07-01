---
name: zf-harness-instruction-hygiene
description: "Use when reconciling ZaoFu repo instructions, role instructions, and enabled skills without creating another control plane."
---

# ZaoFu Harness Instruction Hygiene

This skill helps a role reason about instruction sources while keeping
`zf.yaml` as the only enablement control plane.

## Priority

Use this order when sources conflict:

1. active system/developer/runtime instructions
2. repository `AGENTS.md`
3. role briefing and role config
4. enabled skills
5. design docs and historical notes

## Rules

- Do not assume the provider automatically read every instruction file.
- Report missing or conflicting instruction sources.
- Treat skill materialization as projection, not configuration.
- Do not import global provider skills as hidden behavior.
- If a skill conflicts with ZaoFu runtime truth rules, follow runtime truth.

## Report Shape

When instruction risk matters, report:

- source files considered
- enabled skills considered
- conflict or gap
- chosen rule
- evidence path
