import { LatestRequestGate } from "../src/app/latestRequestGate.js";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const gate = new LatestRequestGate();
const slow = gate.issue();
const fast = gate.issue();

assertEqual(gate.isCurrent(slow), false, "a newer request invalidates an older response");
assertEqual(gate.isCurrent(fast), true, "the latest response remains current");

gate.invalidate();
assertEqual(gate.isCurrent(fast), false, "cleanup invalidates an in-flight response");

console.log("latestRequestGate tests passed");
