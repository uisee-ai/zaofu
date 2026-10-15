import { useChannelComposerDraft, useChannelThreadSelection, channelDraftStorageKey, type ComposerAttachment, type ChannelComposerDraft } from "./useChannelConversationState";
import { ChannelContextFailures } from "./ChannelContextFailures";
// ChannelPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { search, uploadChannelAttachment } from "../../api/client";
import type { ActionResponse, ChannelDetail, ChannelHistorySearchResult, ChannelSummary, RecentEvent } from "../../api/types";
import { AgentSessionTimeline } from "../../components/agent-session/AgentSessionTimeline";
import type { AgentSessionMessage, AgentSessionRun, AgentSessionTurn } from "../../components/agent-session/types";
import { buildChannelConversation, channelThreadId } from "../../components/agent-session/channelProjection";
import { elapsedSecondsSince, formatElapsed } from "../../components/agent-session/liveRunIndicator";
import { channelLiveStreamRows, compactChannelLiveRows, foldChannelLiveStream } from "../agent-session/channelLiveStream";
import { mergeEventsByIdentity } from "../orchestrator/kanbanSessionEvents";
import { formatTime } from "../../lib/format";
import { Archive, ArrowUp, AtSign, Bell, Bold, ChevronDown, Code, FileText, Hash, Italic, Link, List, ListOrdered, Loader2, MessageSquare, MoreHorizontal, PlayCircle, Plus, Quote, Search, Send, Smile, SquareCode, Strikethrough, Trash2, Type, Underline, Users, Wrench, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Fragment, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { UIEvent as ReactUIEvent } from "react";
import type { ChannelPermissionProfile } from "../../app/sharedTypes";
import { TablePage, agentConversationScrollSignature, asRecordArray, asStringArray, channelIdOf, channelNameOf, recordString, recordValue, scrollElementToBottom, textValue } from "../../app/shared";
import { previewItemsFromRefs, type PreviewItem } from "../agent-session/previewRegistry";
import { ChannelFilePreview } from "./ChannelFilePreview";
import { channelFlow, channelMemberLabel, channelSourceThreadId, FLOW_LABELS, type ChannelFlowNode } from "./channelFlow";
import { ChannelFlowNavigation, ChannelFlowPreview } from "./ChannelFlowNavigation";
import { ChannelMemberIdentity, memberColor } from "./ChannelMemberIdentity";
import { ChannelQuestionCard } from "./ChannelQuestionCard";
import { ChannelFactScopeControl } from "./ChannelFactScopeControl";
import { ChannelDecisionQueue, type ChannelDecisionItem } from "./ChannelDecisionQueue";
import { ChannelMemberInvitations } from "./ChannelMemberInvitations";
import { channelReplyHealth, replyFailurePresentation } from "./replyHealth";
import { channelDecisionReceipts, questionTargetRun } from "./channelDecisions";
import { ChannelDecisionHistory } from "./ChannelDecisionHistory";
import { channelChatConversation } from "./chatPresentation";
import { ChannelPrdHandoff } from "./ChannelPrdHandoff";
import { ChannelPrdPreview } from "./ChannelPrdPreview";
import { ChannelConsensusControl } from "./ChannelConsensusControl";
import { useChannelDelivery } from "./useChannelDelivery";
import { channelDocumentKey } from "../../api/channelDelivery";
import { openOwnerQuestionnaire } from "./channelQuestionnaire";
import { type AskUserQuestionAnswer } from "../common/AskUserQuestion";
import {
  channelDiscussionControlPolicy,
  presentChannelDiscussionAttention,
  selectChannelDiscussionAttention,
  selectDiscussionStartSource,
} from "./channelDiscussionAttention";

const ChannelEmojiPicker = lazy(() => import("./ChannelEmojiPicker").then((module) => ({
  default: module.ChannelEmojiPicker,
})));

type ChannelDrawerKey = "members" | "attention" | "diagnostics" | "reports" | "settings" | "file" | ChannelFlowNode;

type ComposerFormatAction = "bold" | "italic" | "underline" | "strike" | "link" | "ordered-list" | "unordered-list" | "quote" | "inline-code" | "code-block";


interface EmojiMartSelection {
  native?: string;
  shortcodes?: string;
}


interface PendingChannelMessage {
  id: string;
  channelId: string;
  projectId: string;
  threadId: string;
  text: string;
  ts: string;
  targets: string[];
  refs?: Record<string, unknown>;
}

interface ChannelMessageIngress {
  clientMessageId: string;
  threadId: string;
  replyToMessageId?: string;
}

interface ChannelMentionDigest {
  id: string;
  messageId: string;
  threadId: string;
  actor: string;
  source: string;
  ts: string;
  targets: string[];
  unresolvedTargets: string[];
  text: string;
}

interface ChannelOwnerQuestion {
  id: string;
  header: string;
  question: string;
  description?: string;
  options: Array<{
    id: string;
    label: string;
    description?: string;
    recommended?: boolean;
  }>;
  allowOther: boolean;
  inputPlaceholder: string;
  required: boolean;
  skipLabel: string;
  askedBy: string;
  priority: string;
  sourceThreadId: string;
  sourceMessageId: string;
  sourceRunId: string;
}

function ChannelLiveElapsed({ startedAt }: { startedAt: string }) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return undefined;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  const seconds = elapsedSecondsSince(startedAt, nowMs);
  if (seconds === undefined) return null;
  return (
    <span aria-hidden="true" className="channel-discussion-live-elapsed">
      {formatElapsed(seconds)}
    </span>
  );
}


function channelActionNoticeText(result: ActionResponse): string {
  if (result.action !== "channel-post-message") {
    return result.reason || result.status;
  }
  if (!result.ok) return result.reason || result.status || "message was not posted";
  const route = result.route && typeof result.route === "object" && !Array.isArray(result.route)
    ? result.route
    : {};
  const targets = asStringArray(route.targets);
  const replyRequests = asStringArray(route.reply_requests);
  const intentRequests = asStringArray(route.intent_requests);
  const skipped = asRecordArray(route.skipped);
  if (!targets.length && skipped.some((item) => textValue(item.reason) === "no_target")) {
    return "posted; no channel member matched @mention, so no agent reply was requested";
  }
  if (targets.length && !replyRequests.length && !intentRequests.length) {
    const reasons = skipped.map((item) => textValue(item.reason)).filter(Boolean);
    return reasons.length
      ? `posted; no reply requested (${reasons.join(", ")})`
      : "posted; no reply requested";
  }
  return result.reason || "message posted";
}

function newClientMessageId(): string {
  const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
  return `client-${suffix}`;
}


function ChannelReportPreviewRefs({
  rows,
  title,
}: {
  rows: Record<string, unknown>[];
  title: string;
}) {
  const items = rows.flatMap((row) => {
    const refs = recordValue(row.refs) ?? row;
    return previewItemsFromRefs(refs).map((item) => ({
      ...item,
      reportId: textValue(row.report_id) || textValue(row.event_id),
    }));
  }).slice(0, 12);
  if (!items.length) return null;
  return (
    <div className="channel-report-preview-panel">
      <div className="inline-heading">
        <h3>{title}</h3>
        <span className="muted">{items.length} refs</span>
      </div>
      <div className="agent-ref-chips">
        {items.map((item, index) => (
          <span className={`agent-ref-chip profile-${item.profile || "text"}`} key={`${item.reportId}-${item.kind}-${item.id || item.name}-${index}`}>
            <span>{item.name}</span>
            <small>{item.reportId ? `${item.reportId} / ${item.meta || item.kind}` : item.meta || item.kind}</small>
          </span>
        ))}
      </div>
    </div>
  );
}


function shouldShowChannelActionNotice(result: ActionResponse): boolean {
  if (result.action !== "channel-post-message") return true;
  if (!result.ok) return true;
  const route = result.route && typeof result.route === "object" && !Array.isArray(result.route)
    ? result.route
    : {};
  const targets = asStringArray(route.targets);
  const replyRequests = asStringArray(route.reply_requests);
  const intentRequests = asStringArray(route.intent_requests);
  const skipped = asRecordArray(route.skipped);
  if (!targets.length && skipped.some((item) => textValue(item.reason) === "no_target")) return true;
  return Boolean(targets.length && !replyRequests.length && !intentRequests.length && skipped.length);
}


function stringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (typeof value === "string") return value.split(",").map((item) => item.trim()).filter(Boolean);
  return [];
}


function isScrollElementNearBottom(node: HTMLElement, thresholdPx = 96): boolean {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= thresholdPx;
}


function channelMessageText(row: Record<string, unknown>): string {
  return recordString(row, "text") || recordString(row, "message") || recordString(row, "summary");
}


function channelMessageRole(row: Record<string, unknown>): string {
  const role = recordString(row, "role");
  if (role) return role;
  const memberId = recordString(row, "member_id");
  if (memberId && memberId !== "operator") return "assistant";
  return "user";
}


function channelDetailHasMessage(detail: ChannelDetail | null, pending: PendingChannelMessage): boolean {
  const rows = [
    ...((detail?.messages ?? []) as Record<string, unknown>[]),
    ...((detail?.recent_messages ?? []) as unknown as Record<string, unknown>[]),
  ];
  return rows.some((row) =>
    recordString(row, "message_id") === pending.id
    || (
      recordString(row, "thread_id", "main") === pending.threadId
      && channelMessageRole(row) === "user"
      && recordString(row, "member_id", "operator") === "operator"
      && channelMessageText(row) === pending.text
    ),
  );
}


function channelDetailWithPendingMessage(
  detail: ChannelDetail | null,
  selectedChannelId: string,
  pendingMessages: PendingChannelMessage[],
): ChannelDetail | null {
  const pending = pendingMessages.filter((item) => !channelDetailHasMessage(detail, item));
  if (!pending.length) return detail;
  const base = detail ?? ({
    channel_id: selectedChannelId || "ch-zaofu",
    name: selectedChannelId || "ch-zaofu",
    members: [],
    workflow_requests: [],
  } as ChannelDetail);
  const pendingMessageRows: Record<string, unknown>[] = pending.map((item) => ({
    event_id: `local-${item.id}`,
    message_id: item.id,
    thread_id: item.threadId,
    ts: item.ts,
    actor: "web",
    member_id: "operator",
    role: "user",
    source: "web",
    text: item.text,
    mentions: item.targets,
    refs: item.refs ?? {},
  }));
  const pendingRequests = pending.flatMap((item) => (
    item.targets.map((target, index) => ({
      request_id: `local-reply-${item.id}-${index}`,
      event_id: `local-reply-${item.id}-${index}`,
      created_at: item.ts,
      updated_at: item.ts,
      thread_id: item.threadId,
      message_id: item.id,
      member_id: "operator",
      target_member_id: target,
      status: "submitted",
      queue_state: "ready",
      provider: "agent",
      backend: "agent",
      reason: target ? `working for @${target}` : "working",
    }))
  ));
  return {
    ...base,
    messages: [...(base.messages ?? []), ...pendingMessageRows],
    reply_requests: [...(base.reply_requests ?? []), ...pendingRequests],
  };
}


function buildChannelMentionDigests(
  rows: Record<string, unknown>[],
  messages: Record<string, unknown>[],
  memberIds: Set<string>,
): ChannelMentionDigest[] {
  const messageById = new Map<string, Record<string, unknown>>();
  for (const message of messages) {
    const messageId = recordString(message, "message_id") || recordString(message, "event_id");
    if (messageId) messageById.set(messageId, message);
  }
  const groups = new Map<string, ChannelMentionDigest>();
  for (const row of rows) {
    const messageId = recordString(row, "message_id") || recordString(row, "event_id");
    const eventId = recordString(row, "event_id");
    const key = messageId || eventId;
    if (!key) continue;
    const message = messageById.get(messageId);
    const existing = groups.get(key);
    const digest = existing ?? {
      id: key,
      messageId,
      threadId: recordString(row, "thread_id", "main"),
      actor: recordString(row, "member_id") || "operator",
      source: recordString(row, "source") || "web",
      ts: recordString(row, "ts") || recordString(message ?? {}, "ts"),
      targets: [],
      unresolvedTargets: [],
      text: message ? channelMessageText(message) : "",
    };
    const target = recordString(row, "target_member_id");
    const isKnownTarget = target && (!memberIds.size || memberIds.has(target));
    const isSyntheticIndex = /^\d+$/.test(target);
    if (isKnownTarget) {
      if (!digest.targets.includes(target)) digest.targets.push(target);
    } else if (target && !isSyntheticIndex && !digest.unresolvedTargets.includes(target)) {
      digest.unresolvedTargets.push(target);
    }
    groups.set(key, digest);
  }
  return [...groups.values()]
    .filter((item) => item.targets.length || item.unresolvedTargets.length)
    .sort((left, right) => (right.ts || "").localeCompare(left.ts || ""));
}


function channelComposerMarkdownFromElement(root: HTMLElement | null): string {
  if (!root) return "";
  return Array.from(root.childNodes).map(channelComposerNodeToMarkdown).join("").trim();
}


function channelComposerNodeToMarkdown(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (!(node instanceof HTMLElement)) return "";
  const tag = node.tagName.toLowerCase();
  const childText = () => Array.from(node.childNodes).map(channelComposerNodeToMarkdown).join("");
  const content = childText();
  if (tag === "br") return "\n";
  if (tag === "strong" || tag === "b") return content ? `**${content}**` : "";
  if (tag === "em" || tag === "i") return content ? `_${content}_` : "";
  if (tag === "u") return content ? `<u>${content}</u>` : "";
  if (tag === "s" || tag === "strike" || tag === "del") return content ? `~~${content}~~` : "";
  if (tag === "code") return content ? `\`${content}\`` : "";
  if (tag === "a") {
    const href = node.getAttribute("href") || "";
    return href ? `[${content || href}](${href})` : content;
  }
  if (tag === "pre") return content.trim() ? `\`\`\`\n${content.trim()}\n\`\`\`\n` : "";
  if (tag === "blockquote") {
    const quoted = content.trim().split("\n").map((line) => `> ${line}`).join("\n");
    return quoted ? `${quoted}\n` : "";
  }
  if (tag === "li") return `${content.trim()}\n`;
  if (tag === "ul") {
    return Array.from(node.children).map((child) => `- ${channelComposerNodeToMarkdown(child).trim()}`).join("\n") + "\n";
  }
  if (tag === "ol") {
    return Array.from(node.children).map((child, index) => `${index + 1}. ${channelComposerNodeToMarkdown(child).trim()}`).join("\n") + "\n";
  }
  if (tag === "div" || tag === "p") return content ? `${content}\n` : "\n";
  return content;
}


function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}


export function ChannelPage({
  actionReady,
  actionResult,
  channels,
  detail,
  diagnosticsDetail,
  diagnosticsError,
  diagnosticsLoading,
  loadError,
  onAddAgent,
  onClearHistory,
  onDeleteChannel,
  onDrainReplies,
  onGenerateOwnerReport,
  onEvaluatePrd,
  onDecideMemberInvitation,
  onMarkRead,
  onLoadDiagnostics,
  onLoadEarlier,
  onPinMessage,
  onNewChannel,
  onOpenChannel,
  onPostMessage,
  onRemoveMember,
  onResearchAdopt,
  onCancelProviderRun,
  onRequestSynthesis,
  onResolveQuestion,
  onSetMemberPermission,
  onSearchHistory,
  onSetDiscussionMode,
  onStartDiscussion,
  onConsensusDecision,
  onWorkflowRequest,
  onRequestProjectProfile,
  selectedChannelId,
  projectId = "",
  onReviewPlans,
  events = [],
}: {
  actionReady: boolean;
  actionResult: ActionResponse | null;
  channels: ChannelSummary[];
  detail: ChannelDetail | null;
  diagnosticsDetail: ChannelDetail | null;
  diagnosticsError: string | null;
  diagnosticsLoading: boolean;
  events?: RecentEvent[];
  loadError: string | null;
  onAddAgent: () => void;
  onRequestProjectProfile?: (objective: string, threadId: string) => Promise<void>;
  onClearHistory: (threadId: string) => Promise<void>;
  onDeleteChannel: () => Promise<void>;
  onDrainReplies: () => Promise<void>;
  onGenerateOwnerReport: (threadId: string) => Promise<void>;
  onEvaluatePrd: (threadId: string, revision: number, artifactRef: string, artifactDigest: string, role: "full" | "focused", retry: boolean) => Promise<void>;
  onDecideMemberInvitation: (proposal: Record<string, unknown>, decision: "approved" | "rejected") => Promise<void>;
  onMarkRead: (threadId: string) => Promise<void>;
  onLoadDiagnostics: () => Promise<void>;
  onLoadEarlier: () => Promise<void>;
  onPinMessage: (
    messageId: string,
    threadId: string,
    pinned: boolean,
  ) => Promise<void>;
  onNewChannel: () => void;
  onOpenChannel: (channelId: string) => void;
  onPostMessage: (
    text: string,
    refs?: Record<string, unknown>,
    ingress?: ChannelMessageIngress,
  ) => Promise<void>;
  onRemoveMember: (memberId: string) => Promise<void>;
  onResearchAdopt: (payload: Record<string, unknown>) => Promise<void>;
  onCancelProviderRun: (runId: string) => Promise<void>;
  onRequestSynthesis: (threadId: string, targetMemberId?: string, sourceFile?: { ref: string; sha256: string }) => Promise<void>;
  onResolveQuestion: (questionId: string, threadId: string, resolution: string, answer: string) => Promise<void>;
  onSetMemberPermission: (memberId: string, permissionProfile: ChannelPermissionProfile) => Promise<void>;
  onSearchHistory: (query: string, threadId?: string) => Promise<ChannelHistorySearchResult>;
  onSetDiscussionMode: (threadId: string, mode: string, defaultResponderId?: string) => Promise<void>;
  onStartDiscussion: (threadId: string, message: string, messageId: string, mode: string, restart: boolean) => Promise<void>;
  onConsensusDecision: (decision: "confirm" | "block", threadId: string, artifactRef: string, artifactDigest: string, blocker?: string, acceptReadinessRisk?: boolean) => Promise<void>;
  onWorkflowRequest: (taskId: string, reason: string, threadId: string) => Promise<void>;
  selectedChannelId: string;
  projectId?: string;
  onReviewPlans: () => void;
}) {
  const composerRef = useRef<HTMLDivElement | null>(null);
  const [profileObjective, setProfileObjective] = useState("");
  const [activeChannelThreadId, setActiveChannelThreadId] = useChannelThreadSelection(projectId || "", selectedChannelId, detail);
  const { composerText, setComposerText, composerAttachments, setComposerAttachments,
    composerRefs, setComposerRefs,
    retryClientMessageId, setRetryClientMessageId, composerError, setComposerError, currentDraftKey,
  } = useChannelComposerDraft(projectId || "", selectedChannelId, activeChannelThreadId, composerRef);
  const [postingCount, setPostingCount] = useState(0);
  const evaluationSourceDocument = composerRefs.evaluation_source_document === true;
  function setEvaluationSourceDocument(value: boolean) {
    setComposerRefs((current) => ({...current, evaluation_source_document: value}));
  }
  const [workflowDraft, setWorkflowDraft] = useState({ taskId: "", reason: "" });
  const [controlsBusy, setControlsBusy] = useState(false);
  const [controlError, setControlError] = useState("");
  const [researchActionBusyId, setResearchActionBusyId] = useState("");
  const [drawer, setDrawer] = useState<ChannelDrawerKey | null>(null);
  const [channelSwitcherOpen, setChannelSwitcherOpen] = useState(false);
  const [channelSearch, setChannelSearch] = useState("");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyResult, setHistoryResult] = useState<ChannelHistorySearchResult | null>(null);
  const [historySearching, setHistorySearching] = useState(false);
  const [historyLoadingEarlier, setHistoryLoadingEarlier] = useState(false);
  const [historySearchOpen, setHistorySearchOpen] = useState(false);
  const [formattingOpen, setFormattingOpen] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [replySelection, setReplySelection] = useState<{ scope: string; message: AgentSessionMessage } | null>(null);
  const replyScope = JSON.stringify([projectId, selectedChannelId, activeChannelThreadId]);
  const replyTarget = replySelection?.scope === replyScope ? replySelection.message : null;
  function setReplyTarget(message: AgentSessionMessage | null, threadId = activeChannelThreadId) {
    setReplySelection(message ? { scope: JSON.stringify([projectId, selectedChannelId, threadId]), message } : null);
  }
  const [pendingChannelMessages, setPendingChannelMessages] = useState<PendingChannelMessage[]>([]);
  const [channelSplitThreadId, setChannelSplitThreadId] = useState("");
  const [channelPinnedToBottom, setChannelPinnedToBottom] = useState(true);
  const [channelHasNewBelow, setChannelHasNewBelow] = useState(false);
  const [ownerQuestionsOpen, setOwnerQuestionsOpen] = useState(false);
  const [decisionQueueOpen, setDecisionQueueOpen] = useState(false);
  const [questionSourceSelection, setQuestionSourceSelection] = useState<{ id: string; sourceThreadId: string } | null>(null);
  const [memberSearch, setMemberSearch] = useState("");
  const [memberMenuId, setMemberMenuId] = useState("");
  const [memberProfileId, setMemberProfileId] = useState("");
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  const channelTimelineRef = useRef<HTMLDivElement | null>(null);
  const historyPrependAnchorRef = useRef<{ height: number; top: number } | null>(null);
  const historyInputRef = useRef<HTMLInputElement | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const autoOpenedQuestionnaireRef = useRef("");
  const visibleChannels = channels.length
    ? channels
    : [{ channel_id: "ch-zaofu", name: "# zaofu", members: [], workflow_requests: [] } as ChannelSummary];
  const activeChannel = detail ?? visibleChannels.find((channel) => channelIdOf(channel) === selectedChannelId) ?? null;
  const conversationDetail = useMemo(
    () => channelDetailWithPendingMessage(detail, selectedChannelId, pendingChannelMessages.filter((item) => item.channelId === selectedChannelId && item.projectId === (projectId || ""))),
    [detail, pendingChannelMessages, selectedChannelId, projectId],
  );
  const posting = postingCount > 0;
  useEffect(() => {
    setPendingChannelMessages((current) => current.filter((item) => !channelDetailHasMessage(detail, item)));
  }, [detail]);
  // Live-stream fold (operator report 2026-07-16): member replies stream
  // token deltas over the SSE live bus; buffer this channel's rows (the App
  // event window is capped at 120 — a long reply outlives it) and fold them
  // into each rebuilt conversation so text grows while the member types.
  const [liveStreamBuffer, setLiveStreamBuffer] = useState<RecentEvent[]>([]);
  useEffect(() => {
    setLiveStreamBuffer([]);
  }, [selectedChannelId]);
  useEffect(() => {
    const scoped = channelLiveStreamRows(events, selectedChannelId);
    if (!scoped.length) return;
    setLiveStreamBuffer((current) => {
      // Compact instead of dropping oldest: a very long reply must not lose
      // its opening tokens (delta rows merge per run+kind, tools pass through).
      const merged = mergeEventsByIdentity(current, scoped);
      return compactChannelLiveRows(merged, 800);
    });
  }, [events, selectedChannelId]);
  const channelConversation = useMemo(
    () => foldChannelLiveStream(
      buildChannelConversation(conversationDetail, selectedChannelId, activeChannelThreadId),
      channelLiveStreamRows(liveStreamBuffer, selectedChannelId),
      selectedChannelId,
    ),
    [activeChannelThreadId, conversationDetail, liveStreamBuffer, selectedChannelId],
  );
  const discussionBand = useMemo(() => {
    const attention = selectChannelDiscussionAttention(
      detail,
      activeChannelThreadId,
    );
    if (!attention) return null;
    const presentation = presentChannelDiscussionAttention(attention);
    const threadId = channelThreadId(attention.thread_id || activeChannelThreadId || "main");
    const questions = Object.values(detail?.open_questions ?? {})
      .map((item) => item as Record<string, unknown>);
    const threadQuestions = questions.filter((item) => (
      channelThreadId(String(item.thread_id || "main")) === threadId
    ));
    const openQuestions = threadQuestions
      .filter((item) => (
        String(item.status || "") === "open"
      ))
      .map((item) => ({
        id: String(item.question_id || ""),
        threadId: channelThreadId(String(item.thread_id || "main")),
        sourceThreadId: String(item.thread_id || "main"),
        kind: String(item.kind || ""),
        text: String(item.question || ""),
        category: String(item.category || ""),
        askedBy: String(item.asked_by || ""),
      }))
      .filter((item) => item.text);
    const questionConsolidationPending = (
      attention.question_consolidation_status !== undefined
      && attention.question_consolidation_status !== "open"
    ) || attention.attention_kind === "consolidating";
    const ownerQuestions: ChannelOwnerQuestion[] = (questionConsolidationPending
      ? []
      : openOwnerQuestionnaire(detail, threadId))
      .flatMap((item) => {
        const id = String(item.question_id || "").trim();
        const text = String(item.question || "").trim();
        if (!id || !text) return [];
        const options = asRecordArray(item.options).flatMap((option) => {
          const optionId = textValue(option.id).trim();
          const label = textValue(option.label).trim();
          if (!optionId || !label) return [];
          return [{
            id: optionId,
            label,
            description: textValue(option.description),
            recommended: option.recommended === true,
          }];
        });
        const recommendedAnswer = String(item.recommended_answer || "").trim();
        const whyItMatters = String(item.why_it_matters || "").trim();
        const askedBy = String(item.asked_by || "").trim();
        return [{
          id,
          header: [
            String(item.priority || "p1").toUpperCase(),
            String(item.kind || "owner decision").replaceAll("_", " "),
          ].join(" / "),
          question: text,
          description: [
            whyItMatters,
            !options.length && recommendedAnswer
              ? `Recommended: ${recommendedAnswer}`
              : "",
          ].filter(Boolean).join(" "),
          options,
          allowOther: item.allow_other !== false,
          inputPlaceholder: recommendedAnswer
            ? `Recommended: ${recommendedAnswer}`
            : "Answer this owner decision.",
          required: false,
          skipLabel: "Out of scope",
          askedBy,
          priority: String(item.priority || "p1").trim().toLowerCase(),
          sourceThreadId: String(item.thread_id || attention.thread_id || activeChannelThreadId || "main"),
          sourceMessageId: String(item.source_message_id || item.message_id || "").trim(),
          sourceRunId: String(item.source_run_id || item.run_id || "").trim(),
        }];
      });
    return {
      attention,
      presentation,
      threadId,
      openQuestions,
      ownerQuestions,
      questionConsolidationPending,
      questionGraphDigest: detail?.question_graph_digests?.[threadId]
        || Object.entries(detail?.question_graph_digests ?? {})
          .find(([key]) => channelThreadId(key) === threadId)?.[1]
        || "",
    };
  }, [activeChannelThreadId, detail]);
  useEffect(() => {
    setOwnerQuestionsOpen(false);
    setDecisionQueueOpen(false);
    setQuestionSourceSelection(null);
    autoOpenedQuestionnaireRef.current = "";
  }, [activeChannelThreadId, selectedChannelId]);
  useEffect(() => {
    if (discussionBand?.questionConsolidationPending) {
      setOwnerQuestionsOpen(false);
    }
  }, [discussionBand?.questionConsolidationPending]);
  useEffect(() => {
    if (!discussionBand?.ownerQuestions.length) return;
    const attentionKind = discussionBand.attention.attention_kind ?? (
      discussionBand.attention.owner_question_count > 0 ? "question" : "none"
    );
    if (
      attentionKind !== "question"
      || discussionBand.attention.active_agent_count > 0
    ) return;
    const revision = discussionBand.questionGraphDigest || discussionBand.ownerQuestions
      .map((question) => question.id)
      .sort()
      .join(",");
    const key = [selectedChannelId, discussionBand.threadId, revision].join(":");
    if (autoOpenedQuestionnaireRef.current === key) return;
    autoOpenedQuestionnaireRef.current = key;
    setOwnerQuestionsOpen(true);
  }, [discussionBand, selectedChannelId]);
  const ownerQuestionAttachments = useMemo(() => {
    const byRun = new Map<string, ChannelOwnerQuestion[]>();
    const unassigned: ChannelOwnerQuestion[] = [];
    if (!ownerQuestionsOpen || !discussionBand?.ownerQuestions.length) {
      return { byRun, unassigned };
    }
    const activeThread = channelConversation.threads.find((thread) => thread.id === activeChannelThreadId)
      ?? channelConversation.threads[0];
    const runRows = activeThread?.turns.flatMap((turn) => turn.runs.map((run) => ({ run, turn }))) ?? [];
    for (const question of discussionBand.ownerQuestions) {
      const target = questionTargetRun(question, runRows);
      if (!target) {
        unassigned.push(question);
        continue;
      }
      const current = byRun.get(target.run.id) ?? [];
      current.push(question);
      byRun.set(target.run.id, current);
    }
    return { byRun, unassigned };
  }, [activeChannelThreadId, channelConversation, discussionBand, ownerQuestionsOpen]);
  const decisionReceipts = useMemo(() => channelDecisionReceipts(detail, channelConversation, activeChannelThreadId),
    [detail, channelConversation, activeChannelThreadId]);
  const decisionQueueItems: ChannelDecisionItem[] = discussionBand?.ownerQuestions.map((question) => ({
    id: question.id,
    priority: question.priority,
    question: question.question,
    askedBy: question.askedBy,
    sourceThreadId: question.sourceThreadId,
    sourceRunId: question.sourceRunId,
    sourceAvailable: [...ownerQuestionAttachments.byRun.values()].flat().includes(question),
  })) ?? [];
  function focusOwnerQuestions() {
    setOwnerQuestionsOpen(true);
    setDecisionQueueOpen(false);
    setDrawer(null);
    window.requestAnimationFrame(() => {
      const node = channelTimelineRef.current?.querySelector<HTMLElement>(".channel-question-card");
      node?.scrollIntoView({ behavior: "smooth", block: "start" });
      node?.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
      setChannelPinnedToBottom(false);
    });
  }
  function focusOwnerQuestion(question: { id: string; sourceThreadId: string }) {
    setOwnerQuestionsOpen(true);
    setDecisionQueueOpen(false);
    setQuestionSourceSelection(question);
    window.requestAnimationFrame(() => {
      const selector = `.channel-question-card[data-question-id="${CSS.escape(question.id)}"][data-question-thread="${CSS.escape(question.sourceThreadId)}"]`;
      const card = channelTimelineRef.current?.querySelector<HTMLDetailsElement>(selector);
      if (!card) { setDrawer("diagnostics"); return; }
      setDrawer(null);
      card?.scrollIntoView({ behavior: "smooth", block: "center" });
      card?.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
      setChannelPinnedToBottom(false);
    });
  }
  const selectedSourceQuestion = discussionBand?.ownerQuestions.find((question) => (
    question.id === questionSourceSelection?.id && question.sourceThreadId === questionSourceSelection.sourceThreadId
  ));
  useEffect(() => {
    if (!selectedSourceQuestion || drawer !== "diagnostics" || historyLoadingEarlier) return;
    if (![...ownerQuestionAttachments.byRun.values()].flat().includes(selectedSourceQuestion)) return;
    // Source pagination has completed; only the explicitly selected question
    // is allowed to move focus from Diagnostics back to the Agent reply.
    const frame = requestAnimationFrame(() => focusOwnerQuestion(selectedSourceQuestion));
    return () => cancelAnimationFrame(frame);
  }, [selectedSourceQuestion, ownerQuestionAttachments, drawer, historyLoadingEarlier]);

  function renderOwnerQuestionCard(
    questions: ChannelOwnerQuestion[],
    testId: string,
    className: string,
    defaultOpen = false,
  ) {
    if (!questions.length) return null;
    return (
      <div className={className} data-testid={testId}>
        {questions.map((question) => <ChannelQuestionCard key={JSON.stringify([question.sourceThreadId, question.id])}
          sourceThreadId={question.sourceThreadId} defaultOpen={defaultOpen}
          busy={controlsBusy}
          disabled={!actionReady}
          onDiscuss={(question) => {
            quoteDiscussionQuestion(question.question);
            window.setTimeout(() => focusComposer(), 0);
          }}
          onSubmit={(answers: AskUserQuestionAnswer[]) => {
            void runControl(async () => {
              for (const answer of answers) {
                await onResolveQuestion(
                  answer.questionId,
                  question.sourceThreadId,
                  answer.skipped ? "out_of_scope" : "answered",
                  answer.answer,
                );
              }
            });
          }}
          question={question}
          requestId={[
            selectedChannelId,
            question.sourceThreadId,
            discussionBand?.questionGraphDigest || "",
            JSON.stringify([question.question, question.options]),
          ].join(":")}
        />)}
      </div>
    );
  }
  function quoteDiscussionQuestion(text: string) {
    insertComposerText(`Re “${text}”: `);
  }
  function renderChannelConsensusControl() {
    if (recordString(threadConsensus, "artifact_ref") !== flow.artifactRef
      || recordString(threadConsensus, "artifact_digest").replace(/^sha256:/, "") !== flow.artifactDigest.replace(/^sha256:/, "")
      || Number(threadConsensus.prd_revision || 0) !== flow.revision) return null;
    if (!recordString(threadConsensus, "artifact_ref") || recordString(threadConsensus, "reached_event_id")) {
      return null;
    }
    return <ChannelConsensusControl
      key={JSON.stringify([projectId, selectedChannelId, controlThreadId, flow.artifactRef, flow.artifactDigest,
        flow.revision, threadConsensus.readiness_ref, threadConsensus.readiness_digest])}
      selection={{ projectId, channelId: selectedChannelId, threadId: controlThreadId,
        revision: flow.revision, digest: flow.artifactDigest }}
      artifactRef={flow.artifactRef} consensus={threadConsensus} actionReady={actionReady} busy={controlsBusy}
      onDecision={(decision, blocker, acceptRisk) => runControl(() => onConsensusDecision(
        decision, controlThreadId, flow.artifactRef, flow.artifactDigest, blocker, acceptRisk,
      ))}
    />;
  }
  const channelScrollSignature = agentConversationScrollSignature(
    channelConversation,
    activeChannelThreadId,
    [],
  );
  const hasPendingOwnerQuestions = decisionQueueItems.length > 0;
  const channelDetailLoaded = Boolean(detail);
  useEffect(() => {
    if (!channelDetailLoaded) return;
    setChannelPinnedToBottom(!hasPendingOwnerQuestions);
    setChannelHasNewBelow(false);
    if (!hasPendingOwnerQuestions) scrollElementToBottom(channelTimelineRef.current);
  }, [activeChannelThreadId, channelDetailLoaded, hasPendingOwnerQuestions, selectedChannelId]);
  useEffect(() => {
    const node = channelTimelineRef.current;
    if (!node) return;
    if (historyPrependAnchorRef.current) return;
    // A pending decision is an explicit attention state. Never let a late
    // conversation rebuild or a transient near-bottom measurement pull the
    // operator away from the current position while it is visible.
    if (hasPendingOwnerQuestions) {
      setChannelHasNewBelow(!isScrollElementNearBottom(node));
      return;
    }
    if (channelPinnedToBottom || isScrollElementNearBottom(node)) {
      scrollElementToBottom(node);
      setChannelHasNewBelow(false);
      return;
    }
    setChannelHasNewBelow(true);
  }, [channelPinnedToBottom, channelScrollSignature, hasPendingOwnerQuestions]);
  useEffect(() => {
    if ((drawer === "diagnostics" || drawer === "reports") && !diagnosticsLoading && !diagnosticsError) void onLoadDiagnostics();
  }, [drawer, detail?.seq, diagnosticsLoading, diagnosticsError, onLoadDiagnostics, selectedChannelId]);
  useEffect(() => {
    setHistoryQuery("");
    setHistoryResult(null);
  }, [selectedChannelId]);
  const channelHasMultipleThreads = channelConversation.threads.length > 1;
  const activeChannelName = channelNameOf(activeChannel);
  const activeChannelLabel = activeChannelName.replace(/^#\s*/, "");
  const normalizedChannelSearch = channelSearch.trim().toLowerCase();
  const filteredChannels = normalizedChannelSearch
    ? visibleChannels.filter((channel) => {
      const id = channelIdOf(channel).toLowerCase();
      const name = channelNameOf(channel).toLowerCase();
      return id.includes(normalizedChannelSearch) || name.includes(normalizedChannelSearch);
    })
    : visibleChannels;
  const recentChannels = [
    ...visibleChannels.filter((channel) => channelIdOf(channel) === selectedChannelId),
    ...visibleChannels.filter((channel) => channelIdOf(channel) !== selectedChannelId),
  ].slice(0, 3);
  // Fresh conversation facts override cached diagnostics; absent sidecars remain available.
  const workspaceDetail = diagnosticsDetail ? { ...diagnosticsDetail, ...detail } as ChannelDetail : detail;
  const members = detail?.members ?? workspaceDetail?.members ?? [];
  const mentionChoices = useMemo(() => {
    const mentionableMembers = members.filter((member) => canMentionMember(member));
    return [
      {
        id: "all",
        token: "ALL",
        label: "ALL",
        meta: `${mentionableMembers.length} members`,
        avatar: "@",
        search: "all everyone 全部 所有人",
      },
      ...mentionableMembers.map((member) => {
        const memberId = recordString(member, "member_id") || memberDisplayName(member);
        const label = memberDisplayName(member);
        const meta = [memberRoleLabel(member), memberProvider(member)].filter(Boolean).join(" · ");
        return {
          id: memberId,
          token: memberId,
          label,
          meta,
          avatar: initials(label),
          search: [
            memberId,
            label,
            memberRoleLabel(member),
            memberProvider(member),
            recordString(member, "backend"),
            recordString(member, "provider"),
          ].join(" ").toLowerCase(),
        };
      }),
    ];
  }, [members]);
  const filteredMentionChoices = useMemo(() => {
    const query = normalizeMentionQuery(mentionQuery);
    const rows = query
      ? mentionChoices.filter((choice) => choice.search.includes(query) || normalizeMentionQuery(choice.token).includes(query))
      : mentionChoices;
    return rows.slice(0, 8);
  }, [mentionChoices, mentionQuery]);
  const activeMentionIndex = Math.min(mentionActiveIndex, Math.max(filteredMentionChoices.length - 1, 0));
  const boundMembers = members.filter((member) =>
    recordString(member, "worker_session_id") ||
    recordString(member, "backing_worker_session_id") ||
    recordString(member, "provider_session_id") ||
    Object.keys((member["workflow_role_binding"] as Record<string, unknown> | undefined) ?? {}).length > 0
  );
  const messages = detail?.messages ?? detail?.recent_messages ?? [];
  const controlThreadId = channelSourceThreadId(workspaceDetail, activeChannelThreadId || "main");
  const baseFlow = channelFlow(workspaceDetail, controlThreadId);
  const prdSelection = { projectId, channelId: selectedChannelId, threadId: controlThreadId,
    revision: baseFlow.revision, digest: baseFlow.artifactDigest };
  const deliveryVersion = events.filter((event) => /^(kanban\.agent\.(plan|proposal|action|turn)|operator\.action|web\.action|task\.created|workflow\.|run\.)/.test(event.type)).at(-1)?.id || "";
  const delivery = useChannelDelivery(prdSelection, deliveryVersion);
  const flow = channelFlow(workspaceDetail, controlThreadId, delivery.data);
  const canonicalPrd = { ...flow.canonical, ready: flow.ready };
  const threadConsensus = workspaceDetail?.consensus?.[controlThreadId] ?? {};
  const workflowRequests = workspaceDetail?.workflow_requests ?? [];
  const replyRequests = diagnosticsDetail?.reply_requests ?? detail?.reply_requests ?? [];
  const conversationReplyRequests = detail?.reply_requests ?? [];
  const contextPacks = diagnosticsDetail?.context_packs ?? detail?.context_packs ?? [];
  const handoffs = workspaceDetail?.handoffs ?? [];
  const stateUpdates = diagnosticsDetail?.state_updates ?? detail?.state_updates ?? [];
  const ownerReports = workspaceDetail?.owner_reports ?? [];
  const automationReports = workspaceDetail?.automation_reports ?? [];
  const resultReceipts = workspaceDetail?.result_receipts ?? [];
  const discussionMode = recordString(workspaceDetail?.discussion ?? {}, "mode", "conversation");
  const currentDiscussion = workspaceDetail?.discussions?.[controlThreadId] ?? {};
  const latestThreadRequirement = selectDiscussionStartSource(
    messages.map((message) => message as unknown as Record<string, unknown>),
    controlThreadId,
    Boolean(discussionBand?.attention?.can_restart),
    recordString(currentDiscussion, "requirement_message_id"),
  );
  const latestThreadRequirementText = latestThreadRequirement
    ? channelMessageText(latestThreadRequirement)
    : "";
  const latestThreadRequirementId = latestThreadRequirement
    ? recordString(latestThreadRequirement, "message_id")
    : "";
  const currentDiscussionState = recordString(
    currentDiscussion,
    "state",
    "idle",
  );
  const discussionControls = channelDiscussionControlPolicy(
    discussionBand?.attention ?? null,
    currentDiscussionState,
    Boolean(latestThreadRequirementText),
  );
  const defaultResponderId = recordString(workspaceDetail?.discussion ?? {}, "default_responder_id");
  const moderatorMemberId = (
    workspaceDetail?.leader_member_id
    || recordString(workspaceDetail?.discussion ?? {}, "moderator_member_id")
    || recordString(workspaceDetail?.discussion ?? {}, "leader_member_id")
  );
  const moderatorMember = members.find((member) => (
    recordString(member, "member_id") === moderatorMemberId
  ));
  const moderatorLabel = moderatorMember
    ? memberDisplayName(moderatorMember)
    : moderatorMemberId || "未配置";
  const defaultResponderMember = members.find((member) => (
    recordString(member, "member_id") === defaultResponderId
  ));
  const defaultResponderLabel = defaultResponderMember
    ? memberDisplayName(defaultResponderMember)
    : defaultResponderId || "未配置";
  const sameModeratorAndResponder = Boolean(
    moderatorMemberId && defaultResponderId && moderatorMemberId === defaultResponderId,
  );
  const replyCapableMembers = members.filter((member) => {
    const status = recordString(member, "status");
    const memberType = recordString(member, "member_type");
    const permissions = stringArray(member["permissions"]);
    return status !== "removed" && status !== "suspended" && memberType !== "observer" && (!permissions.length || permissions.includes("message"));
  });
  const channelMemberIds = new Set(
    members.map((member) => recordString(member, "member_id")).filter(Boolean),
  );
  const replyHealth = channelReplyHealth(conversationReplyRequests, controlThreadId);
  const pendingReplyRows = replyHealth.active;
  const failedReplyRows = replyHealth.failed;
  const currentFailedReplyRows = failedReplyRows.filter((row) => !row.message_id || row.message_id === latestThreadRequirementId);
  const attentionReplyRows = [...pendingReplyRows, ...failedReplyRows];
  const pendingReplies = pendingReplyRows.length;
  const mentionRows = (detail?.mentions_detected ?? []).filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  const mentionDigests = buildChannelMentionDigests(mentionRows, messages as Record<string, unknown>[], channelMemberIds);
  const contextRejections = discussionBand?.attention.context_rejections ?? [];
  const unsettledCrossReviews = (detail?.cross_reviews ?? diagnosticsDetail?.cross_reviews ?? [])
    .filter((row) => recordString(row, "thread_id", "main") === controlThreadId
      && (row.status !== "completed" || row.outcome === "unresolved"));
  const activityCount = pendingReplies + failedReplyRows.length + mentionDigests.length + contextRejections.length + unsettledCrossReviews.length;
  const chatConversation = useMemo(() => channelChatConversation(
    channelConversation,
    new Set([...ownerQuestionAttachments.byRun.keys(), ...decisionReceipts.byRun.keys()]),
  ), [channelConversation, ownerQuestionAttachments, decisionReceipts]);
  const [previewFile, setPreviewFile] = useState<PreviewItem | null>(null);
  const drawerTitles: Record<ChannelDrawerKey, string> = {
    file: "File preview",
    members: "Members",
    attention: "Activity",
    diagnostics: "诊断",
    ...FLOW_LABELS,
    reports: "Reports",
    settings: "更多 · 群组设置",
  };
  const formatButtons: Array<{ action: ComposerFormatAction; label: string; icon: LucideIcon }> = [
    { action: "bold", label: "Bold", icon: Bold },
    { action: "italic", label: "Italic", icon: Italic },
    { action: "underline", label: "Underline", icon: Underline },
    { action: "strike", label: "Strikethrough", icon: Strikethrough },
    { action: "link", label: "Link", icon: Link },
    { action: "ordered-list", label: "Ordered list", icon: ListOrdered },
    { action: "unordered-list", label: "Bulleted list", icon: List },
    { action: "quote", label: "Quote", icon: Quote },
    { action: "inline-code", label: "Inline code", icon: Code },
    { action: "code-block", label: "Code block", icon: SquareCode },
  ];
  useEffect(() => {
    setChannelSplitThreadId("");
    setMemberMenuId("");
    setMemberProfileId("");
    setMemberSearch("");
    setMentionOpen(false);
    setMentionQuery("");
    setHistorySearchOpen(false);
    setHistoryLoadingEarlier(false);
    historyPrependAnchorRef.current = null;
  }, [selectedChannelId, projectId]);
  async function loadEarlierMessages() {
    if (historyLoadingEarlier || !detail?.has_more) return;
    const node = channelTimelineRef.current;
    if (node) {
      historyPrependAnchorRef.current = {
        height: node.scrollHeight,
        top: node.scrollTop,
      };
    }
    setHistoryLoadingEarlier(true);
    try {
      await onLoadEarlier();
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const anchor = historyPrependAnchorRef.current;
          const timeline = channelTimelineRef.current;
          if (anchor && timeline) {
            timeline.scrollTop = anchor.top + timeline.scrollHeight - anchor.height;
          }
          historyPrependAnchorRef.current = null;
          setChannelHasNewBelow(false);
        });
      });
    } finally {
      setHistoryLoadingEarlier(false);
    }
  }
  function toggleDrawer(nextDrawer: ChannelDrawerKey) {
    setDrawer((current) => current === nextDrawer ? null : nextDrawer);
  }
  function channelMemberCount(channel: ChannelSummary): number {
    return channel.members?.length ?? 0;
  }
  function channelAttentionCount(channel: ChannelSummary): number {
    return (
      Number(channel.pending_reply_count ?? 0) +
      Number(channel.unread_count ?? 0) +
      (channel.attention?.length ?? 0) +
      (channel.running_replies?.length ?? 0) +
      (channel.queued_replies?.length ?? 0)
    );
  }
  function selectChannel(channelId: string) {
    onOpenChannel(channelId);
    setChannelSwitcherOpen(false);
    setChannelSearch("");
    setEmojiOpen(false);
    setMentionOpen(false);
  }
  function renderChannelSwitchRow(channel: ChannelSummary) {
    const channelId = channelIdOf(channel);
    const isActive = channelId === selectedChannelId;
    const memberCount = channelMemberCount(channel);
    const attention = channelAttentionCount(channel);
    return (
      <button
        className={`channel-switch-row ${isActive ? "active" : ""}`}
        key={channelId}
        type="button"
        onClick={() => selectChannel(channelId)}
      >
        <Hash size={15} />
        <span>{channelNameOf(channel).replace(/^#\s*/, "")}</span>
        <span className="channel-switch-meta">
          {memberCount ? <span>{memberCount}</span> : null}
          {attention ? <span className="channel-attention-badge">{attention}</span> : null}
        </span>
      </button>
    );
  }
  function memberDisplayName(member: Record<string, unknown>): string {
    return channelMemberLabel(member);
  }
  function memberProvider(member: Record<string, unknown>): string {
    return recordString(member, "provider") || recordString(member, "backend") || recordString(member, "member_type") || "agent";
  }
  function memberRoleLabel(member: Record<string, unknown>): string {
    return recordString(member, "channel_role") || recordString(member, "role") || recordString(member, "member_type") || "member";
  }
  function memberPresence(member: Record<string, unknown>): string {
    return recordString(member, "presence") || recordString(member, "status") || "observed";
  }
  function memberCapabilitySummary(member: Record<string, unknown>): string {
    const capabilities = recordValue(member.provider_capabilities);
    if (!capabilities) return "-";
    const stream = capabilities.supports_stream ? "stream" : "";
    const resume = capabilities.supports_resume ? "resume" : "";
    const interrupt = capabilities.supports_interrupt ? "interrupt" : "";
    const cost = recordString(capabilities, "cost_class");
    return [stream, resume, interrupt, cost].filter(Boolean).join(" / ") || "-";
  }
  function memberSearchText(member: Record<string, unknown>): string {
    return [
      recordString(member, "member_id"),
      memberDisplayName(member),
      memberProvider(member),
      memberRoleLabel(member),
      memberPresence(member),
      recordString(member, "visibility_profile"),
      recordString(member, "permission_profile"),
      recordString(member, "status"),
    ].join(" ").toLowerCase();
  }
  function initials(value: string): string {
    return value.trim().slice(0, 1).toUpperCase() || "#";
  }
  function focusComposer() {
    composerRef.current?.focus();
  }
  function composerPlainText(): string {
    return (composerRef.current?.innerText ?? composerText).replace(/\n$/, "");
  }
  function syncComposerFromEditor(): string {
    const next = composerPlainText();
    setComposerText(next);
    return next;
  }
  function composerCaretOffset(): number {
    const editor = composerRef.current;
    const selection = window.getSelection();
    if (!editor || !selection?.rangeCount) return composerPlainText().length;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.endContainer)) return composerPlainText().length;
    const preRange = range.cloneRange();
    preRange.selectNodeContents(editor);
    preRange.setEnd(range.endContainer, range.endOffset);
    return preRange.toString().length;
  }
  function setComposerCaretOffset(offset: number) {
    const editor = composerRef.current;
    if (!editor) return;
    const bounded = Math.max(0, offset);
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    let current = walker.nextNode();
    let remaining = bounded;
    const range = document.createRange();
    while (current) {
      const length = current.textContent?.length ?? 0;
      if (remaining <= length) {
        range.setStart(current, remaining);
        range.collapse(true);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        return;
      }
      remaining -= length;
      current = walker.nextNode();
    }
    range.selectNodeContents(editor);
    range.collapse(false);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }
  function replaceComposerRange(start: number, end: number, text: string) {
    const editor = composerRef.current;
    if (!editor) {
      const next = `${composerText.slice(0, start)}${text}${composerText.slice(end)}`;
      setComposerText(next);
      updateMentionState(next, start + text.length);
      return;
    }
    editor.focus();
    setComposerCaretOffset(start);
    const selection = window.getSelection();
    if (selection?.rangeCount) {
      const deleteLength = Math.max(0, end - start);
      if (deleteLength) {
        const plain = composerPlainText();
        editor.textContent = `${plain.slice(0, start)}${plain.slice(end)}`;
        setComposerCaretOffset(start);
      }
    }
    document.execCommand("insertText", false, text);
    const next = syncComposerFromEditor();
    updateMentionState(next, start + text.length);
  }
  function clearComposerEditor() {
    const editor = composerRef.current;
    if (editor) editor.innerHTML = "";
    setComposerText("");
  }
  function restoreComposerEditor(html: string, plain: string) {
    const editor = composerRef.current;
    if (editor) {
      editor.innerHTML = html || "";
      window.requestAnimationFrame(() => {
        editor.focus();
        setComposerCaretOffset(composerPlainText().length);
      });
    }
    setComposerText(plain);
  }
  function openHistorySearch() {
    setHistorySearchOpen(true);
    window.requestAnimationFrame(() => historyInputRef.current?.focus());
  }
  function canMentionMember(member: Record<string, unknown>): boolean {
    const memberId = recordString(member, "member_id");
    const status = recordString(member, "status").toLowerCase();
    const memberType = recordString(member, "member_type");
    const permissions = Array.isArray(member.permissions) ? member.permissions.map(String) : [];
    if (!memberId || ["removed", "suspended", "rejected", "failed"].includes(status)) return false;
    if (["readonly-reviewer", "observer"].includes(memberType)) return false;
    return !permissions.length || permissions.includes("message");
  }
  function targetMembersForMessage(text: string): string[] {
    const mentionableMembers = members.filter((member) => canMentionMember(member));
    const tokens = [...text.matchAll(/@([^\s,，。；;:：)）\]]+)/g)]
      .map((match) => normalizeMentionQuery(match[1] || ""))
      .filter(Boolean);
    if (tokens.includes("all")) {
      return mentionableMembers
        .map((member) => recordString(member, "member_id"))
        .filter(Boolean);
    }
    const targets = new Set<string>();
    for (const token of tokens) {
      const match = mentionableMembers.find((member) => {
        const memberId = recordString(member, "member_id");
        return (
          normalizeMentionQuery(memberId) === token
          || normalizeMentionQuery(memberDisplayName(member)) === token
        );
      });
      if (match) targets.add(recordString(match, "member_id"));
    }
    return [...targets];
  }
  function normalizeMentionQuery(value: string): string {
    return value.trim().toLowerCase().replace(/^@/, "").replace(/[^a-z0-9]+/g, "");
  }
  function currentMentionContext(text: string, caret: number): { start: number; end: number; query: string } | null {
    const boundedCaret = Math.max(0, Math.min(caret, text.length));
    const at = text.lastIndexOf("@", Math.max(0, boundedCaret - 1));
    if (at < 0) return null;
    const before = at > 0 ? text[at - 1] : "";
    if (before && /[\w@./-]/.test(before)) return null;
    const token = text.slice(at + 1, boundedCaret);
    if (/\s/.test(token) || !/^[A-Za-z0-9_.-]*$/.test(token)) return null;
    return { start: at, end: boundedCaret, query: token };
  }
  function updateMentionState(text: string, caret: number) {
    const context = currentMentionContext(text, caret);
    if (!context) {
      setMentionOpen(false);
      return;
    }
    setMentionQuery(context.query);
    setMentionActiveIndex(0);
    setMentionOpen(true);
    setEmojiOpen(false);
  }
  function openMentionMenu() {
    setEmojiOpen(false);
    setFormattingOpen(false);
    const text = composerPlainText();
    const caret = composerCaretOffset();
    const context = currentMentionContext(text, caret);
    if (context) {
      setMentionQuery(context.query);
      setMentionActiveIndex(0);
      setMentionOpen(true);
      window.setTimeout(() => focusComposer(), 0);
      return;
    }
    setMentionQuery("");
    setMentionActiveIndex(0);
    setMentionOpen(true);
    replaceComposerRange(caret, caret, "@");
  }
  function selectMentionChoice(choice: { token: string }) {
    const text = composerPlainText();
    const caret = composerCaretOffset();
    const context = currentMentionContext(text, caret);
    const start = context?.start ?? caret;
    const end = context?.end ?? caret;
    const mention = `@${choice.token} `;
    setMentionOpen(false);
    setMentionQuery("");
    setMentionActiveIndex(0);
    replaceComposerRange(start, end, mention);
  }
  function insertComposerText(text: string) {
    focusComposer();
    document.execCommand("insertText", false, text);
    const next = syncComposerFromEditor();
    updateMentionState(next, composerCaretOffset());
  }
  function applyComposerFormat(action: ComposerFormatAction) {
    focusComposer();
    const selectionText = window.getSelection()?.toString() || "";
    if (action === "bold") document.execCommand("bold");
    if (action === "italic") document.execCommand("italic");
    if (action === "underline") document.execCommand("underline");
    if (action === "strike") document.execCommand("strikeThrough");
    if (action === "ordered-list") document.execCommand("insertOrderedList");
    if (action === "unordered-list") document.execCommand("insertUnorderedList");
    if (action === "quote") document.execCommand("formatBlock", false, "blockquote");
    if (action === "inline-code") {
      document.execCommand("insertHTML", false, `<code>${escapeHtml(selectionText || "code")}</code>`);
    }
    if (action === "link") {
      document.execCommand("insertHTML", false, `<a href="https://" target="_blank" rel="noreferrer">${escapeHtml(selectionText || "link text")}</a>`);
    }
    if (action === "code-block") {
      document.execCommand("insertHTML", false, `<pre><code>${escapeHtml(selectionText || "code")}</code></pre>`);
    }
    syncComposerFromEditor();
  }
  function handleEmojiSelect(emoji: EmojiMartSelection) {
    const selected = emoji.native || emoji.shortcodes;
    if (!selected) return;
    insertComposerText(selected);
    setEmojiOpen(false);
    setMentionOpen(false);
  }
  function addAttachmentFiles(files: FileList | null) {
    if (!files?.length) return;
    if (composerAttachments.length + files.length > 8) {
      setComposerError("每条消息最多 8 个附件");
      return;
    }
    const added: ComposerAttachment[] = Array.from(files).map((file) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(16).slice(2)}`,
      name: file.name, size: file.size, type: file.type, lastModified: file.lastModified,
      uploadStatus: "uploading",
    }));
    setComposerAttachments((current) => [...current, ...added]);
    Array.from(files).forEach((file, index) => {
      const id = added[index].id;
      void (file.size > 10 * 1024 * 1024
        ? Promise.reject(new Error("附件不能超过 10 MiB"))
        : uploadChannelAttachment(projectId || "", selectedChannelId, activeChannelThreadId, file))
        .then((uploadedRef) => setComposerAttachments((current) => current.map((item) => item.id === id
          ? { ...item, uploadStatus: "uploaded", uploadedRef } : item)))
        .catch((error) => setComposerAttachments((current) => current.map((item) => item.id === id
          ? { ...item, uploadStatus: "failed", uploadError: error instanceof Error ? error.message : "上传失败" } : item)));
    });
    focusComposer();
  }
  function removeAttachment(id: string) {
    setComposerAttachments((current) => current.filter((item) => item.id !== id));
  }
  function attachmentRefs(): Record<string, unknown> | undefined {
    if (!composerAttachments.length && !evaluationSourceDocument) return undefined;
    return {
      ...(evaluationSourceDocument ? { evaluation_source_document: true } : {}),
      ...(composerAttachments.length ? { attachments: composerAttachments.map((attachment) => ({
        ...attachment.uploadedRef,
        source: "browser-file-picker",
      })) } : {}),
    };
  }
  function formatBytes(value: number): string {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  function messageAttachments(message: Record<string, unknown>): Record<string, unknown>[] {
    const refs = recordValue(message.refs);
    const attachments = refs && Array.isArray(refs.attachments) ? refs.attachments : [];
    return attachments.filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  }
  async function submitHistorySearch() {
    const query = historyQuery.trim();
    if (!query || historySearching) return;
    setHistorySearching(true);
    try {
      const result = await onSearchHistory(query, activeChannelThreadId);
      setHistoryResult(result);
    } finally {
      setHistorySearching(false);
    }
  }
  function openHistoryResult(row: Record<string, unknown>) {
    const threadId = recordString(row, "thread_id", "main");
    setActiveChannelThreadId(threadId || "main");
    setChannelSplitThreadId("");
  }
  function handleChannelTimelineScroll(event: ReactUIEvent<HTMLDivElement>) {
    const nearBottom = isScrollElementNearBottom(event.currentTarget);
    setChannelPinnedToBottom(nearBottom);
    if (nearBottom) setChannelHasNewBelow(false);
  }
  function showLatestChannelMessages() {
    setChannelPinnedToBottom(true);
    setChannelHasNewBelow(false);
    scrollElementToBottom(channelTimelineRef.current);
  }
  async function markActiveThreadRead() {
    await runControl(() => onMarkRead(activeChannelThreadId || "main"));
  }
  async function submitComposer() {
    const plainText = syncComposerFromEditor().trim();
    const markdownText = channelComposerMarkdownFromElement(composerRef.current);
    if ((!plainText && !composerAttachments.length) || !actionReady) return;
    if (composerAttachments.some((item) => !item.uploadedRef)) {
      setComposerError("附件尚未上传完成或正文丢失，请等待或移除后重新上传");
      return;
    }
    const refs = attachmentRefs();
    const messageText = markdownText || composerAttachments.map((attachment) => `[attachment] ${attachment.name}`).join("\n");
    const pendingText = plainText;
    const pendingHtml = composerRef.current?.innerHTML ?? "";
    const pendingAttachments = composerAttachments;
    const clientMessageId = retryClientMessageId || newClientMessageId();
    const pendingId = clientMessageId;
    const draftKey = channelDraftStorageKey(
      projectId || "",
      selectedChannelId,
      activeChannelThreadId,
    );
    setComposerError("");
    setChannelPinnedToBottom(true);
    setChannelHasNewBelow(false);
    setPendingChannelMessages((current) => [...current, {
      id: pendingId,
      channelId: selectedChannelId,
      projectId: projectId || "",
      threadId: activeChannelThreadId,
      text: messageText,
      ts: new Date().toISOString(),
      targets: targetMembersForMessage(messageText),
      refs,
    }]);
    clearComposerEditor();
    setComposerAttachments([]);
    setComposerRefs({});
    setMentionOpen(false);
    setMentionQuery("");
    setPostingCount((count) => count + 1);
    void onPostMessage(messageText, refs, {
      clientMessageId,
      threadId: activeChannelThreadId,
      replyToMessageId: replyTarget?.id || undefined,
    })
      .then(() => {
        const saved = window.localStorage.getItem(draftKey);
        if (saved && JSON.parse(saved).clientMessageId === clientMessageId) window.localStorage.removeItem(draftKey);
        if (currentDraftKey.current === draftKey) {
          setRetryClientMessageId("");
          setReplyTarget(null);
        }
        // Success: keep the optimistic row until the REAL message lands in
        // `detail` — channelDetailWithPendingMessage drops it by identity
        // the moment the projection catches up. Removing it here (the old
        // finally) opened a visible gap: the POST returns before read_model
        // ingests the event, so the message vanished for a beat and then
        // reappeared. The timeout is only a ghost-row backstop in case the
        // projection never picks the message up.
        window.setTimeout(() => {
          setPendingChannelMessages((current) => current.filter((item) => item.id !== pendingId));
        }, 20000);
      })
      .catch((error) => {
        const reason = error instanceof Error ? error.message : "message was not posted";
        const draft: ChannelComposerDraft = {
          schemaVersion: "channel-composer-draft.v1",
          clientMessageId,
          threadId: activeChannelThreadId,
          text: pendingText,
          html: pendingHtml,
          attachments: pendingAttachments,
          refs,
          reason,
        };
        window.localStorage.setItem(draftKey, JSON.stringify(draft));
        if (currentDraftKey.current === draftKey) {
          setComposerError(reason);
          setRetryClientMessageId(clientMessageId);
          if (!composerPlainText().trim()) restoreComposerEditor(pendingHtml, pendingText);
          setComposerAttachments((current) => current.length ? current : pendingAttachments);
          setComposerRefs(refs || {});
        }
        setPendingChannelMessages((current) => current.filter((item) => item.id !== pendingId));
      })
      .finally(() => {
        setPostingCount((count) => Math.max(0, count - 1));
      });
  }
  async function runControl(action: () => Promise<void>) {
    if (controlsBusy) return;
    setControlsBusy(true);
    setControlError("");
    try {
      await action();
    } catch (error) {
      setControlError(error instanceof Error ? error.message : "操作未完成，请重试。");
    } finally {
      setControlsBusy(false);
    }
  }
  function messageMember(memberId: string) {
    if (!memberId) return;
    setMemberMenuId("");
    setMentionOpen(false);
    insertComposerText(`@${memberId} `);
    window.setTimeout(() => focusComposer(), 0);
  }
  function renderMentionPicker() {
    return (
      <span className="channel-popover channel-mention-popover" role="listbox" aria-label="Mention members">
        {filteredMentionChoices.length ? filteredMentionChoices.map((choice, index) => (
          <button
            aria-selected={index === activeMentionIndex}
            className={`channel-mention-option ${index === activeMentionIndex ? "active" : ""}`}
            key={choice.id}
            role="option"
            type="button"
            onMouseDown={(event) => {
              event.preventDefault();
              selectMentionChoice(choice);
            }}
          >
            <span className="channel-avatar small">{choice.avatar}</span>
            <span>
              <strong>@{choice.token}</strong>
              <small>{choice.meta}</small>
            </span>
          </button>
        )) : (
          <span className="empty-text">No matching members.</span>
        )}
      </span>
    );
  }
  function renderMemberProfile(member: Record<string, unknown>) {
    const profileRows = [
      { key: "member", value: recordString(member, "member_id") || memberDisplayName(member) },
      { key: "display", value: memberDisplayName(member) },
      { key: "provider", value: memberProvider(member) },
      { key: "binding", value: recordString(member, "provider_binding_id") || "-" },
      { key: "backend", value: recordString(member, "backend") || "-" },
      { key: "status", value: recordString(member, "status") || "observed" },
      { key: "presence", value: memberPresence(member) },
      { key: "role", value: memberRoleLabel(member) },
      { key: "visibility", value: recordString(member, "visibility_profile") || "minimal" },
      { key: "permission", value: recordString(member, "permission_profile") || "read_only" },
      { key: "latest_run", value: recordString(member, "latest_run_id") || "-" },
      { key: "latest_run_status", value: recordString(member, "latest_run_status") || "-" },
      { key: "active_request", value: recordString(member, "active_request_id") || "-" },
      { key: "context_status", value: recordString(member, "context_status") || "-" },
      { key: "capabilities", value: memberCapabilitySummary(member) },
      { key: "provider_session", value: recordString(member, "provider_session_id") || "-" },
      { key: "worker_session", value: recordString(member, "worker_session_id") || recordString(member, "backing_worker_session_id") || "-" },
    ];
    return (
      <dl className="channel-member-profile">
        {profileRows.map((row) => (
          <Fragment key={row.key}>
            <dt>{row.key}</dt>
            <dd className={row.value.length > 18 ? "mono" : ""}>{row.value}</dd>
          </Fragment>
        ))}
      </dl>
    );
  }
  function renderMemberList(rows: Record<string, unknown>[], options: { actions?: boolean; filter?: string } = {}) {
    const normalizedFilter = (options.filter ?? "").trim().toLowerCase();
    const visibleRows = normalizedFilter
      ? rows.filter((member) => memberSearchText(member).includes(normalizedFilter))
      : rows;
    if (!visibleRows.length) {
      return <p className="empty-text">No member projection loaded.</p>;
    }
    return (
      <div className="channel-member-list">
        {visibleRows.map((member) => {
          const memberId = recordString(member, "member_id") || memberDisplayName(member);
          const displayName = memberDisplayName(member);
          const permissionProfile = recordString(member, "permission_profile") || "read_only";
          const menuOpen = memberMenuId === memberId;
          const profileOpen = memberProfileId === memberId;
          return (
            <div className={`channel-member-item ${profileOpen ? "profile-open" : ""}`} key={memberId}>
              <div className="channel-member">
                <div className="channel-member-identity">
                  <span className="channel-message-avatar" style={{ color: memberColor(memberId) }}>{initials(displayName)}</span>
                  <div>
                    <strong>{displayName}</strong>
                    <span className="muted">{memberId === moderatorMemberId ? "主持人" : memberId === defaultResponderId ? "默认回复" : recordString(member, "responsibility") || channelMemberLabel(member)}</span>
                  </div>
                </div>
                <details className="channel-member-runtime"><summary>运行信息</summary>
                  <span>{memberProvider(member)} · {permissionProfile}</span>
                  <span>最近回复：{memberPresence(member)}</span>
                </details>
                {options.actions ? (
                  <div className="channel-member-actions">
                    <button
                      aria-expanded={menuOpen}
                      className="channel-tool-button"
                      title={`Actions for ${displayName}`}
                      type="button"
                      onClick={() => setMemberMenuId(menuOpen ? "" : memberId)}
                    >
                      <MoreHorizontal size={16} />
                    </button>
                    {menuOpen ? (
                      <div className="channel-member-menu">
                        <button type="button" onClick={() => messageMember(memberId)}>
                          <MessageSquare size={16} />
                          Message
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setMemberProfileId(profileOpen ? "" : memberId);
                            setMemberMenuId("");
                          }}
                        >
                          <Users size={16} />
                          View profile
                        </button>
                        <button
                          disabled={!actionReady || controlsBusy || permissionProfile === "project_writer"}
                          type="button"
                          onClick={() => {
                            setMemberMenuId("");
                            void runControl(() => onSetMemberPermission(memberId, "project_writer"));
                          }}
                        >
                          <Wrench size={16} />
                          Set project writer
                        </button>
                        <button
                          disabled={!actionReady || controlsBusy || permissionProfile === "read_only"}
                          type="button"
                          onClick={() => {
                            setMemberMenuId("");
                            void runControl(() => onSetMemberPermission(memberId, "read_only"));
                          }}
                        >
                          <FileText size={16} />
                          Set read only
                        </button>
                        <button
                          className="danger"
                          disabled={!actionReady || controlsBusy}
                          type="button"
                          onClick={() => {
                            setMemberMenuId("");
                            void runControl(() => onRemoveMember(memberId));
                          }}
                        >
                          <Trash2 size={16} />
                          Remove from channel
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <span className={`status-pill status-${recordString(member, "status") || "observed"}`}>
                    {recordString(member, "status") || "observed"}
                  </span>
                )}
              </div>
              {profileOpen ? renderMemberProfile(member) : null}
            </div>
          );
        })}
      </div>
    );
  }
  function renderWorkflowRequestForm() {
    return (
      <section className="channel-control-panel channel-control-card">
        <div className="inline-heading">
          <h3>Delivery handoff</h3>
          <span className={`metric-chip ${canonicalPrd.ready ? "" : "chip-warn"}`}>
            {canonicalPrd.ready ? "canonical PRD" : "PRD pending"}
          </span>
        </div>
        <p className="muted">先生成规划；创建 Task 与启动 Workflow 各需一次独立确认。</p>
        <div className="channel-control-form-grid">
          <label className="channel-control-field channel-control-field-wide">
            <span>Objective</span>
            <input
              className="filter-input"
              data-testid="channel-workflow-objective"
              placeholder="workflow objective"
              value={workflowDraft.reason}
              onChange={(event) => setWorkflowDraft({ ...workflowDraft, reason: event.target.value })}
            />
          </label>
        </div>
        <details className="channel-workflow-existing-task">
          <summary>Plan an existing Task</summary>
          <form
            className="channel-control-form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              void runControl(async () => {
                await onWorkflowRequest(
                  workflowDraft.taskId.trim(),
                  workflowDraft.reason.trim() || "Execute the canonical Channel PRD.",
                  controlThreadId,
                );
                setWorkflowDraft({ taskId: "", reason: "" });
              });
            }}
          >
            <label className="channel-control-field">
              <span>Task</span>
              <input
                className="filter-input"
                data-testid="channel-workflow-task"
                placeholder="task id"
                value={workflowDraft.taskId}
                onChange={(event) => setWorkflowDraft({ ...workflowDraft, taskId: event.target.value })}
              />
            </label>
            <button
              className="icon-button"
              data-testid="channel-plan-workflow"
              disabled={!actionReady || controlsBusy || !canonicalPrd.ready || !workflowDraft.taskId.trim()}
              type="submit"
            >
              <PlayCircle size={16} />
              Plan existing Task
            </button>
          </form>
        </details>
      </section>
    );
  }
  function renderResultReceipts() {
    if (!resultReceipts.length) return null;
    return (
      <div className="channel-result-receipts">
        <div className="inline-heading">
          <h3>Linked results</h3>
          <span className="muted">{resultReceipts.length} receipts</span>
        </div>
        <div className="channel-result-receipt-list">
          {resultReceipts.slice().reverse().slice(0, 12).map((receipt, index) => {
            const kind = recordString(receipt, "receipt_kind", "result");
            const status = recordString(receipt, "status", "available");
            const artifactRef = recordString(receipt, "artifact_ref");
            const taskId = recordString(receipt, "task_id");
            const workflowRunId = recordString(receipt, "workflow_run_id");
            const receiptId = recordString(receipt, "receipt_id")
              || recordString(receipt, "event_id")
              || `${kind}-${index}`;
            return (
              <article className="channel-result-receipt" key={receiptId}>
                <div>
                  <strong>{kind.replaceAll("_", " ")}</strong>
                  <span className={`status-pill status-${status}`}>{status}</span>
                </div>
                <small>{[taskId, workflowRunId].filter(Boolean).join(" / ") || "Channel result"}</small>
                {artifactRef ? (
                  <code title={artifactRef}>
                    <Link size={13} />
                    {artifactRef}
                  </code>
                ) : null}
              </article>
            );
          })}
        </div>
      </div>
    );
  }
  function renderHistorySearchPanel() {
    const rows = (historyResult?.items ?? historyResult?.results ?? [])
      .filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
    if (!historySearchOpen && !historyQuery && !historyResult) return null;
    return (
      <div className="channel-history-panel">
        <form
          className="channel-history-search"
          onSubmit={(event) => {
            event.preventDefault();
            void submitHistorySearch();
          }}
        >
          <Search size={15} />
          <input
            ref={historyInputRef}
            className="channel-history-input"
            placeholder="Search messages"
            value={historyQuery}
            onChange={(event) => setHistoryQuery(event.target.value)}
          />
          <button className="icon-button" disabled={!historyQuery.trim() || historySearching} type="submit">
            {historySearching ? "Searching" : "Search"}
          </button>
          <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void markActiveThreadRead()}>
            Mark Read
          </button>
          <button
            className="icon-button"
            title="Close search"
            type="button"
            onClick={() => {
              setHistorySearchOpen(false);
              setHistoryQuery("");
              setHistoryResult(null);
            }}
          >
            <X size={15} />
          </button>
        </form>
        {rows.length ? (
          <div className="channel-history-results">
            {rows.map((row) => {
              const messageId = recordString(row, "message_id") || recordString(row, "event_id");
              return (
                <button
                  className="channel-history-result"
                  key={`${recordString(row, "thread_id", "main")}-${messageId}`}
                  type="button"
                  onClick={() => openHistoryResult(row)}
                >
                  <span className="mono">{recordString(row, "thread_id", "main")}</span>
                  <span>{recordString(row, "text_excerpt") || messageId}</span>
                  <small>{recordString(row, "member_id") || recordString(row, "role") || "message"}</small>
                </button>
              );
            })}
          </div>
        ) : historyResult ? (
          <div className="channel-history-empty">No messages found.</div>
        ) : null}
      </div>
    );
  }
  function renderReplyAttentionList(rows = attentionReplyRows, history = false) {
    const reviews = history ? [] : unsettledCrossReviews;
    return (
      <div className="channel-attention-section">
        <div className="inline-heading">
          <h3>{history ? "历史失败（不代表仍在运行）" : "回复状态"}</h3>
          <span className="muted">{history ? `${rows.length} records` : `${replyHealth.running.length} running · ${replyHealth.waiting.length} waiting`}</span>
        </div>
        {reviews.length ? <div data-testid="channel-review-attention" className="channel-attention-list">
          <strong>正式审查尚未通过 · {reviews.length}</strong>
          <p>成员回复完成不代表正式结果已通过检查。格式错误由运行时有限修正，未解决的事实仍需证据。</p>
          {reviews.map((row) => <div className="channel-attention-card" key={recordString(row, "request_id")}>
            <strong>{recordString(row, "target_member_id")}</strong>
            <span>{row.status === "rejected" ? "输出未通过检查" : row.outcome === "unresolved" ? "事实尚未解决" : "复核待完成"}</span>
            {row.reason ? <details><summary>查看原因</summary><p>{recordString(row, "reason")}</p></details> : null}
          </div>)}
        </div> : null}
        {rows.length ? (
          <div className="channel-attention-list">
            {rows.map((row) => {
              const status = recordString(row, "status", "pending");
              const target = recordString(row, "target_member_id") || recordString(row, "member_id") || "agent";
              const reason = recordString(row, "reason") || recordString(row, "error") || recordString(row, "summary");
              const failure = failedReplyRows.includes(row) ? replyFailurePresentation(row) : null;
              const historic = Boolean(failure && row.message_id && row.message_id !== latestThreadRequirementId);
              const requestId = recordString(row, "request_id") || recordString(row, "event_id") || target;
              const ts = recordString(row, "updated_at") || recordString(row, "created_at") || recordString(row, "ts");
              const active = status === "running" || status === "started";
              const startedAt = recordString(row, "started_at") || (
                status === "running" || status === "started"
                  ? recordString(row, "updated_at")
                  : recordString(row, "created_at")
              );
              return (
                <div className={`channel-attention-card ${failedReplyRows.includes(row) ? "failed" : ""}`} data-member-id={target} key={requestId}>
                  <div className="channel-attention-card-main">
                    <span className="channel-avatar tiny">{initials(target)}</span>
                    <div>
                      <strong>{channelMemberLabel(members.find((member) => member.member_id === target) ?? { member_id: target })}</strong>
                      <span className="muted">{failure ? `${historic ? "历史记录 · " : ""}${failure.label}` : active ? "正在回复" : "等待运行时调度"}</span>
                      {failure ? <small>{failure.advice}</small> : null}
                      {reason ? <details className="channel-reply-error"><summary>错误详情 / 原始原因</summary><p>{reason}</p></details> : null}
                    </div>
                  </div>
                  <div className="channel-attention-card-meta">
                    <span className={`status-pill status-${status}`}>{status}</span>
                    {active && startedAt ? (
                      <ChannelLiveElapsed startedAt={startedAt} />
                    ) : ts ? <span className="mono muted">{formatTime(ts)}</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-attention-empty">
            <strong>{reviews.length ? "等待正式审查结果" : contextRejections.length ? "评审尚未派发" : "No reply attention"}</strong>
            <span>{reviews.length ? "查看上方审查状态；尚不能认定讨论完成。" : contextRejections.length ? "来源上下文检查阻止了后续评审，请先处理上方原因。" : "All requested agent replies have settled."}</span>
          </div>
        )}
      </div>
    );
  }
  function renderMentionEventList() {
    const visibleMentions = mentionDigests.slice(0, 12);
    return (
      <div className="channel-attention-section">
        <div className="inline-heading">
          <h3>Mention Events</h3>
          <span className="muted">{mentionDigests.length} grouped</span>
        </div>
        {visibleMentions.length ? (
          <div className="channel-mention-event-list">
            {visibleMentions.map((item) => {
              const preview = item.text.trim().replace(/\s+/g, " ");
              return (
                <div className="channel-mention-event" key={item.id}>
                  <div className="channel-mention-event-head">
                    <span className="channel-avatar tiny">{initials(item.actor)}</span>
                    <strong>{item.actor}</strong>
                    <span className="muted">mentioned</span>
                    <span className="mono muted">{formatTime(item.ts)}</span>
                  </div>
                  <div className="channel-mention-targets">
                    {item.targets.map((target) => <span className="mention-target-chip" key={target}>@{target}</span>)}
                    {item.unresolvedTargets.map((target) => <span className="mention-target-chip unresolved" key={target}>@{target}</span>)}
                  </div>
                  {preview ? <p>{preview.slice(0, 220)}</p> : null}
                  <span className="channel-mention-event-foot mono">{item.threadId} · {item.messageId || item.id}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-attention-empty">
            <strong>No mention events</strong>
            <span>Mentions will appear here once channel messages target members.</span>
          </div>
        )}
      </div>
    );
  }
  function workspaceStatusFromRow(row: Record<string, unknown>, fallback = "idle"): string {
    const explicit = recordString(row, "status");
    if (explicit) return explicit;
    const type = recordString(row, "type");
    if (type.endsWith(".completed")) return "completed";
    if (type.endsWith(".failed")) return "failed";
    if (type.endsWith(".built")) return "built";
    if (type.endsWith(".queued")) return "queued";
    if (type.endsWith(".running")) return "running";
    return fallback;
  }
  function workspaceRowTime(row: Record<string, unknown>): string {
    return recordString(row, "updated_at") || recordString(row, "created_at") || recordString(row, "ts");
  }
  function latestWorkspaceRows(rows: Record<string, unknown>[], limit = 6): Record<string, unknown>[] {
    return [...rows]
      .sort((left, right) => workspaceRowTime(right).localeCompare(workspaceRowTime(left)))
      .slice(0, limit);
  }
  function countWorkspaceRows(rows: Record<string, unknown>[], predicate: (row: Record<string, unknown>) => boolean): number {
    return rows.reduce((count, row) => count + (predicate(row) ? 1 : 0), 0);
  }
  function renderWorkspaceReplyFlow(includeHistory = false) {
    const attentionRows = replyRequests.filter((row) => {
      const status = workspaceStatusFromRow(row);
      return status === "pending" || status === "running" || status === "started"
        || status === "queued" || status === "failed" || status === "rejected"
        || status === "escalated";
    });
    const latestReplies = latestWorkspaceRows(
      includeHistory ? replyRequests : attentionRows,
      includeHistory ? 10 : 7,
    );
    const completed = countWorkspaceRows(replyRequests, (row) => workspaceStatusFromRow(row) === "completed");
    const failed = countWorkspaceRows(replyRequests, (row) => workspaceStatusFromRow(row) === "failed");
    return (
      <section className={`channel-workspace-panel ${includeHistory ? "span-2" : ""}`.trim()}>
        <div className="channel-workspace-panel-head">
          <div>
            <h3>{includeHistory ? "Reply history" : "Reply attention"}</h3>
            <span className="muted">
              {includeHistory
                ? `${replyRequests.length} requests across channel history`
                : `${attentionRows.length} pending or failed requests`}
            </span>
          </div>
          <div className="channel-workspace-chip-row">
            <span className="metric-chip">{completed} done</span>
            <span className={`metric-chip ${failed ? "chip-warn" : ""}`}>{failed} failed</span>
            <span className="metric-chip">{pendingReplies} pending</span>
          </div>
        </div>
        {latestReplies.length ? (
          <div className="channel-workspace-list">
            {latestReplies.map((row) => {
              const requestId = recordString(row, "request_id") || recordString(row, "event_id");
              const target = recordString(row, "target_member_id") || recordString(row, "member_id") || "agent";
              const status = workspaceStatusFromRow(row, "pending");
              const messageId = recordString(row, "message_id") || "-";
              return (
                <div className="channel-workspace-row" key={requestId || `${target}-${messageId}`}>
                  <span className="channel-avatar tiny">{initials(target)}</span>
                  <div>
                    <strong>@{target}</strong>
                    <span className="muted">message {messageId}</span>
                  </div>
                  <span className={`status-pill status-${status}`}>{status}</span>
                  <span className="mono muted">{formatTime(workspaceRowTime(row)) || "-"}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-workspace-empty">
            {includeHistory ? "No reply requests yet." : "No replies need attention."}
          </div>
        )}
      </section>
    );
  }
  function renderWorkspaceContextFlow() {
    const latestContexts = latestWorkspaceRows(contextPacks, 7);
    const built = countWorkspaceRows(contextPacks, (row) => workspaceStatusFromRow(row) === "built");
    const profiles = new Set(contextPacks.map((row) => recordString(row, "visibility_profile")).filter(Boolean));
    return (
      <section className="channel-workspace-panel">
        <div className="channel-workspace-panel-head">
          <div>
            <h3>Context Packs</h3>
            <span className="muted">{contextPacks.length} packs · {profiles.size || 0} profiles</span>
          </div>
          <span className="metric-chip">{built} built</span>
        </div>
        {latestContexts.length ? (
          <div className="channel-workspace-list compact">
            {latestContexts.map((row) => {
              const contextId = recordString(row, "context_pack_id") || recordString(row, "event_id");
              const target = recordString(row, "target_member_id") || "agent";
              const profile = recordString(row, "visibility_profile") || "default";
              const status = workspaceStatusFromRow(row, "built");
              return (
                <div className="channel-workspace-row compact" key={contextId}>
                  <span className="channel-avatar tiny">{initials(target)}</span>
                  <div>
                    <strong>@{target}</strong>
                    <span className="muted">{profile}</span>
                  </div>
                  <span className={`status-pill status-${status}`}>{status}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-workspace-empty">No context packs built.</div>
        )}
      </section>
    );
  }
  function renderDiscussionActivity() {
    if (!discussionBand) return null;
    const { attention, presentation } = discussionBand;
    const activityLabel = attention.state === "needs_input" && attention.owner_question_count > 0
      ? `${attention.owner_question_count} decisions need review`
      : presentation.label;
    const activitySummary = attention.state === "needs_input" && attention.owner_question_count > 0
      ? "Questions stay attached to the Agent that asked them."
      : presentation.summary;
    return (
      <section
        className={`channel-discussion-activity tone-${presentation.tone}`}
        data-discussion-state={attention.state}
        data-testid="channel-discussion-activity"
      >
        <div className="channel-discussion-activity-head">
          <span className="channel-discussion-state-icon"><Bell size={15} /></span>
          <div>
            <strong>{activityLabel}</strong>
            <span>{activitySummary}</span>
          </div>
        </div>
        <div className="channel-discussion-activity-metrics">
          <span><strong>{attention.active_agent_count}</strong> active agents</span>
          <span><strong>{attention.completed_reply_count}</strong> replies complete</span>
          <span><strong>{attention.open_question_count}</strong> open questions</span>
          <span><strong>{attention.failed_reply_count}</strong> failed replies</span>
        </div>
        <dl className="channel-discussion-activity-meta">
          <dt>Thread</dt>
          <dd className="mono">{attention.thread_id}</dd>
          <dt>Runtime phase</dt>
          <dd className="mono">{attention.kernel_phase || "idle"}</dd>
          <dt>Last activity</dt>
          <dd>{formatTime(attention.last_activity_at) || "-"}</dd>
        </dl>
        {discussionBand.openQuestions.length ? (
          <div className="channel-discussion-open-questions">
            <strong>Open questions</strong>
            {discussionBand.openQuestions.slice(0, 5).map((question) => (
              <button
                key={question.id}
                type="button"
                onClick={() => {
                  setDrawer(null);
                  quoteDiscussionQuestion(question.text);
                  window.setTimeout(() => focusComposer(), 0);
                }}
              >
                <span>{question.text}</span>
                <Quote size={14} />
              </button>
            ))}
          </div>
        ) : null}
        {discussionBand.openQuestions.filter((question) => question.kind === "fact").map((question) => (
          <ChannelFactScopeControl key={JSON.stringify([selectedChannelId, question.sourceThreadId, question.id])}
            question={question} busy={controlsBusy}
            disabled={!actionReady || discussionBand.questionConsolidationPending}
            onExclude={(questionId, threadId, reason) => runControl(() =>
              onResolveQuestion(questionId, threadId, "out_of_scope", reason))} />
        ))}
        {presentation.action === "result" && presentation.actionLabel ? (
          <button
            className="icon-button primary"
            type="button"
            onClick={() => {
              void onLoadDiagnostics();
              setDrawer("prd");
            }}
          >
            {presentation.actionLabel}
          </button>
        ) : null}
        {renderChannelConsensusControl()}
        <div className="channel-discussion-activity-actions">
          {attention.can_review_questions ? (
            <button
              className="icon-button primary"
              type="button"
              onClick={focusOwnerQuestions}
            >
              Locate questions
            </button>
          ) : null}
          <button
            className="icon-button"
            disabled={!actionReady || controlsBusy || !discussionControls.canDrainReplies}
            type="button"
            onClick={() => void runControl(onDrainReplies)}
          >
            Drain replies
          </button>
          <button
            className="icon-button"
            disabled={!actionReady || controlsBusy || !discussionControls.canSynthesize}
            type="button"
            onClick={() => void runControl(() => onRequestSynthesis(
              controlThreadId,
            ))}
          >
            <FileText size={15} />
            Synthesize
          </button>
          <button
            className="icon-button"
            disabled={!actionReady || controlsBusy || !discussionControls.canStartOrRestart}
            type="button"
            onClick={() => void runControl(() => onStartDiscussion(
              controlThreadId,
              latestThreadRequirementText,
              latestThreadRequirementId,
              discussionMode,
              discussionControls.restart,
            ))}
          >
            <PlayCircle size={15} />
            {discussionControls.startLabel}
          </button>
        </div>
      </section>
    );
  }
  function renderDrawerContent(activeDrawer: ChannelDrawerKey) {
    if (activeDrawer === "file" && previewFile) return <ChannelFilePreview
      key={`${projectId}:${selectedChannelId}:${controlThreadId}:${previewFile.id}:${previewFile.name}`}
      item={previewFile} projectId={projectId || "default"} channelId={selectedChannelId} threadId={controlThreadId}
      publishDisabled={!actionReady || controlsBusy}
      onPublish={async (sourceFile) => {
        await onRequestSynthesis(controlThreadId, undefined, sourceFile);
        setDrawer("prd");
      }}
      onClose={() => setDrawer(null)} />;
    if (activeDrawer === "members") {
      return (
        <>
          <div className="channel-control-panel">
            <div className="channel-control-row read-only channel-member-role-summary">
              <span className="channel-control-copy">
                <strong>Channel Moderator</strong>
                <small>{moderatorLabel}</small>
              </span>
              <span className="status-pill status-observed">{sameModeratorAndResponder ? "主持人 · 默认回复（同一成员）" : "主持人"}</span>
            </div>
            {!sameModeratorAndResponder ? <div className="channel-control-row read-only channel-member-role-summary">
              <span className="channel-control-copy">
                <strong>Default Responder</strong>
                <small>{defaultResponderLabel}</small>
              </span>
              <span className="status-pill status-observed">
                {sameModeratorAndResponder ? "同一成员" : "默认回复"}
              </span>
            </div> : null}
            <div className="inline-heading">
              <h3>Members</h3>
              <span className="muted">{members.length}</span>
            </div>
            <input
              className="filter-input"
              placeholder={`Search ${members.length} members`}
              value={memberSearch}
              onChange={(event) => setMemberSearch(event.target.value)}
            />
            <button className="channel-member-add-row" disabled={!actionReady} type="button" onClick={onAddAgent}>
              <Plus size={16} />
              Add role / member
            </button>
            {onRequestProjectProfile ? <div className="channel-control-panel">
              <label htmlFor="channel-profile-objective">缺少合适角色？交给 Kanban Agent 生成项目级配置</label>
              <textarea id="channel-profile-objective" className="filter-input" value={profileObjective}
                placeholder="说明需要的角色、职责和交付物，例如页面布局、交互与可视化设计"
                onChange={(event) => setProfileObjective(event.target.value)} />
              <button type="button" className="icon-button" disabled={!actionReady || controlsBusy || !profileObjective.trim()}
                onClick={() => void runControl(() => onRequestProjectProfile(profileObjective.trim(), controlThreadId))}>
                生成项目角色
              </button>
              <small>优先复用项目配置；新增配置需审核。批准后返回当前讨论参评。</small>
            </div> : null}
            {renderMemberList(members, { actions: true, filter: memberSearch })}
          </div>
          <div className="channel-control-panel">
            <div className="inline-heading">
              <h3>Bound Runtime Sessions</h3>
              <span className="muted">{boundMembers.length}</span>
            </div>
            {boundMembers.length ? renderMemberList(boundMembers) : (
              <p className="muted">No runtime binding. Channel discussion stays separate from workflow execution.</p>
            )}
          </div>
        </>
      );
    }
    if (activeDrawer === "attention") {
      return (
        <>
          <ChannelContextFailures rows={contextRejections} />
          <div className="channel-attention-strip">
            <span className="metric-chip">{replyHealth.running.length} running</span>
            <span className="metric-chip">{replyHealth.waiting.length} waiting</span>
            <span className={`metric-chip ${currentFailedReplyRows.length ? "chip-warn" : ""}`}>{currentFailedReplyRows.length} failed</span>
            <span className="metric-chip">{mentionDigests.length} mentions</span>
            <button className="icon-button" disabled={!actionReady || controlsBusy || !discussionControls.canDrainReplies} type="button" onClick={() => void runControl(onDrainReplies)}>
              Drain Replies
            </button>
          </div>
          {renderReplyAttentionList([...pendingReplyRows, ...currentFailedReplyRows])}
          {failedReplyRows.length > currentFailedReplyRows.length ? <details className="channel-reply-history"><summary>历史失败 · {failedReplyRows.length - currentFailedReplyRows.length}</summary>
            {renderReplyAttentionList(failedReplyRows.filter((row) => !currentFailedReplyRows.includes(row)), true)}
          </details> : null}
          {replyHealth.settled.length ? <details className="channel-reply-history"><summary>已结束记录 · {replyHealth.settled.length}</summary>
            {replyHealth.settled.map((row, index) => <p key={String(row.request_id || index)}>{String(row.target_member_id || "成员")} · {String(row.failure_category || row.terminal_disposition || row.status)}</p>)}
          </details> : null}
          {failedReplyRows.length ? <button className="icon-button" type="button" onClick={() => setDrawer("discussion")}>查看讨论状态与恢复操作</button> : null}
          {renderMentionEventList()}
        </>
      );
    }
    if (activeDrawer === "prd") return <ChannelPrdPreview key={channelDocumentKey(prdSelection)}
      selection={prdSelection} flow={flow} actionReady={actionReady} busy={controlsBusy}
      onClose={() => setDrawer(null)}
      onDiscuss={() => { setDrawer(null); composerRef.current?.focus(); }}
      onProcess={() => onWorkflowRequest("", workflowDraft.reason.trim() || "Create a Task bound to the canonical Channel PRD.", controlThreadId)}
      onEvaluate={(role, retry) => onEvaluatePrd(controlThreadId, flow.revision, flow.artifactRef, flow.artifactDigest, role, retry)}>
      {renderChannelConsensusControl()}
      {flow.ready ? <details><summary>Planning options</summary>{renderWorkflowRequestForm()}</details> : null}
    </ChannelPrdPreview>;
    if (activeDrawer === "requirement" || activeDrawer === "task" || activeDrawer === "workflow") {
      return <>
        <ChannelFlowPreview flow={flow} node={activeDrawer} />
        {activeDrawer === "requirement" && detail?.has_more ? <button className="icon-button" type="button"
          disabled={historyLoadingEarlier} onClick={() => void loadEarlierMessages()}>
          {historyLoadingEarlier ? "Loading…" : "加载更早的需求记录"}
        </button> : null}
        {activeDrawer !== "requirement" ? <div className="channel-control-actions">
          <button className="icon-button" type="button" onClick={() => setDrawer("prd")}>Preview PRD</button>
          <button className="icon-button" type="button" onClick={onReviewPlans}>Open Kanban Agent</button>
        </div> : null}
      </>;
    }
    if (activeDrawer === "discussion") return renderDiscussionActivity();
    if (activeDrawer === "diagnostics") {
      if (selectedSourceQuestion && ownerQuestionAttachments.unassigned.includes(selectedSourceQuestion)) return <section data-testid="channel-question-source-diagnostics">
        <p>Source reply unavailable · {selectedSourceQuestion.askedBy || "Unknown agent"}</p>
        <p className="muted">该历史问题缺少可唯一定位的发言，或来源尚未加载。保留原始问题身份，不附到其他 Agent 回复下。</p>
        {detail?.has_more ? <button className="icon-button" type="button" disabled={historyLoadingEarlier}
          onClick={() => void loadEarlierMessages()}>{historyLoadingEarlier ? "Loading…" : "Load earlier source"}</button> : null}
        {renderOwnerQuestionCard([selectedSourceQuestion], "channel-source-question", "channel-inline-question", true)}
      </section>;
      if (diagnosticsError) return <div role="alert" data-testid="channel-diagnostics-error">
        <p>{diagnosticsError}</p>
        <button className="icon-button" type="button" onClick={() => void onLoadDiagnostics()}>Retry</button>
      </div>;
      if (diagnosticsLoading || !diagnosticsDetail) return <p role="status" data-testid="channel-diagnostics-loading">Loading diagnostics…</p>;
      return (
        <>
          <div data-testid="channel-diagnostics-loaded">
            {renderWorkspaceReplyFlow(true)}
            {renderWorkspaceContextFlow()}
          </div>
          <TablePage title="Context Packs" rows={contextPacks} embedded />
          <TablePage title="State Updates" rows={stateUpdates} embedded />
          <TablePage title="Provider Runs" rows={diagnosticsDetail?.provider_runs ?? []} embedded />
          <TablePage title="Workflow Requests / Handoffs" rows={[...workflowRequests, ...handoffs]} embedded />
          {renderResultReceipts()}
        </>
      );
    }
    if (activeDrawer === "reports") {
      return (
        <>
          <div className="channel-control-panel">
            <div className="inline-heading">
              <h3>Owner</h3>
              <span className="muted">{ownerReports.length} reports</span>
            </div>
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(() => onGenerateOwnerReport(controlThreadId))}>
              <FileText size={16} />
              Report
            </button>
          </div>
          <ChannelReportPreviewRefs rows={ownerReports} title="Owner Report Preview Refs" />
          <ChannelReportPreviewRefs rows={automationReports} title="Automation Report Preview Refs" />
          <TablePage title="Owner Reports" rows={ownerReports} embedded />
          <TablePage title="Automation Reports" rows={automationReports} embedded />
        </>
      );
    }
    return (
      <>
        <div className="channel-control-panel">
          <div className="inline-heading">
            <h3>Channels</h3>
            <span className="muted">{visibleChannels.length}</span>
          </div>
          <div className="compact-list">
            {visibleChannels.map((channel) => {
              const channelId = channelIdOf(channel);
              return (
                <button
                  className={`channel-nav-row ${channelId === selectedChannelId ? "active" : ""}`}
                  key={channelId}
                  type="button"
                  onClick={() => onOpenChannel(channelId)}
                >
                  <Hash size={15} />
                  <span>{channelNameOf(channel).replace(/^#\s*/, "")}</span>
                  <span className="muted">{channel.members?.length ?? 0}</span>
                </button>
              );
            })}
          </div>
          <button className="channel-nav-row channel-nav-action" disabled={!actionReady} type="button" onClick={onNewChannel}>
            <Plus size={15} />
            <span>New Channel</span>
          </button>
        </div>
        <div className="channel-control-panel">
          <div className="inline-heading">
            <h3>Workflow</h3>
            <span className="muted">{workflowRequests.length} requests</span>
          </div>
          <button className="icon-button" type="button" onClick={() => setDrawer("diagnostics")}>
            <Wrench size={16} />诊断
          </button>
          <button className="icon-button" type="button" onClick={() => setDrawer("workflow")}>
            <PlayCircle size={16} />
            Workflow
          </button>
          <button className="icon-button" type="button" onClick={() => setDrawer("reports")}>
            <FileText size={16} />
            Reports
          </button>
        </div>
        <div className="channel-control-panel">
          <div className="inline-heading">
            <h3>Discussion Mode</h3>
            <span className="muted">{discussionMode}</span>
          </div>
          <select
            aria-label="Discussion mode"
            className="filter-input"
            disabled={!actionReady || controlsBusy}
            value={discussionMode}
            onChange={(event) => {
              const mode = event.target.value;
              void runControl(() => onSetDiscussionMode(controlThreadId, mode, defaultResponderId));
            }}
          >
            <option value="conversation">conversation</option>
            <option value="clarification">clarification</option>
            <option value="multi_lens">multi lens</option>
          </select>
          <select
            aria-label="Default responder"
            className="filter-input"
            disabled={!actionReady || controlsBusy}
            value={defaultResponderId}
            onChange={(event) => {
              const memberId = event.target.value;
              void runControl(() => onSetDiscussionMode(controlThreadId, discussionMode, memberId));
            }}
          >
            <option value="">no default responder</option>
            {replyCapableMembers.map((member) => {
              const memberId = recordString(member, "member_id");
              return <option key={memberId} value={memberId}>{memberDisplayName(member)}</option>;
            })}
          </select>
          <button
            className="icon-button"
            disabled={
              !actionReady
              || controlsBusy
              || !discussionControls.canStartOrRestart
            }
            type="button"
            onClick={() => void runControl(() => onStartDiscussion(
              controlThreadId,
              latestThreadRequirementText,
              latestThreadRequirementId,
              discussionMode,
              discussionControls.restart,
            ))}
          >
            <PlayCircle size={16} />
            {discussionControls.startLabel}
          </button>
        </div>
        <button className="icon-button" disabled={!actionReady || controlsBusy || !discussionControls.canSynthesize} type="button"
          onClick={() => void runControl(() => onRequestSynthesis(controlThreadId))}>
          <FileText size={16} />Synthesize
        </button>
        <div className="channel-control-panel">
          <div className="inline-heading">
            <h3>Danger Zone</h3>
            <span className="muted">event gated</span>
          </div>
          <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(() => onClearHistory(controlThreadId))}>
            <Archive size={16} />
            Clear History
          </button>
          <button className="icon-button danger" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onDeleteChannel)}>
            <Trash2 size={16} />
            Delete Channel
          </button>
        </div>
        <dl className="detail-grid channel-settings-grid">
          <dt>Channel</dt>
          <dd>{activeChannelName}</dd>
          <dt>ID</dt>
          <dd className="mono">{selectedChannelId}</dd>
          <dt>Status</dt>
          <dd>{activeChannel?.status || "open"}</dd>
          <dt>Source</dt>
          <dd>event projection</dd>
          <dt>History clear</dt>
          <dd>{detail?.history_cleared_at ? "projection reset; events retained" : "events retained"}</dd>
        </dl>
      </>
    );
  }
  return (
    <section className={`channel-page channel-page-chat ${decisionQueueItems.length ? "has-decision-queue" : ""} ${drawer ? "has-drawer" : ""}`.trim()}>
      <div className="channel-shell-header">
        <div className="channel-heading-stack">
            <div className="channel-title-row">
              <div className="channel-switcher-wrap">
              <button
                aria-expanded={channelSwitcherOpen}
                className={`channel-title-button ${channelSwitcherOpen ? "active" : ""}`}
                title="Switch channel"
                type="button"
                onClick={() => {
                  setChannelSwitcherOpen((open) => !open);
                  setEmojiOpen(false);
                }}
              >
                <Hash size={20} />
                <h2>{activeChannelLabel}</h2>
                <ChevronDown size={15} />
              </button>
              {channelSwitcherOpen ? (
                <div className="channel-switcher-popover">
                  <input
                    autoFocus
                    className="channel-switch-search"
                    placeholder="Search channels"
                    value={channelSearch}
                    onChange={(event) => setChannelSearch(event.target.value)}
                  />
                  {!normalizedChannelSearch ? (
                    <>
                      <div className="channel-switch-section-title">Recent</div>
                      <div className="channel-switch-list">
                        {recentChannels.map((channel) => renderChannelSwitchRow(channel))}
                      </div>
                    </>
                  ) : null}
                  <div className="channel-switch-section-title">All Channels</div>
                  <div className="channel-switch-list">
                    {filteredChannels.length ? filteredChannels.map((channel) => renderChannelSwitchRow(channel)) : (
                      <div className="empty-text">No channels found.</div>
                    )}
                  </div>
                  <button
                    className="channel-switch-new"
                    disabled={!actionReady}
                    type="button"
                    onClick={() => {
                      setChannelSwitcherOpen(false);
                      setChannelSearch("");
                      onNewChannel();
                    }}
                  >
                    <Plus size={15} />
                    New Channel
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
        <div className="channel-header-actions">
          <button
            aria-pressed={historySearchOpen}
            className={`channel-tool-button ${historySearchOpen ? "active" : ""}`}
            title="Search messages"
            type="button"
            onClick={openHistorySearch}
          >
            <Search size={16} />
          </button>
          <button
            aria-label={`Members ${members.length}`}
            aria-pressed={drawer === "members"}
            className={`channel-member-stack-button ${drawer === "members" ? "active" : ""}`}
            data-testid="channel-header-members"
            title={`Members ${members.length}`}
            type="button"
            onClick={() => toggleDrawer("members")}
          >
            <span className="channel-member-stack-avatars" aria-hidden="true">
              {members.length ? members.slice(0, 4).map((member) => {
                const memberId = recordString(member, "member_id") || memberDisplayName(member);
                return (
                  <span className="channel-member-stack-avatar" style={{ color: memberColor(memberId) }} key={memberId}>
                    {initials(memberDisplayName(member))}
                  </span>
                );
              }) : <Users size={15} />}
              {members.length > 4 ? <span className="channel-member-stack-more">+{members.length - 4}</span> : null}
            </span>
            <span>{members.length}</span>
          </button>
          {activityCount ? (
            <button
              aria-label={`Activity ${activityCount}`}
              aria-pressed={drawer === "attention"}
              className={`channel-tool-button ${currentFailedReplyRows.length ? "channel-header-failure" : ""} ${drawer === "attention" ? "active" : ""}`}
              data-testid="channel-header-failures"
              title="Activity"
              type="button"
              onClick={() => toggleDrawer("attention")}
            >
              <Bell size={16} />
              <span className="channel-activity-label">Activity</span><span>{activityCount}</span>
            </button>
          ) : null}
          <button
            aria-pressed={drawer === "settings"}
            className={`channel-tool-button ${drawer === "settings" ? "active" : ""}`}
            title="More"
            type="button"
            onClick={() => toggleDrawer("settings")}
          >
            <MoreHorizontal size={16} />
          </button>
        </div>
      </div>
      <ChannelFlowNavigation flow={flow} onSelect={(node) => toggleDrawer(node)}
        onReviewQuestions={focusOwnerQuestions} onReviewPlans={onReviewPlans} deliveryError={delivery.error} />
      {loadError ? <div className="notice">{loadError}</div> : null}
      {actionResult && (
        actionResult?.action === "channel-create"
        || actionResult?.action === "channel-invite-member"
        || actionResult?.action === "channel-update-member-permission"
        || actionResult?.action === "channel-remove-member"
        || actionResult?.action === "channel-delete"
        || actionResult?.action === "channel-clear-history"
        || actionResult?.action === "channel-post-message"
        || actionResult?.action === "channel-question-resolve"
        || actionResult?.action === "channel-consensus-confirm"
        || actionResult?.action === "channel-consensus-block"
        || actionResult?.action === "chat-orchestrator"
        || actionResult?.action === "channel.add_member"
        || actionResult?.action === "channel-owner-report"
      ) && shouldShowChannelActionNotice(actionResult) ? (
        <div className={`notice ${actionResult.ok ? "notice-ok" : ""}`}>
          <span className="mono">{actionResult.status}</span> {channelActionNoticeText(actionResult)}
        </div>
      ) : null}
      <div className={`channel-shell ${drawer ? "has-drawer" : ""} ${decisionQueueItems.length ? "has-decision-queue" : ""}`.trim()}>
        <main className="channel-main">
          <>
              {renderHistorySearchPanel()}
              <div
                ref={channelTimelineRef}
                className="channel-timeline"
                aria-live="polite"
                onScroll={handleChannelTimelineScroll}
              >
                {detail?.has_more ? (
                  <button
                    className="agent-history-load channel-history-load"
                    data-testid="channel-load-earlier"
                    disabled={historyLoadingEarlier}
                    type="button"
                    onClick={() => void loadEarlierMessages()}
                  >
                    {historyLoadingEarlier ? "Loading…" : "Load earlier messages"}
                  </button>
                ) : null}
                {controlError ? <p role="alert" data-testid="channel-control-error">{controlError}</p> : null}
                <ChannelMemberInvitations
                  key={`${projectId}:${selectedChannelId}:${controlThreadId}`}
                  proposals={detail?.member_invitation_proposals || []}
                  threadId={controlThreadId} enabled={actionReady} onDecide={onDecideMemberInvitation}
                />
                <AgentSessionTimeline
                  onPreviewReference={(item) => { setPreviewFile(item); setDrawer("file"); }}
                  actionBusyId={researchActionBusyId}
                  onCancelRun={actionReady && !controlsBusy
                    ? (runId) => { void runControl(() => onCancelProviderRun(runId)); }
                    : undefined}
                  activeThreadId={activeChannelThreadId}
                  allowSplit={channelHasMultipleThreads}
                  allowPreviewSplit
                  compact={!channelHasMultipleThreads}
                  conversation={chatConversation}
                  collapseCompletedRunDetails
                  emptyBody="Post a message or @mention an agent to start a channel run."
                  emptyTitle="No channel messages"
                  minimalRunActivity
                  renderRunIdentity={(run) => <ChannelMemberIdentity
                    member={members.find((member) => recordString(member, "member_id") === run.memberId) ?? { member_id: run.memberId }}
                    moderator={run.memberId === moderatorMemberId}
                    responder={run.memberId === defaultResponderId}
                    onDetails={() => { setMemberProfileId(run.memberId || ""); setMemberSearch(""); setDrawer("members"); }}
                  />}
                  renderRunSupplement={(run: AgentSessionRun, _turn: AgentSessionTurn) => <>
                    {ownerQuestionsOpen
                      ? renderOwnerQuestionCard(
                        ownerQuestionAttachments.byRun.get(run.id) || [],
                        "channel-inline-question",
                        "channel-inline-question",
                      )
                      : null}
                  </>}
                  onAdoptResearchResult={(card, cardId) => {
                    const payload = recordValue(
                      card.payload?.adoptPayload,
                    );
                    if (!payload || researchActionBusyId) return;
                    setResearchActionBusyId(cardId);
                    void onResearchAdopt(payload).finally(() => {
                      setResearchActionBusyId("");
                    });
                  }}
                  onPinMessage={(message, threadId) => {
                    void runControl(() => onPinMessage(
                      message.id,
                      threadId,
                      !Boolean(message.refs?.pinned),
                    ));
                  }}
                  onReplyMessage={(message, threadId) => {
                    setActiveChannelThreadId(threadId);
                    setReplyTarget(message, threadId);
                    window.setTimeout(() => focusComposer(), 0);
                  }}
                  onActiveThreadChange={(threadId) => {
                    setActiveChannelThreadId(threadId);
                    if (channelSplitThreadId === threadId) setChannelSplitThreadId("");
                  }}
                  onSplitThreadChange={setChannelSplitThreadId}
                  showRunDetails={false}
                  showRunProvider={false}
                  showThreadChips={channelHasMultipleThreads}
                  splitThreadId={channelSplitThreadId}
                />
                <ChannelDecisionHistory key={`${selectedChannelId}:${activeChannelThreadId}`}
                  groups={decisionReceipts.groups} recordCount={decisionReceipts.recordCount} />
                <ChannelPrdHandoff flow={flow} onDetails={() => setDrawer("prd")} />
              </div>
              {channelHasNewBelow ? (
                <button className="channel-scroll-latest" type="button" onClick={showLatestChannelMessages}>
                  <ChevronDown size={15} />
                  New messages
                </button>
              ) : null}
              <form
                className="channel-composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitComposer();
                }}
              >
                {replyTarget ? (
                  <div className="channel-composer-reply" data-testid="channel-reply-target">
                    <span>
                      Replying to <strong>{replyTarget.label}</strong>
                    </span>
                    <button
                      aria-label="Cancel reply"
                      className="icon-button"
                      title="Cancel reply"
                      type="button"
                      onClick={() => setReplyTarget(null)}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : null}
                <input
                  ref={attachmentInputRef}
                  className="channel-file-input"
                  multiple
                  type="file"
                  onChange={(event) => {
                    addAttachmentFiles(event.target.files);
                    event.currentTarget.value = "";
                  }}
                />
                {formattingOpen ? (
                  <div className="channel-format-toolbar" aria-label="Formatting toolbar">
                    {formatButtons.map(({ action, label, icon: Icon }) => (
                      <button
                        aria-label={label}
                        className="channel-format-button"
                        key={action}
                        title={label}
                        type="button"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          applyComposerFormat(action);
                        }}
                      >
                        <Icon size={16} />
                      </button>
                    ))}
                  </div>
                ) : null}
                <div
                  ref={composerRef}
                  className="channel-composer-input"
                  aria-busy={posting}
                  aria-label={`Message ${activeChannelName}`}
                  aria-multiline="true"
                  contentEditable
                  data-placeholder={`Message ${activeChannelName}`}
                  role="textbox"
                  suppressContentEditableWarning
                  onInput={() => {
                    const next = syncComposerFromEditor();
                    updateMentionState(next, composerCaretOffset());
                  }}
                  onClick={() => updateMentionState(composerPlainText(), composerCaretOffset())}
                  onPaste={(event) => {
                    event.preventDefault();
                    const text = event.clipboardData.getData("text/plain");
                    document.execCommand("insertText", false, text);
                    const next = syncComposerFromEditor();
                    updateMentionState(next, composerCaretOffset());
                  }}
                  onKeyDown={(event) => {
                    if (mentionOpen) {
                      if (event.key === "ArrowDown") {
                        event.preventDefault();
                        setMentionActiveIndex((index) => (index + 1) % Math.max(filteredMentionChoices.length, 1));
                        return;
                      }
                      if (event.key === "ArrowUp") {
                        event.preventDefault();
                        setMentionActiveIndex((index) => (index - 1 + Math.max(filteredMentionChoices.length, 1)) % Math.max(filteredMentionChoices.length, 1));
                        return;
                      }
                      if ((event.key === "Enter" || event.key === "Tab") && filteredMentionChoices.length) {
                        event.preventDefault();
                        selectMentionChoice(filteredMentionChoices[activeMentionIndex]);
                        return;
                      }
                      if (event.key === "Escape") {
                        event.preventDefault();
                        setMentionOpen(false);
                        return;
                      }
                    }
                    if (
                      event.key === "Enter"
                      && !event.shiftKey
                      && !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void submitComposer();
                    }
                  }}
                />
                {composerAttachments.length ? (
                  <div className="channel-attachment-list">
                    {composerAttachments.map((attachment) => (
                      <span className="channel-attachment-chip" key={attachment.id}>
                        <span>{attachment.name}</span>
                        <span className="muted">{formatBytes(attachment.size)}</span>
                        <span title={attachment.uploadError}>{attachment.uploadedRef ? "已上传" : attachment.uploadStatus === "uploading" ? "上传中…" : attachment.uploadError || "正文不可用，请移除后重新上传"}</span>
                        <button aria-label={`Remove ${attachment.name}`} type="button" onClick={() => removeAttachment(attachment.id)}>
                          <X size={12} />
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
                {composerError ? <div className="notice compact-error channel-composer-error">{composerError}</div> : null}
                <label className="channel-source-document-toggle">
                  <input type="checkbox" checked={evaluationSourceDocument}
                    onChange={(event) => setEvaluationSourceDocument(event.target.checked)} />
                  Mark as original requirement source
                </label>
                <div className="channel-composer-toolbar">
                  <div className="channel-composer-tools">
                    <button className="channel-tool-button" title="Upload attachment" type="button" onClick={() => attachmentInputRef.current?.click()}>
                      <Plus size={16} />
                    </button>
                    <span className="channel-composer-divider" />
                    <button
                      aria-pressed={formattingOpen}
                      className={`channel-tool-button ${formattingOpen ? "active" : ""}`}
                      title={formattingOpen ? "Hide formatting" : "Show formatting"}
                      type="button"
                      onClick={() => {
                        setFormattingOpen((open) => !open);
                        setEmojiOpen(false);
                        focusComposer();
                      }}
                    >
                      <Type size={16} />
                    </button>
                    <span className="channel-composer-tool-wrap">
                      <button
                        className={`channel-tool-button ${emojiOpen ? "active" : ""}`}
                        title="Insert emoji"
                        type="button"
                        onClick={() => {
                          setEmojiOpen((open) => !open);
                          setMentionOpen(false);
                        }}
                      >
                        <Smile size={16} />
                      </button>
                      {emojiOpen ? (
                        <span className="channel-popover channel-emoji-popover">
                          <Suspense fallback={<span className="muted">Loading emoji…</span>}>
                            <ChannelEmojiPicker onEmojiSelect={handleEmojiSelect} />
                          </Suspense>
                        </span>
                      ) : null}
                    </span>
                    <span className="channel-composer-tool-wrap">
                      <button
                        aria-expanded={mentionOpen}
                        className={`channel-tool-button ${mentionOpen ? "active" : ""}`}
                        title="Mention member"
                        type="button"
                        onClick={openMentionMenu}
                      >
                        <AtSign size={16} />
                      </button>
                      {mentionOpen ? renderMentionPicker() : null}
                    </span>
                  </div>
                  <button
                    aria-label="Send message"
                    className="channel-send-button"
                    disabled={!actionReady || composerAttachments.some((item) => !item.uploadedRef) || (!composerText.trim() && !composerAttachments.length)}
                    title={posting ? "Sending in background" : "Send"}
                    type="submit"
                  >
                    <Send size={16} />
                  </button>
                </div>
              </form>
          </>
        </main>

        <ChannelDecisionQueue
          items={decisionQueueItems}
          mobileOpen={decisionQueueOpen}
          onCloseMobile={() => setDecisionQueueOpen(false)}
          onSelect={focusOwnerQuestion}
          onToggleMobile={() => setDecisionQueueOpen((open) => !open)}
        />

        {drawer ? (
          <aside className={`channel-drawer ${drawer === "prd" || drawer === "file" ? "channel-document-drawer" : ""}`}>
            {drawer !== "prd" && drawer !== "file" ? <div className="channel-drawer-header">
              <div>
                <h3>{drawerTitles[drawer]}</h3>
                <span className="muted mono">{selectedChannelId}</span>
              </div>
              <button className="channel-tool-button" title="Close drawer" type="button" onClick={() => setDrawer(null)}>
                <X size={16} />
              </button>
            </div> : null}
            <div className="channel-drawer-content">
              {renderDrawerContent(drawer)}
            </div>
          </aside>
        ) : null}
      </div>
    </section>
  );
}
