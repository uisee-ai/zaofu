import {
  selectTaskFocus,
  taskFocusForFilters,
} from "../src/components/kanban/taskToolbarSelection.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function assertSelection(
  actual: { statusFilter: string; quickFilter: string },
  statusFilter: string,
  quickFilter: string,
  message: string,
): void {
  assert(
    actual.statusFilter === statusFilter && actual.quickFilter === quickFilter,
    `${message}: got ${actual.statusFilter}/${actual.quickFilter}`,
  );
}

assert(taskFocusForFilters("all", "all") === "all", "all filters map to All");
assert(taskFocusForFilters("all", "ready") === "ready", "ready signal maps to Ready");
assert(taskFocusForFilters("all", "blocked") === "blocked", "blocked signal maps to Blocked");
assert(taskFocusForFilters("testing", "all") === "verify", "testing status maps to Verify");
assert(taskFocusForFilters("testing", "ready") === null, "custom intersections do not light two shortcuts");
assert(taskFocusForFilters("all", "focused") === null, "advanced focused mode stays explicit");

assertSelection(selectTaskFocus("all", "focused", "ready"), "all", "ready", "Ready normalizes filters");
assertSelection(selectTaskFocus("all", "ready", "ready"), "all", "all", "active Ready toggles to All");
assertSelection(selectTaskFocus("all", "ready", "verify"), "testing", "all", "Verify clears Ready");
assertSelection(selectTaskFocus("testing", "all", "blocked"), "all", "blocked", "Blocked clears Verify");
assertSelection(selectTaskFocus("testing", "blocked", "all"), "all", "all", "All clears custom filters");

// eslint-disable-next-line no-console
console.log("taskToolbarSelection.test.ts OK");
