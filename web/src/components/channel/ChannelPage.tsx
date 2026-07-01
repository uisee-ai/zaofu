// ChannelPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { search } from "../../api/client";
import type { ActionResponse, ChannelDetail, ChannelHistorySearchResult, ChannelSummary, RoleSummary, Task } from "../../api/types";
import { AgentSessionTimeline } from "../../components/agent-session/AgentSessionTimeline";
import { buildChannelConversation } from "../../components/agent-session/projection";
import { formatTime } from "../../lib/format";
import emojiData from "@emoji-mart/data";
import Picker from "@emoji-mart/react";
import { Archive, ArrowUp, AtSign, Bell, Bold, Boxes, ChevronDown, Code, FileText, GitFork, Hash, Info, Italic, Link, List, ListOrdered, MessageSquare, MoreHorizontal, PlayCircle, Plus, Quote, Search, Send, Settings, Smile, SquareCode, Strikethrough, Trash2, Type, Underline, Users, Wrench, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { UIEvent as ReactUIEvent } from "react";
import type { ChannelPermissionProfile } from "../../app/sharedTypes";
import { TablePage, agentConversationScrollSignature, asRecordArray, asStringArray, channelIdOf, channelNameOf, recordString, recordValue, scrollElementToBottom, textValue } from "../../app/shared";
import { previewItemsFromRefs } from "../agent-session/previewRegistry";

type ChannelTab = "chat" | "workspace";

type ChannelDrawerKey = "members" | "attention" | "context" | "reports" | "workflow" | "settings";

type ComposerFormatAction = "bold" | "italic" | "underline" | "strike" | "link" | "ordered-list" | "unordered-list" | "quote" | "inline-code" | "code-block";


interface EmojiMartSelection {
  native?: string;
  shortcodes?: string;
}


interface ComposerAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
  lastModified: number;
}


interface PendingChannelMessage {
  id: string;
  text: string;
  ts: string;
  targets: string[];
  refs?: Record<string, unknown>;
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


function latestChannelRepliesByTarget(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const latest = new Map<string, Record<string, unknown>>();
  for (const row of rows) {
    const target = recordString(row, "target_member_id") || recordString(row, "member_id");
    const key = target || recordString(row, "request_id") || recordString(row, "event_id");
    if (!key) continue;
    const current = latest.get(key);
    if (!current || channelReplySortKey(row) >= channelReplySortKey(current)) {
      latest.set(key, row);
    }
  }
  return [...latest.values()];
}


function channelReplySortKey(row: Record<string, unknown>): string {
  return (
    recordString(row, "updated_at")
    || recordString(row, "created_at")
    || recordString(row, "ts")
    || recordString(row, "event_id")
    || recordString(row, "request_id")
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
      channelMessageRole(row) === "user"
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
    thread_id: "main",
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
      thread_id: "main",
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
  loadError,
  onAddAgent,
  onClearHistory,
  onDeleteChannel,
  onDrainReplies,
  onGenerateOwnerReport,
  onMarkRead,
  onNewChannel,
  onOpenChannel,
  onPostMessage,
  onRemoveMember,
  onRequestSynthesis,
  onSetMemberPermission,
  onSearchHistory,
  onSetDiscussionMode,
  onWorkflowRequest,
  selectedChannelId,
  workflowRoles,
}: {
  actionReady: boolean;
  actionResult: ActionResponse | null;
  channels: ChannelSummary[];
  detail: ChannelDetail | null;
  loadError: string | null;
  onAddAgent: () => void;
  onClearHistory: () => Promise<void>;
  onDeleteChannel: () => Promise<void>;
  onDrainReplies: () => Promise<void>;
  onGenerateOwnerReport: () => Promise<void>;
  onMarkRead: (threadId: string) => Promise<void>;
  onNewChannel: () => void;
  onOpenChannel: (channelId: string) => void;
  onPostMessage: (text: string, refs?: Record<string, unknown>) => Promise<void>;
  onRemoveMember: (memberId: string) => Promise<void>;
  onRequestSynthesis: (targetMemberId?: string) => Promise<void>;
  onSetMemberPermission: (memberId: string, permissionProfile: ChannelPermissionProfile) => Promise<void>;
  onSearchHistory: (query: string, threadId?: string) => Promise<ChannelHistorySearchResult>;
  onSetDiscussionMode: (mode: string, defaultResponderId?: string) => Promise<void>;
  onWorkflowRequest: (patternId: string, taskId: string, reason: string) => Promise<void>;
  selectedChannelId: string;
  workflowRoles: RoleSummary[];
}) {
  const [composerText, setComposerText] = useState("");
  const [postingCount, setPostingCount] = useState(0);
  const [composerError, setComposerError] = useState("");
  const [workflowDraft, setWorkflowDraft] = useState({ patternId: "", taskId: "", reason: "" });
  const [controlsBusy, setControlsBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<ChannelTab>("chat");
  const [drawer, setDrawer] = useState<ChannelDrawerKey | null>(null);
  const [channelSwitcherOpen, setChannelSwitcherOpen] = useState(false);
  const [channelSearch, setChannelSearch] = useState("");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyResult, setHistoryResult] = useState<ChannelHistorySearchResult | null>(null);
  const [historySearching, setHistorySearching] = useState(false);
  const [historySearchOpen, setHistorySearchOpen] = useState(false);
  const [formattingOpen, setFormattingOpen] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [composerAttachments, setComposerAttachments] = useState<ComposerAttachment[]>([]);
  const [pendingChannelMessages, setPendingChannelMessages] = useState<PendingChannelMessage[]>([]);
  const [activeChannelThreadId, setActiveChannelThreadId] = useState("main");
  const [channelSplitThreadId, setChannelSplitThreadId] = useState("");
  const [channelPinnedToBottom, setChannelPinnedToBottom] = useState(true);
  const [channelHasNewBelow, setChannelHasNewBelow] = useState(false);
  const [memberSearch, setMemberSearch] = useState("");
  const [memberMenuId, setMemberMenuId] = useState("");
  const [memberProfileId, setMemberProfileId] = useState("");
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const channelTimelineRef = useRef<HTMLDivElement | null>(null);
  const historyInputRef = useRef<HTMLInputElement | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const visibleChannels = channels.length
    ? channels
    : [{ channel_id: "ch-zaofu", name: "# zaofu", members: [], workflow_requests: [] } as ChannelSummary];
  const activeChannel = detail ?? visibleChannels.find((channel) => channelIdOf(channel) === selectedChannelId) ?? null;
  const conversationDetail = useMemo(
    () => channelDetailWithPendingMessage(detail, selectedChannelId, pendingChannelMessages),
    [detail, pendingChannelMessages, selectedChannelId],
  );
  const posting = postingCount > 0;
  useEffect(() => {
    setPendingChannelMessages((current) => current.filter((item) => !channelDetailHasMessage(detail, item)));
  }, [detail]);
  const channelConversation = useMemo(
    () => buildChannelConversation(conversationDetail, selectedChannelId, activeChannelThreadId),
    [activeChannelThreadId, conversationDetail, selectedChannelId],
  );
  const channelScrollSignature = agentConversationScrollSignature(
    channelConversation,
    activeChannelThreadId,
    [],
  );
  useEffect(() => {
    if (activeTab !== "chat") return;
    setChannelPinnedToBottom(true);
    setChannelHasNewBelow(false);
    scrollElementToBottom(channelTimelineRef.current);
  }, [activeChannelThreadId, activeTab, selectedChannelId]);
  useEffect(() => {
    if (activeTab !== "chat") return;
    const node = channelTimelineRef.current;
    if (!node) return;
    if (channelPinnedToBottom || isScrollElementNearBottom(node)) {
      scrollElementToBottom(node);
      setChannelHasNewBelow(false);
      return;
    }
    setChannelHasNewBelow(true);
  }, [activeTab, channelPinnedToBottom, channelScrollSignature]);
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
  const members = detail?.members ?? [];
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
  const syntheses = detail?.syntheses ?? [];
  const workflowRequests = detail?.workflow_requests ?? [];
  const replyRequests = detail?.reply_requests ?? [];
  const contextPacks = detail?.context_packs ?? [];
  const handoffs = detail?.handoffs ?? [];
  const stateUpdates = detail?.state_updates ?? [];
  const ownerReports = detail?.owner_reports ?? [];
  const automationReports = detail?.automation_reports ?? [];
  const discussionMode = recordString(detail?.discussion ?? {}, "mode", "manual_mention");
  const defaultResponderId = recordString(detail?.discussion ?? {}, "default_responder_id");
  const replyCapableMembers = members.filter((member) => {
    const status = recordString(member, "status");
    const memberType = recordString(member, "member_type");
    const permissions = stringArray(member["permissions"]);
    return status !== "removed" && status !== "suspended" && memberType !== "observer" && (!permissions.length || permissions.includes("message"));
  });
  const channelMemberIds = new Set(
    members.map((member) => recordString(member, "member_id")).filter(Boolean),
  );
  const latestReplyRowsByTarget = latestChannelRepliesByTarget(replyRequests).filter((request) => {
    const target = recordString(request, "target_member_id") || recordString(request, "member_id");
    return !target || channelMemberIds.has(target);
  });
  const pendingReplyRows = latestReplyRowsByTarget.filter((request) => {
    const status = recordString(request, "status", "pending");
    return status === "pending" || status === "queued" || status === "running";
  });
  const failedReplyRows = latestReplyRowsByTarget.filter((request) => {
    const status = recordString(request, "status");
    return status === "failed" || status === "rejected" || status === "escalated";
  });
  const attentionReplyRows = [...pendingReplyRows, ...failedReplyRows];
  const pendingReplies = pendingReplyRows.length;
  const attentionCount = pendingReplies + failedReplyRows.length;
  const mentionRows = (detail?.mentions_detected ?? []).filter((item): item is Record<string, unknown> => Boolean(recordValue(item)));
  const mentionDigests = buildChannelMentionDigests(mentionRows, messages as Record<string, unknown>[], channelMemberIds);
  const timeline = [
    ...messages,
    ...stateUpdates.map((item) => ({ ...item, role: "state_update", text: item.summary })),
  ] as Record<string, unknown>[];
  const drawerTitles: Record<ChannelDrawerKey, string> = {
    members: "Members",
    attention: "Attention",
    context: "Context",
    reports: "Reports",
    workflow: "Workflow",
    settings: "Channel Settings",
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
    setActiveChannelThreadId("main");
    setChannelSplitThreadId("");
    setMemberMenuId("");
    setMemberProfileId("");
    setMemberSearch("");
    setMentionOpen(false);
    setMentionQuery("");
    setHistorySearchOpen(false);
  }, [selectedChannelId]);
  function toggleDrawer(nextDrawer: ChannelDrawerKey) {
    setDrawer((current) => current === nextDrawer ? null : nextDrawer);
  }
  function channelMemberCount(channel: ChannelSummary): number {
    return channel.members?.length ?? 0;
  }
  function channelAttentionCount(channel: ChannelSummary): number {
    return (
      Number(channel.pending_reply_count ?? 0) +
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
    return recordString(member, "display_name") || recordString(member, "member_id") || "member";
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
    setActiveTab("chat");
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
    setActiveTab("chat");
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
    const added = Array.from(files).map((file) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(16).slice(2)}`,
      name: file.name,
      size: file.size,
      type: file.type,
      lastModified: file.lastModified,
    }));
    setComposerAttachments((current) => [...current, ...added]);
    focusComposer();
  }
  function removeAttachment(id: string) {
    setComposerAttachments((current) => current.filter((item) => item.id !== id));
  }
  function attachmentRefs(): Record<string, unknown> | undefined {
    if (!composerAttachments.length) return undefined;
    return {
      attachments: composerAttachments.map(({ id: _id, ...attachment }) => ({
        ...attachment,
        source: "browser-file-picker",
      })),
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
    setActiveTab("chat");
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
    const refs = attachmentRefs();
    const messageText = markdownText || composerAttachments.map((attachment) => `[attachment] ${attachment.name}`).join("\n");
    const pendingText = plainText;
    const pendingHtml = composerRef.current?.innerHTML ?? "";
    const pendingAttachments = composerAttachments;
    const pendingId = `pending-${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
    setComposerError("");
    setChannelPinnedToBottom(true);
    setChannelHasNewBelow(false);
    setPendingChannelMessages((current) => [...current, {
      id: pendingId,
      text: messageText,
      ts: new Date().toISOString(),
      targets: targetMembersForMessage(messageText),
      refs,
    }]);
    clearComposerEditor();
    setComposerAttachments([]);
    setMentionOpen(false);
    setMentionQuery("");
    setPostingCount((count) => count + 1);
    void onPostMessage(messageText, refs)
      .catch((error) => {
        setComposerError(error instanceof Error ? error.message : "message was not posted");
        if (!composerPlainText().trim()) restoreComposerEditor(pendingHtml, pendingText);
        setComposerAttachments((current) => current.length ? current : pendingAttachments);
      })
      .finally(() => {
        setPendingChannelMessages((current) => current.filter((item) => item.id !== pendingId));
        setPostingCount((count) => Math.max(0, count - 1));
      });
  }
  async function runControl(action: () => Promise<void>) {
    if (controlsBusy) return;
    setControlsBusy(true);
    try {
      await action();
    } finally {
      setControlsBusy(false);
    }
  }
  function messageMember(memberId: string) {
    if (!memberId) return;
    setActiveTab("chat");
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
                  <span className="channel-avatar small">{initials(displayName)}</span>
                  <div>
                    <strong>{displayName}</strong>
                    <span className="muted">{memberRoleLabel(member)}</span>
                  </div>
                </div>
                <span className="status-pill">{memberProvider(member)}</span>
                <span className="status-pill">{permissionProfile}</span>
                <span className={`status-pill status-${memberPresence(member)}`}>{memberPresence(member)}</span>
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
      <form
        className="channel-control-panel channel-control-card"
        onSubmit={(event) => {
          event.preventDefault();
          void runControl(async () => {
            await onWorkflowRequest(
              workflowDraft.patternId.trim(),
              workflowDraft.taskId.trim(),
              workflowDraft.reason.trim() || "requested from channel",
            );
            setWorkflowDraft({ patternId: "", taskId: "", reason: "" });
          });
        }}
      >
        <div className="inline-heading">
          <h3>Workflow Request</h3>
          <span className="muted">kernel gated</span>
        </div>
        <div className="channel-control-form-grid">
          <label className="channel-control-field">
            <span>Pattern</span>
            <input
              className="filter-input"
              placeholder="pattern id"
              value={workflowDraft.patternId}
              onChange={(event) => setWorkflowDraft({ ...workflowDraft, patternId: event.target.value })}
            />
          </label>
          <label className="channel-control-field">
            <span>Task</span>
            <input
              className="filter-input"
              placeholder="task id"
              value={workflowDraft.taskId}
              onChange={(event) => setWorkflowDraft({ ...workflowDraft, taskId: event.target.value })}
            />
          </label>
          <label className="channel-control-field channel-control-field-wide">
            <span>Reason</span>
            <input
              className="filter-input"
              placeholder="reason"
              value={workflowDraft.reason}
              onChange={(event) => setWorkflowDraft({ ...workflowDraft, reason: event.target.value })}
            />
          </label>
        </div>
        <div className="channel-control-actions">
          <button className="icon-button primary" disabled={!actionReady || controlsBusy || !workflowDraft.patternId.trim() || !workflowDraft.taskId.trim()} type="submit">
            <PlayCircle size={16} />
            Request
          </button>
        </div>
      </form>
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
  function renderReplyAttentionList() {
    return (
      <div className="channel-attention-section">
        <div className="inline-heading">
          <h3>Reply Queue</h3>
          <span className="muted">{attentionReplyRows.length} active</span>
        </div>
        {attentionReplyRows.length ? (
          <div className="channel-attention-list">
            {attentionReplyRows.map((row) => {
              const status = recordString(row, "status", "pending");
              const target = recordString(row, "target_member_id") || recordString(row, "member_id") || "agent";
              const reason = recordString(row, "reason") || recordString(row, "error") || recordString(row, "summary");
              const requestId = recordString(row, "request_id") || recordString(row, "event_id") || target;
              const ts = recordString(row, "updated_at") || recordString(row, "created_at") || recordString(row, "ts");
              return (
                <div className={`channel-attention-card ${failedReplyRows.includes(row) ? "failed" : ""}`} key={requestId}>
                  <div className="channel-attention-card-main">
                    <span className="channel-avatar tiny">{initials(target)}</span>
                    <div>
                      <strong>@{target}</strong>
                      <span className="muted">{reason || (status === "running" ? "reply is running" : "waiting for reply")}</span>
                    </div>
                  </div>
                  <div className="channel-attention-card-meta">
                    <span className={`status-pill status-${status}`}>{status}</span>
                    {ts ? <span className="mono muted">{formatTime(ts)}</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-attention-empty">
            <strong>No reply attention</strong>
            <span>All requested agent replies have settled.</span>
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
  function renderWorkspaceMetric(label: string, value: number | string, detail: string, Icon: LucideIcon) {
    return (
      <div className="channel-workspace-metric">
        <span className="channel-workspace-metric-icon"><Icon size={16} /></span>
        <div>
          <strong>{value}</strong>
          <span>{label}</span>
          <small>{detail}</small>
        </div>
      </div>
    );
  }
  function renderWorkspaceReplyFlow() {
    const latestReplies = latestWorkspaceRows(replyRequests, 7);
    const completed = countWorkspaceRows(replyRequests, (row) => workspaceStatusFromRow(row) === "completed");
    const failed = countWorkspaceRows(replyRequests, (row) => workspaceStatusFromRow(row) === "failed");
    return (
      <section className="channel-workspace-panel span-2">
        <div className="channel-workspace-panel-head">
          <div>
            <h3>Reply Flow</h3>
            <span className="muted">{replyRequests.length} requests across channel history</span>
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
          <div className="channel-workspace-empty">No reply requests yet.</div>
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
  function renderWorkspaceRoles() {
    return (
      <section className="channel-workspace-panel">
        <div className="channel-workspace-panel-head">
          <div>
            <h3>Workflow Roles</h3>
            <span className="muted">{workflowRoles.length} configured roles</span>
          </div>
        </div>
        {workflowRoles.length ? (
          <div className="channel-role-chip-list">
            {workflowRoles.map((role) => {
              const row = role as unknown as Record<string, unknown>;
              const id = recordString(row, "instance_id") || recordString(row, "name") || recordString(row, "role");
              const label = recordString(row, "name") || id || "role";
              const backend = recordString(row, "backend") || "-";
              const kind = recordString(row, "role_kind") || recordString(row, "origin") || "role";
              return (
                <div className="channel-role-chip" key={id || label}>
                  <strong>{label}</strong>
                  <span>{kind}</span>
                  <small>{backend}</small>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="channel-workspace-empty">No workflow roles configured.</div>
        )}
      </section>
    );
  }
  function renderWorkspaceRequests() {
    const activeWorkflowItems = [...workflowRequests, ...syntheses, ...handoffs].filter((item) => Boolean(recordValue(item))) as Record<string, unknown>[];
    return (
      <section className="channel-workspace-panel" data-testid="channel-workflow-surface">
        <div className="channel-workspace-panel-head">
          <div>
            <h3>Workflow Surface</h3>
            <span className="muted">{activeWorkflowItems.length} requests / handoffs</span>
          </div>
        </div>
        {activeWorkflowItems.length ? (
          <div className="channel-workspace-list compact">
            {latestWorkspaceRows(activeWorkflowItems, 5).map((row, index) => (
              <div
                className="channel-workspace-row compact"
                data-pattern-id={recordString(row, "pattern_id") || undefined}
                data-request-id={recordString(row, "request_id") || recordString(row, "event_id") || undefined}
                key={recordString(row, "event_id") || recordString(row, "request_id") || index}
              >
                <GitFork size={15} />
                <div>
                  <strong>{recordString(row, "pattern_id") || recordString(row, "type") || "workflow"}</strong>
                  <span className="muted">{recordString(row, "task_id") || recordString(row, "summary") || recordString(row, "reason") || "-"}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="channel-workspace-empty">No workflow requests or handoffs.</div>
        )}
      </section>
    );
  }
  function renderWorkspaceControls() {
    return (
      <section className="channel-workspace-panel channel-workspace-controls">
        <div className="channel-workspace-panel-head">
          <div>
            <h3>Controls</h3>
            <span className="muted">kernel-gated actions</span>
          </div>
          <span className={`metric-chip ${actionReady ? "" : "chip-warn"}`}>
            {actionReady ? "active" : "locked"}
          </span>
        </div>
        <div className="channel-control-panel flat channel-control-card">
          <div className="inline-heading">
            <h3>Discussion</h3>
            <span className="muted">{discussionMode}</span>
          </div>
          <div className="channel-control-stack">
            <label className="channel-control-row">
              <span className="channel-control-copy">
                <strong>Mode</strong>
                <small>How channel messages choose speakers.</small>
              </span>
              <select
                className="filter-input"
                disabled={!actionReady || controlsBusy}
                value={discussionMode}
                onChange={(event) => {
                  const mode = event.target.value;
                  void runControl(() => onSetDiscussionMode(mode, defaultResponderId));
                }}
              >
                <option value="manual_mention">manual mention</option>
                <option value="round_robin">round robin</option>
                <option value="priority">priority</option>
                <option value="leader_delegation">leader delegation</option>
                <option value="fanout_then_synthesis">fanout then synthesis</option>
                <option value="debate_judge">debate judge</option>
              </select>
            </label>
            <label className="channel-control-row">
              <span className="channel-control-copy">
                <strong>Default responder</strong>
                <small>Handles messages without an explicit @mention.</small>
              </span>
              <select
                className="filter-input"
                disabled={!actionReady || controlsBusy}
                value={defaultResponderId}
                onChange={(event) => {
                  const memberId = event.target.value;
                  void runControl(() => onSetDiscussionMode(discussionMode, memberId));
                }}
              >
                <option value="">no default responder</option>
                {replyCapableMembers.map((member) => {
                  const memberId = recordString(member, "member_id");
                  return <option key={memberId} value={memberId}>{memberDisplayName(member)}</option>;
                })}
              </select>
            </label>
          </div>
          <div className="channel-control-actions">
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onDrainReplies)}>
              Drain Replies
            </button>
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(() => onRequestSynthesis(defaultResponderId || undefined))}>
              <FileText size={16} />
              Synthesize
            </button>
          </div>
        </div>
        <div className="channel-control-panel flat channel-control-card">
          <div className="inline-heading">
            <h3>Owner</h3>
            <span className="muted">{ownerReports.length} reports</span>
          </div>
          <div className="channel-control-row read-only">
            <span className="channel-control-copy">
              <strong>Brief</strong>
              <small>Generate an owner-visible channel report.</small>
            </span>
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onGenerateOwnerReport)}>
              <FileText size={16} />
              Report
            </button>
          </div>
        </div>
        {renderWorkflowRequestForm()}
      </section>
    );
  }
  function renderChannelWorkspace() {
    return (
      <div className="channel-workspace-dashboard">
        <section className="channel-workspace-hero">
          {renderWorkspaceMetric("Members", members.length, `${boundMembers.length} runtime bound`, Users)}
          {renderWorkspaceMetric("Messages", messages.length, `${mentionDigests.length} mention events`, MessageSquare)}
          {renderWorkspaceMetric("Replies", replyRequests.length, `${pendingReplies} pending / ${failedReplyRows.length} failed`, Bell)}
          {renderWorkspaceMetric("Context", contextPacks.length, `${stateUpdates.length} state updates`, Boxes)}
          {renderWorkspaceMetric("Reports", ownerReports.length + automationReports.length, `${automationReports.length} automation`, FileText)}
        </section>
        <div className="channel-workspace-main-grid">
          {renderWorkspaceReplyFlow()}
          {renderWorkspaceContextFlow()}
          {renderWorkspaceRoles()}
          {renderWorkspaceRequests()}
          {renderWorkspaceControls()}
        </div>
      </div>
    );
  }
  function renderDrawerContent(activeDrawer: ChannelDrawerKey) {
    if (activeDrawer === "members") {
      return (
        <>
          <div className="channel-control-panel">
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
              Add people
            </button>
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
          <div className="channel-attention-strip">
            <span className="metric-chip">{pendingReplies} pending</span>
            <span className={`metric-chip ${failedReplyRows.length ? "chip-warn" : ""}`}>{failedReplyRows.length} failed</span>
            <span className="metric-chip">{mentionDigests.length} mentions</span>
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onDrainReplies)}>
              Drain Replies
            </button>
          </div>
          {renderReplyAttentionList()}
          {renderMentionEventList()}
        </>
      );
    }
    if (activeDrawer === "context") {
      return (
        <>
          <TablePage title="Context Packs" rows={contextPacks} embedded />
          <TablePage title="State Updates" rows={stateUpdates} embedded />
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
            <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onGenerateOwnerReport)}>
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
    if (activeDrawer === "workflow") {
      return (
        <>
          {renderWorkflowRequestForm()}
          <TablePage title="Workflow Requests" rows={workflowRequests} embedded />
          <TablePage title="Synthesis / Handoffs" rows={[...syntheses, ...handoffs]} embedded />
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
            className="filter-input"
            disabled={!actionReady || controlsBusy}
            value={discussionMode}
            onChange={(event) => {
              const mode = event.target.value;
              void runControl(() => onSetDiscussionMode(mode, defaultResponderId));
            }}
          >
            <option value="manual_mention">manual mention</option>
            <option value="round_robin">round robin</option>
            <option value="priority">priority</option>
            <option value="leader_delegation">leader delegation</option>
            <option value="fanout_then_synthesis">fanout then synthesis</option>
            <option value="debate_judge">debate judge</option>
          </select>
          <select
            className="filter-input"
            disabled={!actionReady || controlsBusy}
            value={defaultResponderId}
            onChange={(event) => {
              const memberId = event.target.value;
              void runControl(() => onSetDiscussionMode(discussionMode, memberId));
            }}
          >
            <option value="">no default responder</option>
            {replyCapableMembers.map((member) => {
              const memberId = recordString(member, "member_id");
              return <option key={memberId} value={memberId}>{memberDisplayName(member)}</option>;
            })}
          </select>
        </div>
        <div className="channel-control-panel">
          <div className="inline-heading">
            <h3>Danger Zone</h3>
            <span className="muted">event gated</span>
          </div>
          <button className="icon-button" disabled={!actionReady || controlsBusy} type="button" onClick={() => void runControl(onClearHistory)}>
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
    <section className="channel-page channel-page-chat">
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
            <span className="muted mono">{selectedChannelId}</span>
          </div>
          <div className="channel-tabs" role="tablist" aria-label="Channel view">
            <button
              aria-selected={activeTab === "chat"}
              className={`channel-tab ${activeTab === "chat" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveTab("chat")}
            >
              <MessageSquare size={16} />
              Chat
            </button>
            <button
              aria-selected={activeTab === "workspace"}
              className={`channel-tab ${activeTab === "workspace" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveTab("workspace")}
            >
              <Info size={16} />
              Details
            </button>
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
            aria-pressed={drawer === "members"}
            className={`channel-tool-button ${drawer === "members" ? "active" : ""}`}
            title="Members"
            type="button"
            onClick={() => toggleDrawer("members")}
          >
            <Users size={16} />
            <span>{members.length}</span>
          </button>
          <button
            aria-pressed={drawer === "attention"}
            className={`channel-tool-button ${drawer === "attention" ? "active" : ""}`}
            title="Attention"
            type="button"
            onClick={() => toggleDrawer("attention")}
          >
            <Bell size={16} />
            <span>{attentionCount}</span>
          </button>
          <button
            aria-pressed={drawer === "context"}
            className={`channel-tool-button ${drawer === "context" ? "active" : ""}`}
            title="Context"
            type="button"
            onClick={() => toggleDrawer("context")}
          >
            <FileText size={16} />
          </button>
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
      {loadError ? <div className="notice">{loadError}</div> : null}
      {actionResult && (
        actionResult?.action === "channel-create"
        || actionResult?.action === "channel-invite-member"
        || actionResult?.action === "channel-update-member-permission"
        || actionResult?.action === "channel-remove-member"
        || actionResult?.action === "channel-delete"
        || actionResult?.action === "channel-clear-history"
        || actionResult?.action === "channel-post-message"
        || actionResult?.action === "channel.add_member"
        || actionResult?.action === "channel-owner-report"
      ) && shouldShowChannelActionNotice(actionResult) ? (
        <div className={`notice ${actionResult.ok ? "notice-ok" : ""}`}>
          <span className="mono">{actionResult.status}</span> {channelActionNoticeText(actionResult)}
        </div>
      ) : null}
      <div className={`channel-shell ${drawer ? "has-drawer" : ""}`}>
        <main className="channel-main">
          {activeTab === "chat" ? (
            <>
              {renderHistorySearchPanel()}
              <div
                ref={channelTimelineRef}
                className="channel-timeline"
                aria-live="polite"
                onScroll={handleChannelTimelineScroll}
              >
                <AgentSessionTimeline
                  activeThreadId={activeChannelThreadId}
                  allowSplit={channelHasMultipleThreads}
                  allowPreviewSplit
                  compact={!channelHasMultipleThreads}
                  conversation={channelConversation}
                  collapseCompletedRunDetails
                  emptyBody="Post a message or @mention an agent to start a channel run."
                  emptyTitle="No channel messages"
                  minimalRunActivity
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
                        <button aria-label={`Remove ${attachment.name}`} type="button" onClick={() => removeAttachment(attachment.id)}>
                          <X size={12} />
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
                {composerError ? <div className="notice compact-error channel-composer-error">{composerError}</div> : null}
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
                          <Picker
                            data={emojiData}
                            emojiButtonSize={30}
                            emojiSize={21}
                            locale="zh"
                            maxFrequentRows={2}
                            navPosition="top"
                            onEmojiSelect={handleEmojiSelect}
                            previewPosition="none"
                            searchPosition="top"
                            set="native"
                            theme="light"
                          />
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
                    disabled={!actionReady || (!composerText.trim() && !composerAttachments.length)}
                    title={posting ? "Sending in background" : "Send"}
                    type="submit"
                  >
                    <Send size={16} />
                  </button>
                </div>
              </form>
            </>
          ) : (
            renderChannelWorkspace()
          )}
        </main>

        {drawer ? (
          <aside className="channel-drawer">
            <div className="channel-drawer-header">
              <div>
                <h3>{drawerTitles[drawer]}</h3>
                <span className="muted mono">{selectedChannelId}</span>
              </div>
              <button className="channel-tool-button" title="Close drawer" type="button" onClick={() => setDrawer(null)}>
                <X size={16} />
              </button>
            </div>
            <div className="channel-drawer-content">
              {renderDrawerContent(drawer)}
            </div>
          </aside>
        ) : null}
      </div>
    </section>
  );
}
