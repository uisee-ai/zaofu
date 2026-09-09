import type { RecentEvent } from "../../api/types";
import type { AgentSessionThreadRef } from "../agent-session/types";
import { defaultKanbanThreadKey, kanbanThreadStorageKey } from "./kanbanAgentHistoryPolicy.js";

const LEGACY_THREAD_REFS_KEY = "zf.kanbanAgentThreads";
const THREAD_REFS_SCHEMA_VERSION = 1;
const THREAD_READ_SCHEMA_VERSION = 1;
const MAX_THREAD_REFS = 8;

export interface KanbanThreadReadState {
  schemaVersion: 1;
  initialized: boolean;
  readThrough: Record<string, number>;
}

export type KanbanThreadActivity = Record<string, number[]>;

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function parsedJson(value: string | null): unknown {
  if (!value) return null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function parsedThreadRefs(value: unknown): AgentSessionThreadRef[] {
  const payload = record(value);
  const rows = Array.isArray(payload?.threads)
    ? payload.threads
    : Array.isArray(value)
      ? value
      : [];
  return rows.flatMap((item) => {
    const row = record(item);
    const id = text(row?.id);
    if (!row || !id) return [];
    return [{
      id,
      title: text(row.title),
      createdAt: text(row.createdAt) || undefined,
    }];
  });
}

function nextChatTitle(usedTitles: Set<string>): string {
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `chat ${index}`;
    if (!usedTitles.has(candidate)) return candidate;
  }
  return "new chat";
}

export function kanbanThreadRefsStorageKey(projectId: string): string {
  return `zf.kanbanAgentThreads.v${THREAD_REFS_SCHEMA_VERSION}:${projectId || "default"}`;
}

export function kanbanThreadReadStorageKey(projectId: string): string {
  return `zf.kanbanAgentThreadRead.v${THREAD_READ_SCHEMA_VERSION}:${projectId || "default"}`;
}

export function normalizeKanbanThreadRefs(
  refs: AgentSessionThreadRef[],
  projectId: string,
  activeThreadId: string,
): AgentSessionThreadRef[] {
  const defaultThreadId = defaultKanbanThreadKey(projectId);
  const byId = new Map<string, AgentSessionThreadRef>();
  for (const ref of refs) {
    const id = text(ref.id);
    if (!id || byId.has(id)) continue;
    byId.set(id, {
      id,
      title: text(ref.title),
      createdAt: text(ref.createdAt) || undefined,
    });
  }

  if (!byId.has(defaultThreadId)) {
    byId.set(defaultThreadId, { id: defaultThreadId, title: "main" });
  }
  if (activeThreadId && !byId.has(activeThreadId)) {
    byId.set(activeThreadId, { id: activeThreadId, title: "" });
  }

  const defaultRef = byId.get(defaultThreadId) as AgentSessionThreadRef;
  const remaining = [...byId.values()].filter((ref) => ref.id !== defaultThreadId);
  let selected = [defaultRef, ...remaining].slice(0, MAX_THREAD_REFS);
  if (activeThreadId && !selected.some((ref) => ref.id === activeThreadId)) {
    selected = [...selected.slice(0, MAX_THREAD_REFS - 1), byId.get(activeThreadId) as AgentSessionThreadRef];
  }

  const usedTitles = new Set<string>(["main"]);
  return selected.map((ref) => {
    if (ref.id === defaultThreadId) return { ...ref, title: "main" };
    const requested = text(ref.title).toLowerCase();
    const title = !requested || requested === "main" || usedTitles.has(requested)
      ? nextChatTitle(usedTitles)
      : text(ref.title);
    usedTitles.add(title.toLowerCase());
    return { ...ref, title };
  });
}

export function loadKanbanThreadRefs(
  storage: Pick<Storage, "getItem" | "setItem"> | null,
  projectId: string,
  activeThreadId: string,
): AgentSessionThreadRef[] {
  if (!storage) return normalizeKanbanThreadRefs([], projectId, activeThreadId);
  const scopedKey = kanbanThreadRefsStorageKey(projectId);
  let refs = parsedThreadRefs(parsedJson(storage.getItem(scopedKey)));
  if (!refs.length) {
    const defaultThreadId = defaultKanbanThreadKey(projectId);
    refs = parsedThreadRefs(parsedJson(storage.getItem(LEGACY_THREAD_REFS_KEY))).filter((ref) => (
      ref.id === defaultThreadId || ref.id === activeThreadId
    ));
  }
  const normalized = normalizeKanbanThreadRefs(refs, projectId, activeThreadId);
  storage.setItem(scopedKey, JSON.stringify({
    schemaVersion: THREAD_REFS_SCHEMA_VERSION,
    threads: normalized,
  }));
  return normalized;
}

export function saveKanbanThreadRefs(
  storage: Pick<Storage, "setItem"> | null,
  projectId: string,
  activeThreadId: string,
  refs: AgentSessionThreadRef[],
): AgentSessionThreadRef[] {
  const normalized = normalizeKanbanThreadRefs(refs, projectId, activeThreadId);
  storage?.setItem(kanbanThreadRefsStorageKey(projectId), JSON.stringify({
    schemaVersion: THREAD_REFS_SCHEMA_VERSION,
    threads: normalized,
  }));
  return normalized;
}

export function loadKanbanThreadReadState(
  storage: Pick<Storage, "getItem"> | null,
  projectId: string,
): KanbanThreadReadState {
  const payload = record(parsedJson(storage?.getItem(kanbanThreadReadStorageKey(projectId)) ?? null));
  const rawReadThrough = record(payload?.readThrough) ?? {};
  const readThrough: Record<string, number> = {};
  for (const [threadId, value] of Object.entries(rawReadThrough)) {
    const seq = Number(value);
    if (threadId && Number.isSafeInteger(seq) && seq >= 0) readThrough[threadId] = seq;
  }
  return {
    schemaVersion: THREAD_READ_SCHEMA_VERSION,
    initialized: payload?.schemaVersion === THREAD_READ_SCHEMA_VERSION && payload.initialized === true,
    readThrough,
  };
}

export function saveKanbanThreadReadState(
  storage: Pick<Storage, "setItem"> | null,
  projectId: string,
  state: KanbanThreadReadState,
): void {
  storage?.setItem(kanbanThreadReadStorageKey(projectId), JSON.stringify(state));
}

function eventThreadId(event: RecentEvent, fallbackThreadId: string): string {
  const payload = event.payload ?? {};
  const origin = record(payload.origin_binding);
  return text(
    payload.thread_key
    || payload.thread_id
    || origin?.thread_key
    || origin?.thread_id
    || fallbackThreadId,
  ) || fallbackThreadId;
}

function isUnreadActivity(event: RecentEvent): boolean {
  return event.type === "kanban.agent.reply"
    || event.type === "kanban.agent.turn.failed"
    || event.type === "workflow.result.available";
}

function eventActivityKey(event: RecentEvent): string {
  const payload = event.payload ?? {};
  if (event.type === "kanban.agent.reply" || event.type === "kanban.agent.turn.failed") {
    return `turn:${text(payload.turn_id || event.correlation_id || event.causation_id || event.id || event.seq)}`;
  }
  return `event:${text(event.id || event.seq)}`;
}

export function kanbanThreadActivity(
  events: RecentEvent[],
  fallbackThreadId: string,
): KanbanThreadActivity {
  const activity = new Map<string, Map<string, number>>();
  for (const event of events) {
    const seq = Number(event.seq);
    if (!isUnreadActivity(event) || !Number.isSafeInteger(seq) || seq <= 0) continue;
    const threadId = eventThreadId(event, fallbackThreadId);
    const activities = activity.get(threadId) ?? new Map<string, number>();
    const key = eventActivityKey(event);
    activities.set(key, Math.max(seq, activities.get(key) ?? 0));
    activity.set(threadId, activities);
  }
  return Object.fromEntries(
    [...activity.entries()].map(([threadId, activities]) => [
      threadId,
      [...activities.values()].sort((left, right) => left - right),
    ]),
  );
}

export function initializeKanbanThreadReads(
  state: KanbanThreadReadState,
  activity: KanbanThreadActivity,
): KanbanThreadReadState {
  if (state.initialized) return state;
  const readThrough = { ...state.readThrough };
  for (const [threadId, sequences] of Object.entries(activity)) {
    readThrough[threadId] = sequences.at(-1) ?? 0;
  }
  return { schemaVersion: THREAD_READ_SCHEMA_VERSION, initialized: true, readThrough };
}

export function markKanbanThreadRead(
  state: KanbanThreadReadState,
  activity: KanbanThreadActivity,
  threadId: string,
): KanbanThreadReadState {
  const latestSeq = activity[threadId]?.at(-1) ?? state.readThrough[threadId] ?? 0;
  if (state.initialized && state.readThrough[threadId] === latestSeq) return state;
  return {
    schemaVersion: THREAD_READ_SCHEMA_VERSION,
    initialized: true,
    readThrough: { ...state.readThrough, [threadId]: latestSeq },
  };
}

export function kanbanThreadUnreadCounts(
  state: KanbanThreadReadState,
  activity: KanbanThreadActivity,
): Record<string, number> {
  if (!state.initialized) return {};
  return Object.fromEntries(Object.entries(activity).map(([threadId, sequences]) => [
    threadId,
    sequences.filter((seq) => seq > (state.readThrough[threadId] ?? 0)).length,
  ]));
}

export function storedActiveKanbanThread(
  storage: Pick<Storage, "getItem" | "setItem"> | null,
  projectId: string,
): string {
  const fallback = defaultKanbanThreadKey(projectId);
  if (!storage) return fallback;
  const key = kanbanThreadStorageKey(projectId);
  const stored = text(storage.getItem(key));
  if (stored) return stored;
  storage.setItem(key, fallback);
  return fallback;
}
