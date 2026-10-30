// OrchestratorPanel + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { clearQuestionDraft, planQuestionDraftKey } from "../common/questionDraft";
import { OPERATOR_BACKENDS } from "../../app/sharedTypes";
import type { ActionResponse, RecentEvent, Snapshot, WebChatArtifact, WebChatChildActivity, WebChatInteraction, WebChatQueueItem, WebChatRuntime } from "../../api/types";
import { referencedTaskId, withImplicitTaskReference } from "./kanbanTaskReference";
import {
  answerWebChatInteraction,
  cancelWebChatQueue,
  controlWebChatTurn,
  enqueueWebChatMessage,
  getAgentSessionHistory,
  getKanbanPendingProposals,
  getWebChatChildActivities,
  getWebChatRuntime,
} from "../../api/client";
import type { PendingKanbanProposal } from "../../api/client";
import { AgentSessionTimeline } from "../../components/agent-session/AgentSessionTimeline";
import { AgentThreadNavigation } from "../../components/agent-session/AgentThreadNavigation";
import { ComposerSubmitButton } from "../../components/agent-session/ComposerSubmitButton";
import { DuringRunComposerActions } from "../../components/agent-session/DuringRunComposerActions";
import { actionPresentation } from "../../components/agent-session/actionPresentation";
import { deriveComposerStatus } from "../../components/agent-session/workState";
import { useWorkingTitle } from "../../components/agent-session/useWorkingTitle";
import { WebChatActivityDrawer } from "../../components/webchat/WebChatActivityDrawer";
import { WebChatArtifactDrawer } from "../../components/webchat/WebChatArtifactDrawer";
import { WebChatAttachments } from "../../components/webchat/WebChatAttachments";
import { WebChatConversationDrawer } from "../../components/webchat/WebChatConversationDrawer";
import { WebChatProfileDrawer } from "../../components/webchat/WebChatProfileDrawer";
import type { WebChatDrawerKind } from "../../components/webchat/WebChatResourceMenu";
import { KanbanModelMenu } from "./KanbanModelMenu";
import { WebChatInteractionCards } from "../../components/webchat/WebChatInteractionCards";
import { WebChatInteractionPanel } from "../../components/webchat/WebChatInteractionPanel";
import { WebChatPendingControls } from "../../components/webchat/WebChatPendingControls";
import {
  emptyInteractionDraft,
  interactionDraftStorageKey,
  interactionKey,
  loadInteractionDrafts,
  saveInteractionDrafts,
  type WebChatInteractionDraft,
} from "../../components/webchat/webChatInteractionModel";
import { buildKanbanConversation } from "../../components/agent-session/projection";
import { proposalRunNotice } from "../../app/triageProposals";
import { planDiscussionBackend, profileRegistrationContinuation } from "../../app/kanbanAgentInteractionPolicy";
import { projectionNeedsFresh } from "../../app/pageLoadPolicy";
import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentHistoryParams,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
} from "./kanbanAgentHistoryPolicy";
import {
  initializeKanbanThreadReads,
  kanbanThreadActivity,
  kanbanThreadUnreadCounts,
  loadKanbanThreadReadState,
  loadKanbanThreadRefs,
  markKanbanThreadRead,
  saveKanbanThreadReadState,
  saveKanbanThreadRefs,
  storedActiveKanbanThread,
} from "./kanbanSessionNavigation";
import type { KanbanThreadActivity, KanbanThreadReadState } from "./kanbanSessionNavigation";
import type {
  AgentConversation,
  AgentProviderCapability,
  AgentSessionActionProposal,
  AgentSessionCard,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
  AgentSessionThreadRef,
} from "../../components/agent-session/types";
import {
  kanbanAgentSessionEventsFromLive,
  kanbanHistoryBoundary,
  isKanbanRunWorking,
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "./kanbanSessionEvents";
import { resolveKanbanTurnPermission } from "./kanbanAgentPermission";
import { useKanbanComposerDraft } from "./useKanbanComposerDraft";
import { useKanbanModelSelection } from "./useKanbanModelSelection";
import { ChevronDown, Maximize2, MessageCircle, Minimize2, Minus, Plus, Send, ShieldAlert, X } from "lucide-react";
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


interface HeadlessPendingMessage {
  id: string;
  threadId: string;
  message: string;
  createdAt: string;
  requestPatch?: Record<string, unknown>;
  backend: OperatorBackend;
  turnId: string;
}

interface SubmitHeadlessOptions {
  backendOverride?: OperatorBackend;
  dangerousAck?: boolean;
  force?: boolean;
  permissionProfileOverride?: string;
  projectionFirst?: boolean;
  requestPatch?: Record<string, unknown>;
}

interface PermissionEscalation {
  backend: OperatorBackend;
  failureEventId: string;
  message: string;
  reason: string;
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


// Mirrors ChannelPage: only auto-scroll to bottom when the user is already
// pinned there. Without this the kanban-agent thread yanked the user back to
// the bottom on every content change (new turn / 15s refresh / thinking-trace
// collapse), so scrolling up to read earlier messages was impossible.
function isScrollElementNearBottom(node: HTMLElement, thresholdPx = 96): boolean {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= thresholdPx;
}


interface HeadlessThreadScrollPosition {
  pinnedToBottom: boolean;
  scrollTop: number;
}


function headlessThreadScrollKey(projectId: string, threadId: string): string {
  return `${projectId}:${threadId}`;
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

function latestPermissionEscalation(
  events: RecentEvent[],
  args: {
    conversationId: string;
    dismissedEventIds: string[];
    projectId: string;
    threadId: string;
  },
): PermissionEscalation | null {
  const byId = new Map(
    events
      .filter((event) => Boolean(event.id))
      .map((event) => [textValue(event.id), event]),
  );
  const dismissed = new Set(args.dismissedEventIds);
  const retried = new Set(
    events
      .filter((event) => event.type === "user.message")
      .map((event) => textValue(event.payload?.permission_escalation_retry_for).trim())
      .filter(Boolean),
  );
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const failed = events[index];
    const payload = failed?.payload ?? {};
    const failureEventId = textValue(failed?.id).trim();
    if (
      failed?.type !== "kanban.agent.turn.failed"
      || payload.status !== "sandbox_unsupported"
      || payload.permission_profile !== "workspace_writer"
      || textValue(payload.backend) !== "codex-headless"
      || !failureEventId
      || dismissed.has(failureEventId)
      || retried.has(failureEventId)
    ) {
      continue;
    }
    if (args.projectId && textValue(payload.project_id) !== args.projectId) continue;
    if (args.conversationId && textValue(payload.conversation_id) !== args.conversationId) continue;
    if (args.threadId && textValue(payload.thread_key) !== args.threadId) continue;

    const replyEventId = textValue(payload.reply_event_id || failed?.causation_id).trim();
    const reply = byId.get(replyEventId);
    const user = reply ? byId.get(textValue(reply.causation_id).trim()) : undefined;
    const backend = asOperatorBackend(payload.backend);
    const message = textValue(user?.payload?.message).trim();
    if (!backend || !message) continue;
    return {
      backend,
      failureEventId,
      message,
      reason: textValue(payload.reason || "Workspace isolation is unavailable on this host."),
    };
  }
  return null;
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
  onOpenChannelThread,
  onOpenTask,
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
  onOpenChannelThread?: (channelId: string, threadId: string) => void;
  onOpenTask?: (taskId: string) => void;
  onSaveToken: (token: string) => void;
  onUnlockSession: (passcode: string) => Promise<{ ok: boolean; status: string; reason?: string }>;
  snapshot: Snapshot | null;
  tokenPresent: boolean;
}) {
  const headlessProjectId = kanbanAgentProjectId(
    activeProjectId,
    snapshot?.project?.project_id || "",
  );
  const localStorage = typeof window === "undefined" ? null : window.localStorage;
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
  const [dismissedPermissionEscalations, setDismissedPermissionEscalations] = useState<string[]>([]);
  const [headlessSubmitting, setHeadlessSubmitting] = useState(false);
  const headlessSubmitInFlight = useRef(false);
  const planSubmissionsInFlight = useRef(new Set<string>());
  const historyRecovery = useRef({ identity: "", boundary: "", inFlight: false });
  const [headlessProposalRunning, setHeadlessProposalRunning] = useState("");
  const [pendingProposals, setPendingProposals] = useState<PendingKanbanProposal[]>([]);
  const [pendingProposalsError, setPendingProposalsError] = useState("");
  const [pendingProposalsRefresh, setPendingProposalsRefresh] = useState(0);
  const [pendingProposalBusy, setPendingProposalBusy] = useState("");
  const [pendingProposalExpanded, setPendingProposalExpanded] = useState<Record<string, boolean>>({});
  const [pendingProposalErrors, setPendingProposalErrors] = useState<Record<string, string>>({});
  const [pendingProposalNotice, setPendingProposalNotice] = useState("");
  const [headlessThreadKey, setHeadlessThreadKey] = useState(() => (
    storedActiveKanbanThread(localStorage, headlessProjectId)
  ));
  const composerDraft = useKanbanComposerDraft(headlessProjectId, headlessThreadKey);
  const headlessPlanDiscussion = composerDraft.planDiscussion;
  const setHeadlessPlanDiscussion = composerDraft.setPlanDiscussion;
  const headlessMessage = composerDraft.message;
  const setHeadlessMessage = composerDraft.setMessage;
  const webChatAttachments = composerDraft.attachments;
  const setWebChatAttachments = composerDraft.setAttachments;
  const [headlessThreads, setHeadlessThreads] = useState<AgentSessionThreadRef[]>(() =>
    loadKanbanThreadRefs(localStorage, headlessProjectId, headlessThreadKey),
  );
  const [headlessReadState, setHeadlessReadState] = useState<KanbanThreadReadState>(() => (
    loadKanbanThreadReadState(localStorage, headlessProjectId)
  ));
  const [headlessReadProjectId, setHeadlessReadProjectId] = useState(headlessProjectId);
  const [headlessQueue, setHeadlessQueue] = useState<WebChatQueueItem[]>([]);
  const [webChatRuntime, setWebChatRuntime] = useState<WebChatRuntime | null>(null);
  const [headlessDurableMessages, setHeadlessDurableMessages] = useState<NonNullable<WebChatRuntime["messages"]>>([]);
  const [webChatArtifacts, setWebChatArtifacts] = useState<WebChatArtifact[]>([]);
  const [webChatActivities, setWebChatActivities] = useState<WebChatChildActivity[]>([]);
  const [webChatDrawer, setWebChatDrawer] = useState<WebChatDrawerKind>("");
  const [webChatAnswering, setWebChatAnswering] = useState<WebChatInteraction | null>(null);
  const [webChatInteractionDrafts, setWebChatInteractionDrafts] = useState<Record<string, WebChatInteractionDraft>>({});
  const [webChatInteractionErrors, setWebChatInteractionErrors] = useState<Record<string, string>>({});
  const [webChatInteractionBusy, setWebChatInteractionBusy] = useState("");
  const [webChatCollapsedInteractions, setWebChatCollapsedInteractions] = useState<string[]>([]);
  const [webChatQueueBusy, setWebChatQueueBusy] = useState("");
  const [headlessPendingMessages, setHeadlessPendingMessages] = useState<HeadlessPendingMessage[]>([]);
  const [headlessHistoryEvents, setHeadlessHistoryEvents] = useState<RecentEvent[]>([]);
  const [headlessBufferedEvents, setHeadlessBufferedEvents] = useState<RecentEvent[]>([]);
  const [headlessHistoryBeforeSeq, setHeadlessHistoryBeforeSeq] = useState<number | null>(null);
  const [headlessHistoryHasMore, setHeadlessHistoryHasMore] = useState(false);
  const [headlessHistoryLoading, setHeadlessHistoryLoading] = useState(false);
  const [headlessHistoryReady, setHeadlessHistoryReady] = useState(false);
  const [headlessHistoryError, setHeadlessHistoryError] = useState("");
  const [headlessSplitThreadKey, setHeadlessSplitThreadKey] = useState("");
  const [backendMenuOpen, setBackendMenuOpen] = useState(false);
  const headlessInputRef = useRef<HTMLTextAreaElement | null>(null);
  // Message typed before the first snapshot resolved the action gate — sent
  // automatically once the gate is known (2026-07-16 first-message race).
  const [pendingGateMessage, setPendingGateMessage] = useState<{ message: string; projectId: string; threadId: string; attachmentVersions: string[] } | null>(null);
  const headlessThreadRef = useRef<HTMLDivElement | null>(null);
  const headlessThreadScrollPositions = useRef(new Map<string, HeadlessThreadScrollPosition>());
  const headlessThreadProjectRef = useRef(headlessProjectId);
  const headlessActivityRef = useRef<KanbanThreadActivity>({});
  const webChatResourceIdentityRef = useRef("");
  const [headlessPinnedToBottom, setHeadlessPinnedToBottom] = useState(true);
  const [headlessHasNewBelow, setHeadlessHasNewBelow] = useState(false);

  const allowedActions = snapshot?.runtime.actions?.allowed ?? [];
  const webSession = snapshot?.runtime.web_session;
  const agentSurface = snapshot?.runtime.agent_surface;
  const appliedAgentProfile = recordValue(webChatRuntime?.agent_profile?.profile);
  const permissionProfile = textValue(
    appliedAgentProfile?.permission_profile || agentSurface?.permission_profile,
  ).trim() || "workspace_writer";
  // A project snapshot is intentionally token-agnostic and may report
  // mutation_enabled=false when it was hydrated without the browser token.
  // A locally saved token is the explicit fallback authorization for this
  // surface, so it must participate in the same gate as the snapshot flag.
  const mutationEnabled = Boolean(snapshot?.runtime.actions?.mutation_enabled)
    || tokenPresent;
  const headlessConversationId = kanbanAgentConversationId(headlessProjectId);
  const webChatInteractionDraftKey = interactionDraftStorageKey(
    headlessProjectId,
    headlessConversationId,
    headlessThreadKey,
  );
  webChatResourceIdentityRef.current = `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`;
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
  const modelSelection = useKanbanModelSelection(headlessProjectId, headlessThreadKey, operatorBackend,
    visible && canUseAction("chat-orchestrator"),
    context.taskId && !composerDraft.unreferencedTasks.includes(context.taskId) ? context.taskId : "");
  useEffect(() => {
    if (pendingGateMessage === null || !snapshot) return;
    const parked = pendingGateMessage;
    setPendingGateMessage(null);
    if (parked.projectId !== headlessProjectId || parked.threadId !== headlessThreadKey) return;
    if (headlessMessage.trim() !== parked.message || JSON.stringify(webChatAttachments.map((item) => item.version_id)) !== JSON.stringify(parked.attachmentVersions)) {
      setOperatorError("The draft changed while connecting; send it again when ready.");
      return;
    }
    if (actionReady && allowedActions.includes("chat-orchestrator")) {
      void submitHeadlessMessage(parked.message);
    } else {
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
          if (webChatResourceIdentityRef.current === `${parked.projectId}:${kanbanAgentConversationId(parked.projectId)}:${parked.threadId}`) {
            setOperatorError(
              "Runtime snapshot unavailable for this project — message not sent. "
              + "Check the project selection (its root may be missing).",
            );
          }
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
    const profileBackend = kanbanChatBackend(
      asOperatorBackend(textValue(appliedAgentProfile?.provider)) ?? "claude-headless",
    );
    if (!profileBackend || !appliedAgentProfile?.provider) return;
    setOperatorBackend(profileBackend);
    setOperatorBackendTouched(true);
    window.localStorage.setItem("zf.operatorBackend", profileBackend);
  }, [webChatRuntime?.agent_profile?.profile_digest]);

  useEffect(() => {
    if (operatorBackendTouched) return;
    setOperatorBackend(preferredHeadlessBackend(headlessBackendOptions));
  }, [headlessBackendOptions, operatorBackendTouched]);

  useEffect(() => {
    const previousProjectId = headlessThreadProjectRef.current;
    if (previousProjectId === headlessProjectId) return;
    rememberHeadlessThreadScroll(previousProjectId, headlessThreadKey);
    const placeholderUpgrade = previousProjectId === "default" && headlessProjectId !== "default";
    const previousDefaultThread = defaultKanbanThreadKey(previousProjectId);
    const storedThreadId = storedActiveKanbanThread(localStorage, headlessProjectId);
    const nextThreadId = placeholderUpgrade && headlessThreadKey !== previousDefaultThread
      ? headlessThreadKey
      : storedThreadId;
    const scopedRefs = loadKanbanThreadRefs(localStorage, headlessProjectId, storedThreadId);
    const carriedRefs = placeholderUpgrade
      ? headlessThreads.filter((thread) => thread.id !== previousDefaultThread)
      : [];
    const nextRefs = saveKanbanThreadRefs(
      localStorage,
      headlessProjectId,
      nextThreadId,
      [...scopedRefs, ...carriedRefs],
    );
    const previousScroll = headlessThreadScrollPositions.current.get(
      headlessThreadScrollKey(previousProjectId, headlessThreadKey),
    );
    if (placeholderUpgrade && previousScroll) {
      headlessThreadScrollPositions.current.set(
        headlessThreadScrollKey(headlessProjectId, nextThreadId),
        previousScroll,
      );
    }
    headlessThreadProjectRef.current = headlessProjectId;
    headlessActivityRef.current = {};
    localStorage?.setItem(kanbanThreadStorageKey(headlessProjectId), nextThreadId);
    setHeadlessThreadKey(nextThreadId);
    setHeadlessThreads(nextRefs);
    setHeadlessReadState(loadKanbanThreadReadState(localStorage, headlessProjectId));
    setHeadlessReadProjectId(headlessProjectId);
    setHeadlessHistoryEvents([]);
    setHeadlessBufferedEvents([]);
    setHeadlessHistoryBeforeSeq(null);
    setHeadlessHistoryHasMore(false);
    setHeadlessHistoryReady(false);
    setHeadlessHistoryError("");
    setHeadlessSplitThreadKey("");
    setHeadlessQueue([]);
    setWebChatRuntime(null);
    setHeadlessDurableMessages([]);
    setWebChatArtifacts([]);
    setWebChatActivities([]);
    setWebChatDrawer("");
    setWebChatAnswering(null);
    setWebChatInteractionDrafts({});
    setWebChatInteractionErrors({});
    setWebChatInteractionBusy("");
    setWebChatCollapsedInteractions([]);
    setWebChatQueueBusy("");
    setHeadlessPendingMessages([]);
    setHeadlessHasNewBelow(false);
    setPendingProposals([]);
    setPendingProposalsError("");
  }, [headlessProjectId]);

  // chat-e2e F2: pending proposals are ledger truth, not session state — a
  // fresh session must resurface them for approval/dismissal.
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    void getKanbanPendingProposals(headlessProjectId).then((page) => {
      if (!cancelled) {
        setPendingProposals(page.items ?? []);
        setPendingProposalsError("");
      }
    }).catch((err: unknown) => {
      if (!cancelled) setPendingProposalsError(err instanceof Error ? err.message : String(err));
    });
    return () => { cancelled = true; };
  }, [headlessProjectId, visible, pendingProposalsRefresh]);

  async function refreshWebChatResources(): Promise<void> {
    const identity = `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`;
    try {
      const runtime = await getWebChatRuntime(headlessProjectId, {
        conversationId: headlessConversationId,
        threadId: headlessThreadKey,
      });
      if (webChatResourceIdentityRef.current !== identity) return;
      setWebChatRuntime(runtime);
      setHeadlessQueue(runtime.queue ?? []);
      setWebChatArtifacts(runtime.artifacts ?? []);
      setHeadlessDurableMessages(runtime.messages ?? []);
      if (historyRecovery.current.identity !== identity) {
        historyRecovery.current = { identity, boundary: "", inFlight: false };
      }
      const recovery = historyRecovery.current;
      const boundary = kanbanHistoryBoundary(runtime.turns, headlessThreadKey);
      if (boundary !== "[]" && boundary !== recovery.boundary && !recovery.inFlight) {
        recovery.inFlight = true;
        try {
          if (await reconcileHeadlessHistoryAfterTurn(identity)) recovery.boundary = boundary;
        } finally {
          recovery.inFlight = false;
        }
      }
      if (webChatResourceIdentityRef.current !== identity) return;
      const durableRefs = (runtime.threads ?? []).flatMap((raw) => {
        const threadId = textValue(raw.thread_id).trim();
        if (!threadId) return [];
        return [{
          id: threadId,
          title: textValue(raw.title).trim() || (threadId === "main" ? "main" : `chat ${threadId.slice(0, 4)}`),
          createdAt: textValue(raw.created_at).trim() || undefined,
        }];
      });
      if (durableRefs.length) {
        setHeadlessThreads((current) => saveKanbanThreadRefs(
          localStorage,
          headlessProjectId,
          headlessThreadKey,
          [...current, ...durableRefs],
        ));
      }
    } catch {
      // Legacy history remains usable while the candidate resource API is
      // unavailable during rollout.
    }
  }

  useEffect(() => {
    if (!visible || !headlessProjectId) return undefined;
    let cancelled = false;
    const refresh = () => {
      if (!cancelled) void refreshWebChatResources();
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // The callback reads the current thread/conversation and is intentionally
    // restarted only when that resource identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [headlessConversationId, headlessProjectId, headlessThreadKey, visible]);

  useEffect(() => {
    setWebChatInteractionDrafts(loadInteractionDrafts(localStorage, webChatInteractionDraftKey));
    setWebChatInteractionErrors({});
    setWebChatInteractionBusy("");
    setWebChatCollapsedInteractions([]);
    setWebChatAnswering(null);
  }, [localStorage, webChatInteractionDraftKey]);

  useEffect(() => {
    if (!visible || webChatDrawer !== "activity") return undefined;
    let cancelled = false;
    const refresh = () => {
      void getWebChatChildActivities(headlessProjectId).then((page) => {
        if (!cancelled) setWebChatActivities(page.items ?? []);
      }).catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [headlessProjectId, visible, webChatDrawer]);

  useEffect(() => {
    if (!visible) return;
    headlessInputRef.current?.focus();
  }, [focusSignal, panelMode, visible]);

  useEffect(() => {
    let cancelled = false;
    setHeadlessHistoryLoading(true);
    setHeadlessHistoryReady(false);
    setHeadlessHistoryError("");
    const request = kanbanAgentHistoryParams({
      threadId: headlessThreadKey,
      conversationId: headlessConversationId,
      backend: operatorBackend,
      limit: 160,
    });
    void getAgentSessionHistory(headlessProjectId, request).then((page) => {
      if (cancelled) return;
      setHeadlessHistoryEvents(page.items ?? []);
      setHeadlessHistoryBeforeSeq(page.next_before_seq ?? null);
      setHeadlessHistoryHasMore(Boolean(page.has_more));
      if (projectionNeedsFresh(page)) {
        void getAgentSessionHistory(headlessProjectId, {
          ...request,
          requireFresh: true,
        }).then((fresh) => {
          if (cancelled) return;
          setHeadlessHistoryEvents(fresh.items ?? []);
          setHeadlessHistoryBeforeSeq(fresh.next_before_seq ?? null);
          setHeadlessHistoryHasMore(Boolean(fresh.has_more));
        }).catch(() => undefined);
      }
    }).catch((err) => {
      if (!cancelled) {
        setHeadlessHistoryEvents([]);
        setHeadlessHistoryBeforeSeq(null);
        setHeadlessHistoryHasMore(false);
        setHeadlessHistoryError(err instanceof Error ? err.message : String(err));
      }
    }).finally(() => {
      if (!cancelled) {
        setHeadlessHistoryLoading(false);
        setHeadlessHistoryReady(true);
      }
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

  async function reconcileHeadlessHistoryAfterTurn(
    identity = `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`,
  ): Promise<boolean> {
    // SSE is the primary path. A successful action response is also a
    // deterministic terminal boundary, so reconcile the durable transcript
    // here when an event-stream delivery is delayed or dropped. Without this,
    // a plan card could exist in the ledger but remain invisible until reload.
    try {
      const page = await getAgentSessionHistory(headlessProjectId, {
        ...kanbanAgentHistoryParams({
          threadId: headlessThreadKey,
          conversationId: headlessConversationId,
          backend: operatorBackend,
          limit: 160,
        }),
        requireFresh: true,
      });
      if (webChatResourceIdentityRef.current !== identity) return false;
      setHeadlessHistoryEvents((current) => mergeEventsByIdentity(current, page.items ?? []));
      setHeadlessHistoryBeforeSeq(page.next_before_seq ?? null);
      setHeadlessHistoryHasMore(Boolean(page.has_more));
      return true;
    } catch {
      // The successful action is authoritative. Keep the live transcript when
      // this read-only recovery request is temporarily unavailable.
      return false;
    }
  }

  // 任务自动引用(operator 2026-07-11):从 TaskDetail 打开时 context.taskId
  // 已自动派生;这里补"看得见/可解除"——chip + 可关。关掉后消息不再携带
  // task/trace/pdd/fanout 引用。
  const taskRefOn = !context.taskId || !composerDraft.unreferencedTasks.includes(context.taskId);
  const setTaskRefOn = (referenced: boolean) => composerDraft.setTaskReferenced(context.taskId, referenced);

  function contextPayload(): Record<string, unknown> {
    return {
      task_id: referencedTaskId(taskRefOn, context.taskId),
      trace_id: (taskRefOn && context.traceId) || undefined,
      pdd_id: (taskRefOn && context.pddId) || undefined,
      fanout_id: (taskRefOn && context.fanoutId) || undefined,
      project_id: headlessProjectId,
      conversation_id: headlessConversationId,
      thread_key: headlessThreadKey,
    };
  }

  function rememberHeadlessThreadScroll(projectId: string, threadId: string): void {
    const node = headlessThreadRef.current;
    if (!node || !projectId || !threadId) return;
    headlessThreadScrollPositions.current.set(
      headlessThreadScrollKey(projectId, threadId),
      {
        pinnedToBottom: isScrollElementNearBottom(node),
        scrollTop: node.scrollTop,
      },
    );
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
    rememberHeadlessThreadScroll(headlessProjectId, headlessThreadKey);
    const next = newHeadlessThreadKey();
    setHeadlessThreadKey(next);
    setHeadlessThreads((current) => {
      const nextRefs = [
        ...current,
        { id: next, title: `chat ${current.length + 1}`, createdAt: new Date().toISOString() },
      ];
      return saveKanbanThreadRefs(localStorage, headlessProjectId, next, nextRefs);
    });
    setHeadlessSplitThreadKey("");
    localStorage?.setItem(kanbanThreadStorageKey(headlessProjectId), next);
    setOperatorError("");
    headlessInputRef.current?.focus();
  }

  function selectHeadlessThread(threadId: string) {
    if (threadId === headlessThreadKey) {
      headlessInputRef.current?.focus();
      return;
    }
    rememberHeadlessThreadScroll(headlessProjectId, headlessThreadKey);
    const nextReadState = markKanbanThreadRead(
      headlessReadState,
      headlessActivityRef.current,
      threadId,
    );
    saveKanbanThreadReadState(localStorage, headlessProjectId, nextReadState);
    setHeadlessReadState(nextReadState);
    setHeadlessThreadKey(threadId);
    if (headlessSplitThreadKey === threadId) setHeadlessSplitThreadKey("");
    localStorage?.setItem(kanbanThreadStorageKey(headlessProjectId), threadId);
    headlessInputRef.current?.focus();
  }

  async function queueHeadlessMessage(
    message: string,
    requestPatch?: Record<string, unknown>,
    backend: OperatorBackend = operatorBackend,
    turnPermissionProfile: string = permissionProfile,
  ) {
    const idempotencyKey = `queue-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    try {
      const result = await enqueueWebChatMessage(headlessProjectId, {
        ...requestPatch,
        ...contextPayload(),
        backend,
        permission_profile: turnPermissionProfile,
        thread_id: headlessThreadKey,
        message,
        idempotency_key: idempotencyKey,
        source_turn_id: webChatRuntime?.turns.find((item) => (
          item.thread_id === headlessThreadKey
          && ["claimed", "running", "waiting_input"].includes(item.state)
        ))?.turn_id || "",
      });
      setHeadlessQueue((current) => [
        ...current.filter((item) => item.queue_id !== result.item.queue_id),
        result.item,
      ]);
      setHeadlessMessage("");
      setWebChatAttachments([]);
      setOperatorError("");
      await refreshWebChatResources();
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitHeadlessMessage(messageOverride?: string, options: SubmitHeadlessOptions = {}) {
    if (composerDraft.uploading) {
      setOperatorError("Wait for attachments to finish uploading before sending.");
      return;
    }
    if (modelSelection.selectionUnavailable && !options.backendOverride && !options.projectionFirst) {
      setOperatorError("The selected model or effort is unavailable. Choose an available option before sending.");
      return;
    }
    // React state has not committed yet when two Enter/click events arrive.
    // Lock before permission lookup, including the queue and early-return paths.
    if (headlessSubmitInFlight.current) return;
    headlessSubmitInFlight.current = true;
    setHeadlessSubmitting(true);
    try {
      await dispatchHeadlessMessage(messageOverride, options);
    } finally {
      headlessSubmitInFlight.current = false;
      setHeadlessSubmitting(false);
    }
  }

  async function dispatchHeadlessMessage(messageOverride?: string, options: SubmitHeadlessOptions = {}) {
    const message = (messageOverride ?? headlessMessage).trim();
    const explicitRequestPatch = options.requestPatch ?? {};
    const discussionRequestPatch = (
      headlessPlanDiscussion
      && !("plan_response" in explicitRequestPatch)
      && !("plan_discussion" in explicitRequestPatch)
    ) ? {
        plan_discussion: {
          request_event_id: headlessPlanDiscussion.requestEventId,
          request_id: headlessPlanDiscussion.requestId,
          revision: headlessPlanDiscussion.revision,
        },
      } : {};
    const attachmentPatch = webChatAttachments.length && !("attachment_refs" in explicitRequestPatch)
      ? {
          attachment_refs: webChatAttachments.map((item) => ({
            ref: item.storage_uri,
            digest: item.digest,
            version_id: item.version_id,
            logical_name: item.logical_name,
          })),
        }
      : {};
    const isPlanDiscussion = "plan_discussion" in discussionRequestPatch || "plan_discussion" in explicitRequestPatch;
    const boundDiscussionBackend = isPlanDiscussion
      ? kanbanChatBackend(
          asOperatorBackend(planDiscussionBackend(
            headlessPlanDiscussion?.backend,
            operatorBackend,
          )) ?? operatorBackend,
        )
      : null;
    if (isPlanDiscussion && modelSelection.customized && boundDiscussionBackend !== operatorBackend && !options.backendOverride) {
      setOperatorError("This Plan is bound to a different provider. Switch providers or clear the selected model and effort.");
      return;
    }
    const requestPatch = {
      ...discussionRequestPatch,
      ...attachmentPatch,
      ...(!options.backendOverride && !options.projectionFirst ? modelSelection.request : {}),
      ...explicitRequestPatch,
    };
    const targetBackend = options.backendOverride ?? boundDiscussionBackend ?? operatorBackend;
    if (!message || !isChatBackend(targetBackend)) return;
    const permissionResolution = await resolveKanbanTurnPermission({
      override: options.permissionProfileOverride,
      projectId: headlessProjectId,
      runtime: webChatRuntime,
      fallback: textValue(agentSurface?.permission_profile).trim() || "workspace_writer",
      loadRuntime: () => getWebChatRuntime(headlessProjectId, {
        conversationId: headlessConversationId,
        threadId: headlessThreadKey,
      }),
    });
    const turnPermissionProfile = permissionResolution.permissionProfile;
    if (
      permissionResolution.runtime
      && webChatResourceIdentityRef.current
        === `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`
    ) {
      setWebChatRuntime(permissionResolution.runtime);
      setHeadlessQueue(permissionResolution.runtime.queue ?? []);
      setWebChatArtifacts(permissionResolution.runtime.artifacts ?? []);
    }
    if (activeThreadBusy && !options.force && !isPlanDiscussion) {
      await queueHeadlessMessage(message, requestPatch, targetBackend, turnPermissionProfile);
      return;
    }
    if (!canUseAction("chat-orchestrator")) {
      // First-message race (operator report 2026-07-16): before the snapshot
      // arrives the gate reads "read only" and the send silently bounced.
      // Park the message and let the snapshot effect below flush it instead
      // of erroring on a gate that is merely unknown.
      if (!snapshot) {
        setPendingGateMessage({ message, projectId: headlessProjectId, threadId: headlessThreadKey,
          attachmentVersions: webChatAttachments.map((item) => item.version_id) });
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
      && !options.backendOverride
      && !boundDiscussionBackend
      && operatorBackend === "claude-headless"
      && !!configuredChatBackend
      && configuredChatBackend !== "claude-headless"
    );
    if (!agentSurface || stillOnUncorrectedDefault) {
      setOperatorError("Agent backend list is still loading; try again in a moment.");
      return;
    }
    const directAction = slashAction(message);
    setHeadlessMessage("");
    let pendingTurnId = "";
    try {
      if (directAction) {
        if (!canUseAction(directAction.action)) {
          setOperatorError(`action ${directAction.action} is ${actionState}`);
          setHeadlessMessage(message);
          return;
        }
        const payload = withImplicitTaskReference(directAction.payload, directAction.action, taskRefOn, context.taskId);
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
        backend: targetBackend,
        createdAt: new Date().toISOString(),
      };
      setHeadlessPendingMessages((current) => [
        ...current.filter((item) => item.turnId !== turnId),
        pendingMessage,
      ]);
      const result = await Promise.resolve(onAction("chat-orchestrator", {
        ...requestPatch,
        ...contextPayload(),
        backend: targetBackend,
        permission_profile: turnPermissionProfile,
        // The token/passcode-gated Web session is the operator acknowledgement
        // for its configured default profile. Explicit escalation remains a
        // separate "Run once with full access" action.
        dangerous_ack: turnPermissionProfile === "dangerous_full" || undefined,
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
      setWebChatAttachments([]);
      if (isPlanDiscussion) {
        setHeadlessPlanDiscussion(null);
      }
      await reconcileHeadlessHistoryAfterTurn();
      await refreshWebChatResources();
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
      setHeadlessMessage(message);
      if (pendingTurnId) {
        setHeadlessPendingMessages((current) => current.filter((item) => item.turnId !== pendingTurnId));
      }
    }
  }

  async function runPermissionEscalation() {
    const pending = permissionEscalation;
    if (!pending) return;
    setDismissedPermissionEscalations((current) => (
      current.includes(pending.failureEventId)
        ? current
        : [...current, pending.failureEventId]
    ));
    await submitHeadlessMessage(pending.message, {
      backendOverride: pending.backend,
      dangerousAck: true,
      force: true,
      permissionProfileOverride: "dangerous_full",
      requestPatch: {
        permission_escalation_retry_for: pending.failureEventId,
      },
    });
  }

  async function findPendingDurableInteraction(
    matches: (interaction: WebChatInteraction) => boolean,
  ): Promise<WebChatInteraction | undefined> {
    let runtime = webChatRuntime;
    try {
      const freshRuntime = await getWebChatRuntime(headlessProjectId, {
        conversationId: headlessConversationId,
        threadId: headlessThreadKey,
      });
      runtime = freshRuntime;
      if (
        webChatResourceIdentityRef.current
        === `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`
      ) {
        setWebChatRuntime(freshRuntime);
        setHeadlessQueue(freshRuntime.queue ?? []);
        setWebChatArtifacts(freshRuntime.artifacts ?? []);
      }
    } catch {
      // Preserve the last fresh projection as a compatibility fallback while
      // the candidate runtime endpoint is temporarily unavailable.
    }
    return runtime?.interactions.find((item) => {
      if (item.status !== "pending") return false;
      const interactionTurn = runtime?.turns.find((turn) => turn.turn_id === item.turn_id);
      return interactionTurn?.thread_id === headlessThreadKey && matches(item);
    });
  }

  async function pendingProposalInteraction(
    proposal: AgentSessionActionProposal,
  ): Promise<WebChatInteraction | undefined> {
    return findPendingDurableInteraction((item) => {
      if (item.kind !== "action_approval") return false;
      const proposalId = String(item.request.proposal_id || "");
      const proposalEventId = String(item.request.proposal_event_id || "");
      return Boolean(
        (proposal.proposalId && proposalId === proposal.proposalId)
        || (proposal.proposalEventId && proposalEventId === proposal.proposalEventId),
      );
    });
  }

  async function runHeadlessProposal(proposal: AgentSessionActionProposal, key: string) {
    if (!proposal.valid || !canUseAction(proposal.action)) return;
    setHeadlessProposalRunning(key);
    try {
      const durableInteraction = await pendingProposalInteraction(proposal);
      const payload = withImplicitTaskReference({
        ...proposal.payload,
        project_id: textValue(proposal.payload.project_id) || headlessProjectId,
        conversation_id: textValue(proposal.payload.conversation_id) || headlessConversationId,
        thread_id: textValue(proposal.payload.thread_id) || headlessThreadKey,
        run_id: textValue(proposal.payload.run_id) || key.replace(/^proposal-/, ""),
        source: textValue(proposal.payload.source) || "kanban-agent-proposal",
        proposal_event_id: proposal.proposalEventId || undefined,
      }, proposal.action, taskRefOn, context.taskId);
      const result = await Promise.resolve(onAction(proposal.action, payload));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        return;
      }
      if (durableInteraction) {
        await answerWebChatInteraction(
          headlessProjectId,
          durableInteraction.interaction_id,
          {
            revision: durableInteraction.revision,
            response: {
              decision: "approved",
              action: proposal.action,
              proposal_id: proposal.proposalId || "",
              proposal_event_id: proposal.proposalEventId || "",
              applied_action: proposal.action,
            },
            currentness: durableInteraction.currentness || {},
            resume: false,
          },
        );
        await refreshWebChatResources();
      }
      setOperatorError("");
      await continueChannelSetup(proposal.action, proposal.payload);
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    } finally {
      setHeadlessProposalRunning("");
      setPendingProposalsRefresh((value) => value + 1);
    }
  }

  async function continueChannelSetup(action: string, payload: Record<string, unknown>) {
    const plan = profileRegistrationContinuation(action, payload);
    if (!plan) return;
    await submitHeadlessMessage(
      "The requested Channel Profile registration has succeeded. Continue the original Channel setup using the owner's custom answer and return updated confirmation options.",
      { force: true, requestPatch: { plan_discussion: plan } },
    );
  }

  async function rejectHeadlessProposal(proposal: AgentSessionActionProposal, key: string) {
    if (!proposal.proposalEventId || !canUseAction("kanban-proposal-dismiss")) return;
    setHeadlessProposalRunning(key);
    try {
      const durableInteraction = await pendingProposalInteraction(proposal);
      const result = await Promise.resolve(onAction("kanban-proposal-dismiss", {
        project_id: headlessProjectId,
        proposal_event_id: proposal.proposalEventId,
        reason: "rejected from Kanban Agent Approve interaction",
      }));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        return;
      }
      if (durableInteraction) {
        await answerWebChatInteraction(
          headlessProjectId,
          durableInteraction.interaction_id,
          {
            revision: durableInteraction.revision,
            response: {
              decision: "rejected",
              action: proposal.action,
              proposal_id: proposal.proposalId || "",
              proposal_event_id: proposal.proposalEventId,
              applied_action: "kanban-proposal-dismiss",
            },
            currentness: durableInteraction.currentness || {},
            resume: false,
          },
        );
        await refreshWebChatResources();
      }
      setOperatorError("");
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    } finally {
      setHeadlessProposalRunning("");
      setPendingProposalsRefresh((value) => value + 1);
    }
  }

  async function adoptResearchResult(card: AgentSessionCard, key: string) {
    const payload = card.payload?.adoptPayload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return;
    }
    setHeadlessProposalRunning(key);
    try {
      const result = await Promise.resolve(onAction(
        "research-adopt",
        payload as Record<string, unknown>,
      ));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        return;
      }
      setOperatorError("");
    } finally {
      setHeadlessProposalRunning("");
    }
  }

  function reviseHeadlessProposal(proposal: AgentSessionActionProposal) {
    const proposalId = proposal.proposalEventId || proposal.proposalId || proposal.action;
    setHeadlessMessage(
      `Revise proposal ${proposalId} for ${proposal.action}: ${proposal.reason || "adjust the proposed action before approval"}`,
    );
    headlessInputRef.current?.focus();
  }

  async function submitPlanResponse(
    request: AgentSessionPlanRequest,
    response: AgentSessionPlanResponse,
    key: string,
  ) {
    if (planSubmissionsInFlight.current.has(key)) return;
    planSubmissionsInFlight.current.add(key);
    if (
      headlessPlanDiscussion?.requestEventId === request.requestEventId
    ) {
      setHeadlessPlanDiscussion(null);
    }
    setHeadlessProposalRunning(key);
    const clearDraft = () => clearQuestionDraft(planQuestionDraftKey(
      JSON.stringify([headlessProjectId, headlessConversationId, headlessThreadKey]), request,
    ));
    try {
      const selectedOption = request.options.find(
        (option) => option.id === response.optionId,
      );
      const customAnswer = response.answers.some((answer) => answer.optionId === "other");
      const submitAction = customAnswer ? "" : (
        selectedOption?.submitAction
        || request.submitAction
        || ""
      );
      const submitMode = customAnswer ? "continue" : (
        selectedOption?.submitMode
        || request.submitMode
        || (submitAction ? "apply" : "continue")
      );
      const planResponse = {
        request_event_id: response.requestEventId,
        request_id: response.requestId,
        revision: response.revision,
        question_id: response.questionId,
        option_id: response.optionId,
        answer: response.answer,
        answers: response.answers.map((answer) => ({
          question_id: answer.questionId,
          option_id: answer.optionId,
          answer: answer.answer,
        })),
      };
      const durableInteraction = await findPendingDurableInteraction((item) => {
        const interactionRequestId = String(item.request.request_id || "");
        const interactionEventId = String(item.request.request_event_id || "");
        return interactionRequestId === request.requestId
          || interactionEventId === request.requestEventId;
      });
      if (submitAction && submitMode !== "continue") {
        if (!canUseAction("kanban-plan-apply")) {
          setOperatorError(`action kanban-plan-apply is ${actionState}`);
          return;
        }
        const result = await Promise.resolve(onAction("kanban-plan-apply", {
          project_id: headlessProjectId,
          conversation_id: headlessConversationId,
          thread_id: headlessThreadKey,
          task_id: referencedTaskId(taskRefOn, context.taskId),
          plan_response: planResponse,
        }));
        if (actionFailed(result)) {
          setOperatorError(actionFailureReason(result));
          return;
        }
        clearDraft();
        if (durableInteraction) {
          await answerWebChatInteraction(
            headlessProjectId,
            durableInteraction.interaction_id,
            {
              revision: durableInteraction.revision,
              response: {
                answer: response.answer,
                option_id: response.optionId,
                question_id: response.questionId,
                answers: planResponse.answers,
                plan_response: planResponse,
                applied_action: submitAction,
              },
              currentness: durableInteraction.currentness || {},
              resume: false,
            },
          );
          await refreshWebChatResources();
        }
        setOperatorError("");
        return;
      }
      if (durableInteraction) {
        await answerWebChatInteraction(
          headlessProjectId,
          durableInteraction.interaction_id,
          {
            revision: durableInteraction.revision,
            response: {
              answer: response.answer,
              option_id: response.optionId,
              question_id: response.questionId,
              answers: planResponse.answers,
              plan_response: planResponse,
            },
            currentness: durableInteraction.currentness || {},
          },
        );
        clearDraft();
        await refreshWebChatResources();
        setOperatorError("");
        return;
      }
      await submitHeadlessMessage(
        `Plan: ${request.question}\nAnswer: ${response.answer}`,
        {
          force: true,
          backendOverride: (
            asOperatorBackend(request.backend)
            ?? operatorBackend
          ),
          requestPatch: {
            plan_response: planResponse,
          },
        },
      );
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    } finally {
      planSubmissionsInFlight.current.delete(key);
      setHeadlessProposalRunning("");
      setPendingProposalsRefresh((value) => value + 1);
    }
  }

  function chatAboutPlan(request: AgentSessionPlanRequest) {
    setHeadlessPlanDiscussion(request);
    headlessInputRef.current?.focus();
  }

  async function runPendingProposal(item: PendingKanbanProposal) {
    if (!item.valid || !canUseAction(item.action)) return;
    setPendingProposalBusy(item.proposal_event_id);
    setPendingProposalErrors((current) => ({ ...current, [item.proposal_event_id]: "" }));
    try {
      const result = await Promise.resolve(onAction(item.action, {
        ...item.payload,
        project_id: textValue(item.payload.project_id) || headlessProjectId,
        conversation_id: textValue(item.payload.conversation_id)
          || item.conversation_id
          || headlessConversationId,
        thread_id: textValue(item.payload.thread_id)
          || item.thread_key
          || headlessThreadKey,
        run_id: textValue(item.payload.run_id) || item.turn_id || undefined,
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
        const pending = await findPendingDurableInteraction((interaction) => (
          interaction.kind === "action_approval"
          && interaction.request.proposal_event_id === item.proposal_event_id
        ));
        if (pending) {
          await answerWebChatInteraction(headlessProjectId, pending.interaction_id, {
            revision: pending.revision,
            response: { decision: "approved", applied_action: item.action },
            currentness: pending.currentness || {},
            resume: false,
          });
          await refreshWebChatResources();
        }
        await continueChannelSetup(item.action, item.payload);
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
    const durableTurn = webChatRuntime?.turns.find((item) => (
      item.turn_id === runId
      || item.current_attempt_id === runId
      || (
        item.thread_id === headlessThreadKey
        && ["claimed", "running", "waiting_input", "resume_queued", "retry_wait"].includes(item.state)
      )
    ));
    try {
      if (durableTurn) {
        await controlWebChatTurn(headlessProjectId, durableTurn.turn_id, {
          operation: "cancel",
          idempotency_key: `cancel-${durableTurn.turn_id}`,
        });
        await refreshWebChatResources();
        setOperatorError("");
        return;
      }
      if (!canUseAction("agent-session-cancel")) {
        setOperatorError(`cancel is ${actionState}`);
        return;
      }
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

  async function removeQueuedMessage(item: WebChatQueueItem) {
    setWebChatQueueBusy(item.queue_id);
    try {
      await cancelWebChatQueue(headlessProjectId, item.queue_id);
      await refreshWebChatResources();
      setOperatorError("");
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    } finally {
      setWebChatQueueBusy("");
    }
  }

  async function restoreQueuedMessage(item: WebChatQueueItem) {
    if (headlessMessage.trim()) {
      setOperatorError("Clear the current draft before restoring a queued message.");
      headlessInputRef.current?.focus();
      return;
    }
    setWebChatQueueBusy(item.queue_id);
    try {
      if (item.state === "queued") {
        await cancelWebChatQueue(headlessProjectId, item.queue_id);
      }
      setHeadlessMessage(item.message);
      await refreshWebChatResources();
      setOperatorError("");
      window.requestAnimationFrame(() => headlessInputRef.current?.focus());
    } catch (err) {
      setOperatorError(err instanceof Error ? err.message : String(err));
    } finally {
      setWebChatQueueBusy("");
    }
  }

  function updateWebChatInteractionDraft(
    interaction: WebChatInteraction,
    draft: WebChatInteractionDraft,
  ) {
    const key = interactionKey(interaction);
    setWebChatInteractionDrafts((current) => {
      const next = { ...current, [key]: draft };
      saveInteractionDrafts(localStorage, webChatInteractionDraftKey, next);
      return next;
    });
  }

  function composeWebChatInteraction(interaction: WebChatInteraction) {
    const key = interactionKey(interaction);
    setWebChatCollapsedInteractions((current) => current.filter((item) => item !== key));
    setWebChatAnswering(interaction);
    setHeadlessMessage("");
  }

  function collapseWebChatInteraction(interaction: WebChatInteraction) {
    const key = interactionKey(interaction);
    setWebChatCollapsedInteractions((current) => (
      current.includes(key) ? current : [...current, key]
    ));
    setWebChatAnswering((current) => (
      current && interactionKey(current) === key ? null : current
    ));
  }

  async function answerDurableInteraction(
    interaction: WebChatInteraction,
    response: Record<string, unknown>,
  ) {
    const key = interactionKey(interaction);
    if (planSubmissionsInFlight.current.has(key)) return;
    planSubmissionsInFlight.current.add(key);
    setWebChatInteractionBusy(key);
    setWebChatInteractionErrors((current) => ({ ...current, [key]: "" }));
    try {
      const result = await answerWebChatInteraction(headlessProjectId, interaction.interaction_id, {
        revision: interaction.revision,
        response,
        currentness: interaction.currentness || {},
      });
      setWebChatAnswering(null);
      setWebChatCollapsedInteractions((current) => current.filter((item) => item !== key));
      setWebChatInteractionDrafts((current) => {
        const next = { ...current };
        delete next[key];
        saveInteractionDrafts(localStorage, webChatInteractionDraftKey, next);
        return next;
      });
      await refreshWebChatResources();
      const resume = recordValue(result.resume);
      setOperatorError(
        resume?.status === "awaiting_exact_interaction_consumer"
          ? "Approval recorded. Waiting for the exact tool consumer to continue."
          : "",
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setWebChatInteractionErrors((current) => ({ ...current, [key]: message }));
      setOperatorError("");
      await refreshWebChatResources();
    } finally {
      planSubmissionsInFlight.current.delete(key);
      setWebChatInteractionBusy("");
    }
  }

  async function applyDurableControl(operation: "steer" | "interrupt", messageOverride = "") {
    const turn = webChatRuntime?.turns.find((item) => (
      item.thread_id === headlessThreadKey
      && ["claimed", "running", "waiting_input", "resume_queued", "retry_wait"].includes(item.state)
    ));
    if (!turn) return;
    const controlMessage = (messageOverride || (operation === "steer" ? headlessMessage : "")).trim();
    try {
      const result = await controlWebChatTurn(headlessProjectId, turn.turn_id, {
        operation,
        message: controlMessage,
        idempotency_key: `${operation}-${turn.turn_id}-${Date.now().toString(36)}`,
      });
      const receipt = recordValue(result.receipt);
      if (controlMessage) {
        setHeadlessMessage("");
      }
      setOperatorError(
        receipt?.status === "queued_fallback" || receipt?.fallback === "queue"
        ? "Native steer is unavailable; the message was queued for the next turn."
        : "",
      );
      await refreshWebChatResources();
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
  const headlessActivity = useMemo(
    () => kanbanThreadActivity(
      kanbanAgentSessionEventsFromLive(headlessConversationEvents, {
        conversationId: headlessConversationId,
        projectId: headlessProjectId,
      }),
      headlessThreadKey,
    ),
    [headlessConversationEvents, headlessConversationId, headlessProjectId, headlessThreadKey],
  );
  headlessActivityRef.current = headlessActivity;
  useEffect(() => {
    if (!headlessHistoryReady || headlessReadProjectId !== headlessProjectId) return;
    setHeadlessReadState((current) => {
      const initialized = initializeKanbanThreadReads(current, headlessActivity);
      const next = markKanbanThreadRead(initialized, headlessActivity, headlessThreadKey);
      if (next === current) return current;
      saveKanbanThreadReadState(localStorage, headlessProjectId, next);
      return next;
    });
  }, [headlessActivity, headlessHistoryReady, headlessProjectId, headlessReadProjectId, headlessThreadKey]);
  const permissionEscalation = useMemo(() => latestPermissionEscalation(
    headlessConversationEvents,
    {
      conversationId: headlessConversationId,
      dismissedEventIds: dismissedPermissionEscalations,
      projectId: headlessProjectId,
      threadId: headlessThreadKey,
    },
  ), [
    dismissedPermissionEscalations,
    headlessConversationEvents,
    headlessConversationId,
    headlessProjectId,
    headlessThreadKey,
  ]);
  const headlessConversation = useMemo(() => buildKanbanConversation({
    activeThreadId: headlessThreadKey,
    backend: operatorBackend,
    conversationId: headlessConversationId,
    events: headlessConversationEvents,
    durableMessages: headlessDurableMessages,
    durableTurns: webChatRuntime?.turns,
    knownThreads: headlessThreads,
    projectId: headlessProjectId,
  }), [headlessConversationEvents, headlessConversationId, headlessDurableMessages, headlessProjectId, headlessThreadKey, headlessThreads, operatorBackend, webChatRuntime?.turns]);
  useEffect(() => {
    setHeadlessPendingMessages((current) => (
      current.filter((item) => !conversationHasHeadlessTurn(headlessConversation, item))
    ));
  }, [headlessConversation]);
  const visibleHeadlessConversation = useMemo(() => (
    withPendingHeadlessTurns(headlessConversation, headlessPendingMessages)
  ), [headlessConversation, headlessPendingMessages]);
  const headlessUnreadCounts = useMemo(
    () => headlessReadProjectId === headlessProjectId
      ? kanbanThreadUnreadCounts(headlessReadState, headlessActivity)
      : {},
    [headlessActivity, headlessProjectId, headlessReadProjectId, headlessReadState],
  );
  const displayedHeadlessConversation = useMemo(() => ({
    ...visibleHeadlessConversation,
    threads: visibleHeadlessConversation.threads.map((thread) => ({
      ...thread,
      unseenCount: thread.id === headlessThreadKey ? 0 : headlessUnreadCounts[thread.id] ?? 0,
    })),
  }), [headlessThreadKey, headlessUnreadCounts, visibleHeadlessConversation]);
  const activeHeadlessThread = displayedHeadlessConversation.threads.find((thread) => thread.id === headlessThreadKey)
    ?? displayedHeadlessConversation.threads[0];
  const referenceablePlans = (activeHeadlessThread?.id === headlessThreadKey ? activeHeadlessThread.turns : [])
    .flatMap((turn) => turn.cards)
    .map((card) => card.planRequest)
    .filter((plan): plan is AgentSessionPlanRequest => Boolean(plan?.valid && plan.requestId && plan.requestEventId));
  const activeDurableTurn = webChatRuntime?.turns.find((item) => (
    item.thread_id === headlessThreadKey
    && ["submitted", "queued", "claimed", "running", "resume_queued", "retry_wait"].includes(item.state)
  )) ?? webChatRuntime?.turns.find((item) => (
    item.thread_id === headlessThreadKey && item.state === "waiting_input"
  ));
  const hasDurableTurns = webChatRuntime?.turns.some((item) => item.thread_id === headlessThreadKey);
  const activeHeadlessRun = activeHeadlessThread
    ? [...activeHeadlessThread.turns.flatMap((turn) => turn.runs)].reverse()
        .find((run) => isKanbanRunWorking(run, webChatRuntime?.turns ?? []))
    : undefined;
  const activeThreadBusy = Boolean(
    activeDurableTurn
    || activeHeadlessRun
    || (
      !hasDurableTurns && activeHeadlessThread
      && ["streaming", "submitted", "queued", "waiting_input"].includes(activeHeadlessThread.status)
    ),
  );
  // Tab title shows "● …" while the headless session is working. Single owner:
  // channel group chat deliberately doesn't drive it.
  useWorkingTitle(Boolean(activeHeadlessRun) || Boolean(activeDurableTurn && activeDurableTurn.state !== "waiting_input"));
  const durableControls = new Set(webChatRuntime?.capabilities.controls ?? []);
  const canNativeSteer = Boolean(
    activeDurableTurn
    && webChatRuntime?.capabilities.native_steer
    && durableControls.has("steer")
    && activeDurableTurn.state !== "waiting_input"
    && !modelSelection.customized
    && webChatAttachments.length === 0
  );
  const canInterruptDurableTurn = Boolean(
    activeDurableTurn
    && durableControls.has("interrupt")
    && activeDurableTurn.state !== "waiting_input"
    && !modelSelection.customized
    && webChatAttachments.length === 0
  );
  const canCancelDurableTurn = Boolean(activeDurableTurn && durableControls.has("cancel"));
  const scopedHeadlessQueue = headlessQueue.filter((item) => item.thread_id === headlessThreadKey);
  const pendingDurableInteractions = useMemo(() => {
    const projectedInteractionIds = new Set(
      (activeHeadlessThread?.turns || []).flatMap((turn) => turn.cards.flatMap((card) => [
        card.id,
        card.planRequest?.requestId || "",
        card.planRequest?.requestEventId || "",
        String(card.proposal?.payload?.proposal_id || ""),
      ])).filter(Boolean),
    );
    return (webChatRuntime?.interactions || []).filter((item) => {
      if (item.status !== "pending") return false;
      const turn = webChatRuntime?.turns.find((candidate) => candidate.turn_id === item.turn_id);
      if (!turn || turn.thread_id !== headlessThreadKey) return false;
      const requestId = String(
        item.request.request_id
        || item.request.request_event_id
        || item.request.proposal_id
        || item.request.id
        || "",
      );
      return !requestId || !projectedInteractionIds.has(requestId);
    });
  }, [activeHeadlessThread?.turns, headlessThreadKey, webChatRuntime]);
  const activeInteractionKey = webChatAnswering ? interactionKey(webChatAnswering) : "";
  useEffect(() => {
    setWebChatAnswering((current) => {
      const currentKey = current ? interactionKey(current) : "";
      const refreshed = currentKey
        ? pendingDurableInteractions.find((item) => interactionKey(item) === currentKey)
        : undefined;
      if (refreshed) return refreshed === current ? current : refreshed;
      return pendingDurableInteractions.find((item) => (
        ["ask_user", "tool_approval"].includes(item.kind)
        && !webChatCollapsedInteractions.includes(interactionKey(item))
      )) ?? null;
    });
  }, [pendingDurableInteractions, webChatCollapsedInteractions]);
  const headlessScrollSignature = agentConversationScrollSignature(
    visibleHeadlessConversation,
    headlessThreadKey,
    [],
  );

  // Each thread owns its reading position. Restoring after history settles keeps
  // switching sessions from discarding where the operator was reading.
  useEffect(() => {
    if (!headlessHistoryReady || headlessHistoryLoading) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const node = headlessThreadRef.current;
      if (!node) return;
      const saved = headlessThreadScrollPositions.current.get(
        headlessThreadScrollKey(headlessProjectId, headlessThreadKey),
      );
      if (!saved || saved.pinnedToBottom) {
        scrollElementToBottom(node);
        setHeadlessPinnedToBottom(true);
      } else {
        node.scrollTop = Math.min(saved.scrollTop, Math.max(0, node.scrollHeight - node.clientHeight));
        setHeadlessPinnedToBottom(isScrollElementNearBottom(node));
      }
      setHeadlessHasNewBelow(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [headlessHistoryLoading, headlessHistoryReady, headlessProjectId, headlessThreadKey, panelMode]);
  // Content changed (new turn / streamed delta / refresh). Only follow to the
  // bottom when the user is pinned there; otherwise surface a "New messages"
  // affordance instead of yanking their scroll position.
  useEffect(() => {
    const node = headlessThreadRef.current;
    if (!node || !headlessHistoryReady || headlessHistoryLoading) return;
    if (headlessPinnedToBottom || isScrollElementNearBottom(node)) {
      scrollElementToBottom(node);
      setHeadlessHasNewBelow(false);
    } else {
      setHeadlessHasNewBelow(true);
    }
  }, [headlessHistoryLoading, headlessHistoryReady, headlessScrollSignature, headlessPinnedToBottom]);
  function showLatestHeadless() {
    setHeadlessPinnedToBottom(true);
    setHeadlessHasNewBelow(false);
    scrollElementToBottom(headlessThreadRef.current);
    rememberHeadlessThreadScroll(headlessProjectId, headlessThreadKey);
  }
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
  const headlessEmptyTitle = headlessCanChat ? "What should we work through?" : "Action token needed";
  const headlessEmptyBody = headlessCanChat
    ? "Ask about this project, shape a plan, or prepare a controlled action."
    : "Save a valid action token to send messages. Existing replies will still appear here.";
  const headlessPlaceholder = !headlessCanChat
    ? "Save action token to send..."
    : headlessPlanDiscussion
      ? `Ask about ${headlessPlanDiscussion.header || "this plan"}...`
    : taskRefOn && context.taskId
      ? `问关于 ${context.taskId} 的任何事(状态 / 合同 / 证据 / 时间线…)`
      : `Message ${activeBackendTitle}...`;
  const headlessWelcome = Boolean(
    headlessHistoryReady
    && !headlessHistoryLoading
    && !activeHeadlessThread?.turns.length
    && !pendingDurableInteractions.length
    && !pendingProposals.length
    && !pendingProposalNotice
    && !permissionEscalation,
  );

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
          <span className="agent-surface-title">
            <MessageCircle aria-hidden="true" size={16} strokeWidth={1.9} />
            Kanban Agent
          </span>
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
              aria-label={`Agent backend: ${activeBackendTitle}; ${actionState}`}
              className="agent-model-trigger"
              title={`${activeBackendTitle} · ${actionState}`}
              type="button"
              onClick={() => setBackendMenuOpen((open) => !open)}
            >
              <span className={`agent-model-dot ${actionStateClass}`} aria-hidden="true" />
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

        <div className={`headless-chat ${headlessWelcome ? "welcome" : ""}`}>
          <AgentThreadNavigation
            activeThreadId={headlessThreadKey}
            allowSplit={fullscreen}
            conversation={displayedHeadlessConversation}
            onActiveThreadChange={selectHeadlessThread}
            onSplitThreadChange={setHeadlessSplitThreadKey}
            splitThreadId={headlessSplitThreadKey}
          />
          <div
            className="headless-thread"
            ref={headlessThreadRef}
            onScroll={(event) => {
              const nearBottom = isScrollElementNearBottom(event.currentTarget);
              setHeadlessPinnedToBottom(nearBottom);
              if (nearBottom) setHeadlessHasNewBelow(false);
              rememberHeadlessThreadScroll(headlessProjectId, headlessThreadKey);
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
            <WebChatInteractionCards
              activeKey={activeInteractionKey}
              busyKey={webChatInteractionBusy}
              drafts={webChatInteractionDrafts}
              errors={webChatInteractionErrors}
              interactions={pendingDurableInteractions}
              onCompose={composeWebChatInteraction}
              onDraftChange={updateWebChatInteractionDraft}
              onRespond={answerDurableInteraction}
            />
            <AgentSessionTimeline
              actionBusyId={headlessProposalRunning}
              activeThreadId={headlessThreadKey}
              allowSplit={fullscreen && headlessConversation.threads.length > 1}
              allowPreviewSplit={fullscreen}
              compact={!fullscreen}
              compactRunHeader
              conversation={displayedHeadlessConversation}
              collapseCompletedRunDetails
              emptyBody={headlessEmptyBody}
              emptyTitle={headlessEmptyTitle}
              onActiveThreadChange={selectHeadlessThread}
              onAdoptResearchResult={(card, cardId) => void adoptResearchResult(card, cardId)}
              onAnswerQuestion={(card) => {
                setHeadlessMessage(card.body || "");
                headlessInputRef.current?.focus();
              }}
              onApproveProposal={(proposal, cardId) => void runHeadlessProposal(proposal, cardId)}
              onRejectProposal={(proposal, cardId) => void rejectHeadlessProposal(proposal, cardId)}
              onReviseProposal={(proposal) => reviseHeadlessProposal(proposal)}
              onSubmitPlan={canUseAction("kanban-plan-apply")
                ? (request, response, cardId) => void submitPlanResponse(request, response, cardId)
                : undefined}
              onChatAboutPlan={(request) => chatAboutPlan(request)}
              onCancelRun={(runId) => void cancelHeadlessRun(runId)}
              providerCapabilities={headlessCapabilities}
              onSplitThreadChange={setHeadlessSplitThreadKey}
              showRunDetails={false}
              showRunProvider={false}
              showThreadChips={false}
              splitThreadId={headlessSplitThreadKey}
            />
          </div>
          {headlessHasNewBelow ? (
            <button className="channel-scroll-latest" type="button" onClick={showLatestHeadless}>
              <ChevronDown size={15} />
              New messages
            </button>
          ) : null}
          {webChatDrawer === "conversation" ? (
            <WebChatConversationDrawer
              conversationId={headlessConversationId}
              projectId={headlessProjectId}
              threadId={headlessThreadKey}
              onClose={() => setWebChatDrawer("")}
              onFork={(threadId) => {
                setHeadlessThreads((current) => saveKanbanThreadRefs(
                  localStorage,
                  headlessProjectId,
                  threadId,
                  [...current, { id: threadId, title: "Fork", createdAt: new Date().toISOString() }],
                ));
                selectHeadlessThread(threadId);
                setWebChatDrawer("");
              }}
              onRetry={(content, messageId) => {
                setWebChatDrawer("");
                void submitHeadlessMessage(content, {
                  force: true,
                  requestPatch: { retry_message_id: messageId },
                });
              }}
            />
          ) : null}
          {webChatDrawer === "artifacts" ? (
            <WebChatArtifactDrawer
              artifacts={webChatArtifacts}
              projectId={headlessProjectId}
              taskId={context.taskId}
              onChanged={refreshWebChatResources}
              onReference={composerDraft.uploading ? undefined : (item) => {
                if (!webChatAttachments.some((existing) => existing.version_id === item.version_id)) {
                  setWebChatAttachments([...webChatAttachments, item]);
                }
                setWebChatDrawer("");
                headlessInputRef.current?.focus();
              }}
              onClose={() => setWebChatDrawer("")}
            />
          ) : null}
          {webChatDrawer === "activity" ? (
            <WebChatActivityDrawer
              activities={webChatActivities}
              projectId={headlessProjectId}
              onClose={() => setWebChatDrawer("")}
              onOpenThread={(activity) => {
                const threadId = activity.thread_id || "";
                if (threadId) {
                  setHeadlessThreads((current) => saveKanbanThreadRefs(
                    localStorage,
                    headlessProjectId,
                    threadId,
                    [...current, { id: threadId, title: "Child activity", createdAt: new Date().toISOString() }],
                  ));
                }
                if (activity.channel_id && onOpenChannelThread) {
                  onOpenChannelThread(activity.channel_id, threadId || "main");
                } else if (activity.task_id && onOpenTask) {
                  onOpenTask(activity.task_id);
                } else if (threadId) {
                  selectHeadlessThread(threadId);
                }
                setWebChatDrawer("");
              }}
              onUpdated={refreshWebChatResources}
            />
          ) : null}
          {webChatDrawer === "profile" ? (
            <WebChatProfileDrawer
              currentPermission={permissionProfile}
              currentProvider={operatorBackend}
              projectId={headlessProjectId}
              onApplied={refreshWebChatResources}
              onClose={() => setWebChatDrawer("")}
            />
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
                const presentation = actionPresentation(item.action);
                return (
                  <div className="headless-pending-entry" key={item.proposal_event_id}>
                    <div className="headless-pending-item">
                      <div className="headless-pending-main">
                        <strong>{item.title || presentation.title}</strong>
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
                        {pendingProposalBusy === item.proposal_event_id
                          ? presentation.busyLabel
                          : presentation.confirmLabel}
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
          {pendingProposalsError ? (
            <div className="headless-composer-alert" role="alert">
              Pending proposals unavailable: {pendingProposalsError}. Displayed results may be stale.
              <button type="button" onClick={() => setPendingProposalsRefresh((value) => value + 1)}>Retry</button>
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
            {headlessPlanDiscussion ? (
              <div className="headless-plan-discussion">
                <MessageCircle aria-hidden="true" size={15} />
                <span>
                  <small>Discussing plan</small>
                  <strong>{headlessPlanDiscussion.header || "Plan"}</strong>
                </span>
                <button
                  aria-label="Stop discussing plan"
                  className="headless-plan-discussion-close"
                  title="Remove plan context"
                  type="button"
                  onClick={() => setHeadlessPlanDiscussion(null)}
                >
                  <X aria-hidden="true" size={14} />
                </button>
              </div>
            ) : null}
            <WebChatPendingControls
              busyId={webChatQueueBusy}
              composerHasDraft={Boolean(headlessMessage.trim())}
              queue={scopedHeadlessQueue}
              receipts={webChatRuntime?.control_receipts ?? []}
              turns={webChatRuntime?.turns ?? []}
              onCancel={(item) => void removeQueuedMessage(item)}
              onRestore={(item) => void restoreQueuedMessage(item)}
            />
            {webChatAnswering ? (
              <WebChatInteractionPanel
                busy={webChatInteractionBusy === activeInteractionKey}
                draft={webChatInteractionDrafts[activeInteractionKey] ?? emptyInteractionDraft()}
                error={webChatInteractionErrors[activeInteractionKey] ?? ""}
                interaction={webChatAnswering}
                mode="composer"
                onCollapse={() => collapseWebChatInteraction(webChatAnswering)}
                onDraftChange={(draft) => updateWebChatInteractionDraft(webChatAnswering, draft)}
                onRespond={(response) => void answerDurableInteraction(webChatAnswering, response)}
              />
            ) : null}
            {permissionEscalation ? (
              <div className="headless-permission-request" role="alert">
                <ShieldAlert aria-hidden="true" size={18} />
                <span>
                  <strong>Full access required</strong>
                  <small>
                    Workspace isolation is unavailable on this host. Retry only this turn
                    with full shell and Git access.
                  </small>
                </span>
                <div className="headless-permission-actions">
                  <button
                    className="agent-inline-button"
                    type="button"
                    onClick={() => setDismissedPermissionEscalations((current) => [
                      ...current,
                      permissionEscalation.failureEventId,
                    ])}
                  >
                    Cancel
                  </button>
                  <button
                    className="agent-inline-button primary"
                    type="button"
                    onClick={() => void runPermissionEscalation()}
                  >
                    Run once with full access
                  </button>
                </div>
              </div>
            ) : operatorError ? (
              <div className="headless-composer-alert" role="alert">{operatorError}</div>
            ) : !headlessCanChat ? (
              // Surfaced from the very moment the panel opens (not only after a
              // first failed send). Without this, users typed + pressed Enter,
              // the token gate (submitHeadlessMessage:7945) silently set
              // operatorError but the input had no visible block, and the
              // experience read as "first message hangs, refresh fixes it".
              <div
                className={`headless-composer-alert ${snapshot ? "blocked" : "connecting"}`}
                role={snapshot ? "alert" : "status"}
              >
                {snapshot
                  ? `${activeBackendTitle} is ${actionState}. Save a valid action token to send messages.`
                  // Gate not resolved yet (first snapshot in flight): saying
                  // "read only, save a token" here was a lie that flashed on
                  // every panel open. Messages typed now park and auto-send.
                  : "Connecting — messages send automatically once ready."}
              </div>
            ) : null}
            {!webChatAnswering ? (
              <>
                <textarea
                  ref={headlessInputRef}
                  className="headless-input"
                  placeholder={headlessPlaceholder}
                  aria-invalid={!headlessCanChat || undefined}
                  disabled={headlessSubmitting || Boolean(permissionEscalation)}
                  value={headlessMessage}
                  onChange={(event) => setHeadlessMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter"
                      && !event.shiftKey
                      && !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      if (composerDraft.uploading) {
                        setOperatorError("Wait for attachments to finish uploading before sending.");
                        return;
                      }
                      if (canNativeSteer) {
                        void applyDurableControl("steer", event.currentTarget.value);
                      } else {
                        void submitHeadlessMessage(event.currentTarget.value);
                      }
                    }
                  }}
                />
                <div className="headless-composer-footer">
                  <div className="headless-composer-tools">
                    <WebChatAttachments
                      key={`${headlessProjectId}:${headlessThreadKey}`}
                      artifacts={webChatAttachments}
                      conversationId={headlessConversationId}
                      disabled={headlessSubmitting}
                      projectId={headlessProjectId}
                      threadId={headlessThreadKey}
                      onChange={setWebChatAttachments}
                      onError={(error) => {
                        if (webChatResourceIdentityRef.current === `${headlessProjectId}:${headlessConversationId}:${headlessThreadKey}`) {
                          setOperatorError(error);
                        }
                      }}
                      onUploadingChange={composerDraft.setUploading}
                      onReferenceTask={context.taskId ? () => setTaskRefOn(true) : undefined}
                      onReferenceArtifact={() => setWebChatDrawer("artifacts")}
                      activeDrawer={webChatDrawer}
                      activityCount={webChatActivities.length}
                      artifactCount={webChatArtifacts.length}
                      onDrawerChange={setWebChatDrawer}
                      toolCatalog={webChatRuntime?.tool_catalog}
                      plans={referenceablePlans.map((plan) => ({
                        id: plan.requestEventId, label: plan.header || plan.question || plan.requestId,
                        onSelect: () => chatAboutPlan(plan),
                      }))}
                    />
                  </div>
                  <KanbanModelMenu selection={modelSelection} disabled={headlessSubmitting}
                    catalogSupported={operatorBackend === "codex-headless"} unlocked={canUseAction("chat-orchestrator")}
                    onReauthorize={tokenPresent ? () => onSaveToken("") : undefined}
                    onSwitchToCodex={headlessBackendOptions.some((item) => item.id === "codex-headless" && item.available !== false)
                      ? () => selectOperatorBackend("codex-headless") : undefined} />
                  {activeDurableTurn ? (
                    <DuringRunComposerActions
                      waitingForInput={activeDurableTurn.state === "waiting_input"}
                      canCancel={canCancelDurableTurn}
                      canInterrupt={canInterruptDurableTurn}
                      canSteer={canNativeSteer}
                      disabled={headlessSubmitting || Boolean(permissionEscalation)}
                      sendingDisabled={composerDraft.uploading}
                      hasMessage={Boolean(headlessMessage.trim())}
                      primaryAction={canNativeSteer ? "steer" : "queue"}
                      onCancel={() => void cancelHeadlessRun(activeDurableTurn.turn_id)}
                      onQueue={() => void submitHeadlessMessage()}
                      onSteer={() => void applyDurableControl("steer")}
                      onSteerSooner={() => void applyDurableControl("interrupt", headlessMessage)}
                    />
                  ) : (
                    <ComposerSubmitButton
                      className="headless-send-button"
                      disabled={!headlessMessage.trim() || composerDraft.uploading || Boolean(permissionEscalation)}
                      iconSize={21}
                      status={deriveComposerStatus(activeHeadlessRun?.status ?? (hasDurableTurns ? "idle" : activeHeadlessThread?.status), headlessSubmitting)}
                      onStop={activeHeadlessRun ? () => void cancelHeadlessRun(activeHeadlessRun.id) : undefined}
                      title={canUseAction("chat-orchestrator") ? "Send" : `${actionState}; save action token first`}
                      onClick={() => void submitHeadlessMessage()}
                    />
                  )}
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
