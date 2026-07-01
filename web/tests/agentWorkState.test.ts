import {
  deriveComposerStatus,
  isWorking,
  showsWorking,
} from "../src/components/agent-session/workState.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

// --- isWorking ---
assert(isWorking("streaming"), "streaming is working");
assert(isWorking("submitted"), "submitted is working");
assert(!isWorking("completed"), "completed not working");
assert(!isWorking("idle"), "idle not working");
assert(!isWorking(undefined), "undefined not working");

// --- showsWorking gates ---
assert(showsWorking("streaming"), "streaming shows working by default");
assert(!showsWorking("streaming", { runnerOnline: false }), "known-offline suppresses");
assert(showsWorking("streaming", { runnerOnline: undefined }), "pre-poll does not suppress");
assert(!showsWorking("streaming", { hasPendingApproval: true }), "pending approval suppresses");
assert(!showsWorking("completed"), "completed never shows working");

// --- deriveComposerStatus ---
assert(deriveComposerStatus("streaming", false) === "streaming", "streaming → interrupt");
assert(deriveComposerStatus("submitted", false) === "submitted", "submitted → spinner");
assert(deriveComposerStatus("idle", true) === "submitted", "posting → spinner even before run flips");
assert(deriveComposerStatus("failed", false) === "error", "failed → error");
assert(deriveComposerStatus("completed", false) === "idle", "completed → idle (ready to send)");
assert(deriveComposerStatus(undefined, false) === "idle", "no run → idle");
// streaming wins over posting
assert(deriveComposerStatus("streaming", true) === "streaming", "live stream beats posting flag");

console.log("agentWorkState.test.ts OK");
