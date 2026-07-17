import { ProjectRequestScope } from "../src/app/projectRequestScope.js";

function assertEqual(actual: unknown, expected: unknown, message = "values differ"): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const scope = new ProjectRequestScope("project-a");
const first = scope.capture();

assertEqual(scope.isCurrent(first), true);
scope.activate("project-a");
assertEqual(scope.isCurrent(first), true, "same project must not invalidate reads");

scope.activate("project-b");
assertEqual(scope.isCurrent(first), false, "old project response must be ignored");
assertEqual(scope.isCurrent(scope.capture()), true);
assertEqual(
  scope.isCurrent(scope.capture("project-a")),
  false,
  "a ticket cannot impersonate a non-active project",
);

console.log("projectRequestScope tests passed");
