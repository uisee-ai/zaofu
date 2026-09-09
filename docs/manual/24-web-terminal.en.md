# Web Terminal: run a real Coding Agent CLI in the browser

[中文](24-web-terminal.md)

> This public page covers installation, protocol boundaries, and common
> recovery paths. Production qualification remains deployment-specific.

Web Terminal runs a real Claude Code or Codex CLI (and, after host
qualification, OpenCode or Pi) inside a server-side Herdr PTY and renders it
with xterm.js. It is separate from the headless Kanban Agent. Closing a drawer
or browser does not stop the CLI; only `Stop CLI` terminates the session.

## Shareable demo

[![ZaoFu Web Terminal: cross-client control and offline recovery](assets/web-terminal-introduction-poster.png)](assets/web-terminal-introduction-zh-1080p.mp4)

[Open the player](assets/web-terminal-showcase.html) ·
[Download 1080p](assets/web-terminal-introduction-zh-1080p.mp4) ·
[Download 4K](assets/web-terminal-introduction-zh-4k.mp4) ·
[Showcase and evidence](showcases/web-terminal.en.md) ·
[Captions](assets/web-terminal-introduction-zh.vtt) ·
[Recording provenance](assets/web-terminal-showcase-provenance.v1.json)

The 116.96-second Chinese-narrated video comes from one real Docker Chromium +
Herdr + Codex run. In addition to Project-derived providers, a real PTY,
multi-tab, theme, and usage, it demonstrates three isolated browser clients
observing and explicitly taking over the same Session, server-side continuation
while the controller is offline, and full-history recovery in a fresh client.
It proves client disconnect/device handoff—not Dashboard or host-failure
recovery. See provenance for the exact boundary and hashes. The mixed Project
genuinely derives Claude Code, but this recording does not start it.

The original [52.8-second silent short demo](assets/web-terminal-demo.mp4) and
its [legacy provenance](assets/web-terminal-demo-provenance.v1.json) remain
available for a quick tour of the baseline interaction.

## 1. Install dependencies

Complete the ZaoFu, Web, and provider CLI installation in the
[Quickstart](01-quickstart.en.md), then install Herdr:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
# macOS/Linuxbrew: brew install herdr
# mise: mise use -g herdr

herdr --version
herdr api schema --json >/dev/null
herdr terminal session observe --help
herdr terminal session control --help
herdr tab rename --help
```

ZaoFu requires Herdr `>=0.8.0`. Production installations should pin and
record the qualified version, binary SHA256, and evidence rather than recording
only “latest”. Install and authenticate each provider CLI separately:

```bash
codex --version
claude --version
```

The Herdr native-session hooks are optional but recommended:

```bash
herdr integration install codex
herdr integration install claude
herdr integration status
```

Hooks improve native session identity and recovery metadata. They do not
replace provider authentication.

## 2. Default enablement and host configuration

Web Terminal is enabled on the Dashboard host by default. Default enablement
only makes the route and capability probe available; it does not install
Herdr, start a provider CLI, or bypass mutation authentication. When Herdr is
unavailable, the API returns a typed unavailable result and other surfaces such
as Board and Kanban Agent remain unaffected. No v4 or other target Project YAML
change is required.

Only configure the ZaoFu `zf.yaml` that starts the Dashboard when overriding
host paths, security, or resource policy:

```yaml
runtime:
  web_terminal:
    # Defaults to true; set false to disable Web Terminal for this host.
    enabled: true
    backend: herdr
    herdr_binary: herdr
    minimum_herdr_version: 0.8.0
    provider_start_timeout_seconds: 60
    allow_takeover: true
```

This is Dashboard host capability, security, and resource policy—not target
Project provider policy. Do not add `allowed_providers`. New Session derives
providers from the current Project's effective orchestrator and role backends.
A single-provider Project shows one provider; a Mixed team shows Claude Code
and Codex. Every registered Project allowed by the action token and mutation
authorization can use the host capability, while each Project keeps an
isolated cwd, Herdr named session, PTY, and registry.

Verify after startup:

```bash
uv run zf validate --cold-start
uv run zf web --host 127.0.0.1 --port 8001
curl -fsS http://127.0.0.1:8001/api/projects/default/terminal-sessions
```

The response should report `enabled=true`, `capability.available=true`, and an
`allowed_providers` projection matching the current Project. Remote deployments
must use a long-lived action token or passcode session and place the Dashboard
behind a trusted network/HTTPS boundary. A Herdr named session is not an OS
sandbox.

## 3. Sessions, tabs, and recovery

1. Open a Project and select the Terminal icon in the upper-right; it opens
   fullscreen by default.
2. Select `+` and choose a provider permitted by the Project configuration.
   The CLI cwd is the Project root.
3. Select `+` again for multiple independent sessions. Each tab has its own
   PTY, provider identity, and usage attribution.
4. Double-click a tab or use Rename under `…` to give it a useful name.
5. A tab `×`, drawer close, or browser reload only detaches. Reopening attaches
   to the same server-side PTY.
6. Only `Stop CLI` terminates that session; other tabs keep running.

Dock height, fullscreen/dock mode, theme, titles, and the current browser's open
tab set recover according to their own scope. Different browsers may expose
different UI tabs, but they attach to the same server-side PTY session.

## 4. Observe, Control, and Take over control

| Mode | Permission and behavior | Use it for |
|---|---|---|
| Observe | Read-only attachment; multiple viewers, no input, resize, or terminal mutation | Sharing and diagnosis |
| Control | Normal writable attachment; input, resize, and scroll; one controller per session | Routine interaction |
| Take over control | Explicit Herdr `--takeover`; replaces the current controller, which immediately loses control | Lost controller or deliberate device handoff |

Use Observe to watch, Control to work, and Take over only when deliberately
replacing another controller. Takeover is constrained by `allow_takeover`,
mutation authorization, and a takeover receipt.

## 5. Input and scrollback

In Control mode, xterm.js continues to send ordinary ASCII, Chinese/Unicode,
paste, and control sequences as terminal input. The wheel and unmodified
`PageUp` / `PageDown` become Herdr `terminal.scroll` commands, so Herdr's
Ghostty scrollback and the active TUI mode decide whether to traverse retained
history or apply terminal semantics. The browser does not maintain a second
provider-screen state, and Page keys are not also duplicated as raw input.

While a Chinese, Japanese, or other IME composition is active, Space, numeric
candidate keys, and candidate paging remain owned by the browser IME. xterm
does not encode them as CLI keys or turn them into `terminal.scroll`. If xterm
6 emits no `onData` after an IME replaces its hidden textarea, Web Terminal
sends the committed `compositionend` text exactly once as a fallback. Healthy
xterm commits stay on the normal path and are not duplicated. If an IME emits
only `Process/keyCode=229` followed by a plain `insertText` without a confirmed
composition, Web Terminal preserves xterm's textarea recovery path. Browser
automation covers both event shapes, but each supported host OS and IME still
needs manual qualification.

The Herdr viewport is terminal state shared by the controller, so subsequent
frames expose controller scrolling to other attachments. Observe never sends
scroll, resize, or input and can only inspect content retained locally by
xterm. Acquire Control before changing the shared viewport. Browser-reserved
shortcuts such as `Ctrl+W`, `Ctrl+T`, and `Ctrl+L` are not guaranteed to reach
the CLI.

## 6. Usage, cost, and safety boundaries

The Agents page lists each tab under `Interactive Terminals`, including
provider, model, context, tokens, cost, and precision. Rename does not reset
usage. Counters come from each CLI's structured transcript and are stored in a
separate `terminal-cost.jsonl`; they do not enter Workflow budgets. `awaiting
usage`, `unsupported`, or `—` means insufficient evidence, not zero usage.

The browser cannot choose an arbitrary executable, cwd, argv, or environment.
Terminal bytes do not enter EventLog, Tasks, Workflows, or Artifacts and cannot
advance Kernel state through screen scraping. For unavailable runtimes,
controller conflicts, and recovery failures, verify
[host configuration](#2-default-enablement-and-host-configuration) and
[session recovery](#3-sessions-tabs-and-recovery).
