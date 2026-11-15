# Real Codex Provider Preflight

> Audience: operators running real Codex E2E, Channel providers, or provider smoke tests.

## 1. Run Preflight First

```bash
uv run zf doctor provider --backend codex
uv run zf doctor provider --backend codex --json
```

Preflight reads the environment without starting a worker or writing runtime
truth. It checks whether the `codex` CLI is on `PATH`, whether
`codex --version` executes, and whether the installed Codex can launch a command through its own workspace permission profile.
The probe does not call a model or qualify credentials; unknown interfaces and failed probes return nonzero.

If the result says `sandbox: unsupported`, normal Codex sandbox startup may
fail with an error such as:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
Codex sandbox unsupported for sandbox=workspace-write:
unshare: unshare failed: Operation not permitted
```

## 2. E2E Policy

Real E2E must not replace restricted execution with a fake provider or a sandbox bypass.
Repair host prerequisites, then rerun as the intended ordinary service user, not root.

```bash
bash scripts/setup-codex-sandbox.sh --check
bash scripts/setup-codex-sandbox.sh --apply
```

The apply mode is qualified for Ubuntu 24.04 only. It installs distribution bubblewrap/AppArmor
packages when missing and loads the distribution bwrap-userns-restrict profile. It preserves
customized profiles, rejects symlinks/missing package profiles, and leaves global AppArmor/userns
restrictions enabled. It does not change Codex accounts, install a setuid helper, or reboot services.
Repeat execution reuses installed packages and an identical profile. Other distributions must use
their package manager and run doctor; they are not automatically qualified by this script.

See [official prerequisites](https://learn.chatgpt.com/docs/sandboxing). On Ubuntu 24.04.2 with
Codex 0.164.0 and bubblewrap 0.9.0, installing the package alone still failed; loading the profile
restored normal startup while workspace-external writes remained denied. Verify each new host.

### 2.1 WebKanban Launch Modes

Channel `permission_profile` is a ZaoFu semantic write constraint; it no
longer selects a provider OS sandbox. Codex headless turns default to
`danger-full-access` and Claude headless turns default to
`bypassPermissions`; Kernel write-policy, artifact paths, and controlled
action gates remain independent.

Use the canonical launcher on a trusted local development host:

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

The launcher loads the action token and Workspace provider environment. Unless
explicitly overridden, it configures trusted-local WebKanban with:

```text
ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY=never
ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE=bypassPermissions
```

Verify the effective launch policy:

```bash
tools/start-webkanban.sh --port 8001 --status
```

The historical trusted-local default may include `codex_headless_sandbox: danger-full-access`,
`claude_headless_permission_mode: bypassPermissions`, `tmux: running`, and
`api: ok`. This does not qualify restricted execution; explicitly choose `workspace-write` on restricted deployments. `zf doctor provider` may still report that the host's normal sandbox
is unsupported; it checks host capability, not whether ZaoFu explicitly
bypasses it.

Direct `uv run zf web ...` uses the same code defaults. These variables are
only needed to record or override the launcher process explicitly:

```bash
export ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
export ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY=never
export ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE=bypassPermissions
uv run zf web --host 127.0.0.1 --port 8001
```

`danger-full-access` and `bypassPermissions` disable the provider-native
permission gates. Use them only for an explicitly trusted local repository and
network. Shared hosts, untrusted projects, and production-like environments
must not use this deployment default.

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
