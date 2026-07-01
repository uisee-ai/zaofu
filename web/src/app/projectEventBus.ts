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

export interface ProjectEventBusOptions {
  cursor: number;
  projectId?: string;
  createEventSource?: EventSourceFactory;
  shouldRefresh?: (event: RecentEvent) => boolean;
  onRefresh?: (event: RecentEvent, reason: "event" | "gap" | "error") => void;
  onStatusChange?: (state: LiveConnectionState) => void;
  agentStreamFlushMs?: number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
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
  private readonly onStatusChange?: ProjectEventBusOptions["onStatusChange"];
  private generation = 0;
  private lastSeq: number;
  private projectId: string;
  private source: EventSourceLike | null = null;
  private subscribers = new Set<ProjectEventBusSubscriber>();
  private readonly streamBatcher: AgentStreamBatcher<MessageEvent>;

  constructor(options: ProjectEventBusOptions) {
    this.projectId = options.projectId ?? "";
    this.lastSeq = Math.max(0, options.cursor || 0);
    this.createEventSource = options.createEventSource ?? defaultEventSourceFactory;
    this.shouldRefresh = options.shouldRefresh ?? (() => false);
    this.onRefresh = options.onRefresh;
    this.onStatusChange = options.onStatusChange;
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
    this.closeSource();
    const generation = ++this.generation;
    this.onStatusChange?.("connecting");
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
    const seq = eventSeq(event, message);
    if (seq && seq <= this.lastSeq) return;
    if (seq) this.lastSeq = seq;
    const normalized = normalizeSeq(event, seq);
    if (this.streamBatcher.handle({ event: normalized, seq, raw: message })) {
      if (!isAgentStreamDeltaEvent(normalized.type) && this.shouldRefresh(normalized)) {
        this.onRefresh?.(normalized, "event");
      }
      return;
    }
    this.emit(normalized, seq, message);
    if (this.shouldRefresh(normalized)) {
      this.onRefresh?.(normalized, "event");
    }
  }

  private handleGap(message: MessageEvent): void {
    const event = parseSseEvent(message) ?? syntheticStreamEvent("stream.gap");
    const seq = eventSeq(event, message);
    if (seq) this.lastSeq = Math.max(this.lastSeq, seq);
    const normalized = normalizeSeq(event, seq);
    this.emit(normalized, seq, message);
    this.onStatusChange?.("degraded");
    this.onRefresh?.(normalized, "gap");
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
