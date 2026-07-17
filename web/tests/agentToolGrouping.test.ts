import {
  findStreamingRunStart,
  partitionToolRun,
  segmentRunParts,
} from "../src/components/agent-session/toolGrouping.js";
import type { AgentSessionPart, AgentSessionStatus } from "../src/components/agent-session/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function part(id: string, kind: string, state: AgentSessionStatus = "completed"): AgentSessionPart {
  return { id, runId: "r1", kind: kind as AgentSessionPart["kind"], state, title: id };
}

// --- findStreamingRunStart ---
const endsText = [part("a", "tool"), part("b", "text")];
assert(findStreamingRunStart(endsText) === -1, "ends in text → -1");
const endsTools = [part("t", "text"), part("a", "tool"), part("b", "tool")];
assert(findStreamingRunStart(endsTools) === 1, `trailing tool run starts at 1, got ${findStreamingRunStart(endsTools)}`);
assert(findStreamingRunStart([]) === -1, "empty → -1");

// --- partitionToolRun (streaming run keeps the most recent tool visible;
// stream-ux axis 3: one line of current activity, earlier tools fold) ---
const sixTools = Array.from({ length: 6 }, (_, i) => part(`tool${i}`, "tool"));
const live = partitionToolRun(sixTools, true);
assert(live.standalone.length === 1, `live run exposes 1, got ${live.standalone.length}`);
assert(live.standalone[0]!.id === "tool5", "live run exposes the most recent tool");
assert(live.grouped.length === 5, `live run folds 5, got ${live.grouped.length}`);

// --- partitionToolRun (idle run folds all completed, keeps in-progress) ---
const mixed = [part("done1", "tool", "completed"), part("running", "tool", "streaming"), part("done2", "tool", "completed")];
const idle = partitionToolRun(mixed, false);
assert(idle.standalone.length === 1 && idle.standalone[0]!.id === "running", "idle run exposes only in-progress");
assert(idle.grouped.length === 2, "idle run folds completed tools");

// --- segmentRunParts: count reflects full run, not just folded ---
const segments = segmentRunParts(sixTools, "streaming");
assert(segments.length === 1 && segments[0]!.kind === "tools", "one tool segment");
const seg = segments[0] as Extract<typeof segments[number], { kind: "tools" }>;
assert(seg.total === 6, `segment total is full run length 6, got ${seg.total}`);
assert(seg.standalone.length === 1, "streaming tail of 1 exposed");
assert(seg.live, "trailing tool run of a streaming run is live");

// --- segmentRunParts: idle run folds entirely ---
const idleSegments = segmentRunParts(sixTools, "completed");
const idleSeg = idleSegments[0] as Extract<typeof idleSegments[number], { kind: "tools" }>;
assert(idleSeg.standalone.length === 0, "completed run folds all (no in-progress)");
assert(idleSeg.grouped.length === 6, "completed run folds all 6");
assert(!idleSeg.live, "completed run has no live segment");

// --- segmentRunParts: text after tools keeps them folded (not the live tail) ---
const toolsThenText = [...sixTools, part("reply", "text")];
const mixedSegments = segmentRunParts(toolsThenText, "streaming");
assert(mixedSegments.length === 2, "tool segment + text segment");
const foldedSeg = mixedSegments[0] as Extract<typeof mixedSegments[number], { kind: "tools" }>;
assert(foldedSeg.standalone.length === 0, "tools followed by text are no longer the live tail");
assert(!foldedSeg.live, "tools followed by text are not live");

console.log("agentToolGrouping.test.ts OK");
