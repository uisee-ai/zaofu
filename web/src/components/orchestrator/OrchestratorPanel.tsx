// OrchestratorPanel + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { OPERATOR_BACKENDS } from "../../app/sharedTypes";
import type { ActionResponse, RecentEvent, Snapshot } from "../../api/types";
import { getAgentSessionHistory, getKanbanPendingProposals } from "../../api/client";
import type { PendingKanbanProposal } from "../../api/client";
import { AgentSessionTimeline } from "../../components/agent-session/AgentSessionTimeline";
import { ComposerSubmitButton } from "../../components/agent-session/ComposerSubmitButton";
import { MarkdownText } from "../../components/agent-session/MarkdownText";
import { SelfIssueIntakeWizard } from "./SelfIssueIntakeWizard";
import { SelfIssueReadPoller } from "./selfIssuePoller";
import { actionPresentation } from "../../components/agent-session/actionPresentation";
import { deriveComposerStatus } from "../../components/agent-session/workState";
import { useWorkingTitle } from "../../components/agent-session/useWorkingTitle";
import { buildKanbanConversation } from "../../components/agent-session/projection";
import { proposalRunNotice } from "../../app/triageProposals";
import { planDiscussionBackend } from "../../app/kanbanAgentInteractionPolicy";
import { projectionNeedsFresh } from "../../app/pageLoadPolicy";
import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentHistoryParams,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
} from "./kanbanAgentHistoryPolicy";
import {
  SELF_ISSUE_RUNTIME_STOPPED_WARNING,
  selfIssueCardLayout,
  selfIssueCardLayoutStorageKey,
  selfIssueCompactText,
  selfIssueCreatedAfterCutoff,
  selfIssueDismissCutoffStorageKey,
  selfIssueEvidenceControls,
  selfIssueEvidenceBlocksPreview,
  selfIssueGithubTransactionStorageKey,
  selfIssueIntakeMountState,
  selfIssueLocalAttachmentUrl,
  selfIssueOAuthSession,
  selfIssueOAuthContinuation,
  selfIssuePreviewIsReusable,
  selfIssueProviderLabel,
  selfIssuePublicationLocked,
  selfIssueRefreshErrorIsTransient,
  selfIssueSelectDestination,
  selfIssuePublishedUrls,
  selfIssueSlashAction,
  selfIssueTargetLocked,
} from "./selfIssue";
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
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "./kanbanSessionEvents";
import { ChevronDown, LoaderCircle, Maximize2, MessageCircle, Minimize2, Minus, Play, Plus, RotateCcw, Send, ShieldAlert, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
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

async function fileAsBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return window.btoa(binary);
}

function attachmentContentType(filename: string): string {
  const suffix = filename.toLowerCase().split(".").pop() ?? "";
  return {
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp",
    gif: "image/gif", mp4: "video/mp4", webm: "video/webm", txt: "text/plain",
    log: "text/plain", json: "application/json",
  }[suffix] ?? "application/octet-stream";
}

function selfIssueAssessmentText(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length
      ? value.map((item) => selfIssueAssessmentText(item)).filter(Boolean).join("\n")
      : "Not provided.";
  }
  const item = recordValue(value);
  if (item) {
    const lines = Object.entries(item).map(([key, nested]) => (
      `${key.replaceAll("_", " ")}: ${selfIssueAssessmentText(nested)}`
    ));
    return lines.length ? lines.join("\n") : "Not provided.";
  }
  return textValue(value).trim() || "Not provided.";
}


interface HeadlessQueueItem {
  id: string;
  threadId: string;
  message: string;
  createdAt: string;
  requestPatch?: Record<string, unknown>;
}


interface HeadlessPendingMessage extends HeadlessQueueItem {
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

function persistSelfIssueCardLayout(
  projectId: string,
  draftId: string,
  expanded: boolean,
): void {
  if (typeof window === "undefined" || !projectId || !draftId) return;
  window.localStorage.setItem(
    selfIssueCardLayoutStorageKey(projectId, draftId),
    expanded ? "expanded" : "minimized",
  );
}

function restoredSelfIssueCardExpanded(projectId: string, draftId: string): boolean {
  if (typeof window === "undefined" || !projectId || !draftId) return false;
  return selfIssueCardLayout(
    window.localStorage.getItem(selfIssueCardLayoutStorageKey(projectId, draftId)),
  ) === "expanded";
}

function clearSelfIssueCardLayout(projectId: string, draftId: string): void {
  if (typeof window === "undefined" || !projectId || !draftId) return;
  window.localStorage.removeItem(selfIssueCardLayoutStorageKey(projectId, draftId));
}

function selfIssueDismissCutoff(projectId: string): string | null {
  if (typeof window === "undefined" || !projectId) return null;
  return window.localStorage.getItem(selfIssueDismissCutoffStorageKey(projectId));
}

function markSelfIssueDismissed(projectId: string): void {
  if (typeof window === "undefined" || !projectId) return;
  window.localStorage.setItem(
    selfIssueDismissCutoffStorageKey(projectId),
    new Date().toISOString(),
  );
}

function clearSelfIssueDismissCutoff(projectId: string): void {
  if (typeof window === "undefined" || !projectId) return;
  window.localStorage.removeItem(selfIssueDismissCutoffStorageKey(projectId));
}

function slashAction(message: string): { action: string; payload: Record<string, unknown> } | null {
  const trimmed = message.trim();
  const selfIssue = selfIssueSlashAction(trimmed);
  if (selfIssue) return selfIssue;
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
  const [dismissedPermissionEscalations, setDismissedPermissionEscalations] = useState<string[]>([]);
  const [headlessMessage, setHeadlessMessage] = useState("");
  const [selfIssueCard, setSelfIssueCard] = useState<Record<string, unknown> | null>(null);
  const [selfIssueIntake, setSelfIssueIntake] = useState<Record<string, unknown> | null>(null);
  const [selfIssueIntakeExpanded, setSelfIssueIntakeExpanded] = useState(true);
  const [selfIssueIntakeForeground, setSelfIssueIntakeForeground] = useState(true);
  const [selfIssueIntakeBusy, setSelfIssueIntakeBusy] = useState(false);
  const [selfIssueWorkspaceHost, setSelfIssueWorkspaceHost] = useState<HTMLElement | null>(null);
  const [selfIssuePreviewBusy, setSelfIssuePreviewBusy] = useState(false);
  const [selfIssueRuntimeWarning, setSelfIssueRuntimeWarning] = useState("");
  const [selfIssueCardExpanded, setSelfIssueCardExpanded] = useState(false);
  const [selfIssueCardResetting, setSelfIssueCardResetting] = useState(false);
  const [selfIssueCardClosing, setSelfIssueCardClosing] = useState(false);
  const [selfIssueEvidenceBusyAction, setSelfIssueEvidenceBusyAction] = useState("");
  const [selfIssueRuntimeActionNotice, setSelfIssueRuntimeActionNotice] = useState("");
  const [selfIssueSuccessNotice, setSelfIssueSuccessNotice] = useState("");
  const [selfIssueSavePreviewBusy, setSelfIssueSavePreviewBusy] = useState(false);
  const [selfIssueAttachmentBusy, setSelfIssueAttachmentBusy] = useState(false);
  const [selfIssueConfirmationBusy, setSelfIssueConfirmationBusy] = useState(false);
  const [selfIssuePublishBusy, setSelfIssuePublishBusy] = useState(false);
  const selfIssueActionRef = useRef(onAction);
  const selfIssueEvidencePollGenerationRef = useRef(0);
  const selfIssueEvidenceBusy = Boolean(selfIssueEvidenceBusyAction);
  const [selfIssueDraftTab, setSelfIssueDraftTab] = useState<"draft" | "preview">("draft");
  const [headlessPlanDiscussion, setHeadlessPlanDiscussion] = useState<AgentSessionPlanRequest | null>(null);
  const [headlessSubmitting, setHeadlessSubmitting] = useState(false);
  const [headlessProposalRunning, setHeadlessProposalRunning] = useState("");
  const [pendingProposals, setPendingProposals] = useState<PendingKanbanProposal[]>([]);
  const [pendingProposalsRefresh, setPendingProposalsRefresh] = useState(0);
  const [pendingProposalBusy, setPendingProposalBusy] = useState("");
  const [pendingProposalExpanded, setPendingProposalExpanded] = useState<Record<string, boolean>>({});
  const [pendingProposalErrors, setPendingProposalErrors] = useState<Record<string, string>>({});
  const [pendingProposalNotice, setPendingProposalNotice] = useState("");

  useEffect(() => {
    setSelfIssueWorkspaceHost(document.getElementById("self-issue-workspace-host"));
  }, []);

  useEffect(() => {
    if (snapshot?.runtime.live) setSelfIssueRuntimeWarning("");
  }, [snapshot?.runtime.live]);

  useEffect(() => {
    const response = recordValue(actionResult);
    const requestedAction = textValue(response?.requested_action ?? response?.action);
    if (requestedAction !== "self-issue-oauth-callback") return;
    const result = recordValue(response?.result);
    const nestedResult = recordValue(result?.result);
    const draft = recordValue(response?.draft)
      ?? recordValue(result?.draft)
      ?? recordValue(nestedResult?.draft);
    if (draft) {
      setOperatorError(actionFailed(actionResult) ? actionFailureReason(actionResult) : "");
      const issue = recordValue(response?.issue)
        ?? recordValue(result?.issue)
        ?? recordValue(nestedResult?.issue);
      const callbackStatus = textValue(response?.status)
        || textValue(result?.status)
        || textValue(nestedResult?.status)
        || "connected";
      const connectedDraft: Record<string, unknown> = {
        ...draft,
        ...(nestedResult ?? {}),
        ...(result ?? {}),
        ...response,
        status: callbackStatus,
        ...(issue ? { issue } : {}),
      };
      setSelfIssueCard(connectedDraft);
      // OAuth completion is the user's authorization decision. When the
      // exact preview was already confirmed, continue the pending publication
      // immediately instead of requiring a second manual Publish click.
      const callbackBatchId = textValue(connectedDraft.batch_id);
      const callbackConfirmationId = textValue(connectedDraft.confirmation_id);
      if (
        callbackBatchId
        && callbackConfirmationId
        && !["published", "partially_published", "publishing"].includes(callbackStatus)
      ) {
        setSelfIssuePublishBusy(true);
        setSelfIssueCard((current) => ({ ...(current ?? connectedDraft), status: "publishing" }));
        void Promise.resolve(selfIssueActionRef.current("self-issue-publish", {
          batch_id: callbackBatchId,
          confirmation_id: callbackConfirmationId,
        })).then((publishResult) => {
          const published = recordValue(publishResult);
          if (actionFailed(publishResult)) {
            setOperatorError(actionFailureReason(publishResult));
            return;
          }
          if (published) {
            setSelfIssueCard((current) => ({
              ...(current ?? connectedDraft),
              ...(recordValue(published.draft) ?? {}),
              ...published,
            }));
            if (["published", "partially_published"].includes(textValue(published.status))) {
              setSelfIssueSuccessNotice("Published successfully.");
            }
          }
        }).catch((error: unknown) => {
          setOperatorError(error instanceof Error ? error.message : String(error));
        }).finally(() => setSelfIssuePublishBusy(false));
      }
      if (["published", "partially_published"].includes(callbackStatus)) {
        setSelfIssueSuccessNotice("Published successfully.");
      }
      setSelfIssueCardExpanded(true);
      persistSelfIssueCardLayout(
        activeProjectId,
        textValue(connectedDraft.draft_id),
        true,
      );
      // The OAuth callback can complete in a popup while the opener still
      // holds the pre-authorization Draft projection. Read the canonical
      // Draft once more so a completed publication cannot look unauthorized
      // until the user manually toggles tabs or refreshes the page.
      const draftId = textValue(connectedDraft.draft_id);
      if (draftId) {
        void Promise.resolve(selfIssueActionRef.current("self-issue-get", {
          draft_id: draftId,
        })).then((latest) => {
          const latestDraft = recordValue(recordValue(latest)?.draft);
          if (latestDraft) {
            setSelfIssueCard((current) => ({
              ...(current ?? {}),
              ...latestDraft,
            }));
          }
        }).catch(() => undefined);
      }
    } else if (actionFailed(actionResult)) {
      setOperatorError(actionFailureReason(actionResult));
    }
  }, [actionResult, activeProjectId]);
  const selfIssueDraftId = textValue(selfIssueCard?.draft_id);
  const selfIssueIntakeId = textValue(selfIssueIntake?.intake_id);
  useEffect(() => {
    if (selfIssueIntakeId) {
      setSelfIssueIntakeExpanded(true);
      setSelfIssueIntakeForeground(true);
    }
  }, [selfIssueIntakeId]);
  const selfIssueEvidenceStatus = textValue(selfIssueCard?.evidence_status);
  const selfIssueRuntimeStatus = textValue(selfIssueCard?.runtime_status) || "unknown";
  const selfIssueRuntimeLive = selfIssueRuntimeStatus === "live"
    || selfIssueRuntimeActionNotice.startsWith("Runtime started");
  const selfIssueAssessmentStatus = textValue(selfIssueCard?.assessment_status) || "not_started";
  const evidenceControls = selfIssueEvidenceControls(selfIssueEvidenceStatus);
  useEffect(() => {
    if (!selfIssueCardExpanded || !selfIssueDraftId) return;
    const minimizeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelfIssueCardExpanded(false);
      persistSelfIssueCardLayout(activeProjectId, selfIssueDraftId, false);
    };
    window.addEventListener("keydown", minimizeOnEscape);
    return () => window.removeEventListener("keydown", minimizeOnEscape);
  }, [activeProjectId, selfIssueCardExpanded, selfIssueDraftId]);
  useEffect(() => {
    if (
      !selfIssueDraftId
      || ![
        "collecting_static", "collecting_live", "waiting_for_runtime",
      ].includes(selfIssueEvidenceStatus)
    ) return;
    let cancelled = false;
    let requestRunning = false;
    const pollGeneration = selfIssueEvidencePollGenerationRef.current;
    const refreshEvidence = () => {
      if (requestRunning) return;
      requestRunning = true;
      void Promise.resolve(selfIssueActionRef.current(
        "self-issue-get", { draft_id: selfIssueDraftId },
      ))
        .then((result) => {
          if (cancelled || pollGeneration !== selfIssueEvidencePollGenerationRef.current) return;
          const record = recordValue(result);
          const draft = recordValue(record?.draft);
          if (actionFailed(result)) setOperatorError(actionFailureReason(result));
          else if (draft) {
            setOperatorError("");
            setSelfIssueCard(draft);
          }
        })
        .catch((error: unknown) => {
          if (!cancelled && !selfIssueRefreshErrorIsTransient(error)) {
            setOperatorError(error instanceof Error ? error.message : String(error));
          }
        })
        .finally(() => {
          requestRunning = false;
        });
    };
    refreshEvidence();
    const timer = window.setInterval(
      refreshEvidence,
      selfIssueEvidenceStatus === "waiting_for_runtime" ? 5000 : 1200,
    );
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selfIssueAssessmentStatus, selfIssueEvidenceStatus, selfIssueDraftId]);
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
  const selfIssueRestoreProjectRef = useRef("");
  const selfIssueRestoreGenerationRef = useRef(0);
  // Message typed before the first snapshot resolved the action gate — sent
  // automatically once the gate is known (2026-07-16 first-message race).
  const [pendingGateMessage, setPendingGateMessage] = useState<string | null>(null);
  const headlessThreadRef = useRef<HTMLDivElement | null>(null);
  const [headlessPinnedToBottom, setHeadlessPinnedToBottom] = useState(true);
  const [headlessHasNewBelow, setHeadlessHasNewBelow] = useState(false);

  // The Issues tab intentionally omits the expensive snapshot. A valid web
  // action token is already sufficient for token-gated mutations, so keep the
  // proposal controls usable during the brief snapshot/bootstrap race.
  const storedActionToken = typeof window !== "undefined"
    && Boolean(window.localStorage.getItem("zf.webActionToken"));
  const effectiveTokenPresent = tokenPresent || storedActionToken;
  const allowedActions = snapshot?.runtime.actions?.allowed ?? (
    effectiveTokenPresent ? ["kanban-plan-apply", "workflow-start", "task-workflow-start"] : []
  );
  const webSession = snapshot?.runtime.web_session;
  const agentSurface = snapshot?.runtime.agent_surface;
  const permissionProfile = textValue(
    agentSurface?.permission_profile,
  ).trim() || "dangerous_full";
  // A valid Web action token is an explicit mutation capability even when
  // the runtime snapshot was produced in read-only session mode.  Keep this
  // gate aligned with App.runtimeActionState so the panel can execute the
  // same token-gated actions that the rest of the shell exposes.
  const mutationEnabled = Boolean(snapshot?.runtime.actions?.mutation_enabled)
    || Boolean(webSession && webSession.mode !== "read_only")
    || effectiveTokenPresent;
  const headlessProjectId = kanbanAgentProjectId(activeProjectId, snapshot?.project?.project_id || "");
  const headlessConversationId = kanbanAgentConversationId(headlessProjectId);
  const sessionActionReady = Boolean(webSession?.actions_enabled);
  const tokenFallbackAvailable = webSession?.mode === "token_required"
    || Boolean(webSession?.token_fallback_enabled);
  const passcodeRequired = webSession?.mode === "remote_passcode" && !sessionActionReady;
  const showTokenRow = mutationEnabled && !sessionActionReady && tokenFallbackAvailable && !tokenPresent;
  const tokenRequired = showTokenRow && !tokenPresent;
  const actionReady = sessionActionReady || (mutationEnabled && effectiveTokenPresent);
  const actionState = actionReady
    ? "active"
    : mutationEnabled
      ? (passcodeRequired ? "passcode needed" : tokenRequired ? "token needed" : "locked")
      : "read only";
  const canUseAction = (action: string) => actionReady && allowedActions.includes(action);
  const canRestoreSelfIssue = actionReady && allowedActions.includes("self-issue-get");
  useEffect(() => {
    selfIssueActionRef.current = onAction;
  }, [onAction]);
  useEffect(() => {
    if (!visible || !canRestoreSelfIssue || !headlessProjectId) return;
    if (selfIssueRestoreProjectRef.current === headlessProjectId) return;
    selfIssueRestoreProjectRef.current = headlessProjectId;
    const generation = selfIssueRestoreGenerationRef.current + 1;
    selfIssueRestoreGenerationRef.current = generation;
    setSelfIssueCard(null);
    setSelfIssueIntake(null);
    setSelfIssueCardExpanded(false);
    void Promise.resolve(selfIssueActionRef.current("self-issue-get", {}))
      .then((result) => {
        if (selfIssueRestoreGenerationRef.current !== generation) return;
        const record = recordValue(result);
        const serverDraft = recordValue(record?.draft);
        const storedGithubTransaction = serverDraft
          ? window.localStorage.getItem(selfIssueGithubTransactionStorageKey(
            activeProjectId,
            textValue(serverDraft.draft_id),
          ))
          : null;
        const draft = serverDraft && storedGithubTransaction
          && !textValue(serverDraft.transaction_id)
          && ["authorization_required", "authorization_pending", "slow_down"].includes(textValue(serverDraft.status))
          ? { ...serverDraft, transaction_id: storedGithubTransaction }
          : serverDraft;
        const intake = recordValue(record?.intake);
        const dismissCutoff = selfIssueDismissCutoff(activeProjectId);
        const intakeIsNewer = Boolean(intake) && (
          !draft
          || Date.parse(textValue(intake?.updated_at)) >= Date.parse(textValue(draft?.updated_at))
        );
        if (actionFailed(result)) {
          selfIssueRestoreProjectRef.current = "";
          setOperatorError(actionFailureReason(result));
        } else if (intake && intakeIsNewer && selfIssueCreatedAfterCutoff(intake, dismissCutoff)) {
          setOperatorError("");
          setSelfIssueIntake(intake);
        } else if (draft && selfIssueCreatedAfterCutoff(draft, dismissCutoff)) {
          setOperatorError("");
          setSelfIssueCard(draft);
          setSelfIssueCardExpanded(restoredSelfIssueCardExpanded(
            activeProjectId,
            textValue(draft.draft_id),
          ));
        }
      })
      .catch((error: unknown) => {
        if (selfIssueRestoreGenerationRef.current !== generation) return;
        selfIssueRestoreProjectRef.current = "";
        setOperatorError(error instanceof Error ? error.message : String(error));
      });
  }, [activeProjectId, canRestoreSelfIssue, headlessProjectId, visible]);
  useEffect(() => {
    if (!visible || !canRestoreSelfIssue || !headlessProjectId) return;
    const poller = new SelfIssueReadPoller({
      intervalMs: 5000,
      request: () => Promise.resolve(selfIssueActionRef.current("self-issue-get", {})),
      isEnabled: () => !document.hidden,
      onResult: (result) => {
        if (actionFailed(result)) return;
        const record = recordValue(result);
        const intake = recordValue(record?.intake);
        if (
          !intake
          || textValue(intake.origin) !== "system_detected"
          || !selfIssueCreatedAfterCutoff(intake, selfIssueDismissCutoff(activeProjectId))
        ) return;
        setSelfIssueIntake((current) => (
          textValue(current?.intake_id) === textValue(intake.intake_id) ? current : intake
        ));
      },
    });
    const handleVisibilityChange = () => {
      if (!document.hidden) poller.wake();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    poller.start();
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      poller.stop();
    };
  }, [activeProjectId, canRestoreSelfIssue, headlessProjectId, visible]);
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
    const historyRequest = kanbanAgentHistoryParams({
      threadId: headlessThreadKey,
      conversationId: headlessConversationId,
      backend: operatorBackend,
      limit: 160,
    });
    void getAgentSessionHistory(headlessProjectId, historyRequest).then((page) => {
      if (cancelled) return;
      setHeadlessHistoryEvents(page.items ?? []);
      setHeadlessHistoryBeforeSeq(page.next_before_seq ?? null);
      setHeadlessHistoryHasMore(Boolean(page.has_more));
      if (projectionNeedsFresh(page)) {
        void getAgentSessionHistory(headlessProjectId, {
          ...historyRequest,
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
    setHeadlessPlanDiscussion(null);
    setOperatorError("");
    headlessInputRef.current?.focus();
  }

  function selectHeadlessThread(threadId: string) {
    setHeadlessThreadKey(threadId);
    setHeadlessPlanDiscussion(null);
    if (headlessSplitThreadKey === threadId) setHeadlessSplitThreadKey("");
    if (typeof window !== "undefined") {
      window.localStorage.setItem(kanbanThreadStorageKey(activeProjectId), threadId);
    }
    headlessInputRef.current?.focus();
  }

  function queueHeadlessMessage(
    message: string,
    requestPatch?: Record<string, unknown>,
  ) {
    const item: HeadlessQueueItem = {
      id: `queue-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
      threadId: headlessThreadKey,
      message,
      createdAt: new Date().toISOString(),
      requestPatch,
    };
    setHeadlessQueue((current) => [...current, item]);
    setHeadlessMessage("");
  }

  async function submitHeadlessMessage(messageOverride?: string, options: SubmitHeadlessOptions = {}) {
    const message = (messageOverride ?? headlessMessage).trim();
    const turnPermissionProfile = options.permissionProfileOverride ?? permissionProfile;
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
    const requestPatch = {
      ...discussionRequestPatch,
      ...explicitRequestPatch,
    };
    const isPlanDiscussion = "plan_discussion" in requestPatch;
    const boundDiscussionBackend = isPlanDiscussion
      ? kanbanChatBackend(
        asOperatorBackend(planDiscussionBackend(headlessPlanDiscussion?.backend, operatorBackend))
          ?? operatorBackend,
      )
      : null;
    const targetBackend = options.backendOverride ?? boundDiscussionBackend ?? operatorBackend;
    if (!message || !isChatBackend(targetBackend) || headlessSubmitting) return;
    if (activeThreadBusy && !options.force && !isPlanDiscussion) {
      queueHeadlessMessage(message, requestPatch);
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
      && !options.backendOverride
      && operatorBackend === "claude-headless"
      && !!configuredChatBackend
      && configuredChatBackend !== "claude-headless"
    );
    if (!agentSurface || stillOnUncorrectedDefault) {
      setOperatorError("Agent backend list is still loading; try again in a moment.");
      return;
    }
    const directAction = slashAction(message);
    if (directAction?.action === "self-issue-capture" && !snapshot.runtime.live) {
      setSelfIssueRuntimeWarning(SELF_ISSUE_RUNTIME_STOPPED_WARNING);
    }
    setHeadlessSubmitting(true);
    setHeadlessMessage("");
    let pendingTurnId = "";
    try {
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
        if (directAction.action.startsWith("self-issue-")) {
          const actionRecord = recordValue(result);
          const draft = recordValue(actionRecord?.draft);
          const intake = recordValue(actionRecord?.intake);
          const preview = recordValue(actionRecord?.preview);
          if (directAction.action === "self-issue-capture") {
            clearSelfIssueDismissCutoff(activeProjectId);
          }
          if (intake) {
            setSelfIssueSuccessNotice("");
            setSelfIssueIntake(intake);
            setSelfIssueCard(null);
            setSelfIssueCardExpanded(true);
            setOperatorError("");
            return;
          }
          const nextCard = draft ?? (actionRecord ? { ...actionRecord, preview } : null);
          if (nextCard && textValue(nextCard.status) !== "published") setSelfIssueSuccessNotice("");
          setSelfIssueCard(nextCard);
          if (nextCard) {
            setSelfIssueCardExpanded(true);
            persistSelfIssueCardLayout(
              activeProjectId,
              textValue(nextCard.draft_id),
              true,
            );
          }
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
      if (isPlanDiscussion) {
        setHeadlessPlanDiscussion(null);
      }
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
        proposal_event_id: proposal.proposalEventId || undefined,
      };
      if (!("task_id" in payload) && proposal.action !== "create-task" && context.taskId) {
        payload.task_id = context.taskId;
      }
      const result = await Promise.resolve(onAction(proposal.action, payload));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        return;
      }
      setOperatorError("");
    } finally {
      setHeadlessProposalRunning("");
      setPendingProposalsRefresh((value) => value + 1);
    }
  }

  async function rejectHeadlessProposal(proposal: AgentSessionActionProposal, key: string) {
    if (!proposal.proposalEventId || !canUseAction("kanban-proposal-dismiss")) return;
    setHeadlessProposalRunning(key);
    try {
      const result = await Promise.resolve(onAction("kanban-proposal-dismiss", {
        project_id: headlessProjectId,
        proposal_event_id: proposal.proposalEventId,
        reason: "rejected from Kanban Agent Approve interaction",
      }));
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        return;
      }
      setOperatorError("");
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
    if (
      headlessPlanDiscussion?.requestEventId === request.requestEventId
    ) {
      setHeadlessPlanDiscussion(null);
    }
    setHeadlessProposalRunning(key);
    try {
      const selectedOption = request.options.find(
        (option) => option.id === response.optionId,
      );
      const submitAction = (
        selectedOption?.submitAction
        || request.submitAction
        || ""
      );
      const submitMode = (
        selectedOption?.submitMode
        || request.submitMode
        || (submitAction ? "apply" : "continue")
      );
      if (submitAction && submitMode !== "continue") {
        if (!canUseAction("kanban-plan-apply")) {
          setOperatorError(`action kanban-plan-apply is ${actionState}`);
          return;
        }
        const result = await Promise.resolve(onAction("kanban-plan-apply", {
          project_id: headlessProjectId,
          conversation_id: headlessConversationId,
          thread_id: headlessThreadKey,
          task_id: context.taskId || undefined,
          plan_response: {
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
          },
        }));
        if (actionFailed(result)) {
          setOperatorError(actionFailureReason(result));
          return;
        }
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
            plan_response: {
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
            },
          },
        },
      );
    } finally {
      setHeadlessProposalRunning("");
      setPendingProposalsRefresh((value) => value + 1);
    }
  }

  function chatAboutPlan(request: AgentSessionPlanRequest) {
    setHeadlessPlanDiscussion(request);
    headlessInputRef.current?.focus();
  }

  async function runPendingProposal(item: PendingKanbanProposal) {
    // Pending proposals are already validated by the kernel projection.  A
    // slow light-snapshot response may leave `allowedActions` empty for a
    // moment even though the Web session/token is ready; do not silently
    // discard the operator's click in that window.
    const pendingActionReady = canUseAction(item.action)
      || sessionActionReady
      || tokenPresent
      || Boolean(snapshot?.runtime.actions?.mutation_enabled);
    if (!item.valid || !pendingActionReady) return;
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
    knownThreads: headlessThreads,
    projectId: headlessProjectId,
  }), [headlessConversationEvents, headlessConversationId, headlessProjectId, headlessThreadKey, headlessThreads, operatorBackend]);
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
      void submitHeadlessMessage(next.message, {
        force: true,
        requestPatch: next.requestPatch,
      });
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
    : headlessPlanDiscussion
      ? `Ask about ${headlessPlanDiscussion.header || "this plan"}...`
    : taskRefOn && context.taskId
      ? `问关于 ${context.taskId} 的任何事(状态 / 合同 / 证据 / 时间线…)`
      : "Found a bug? Tab /issue to report.";
  const selfIssueTargetIsLocked = selfIssueTargetLocked(selfIssueCard);
  const selfIssuePublishedIssueUrls = selfIssuePublishedUrls(selfIssueCard);
  const selfIssueTargetPolicy = recordValue(selfIssueCard?.target_policy);
  const selfIssueTargets = recordValue(selfIssueTargetPolicy?.targets);
  const selfIssuePublicationBatch = recordValue(selfIssueCard?.publication_batch);
  const selfIssuePublicationMode = textValue(
    selfIssueCard?.selected_publication_mode
      ?? selfIssueCard?.publication_mode
      ?? selfIssuePublicationBatch?.publication_mode,
  ) || "github";
  const selfIssueAllowedModes = Array.isArray(selfIssueTargetPolicy?.allowed_modes)
    ? selfIssueTargetPolicy.allowed_modes.map((value) => textValue(value)).filter(Boolean)
    : ["github"];
  const selfIssuePreviews = recordValue(
    selfIssueCard?.previews ?? selfIssuePublicationBatch?.previews,
  );
  const selfIssuePreview = recordValue(
    selfIssueCard?.preview
      ?? selfIssuePreviews?.[selfIssuePublicationMode === "both" ? "gitlab" : selfIssuePublicationMode],
  );
  const selfIssueEnvironment = recordValue(selfIssueCard?.environment);
  const selfIssueAttachmentPreparation = recordValue(selfIssueCard?.attachment_preparation);
  const selfIssueRawStatus = textValue(selfIssueCard?.status);
  const selfIssueAuthorizationProvider = textValue(selfIssueCard?.provider);
  const selfIssueProviderStatuses = recordValue(selfIssueCard?.providers);
  const selfIssueIntentIds = recordValue(selfIssueCard?.intent_ids);
  const selfIssueRecoverProvider = Object.entries(selfIssueProviderStatuses ?? {}).find(
    ([, status]) => ["publishing", "outcome_unknown"].includes(textValue(status)),
  )?.[0] ?? "";
  const selfIssueRecoverIntentId = textValue(
    selfIssueIntentIds?.[selfIssueRecoverProvider] ?? selfIssueCard?.intent_id,
  );
  const selfIssueGithubTransactionId = textValue(selfIssueCard?.transaction_id);
  const selfIssuePublished = selfIssuePublicationLocked(selfIssueCard);
  const selfIssueAuthorizationPublishing = selfIssueRawStatus === "authorization_pending"
    && selfIssueAuthorizationProvider !== "github";
  const selfIssuePreparationId = textValue(
    selfIssueCard?.preparation_id ?? selfIssueAttachmentPreparation?.preparation_id,
  );
  const selfIssuePreparationStatus = textValue(
    selfIssueRawStatus.startsWith("attachments_")
      ? selfIssueRawStatus
      : selfIssueAttachmentPreparation?.status,
  ).replace(/^attachments_/, "");
  const selfIssuePreparationConfirmation = textValue(
    selfIssueCard?.attachment_confirmation_id
      ?? selfIssueAttachmentPreparation?.confirmation_id,
  );
  const selfIssueManifestDigest = textValue(
    selfIssueCard?.manifest_digest ?? selfIssueAttachmentPreparation?.manifest_digest,
  );
  const selfIssuePreviewEntries = selfIssuePreviews
    ? Object.entries(selfIssuePreviews).flatMap(([provider, raw]) => {
        const preview = recordValue(raw);
        return preview ? [[provider, preview] as const] : [];
      })
    : selfIssuePreview
      ? [[selfIssuePublicationMode, selfIssuePreview] as const]
      : [];
  const selfIssueEvidenceActivity = recordValue(selfIssueCard?.evidence_activity);
  const selfIssueEvidenceActivityEntries = Array.isArray(selfIssueEvidenceActivity?.entries)
    ? selfIssueEvidenceActivity.entries.flatMap((raw) => {
        const entry = recordValue(raw);
        const phase = textValue(entry?.phase);
        const label = textValue(entry?.label);
        const actor = textValue(entry?.actor);
        return phase && label ? [{ actor, phase, label, at: textValue(entry?.at) }] : [];
    })
    : [];

  useEffect(() => {
    if (
      !selfIssueGithubTransactionId
      || !["authorization_required", "authorization_pending", "slow_down"].includes(selfIssueRawStatus)
    ) return undefined;
    const retrySeconds = Math.max(1, Number(selfIssueCard?.retry_after || selfIssueCard?.interval || 5));
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void Promise.resolve(selfIssueActionRef.current("self-issue-github-device-poll", {
        transaction_id: selfIssueGithubTransactionId,
        session_id: selfIssueOAuthSession(),
      })).then((result) => {
        if (cancelled) return;
        const record = recordValue(result);
        const status = textValue(record?.status);
        if (["authorization_pending", "slow_down"].includes(status)) {
          setSelfIssueCard((current) => ({ ...(current ?? {}), ...(record ?? {}) }));
          return;
        }
        if (actionFailed(result)) {
          setOperatorError(actionFailureReason(result));
          return;
        }
        if (record) setSelfIssueCard((current) => ({
          ...(current ?? {}),
          ...(recordValue(record.draft) ?? {}),
          ...record,
        }));
        if (record && ["published", "publish_failed"].includes(textValue(record.status))) {
          window.localStorage.removeItem(selfIssueGithubTransactionStorageKey(activeProjectId, selfIssueDraftId));
        }
      }).catch((error: unknown) => {
        if (!cancelled) setOperatorError(error instanceof Error ? error.message : String(error));
      });
    }, retrySeconds * 1000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeProjectId, selfIssueCard?.interval, selfIssueCard?.retry_after, selfIssueDraftId, selfIssueGithubTransactionId, selfIssueRawStatus]);

  useEffect(() => {
    const applyOAuthCompletion = (value: unknown) => {
      const completion = recordValue(value);
      if (textValue(completion?.type) !== "zf:self-issue-oauth-complete") return;
      const result = recordValue(completion?.result);
      if (!result) return;
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        setSelfIssueCard((current) => current ? { ...current, status: "authorization_required" } : current);
        return;
      }
      const payload = recordValue(result.result);
      const draft = recordValue(result.draft) ?? recordValue(payload?.draft);
      const callbackStatus = textValue(result.status) || textValue(payload?.status);
      setOperatorError("");
      setSelfIssueCard((current) => ({
        ...(current ?? {}),
        ...(draft ?? {}),
        ...(payload ?? {}),
        ...result,
        ...(callbackStatus ? { status: callbackStatus } : {}),
      }));
      if (["published", "partially_published"].includes(callbackStatus)) {
        setSelfIssueSuccessNotice("Published successfully.");
      }
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin) applyOAuthCompletion(event.data);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== "zf.selfIssueOAuthCompleted" || !event.newValue) return;
      try { applyOAuthCompletion(JSON.parse(event.newValue)); } catch { /* ignore malformed external state */ }
    };
    window.addEventListener("message", onMessage);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("message", onMessage);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const openSelfIssuePreview = async (
    draftCard: Record<string, unknown> | null = selfIssueCard,
  ) => {
    if (!draftCard || selfIssuePreviewBusy) return;
    setSelfIssueDraftTab("preview");
    if (selfIssuePreviewIsReusable(draftCard, selfIssuePublicationMode)) return;
    setSelfIssuePreviewBusy(true);
    try {
      const needsPreparation = Array.isArray(draftCard.attachment_refs)
        && draftCard.attachment_refs.length > 0
        && (!Array.isArray(draftCard.published_attachments)
          || draftCard.published_attachments.length !== draftCard.attachment_refs.length);
      const action = needsPreparation && selfIssuePublicationMode !== "github"
        ? "self-issue-attachment-preview"
        : "self-issue-preview";
      const result = await Promise.resolve(onAction(action, {
        draft_id: textValue(draftCard.draft_id),
        publication_mode: selfIssuePublicationMode,
      }));
      const record = recordValue(result);
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
      } else if (record) {
        const draft = recordValue(record.draft);
        let merged: Record<string, unknown> = {
          ...draftCard,
          ...(draft ?? {}),
          ...record,
          ...(textValue(record.batch_id) ? { publication_batch: record } : {}),
          selected_publication_mode: selfIssuePublicationMode,
        };
        const publicationBatch = recordValue(merged.publication_batch);
        if (!textValue(merged.batch_id ?? publicationBatch?.batch_id)) {
          try {
            const restored = recordValue(await Promise.resolve(onAction("self-issue-get", {
              draft_id: textValue(merged.draft_id),
            })));
            const restoredDraft = recordValue(restored?.draft);
            if (restoredDraft) merged = {
              ...merged,
              ...restoredDraft,
              selected_publication_mode: selfIssuePublicationMode,
            };
          } catch {
            // The visible preview remains useful; the next background refresh can
            // restore its canonical batch controls after a transient API delay.
          }
        }
        setOperatorError("");
        setSelfIssueCard(merged);
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssuePreviewBusy(false);
    }
  };

  const saveSelfIssueAndPreview = async () => {
    if (!selfIssueCard || selfIssuePublished || selfIssueSavePreviewBusy) return;
    setSelfIssueSavePreviewBusy(true);
    const current = selfIssueCard;
    const environment = recordValue(current.environment);
    try {
      const result = await Promise.resolve(onAction("self-issue-update", {
        draft_id: textValue(current.draft_id),
        revision: Number(current.revision || 0),
        title: textValue(current.title),
        classification: textValue(current.classification) || "unknown",
        severity: textValue(current.severity) || "P2",
        reproduction_status: textValue(current.reproduction_status) || "unverified",
        summary: textValue(current.summary),
        bug_description: textValue(current.bug_description || current.summary),
        reproduction_steps: textValue(current.reproduction_steps),
        expected_behavior: textValue(current.expected_behavior),
        attachment_context: textValue(current.attachment_context),
        environment: {
          os: textValue(environment?.os),
          version: textValue(environment?.version),
        },
        zaofu_version: textValue(current.zaofu_version),
        additional_context: textValue(current.additional_context),
        component: textValue(current.component) || "unknown",
        impact_scope: textValue(current.impact_scope) || "unknown",
        assessment_confidence: textValue(current.assessment_confidence) || "low",
        recommended_next_action: textValue(current.recommended_next_action),
        suggested_fix: textValue(current.recommended_next_action),
        ...(selfIssueTargetIsLocked ? {} : {
          target_binding: {
            provider: "gitlab",
            project: textValue(recordValue(current.target_binding)?.project),
          },
        }),
      }));
      const record = recordValue(result);
      const draft = recordValue(record?.draft);
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
      } else if (draft) {
        const saved = {
          ...draft,
          selected_publication_mode: selfIssuePublicationMode,
        };
        setOperatorError("");
        setSelfIssueCard(saved);
        await openSelfIssuePreview(saved);
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssueSavePreviewBusy(false);
    }
  };

  const runSelfIssueEvidence = async (
    draft: Record<string, unknown>,
    action = "self-issue-evidence-start",
    extra: Record<string, unknown> = {},
  ) => {
    if (selfIssueEvidenceBusy) return;
    selfIssueEvidencePollGenerationRef.current += 1;
    setSelfIssueEvidenceBusyAction(action);
    setSelfIssueRuntimeActionNotice("");
    if (action === "self-issue-evidence-interrupt") {
      setSelfIssueCard((current) => current ? {
        ...current,
        evidence_status: "interrupting",
      } : current);
    }
    try {
      const result = await Promise.resolve(onAction(action, {
        draft_id: textValue(draft.draft_id),
        revision: Number(draft.revision || 0),
        ...extra,
      }));
      const resultRecord = recordValue(result);
      const actionPayload = recordValue(resultRecord?.result) ?? resultRecord;
      const updatedDraft = recordValue(actionPayload?.draft) ?? recordValue(resultRecord?.draft);
      if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
        setSelfIssueRuntimeActionNotice(actionFailureReason(result));
        if (action === "self-issue-evidence-interrupt") setSelfIssueCard(draft);
      }
      else if (updatedDraft) {
        setOperatorError("");
        setSelfIssueCard(updatedDraft);
        if (action === "self-issue-runtime-check") {
          const status = textValue(actionPayload?.status || updatedDraft.runtime_status);
          setSelfIssueRuntimeActionNotice(
            status === "assessment_requested" || textValue(updatedDraft.runtime_status) === "live"
              ? "Runtime started. Live evidence collection and Orchestrator assessment are queued."
              : "Runtime is stopped. Static evidence remains saved locally.",
          );
        } else if (action === "self-issue-limited-continue") {
          setSelfIssueRuntimeActionNotice(
            "Limited report selected. Review the saved evidence and publication preview before confirming.",
          );
        }
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssueEvidenceBusyAction("");
    }
  };

  const prepareSelfIssueAttachments = async () => {
    if (!selfIssueCard || !selfIssuePreparationId || selfIssueAttachmentBusy) return;
    setSelfIssueAttachmentBusy(true);
    try {
      let confirmationId = selfIssuePreparationConfirmation;
      if (!confirmationId) {
        const confirmedResult = await Promise.resolve(onAction(
          "self-issue-attachment-confirm", {
            preparation_id: selfIssuePreparationId,
            manifest_digest: selfIssueManifestDigest,
          },
        ));
        const confirmed = recordValue(confirmedResult);
        if (actionFailed(confirmedResult)) {
          setOperatorError(actionFailureReason(confirmedResult));
          return;
        }
        confirmationId = textValue(confirmed?.confirmation_id);
      }
      const preparedResult = await Promise.resolve(onAction(
        "self-issue-attachment-prepare", {
          preparation_id: selfIssuePreparationId,
          confirmation_id: confirmationId,
        },
      ));
      const prepared = recordValue(preparedResult);
      const draft = recordValue(prepared?.draft);
      if (textValue(prepared?.status) === "authorization_required") {
        setOperatorError("");
        setSelfIssueCard({
          ...selfIssueCard,
          ...prepared,
          attachment_confirmation_id: confirmationId,
        });
      } else if (actionFailed(preparedResult)) {
        setOperatorError(actionFailureReason(preparedResult));
      } else if (draft) {
        const updated = {
          ...draft,
          selected_publication_mode: selfIssuePublicationMode,
        };
        setOperatorError("");
        setSelfIssueCard(updated);
        setSelfIssueSuccessNotice("GitLab attachments uploaded successfully.");
        await openSelfIssuePreview(updated);
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssueAttachmentBusy(false);
    }
  };

  const confirmSelfIssuePreview = async () => {
    if (!selfIssueCard || selfIssueConfirmationBusy) return;
    setSelfIssueConfirmationBusy(true);
    try {
      const result = await Promise.resolve(onAction("self-issue-confirm", {
        batch_id: textValue(selfIssueCard.batch_id ?? selfIssuePublicationBatch?.batch_id),
        payload_digest: textValue(selfIssueCard.payload_digest),
      }));
      const record = recordValue(result);
      if (actionFailed(result)) setOperatorError(actionFailureReason(result));
      else if (record) {
        setOperatorError("");
        setSelfIssueCard({ ...selfIssueCard, ...record });
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssueConfirmationBusy(false);
    }
  };

  const publishSelfIssue = async () => {
    if (!selfIssueCard || selfIssuePublishBusy) return;
    setSelfIssuePublishBusy(true);
    try {
      const recovering = ["publishing", "outcome_unknown"].includes(
        textValue(selfIssueCard.status),
      );
      const result = await Promise.resolve(onAction(
        recovering ? "self-issue-recover" : "self-issue-publish", {
          ...(recovering ? {
            intent_id: selfIssueRecoverIntentId,
          } : {
            batch_id: textValue(selfIssueCard.batch_id ?? selfIssuePublicationBatch?.batch_id),
            confirmation_id: textValue(selfIssueCard.confirmation_id),
          }),
        },
      ));
      const record = recordValue(result);
      if (record?.status === "authorization_required") {
        setOperatorError("");
        setSelfIssueCard({ ...selfIssueCard, ...record });
      } else if (actionFailed(result)) {
        setOperatorError(actionFailureReason(result));
      } else if (record) {
        setOperatorError("");
        const updated = {
          ...selfIssueCard,
          ...(recordValue(record.draft) ?? {}),
          ...record,
        };
        setSelfIssueCard(updated);
        if (["published", "partially_published"].includes(textValue(record.status))) {
          setSelfIssueSuccessNotice("Published successfully.");
        }
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssuePublishBusy(false);
    }
  };

  const resetSelfIssueCard = async () => {
    const draftId = textValue(selfIssueCard?.draft_id);
    if (!draftId || selfIssueCardResetting) return;
    setSelfIssueCardResetting(true);
    try {
      const result = await Promise.resolve(onAction("self-issue-get", { draft_id: draftId }));
      const record = recordValue(result);
      const draft = recordValue(record?.draft);
      if (actionFailed(result)) setOperatorError(actionFailureReason(result));
      else if (draft) {
        setOperatorError("");
        setSelfIssueCard(draft);
      }
    } catch (error: unknown) {
      setOperatorError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfIssueCardResetting(false);
    }
  };

  return (
    <section
      className={`panel orchestrator-panel ${panelMode}`}
      role="dialog"
      aria-modal={fullscreen}
      aria-label="Kanban Agent"
      onPointerDownCapture={(event) => {
        if (!(event.target instanceof Element) || !event.target.closest(".self-issue-intake-workspace")) {
          setSelfIssueIntakeForeground(false);
        }
      }}
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
          {selfIssueCard && !selfIssueCardExpanded ? (
            <button
              aria-label="Open Self-Issue Draft"
              className="self-issue-draft-launcher"
              data-testid="self-issue-draft-launcher"
              type="button"
              onClick={() => {
                setSelfIssueCardExpanded(true);
                persistSelfIssueCardLayout(activeProjectId, selfIssueDraftId, true);
              }}
            >
              <span className="self-issue-draft-launcher-main">
                <small>Self-Issue Draft</small>
                <strong title={textValue(selfIssueCard.title)}>
                  {textValue(selfIssueCard.title) || "Observed ZaoFu issue"}
                </strong>
              </span>
              <span className="self-issue-draft-launcher-status">
                {selfIssueEvidenceStatus || textValue(selfIssueCard.publication_state) || "draft"}
              </span>
              <Maximize2 aria-hidden="true" size={15} />
            </button>
          ) : null}
          {selfIssueIntake && !selfIssueIntakeExpanded ? (
            <button
              aria-label="Open Self-Issue questions"
              className="self-issue-draft-launcher"
              data-testid="self-issue-intake-launcher"
              type="button"
              onClick={() => {
                setSelfIssueIntakeExpanded(true);
                setSelfIssueIntakeForeground(true);
              }}
            >
              <span className="self-issue-draft-launcher-main">
                <small>Self-Issue Report</small>
                <strong title={textValue(recordValue(selfIssueIntake.answers)?.title)}>
                  {textValue(recordValue(selfIssueIntake.answers)?.title) || "Report a ZaoFu bug"}
                </strong>
              </span>
              <span className="self-issue-draft-launcher-status">
                {selfIssueIntakeBusy ? "Submitting…" : "Saved locally"}
              </span>
              <Maximize2 aria-hidden="true" size={15} />
            </button>
          ) : null}
          <div className="headless-composer">
            {selfIssueIntake && selfIssueWorkspaceHost ? createPortal(
              <div
                className={`self-issue-intake-workspace ${selfIssueIntakeForeground ? "foreground" : ""}`}
                data-testid="self-issue-intake-workspace"
                hidden={selfIssueIntakeMountState(selfIssueIntake, selfIssueIntakeExpanded).hidden}
                tabIndex={-1}
                onPointerDown={(event) => {
                  setSelfIssueIntakeForeground(true);
                  const target = event.target as HTMLElement;
                  if (!target.closest("input,textarea,select,button,a")) {
                    event.currentTarget.focus();
                  }
                }}
              >
                {selfIssueRuntimeWarning ? (
                  <div className="self-issue-runtime-warning" role="status">
                    <strong>Runtime is stopped</strong>
                    <span>{selfIssueRuntimeWarning}</span>
                  </div>
                ) : null}
                <SelfIssueIntakeWizard
                busy={selfIssueIntakeBusy}
                intake={selfIssueIntake}
                onSave={async (answers, currentStep) => {
                  const result = await Promise.resolve(onAction("self-issue-intake-save", {
                    intake_id: textValue(selfIssueIntake.intake_id),
                    answers,
                    current_step: currentStep,
                  }));
                  const record = recordValue(result);
                  const intake = recordValue(record?.intake);
                  const draft = recordValue(record?.draft);
                  if (actionFailed(result)) setOperatorError(actionFailureReason(result));
                  else if (draft) {
                    setOperatorError("");
                    setSelfIssueIntake(null);
                    setSelfIssueCard(draft);
                    setSelfIssueCardExpanded(true);
                  } else if (intake) {
                    setOperatorError("");
                    setSelfIssueIntake(intake);
                  }
                }}
                onAddAttachment={async (file, videoDisclosureConfirmed) => {
                  setSelfIssueIntakeBusy(true);
                  try {
                    const result = await Promise.resolve(onAction("self-issue-intake-attachment-add", {
                      intake_id: textValue(selfIssueIntake.intake_id),
                      filename: file.name,
                      content_type: file.type || attachmentContentType(file.name),
                      content_base64: await fileAsBase64(file),
                      video_disclosure_confirmed: videoDisclosureConfirmed,
                    }));
                    const record = recordValue(result);
                    const intake = recordValue(record?.intake);
                    if (actionFailed(result)) setOperatorError(actionFailureReason(result));
                    else if (intake) {
                      setOperatorError("");
                      setSelfIssueIntake(intake);
                    }
                  } finally {
                    setSelfIssueIntakeBusy(false);
                  }
                }}
                onRemoveAttachment={async (attachmentId) => {
                  setSelfIssueIntakeBusy(true);
                  try {
                    const result = await Promise.resolve(onAction("self-issue-intake-attachment-remove", {
                      intake_id: textValue(selfIssueIntake.intake_id),
                      attachment_id: attachmentId,
                    }));
                    const record = recordValue(result);
                    const intake = recordValue(record?.intake);
                    if (actionFailed(result)) setOperatorError(actionFailureReason(result));
                    else if (intake) {
                      setOperatorError("");
                      setSelfIssueIntake(intake);
                    }
                  } finally {
                    setSelfIssueIntakeBusy(false);
                  }
                }}
                onCancel={async () => {
                  setSelfIssueIntakeBusy(true);
                  try {
                    const result = await Promise.resolve(onAction("self-issue-intake-dismiss", {
                      intake_id: textValue(selfIssueIntake.intake_id),
                    }));
                    const record = recordValue(result);
                    const draft = recordValue(record?.draft);
                    if (actionFailed(result)) setOperatorError(actionFailureReason(result));
                    else if (draft) {
                      setOperatorError("");
                      setSelfIssueIntake(null);
                      setSelfIssueCard(draft);
                      setSelfIssueCardExpanded(true);
                    } else {
                      setOperatorError("");
                      setSelfIssueIntake(null);
                    }
                  } catch (error: unknown) {
                    setOperatorError(error instanceof Error ? error.message : String(error));
                  } finally {
                    setSelfIssueIntakeBusy(false);
                  }
                }}
                onMinimize={() => setSelfIssueIntakeExpanded(false)}
                onSubmit={async (answers, attachmentDisclosureConfirmed) => {
                  setSelfIssueIntakeBusy(true);
                  try {
                    const result = await Promise.resolve(onAction("self-issue-intake-submit", {
                      intake_id: textValue(selfIssueIntake.intake_id),
                      answers,
                      attachment_disclosure_confirmed: attachmentDisclosureConfirmed,
                    }));
                    const record = recordValue(result);
                    const draft = recordValue(record?.draft);
                    if (textValue(record?.status) === "intake_incomplete") {
                      return { missingQuestionId: textValue(record?.missing_question_id) };
                    }
                    if (textValue(record?.status) === "attachment_disclosure_required") {
                      return { attachmentDisclosureRequired: true };
                    }
                    if (actionFailed(result)) {
                      setOperatorError(actionFailureReason(result));
                      return;
                    }
                    if (draft) {
                      setOperatorError("");
                      setSelfIssueIntake(null);
                      setSelfIssueCard(draft);
                      setSelfIssueCardExpanded(true);
                      await runSelfIssueEvidence(draft);
                    }
                  } catch (error: unknown) {
                    setOperatorError(error instanceof Error ? error.message : String(error));
                  } finally {
                    setSelfIssueIntakeBusy(false);
                  }
                }}
                />
              </div>,
              selfIssueWorkspaceHost,
            ) : null}
            {selfIssueCard && selfIssueCardExpanded ? (
              <div
                aria-label="Self-Issue Draft"
                aria-modal="false"
                className="headless-pending-entry self-issue-draft-card expanded"
                data-testid="self-issue-draft-card"
                role="dialog"
              >
                <div className="self-issue-card-header">
                  <div className="headless-pending-title">Self-Issue Draft</div>
                  <div className="self-issue-card-controls">
                    <button
                      aria-label="Reset Self-Issue Draft"
                      className="self-issue-card-control"
                      disabled={selfIssueCardResetting}
                      title="Discard unsaved edits and reload the saved Draft"
                      type="button"
                      onClick={() => void resetSelfIssueCard()}
                    >
                      <RotateCcw aria-hidden="true" className={selfIssueCardResetting ? "spinning" : ""} size={15} />
                    </button>
                    <button
                      aria-label="Enlarge Self-Issue Draft"
                      className="self-issue-card-control"
                      disabled={selfIssueCardExpanded}
                      title="Enlarge Draft"
                      type="button"
                      onClick={() => {
                        setSelfIssueCardExpanded(true);
                        persistSelfIssueCardLayout(activeProjectId, selfIssueDraftId, true);
                      }}
                    >
                      <Maximize2 aria-hidden="true" size={15} />
                    </button>
                    <button
                      aria-label="Shrink Self-Issue Draft"
                      className="self-issue-card-control"
                      disabled={!selfIssueCardExpanded}
                      title="Restore Draft size"
                      type="button"
                      onClick={() => {
                        setSelfIssueCardExpanded(false);
                        persistSelfIssueCardLayout(activeProjectId, selfIssueDraftId, false);
                      }}
                    >
                      <Minus aria-hidden="true" size={16} />
                    </button>
                    <button
                      aria-label="Close Self-Issue Draft"
                      className="self-issue-card-control"
                      disabled={selfIssueCardClosing}
                      title="Dismiss this Draft permanently"
                      type="button"
                      onClick={() => void (async () => {
                        setSelfIssueCardClosing(true);
                        try {
                          const result = await Promise.resolve(onAction("self-issue-dismiss", {
                            draft_id: textValue(selfIssueCard.draft_id),
                          }));
                          if (actionFailed(result)) {
                            setOperatorError(actionFailureReason(result));
                          } else {
                            setOperatorError("");
                            markSelfIssueDismissed(activeProjectId);
                            clearSelfIssueCardLayout(activeProjectId, selfIssueDraftId);
                            window.localStorage.removeItem(
                              selfIssueGithubTransactionStorageKey(activeProjectId, selfIssueDraftId),
                            );
                            setSelfIssueSuccessNotice("");
                            setSelfIssueCard(null);
                            setSelfIssueCardExpanded(false);
                          }
                        } catch (error: unknown) {
                          setOperatorError(error instanceof Error ? error.message : String(error));
                        } finally {
                          setSelfIssueCardClosing(false);
                        }
                      })()}
                    >
                      {selfIssueCardClosing
                        ? <LoaderCircle aria-hidden="true" className="self-issue-spinner" size={16} />
                        : <X aria-hidden="true" size={16} />}
                    </button>
                  </div>
                </div>
                <div className="self-issue-editor-tabs" role="tablist" aria-label="Self-Issue editor">
                  <button
                    aria-selected={selfIssueDraftTab === "draft"}
                    className={selfIssueDraftTab === "draft" ? "active" : ""}
                    role="tab"
                    type="button"
                    onClick={() => setSelfIssueDraftTab("draft")}
                  >Draft</button>
                  <button
                    aria-selected={selfIssueDraftTab === "preview"}
                    className={selfIssueDraftTab === "preview" ? "active" : ""}
                    disabled={selfIssueEvidenceBlocksPreview(selfIssueEvidenceStatus) || selfIssuePreviewBusy || selfIssueSavePreviewBusy}
                    role="tab"
                    type="button"
                    onClick={() => {
                      if (selfIssuePublished) setSelfIssueDraftTab("preview");
                      else void saveSelfIssueAndPreview();
                    }}
                  >{selfIssuePreviewBusy || selfIssueSavePreviewBusy ? "Preparing…" : "Preview"}</button>
                </div>
                <fieldset
                  className={selfIssueDraftTab === "draft" ? "self-issue-draft-fields" : "self-issue-draft-fields hidden"}
                  disabled={selfIssuePublished}
                >
                <div className="self-issue-draft-side self-issue-user-report-side">
                <section className="self-issue-draft-column self-issue-user-report-core">
                <h3>User report</h3>
                <label>
                  Title
                  <input
                    className="filter-input"
                    value={textValue(selfIssueCard.title)}
                    onChange={(event) => setSelfIssueCard({ ...selfIssueCard, title: event.target.value })}
                  />
                </label>
                <label>
                  Publish destination
                  <select
                    className="filter-input"
                    value={selfIssuePublicationMode}
                    onChange={(event) => {
                      setSelfIssueSuccessNotice("");
                      setSelfIssueCard(selfIssueSelectDestination(selfIssueCard, event.target.value));
                    }}
                  >
                    {selfIssueAllowedModes.map((mode) => (
                      <option key={mode} value={mode}>
                        {selfIssueProviderLabel(mode)}
                      </option>
                    ))}
                  </select>
                </label>
                {(["gitlab", "github"] as const).map((provider) => {
                  const target = recordValue(selfIssueTargets?.[provider]);
                  if (!target) return null;
                  return (
                    <label key={provider}>
                      {selfIssueProviderLabel(provider)} project (centrally managed)
                      <input
                        aria-readonly="true"
                        className="filter-input"
                        readOnly
                        title="This target is fixed by zf.yaml"
                        value={textValue(target.project)}
                      />
                    </label>
                  );
                })}
                </section>
                <section className="self-issue-draft-column self-issue-user-report-details">
                <label>
                  Describe the bug
                  <textarea
                    className="filter-input"
                    value={textValue(selfIssueCard.bug_description || selfIssueCard.summary)}
                    onChange={(event) => setSelfIssueCard({
                      ...selfIssueCard, bug_description: event.target.value, summary: event.target.value,
                    })}
                  />
                </label>
                <label>
                  To reproduce
                  <textarea
                    className="filter-input"
                    value={textValue(selfIssueCard.reproduction_steps)}
                    onChange={(event) => setSelfIssueCard({ ...selfIssueCard, reproduction_steps: event.target.value })}
                  />
                </label>
                <label>Expected behavior<textarea className="filter-input" value={textValue(selfIssueCard.expected_behavior)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, expected_behavior: event.target.value })} /></label>
                <label>Attachment context<textarea className="filter-input" value={textValue(selfIssueCard.attachment_context)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, attachment_context: event.target.value })} /></label>
                <label>Operating system<input className="filter-input" value={textValue(selfIssueEnvironment?.os)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, environment: { ...(selfIssueEnvironment ?? {}), os: event.target.value } })} /></label>
                <label>Operating system version<input className="filter-input" value={textValue(selfIssueEnvironment?.version)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, environment: { ...(selfIssueEnvironment ?? {}), version: event.target.value } })} /></label>
                <label>Current ZaoFu version<input className="filter-input" value={textValue(selfIssueCard.zaofu_version)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, zaofu_version: event.target.value })} /></label>
                <label>Additional context<textarea className="filter-input" value={textValue(selfIssueCard.additional_context)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, additional_context: event.target.value })} /></label>
                </section>
                </div>
                <div className="self-issue-draft-side self-issue-assessment-side">
                <section className="self-issue-draft-column self-issue-assessment-core">
                <h3>Agent &amp; Orchestrator assessment</h3>
                <label>
                  Classification
                  <select className="filter-input" value={textValue(selfIssueCard.classification) || "unknown"} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, classification: event.target.value })}>
                    {["runtime", "kernel/state", "provider/integration", "web/ui", "configuration", "security", "performance", "test/regression", "unknown"].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label>
                  Severity
                  <select className="filter-input" value={textValue(selfIssueCard.severity) || "P2"} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, severity: event.target.value })}>
                    {["P0", "P1", "P2", "P3"].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label>
                  Reproduction status
                  <select className="filter-input" value={textValue(selfIssueCard.reproduction_status) || "unverified"} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, reproduction_status: event.target.value })}>
                    {["reproduced", "observed", "unverified"].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                </section>
                <section className="self-issue-draft-column self-issue-assessment-details">
                <label>Component<input className="filter-input" value={textValue(selfIssueCard.component)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, component: event.target.value })} /></label>
                <label>Impact scope<textarea className="filter-input" value={textValue(selfIssueCard.impact_scope)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, impact_scope: event.target.value })} /></label>
                <label>
                  Assessment confidence
                  <select className="filter-input" value={textValue(selfIssueCard.assessment_confidence) || "low"} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, assessment_confidence: event.target.value })}>
                    {["low", "medium", "high"].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label>Recommended next action<textarea className="filter-input" value={textValue(selfIssueCard.recommended_next_action)} onChange={(event) => setSelfIssueCard({ ...selfIssueCard, recommended_next_action: event.target.value })} /></label>
                <div className="headless-pending-details">
                  <div><span className="headless-pending-key">classification</span>{textValue(selfIssueCard.classification) || "unknown"}</div>
                  <div><span className="headless-pending-key">severity</span>{textValue(selfIssueCard.severity) || "P2"}</div>
                  <div><span className="headless-pending-key">status</span>{textValue(selfIssueCard.publication_state) || textValue(selfIssueCard.status) || "draft"}</div>
                  <div><span className="headless-pending-key">component</span>{textValue(selfIssueCard.component) || "unknown"}</div>
                  <div><span className="headless-pending-key">evidence</span>{selfIssueEvidenceStatus || "pending"}</div>
                  <div><span className="headless-pending-key">runtime</span>{selfIssueRuntimeStatus}</div>
                  <div><span className="headless-pending-key">assessment</span>{selfIssueAssessmentStatus}</div>
                  {textValue(selfIssueCard.evidence_error) ? <div role="alert">{textValue(selfIssueCard.evidence_error)}</div> : null}
                  {textValue(selfIssueCard.summary) ? (
                    <div>{selfIssueCompactText(selfIssueCard.summary)}</div>
                  ) : null}
                </div>
                <section className="self-issue-runtime-state" aria-label="Self-Issue runtime state">
                  <div><strong>Project runtime:</strong> {selfIssueRuntimeStatus}</div>
                  <div><strong>Static evidence:</strong> {
                    ["pending", "collecting_static"].includes(selfIssueEvidenceStatus)
                      ? selfIssueEvidenceStatus
                      : "completed"
                  }</div>
                  <div><strong>Orchestrator assessment:</strong> {selfIssueAssessmentStatus}</div>
                  {selfIssueEvidenceStatus === "waiting_for_runtime" ? (
                    <div
                      className={`self-issue-runtime-waiting ${selfIssueRuntimeLive ? "live" : "stopped"}`}
                      role="status"
                    >
                      <p><strong>
                        {selfIssueRuntimeLive
                          ? "Runtime is started."
                          : "Runtime is stopped. Static evidence remains saved locally."}
                      </strong></p>
                      <p>
                        Your report and static evidence were saved locally. Live runtime events,
                        current worker context, active logs, and dynamic reproduction have not been collected.
                      </p>
                      <p>Start the runtime with:</p>
                      <code>cd /path_to_project &amp;&amp; zf start</code>
                      {selfIssueRuntimeActionNotice ? (
                        <p
                          className={`self-issue-runtime-action-notice ${selfIssueRuntimeLive ? "live" : "stopped"}`}
                          role="status"
                        >
                          {selfIssueRuntimeActionNotice}
                        </p>
                      ) : null}
                      <div className="self-issue-runtime-actions">
                        <button
                          className="headless-pending-run"
                          disabled={selfIssueEvidenceBusy}
                          type="button"
                          onClick={() => void runSelfIssueEvidence(selfIssueCard, "self-issue-runtime-check")}
                        >
                          {selfIssueEvidenceBusyAction === "self-issue-runtime-check"
                            ? "Checking…" : "Check runtime again"}
                        </button>
                        <button
                          className="headless-pending-run"
                          disabled={selfIssueEvidenceBusy}
                          type="button"
                          onClick={() => void runSelfIssueEvidence(selfIssueCard, "self-issue-limited-continue")}
                        >
                          {selfIssueEvidenceBusyAction === "self-issue-limited-continue"
                            ? "Continuing…" : "Continue with limited report"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </section>
                {recordValue(selfIssueCard.analysis) ? (
                  <section className="self-issue-assessment-findings" aria-label="Orchestrator assessment findings">
                    <strong>Assessment findings</strong>
                    {Object.entries(recordValue(selfIssueCard.analysis) ?? {}).map(([key, value]) => (
                      <div key={key}>
                        <span>{key.replaceAll("_", " ")}</span>
                        <p>{selfIssueAssessmentText(value)}</p>
                      </div>
                    ))}
                  </section>
                ) : null}
                {selfIssueEvidenceActivityEntries.length ? (
                  <section className="self-issue-evidence-activity" aria-label="Evidence and assessment activity">
                    <div className="self-issue-evidence-activity-heading">
                      <strong>Evidence &amp; assessment activity</strong>
                      <span>{textValue(selfIssueEvidenceActivity?.status) || selfIssueEvidenceStatus}</span>
                    </div>
                    <ol>
                      {selfIssueEvidenceActivityEntries.map((entry, index) => (
                        <li className={index === selfIssueEvidenceActivityEntries.length - 1 ? "current" : ""} key={`${entry.phase}:${entry.at}:${index}`}>
                          <span aria-hidden="true" />
                          <div><strong>{entry.actor ? `${entry.actor} · ` : ""}{entry.label}</strong>{entry.at ? <small>{entry.at}</small> : null}</div>
                        </li>
                      ))}
                    </ol>
                  </section>
                ) : null}
                </section>
                </div>
                </fieldset>
                <div className="headless-pending-item">
                  {selfIssueSuccessNotice || selfIssuePublished ? (
                    <div className="self-issue-success-notice" role="status">
                      {selfIssueSuccessNotice || "Published successfully."}
                    </div>
                  ) : null}
                  <button
                    className="headless-pending-run"
                    disabled={
                      selfIssuePublished
                      || selfIssueEvidenceBlocksPreview(selfIssueEvidenceStatus)
                      || selfIssuePreviewBusy
                      || selfIssueSavePreviewBusy
                    }
                    type="button"
                    onClick={() => void saveSelfIssueAndPreview()}
                  >
                    {selfIssueSavePreviewBusy || selfIssuePreviewBusy ? (
                      <><LoaderCircle aria-hidden="true" className="self-issue-spinner" size={13} /> Saving &amp; preparing…</>
                    ) : "Save & preview"}
                  </button>
                  {evidenceControls.map((evidenceControl) => (
                    <button
                      key={evidenceControl.action}
                      className="headless-pending-run"
                      disabled={selfIssuePublished || selfIssueEvidenceBusy}
                      type="button"
                      onClick={() => void runSelfIssueEvidence(
                        selfIssueCard,
                        evidenceControl.action,
                        evidenceControl.force ? { force: true } : {},
                      )}
                    >
                      {evidenceControl.action === "self-issue-evidence-interrupt"
                        ? <Square aria-hidden="true" size={13} />
                        : <Play aria-hidden="true" size={13} />}
                      {selfIssueEvidenceBusyAction === evidenceControl.action
                        ? evidenceControl.busyLabel
                        : evidenceControl.label}
                    </button>
                  ))}
                  {!selfIssuePublished && selfIssuePreparationId && ["previewed", "confirmed"].includes(selfIssuePreparationStatus) ? (
                    <button
                      className="headless-pending-run"
                      disabled={selfIssueAttachmentBusy}
                      type="button"
                      onClick={() => void prepareSelfIssueAttachments()}
                    >
                      {selfIssueAttachmentBusy ? (
                        <><LoaderCircle aria-hidden="true" className="self-issue-spinner" size={13} /> Uploading attachments…</>
                      ) : "Confirm & upload attachments to Gitlab"}
                    </button>
                  ) : null}
                  {!selfIssuePublished && textValue(selfIssueCard.batch_id ?? selfIssuePublicationBatch?.batch_id) && !textValue(selfIssueCard.confirmation_id) ? (
                    <button
                      className="headless-pending-run"
                      disabled={selfIssueConfirmationBusy}
                      type="button"
                      onClick={() => void confirmSelfIssuePreview()}
                    >
                      {selfIssueConfirmationBusy ? (
                        <><LoaderCircle aria-hidden="true" className="self-issue-spinner" size={13} /> Confirming…</>
                      ) : "Confirm this exact preview"}
                    </button>
                  ) : null}
                  {selfIssuePublished && Object.keys(selfIssuePublishedIssueUrls).length ? (
                    Object.entries(selfIssuePublishedIssueUrls).map(([provider, url]) => (
                      <a
                        className="headless-pending-run self-issue-published-link"
                        href={url}
                        key={provider}
                        rel="noopener noreferrer"
                        target="_blank"
                      >
                        Published on {selfIssueProviderLabel(provider)} &amp; View
                      </a>
                    ))
                  ) : textValue(selfIssueCard.confirmation_id) || ["publishing", "outcome_unknown", "authorization_pending"].includes(textValue(selfIssueCard.status)) ? (
                    <button
                      className="headless-pending-run"
                      disabled={selfIssuePublishBusy || selfIssueAuthorizationPublishing}
                      type="button"
                      onClick={() => void publishSelfIssue()}
                    >
                      {selfIssuePublishBusy || selfIssueAuthorizationPublishing ? (
                        <><LoaderCircle aria-hidden="true" className="self-issue-spinner" size={13} /> Publishing…</>
                      ) : ["publishing", "outcome_unknown"].includes(textValue(selfIssueCard.status))
                        ? "Recover outcome by marker"
                        : `Publish to ${selfIssueProviderLabel(selfIssuePublicationMode)}`}
                    </button>
                  ) : null}
                  {textValue(selfIssueCard.status) === "authorization_required" && selfIssueAuthorizationProvider !== "github" ? (
                    <div>
                      <div className="headless-pending-details">
                        GitLab api scope grants broad read/write API access beyond issue creation.
                      </div>
                      <button className="headless-pending-run" type="button" onClick={() => void (async () => {
                        // Open synchronously so popup blockers do not reject the
                        // provider authorization tab after the API round trip.
                        const authWindow = window.open("about:blank", "_blank");
                        authWindow?.sessionStorage.setItem("zf.selfIssueOAuthPopup", "1");
                        try {
                          const result = await Promise.resolve(onAction("self-issue-oauth-start", {
                            draft_id: textValue(selfIssueCard.draft_id),
                            session_id: selfIssueOAuthSession(),
                            ...selfIssueOAuthContinuation(selfIssueCard),
                          }));
                          const record = recordValue(result);
                          if (actionFailed(result)) {
                            authWindow?.close();
                            setOperatorError(actionFailureReason(result));
                          } else if (textValue(record?.authorization_url)) {
                            const authorizationUrl = textValue(record?.authorization_url);
                            setSelfIssueCard((current) => ({
                              ...(current ?? selfIssueCard),
                              provider: "gitlab",
                              status: "authorization_pending",
                            }));
                            if (authWindow) authWindow.location.href = authorizationUrl;
                            else window.open(authorizationUrl, "_blank");
                          } else {
                            authWindow?.close();
                          }
                        } catch (error: unknown) {
                          authWindow?.close();
                          setOperatorError(error instanceof Error ? error.message : String(error));
                        }
                      })()}>
                        Connect GitLab.com (api scope)
                      </button>
                    </div>
                  ) : null}
                  {["authorization_required", "authorization_pending", "slow_down"].includes(textValue(selfIssueCard.status)) && selfIssueAuthorizationProvider === "github" ? (
                    <div className="self-issue-github-device-flow">
                      <div className="headless-pending-details">
                        GitHub authorization can create Issues in the installed repository. Binary files are not uploaded to GitHub.
                      </div>
                      {selfIssueGithubTransactionId ? (
                        <>
                          <div className="headless-pending-details" role="status">
                            Enter code <strong>{textValue(selfIssueCard.user_code)}</strong> on GitHub. This page will continue automatically after authorization.
                          </div>
                          <a
                            className="headless-pending-run"
                            href={textValue(selfIssueCard.verification_uri)}
                            rel="noopener noreferrer"
                            target="_blank"
                          >
                            Open GitHub authorization
                          </a>
                        </>
                      ) : (
                        <button className="headless-pending-run" type="button" onClick={() => void (async () => {
                          const result = await Promise.resolve(onAction("self-issue-github-device-start", {
                            draft_id: textValue(selfIssueCard.draft_id),
                            batch_id: textValue(selfIssueCard.batch_id ?? selfIssuePublicationBatch?.batch_id),
                            confirmation_id: textValue(selfIssueCard.confirmation_id),
                            session_id: selfIssueOAuthSession(),
                          }));
                          const record = recordValue(result);
                          if (actionFailed(result)) setOperatorError(actionFailureReason(result));
                          else if (record) {
                            const transactionId = textValue(record.transaction_id);
                            if (transactionId) {
                              window.localStorage.setItem(
                                selfIssueGithubTransactionStorageKey(activeProjectId, textValue(selfIssueCard.draft_id)),
                                transactionId,
                              );
                            }
                            setSelfIssueCard({ ...selfIssueCard, ...record });
                          }
                        })()}>
                          Connect GitHub
                        </button>
                      )}
                    </div>
                  ) : null}
                </div>
                {selfIssuePublished ? (
                  <div className="self-issue-published-notice" role="status">
                    This Issue has been published. Its Draft and publication snapshot are
                    immutable; create a new Self-Issue report to make further changes.
                  </div>
                ) : null}
                {selfIssueDraftTab === "preview" && selfIssuePreparationId && selfIssuePreparationStatus !== "prepared" ? (
                  <section className="self-issue-attachment-preparation">
                    <strong>GitLab attachment preparation</strong>
                    <p>Review the selected files, then confirm their external disclosure before upload.</p>
                    <ul>
                      {(Array.isArray(selfIssueCard.attachments) ? selfIssueCard.attachments : []).map((raw, index) => {
                        const item = recordValue(raw);
                        const localUrl = selfIssueLocalAttachmentUrl(
                          activeProjectId,
                          textValue(selfIssueCard.draft_id),
                          textValue(item?.sha256),
                        );
                        return <li key={`${textValue(item?.sha256)}:${index}`}>
                          <div>
                            {localUrl ? (
                              <a href={localUrl} target="_blank" rel="noreferrer">
                                {textValue(item?.filename)}
                              </a>
                            ) : textValue(item?.filename)}
                            {` · ${textValue(item?.content_type)} · ${Number(item?.byte_count || 0)} bytes`}
                            {textValue(item?.capture_source) ? ` · ${textValue(item?.capture_source)}` : ""}
                          </div>
                          {textValue(item?.local_path) ? (
                            <div className="self-issue-local-attachment-path">
                              Local controlled copy: <code>{textValue(item?.local_path)}</code>
                            </div>
                          ) : null}
                        </li>;
                      })}
                    </ul>
                  </section>
                ) : null}
                {selfIssueDraftTab === "preview" ? selfIssuePreviewEntries.map(([provider, preview]) => {
                  const labels = Array.isArray(preview.labels)
                    ? preview.labels.map((value) => textValue(value)).filter(Boolean)
                    : [];
                  return (
                    <section className="self-issue-markdown-preview" aria-label={`${provider} Issue Markdown preview`} key={provider}>
                      <div className="self-issue-preview-heading">
                        <strong>{textValue(preview.title) || "Untitled issue"}</strong>
                        <span>{selfIssueProviderLabel(provider)}</span>
                        {labels.length ? <span>{labels.map((label) => `#${label}`).join(" ")}</span> : null}
                      </div>
                      <MarkdownText content={textValue(preview.body)} />
                    </section>
                  );
                }) : null}
              </div>
            ) : null}
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
                  void submitHeadlessMessage(event.currentTarget.value);
                }
              }}
            />
            <div className="headless-composer-footer">
              <ComposerSubmitButton
                className="headless-send-button"
                disabled={!headlessMessage.trim() || Boolean(permissionEscalation)}
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
