---
name: zf-webui-tui-parity
description: "Web chat, dashboard, SSE, session, and TUI parity checks for agent refactors. Use when a replacement agent must preserve source UI workflows, interactive chat behavior, run dashboards, and terminal UI controls."
stages: [impl, verify]
tags: [parity, "parity:webui", "parity:tui"]
---

# Web UI And TUI Parity

Use this skill when implementing or verifying user-facing agent surfaces. The
replacement UI must connect to the new agent backend, not the original source
server or a mock endpoint.

## Required Inventory

Create a UI parity matrix covering:

- web chat session create/list/resume/delete;
- message send, streaming/SSE rendering, cancellation, and error display;
- model/provider selector behavior and persisted settings;
- run/dashboard views, task status, trace links, logs, and cost display;
- TUI navigation, chat, command shortcuts, resize behavior, and theme support;
- gateway-integrated notifications or inbound commands when part of scope.

Each row needs source refs, target refs, user-visible status, and a browser or
TUI verification command.

## Implementation Rules

Keep the web/TUI client pointed at the replacement agent API. If copying source
UI components, replace source-specific branding, endpoint assumptions, and state
contracts with the new project contract.

Prefer a thin compatibility adapter over duplicating divergent API shapes. The
same session/message model should serve CLI, web, and TUI where practical.

## Verification

Use browser or terminal evidence for interactive flows. For web chat, verify a
real message appears in the transcript after send and, when configured, that SSE
streams intermediate tokens before completion.

Do not pass verify if only static pages render while chat, dashboard, or TUI
actions are disconnected.
