import type { RecentEvent } from "../api/types";
import {
  agentStreamCoalesceKey,
  coalesceAgentStreamEvents,
  isAgentStreamDeltaEvent,
  isAgentStreamTerminalEvent,
} from "../components/agent-session/agentUiEvent.js";

export interface AgentStreamBatcherItem<T = unknown> {
  event: RecentEvent;
  seq: number;
  raw: T;
}

export interface AgentStreamBatcherOptions<T = unknown> {
  flushMs?: number;
  onFlush: (items: AgentStreamBatcherItem<T>[]) => void;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
}

export class AgentStreamBatcher<T = unknown> {
  private readonly flushMs: number;
  private readonly onFlush: (items: AgentStreamBatcherItem<T>[]) => void;
  private readonly setTimer: (callback: () => void, ms: number) => unknown;
  private readonly clearTimer: (id: unknown) => void;
  private pending = new Map<string, AgentStreamBatcherItem<T>>();
  private timer: unknown = null;
  private seenKeys = new Set<string>();

  constructor(options: AgentStreamBatcherOptions<T>) {
    this.flushMs = Math.max(8, options.flushMs ?? 32);
    this.onFlush = options.onFlush;
    this.setTimer = options.setTimer ?? ((callback, ms) => globalThis.setTimeout(callback, ms));
    this.clearTimer = options.clearTimer ?? ((id) => globalThis.clearTimeout(id as ReturnType<typeof setTimeout>));
  }

  handle(item: AgentStreamBatcherItem<T>): boolean {
    if (isAgentStreamTerminalEvent(item.event.type)) {
      this.flush();
      this.onFlush([item]);
      return true;
    }
    if (!isAgentStreamDeltaEvent(item.event.type)) return false;
    const key = agentStreamCoalesceKey(item.event);
    if (!this.seenKeys.has(key)) {
      this.seenKeys.add(key);
      this.onFlush([item]);
      return true;
    }
    const existing = this.pending.get(key);
    this.pending.set(key, existing ? {
      event: coalesceAgentStreamEvents(existing.event, item.event) as RecentEvent,
      seq: item.seq,
      raw: item.raw,
    } : item);
    this.schedule();
    return true;
  }

  flush(): void {
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    const items = [...this.pending.values()].sort((left, right) => left.seq - right.seq);
    this.pending.clear();
    if (items.length) this.onFlush(items);
  }

  close(): void {
    this.flush();
    this.seenKeys.clear();
  }

  private schedule(): void {
    if (this.timer !== null) return;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.flush();
    }, this.flushMs);
  }
}
