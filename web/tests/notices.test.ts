import { completedRunNotices, isCompletedRunNotice } from "../src/components/agent-session/notices.js";
import type { AgentSessionPart } from "../src/components/agent-session/types.js";

function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

function part(over: Partial<AgentSessionPart>): AgentSessionPart {
  return { id: "p", runId: "r", kind: "status", state: "completed", ...over } as AgentSessionPart;
}

// frontend-stress OBS-1: the generic "Status" progress placeholder must collapse
// on a completed run; a real notice (meaningful title) must survive.
const placeholder = part({ id: "s1", title: "Status", summary: "streaming" });
const realNotice = part({ id: "s2", title: "未自动扇出(防风暴)", summary: "防风暴" });
const notRouted = part({ id: "s3", title: "Not routed", summary: "mention someone" });

assert(!isCompletedRunNotice(placeholder), "generic Status placeholder is not a notice");
assert(isCompletedRunNotice(realNotice), "防风暴 line is a real notice");
assert(isCompletedRunNotice(notRouted), "Not routed is a real notice");

const kept = completedRunNotices([placeholder, realNotice, notRouted], "completed").map((p) => p.id);
assert(JSON.stringify(kept) === JSON.stringify(["s2", "s3"]), "only real notices survive a completed run");

// A still-working run surfaces nothing through this path (progress lives elsewhere).
assert(completedRunNotices([placeholder, realNotice], "streaming").length === 0, "no notices on a working run");

// Accumulation guard: many rounds of the Status placeholder collapse to zero.
const manyRounds = Array.from({ length: 20 }, (_, i) => part({ id: `round-${i}`, title: "Status" }));
assert(completedRunNotices(manyRounds, "completed").length === 0, "20 rounds of Status placeholders collapse");

// operator review 2026-07-16: the kanban "Started <backend>" / "Queued" turn
// placeholders and channel progress lines are working-state trail too — a
// finished run must not keep a "Started claude-headless" row.
for (const title of ["Started", "Queued", "Working", "Waiting", "Sending", "Done"]) {
  assert(!isCompletedRunNotice(part({ id: `ph-${title}`, title })), `${title} placeholder is not a notice`);
}
assert(isCompletedRunNotice(part({ id: "cancel", title: "Cancel requested" })), "Cancel requested stays a real notice");

console.log("notices.test.ts OK");
