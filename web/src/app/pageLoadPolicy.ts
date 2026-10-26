import type { PageId } from "./sharedTypes";

export type SnapshotLoadKind = "none" | "board" | "light" | "full";

export type BootstrapResource =
  | "project.health"
  | "project.snapshot.light"
  | "project.snapshot.board"
  | "project.snapshot.full"
  | "delivery.features"
  | "channels.summary"
  | "events.recent"
  | "operator.inbox";

export interface PageResourcePlan {
  foreground: readonly BootstrapResource[];
}

export interface BootstrapCursorSources {
  recentEventsSeq?: number | null;
  snapshotSeq?: number | null;
  healthSeq?: number | null;
  fallbackSeq?: number | null;
}

export interface ProjectionFreshness {
  projection_state?: string;
  tail_behind?: boolean;
}

export interface ChannelEventRefreshPlan {
  conversation: boolean;
  summary: boolean;
}

const CHANNEL_CONVERSATION_ONLY_PREFIXES = [
  "channel.typing.",
  "channel.message.stream.",
  "channel.context_pack.",
  "channel.relay.",
  "channel.finding.",
] as const;

export const BOARD_REFRESH_PAGES = new Set<PageId>(["board", "project", "task", "triage"]);
export const MEASURE_REFRESH_PAGES = new Set<PageId>([
  "delivery",
  "delivery-trace",
  "delivery-graph",
]);

const LOOP_VIEW_REFRESH_NOISE = new Set([
  "agent.session.part.delta",
  "hook.orphan_event",
  "kanban.agent.message.delta",
  "kanban.agent.turn.delta",
  "orchestrator.round.complete",
  "provider.permission.snapshot.recorded",
  "provider.stop.check",
  "run.manager.agent.observation",
  "run.manager.tick.completed",
  "run.manager.tick.started",
  "runtime.snapshot.recorded",
  "run.heartbeat",
  "task.attempt.heartbeat",
  "task.requeue.skipped",
  "worker.heartbeat",
]);

const LOOP_VIEW_REFRESH_NOISE_PREFIXES = [
  "channel.context_pack.",
  "channel.message.stream.",
  "channel.relay.",
  "channel.typing.",
] as const;

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
  "task",
  "triage",
  "runtime",
  "settings",
  "diagnostics",
]);

export function isObservabilitySnapshotPage(page: PageId): boolean {
  return OBSERVABILITY_SNAPSHOT_PAGES.has(page);
}

export function snapshotLoadKindForPage(page: PageId): SnapshotLoadKind {
  if (page === "board" || page === "project") return "board";
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

// Loop owns /loop-view directly. Keep its SSE invalidation policy separate
// from Delivery so a Loop event never revives the unused /delivery-features
// request. Mechanical pump events intentionally wait for the next semantic
// event; they do not change the operator-facing loop census.
export function eventInvalidatesLoopView(eventType: string): boolean {
  if (!eventType || LOOP_VIEW_REFRESH_NOISE.has(eventType)) return false;
  return !LOOP_VIEW_REFRESH_NOISE_PREFIXES.some((prefix) => eventType.startsWith(prefix));
}

export function pagePollsOperatorInbox(page: PageId): boolean {
  return page === "inbox";
}

export function pageLoadsChannels(page: PageId): boolean {
  return pageOwnsBootstrapResource(page, "channels.summary");
}

export function pageLoadsRecentEvents(page: PageId): boolean {
  return pageOwnsBootstrapResource(page, "events.recent");
}

export function pageResourcePlanForPage(page: PageId): PageResourcePlan {
  const foreground: BootstrapResource[] = ["project.health"];
  const snapshotKind = snapshotLoadKindForPage(page);
  if (snapshotKind === "board") foreground.push("project.snapshot.board");
  if (snapshotKind === "light") foreground.push("project.snapshot.light");
  if (snapshotKind === "full") foreground.push("project.snapshot.full");
  if (pageLoadsDeliveryFeatures(page)) foreground.push("delivery.features");
  if (page === "channels") foreground.push("channels.summary");
  // Other consumers either own a scoped history endpoint or only need events
  // arriving after the SSE connection.
  if (page === "triage") foreground.push("events.recent");
  if (page === "inbox") foreground.push("operator.inbox");
  return { foreground };
}

export function pageOwnsBootstrapResource(
  page: PageId,
  resource: BootstrapResource,
): boolean {
  return pageResourcePlanForPage(page).foreground.includes(resource);
}

export function bootstrapEventCursor(sources: BootstrapCursorSources): number {
  if (sources.recentEventsSeq !== null && sources.recentEventsSeq !== undefined) {
    return validCursor(sources.recentEventsSeq);
  }
  return Math.max(
    validCursor(sources.snapshotSeq),
    validCursor(sources.healthSeq),
    validCursor(sources.fallbackSeq),
  );
}

export function projectionNeedsFresh(projection: ProjectionFreshness): boolean {
  return Boolean(
    projection.tail_behind
    || (projection.projection_state && projection.projection_state !== "ready"),
  );
}

export function channelEventRefreshPlan(eventType: string): ChannelEventRefreshPlan {
  if (!eventType.startsWith("channel.")) {
    return { conversation: false, summary: false };
  }
  return {
    conversation: true,
    summary: !CHANNEL_CONVERSATION_ONLY_PREFIXES.some((prefix) => (
      eventType.startsWith(prefix)
    )),
  };
}

function validCursor(value: number | null | undefined): number {
  const cursor = Number(value ?? 0);
  return Number.isInteger(cursor) && cursor >= 0 ? cursor : 0;
}
