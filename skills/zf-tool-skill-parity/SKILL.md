---
name: zf-tool-skill-parity
description: "Tool registry, function calling, plugin/skill discovery, and execution parity checks for agent refactors. Use when a replacement agent must preserve source tools, skill loading, permissions, and tool execution semantics."
---

# Tool And Skill Parity

Use this skill when the agent exposes tools, skills, plugins, or command
execution. The replacement must preserve both the registry contract and runtime
execution behavior.

## Required Inventory

Build a source-to-target matrix for:

- built-in tools and external tool/plugin discovery;
- tool JSON schema, required fields, defaults, and validation;
- permission checks and dangerous-action gates;
- execution sandbox/workdir/env behavior;
- tool result shape, error normalization, and retry semantics;
- skill discovery paths, frontmatter parsing, routing metadata, and load order;
- hidden/system skills and project-local skill overrides.

Rows must include source refs, target refs, status, and an execution or schema
verification command.

## Implementation Rules

Do not collapse tools into free-form prompts. Tools must be registered with
structured schemas and return structured results that the provider/function-call
layer can replay.

Skill loading should be deterministic:

- preserve source precedence rules;
- report missing or invalid skills as actionable diagnostics;
- avoid project-specific skill names in generic workflow profiles;
- keep project-specific parity expectations in project skills or overlays.

## Verification

Run at least one tool-call path through the real provider layer when credentials
are available. Also run a deterministic local registry/schema test so missing
provider credentials do not hide broken tool discovery.

Do not pass final verify while P0/P1 tool or skill rows are unimplemented,
unregistered, or only documented.
