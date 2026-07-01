import { buildObservabilityEventWindow } from "../src/app/observabilityModel.js";
import type { EventRecord } from "../src/api/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function event(index: number): EventRecord {
  return {
    seq: index,
    id: `evt-${index}`,
    type: index % 2 === 0 ? "worker.heartbeat" : "dev.impl.completed",
    payload: {},
  };
}

const events = Array.from({ length: 10_000 }, (_, index) => event(index + 1));
const windowed = buildObservabilityEventWindow(events, { foldNoise: true, maxRows: 600 });

assert(windowed.hiddenNoiseCount === 5_000, `expected 5000 folded events, got ${windowed.hiddenNoiseCount}`);
assert(windowed.visibleEvents.length === 5_000, `expected 5000 visible events, got ${windowed.visibleEvents.length}`);
assert(windowed.renderedEvents.length === 600, `expected 600 rendered events, got ${windowed.renderedEvents.length}`);
assert(windowed.truncatedEventCount === 4_400, `expected 4400 truncated events, got ${windowed.truncatedEventCount}`);
assert(windowed.renderedEvents[0]?.seq === 1, "window keeps newest/source ordering stable");

const unfolded = buildObservabilityEventWindow(events, { foldNoise: false, maxRows: 600 });
assert(unfolded.hiddenNoiseCount === 0, "unfolded window must not hide heartbeat events");
assert(unfolded.renderedEvents.length === 600, "unfolded window still caps rendered rows");
