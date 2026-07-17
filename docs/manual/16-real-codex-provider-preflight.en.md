# Real Codex Provider Preflight

> Audience: operators running real Codex E2E, Channel providers, or provider smoke tests.

## 1. Run Preflight First

```bash
uv run zf doctor provider --backend codex
uv run zf doctor provider --backend codex --json
```

Preflight reads the environment without starting a worker or writing runtime
truth. It checks whether the `codex` CLI is on `PATH`, whether
`codex --version` executes, and whether the host supports the basic network
namespace probe.

If the result says `sandbox: unsupported`, normal Codex sandbox startup may
fail with an error such as:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

## 2. E2E Policy

Real E2E never silently falls back to a fake provider. After a preflight
failure, either fix host namespace and sandbox support or explicitly record the
risk and use bypass only for that trusted validation:

```bash
codex exec --dangerously-bypass-approvals-and-sandbox --json "$PROMPT"
```

This bypass is for controlled smoke testing, not a production worker default.

Channel and Kanban Agent headless Codex normally use `workspace-write` or
`read-only`. When preflight reports unsupported sandboxing, Web returns
`sandbox_unsupported` before starting the turn. For a short-lived write test in
an explicitly trusted local project:

```bash
export ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
```

Restart WebKanban after setting it. This disables the normal sandbox; restoring
host namespace or bubblewrap support remains the long-term fix.

## 3. Channel Failure Signals

When Codex sandbox or app-server startup fails, the expected event sequence is:

- `channel.agent.reply.started`
- `channel.agent.reply.failed`

Web must show the provider failure rather than presenting a fabricated review
or completion. Inspect:

```bash
uv run zf events --last 80
uv run zf doctor provider --backend codex --json
```

The warning below is not itself fatal:

```text
Codex could not find bubblewrap on PATH ... Codex will use the bundled bubblewrap ...
```

For timeouts, determine whether Codex app-server stopped producing events
within the provider budget. Codex turns have no total wall-clock cap: token,
tool, and status events renew the budget after a turn starts. The default idle
budget is 1,800 seconds, increasing to 7,200 seconds while a tool call is in
flight. Override either budget for a local scenario with:

```bash
export ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S=3600
export ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S=14400
```

The legacy `ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S` is still read for
compatibility, but Channel uses `ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S` first.

## 4. Redaction

Never print real tokens. Show only keys or redacted values:

```bash
env | grep -E 'CODEX|OPENAI|ZF_' | sed -E 's/(=.).+$/=***REDACTED***/' | sort
```
