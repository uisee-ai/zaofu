import type { RecentEvent } from "../api/types";
import { isAgentStreamDeltaEvent } from "../components/agent-session/agentUiEvent.js";
import { AgentStreamBatcher } from "./agentStreamBatcher.js";

export type LiveConnectionState = "connecting" | "live" | "reconnecting" | "degraded";

export interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;
export type GapRecoveryHandler = (event: RecentEvent, projectId: string) => Promise<number>;

export interface ProjectEventBusOptions {
  cursor: number;
  projectId?: string;
  createEventSource?: EventSourceFactory;
  shouldRefresh?: (event: RecentEvent) => boolean;
  onRefresh?: (event: RecentEvent, reason: "event" | "gap" | "error") => void;
  onRecoverGap?: GapRecoveryHandler;
  onStatusChange?: (state: LiveConnectionState) => void;
  agentStreamFlushMs?: number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
  gapRecoveryMaxAttempts?: number;
  gapRecoveryWindowMs?: number;
  gapRecoveryBaseDelayMs?: number;
  gapRecoveryMaxDelayMs?: number;
  gapRecoveryNow?: () => number;
  gapRecoveryRandom?: () => number;
  waitForGapRetry?: (delayMs: number) => Promise<void>;
}

export interface ProjectEventBusMessage {
  event: RecentEvent;
  seq: number;
  raw: MessageEvent;
}

type ProjectEventBusSubscriber = (message: ProjectEventBusMessage) => void;

export class ProjectEventBus {
  private readonly createEventSource: EventSourceFactory;
  private readonly shouldRefresh: (event: RecentEvent) => boolean;
  private readonly onRefresh?: ProjectEventBusOptions["onRefresh"];
  private readonly onRecoverGap?: GapRecoveryHandler;
  private readonly onStatusChange?: ProjectEventBusOptions["onStatusChange"];
  private readonly gapRecoveryMaxAttempts: number;
  private readonly gapRecoveryWindowMs: number;
  private readonly gapRecoveryBaseDelayMs: number;
  private readonly gapRecoveryMaxDelayMs: number;
  private readonly gapRecoveryNow: () => number;
  private readonly gapRecoveryRandom: () => number;
  private readonly waitForGapRetry: (delayMs: number) => Promise<void>;
  private generation = 0;
  private lastSeq: number;
  private projectId: string;
  private source: EventSourceLike | null = null;
  private gapRecoveryToken: object | null = null;
  private subscribers = new Set<ProjectEventBusSubscriber>();
  private readonly streamBatcher: AgentStreamBatcher<MessageEvent>;

  constructor(options: ProjectEventBusOptions) {
    this.projectId = options.projectId ?? "";
    this.lastSeq = Math.max(0, options.cursor || 0);
    this.createEventSource = options.createEventSource ?? defaultEventSourceFactory;
    this.shouldRefresh = options.shouldRefresh ?? (() => false);
    this.onRefresh = options.onRefresh;
    this.onRecoverGap = options.onRecoverGap;
    this.onStatusChange = options.onStatusChange;
    this.gapRecoveryMaxAttempts = Math.max(1, Math.trunc(options.gapRecoveryMaxAttempts ?? 3));
    this.gapRecoveryWindowMs = Math.max(1, options.gapRecoveryWindowMs ?? 10_000);
    this.gapRecoveryBaseDelayMs = Math.max(0, options.gapRecoveryBaseDelayMs ?? 250);
    this.gapRecoveryMaxDelayMs = Math.max(
      this.gapRecoveryBaseDelayMs,
      options.gapRecoveryMaxDelayMs ?? 2_000,
    );
    this.gapRecoveryNow = options.gapRecoveryNow ?? (() => Date.now());
    this.gapRecoveryRandom = options.gapRecoveryRandom ?? (() => Math.random());
    this.waitForGapRetry = options.waitForGapRetry ?? waitForDelay;
    this.streamBatcher = new AgentStreamBatcher<MessageEvent>({
      flushMs: options.agentStreamFlushMs,
      setTimer: options.setTimer,
      clearTimer: options.clearTimer,
      onFlush: (items) => {
        for (const item of items) this.emit(item.event, item.seq, item.raw);
      },
    });
  }

  connect(): void {
    this.gapRecoveryToken = null;
    const generation = ++this.generation;
    this.closeSource();
    this.openSource(generation, true);
  }

  private openSource(generation: number, announceConnecting: boolean): void {
    if (generation !== this.generation) return;
    if (announceConnecting) this.onStatusChange?.("connecting");
    const source = this.createEventSource(streamPath(this.projectId, this.lastSeq));
    this.source = source;
    source.onopen = () => {
      if (generation !== this.generation) return;
      this.onStatusChange?.("live");
    };
    source.onerror = (event) => {
      if (generation !== this.generation) return;
      this.onStatusChange?.("reconnecting");
      this.onRefresh?.(syntheticStreamEvent("stream.error"), "error");
      void event;
    };
    source.onmessage = (event) => {
      if (generation !== this.generation) return;
      this.handleMessage(event);
    };
    source.addEventListener("stream.gap", (event) => {
      if (generation !== this.generation) return;
      this.handleGap(event);
    });
  }

  subscribe(subscriber: ProjectEventBusSubscriber): () => void {
    this.subscribers.add(subscriber);
    return () => {
      this.subscribers.delete(subscriber);
    };
  }

  switchProject(projectId: string, cursor: number): void {
    this.projectId = projectId;
    this.lastSeq = Math.max(0, cursor || 0);
    this.connect();
  }

  close(): void {
    this.generation += 1;
    this.gapRecoveryToken = null;
    this.streamBatcher.close();
    this.closeSource();
  }

  private closeSource(): void {
    if (!this.source) return;
    this.source.close();
    this.source = null;
  }

  private handleMessage(message: MessageEvent): void {
    const event = parseSseEvent(message);
    if (!event) {
      this.onStatusChange?.("degraded");
      return;
    }
    // Ephemeral live deltas (LiveDeltaBus rows) never advance the ledger: the
    // SSE wire stamps every one with the *last committed* seq on its `id:`
    // line (events.py `_sse_event(last_seq, row)`). Subjecting them to the
    // strictly-increasing committed-seq gate drops every delta after the last
    // real event — the stream stays stuck on "thinking" until a history
    // refetch (page refresh) shows the committed answer. So deltas must skip
    // the monotonic gate, must not advance lastSeq, and must fold with no seq
    // (their identity is the row `id`, not the shared committed seq — a real
    // seq here collapses every delta under seq-keyed dedup downstream).
    const isLiveDelta = isAgentStreamDeltaEvent(event.type);
    const seq = eventSeq(event, message);
    if (!isLiveDelta) {
      if (seq && seq <= this.lastSeq) return;
      if (seq) this.lastSeq = seq;
    }
    const foldSeq = isLiveDelta ? 0 : seq;
    const normalized = normalizeSeq(event, foldSeq);
    if (this.streamBatcher.handle({ event: normalized, seq: foldSeq, raw: message })) {
      if (!isAgentStreamDeltaEvent(normalized.type) && this.shouldRefresh(normalized)) {
        this.onRefresh?.(normalized, "event");
      }
      return;
    }
    this.emit(normalized, foldSeq, message);
    if (this.shouldRefresh(normalized)) {
      this.onRefresh?.(normalized, "event");
    }
  }

  private handleGap(message: MessageEvent): void {
    const event = parseSseEvent(message) ?? syntheticStreamEvent("stream.gap");
    const seq = eventSeq(event, message);
    const normalized = normalizeSeq(event, seq);
    this.emit(normalized, seq, message);
    if (!this.onRecoverGap) {
      this.onStatusChange?.("degraded");
      this.onRefresh?.(normalized, "gap");
      return;
    }
    this.startGapRecovery(normalized);
  }

  private startGapRecovery(event: RecentEvent): void {
    if (this.gapRecoveryToken) return;
    const token = {};
    const generation = ++this.generation;
    const projectId = this.projectId;
    this.gapRecoveryToken = token;
    this.streamBatcher.flush();
    this.closeSource();
    this.onStatusChange?.("reconnecting");
    void this.recoverGap(event, projectId, generation).finally(() => {
      if (this.gapRecoveryToken === token) this.gapRecoveryToken = null;
    });
  }

  private async recoverGap(event: RecentEvent, projectId: string, generation: number): Promise<void> {
    const startedAt = this.gapRecoveryNow();
    for (let attempt = 1; attempt <= this.gapRecoveryMaxAttempts; attempt += 1) {
      if (generation !== this.generation || projectId !== this.projectId) return;
      const elapsed = this.gapRecoveryNow() - startedAt;
      const remainingMs = this.gapRecoveryWindowMs - elapsed;
      if (remainingMs <= 0) break;
      try {
        const recoveredCursor = await withTimeout(
          this.onRecoverGap!(event, projectId),
          remainingMs,
        );
        if (generation !== this.generation || projectId !== this.projectId) return;
        if (!Number.isInteger(recoveredCursor) || recoveredCursor < 0) {
          throw new Error("gap recovery returned an invalid cursor");
        }
        this.lastSeq = recoveredCursor;
        this.openSource(generation, false);
        return;
      } catch {
        if (generation !== this.generation || projectId !== this.projectId) return;
        const elapsedAfterFailure = this.gapRecoveryNow() - startedAt;
        if (
          attempt >= this.gapRecoveryMaxAttempts
          || elapsedAfterFailure >= this.gapRecoveryWindowMs
        ) {
          break;
        }
        const remainingAfterFailure = this.gapRecoveryWindowMs - elapsedAfterFailure;
        const delayMs = Math.min(
          gapRecoveryDelayMs(
            attempt,
            this.gapRecoveryBaseDelayMs,
            this.gapRecoveryMaxDelayMs,
            this.gapRecoveryRandom(),
          ),
          remainingAfterFailure,
        );
        try {
          await this.waitForGapRetry(delayMs);
        } catch {
          break;
        }
      }
    }
    if (generation === this.generation && projectId === this.projectId) {
      this.onStatusChange?.("degraded");
    }
  }

  private emit(event: RecentEvent, seq: number, raw: MessageEvent): void {
    for (const subscriber of this.subscribers) {
      subscriber({ event, seq, raw });
    }
  }
}

export function streamPath(projectId: string | undefined, cursor: number): string {
  const safeCursor = Math.max(0, cursor || 0);
  if (projectId) {
    return `/api/projects/${encodeURIComponent(projectId)}/stream?cursor=${safeCursor}`;
  }
  return `/api/stream?cursor=${safeCursor}`;
}

function defaultEventSourceFactory(url: string): EventSourceLike {
  return new EventSource(url);
}

function parseSseEvent(message: MessageEvent): RecentEvent | null {
  try {
    const parsed = JSON.parse(String(message.data)) as RecentEvent;
    if (!parsed || typeof parsed !== "object" || !("type" in parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function eventSeq(event: RecentEvent, message: MessageEvent): number {
  const fromEvent = Number(event.seq ?? 0);
  if (Number.isFinite(fromEvent) && fromEvent > 0) return fromEvent;
  const fromMessage = Number(message.lastEventId || 0);
  return Number.isFinite(fromMessage) && fromMessage > 0 ? fromMessage : 0;
}

function normalizeSeq(event: RecentEvent, seq: number): RecentEvent {
  if (!seq || event.seq === seq) return event;
  return { ...event, seq };
}

function syntheticStreamEvent(type: string): RecentEvent {
  return {
    type,
    actor: "web",
    payload: {},
  };
}

function gapRecoveryDelayMs(
  attempt: number,
  baseDelayMs: number,
  maxDelayMs: number,
  randomValue: number,
): number {
  const exponential = Math.min(maxDelayMs, baseDelayMs * (2 ** Math.max(0, attempt - 1)));
  const clampedRandom = Math.min(1, Math.max(0, randomValue));
  return Math.round(exponential * (0.8 + (clampedRandom * 0.4)));
}

function waitForDelay(delayMs: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, delayMs));
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = globalThis.setTimeout(
      () => reject(new Error("gap recovery timed out")),
      Math.max(1, timeoutMs),
    );
    promise.then(
      (value) => {
        globalThis.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        globalThis.clearTimeout(timer);
        reject(error);
      },
    );
  });
}
