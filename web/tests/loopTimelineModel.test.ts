import type { LoopViewAttempt, LoopViewTask } from "../src/api/types.js";
import { attemptRange, selectedAttemptIndices, timelineAttemptKey, timelineModel, timelineSegments } from "../src/components/delivery-trace/loopTimelineModel.js";

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
const at = (minutes: number) => new Date(Date.UTC(2026, 9, 27, 0, minutes)).toISOString();
const attempt = (start: number, end: number | null, extra = {}): LoopViewAttempt => ({
  started_ts: at(start), role: "developer", counted: true, open: end === null,
  terminal: end === null ? null : { ts: at(end), type: "dev.completed" }, ...extra,
});
const task = (attempts: LoopViewAttempt[]): LoopViewTask => ({ id: "T1", attempts, counted: attempts.length, fails: 0, source: "test" });
const sparse = [task([attempt(0, 10), attempt(120, 130)])];
const linear = timelineModel(sparse, at(130), false)!;
assert(linear.at(Date.parse(at(65))) === 50, "real time retains idle wall time");
assert(linear.gaps.length === 0, "linear scale has no compressed gaps");
const compressed = timelineModel(sparse, at(130), true)!;
assert(compressed.gaps.length === 1 && compressed.gaps[0].duration === 110 * 60000, "only true idle gaps compress");
const longAttempt = [task([attempt(0, 130), attempt(60, 65)])];
assert(timelineModel(longAttempt, at(130), true)!.gaps.length === 0, "execution spanning sparse events prevents compression");
const overlap = timelineSegments(longAttempt[0], timelineModel(longAttempt, at(130), false)!);
assert(overlap.segments[1].left === 60 / 130 * 100, "overlapping attempt start never shifts");
assert(overlap.segments[0].lane !== overlap.segments[1].lane, "real overlaps receive separate lanes");
const unknown = attempt(10, 20, { orphan: true });
assert(!attemptRange(unknown, Date.parse(at(30))).known, "orphan event does not imply duration");
assert(!attemptRange(attempt(10, null, { counted: false }), Date.parse(at(30))).known, "superseded open interval has no end");
assert(!attemptRange(attempt(20, 10), Date.parse(at(30))).known, "reversed timestamps do not invent a duration");
const noStart = attempt(10, 20, { started_ts: "" });
assert(attemptRange(noStart, Date.parse(at(30))).start === Date.parse(at(20)), "terminal-only record retains its event time");
assert(!attemptRange(noStart, Date.parse(at(30))).known, "terminal-only record remains a point");
const open = attempt(10, null);
assert(attemptRange(open, Date.parse(at(30))).end === Date.parse(at(30)), "open extent ends at observed evidence, not wall clock");
const dense = task(Array.from({ length: 875 }, (_, i) => attempt(i, i + 0.5)));
const before = JSON.stringify(dense);
const groups = timelineSegments(dense, timelineModel([dense], at(875), false)!);
const indices = groups.segments.flatMap((segment) => segment.indices).sort((a, b) => a - b);
assert(groups.segments.length < 30, "high churn has bounded visual groups");
assert(indices.length === 875 && indices.every((value, index) => value === index), "every grouped attempt retains its source index");
assert(JSON.stringify(dense) === before, "model does not mutate canonical records");
const manyGaps = [task(Array.from({ length: 80 }, (_, i) => attempt(i * 60, i * 60)))];
const many = timelineModel(manyGaps, at(80 * 60), true)!;
assert(many.gaps.every((gap) => gap.left >= 0 && gap.left + gap.width <= 100), "many gaps never produce negative or overflowing coordinates");
assert(timelineModel([], "", false) === null, "empty data has no invented axis");
console.log("Loop timeline model: time scale, overlap, unknown durations and bounded grouping passed");

const picked = attempt(10, null);
const key = timelineAttemptKey(picked);
const reordered = task([attempt(40, 50), attempt(10, 20)]);
assert(selectedAttemptIndices(reordered, [key])[0] === 1, "selection survives prepended attempts and an arriving terminal result");
assert(selectedAttemptIndices(task([attempt(40, 50)]), [key]).length === 0, "missing selected evidence never selects a different attempt");
assert(selectedAttemptIndices(task([picked, picked]), [key]).length === 0, "ambiguous attempt identity fails closed");
