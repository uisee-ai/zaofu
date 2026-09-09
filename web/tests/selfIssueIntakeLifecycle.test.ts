import { selfIssueIntakeMountState } from "../src/components/orchestrator/selfIssue.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const minimized = selfIssueIntakeMountState({ intake_id: "intake-1" }, false);
assert(minimized.mounted, "minimizing an active Intake must keep its component mounted");
assert(minimized.hidden, "a minimized Intake must be hidden without being unmounted");

const expanded = selfIssueIntakeMountState({ intake_id: "intake-1" }, true);
assert(expanded.mounted && !expanded.hidden, "reopening the same Intake must reveal the mounted component");

const absent = selfIssueIntakeMountState(null, false);
assert(!absent.mounted, "an absent Intake must not leave an empty workspace mounted");
