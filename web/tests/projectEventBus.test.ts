import { ProjectEventBus, type EventSourceLike } from "../src/app/projectEventBus.js";
import type { RecentEvent } from "../src/api/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

class MockEventSource implements EventSourceLike {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;
  listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  close(): void {
    this.closed = true;
  }

  emit(event: RecentEvent, id = "1"): void {
    this.onmessage?.(message(event, id));
  }

  emitGap(event: RecentEvent, id = "1"): void {
    for (const listener of this.listeners.get("stream.gap") ?? []) {
      listener(message(event, id));
    }
  }
}

function message(event: RecentEvent, lastEventId: string): MessageEvent {
  return {
    data: JSON.stringify(event),
    lastEventId,
  } as MessageEvent;
}

function makeFactory(sources: MockEventSource[]) {
  return (url: string): EventSourceLike => {
    const source = new MockEventSource(url);
    sources.push(source);
    return source;
  };
}

function testMultipleSubscribersShareOneSource(): void {
  const sources: MockEventSource[] = [];
  const bus = new ProjectEventBus({
    cursor: 3,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
  });
  const seenA: string[] = [];
  const seenB: string[] = [];
  bus.subscribe(({ event }) => seenA.push(event.type));
  bus.subscribe(({ event }) => seenB.push(event.type));
  bus.connect();

  assert(sources.length === 1, "expected one EventSource for two subscribers");
  assert(
    sources[0].url === "/api/projects/project-a/stream?cursor=3",
    `unexpected stream URL: ${sources[0].url}`,
  );
  sources[0].emit({ type: "task.created", payload: {} }, "4");

  assert(seenA.join(",") === "task.created", "subscriber A did not receive event");
  assert(seenB.join(",") === "task.created", "subscriber B did not receive event");
}

function testGapRefreshesOncePerBus(): void {
  const sources: MockEventSource[] = [];
  let refreshes = 0;
  const bus = new ProjectEventBus({
    cursor: 10,
    createEventSource: makeFactory(sources),
    onRefresh: (_event, reason) => {
      if (reason === "gap") refreshes += 1;
    },
  });
  bus.subscribe(() => undefined);
  bus.subscribe(() => undefined);
  bus.connect();

  sources[0].emitGap({ type: "stream.gap", actor: "web", payload: {} }, "8");

  assert(refreshes === 1, `expected one gap refresh, got ${refreshes}`);
}

function testProjectSwitchClosesOldSourceAndIgnoresOldEvents(): void {
  const sources: MockEventSource[] = [];
  const seen: string[] = [];
  const bus = new ProjectEventBus({
    cursor: 0,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
  });
  bus.subscribe(({ event }) => seen.push(event.type));
  bus.connect();
  const oldSource = sources[0];

  bus.switchProject("project-b", 0);
  assert(oldSource.closed, "old project source was not closed");
  oldSource.emit({ type: "task.created", payload: {} }, "1");
  sources[1].emit({ type: "feature.created", payload: {} }, "1");

  assert(seen.join(",") === "feature.created", `old project event leaked: ${seen.join(",")}`);
}

function testErrorMarksReconnectingAndRequestsRefresh(): void {
  const sources: MockEventSource[] = [];
  const statuses: string[] = [];
  const refreshReasons: string[] = [];
  const bus = new ProjectEventBus({
    cursor: 5,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRefresh: (_event, reason) => refreshReasons.push(reason),
    onStatusChange: (state) => statuses.push(state),
  });
  bus.connect();

  sources[0].onerror?.(new Event("error"));

  assert(statuses.join(",") === "connecting,reconnecting", `unexpected statuses: ${statuses.join(",")}`);
  assert(refreshReasons.join(",") === "error", `unexpected refresh reasons: ${refreshReasons.join(",")}`);
}

function testAgentStreamDeltasDoNotRefreshAndCoalesce(): void {
  const sources: MockEventSource[] = [];
  const seen: string[] = [];
  const refreshReasons: string[] = [];
  const timers: Array<() => void> = [];
  const bus = new ProjectEventBus({
    cursor: 0,
    createEventSource: makeFactory(sources),
    shouldRefresh: (event) => event.type.startsWith("agent.session."),
    onRefresh: (_event, reason) => refreshReasons.push(reason),
    agentStreamFlushMs: 16,
    setTimer: (callback) => {
      timers.push(callback);
      return callback;
    },
    clearTimer: () => undefined,
  });
  bus.subscribe(({ event }) => {
    const payload = event.payload ?? {};
    seen.push(`${event.type}:${String(payload.content || payload.delta || "")}`);
  });
  bus.connect();

  sources[0].emit({
    type: "agent.session.part.delta",
    payload: { run_id: "run-1", part_id: "text", content: "first " },
  }, "1");
  sources[0].emit({
    type: "agent.session.part.delta",
    payload: { run_id: "run-1", part_id: "text", content: "second " },
  }, "2");
  sources[0].emit({
    type: "agent.session.part.delta",
    payload: { run_id: "run-1", part_id: "text", content: "third" },
  }, "3");

  assert(seen.join("|") === "agent.session.part.delta:first ", `first delta was not immediate: ${seen.join("|")}`);
  assert(refreshReasons.length === 0, `delta requested refresh: ${refreshReasons.join(",")}`);
  timers.shift()?.();
  assert(
    seen.join("|") === "agent.session.part.delta:first |agent.session.part.delta:second third",
    `deltas were not coalesced: ${seen.join("|")}`,
  );

  sources[0].emit({ type: "agent.session.run.completed", payload: { run_id: "run-1" } }, "4");
  assert(refreshReasons.join(",") === "event", `terminal did not refresh: ${refreshReasons.join(",")}`);
}

testMultipleSubscribersShareOneSource();
testGapRefreshesOncePerBus();
testProjectSwitchClosesOldSourceAndIgnoresOldEvents();
testErrorMarksReconnectingAndRequestsRefresh();
testAgentStreamDeltasDoNotRefreshAndCoalesce();
