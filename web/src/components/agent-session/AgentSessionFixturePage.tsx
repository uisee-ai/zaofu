// Dev-only fixture page for visually verifying AgentSessionTimeline render
// states without a live backend. Reachable at `/?fixture=agent-session`
// (wired in main.tsx). Doubles as a deterministic playwright visual-regression
// target. Not linked from the app UI; harmless if hit in prod.

import { useState } from "react";
import { AgentSessionTimeline } from "./AgentSessionTimeline";
import { ComposerSubmitButton } from "./ComposerSubmitButton";
import type {
  AgentConversation,
  AgentSessionPart,
  AgentSessionRun,
  AgentSessionStatus,
  AgentSessionThread,
  AgentSessionTurn,
} from "./types";
import type { ComposerStatus } from "./workState";

let seq = 0;
function part(kind: AgentSessionPart["kind"], state: AgentSessionStatus, title: string, extra: Partial<AgentSessionPart> = {}): AgentSessionPart {
  seq += 1;
  return { id: `p${seq}`, runId: "r1", kind, state, title, ...extra };
}

const LONG_OUTPUT = Array.from({ length: 140 }, (_, i) => `  ${String(i).padStart(3, "0")}  log line with some detail about step ${i}`).join("\n");

// Streaming run: 6 tools (older fold into "See N steps", last 3 are the live
// tail) + a running tool + a thinking part.
const streamingRun: AgentSessionRun = {
  id: "r1", threadId: "main", provider: "claude-headless", memberId: "dev", status: "streaming",
  startedAt: new Date(Date.now() - 42_000).toISOString(),
  parts: [
    part("tool", "completed", "Tool read", { summary: "src/app/App.tsx", toolName: "read", startedAt: new Date(Date.now() - 40_000).toISOString(), updatedAt: new Date(Date.now() - 39_200).toISOString() }),
    part("tool", "completed", "Tool grep", { summary: "rg streaming", toolName: "grep", startedAt: new Date(Date.now() - 39_000).toISOString(), updatedAt: new Date(Date.now() - 38_800).toISOString() }),
    part("tool", "completed", "Tool edit", { summary: "ToolCard.tsx", toolName: "edit", startedAt: new Date(Date.now() - 38_000).toISOString(), updatedAt: new Date(Date.now() - 35_000).toISOString(), sourceEvent: { seq: 1, id: "e-edit", type: "agent.ui.delta", payload: { tool: "edit", args: { path: "src/ToolCard.tsx", old_string: "foo", new_string: "bar" } } } }),
    part("command", "completed", "Tool bash", { summary: "npm run build", content: LONG_OUTPUT, toolName: "bash", startedAt: new Date(Date.now() - 34_000).toISOString(), updatedAt: new Date(Date.now() - 29_500).toISOString() }),
    part("tool", "completed", "Tool read", { summary: "package.json", toolName: "read", startedAt: new Date(Date.now() - 29_000).toISOString(), updatedAt: new Date(Date.now() - 28_900).toISOString() }),
    part("tool", "streaming", "Tool web_search", { summary: "react streaming patterns", toolName: "web_search", startedAt: new Date(Date.now() - 6_000).toISOString() }),
  ],
};

// Completed run: thinking + long command output (exercises preview/gradient) +
// a reply.
const completedRun: AgentSessionRun = {
  id: "r2", threadId: "main", provider: "claude-headless", memberId: "dev", status: "completed",
  startedAt: new Date(Date.now() - 120_000).toISOString(),
  parts: [
    // Repro: projection emits a "status-started" placeholder with state
    // "submitted" + startedAt; finalize doesn't resolve "submitted", so on a
    // DONE run it lingers — a status part must NOT show a ticking tool timer.
    part("status", "submitted", "Started", { summary: "claude-headless", startedAt: new Date(Date.now() - 119_500).toISOString() }),
    part("thinking", "completed", "Thought", { content: "Plan: read the file, run the suite, summarize failures.", startedAt: new Date(Date.now() - 119_000).toISOString() }),
    part("test_result", "failed", "Tool pytest", { summary: "2 failed, 18 passed", content: "FAILED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y\n" + LONG_OUTPUT, toolName: "pytest", startedAt: new Date(Date.now() - 118_000).toISOString(), updatedAt: new Date(Date.now() - 110_000).toISOString() }),
    part("tool", "cancelled", "Tool deploy", { summary: "cancelled before output", toolName: "deploy", startedAt: new Date(Date.now() - 109_000).toISOString(), updatedAt: new Date(Date.now() - 108_000).toISOString() }),
    part("code_preview", "completed", "src/throttle.ts", { summary: "new helper", content: "```ts\nexport function throttle(ms: number) {\n  return (fn: () => void) => fn();\n}\n```" }),
    part("diff_preview", "completed", "src/RichMarkdownText.tsx", { summary: "+3 −1", content: "```diff\n- const text = content;\n+ const text = isStreaming ? throttled : content;\n```" }),
    part("text", "completed", "Reply", { content: "Done — the build passed but **2 tests fail** in `tests/test_a.py`.\n\nThe failures look related to the streaming refactor:\n\n- `test_x` — expects the old chunked payload shape\n- `test_y` — timing assertion now races the throttle\n\nWant me to fix them, or just update the assertions?" }),
  ],
};

const turn: AgentSessionTurn = {
  id: "t1", threadId: "main", ts: new Date(Date.now() - 130_000).toISOString(),
  user: { id: "u1", role: "user", label: "You", content: "Build the project and report **failures** in `tests/`.", ts: new Date(Date.now() - 130_000).toISOString() },
  runs: [completedRun, streamingRun],
  cards: [],
};

const thread: AgentSessionThread = {
  id: "main", title: "main", status: "streaming", unseenCount: 0,
  turns: [turn], updatedAt: new Date().toISOString(),
};

const conversation: AgentConversation = {
  id: "fixture", surface: "kanban_agent", activeThreadId: "main", threads: [thread],
};

// Same data as a channel_group surface — verifies channel activity now folds
// via the same "See N steps" segmenter (Phase 1 #2).
const channelConversation: AgentConversation = {
  id: "fixture-ch", surface: "channel_group", activeThreadId: "main",
  threads: [{ ...thread, id: "main" }],
};

const COMPOSER_STATES: ComposerStatus[] = ["idle", "submitted", "streaming", "error"];

export function AgentSessionFixturePage() {
  const [interrupted, setInterrupted] = useState("");
  return (
    <div style={{ padding: 24, maxWidth: 980, margin: "0 auto" }}>
      <h2>AgentSessionTimeline fixture</h2>
      <p className="muted">Dev-only render harness — streaming run (fold + tail), completed run (long output / 4-state tools), thinking.</p>

      <h3 style={{ marginTop: 24 }}>ComposerSubmitButton — all states</h3>
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 8 }}>
        {COMPOSER_STATES.map((status) => (
          <span key={status} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <code>{status}</code>
            <ComposerSubmitButton
              className="headless-send-button"
              status={status}
              onStop={() => setInterrupted(`interrupted @ ${status}`)}
            />
          </span>
        ))}
      </div>
      {interrupted ? <p data-testid="interrupt-echo" className="muted">{interrupted}</p> : null}

      <h3 style={{ marginTop: 24 }}>Timeline — kanban_agent(orchestrator 同款 props)</h3>
      <AgentSessionTimeline
        activeThreadId="main"
        conversation={conversation}
        compact
        compactRunHeader
        collapseCompletedRunDetails
        minimalRunActivity
        showRunDetails={false}
        showRunProvider={false}
      />

      <h3 style={{ marginTop: 24 }}>Timeline — channel_group(折叠 + 居中列)</h3>
      <div className="channel-page-chat">
        <AgentSessionTimeline activeThreadId="main" compact conversation={channelConversation} collapseCompletedRunDetails minimalRunActivity />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — kanban fullscreen(点 preview ⤢ → 分屏预览)</h3>
      <div data-testid="fx-kanban-fs" style={{ height: 420, display: "flex" }}>
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={conversation}
          allowPreviewSplit
          collapseCompletedRunDetails
          minimalRunActivity
          showRunDetails={false}
          showRunProvider={false}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Timeline — channel fullscreen(分屏预览同样支持)</h3>
      <div data-testid="fx-channel-fs" className="channel-page-chat" style={{ height: 420, display: "flex" }}>
        <AgentSessionTimeline
          activeThreadId="main"
          conversation={channelConversation}
          allowPreviewSplit
          collapseCompletedRunDetails
          minimalRunActivity
        />
      </div>
    </div>
  );
}
