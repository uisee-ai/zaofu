export type TaskFocus = "all" | "ready" | "blocked" | "verify";

export interface TaskToolbarSelection {
  statusFilter: string;
  quickFilter: string;
}

export const TASK_FOCUS_OPTIONS: ReadonlyArray<{ value: TaskFocus; label: string }> = [
  { value: "all", label: "All" },
  { value: "ready", label: "Ready" },
  { value: "blocked", label: "Blocked" },
  { value: "verify", label: "Verify" },
];

const FOCUS_SELECTIONS: Record<TaskFocus, TaskToolbarSelection> = {
  all: { statusFilter: "all", quickFilter: "all" },
  ready: { statusFilter: "all", quickFilter: "ready" },
  blocked: { statusFilter: "all", quickFilter: "blocked" },
  verify: { statusFilter: "testing", quickFilter: "all" },
};

export function taskFocusForFilters(statusFilter: string, quickFilter: string): TaskFocus | null {
  for (const option of TASK_FOCUS_OPTIONS) {
    const selection = FOCUS_SELECTIONS[option.value];
    if (selection.statusFilter === statusFilter && selection.quickFilter === quickFilter) {
      return option.value;
    }
  }
  return null;
}

export function selectTaskFocus(
  statusFilter: string,
  quickFilter: string,
  requested: TaskFocus,
): TaskToolbarSelection {
  const next = requested !== "all" && taskFocusForFilters(statusFilter, quickFilter) === requested
    ? FOCUS_SELECTIONS.all
    : FOCUS_SELECTIONS[requested];
  return { ...next };
}
