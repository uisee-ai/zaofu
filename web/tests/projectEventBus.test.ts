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

  open(): void {
    this.onopen?.(new Event("open"));
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

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function drainMicrotasks(rounds = 8): Promise<void> {
  for (let index = 0; index < rounds; index += 1) await Promise.resolve();
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

async function testGapRecoveryRebasesCursorAndReconnects(): Promise<void> {
  const sources: MockEventSource[] = [];
  const statuses: string[] = [];
  const seen: string[] = [];
  const bus = new ProjectEventBus({
    cursor: 100,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async (_event, projectId) => {
      assert(projectId === "project-a", `unexpected recovery project: ${projectId}`);
      return 8;
    },
    onStatusChange: (state) => statuses.push(state),
  });
  bus.subscribe(({ event }) => seen.push(event.type));
  bus.connect();
  const oldSource = sources[0];

  oldSource.emitGap({
    type: "stream.gap",
    actor: "web",
    payload: { cursor: 100, current: 8 },
  }, "8");
  assert(oldSource.closed, "gap did not close the stale EventSource");
  await drainMicrotasks();

  assert(sources.length === 2, `expected one replacement source, got ${sources.length}`);
  assert(
    sources[1].url === "/api/projects/project-a/stream?cursor=8",
    `recovery did not rebase cursor: ${sources[1].url}`,
  );
  assert(statuses.join(",") === "connecting,reconnecting", `unexpected pre-open statuses: ${statuses.join(",")}`);
  sources[1].open();
  sources[1].emit({ type: "task.created", payload: {} }, "9");

  assert(statuses.at(-1) === "live", `recovered source did not become live: ${statuses.join(",")}`);
  assert(seen.join(",") === "stream.gap,task.created", `post-recovery event was dropped: ${seen.join(",")}`);
  assert(!statuses.includes("degraded"), `successful recovery degraded: ${statuses.join(",")}`);
}

async function testConcurrentGapsShareOneRecovery(): Promise<void> {
  const sources: MockEventSource[] = [];
  const recovery = deferred<number>();
  let recoveries = 0;
  const bus = new ProjectEventBus({
    cursor: 10,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async () => {
      recoveries += 1;
      return recovery.promise;
    },
  });
  bus.connect();
  const oldSource = sources[0];

  oldSource.emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  oldSource.emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  assert(recoveries === 1, `concurrent gaps started ${recoveries} recoveries`);
  recovery.resolve(8);
  await drainMicrotasks();

  assert(sources.length === 2, `concurrent gaps created ${sources.length - 1} replacement sources`);
}

async function testProjectSwitchCancelsOldGapRecovery(): Promise<void> {
  const sources: MockEventSource[] = [];
  const recovery = deferred<number>();
  const seen: string[] = [];
  const bus = new ProjectEventBus({
    cursor: 10,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async () => recovery.promise,
  });
  bus.subscribe(({ event }) => seen.push(event.type));
  bus.connect();

  sources[0].emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  bus.switchProject("project-b", 4);
  recovery.resolve(8);
  await drainMicrotasks();

  assert(sources.length === 2, `stale recovery created another source: ${sources.length}`);
  assert(
    sources[1].url === "/api/projects/project-b/stream?cursor=4",
    `project switch cursor was polluted: ${sources[1].url}`,
  );
  sources[1].emit({ type: "feature.created", payload: {} }, "5");
  assert(seen.at(-1) === "feature.created", `new project event was not delivered: ${seen.join(",")}`);
}

async function testGapRecoveryRetriesBeforeLive(): Promise<void> {
  const sources: MockEventSource[] = [];
  const statuses: string[] = [];
  const delays: number[] = [];
  let attempts = 0;
  const bus = new ProjectEventBus({
    cursor: 10,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("temporary failure");
      return 8;
    },
    onStatusChange: (state) => statuses.push(state),
    gapRecoveryBaseDelayMs: 10,
    gapRecoveryMaxDelayMs: 40,
    gapRecoveryRandom: () => 0.5,
    waitForGapRetry: async (delayMs) => {
      delays.push(delayMs);
    },
  });
  bus.connect();

  sources[0].emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  await drainMicrotasks(16);

  assert(attempts === 3, `expected three recovery attempts, got ${attempts}`);
  assert(delays.join(",") === "10,20", `unexpected retry backoff: ${delays.join(",")}`);
  assert(sources.length === 2, `successful retry did not reconnect: ${sources.length}`);
  assert(!statuses.includes("degraded"), `retry success was marked degraded: ${statuses.join(",")}`);
  sources[1].open();
  assert(statuses.at(-1) === "live", `retry recovery did not become live: ${statuses.join(",")}`);
}

async function testGapRecoveryExhaustionDegrades(): Promise<void> {
  const sources: MockEventSource[] = [];
  const statuses: string[] = [];
  let attempts = 0;
  const bus = new ProjectEventBus({
    cursor: 10,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async () => {
      attempts += 1;
      throw new Error("still unavailable");
    },
    onStatusChange: (state) => statuses.push(state),
    waitForGapRetry: async () => undefined,
  });
  bus.connect();

  sources[0].emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  await drainMicrotasks(16);

  assert(attempts === 3, `expected bounded attempts, got ${attempts}`);
  assert(statuses.at(-1) === "degraded", `exhausted recovery did not degrade: ${statuses.join(",")}`);
  assert(sources.length === 1, `failed recovery created a replacement source: ${sources.length}`);
}

async function testGapRecoveryWindowDegradesHungRequest(): Promise<void> {
  const sources: MockEventSource[] = [];
  const statuses: string[] = [];
  const bus = new ProjectEventBus({
    cursor: 10,
    projectId: "project-a",
    createEventSource: makeFactory(sources),
    onRecoverGap: async () => new Promise<number>(() => undefined),
    onStatusChange: (state) => statuses.push(state),
    gapRecoveryWindowMs: 10,
  });
  bus.connect();

  sources[0].emitGap({ type: "stream.gap", payload: { current: 8 } }, "8");
  await new Promise((resolve) => globalThis.setTimeout(resolve, 30));

  assert(statuses.at(-1) === "degraded", `hung recovery did not degrade: ${statuses.join(",")}`);
  assert(sources.length === 1, `hung recovery created a replacement source: ${sources.length}`);
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

function testLiveDeltasShareCommittedSeqStillStream(): void {
  // Regression (live-stream stuck-on-thinking): the server rides every
  // ephemeral LiveDeltaBus delta on the SSE wire with the *last committed*
  // seq on its `id:` line (events.py `_sse_event(last_seq, row)`), verified
  // live: three kanban deltas all arrived as `id: 2`. A committed event first
  // advances lastSeq; the deltas then share that exact seq. The old monotonic
  // gate (`seq <= lastSeq`) dropped every one -> stuck on "thinking" until a
  // page refresh. Deltas must bypass the gate and still stream.
  const sources: MockEventSource[] = [];
  const seen: string[] = [];
  const timers: Array<() => void> = [];
  const bus = new ProjectEventBus({
    cursor: 0,
    createEventSource: makeFactory(sources),
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

  // committed turn.started advances lastSeq to 2 (like a real ledger tail)
  sources[0].emit({ type: "kanban.agent.turn.started", seq: 2, payload: { turn_id: "t1" } }, "2");
  // three deltas ALL carry the same committed seq id ("2") — exactly what the
  // live SSE wire sends mid-turn.
  sources[0].emit({ type: "kanban.agent.turn.delta", payload: { turn_id: "t1", content: "Hel" } }, "2");
  sources[0].emit({ type: "kanban.agent.turn.delta", payload: { turn_id: "t1", content: "lo " } }, "2");
  sources[0].emit({ type: "kanban.agent.turn.delta", payload: { turn_id: "t1", content: "world" } }, "2");

  timers.shift()?.();
  assert(
    seen.includes("kanban.agent.turn.started:"),
    `committed event missing: ${seen.join("|")}`,
  );
  const streamed = seen.filter((row) => row.startsWith("kanban.agent.turn.delta:")).join("");
  assert(
    streamed.includes("Hel") && streamed.includes("lo ") && streamed.includes("world"),
    `live deltas sharing the committed seq were dropped: ${seen.join("|")}`,
  );
}

async function main(): Promise<void> {
  testMultipleSubscribersShareOneSource();
  testLiveDeltasShareCommittedSeqStillStream();
  testGapRefreshesOncePerBus();
  await testGapRecoveryRebasesCursorAndReconnects();
  await testConcurrentGapsShareOneRecovery();
  await testProjectSwitchCancelsOldGapRecovery();
  await testGapRecoveryRetriesBeforeLive();
  await testGapRecoveryExhaustionDegrades();
  await testGapRecoveryWindowDegradesHungRequest();
  testProjectSwitchClosesOldSourceAndIgnoresOldEvents();
  testErrorMarksReconnectingAndRequestsRefresh();
  testAgentStreamDeltasDoNotRefreshAndCoalesce();
}

await main();
