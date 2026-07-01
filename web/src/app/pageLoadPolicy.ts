import type { PageId } from "./sharedTypes";

export type SnapshotLoadKind = "none" | "light" | "full";

export const BOARD_REFRESH_PAGES = new Set<PageId>(["board", "project", "task", "triage"]);
export const MEASURE_REFRESH_PAGES = new Set<PageId>(["delivery", "delivery-trace", "delivery-graph", "behavior-loop"]);

const OBSERVABILITY_SNAPSHOT_PAGES = new Set<PageId>([
  "observability",
  "events",
  "runs",
  "fanouts",
  "candidates",
  "workdirs",
  "skills",
  "archives",
]);

const LIGHT_SNAPSHOT_PAGES = new Set<PageId>([
  "project",
  "board",
  "task",
  "triage",
  "traces",
  "runtime",
  "settings",
  "diagnostics",
]);

export function isObservabilitySnapshotPage(page: PageId): boolean {
  return OBSERVABILITY_SNAPSHOT_PAGES.has(page);
}

export function snapshotLoadKindForPage(page: PageId): SnapshotLoadKind {
  if (OBSERVABILITY_SNAPSHOT_PAGES.has(page)) return "full";
  if (LIGHT_SNAPSHOT_PAGES.has(page)) return "light";
  return "none";
}

export function pageLoadsSnapshot(page: PageId): boolean {
  return snapshotLoadKindForPage(page) !== "none";
}

export function pageLoadsDeliveryFeatures(page: PageId): boolean {
  return MEASURE_REFRESH_PAGES.has(page);
}

export function pagePollsOperatorInbox(page: PageId): boolean {
  return page === "inbox";
}
