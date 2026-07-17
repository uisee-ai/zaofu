// OrchestratorPanel + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { OPERATOR_BACKENDS } from "../../app/sharedTypes";
import type { ActionResponse, RecentEvent, Snapshot } from "../../api/types";
import { getAgentSessionHistory, getKanbanPendingProposals } from "../../api/client";
import type { PendingKanbanProposal } from "../../api/client";
import { AgentSessionTimeline } from "../../components/agent-session/AgentSessionTimeline";
import { ComposerSubmitButton } from "../../components/agent-session/ComposerSubmitButton";
import { deriveComposerStatus } from "../../components/agent-session/workState";
import { useWorkingTitle } from "../../components/agent-session/useWorkingTitle";
import { buildKanbanConversation } from "../../components/agent-session/projection";
import { proposalRunNotice } from "../../app/triageProposals";
import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentHistoryParams,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
} from "./kanbanAgentHistoryPolicy";
import type { AgentConversation, AgentProviderCapability, AgentSessionActionProposal, AgentSessionCard, AgentSessionThreadRef } from "../../components/agent-session/types";
import {
  kanbanAgentSessionEventsFromLive,
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "./kanbanSessionEvents";
import { ChevronDown, Maximize2, Minimize2, Minus, Plus, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentPanelMode, OrchestratorContext, OperatorBackend } from "../../app/sharedTypes";
import { actionFailed, actionFailureReason, agentConversationScrollSignature, recordValue, scrollElementToBottom, stringify, supportLabel, textValue } from "../../app/shared";


interface OperatorBackendOption {
  id: OperatorBackend;
  title: string;
  available?: boolean;
  source?: string;
  default?: boolean;
  capabilities?: AgentProviderCapability;
}


interface HeadlessQueueItem {
  id: string;
  threadId: string;
  message: string;
  createdAt: string;
}


interface HeadlessPendingMessage extends HeadlessQueueItem {
  backend: OperatorBackend;
  turnId: string;
}


function slashAction(message: string): { action: string; payload: Record<string, unknown> } | null {
  const trimmed = message.trim();
  if (!trimmed.startsWith("/action ")) return null;
  const body = trimmed.slice("/action ".length).trim();
  const match = /^([a-zA-Z0-9_-]+)(?:\s+([\s\S]+))?$/.exec(body);
  if (!match) return null;
  const action = match[1];
  const rawPayload = (match[2] || "").trim();
  if (!rawPayload) return { action, payload: {} };
  const parsed = JSON.parse(rawPayload) as unknown;
  const payload = recordValue(parsed);
  if (!payload) {
    throw new Error("slash action payload must be a JSON object");
  }
  return { action, payload };
}


function newHeadlessThreadKey(): string {
  if (typeof window !== "undefined" && window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `thread-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}


function storedHeadlessThreadRefs(activeThreadId: string): AgentSessionThreadRef[] {
  if (typeof window === "undefined") return [{ id: activeThreadId, title: "main" }];
  try {
    const parsed = JSON.parse(window.localStorage.getItem("zf.kanbanAgentThreads") || "[]") as unknown;
    if (Array.isArray(parsed)) {
      const refs = parsed
        .map((item) => recordValue(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          id: textValue(item.id).trim(),
          title: textValue(item.title).trim(),
          createdAt: textValue(item.createdAt).trim(),
        }))
        .filter((item) => item.id);
      if (refs.some((item) => item.id === activeThreadId)) return refs;
      return [{ id: activeThreadId, title: "main" }, ...refs];
    }
  } catch {
    // Local UI state only; a malformed value should not break the dashboard.
  }
  return [{ id: activeThreadId, title: "main" }];
}


function saveHeadlessThreadRefs(refs: AgentSessionThreadRef[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem("zf.kanbanAgentThreads", JSON.stringify(refs.slice(0, 8)));
}

// Mirrors ChannelPage: only auto-scroll to bottom when the user is already
// pinned there. Without this the kanban-agent thread yanked the user back to
// the bottom on every content change (new turn / 15s refresh / thinking-trace
// collapse), so scrolling up to read earlier messages was impossible.
function isScrollElementNearBottom(node: HTMLElement, thresholdPx = 96): boolean {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= thresholdPx;
}


function conversationHasHeadlessTurn(conversation: AgentConversation, pending: HeadlessPendingMessage): boolean {
  const thread = conversation.threads.find((item) => item.id === pending.threadId);
  if (!thread) return false;
  return thread.turns.some((turn) => (
    turn.id === pending.turnId
    || turn.id === `turn-${pending.turnId}`
    || turn.user?.id === pending.id
    || turn.runs.some((run) => run.id === pending.turnId)
  ));
}


function withPendingHeadlessTurns(
  conversation: AgentConversation,
  pendingMessages: HeadlessPendingMessage[],
): AgentConversation {
  const relevant = pendingMessages.filter((item) => !conversationHasHeadlessTurn(conversation, item));
  if (!relevant.length) return conversation;
  return {
    ...conversation,
    threads: conversation.threads.map((thread) => {
      const pendingForThread = relevant.filter((item) => item.threadId === thread.id);
      if (!pendingForThread.length) return thread;
      const pendingTurns = pendingForThread.map((item) => ({
        id: item.turnId,
        threadId: thread.id,
        ts: item.createdAt,
        user: {
          id: item.id,
          role: "user" as const,
          label: "You",
          content: item.message,
          ts: item.createdAt,
        },
        runs: [{
          id: item.turnId,
          threadId: thread.id,
          provider: item.backend,
          status: "submitted" as const,
          startedAt: item.createdAt,
          updatedAt: item.createdAt,
          parts: [{
            id: `${item.turnId}-pending`,
            runId: item.turnId,
            kind: "status" as const,
            state: "submitted" as const,
            title: "Sending",
            summary: "Waiting for agent stream",
            startedAt: item.createdAt,
            updatedAt: item.createdAt,
          }],
        }],
        cards: [],
      }));
      const latestPending = pendingForThread[pendingForThread.length - 1];
      return {
        ...thread,
        activeRunId: latestPending?.turnId || thread.activeRunId,
        status: thread.status === "idle" ? "submitted" : thread.status,
        updatedAt: latestPending?.createdAt || thread.updatedAt,
        turns: [...thread.turns, ...pendingTurns],
      };
    }),
  };
}


function asOperatorBackend(value: unknown): OperatorBackend | null {
  const normalized = String(value ?? "").trim();
  let backend = normalized;
  if (normalized === "claude") {
    backend = "claude-headless";
  } else if (normalized === "codex-cli") {
    backend = "codex";
  } else if (normalized === "claude-code-headless" || normalized === "claude_headless") {
    backend = "claude-headless";
  } else if (normalized === "codex-app-server" || normalized === "codex_headless") {
    backend = "codex-headless";
  }
  return OPERATOR_BACKENDS.some((item) => item.id === backend)
    ? backend as OperatorBackend
    : null;
}


function storedOperatorBackend(): OperatorBackend | null {
  if (typeof window === "undefined") return null;
  return asOperatorBackend(window.localStorage.getItem("zf.operatorBackend"));
}


function storedHeadlessBackend(): OperatorBackend | null {
  const backend = storedOperatorBackend();
  return backend ? kanbanChatBackend(backend) : null;
}


function preferredHeadlessBackend(options: OperatorBackendOption[]): OperatorBackend {
  const available = (id: OperatorBackend) => options.some((item) => item.id === id && item.available !== false);
  const configuredDefault = options.find((item) => item.default && item.available !== false && isChatBackend(item.id));
  if (configuredDefault) return kanbanChatBackend(configuredDefault.id) ?? configuredDefault.id;
  if (available("claude-headless")) return "claude-headless";
  if (available("codex-headless")) return "codex-headless";
  return "claude-headless";
}


function isHeadlessBackend(backend: OperatorBackend): boolean {
  return backend === "claude-headless" || backend === "codex-headless";
}


function isChatBackend(backend: OperatorBackend): boolean {
  return isHeadlessBackend(backend) || backend === "claude-code" || backend === "codex";
}


function kanbanChatBackend(backend: OperatorBackend): OperatorBackend | null {
  if (backend === "claude-code" || backend === "claude-headless") return "claude-headless";
  if (backend === "codex" || backend === "codex-headless") return "codex-headless";
  return null;
}


function operatorBackendLabel(backend: OperatorBackend): string {
  if (backend === "claude-code") return "Claude";
  if (backend === "claude-headless") return "Claude";
  if (backend === "codex") return "Codex";
  if (backend === "codex-headless") return "Codex";
  return "Deterministic";
}


function backendCapability(option: OperatorBackendOption, allowedActions: string[]): AgentProviderCapability {
  const provided = recordValue(option.capabilities);
  if (provided) {
    return {
      provider: option.id,
      streaming: Boolean(provided.streaming),
      cancel: Boolean(provided.cancel),
      resume: Boolean(provided.resume),
      native_resume: Boolean(provided.native_resume ?? provided.resume),
      interrupt: Boolean(provided.interrupt),
      tools: Boolean(provided.tools),
      cost: Boolean(provided.cost),
      context_usage: Boolean(provided.context_usage),
      context: textValue(provided.context).trim(),
      workdir: textValue(provided.workdir).trim(),
      test_mode: Boolean(provided.test_mode),
      source: textValue(provided.source || option.source).trim(),
      available: option.available !== false,
    };
  }
  return {
    provider: option.id,
    streaming: isHeadlessBackend(option.id),
    cancel: allowedActions.includes("agent-session-cancel"),
    resume: isHeadlessBackend(option.id),
    native_resume: isHeadlessBackend(option.id),
    interrupt: false,
    tools: isHeadlessBackend(option.id),
    cost: option.id !== "deterministic",
    context_usage: isHeadlessBackend(option.id),
    context: "project projection",
    workdir: "project",
    test_mode: option.id === "deterministic",
    source: option.source,
    available: option.available !== false,
  };
}


export function OrchestratorPanel({
  actionResult,
  activeProjectId,
  context,
  events,
  focusSignal,
  panelMode,
  visible,
  onAction,
  onPanelModeChange,
  onLockSession,
  onSaveToken,
  onUnlockSession,
  snapshot,
  tokenPresent,
}: {
  actionResult: ActionResponse | null;
  activeProjectId: string;
  context: OrchestratorContext;
  events: RecentEvent[];
  focusSignal: number;
  panelMode: Exclude<AgentPanelMode, "collapsed">;
  visible: boolean;
  onAction: (action: string, payload: Record<string, unknown>) => void | Promise<unknown>;
  onPanelModeChange: (mode: AgentPanelMode) => void;
  onLockSession: () => void;
  onSaveToken: (token: string) => void;
  onUnlockSession: (passcode: string) => Promise<{ ok: boolean; status: string; reason?: string }>;
  snapshot: Snapshot | null;
  tokenPresent: boolean;
}) {
  const [passcodeInput, setPasscodeInput] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [operatorBackend, setOperatorBackend] = useState<OperatorBackend>(() => (
    storedHeadlessBackend()
    ?? "claude-headless"
  ));
  const [operatorBackendTouched, setOperatorBackendTouched] = useState(() => (
    Boolean(storedHeadlessBackend())
  ));
  const [operatorError, setOperatorError] = useState("");
  const [headlessMessage, setHeadlessMessage] = useState("");
  const [headlessSubmitting, setHeadlessSubmitting] = useState(false);
  const [headlessProposalRunning, setHeadlessProposalRunning] = useState("");
  const [pendingProposals, setPendingProposals] = useState<PendingKanbanProposal[]>([]);
  const [pendingProposalsRefresh, setPendingProposalsRefresh] = useState(0);
  const [pendingProposalBusy, setPendingProposalBusy] = useState("");
  const [pendingProposalExpanded, setPendingProposalExpanded] = useState<Record<string, boolean>>({});
  const [pendingProposalErrors, setPendingProposalErrors] = useState<Record<string, string>>({});
  const [pendingProposalNotice, setPendingProposalNotice] = useState("");
  const [headlessThreadKey, setHeadlessThreadKey] = useState(() => {
    // Default to the STABLE project-derived thread so a fresh browser/session
    // lands on the existing kanban conversation instead of a random empty thread
    // (channel-kanban E2E 2026-07-09). localStorage is project-scoped.
    const projectDefault = defaultKanbanThreadKey(activeProjectId);
    if (typeof window === "undefined") return projectDefault;
    const stored = window.localStorage.getItem(kanbanThreadStorageKey(activeProjectId));
    if (stored) return stored;
    window.localStorage.setItem(kanbanThreadStorageKey(activeProjectId), projectDefault);
    return projectDefault;
  });
  const [headlessThreads, setHeadlessThreads] = useState<AgentSessionThreadRef[]>(() =>
    storedHeadlessThreadRefs(headlessThreadKey),
  );
  const [headlessQueue, setHeadlessQueue] = useState<HeadlessQueueItem[]>([]);
  const [headlessPendingMessages, setHeadlessPendingMessages] = useState<HeadlessPendingMessage[]>([]);
  const [headlessHistoryEvents, setHeadlessHistoryEvents] = useState<RecentEvent[]>([]);
  const [headlessBufferedEvents, setHeadlessBufferedEvents] = useState<RecentEvent[]>([]);
  const [headlessHistoryBeforeSeq, setHeadlessHistoryBeforeSeq] = useState<number | null>(null);
  const [headlessHistoryHasMore, setHeadlessHistoryHasMore] = useState(false);
  const [headlessHistoryLoading, setHeadlessHistoryLoading] = useState(false);
  const [headlessHistoryError, setHeadlessHistoryError] = useState("");
  const [headlessSplitThreadKey, setHeadlessSplitThreadKey] = useState("");
  const [backendMenuOpen, setBackendMenuOpen] = useState(false);
  const headlessInputRef = useRef<HTMLTextAreaElement | null>(null);
  // Message typed before the first snapshot resolved the action gate — sent
  // automatically once the gate is known (2026-07-16 first-message race).
  const [pendingGateMessage, setPendingGateMessage] = useState<string | null>(null);
  const headlessThreadRef = useRef<HTMLDivElement | null>(null);
  const [headlessPinnedToBottom, setHeadlessPinnedToBottom] = useState(true);
  const [headlessHasNewBelow, setHeadlessHasNewBelow] = useState(false);

  const allowedActions = snapshot?.runtime.actions?.allowed ?? [];
  const webSession = snapshot?.runtime.web_session;
  const agentSurface = snapshot?.runtime.agent_surface;
  const mutationEnabled = Boolean(snapshot?.runtime.actions?.mutation_enabled);
  const headlessProjectId = kanbanAgentProjectId(activeProjectId, snapshot?.project?.project_id || "");
  const headlessConversationId = kanbanAgentConversationId(headlessProjectId);
  const sessionActionReady = Boolean(webSession?.actions_enabled);
  const tokenFallbackAvailable = webSession?.mode === "token_required"
    || Boolean(webSession?.token_fallback_enabled);
  const passcodeRequired = webSession?.mode === "remote_passcode" && !sessionActionReady;
  const showTokenRow = mutationEnabled && !sessionActionReady && tokenFallbackAvailable && !tokenPresent;
  const tokenRequired = showTokenRow && !tokenPresent;
  const actionReady = sessionActionReady || (mutationEnabled && tokenPresent);
  const actionState = actionReady
    ? "active"
    : mutationEnabled
      ? (passcodeRequired ? "passcode needed" : tokenRequired ? "token needed" : "locked")
      : "read only";
  const canUseAction = (action: string) => actionReady && allowedActions.includes(action);
  useEffect(() => {
    if (pendingGateMessage === null || !snapshot) return;
    const message = pendingGateMessage;
    setPendingGateMessage(null);
    if (actionReady && allowedActions.includes("chat-orchestrator")) {
      void submitHeadlessMessage(message);
    } else {
      setHeadlessMessage(message);
      setOperatorError(`${activeBackendTitle} message is ${actionState}; save a valid action token first.`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGateMessage, snapshot, actionReady]);
  // 8001 regression (operator report 2026-07-16): when the selected project is
  // dead (root deleted -> snapshot 404s forever) the parked message became a
  // black hole — cleared textarea, never sent, no feedback. Bound the park:
  // if the snapshot still hasn't arrived after 6s, restore the message and
  // say why instead of silently eating it.
  useEffect(() => {
    if (pendingGateMessage === null || snapshot) return;
    const parked = pendingGateMessage;
    const timer = window.setTimeout(() => {
      setPendingGateMessage((current) => {
        if (current === parked) {
          setHeadlessMessage(parked);
          setOperatorError(
            "Runtime snapshot unavailable for this project — message not sent. "
            + "Check the project selection (its root may be missing).",
          );
          return null;
        }
        return current;
      });
    }, 6000);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGateMessage, snapshot]);
  const desiredOperatorScope = "project";
  const operatorBackendOptions = useMemo<OperatorBackendOption[]>(() => {
    const projected: OperatorBackendOption[] = [];
    for (const item of agentSurface?.backends ?? []) {
      const id = asOperatorBackend(item.id);
      if (id) {
        projected.push({
          id,
          title: operatorBackendLabel(id),
          available: item.available,
          source: item.source,
          default: item.default,
          capabilities: backendCapability({
            id,
            title: operatorBackendLabel(id),
            available: item.available,
            source: item.source,
            default: item.default,
            capabilities: recordValue(item.capabilities) as unknown as AgentProviderCapability | undefined,
          }, agentSurface?.allowed_actions ?? []),
        });
      }
    }
    const order = new Map<OperatorBackend, number>(OPERATOR_BACKENDS.map((item, index) => [item.id, index]));
    return (projected.length ? projected : [...OPERATOR_BACKENDS])
      .slice()
      .sort((left, right) => (order.get(left.id) ?? 99) - (order.get(right.id) ?? 99));
  }, [agentSurface?.allowed_actions, agentSurface?.backends]);
  const headlessBackendOptions = useMemo<OperatorBackendOption[]>(() => {
    const fallbackOptions: OperatorBackendOption[] = OPERATOR_BACKENDS.map((item) => ({
      id: item.id,
      title: item.title,
    }));
    const sourceOptions: OperatorBackendOption[] = operatorBackendOptions.length
      ? operatorBackendOptions
      : fallbackOptions;
    const grouped = new Map<OperatorBackend, OperatorBackendOption>();
    for (const item of sourceOptions) {
      const id = kanbanChatBackend(item.id);
      if (!id) continue;
      const previous = grouped.get(id);
      grouped.set(id, {
        id,
        title: operatorBackendLabel(id),
        available: Boolean(previous?.available) || item.available !== false,
        source: previous?.source || item.source,
        default: Boolean(previous?.default) || Boolean(item.default),
        capabilities: backendCapability({ ...item, id }, agentSurface?.allowed_actions ?? []),
      });
    }
    return (["claude-headless", "codex-headless"] as OperatorBackend[])
      .map((id) => grouped.get(id) ?? {
        id,
        title: operatorBackendLabel(id),
        available: false,
        source: "headless",
        default: false,
        capabilities: backendCapability({ id, title: operatorBackendLabel(id), available: false, source: "headless" }, agentSurface?.allowed_actions ?? []),
      });
  }, [agentSurface?.allowed_actions, operatorBackendOptions]);

  useEffect(() => {
    if (operatorBackendTouched) return;
    setOperatorBackend(preferredHeadlessBackend(headlessBackendOptions));
  }, [headlessBackendOptions, operatorBackendTouched]);

  // chat-e2e F2: pending proposals are ledger truth, not session state — a
  // fresh session must resurface them for approval/dismissal.
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    void getKanbanPendingProposals(headlessProjectId).then((page) => {
      if (!cancelled) setPendingProposals(page.items ?? []);
    }).catch(() => {
      if (!cancelled) setPendingProposals([]);
    });
    return () => { cancelled = true; };
  }, [headlessProjectId, visible, pendingProposalsRefresh]);

  useEffect(() => {
    if (!visible) return;
    headlessInputRef.current?.focus();
  }, [focusSignal, panelMode, visible]);

  useEffect(() => {
    let cancelled = false;
    setHeadlessHistoryLoading(true);
    setHeadlessHistoryError("");
    void getAgentSessionHistory(headlessProjectId, kanbanAgentHistoryParams({
      threadId: headlessThreadKey,
      conversationId: headlessConversationId,
      backend: operatorBackend,
      limit: 160,
    })).then((page) => {
      if (cancelled) return;
      setHeadlessHistoryEvents(page.items ?? []);
      setHeadlessHistoryBeforeSeq(page.next_before_seq ?? null);
      setHeadlessHistoryHasMore(Boolean(page.has_more));
    }).catch((err) => {
      if (!cancelled) {
        setHeadlessHistoryEvents([]);
        setHeadlessHistoryBeforeSeq(null);
        setHeadlessHistoryHasMore(false);
        setHeadlessHistoryError(err instanceof Error ? err.message : String(err));
      }
    }).finally(() => {
      if (!cancelled) setHeadlessHistoryLoading(false);
    });
    return () => { cancelled = true; };
  }, [headlessConversationId, headlessProjectId, headlessThreadKey, operatorBackend]);

  useEffect(() => {
    setHeadlessBufferedEvents([]);
  }, [headlessProjectId]);

  useEffect(() => {
    const scopedEvents = kanbanAgentSessionEventsFromLive(events, {
      projectId: headlessProjectId,
      conversationId: headlessConversationId,
      backend: operatorBackend,
      taskId: context.taskId,
    });
    if (!scopedEvents.length) return;
    setHeadlessBufferedEvents((current) => mergeBoundedKanbanSessionEvents(current, scopedEvents));
  }, [context.taskId, events, headlessConversationId, headlessProjectId, operatorBackend]);

  // 任务自动引用(operator 2026-07-11):从 TaskDetail 打开时 context.taskId
  // 已自动派生;这里补"看得见/可解除"——chip + 可关。关掉后消息不再携带
  // task/trace/pdd/fanout 引用。
  const [taskRefOn, setTaskRefOn] = useState(true);
  useEffect(() => { setTaskRefOn(true); }, [context.taskId]);

  function contextPayload(): Record<string, unknown> {
    return {
      task_id: (taskRefOn && context.taskId) || undefined,
      trace_id: (taskRefOn && context.traceId) || undefined,
      pdd_id: (taskRefOn && context.pddId) || undefined,
      fanout_id: (taskRefOn && context.fanoutId) || undefined,
      project_id: headlessProjectId,
      conversation_id: headlessConversationId,
      thread_key: headlessThreadKey,
    };
  }

  async function loadEarlierHeadlessHistory() {
    if (!headlessHistoryBeforeSeq || headlessHistoryLoading) return;
    const node = headlessThreadRef.current;
    const priorScroll = node ? { height: node.scrollHeight, top: node.scrollTop } : null;
    setHeadlessHistoryLoading(true);
    setHeadlessHistoryError("");
    try {
      const page = await getAgentSessionHistory(headlessProjectId, {
        ...kanbanAgentHistoryParams({
          threadId: headlessThreadKey,
          conversationId: headlessConversationId,
          backend: operatorBackend,
          limit: 160,
        }),
        beforeSeq: headlessHistoryBeforeSeq,
      });
      setHeadlessHistoryEvents((current) => mergeEventsByIdentity(page.items ?? [], current));
      setHeadlessHistoryBeforeSeq(page.next_before_seq ?? null);
      setHeadlessHistoryHasMore(Boolean(page.has_more));
      if (priorScroll && node) {
        window.requestAnimationFrame(() => {
          node.scrollTop = priorScroll.top + Math.max(0, node.scrollHeight - priorScroll.height);
        });
      }
    } catch (err) {
      setHeadlessHistoryError(err instanceof Error ? err.message : String(err));
    } finally {
      setHeadlessHistoryLoading(false);
    }
  }

  function resetHeadlessThread() {
    const next = newHeadlessThreadKey();
    setHeadlessThreadKey(next);
    setHeadlessThreads((current) => {
      const nextRefs = [
        { id: next, title: current.length ? `chat ${current.length + 1}` : "main", createdAt: new Date().toISOString() },
        ...current,
      ].slice(0, 8);
      saveHeadlessThreadRefs(nextRefs);
      return nextRefs;
    });
    setHeadlessSplitThreadKey("");
    if (typeof window !== "undefined") {
      window.localStorage.setItem(kanbanThreadStorageKey(activeProjectId), next);
    }
    setHeadlessMessage("");
    setOperatorError("");
    headlessInputRef.current?.focus();
  }

  function selectHeadlessThread(threadId: string) {
    setHeadlessThreadKey(threadId);
    if (headlessSplitThreadKey === threadId) setHeadlessSplitThreadKey("");
    if (typeof window !== "undefined") {
      window.localStorage.setItem(kanbanThreadStorageKey(activeProjectId), threadId);
    }
    headlessInputRef.current?.focus();
  }

  function queueHeadlessMessage(message: string) {
    const item: HeadlessQueueItem = {
      id: `queue-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
      threadId: headlessThreadKey,
      message,
      createdAt: new Date().toISOString(),
    };
    setHeadlessQueue((current) => [...current, item]);
    setHeadlessMessage("");
  }

  async function submitHeadlessMessage(messageOverride?: string, options: { force?: boolean; projectionFirst?: boolean } = {}) {
    const message = (messageOverride ?? headlessMessage).trim();
    if (!message || !isChatBackend(operatorBackend) || headlessSubmitting) return;
    if (activeThreadBusy && !options.force) {
      queueHeadlessMessage(message);
      return;
    }
    if (!canUseAction("chat-orchestrator")) {
      // First-message race (operator report 2026-07-16): before the snapshot
      // arrives the gate reads "read only" and the send silently bounced.
      // Park the message and let the snapshot effect below flush it instead
      // of erroring on a gate that is merely unknown.
      if (!snapshot) {
        setPendingGateMessage(message);
        setHeadlessMessage("");
        setOperatorError("");
        return;
      }
      setOperatorError(`${activeBackendTitle} message is ${actionState}; save a valid action token first.`);
      headlessInputRef.current?.focus();
      return;
    }
    // 2026-07-03 racing-codex e2e round 2 finding (T3 refinement): guarding
    // on `!agentSurface` alone was not precise enough — agentSurface can
    // already be populated while the *separate* effect that corrects
    // operatorBackend from its hardcoded "claude-headless" initial value
    // (preferredHeadlessBackend, below) hasn't re-rendered yet. Compare the
    // current selection directly against the project's configured backend
    // instead of just checking "has any data arrived".
    const configuredChatBackend = agentSurface?.configured_backend
      ? kanbanChatBackend(asOperatorBackend(agentSurface.configured_backend) ?? "claude-headless")
      : null;
    const stillOnUncorrectedDefault = (
      !operatorBackendTouched
      && operatorBackend === "claude-headless"
      && !!configuredChatBackend
      && configuredChatBackend !== "claude-headless"
    );
    if (!agentSurface || stillOnUncorrectedDefault) {
      setOperatorError("Agent backend list is still loading; try again in a moment.");
      return;
    }
    setHeadlessSubmitting(true);
    setHeadlessMessage("");
    let pendingTurnId = "";
    try {
      const directAction = slashAction(message);
      if (directAction) {
        if (!canUseAction(directAction.action)) {
          setOperatorError(`action ${directAction.action} is ${actionState}`);
          setHeadlessMessage(message);
          return;
        }
        const payload = { ...directAction.payload };
        if (!("task_id" in payload) && directAction.action !== "create-task" && context.taskId) {
          payload.task_id = context.taskId;
        }
        const result = await Promise.resolve(onAction(directAction.action, payload));
        if (actionFailed(result)) {
          setOperatorError(actionFailureReason(result));
          setHeadlessMessage(message);
          return;
        }
        setOperatorError("");
        return;
      }
      const turnId = newHeadlessThreadKey();
      pendingTurnId = turnId;
      const pendingMessage: HeadlessPendingMessage = {
        id: `pending-${turnId}`,
        threadId: headlessThreadKey,
        turnId,
        message,
        backend: operatorBackend,
        createdAt: new Date().toISOString(),
      };
      setHeadlessPendingMessages((current) => [
        ...current.filter((item) => item.turnId !== turnId),
        pendingMessage,
      ]);
      const result = await Promise.resolve(onAction("chat-orchestrator", {
        ...contextPayload(),
        backend: operatorBackend,
        scope: desiredOperatorScope,
        message,
        turn_id: turnId,
        // 快照按钮:强制确定性投影快答(server 端短路 headless 派发)
        mode: options.projectionFirst ? "projection_first" : undefined,
      }));
      if (actionFailed(result)) {
        const reply = recordValue(result.reply);
        if (reply?.source === "kanban-agent.headless") {
          setOperatorError("");
        } else {
          setOperatorError(actionFailureReason(result));
          setHeadlessMessage(message);
        }
        setHeadlessPendingMessages((current) => current.filter((item) => item.turnId !== turnId));
        return;
      }
      setOperatorError("");
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
      setHeadlessMessage(message);
      if (pendingTurnId) {
        setHeadlessPendingMessages((current) => current.filter((item) => item.turnId !== pendingTurnId));
      }
    } finally {
      setHeadlessSubmitting(false);
    }
  }

  async function runHeadlessProposal(proposal: AgentSessionActionProposal, key: string) {
    if (!proposal.valid || !canUseAction(proposal.action)) return;
    setHeadlessProposalRunning(key);
    try {
      const payload: Record<string, unknown> = {
        ...proposal.payload,
        project_id: textValue(proposal.payload.project_id) || headlessProjectId,
        conversation_id: textValue(proposal.payload.conversation_id) || headlessConversationId,
        thread_id: textValue(proposal.payload.thread_id) || headlessThreadKey,
        run_id: textValue(proposal.payload.run_id) || key.replace(/^proposal-/, ""),
        source: textValue(proposal.payload.source) || "kanban-agent-proposal",
      };
      if (!("task_id" in payload) && proposal.action !== "create-task" && context.taskId) {
        payload.task_id = context.taskId;
      }
      await Promise.resolve(onAction(proposal.action, payload));
    } finally {
      setHeadlessProposalRunning("");
    }
  }

  async function runPendingProposal(item: PendingKanbanProposal) {
    if (!item.valid || !canUseAction(item.action)) return;
    setPendingProposalBusy(item.proposal_event_id);
    setPendingProposalErrors((current) => ({ ...current, [item.proposal_event_id]: "" }));
    try {
      const result = await Promise.resolve(onAction(item.action, {
        ...item.payload,
        project_id: textValue(item.payload.project_id) || headlessProjectId,
        proposal_event_id: item.proposal_event_id,
        source: textValue(item.payload.source) || "kanban-agent-pending-proposal",
      }));
      if (actionFailed(result)) {
        setPendingProposalErrors((current) => ({
          ...current,
          [item.proposal_event_id]: actionFailureReason(result) || "action failed",
        }));
      } else {
        const taskId = textValue((result as Record<string, unknown> | undefined)?.task_id);
        setPendingProposalNotice(proposalRunNotice(item.action, item.title || item.action, taskId));
      }
    } catch (err) {
      setPendingProposalErrors((current) => ({
        ...current,
        [item.proposal_event_id]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setPendingProposalBusy("");
      setPendingProposalsRefresh((n) => n + 1);
    }
  }

  async function dismissPendingProposal(item: PendingKanbanProposal) {
    setPendingProposalBusy(item.proposal_event_id);
    setPendingProposalErrors((current) => ({ ...current, [item.proposal_event_id]: "" }));
    try {
      const result = await Promise.resolve(onAction("kanban-proposal-dismiss", {
        project_id: headlessProjectId,
        proposal_event_id: item.proposal_event_id,
        reason: "dismissed from Kanban Agent panel",
      }));
      if (actionFailed(result)) {
        setPendingProposalErrors((current) => ({
          ...current,
          [item.proposal_event_id]: actionFailureReason(result) || "dismiss failed",
        }));
      }
    } catch (err) {
      setPendingProposalErrors((current) => ({
        ...current,
        [item.proposal_event_id]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setPendingProposalBusy("");
      setPendingProposalsRefresh((n) => n + 1);
    }
  }

  function pendingProposalContract(item: PendingKanbanProposal): Array<[string, string]> {
    const contract = item.payload.contract;
    if (!contract || typeof contract !== "object") return [];
    const record = contract as Record<string, unknown>;
    const rows: Array<[string, string]> = [];
    for (const key of ["behavior", "verification"]) {
      const value = textValue(record[key]);
      if (value) rows.push([key, value]);
    }
    const scope = Array.isArray(record.scope) ? record.scope.map((v) => String(v)).filter(Boolean) : [];
    rows.push(["scope", scope.length ? scope.join(", ") : "(empty — no path restriction)"]);
    return rows;
  }

  async function cancelHeadlessRun(runId: string) {
    if (!canUseAction("agent-session-cancel")) {
      setOperatorError(`cancel is ${actionState}`);
      return;
    }
    try {
      const result = await Promise.resolve(onAction("agent-session-cancel", {
        ...contextPayload(),
        backend: operatorBackend,
        conversation_id: headlessConversationId,
        thread_id: headlessThreadKey,
        run_id: runId,
        reason: "operator cancelled from Kanban Agent UI",
      }));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
      }
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    }
  }

  function changeOperatorBackend(value: string) {
    const backend = kanbanChatBackend(asOperatorBackend(value) ?? "claude-headless") ?? "claude-headless";
    setOperatorBackend(backend);
    setOperatorBackendTouched(true);
    window.localStorage.setItem("zf.operatorBackend", backend);
  }

  function selectOperatorBackend(value: string) {
    changeOperatorBackend(value);
    setOperatorError("");
    setBackendMenuOpen(false);
  }

  function saveToken() {
    onSaveToken(tokenInput);
    setTokenInput("");
    setOperatorError("");
  }

  async function unlockWithPasscode() {
    const passcode = passcodeInput.trim();
    if (!passcode) return;
    try {
      const result = await onUnlockSession(passcode);
      if (result.ok) {
        setPasscodeInput("");
        setOperatorError("");
      } else {
        setOperatorError(result.reason || result.status);
      }
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    }
  }

  const headlessConversationEvents = useMemo(
    () => mergeEventsByIdentity(headlessHistoryEvents, headlessBufferedEvents, events),
    [events, headlessBufferedEvents, headlessHistoryEvents],
  );
  const headlessConversation = useMemo(() => buildKanbanConversation({
    activeThreadId: headlessThreadKey,
    backend: operatorBackend,
    events: headlessConversationEvents,
    knownThreads: headlessThreads,
    projectId: headlessProjectId,
  }), [headlessConversationEvents, headlessProjectId, headlessThreadKey, headlessThreads, operatorBackend]);
  useEffect(() => {
    setHeadlessPendingMessages((current) => (
      current.filter((item) => !conversationHasHeadlessTurn(headlessConversation, item))
    ));
  }, [headlessConversation]);
  const visibleHeadlessConversation = useMemo(() => (
    withPendingHeadlessTurns(headlessConversation, headlessPendingMessages)
  ), [headlessConversation, headlessPendingMessages]);
  const activeHeadlessThread = visibleHeadlessConversation.threads.find((thread) => thread.id === headlessThreadKey)
    ?? visibleHeadlessConversation.threads[0];
  const activeHeadlessPrompt = activeHeadlessThread
    ? [...activeHeadlessThread.turns].reverse().find((turn) => turn.user)?.user
    : undefined;
  const activeThreadBusy = Boolean(
    activeHeadlessThread
    && ["streaming", "submitted", "queued", "waiting_input"].includes(activeHeadlessThread.status),
  );
  // Tab title shows "● …" while the headless session is working. Single owner:
  // channel group chat deliberately doesn't drive it.
  useWorkingTitle(activeThreadBusy);
  // The live run on the active thread — its id is what the composer's
  // Interrupt affordance cancels.
  const activeHeadlessRun = activeHeadlessThread
    ? [...activeHeadlessThread.turns.flatMap((turn) => turn.runs)].reverse()
        .find((run) => run.status === "streaming" || run.status === "submitted")
    : undefined;
  const headlessQueueCards: AgentSessionCard[] = headlessQueue
    .filter((item) => item.threadId === headlessThreadKey)
    .map((item) => ({
      id: item.id,
      kind: "queue",
      title: "Queued message",
      body: item.message,
      status: "queued",
      threadId: item.threadId,
    }));
  const headlessScrollSignature = agentConversationScrollSignature(
    visibleHeadlessConversation,
    headlessThreadKey,
    headlessQueueCards,
  );

  // Switching thread or (re)opening the panel re-pins to bottom and jumps there.
  useEffect(() => {
    setHeadlessPinnedToBottom(true);
    setHeadlessHasNewBelow(false);
    scrollElementToBottom(headlessThreadRef.current);
  }, [headlessThreadKey, panelMode]);
  // Content changed (new turn / streamed delta / refresh). Only follow to the
  // bottom when the user is pinned there; otherwise surface a "New messages"
  // affordance instead of yanking their scroll position.
  useEffect(() => {
    const node = headlessThreadRef.current;
    if (!node) return;
    if (headlessPinnedToBottom || isScrollElementNearBottom(node)) {
      scrollElementToBottom(node);
      setHeadlessHasNewBelow(false);
    } else {
      setHeadlessHasNewBelow(true);
    }
  }, [headlessScrollSignature, headlessPinnedToBottom]);
  function showLatestHeadless() {
    setHeadlessPinnedToBottom(true);
    setHeadlessHasNewBelow(false);
    scrollElementToBottom(headlessThreadRef.current);
  }
  useEffect(() => {
    if (activeThreadBusy || headlessSubmitting) return undefined;
    const next = headlessQueue.find((item) => item.threadId === headlessThreadKey);
    if (!next) return undefined;
    const timer = window.setTimeout(() => {
      setHeadlessQueue((current) => current.filter((item) => item.id !== next.id));
      void submitHeadlessMessage(next.message, { force: true });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [activeThreadBusy, headlessQueue, headlessSubmitting, headlessThreadKey]);
  const activeBackendTitle = operatorBackendLabel(operatorBackend);
  const headlessCapabilities = headlessBackendOptions.map((item) =>
    item.capabilities ?? backendCapability(item, agentSurface?.allowed_actions ?? []),
  );
  const actionStateClass = actionReady
    ? "ready"
    : mutationEnabled
      ? "locked"
      : "readonly";
  const fullscreen = panelMode === "fullscreen";
  const headlessCanChat = canUseAction("chat-orchestrator");
  const headlessEmptyTitle = headlessCanChat ? "Chat with your agents" : "Action token needed";
  const headlessEmptyBody = headlessCanChat
    ? "Ask for a board summary, plan a handoff, or prepare a task action."
    : "Save a valid action token to send messages. Existing replies will still appear here.";
  const headlessPlaceholder = !headlessCanChat
    ? "Save action token to send..."
    : taskRefOn && context.taskId
      ? `问关于 ${context.taskId} 的任何事(状态 / 合同 / 证据 / 时间线…)`
      : "Tell me what to do...";

  return (
    <section
      className={`panel orchestrator-panel ${panelMode}`}
      role="dialog"
      aria-modal={fullscreen}
      aria-label="Kanban Agent"
    >
      <div className="agent-shell-header">
        <div className="agent-title-block">
          <button
            className="agent-window-button ghost"
            type="button"
            aria-label="New Kanban Agent chat"
            title="New chat"
            onClick={resetHeadlessThread}
          >
            <Plus size={20} strokeWidth={1.8} />
          </button>
          <div
            className="agent-model-dropdown header-agent-switch"
            onBlur={(event) => {
              const nextTarget = event.relatedTarget;
              if (!(nextTarget instanceof Node) || !event.currentTarget.contains(nextTarget)) {
                setBackendMenuOpen(false);
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") setBackendMenuOpen(false);
            }}
          >
            <button
              aria-expanded={backendMenuOpen}
              aria-haspopup="listbox"
              aria-label={`Agent backend: ${activeBackendTitle}`}
              className="agent-model-trigger"
              type="button"
              onClick={() => setBackendMenuOpen((open) => !open)}
            >
              <span className="agent-model-dot active" aria-hidden="true" />
              <span>{activeBackendTitle}</span>
              <span className="agent-model-chevron" aria-hidden="true" />
            </button>
            {backendMenuOpen ? (
              <div
                className="agent-model-menu"
                role="listbox"
                aria-label="Kanban Agent backend options"
              >
                {headlessBackendOptions.map((backend) => {
                  const active = backend.id === operatorBackend;
                  const capability = backend.capabilities ?? backendCapability(backend, agentSurface?.allowed_actions ?? []);
                  return (
                    <button
                      aria-selected={active}
                      className={`agent-model-menu-item ${active ? "active" : ""}`}
                      disabled={backend.available === false}
                      key={backend.id}
                      role="option"
                      type="button"
                      onClick={() => selectOperatorBackend(backend.id)}
                    >
                      <span className={`agent-model-dot ${active ? "active" : ""}`} aria-hidden="true" />
                      <span>
                        {operatorBackendLabel(backend.id)}
                        <small className="agent-model-capability">
                          stream {supportLabel(capability.streaming)} · resume {supportLabel(capability.resume)} · interrupt {supportLabel(capability.interrupt)} · cost {supportLabel(capability.cost)} · context {supportLabel(capability.context_usage)}
                        </small>
                      </span>
                      {backend.available === false ? <span className="agent-model-status">Unavailable</span> : null}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
          <span className={`agent-state-pill compact ${actionStateClass}`}>{actionState}</span>
        </div>
        <div className="agent-header-actions">
          {webSession?.mode === "remote_passcode" && sessionActionReady ? (
            <button className="agent-lock-button" type="button" onClick={onLockSession}>
              Lock
            </button>
          ) : null}
          <button
            className="agent-window-button emphasized"
            type="button"
            aria-label={fullscreen ? "Restore Kanban Agent" : "Fullscreen Kanban Agent"}
            title={fullscreen ? "Restore" : "Fullscreen"}
            onClick={() => onPanelModeChange(fullscreen ? "docked" : "fullscreen")}
          >
            {fullscreen ? <Minimize2 size={18} strokeWidth={1.8} /> : <Maximize2 size={18} strokeWidth={1.8} />}
          </button>
          <button
            className="agent-window-button ghost"
            type="button"
            aria-label="Minimize Kanban Agent"
            title="Minimize"
            onClick={() => onPanelModeChange("collapsed")}
          >
            <Minus size={19} strokeWidth={1.9} />
          </button>
        </div>
      </div>
      <div className="orchestrator-body">
        {passcodeRequired ? <form
          className="token-row agent-auth-row"
          onSubmit={(event) => {
            event.preventDefault();
            void unlockWithPasscode();
          }}
        >
          <input
            className="filter-input"
            placeholder="web passcode"
            type="password"
            value={passcodeInput}
            onChange={(event) => setPasscodeInput(event.target.value)}
          />
          <button className="icon-button" type="submit">
            Unlock
          </button>
        </form> : null}

        {showTokenRow ? <form
          className="token-row agent-auth-row"
          onSubmit={(event) => {
            event.preventDefault();
            saveToken();
          }}
        >
          <input
            className="filter-input"
            placeholder="action token"
            type="password"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
          />
          <button className="icon-button" type="submit">
            Save
          </button>
          <button className="icon-button" type="button" onClick={() => onSaveToken("")}>
            Clear
          </button>
        </form> : null}

        <div className="headless-chat">
          {activeHeadlessPrompt?.content ? (
            <div className="headless-thread-context" title={activeHeadlessPrompt.content}>
              <strong>{activeHeadlessPrompt.label || "You"}</strong>
              <span>{activeHeadlessPrompt.content}</span>
            </div>
          ) : null}
          <div
            className="headless-thread"
            ref={headlessThreadRef}
            onScroll={(event) => {
              const nearBottom = isScrollElementNearBottom(event.currentTarget);
              setHeadlessPinnedToBottom(nearBottom);
              if (nearBottom) setHeadlessHasNewBelow(false);
            }}
          >
            {headlessHistoryHasMore ? (
              <button
                className="agent-history-load"
                disabled={headlessHistoryLoading}
                type="button"
                onClick={() => void loadEarlierHeadlessHistory()}
              >
                {headlessHistoryLoading ? "Loading history" : "Load earlier"}
              </button>
            ) : null}
            {headlessHistoryError ? (
              <div className="headless-composer-alert" role="alert">
                History unavailable: {headlessHistoryError}
              </div>
            ) : null}
            <AgentSessionTimeline
              actionBusyId={headlessProposalRunning}
              activeThreadId={headlessThreadKey}
              allowSplit={fullscreen && headlessConversation.threads.length > 1}
              allowPreviewSplit={fullscreen}
              compact={!fullscreen}
              compactRunHeader
              conversation={visibleHeadlessConversation}
              collapseCompletedRunDetails
              emptyBody={headlessEmptyBody}
              emptyTitle={headlessEmptyTitle}
              extraCards={headlessQueueCards}
              onActiveThreadChange={selectHeadlessThread}
              onAnswerQuestion={(card) => {
                setHeadlessMessage(card.body || "");
                headlessInputRef.current?.focus();
              }}
              onApproveProposal={(proposal, cardId) => void runHeadlessProposal(proposal, cardId)}
              onCancelQueued={(cardId) => setHeadlessQueue((current) => current.filter((item) => item.id !== cardId))}
              onCancelRun={(runId) => void cancelHeadlessRun(runId)}
              providerCapabilities={headlessCapabilities}
              onSplitThreadChange={setHeadlessSplitThreadKey}
              showRunDetails={false}
              showRunProvider={false}
              showThreadChips={fullscreen && headlessConversation.threads.length > 1}
              splitThreadId={headlessSplitThreadKey}
            />
          </div>
          {headlessHasNewBelow ? (
            <button className="channel-scroll-latest" type="button" onClick={showLatestHeadless}>
              <ChevronDown size={15} />
              New messages
            </button>
          ) : null}
          {pendingProposals.length || pendingProposalNotice ? (
            <div aria-label="Pending proposals" className="headless-pending-proposals">
              <div className="headless-pending-title">
                Pending proposals · {pendingProposals.length}
              </div>
              {pendingProposalNotice ? (
                <div className="headless-pending-notice">
                  {pendingProposalNotice}
                  <button
                    aria-label="Clear notice"
                    className="headless-pending-dismiss"
                    type="button"
                    onClick={() => setPendingProposalNotice("")}
                  >
                    ×
                  </button>
                </div>
              ) : null}
              {pendingProposals.map((item) => {
                const expanded = Boolean(pendingProposalExpanded[item.proposal_event_id]);
                const error = pendingProposalErrors[item.proposal_event_id] || "";
                return (
                  <div className="headless-pending-entry" key={item.proposal_event_id}>
                    <div className="headless-pending-item">
                      <div className="headless-pending-main">
                        <strong>{item.title || item.action}</strong>
                        <small>
                          {item.action}
                          {item.ts ? ` · ${item.ts.slice(11, 16)} UTC` : ""}
                          {!item.valid && item.validation_error ? ` · invalid: ${item.validation_error}` : ""}
                        </small>
                      </div>
                      <button
                        aria-expanded={expanded}
                        className="headless-pending-expand"
                        type="button"
                        onClick={() => setPendingProposalExpanded((current) => ({
                          ...current, [item.proposal_event_id]: !expanded,
                        }))}
                      >
                        {expanded ? "Hide" : "Details"}
                      </button>
                      <button
                        className="headless-pending-run"
                        disabled={!item.valid || pendingProposalBusy === item.proposal_event_id}
                        type="button"
                        onClick={() => void runPendingProposal(item)}
                      >
                        {item.action === "create-task" ? "Create Task" : "Run"}
                      </button>
                      <button
                        className="headless-pending-dismiss"
                        disabled={pendingProposalBusy === item.proposal_event_id}
                        type="button"
                        onClick={() => void dismissPendingProposal(item)}
                      >
                        Dismiss
                      </button>
                    </div>
                    {expanded ? (
                      <div className="headless-pending-details">
                        {item.reason ? (
                          <div><span className="headless-pending-key">reason</span>{item.reason}</div>
                        ) : null}
                        {pendingProposalContract(item).map(([key, value]) => (
                          <div key={key}>
                            <span className="headless-pending-key">{key}</span>
                            {value}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {error ? (
                      <div className="headless-pending-error" role="alert">
                        {error} — the proposal stays pending; retry or dismiss.
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}
          <div className="headless-composer">
            {context.taskId ? (
              taskRefOn ? (
                <div className="headless-task-ref" data-testid="agent-task-ref">
                  <span className="headless-task-ref-chip" title={context.title || context.taskId}>
                    ⛓ {context.taskId}{context.title ? ` · ${context.title.length > 32 ? `${context.title.slice(0, 31)}…` : context.title}` : ""}
                  </span>
                  <button type="button" className="headless-task-ref-action" data-testid="agent-task-snapshot"
                    disabled={!headlessCanChat || headlessSubmitting}
                    title="发送确定性状态快照(不走 LLM)"
                    onClick={() => void submitHeadlessMessage("总结当前状态", { projectionFirst: true })}>
                    快照
                  </button>
                  <button type="button" className="headless-task-ref-action" data-testid="agent-task-unref"
                    title="解除任务引用" onClick={() => setTaskRefOn(false)}>
                    ×
                  </button>
                </div>
              ) : (
                <div className="headless-task-ref off">
                  <button type="button" className="headless-task-ref-action" data-testid="agent-task-reref"
                    onClick={() => setTaskRefOn(true)}>
                    ⛓ 重新引用 {context.taskId}
                  </button>
                </div>
              )
            ) : null}
            {operatorError ? (
              <div className="headless-composer-alert" role="alert">{operatorError}</div>
            ) : !headlessCanChat ? (
              // Surfaced from the very moment the panel opens (not only after a
              // first failed send). Without this, users typed + pressed Enter,
              // the token gate (submitHeadlessMessage:7945) silently set
              // operatorError but the input had no visible block, and the
              // experience read as "first message hangs, refresh fixes it".
              <div className="headless-composer-alert" role="alert">
                {snapshot
                  ? `${activeBackendTitle} is ${actionState}. Save a valid action token to send messages.`
                  // Gate not resolved yet (first snapshot in flight): saying
                  // "read only, save a token" here was a lie that flashed on
                  // every panel open. Messages typed now park and auto-send.
                  : "Connecting — messages send automatically once ready."}
              </div>
            ) : null}
            <textarea
              ref={headlessInputRef}
              className="headless-input"
              placeholder={headlessPlaceholder}
              aria-invalid={!headlessCanChat || undefined}
              disabled={headlessSubmitting}
              value={headlessMessage}
              onChange={(event) => setHeadlessMessage(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter"
                  && !event.shiftKey
                  && !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void submitHeadlessMessage(event.currentTarget.value);
                }
              }}
            />
            <div className="headless-composer-footer">
              <ComposerSubmitButton
                className="headless-send-button"
                disabled={!headlessMessage.trim()}
                iconSize={21}
                status={deriveComposerStatus(activeHeadlessThread?.status, headlessSubmitting)}
                onStop={activeHeadlessRun ? () => void cancelHeadlessRun(activeHeadlessRun.id) : undefined}
                title={canUseAction("chat-orchestrator") ? "Send" : `${actionState}; save action token first`}
                onClick={() => void submitHeadlessMessage()}
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
