import { createContext, Fragment, useContext, useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { AlertTriangle, Bot, Check, CheckCircle2, ChevronRight, CircleSlash, Clock3, Copy, FileText, GitCompare, Hourglass, Info, ListChecks, Loader2, Maximize2, MessageSquare, Minimize2, Paperclip, PauseCircle, Pin, Reply, XCircle } from "lucide-react";
import type {
  AgentConversation,
  AgentSessionActionProposal,
  AgentSessionCard,
  AgentSessionMessage,
  AgentSessionPart,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
  AgentProviderCapability,
  AgentSessionRun,
  AgentSessionStatus,
  AgentSessionThread,
  AgentSessionTurn,
} from "./types";
import { getAgentSessionRawOutput } from "../../api/client";
import { MarkdownText } from "./MarkdownText";
import { completedRunNotices } from "./notices";
import { actionImpactRows, previewItemsFromRefs, type PreviewItem } from "./previewRegistry";
import { formatOutputStats, formatToolDuration, getOutputPreview, prettyPrintIfJson, rawOutputLabel, rawOutputRefFromRefs, type RawOutputRef } from "./toolOutput";
import { actionPresentation } from "./actionPresentation";

// Opens a preview part into the fullscreen side pane. null when preview-split
// isn't available (compact/docked), so preview cards render non-clickable.
const PreviewOpenContext = createContext<((part: AgentSessionPart) => void) | null>(null);
import { segmentRunParts, type ToolRunSegment } from "./toolGrouping";
import { cleanToolTitle, iconForToolName } from "./toolIcon";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { runStartTimestamp, toolCallCount } from "./liveRunIndicator";
import { ApproveInteractionActions, PlanInteractionForm } from "./AgentInteractionControls";
import { AgentThreadNavigation } from "./AgentThreadNavigation";
import {
  contributionReferencePresentation,
  contributionRowLabel,
  presentContribution,
} from "./contributionPresentation";

interface AgentSessionTimelineProps {
  conversation: AgentConversation;
  activeThreadId: string;
  compact?: boolean;
  showThreadChips?: boolean;
  allowSplit?: boolean;
  /** Enable the side-by-side preview pane (chat | preview). Surfaces with room
   *  opt in: kanban-agent fullscreen, channel full-page. */
  allowPreviewSplit?: boolean;
  showRunDetails?: boolean;
  showRunProvider?: boolean;
  collapseCompletedRunDetails?: boolean;
  compactRunHeader?: boolean;
  minimalRunActivity?: boolean;
  splitThreadId?: string;
  extraCards?: AgentSessionCard[];
  actionBusyId?: string;
  onActiveThreadChange?: (threadId: string) => void;
  onSplitThreadChange?: (threadId: string) => void;
  onApproveProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onRejectProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onReviseProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onSubmitPlan?: (request: AgentSessionPlanRequest, response: AgentSessionPlanResponse, cardId: string) => void;
  onChatAboutPlan?: (request: AgentSessionPlanRequest, cardId: string) => void;
  onAnswerQuestion?: (card: AgentSessionCard) => void;
  onCancelQueued?: (cardId: string) => void;
  onCancelRun?: (runId: string) => void;
  onAdoptResearchResult?: (card: AgentSessionCard, cardId: string) => void;
  onReplyMessage?: (message: AgentSessionMessage, threadId: string) => void;
  onPinMessage?: (message: AgentSessionMessage, threadId: string) => void;
  providerCapabilities?: AgentProviderCapability[];
  emptyTitle?: string;
  emptyBody?: string;
}

export function AgentSessionTimeline({
  conversation,
  activeThreadId,
  compact = false,
  showThreadChips = true,
  allowSplit = false,
  allowPreviewSplit = false,
  showRunDetails = true,
  showRunProvider = true,
  collapseCompletedRunDetails = false,
  compactRunHeader = false,
  minimalRunActivity = false,
  splitThreadId = "",
  extraCards = [],
  actionBusyId = "",
  onActiveThreadChange,
  onSplitThreadChange,
  onApproveProposal,
  onRejectProposal,
  onReviseProposal,
  onSubmitPlan,
  onChatAboutPlan,
  onAnswerQuestion,
  onCancelQueued,
  onCancelRun,
  onAdoptResearchResult,
  onReplyMessage,
  onPinMessage,
  providerCapabilities = [],
  emptyTitle = "No messages",
  emptyBody = "Start a conversation to see agent runs, tools, and proposals.",
}: AgentSessionTimelineProps) {
  const [splitPercent, setSplitPercent] = useState(() => readSplitPercent(conversation.id));
  // A code/diff/artifact preview opened into a side pane (fullscreen only).
  const [previewPart, setPreviewPart] = useState<AgentSessionPart | null>(null);
  const channelChatMode = conversation.surface === "channel_group";
  const activeThread = threadById(conversation, activeThreadId) ?? conversation.threads[0];
  const splitThread = allowSplit && !compact && splitThreadId
    ? threadById(conversation, splitThreadId)
    : null;
  // Preview-split (chat | preview) takes priority over thread-split. Gated by
  // allowPreviewSplit (surfaces opt in when wide enough), not `compact`.
  const showPreview = Boolean(previewPart) && allowPreviewSplit;
  const threadPanes = splitThread && splitThread.id !== activeThread?.id
    ? [activeThread, splitThread].filter(Boolean)
    : [activeThread].filter(Boolean);
  const chatPanes = showPreview ? [activeThread].filter(Boolean) : threadPanes;
  const splitActive = chatPanes.length > 1 || showPreview;
  useEffect(() => {
    if (splitActive) saveSplitPercent(conversation.id, splitPercent);
  }, [conversation.id, splitActive, splitPercent]);
  const splitStyle: CSSProperties | undefined = splitActive
    ? { "--agent-split-left": `${splitPercent}%` } as CSSProperties
    : undefined;

  return (
    <div className={`agent-session ${compact ? "compact" : ""} ${channelChatMode ? "channel-chat-mode" : ""}`.trim()}>
      {showThreadChips ? (
        <AgentThreadNavigation
          activeThreadId={activeThreadId}
          allowSplit={allowSplit && !compact}
          conversation={conversation}
          onActiveThreadChange={onActiveThreadChange}
          onSplitThreadChange={onSplitThreadChange}
          splitThreadId={splitThreadId}
        />
      ) : null}

      {chatPanes.length ? (
        <PreviewOpenContext.Provider value={allowPreviewSplit ? setPreviewPart : null}>
          <div className={`agent-session-panes ${splitActive ? "split" : ""} ${showPreview ? "agent-preview-split" : ""}`.trim()} style={splitStyle}>
            {chatPanes.map((thread, index) => thread ? (
              <Fragment key={thread.id}>
                {index === 1 ? <SplitDivider onResize={setSplitPercent} /> : null}
                <ThreadPane
                  actionBusyId={actionBusyId}
                  compact={compact}
                  emptyBody={emptyBody}
                  emptyTitle={emptyTitle}
                  extraCards={thread.id === activeThread?.id ? extraCards : []}
                  onAnswerQuestion={onAnswerQuestion}
                  onApproveProposal={onApproveProposal}
                  onRejectProposal={onRejectProposal}
                  onReviseProposal={onReviseProposal}
                  onSubmitPlan={onSubmitPlan}
                  onChatAboutPlan={onChatAboutPlan}
                  onCancelQueued={onCancelQueued}
                  onCancelRun={onCancelRun}
                  onAdoptResearchResult={onAdoptResearchResult}
                  onReplyMessage={onReplyMessage}
                  onPinMessage={onPinMessage}
                  providerCapabilities={providerCapabilities}
                  channelChatMode={channelChatMode}
                  collapseCompletedRunDetails={collapseCompletedRunDetails}
                  compactRunHeader={compactRunHeader}
                  minimalRunActivity={minimalRunActivity}
                  showRunDetails={showRunDetails}
                  showRunProvider={showRunProvider}
                  thread={thread}
                />
              </Fragment>
            ) : null)}
            {showPreview && previewPart ? (
              <>
                <SplitDivider onResize={setSplitPercent} />
                <PreviewPane onClose={() => setPreviewPart(null)} part={previewPart} projectId={conversation.projectId} />
              </>
            ) : null}
          </div>
        </PreviewOpenContext.Provider>
      ) : (
        <div className="agent-session-empty">
          <Bot size={24} />
          <strong>{emptyTitle}</strong>
          <span>{emptyBody}</span>
        </div>
      )}
    </div>
  );
}

function ThreadPane({
  actionBusyId,
  compact,
  emptyBody,
  emptyTitle,
  extraCards,
  onApproveProposal,
  onRejectProposal,
  onReviseProposal,
  onSubmitPlan,
  onChatAboutPlan,
  onAnswerQuestion,
  onCancelQueued,
  onCancelRun,
  onAdoptResearchResult,
  onReplyMessage,
  onPinMessage,
  providerCapabilities,
  channelChatMode,
  collapseCompletedRunDetails,
  compactRunHeader,
  minimalRunActivity,
  showRunDetails,
  showRunProvider,
  thread,
}: {
  actionBusyId: string;
  compact: boolean;
  emptyBody: string;
  emptyTitle: string;
  extraCards: AgentSessionCard[];
  onApproveProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onRejectProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onReviseProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onSubmitPlan?: (request: AgentSessionPlanRequest, response: AgentSessionPlanResponse, cardId: string) => void;
  onChatAboutPlan?: (request: AgentSessionPlanRequest, cardId: string) => void;
  onAnswerQuestion?: (card: AgentSessionCard) => void;
  onCancelQueued?: (cardId: string) => void;
  onCancelRun?: (runId: string) => void;
  onAdoptResearchResult?: (card: AgentSessionCard, cardId: string) => void;
  onReplyMessage?: (message: AgentSessionMessage, threadId: string) => void;
  onPinMessage?: (message: AgentSessionMessage, threadId: string) => void;
  providerCapabilities: AgentProviderCapability[];
  channelChatMode: boolean;
  collapseCompletedRunDetails: boolean;
  compactRunHeader: boolean;
  minimalRunActivity: boolean;
  showRunDetails: boolean;
  showRunProvider: boolean;
  thread: AgentSessionThread;
}) {
  return (
    <section className="agent-session-pane" aria-label={thread.title}>
      {!compact ? (
        <div className="agent-pane-heading">
          <span className={`agent-thread-dot ${statusClass(thread.status)}`} />
          <strong>{thread.title}</strong>
          <span className="muted">{thread.status}</span>
        </div>
      ) : null}
      <div className="agent-turn-list">
        {thread.turns.length ? thread.turns.map((turn) => {
          const attachedContributionIds = new Set(
            turn.cards
              .filter((card) => card.kind === "contribution" && turn.runs.some((run) => run.id === card.runId))
              .map((card) => card.id),
          );
          const remainingCards = turn.cards.filter((card) => !attachedContributionIds.has(card.id));
          return (
          <article
            className={`agent-turn-group ${isSettledTurn(turn) ? "is-settled" : "is-active"}`}
            key={turn.id}
          >
            {turn.user ? (
              <div className={`agent-user-message ${turn.user.role}`}>
                <div className="agent-message-meta">
                  <span>{turn.user.label}</span>
                  {turn.user.origin?.channel ? (
                    <span
                      className="agent-origin-chip"
                      title={`from ${turn.user.origin.channel}: ${turn.user.origin.chat_id}`}
                    >
                      {turn.user.origin.channel === "feishu" ? "飞书" : turn.user.origin.channel}
                    </span>
                  ) : null}
                  <span className="mono muted">{timeLabel(turn.user.ts)}</span>
                </div>
                {/* Render markdown on both surfaces — the kanban user bubble
                    previously used plain <p>, showing raw **bold** / `code`. */}
                <MarkdownText content={turn.user.content || "-"} />
                <AttachmentChips refs={turn.user.refs} />
                {channelChatMode && (onReplyMessage || onPinMessage) ? (
                  <div className="agent-message-actions">
                    {onReplyMessage ? (
                      <button
                        aria-label="Reply to message"
                        className="icon-button"
                        title="Reply"
                        type="button"
                        onClick={() => onReplyMessage(turn.user!, thread.id)}
                      >
                        <Reply size={13} />
                      </button>
                    ) : null}
                    {onPinMessage ? (
                      <button
                        aria-label={turn.user.refs?.pinned ? "Unpin message" : "Pin message"}
                        className="icon-button"
                        title={turn.user.refs?.pinned ? "Unpin" : "Pin"}
                        type="button"
                        onClick={() => onPinMessage(turn.user!, thread.id)}
                      >
                        <Pin size={13} />
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {turn.runs.map((run) => (
              <RunBlock
                compact={compact}
                contributionCards={turn.cards.filter((card) => card.kind === "contribution" && card.runId === run.id)}
                key={run.id}
                onCancelRun={onCancelRun}
                providerCapabilities={providerCapabilities}
                channelChatMode={channelChatMode}
                collapseCompletedRunDetails={collapseCompletedRunDetails}
                compactRunHeader={compactRunHeader}
                minimalRunActivity={minimalRunActivity}
                run={run}
                showRunDetails={showRunDetails}
                showRunProvider={showRunProvider}
              />
            ))}
            {remainingCards.length ? (
              <StackedCards
                actionBusyId={actionBusyId}
                cards={remainingCards}
                onAnswerQuestion={onAnswerQuestion}
                onApproveProposal={onApproveProposal}
                onRejectProposal={onRejectProposal}
                onReviseProposal={onReviseProposal}
                onSubmitPlan={onSubmitPlan}
                onChatAboutPlan={onChatAboutPlan}
                onCancelQueued={onCancelQueued}
                onAdoptResearchResult={onAdoptResearchResult}
              />
            ) : null}
          </article>
          );
        }) : (
          <div className="agent-session-empty inline">
            <MessageSquare size={20} />
            <strong>{emptyTitle}</strong>
            <span>{emptyBody}</span>
          </div>
        )}
      </div>
      {extraCards.length ? (
        <StackedCards
          actionBusyId={actionBusyId}
          cards={extraCards}
          onAnswerQuestion={onAnswerQuestion}
          onApproveProposal={onApproveProposal}
          onRejectProposal={onRejectProposal}
          onReviseProposal={onReviseProposal}
          onSubmitPlan={onSubmitPlan}
          onChatAboutPlan={onChatAboutPlan}
          onCancelQueued={onCancelQueued}
          onAdoptResearchResult={onAdoptResearchResult}
        />
      ) : null}
    </section>
  );
}

function RunBlock({
  compact,
  contributionCards,
  run,
  onCancelRun,
  providerCapabilities,
  channelChatMode,
  collapseCompletedRunDetails,
  compactRunHeader,
  minimalRunActivity,
  showRunDetails,
  showRunProvider,
}: {
  compact: boolean;
  contributionCards: AgentSessionCard[];
  run: AgentSessionRun;
  onCancelRun?: (runId: string) => void;
  providerCapabilities: AgentProviderCapability[];
  channelChatMode: boolean;
  collapseCompletedRunDetails: boolean;
  compactRunHeader: boolean;
  minimalRunActivity: boolean;
  showRunDetails: boolean;
  showRunProvider: boolean;
}) {
  const capability = providerCapabilities.find((item) => item.provider === run.provider);
  const canCancel = (
    onCancelRun
    && capability?.cancel !== false
    && (run.status === "streaming" || run.status === "submitted")
  );
  // Chat surfaces (channel + kanban-agent) use a prose-first layout: a minimal
  // header (small dot + @member, no provider pill / status text) and the reply
  // rendered ABOVE the folded activity, so the response leads.
  const chatMode = channelChatMode || collapseCompletedRunDetails;
  const runWorking = run.status === "streaming" || run.status === "submitted";
  const hideQueuedStatusOnly = compact
    && (run.status === "queued" || run.status === "waiting_input")
    && (!run.parts.length || run.parts.every((part) => part.kind === "status" && (part.state === "queued" || part.state === "waiting_input")));
  const visibleParts = hideQueuedStatusOnly ? [] : run.parts;
  const replyParts = visibleParts.filter(isReplyPart);
  const detailParts = visibleParts.filter((part) => !isReplyPart(part));
  const thinkingParts = detailParts.filter((part) => part.kind === "thinking");
  // stream-ux axis 2: while the run works and no reply text has arrived, the
  // header renders the live indicator (pulsing dots + member + elapsed) as a
  // SINGLE line instead of a bare dot over blank space. Suppressed only when
  // a live ThinkingPart already shows its own "Thinking · Ns" line (kanban
  // thinking stream — channel hides those).
  const chatToolParts = detailParts.filter((part) => part.kind !== "thinking" && part.kind !== "status");
  const chatVisibleThinking = channelChatMode ? [] : thinkingParts;
  const showThinkingIndicator = chatMode && runWorking && !replyParts.length && !chatVisibleThinking.length;
  // A kanban chat header with no member, no indicator, and no cancel action
  // would render a lone status dot on its own line — drop the row entirely.
  const showChatHeader = showThinkingIndicator || Boolean(run.memberId) || Boolean(canCancel);
  const headerStatusLabel = channelChatMode && thinkingParts.length && (run.status === "streaming" || run.status === "submitted")
    ? `${statusLabel(run.status)} · Thinking`
    : statusLabel(run.status);
  const renderPartList = (parts: AgentSessionPart[], className = "", segmented = false) => (
    <div className={`agent-part-list ${className}`.trim()}>
      {segmented
        ? segmentRunParts(parts, run.status).map((segment, index) => (
            segment.kind === "part" ? (
              <PartRenderer channelChatMode={channelChatMode} key={segment.part.id} part={segment.part} runStatus={run.status} />
            ) : (
              <ToolStepsSegment channelChatMode={channelChatMode} key={`steps-${index}`} runStatus={run.status} segment={segment} />
            )
          ))
        : parts.map((part) => (
            <PartRenderer channelChatMode={channelChatMode} key={part.id} part={part} runStatus={run.status} />
          ))}
    </div>
  );
  // Prose-first detail block: thinking -> subtle "Thought",
  // tools → "See N steps"; drops status placeholders and the "Activity · N
  // events" wrapper. Returns null when there's nothing meaningful to show.
  const renderChatDetails = (parts: AgentSessionPart[]) => {
    // Channel = multi-member group chat: each agent's internal reasoning is
    // noise, so the "Thought" line is suppressed there. The kanban-agent
    // single session keeps it because a single-agent session benefits from a
    // compact reasoning trace.
    const thinking = channelChatMode ? [] : parts.filter((part) => part.kind === "thinking");
    // ZF-E2E-RACING UI (2026-07-11): status parts that survive on a COMPLETED
    // run are real notices (e.g. the blocked-route "未自动扇出(防风暴)" line
    // from chat-e2e F5), not progress placeholders — those are synthesized
    // only while a run is working. Dropping them left a bare presence dot:
    // four mystery bubbles in one racing-channel discussion. Render them as
    // muted system lines instead.
    // frontend-stress OBS-1 (2026-07-15): keep only real notices on a completed
    // run; progress placeholders collapse (see notices.ts).
    const notices = completedRunNotices(parts, run.status);
    // Tool trail is working-state only (operator decision 2026-07-16): live
    // activity (current tool + "Tool call N" + folded earlier steps) stays
    // visible while the run works; once the reply lands the trail is dropped
    // on BOTH chat surfaces — the conversation reads prose-first, and deep
    // audit lives in trace/events, not the chat bubble.
    const showTools = runWorking;
    const tools = showTools ? parts.filter((part) => part.kind !== "thinking" && part.kind !== "status") : [];
    if (!thinking.length && !tools.length && !notices.length) return null;
    return (
      <div className="agent-chat-details">
        {notices.length ? renderPartList(notices, "agent-notice-list") : null}
        {thinking.length ? renderPartList(thinking, "agent-thinking-list") : null}
        {tools.length ? renderPartList(tools, "agent-process-list", true) : null}
      </div>
    );
  };
  const statusPart = (): AgentSessionPart => ({
    id: `${run.id}-pending`,
    runId: run.id,
    kind: "status",
    state: run.status,
    title: statusLabel(run.status),
    summary: statusLabel(run.status),
  });
  return (
    <div className={`agent-run-block ${statusClass(run.status)} ${channelChatMode ? "channel-run-block" : ""}`.trim()}>
      {!chatMode || showChatHeader ? (
      <div className={`agent-run-header ${chatMode ? "agent-run-header-min" : ""}`.trim()}>
        {chatMode ? (
          <div className="agent-run-title agent-run-title-min">
            {showThinkingIndicator ? (
              // Single status line: pulse dots + @member + "Thinking · 12s".
              <ThinkingIndicator
                label={chatToolParts.length ? "Working" : "Thinking"}
                startedAt={runStartTimestamp(run)}
                who={run.memberId}
              />
            ) : (
              <>
                <span className={`agent-thread-dot ${statusClass(run.status)}`} />
                {run.memberId ? <span className="agent-run-who">@{run.memberId}</span> : null}
              </>
            )}
          </div>
        ) : (
          <div className="agent-run-title">
            {!compactRunHeader ? statusIcon(run.status) : null}
            <strong>{run.memberId ? `@${run.memberId}` : "Agent"}</strong>
            {compactRunHeader ? statusIcon(run.status) : null}
            {showRunDetails && !channelChatMode ? <RunCapabilityPopover capability={capability} run={run} /> : null}
            {(showRunProvider || channelChatMode) && run.provider ? <span className="status-pill">{providerLabel(run.provider)}</span> : null}
            {!compactRunHeader ? (
              <span className={`agent-run-status ${statusClass(run.status)}`}>{headerStatusLabel}</span>
            ) : null}
          </div>
        )}
        {canCancel ? (
          <div className="agent-run-actions">
            <button className="agent-inline-button" type="button" onClick={() => onCancelRun?.(run.id)}>
              Cancel
            </button>
          </div>
        ) : null}
      </div>
      ) : null}
      {!hideQueuedStatusOnly ? (
        chatMode ? (
          // WHILE STREAMING the block reads in wall-clock order — thinking /
          // live activity first, the growing reply below it (operator report
          // 2026-07-16: the reply above a still-ticking "Thinking" read
          // backwards). Once the run completes, prose-first: reply leads and
          // the details fold underneath.
          runWorking ? (
            <>
              {renderChatDetails(detailParts)}
              {replyParts.length ? renderPartList(replyParts, "agent-reply-list") : null}
            </>
          ) : (
            <>
              {replyParts.length ? renderPartList(replyParts, "agent-reply-list") : null}
              {renderChatDetails(detailParts)}
            </>
          )
        ) : visibleParts.length ? (
          renderPartList(visibleParts, "", true)
        ) : (
          <div className="agent-part-list">
            <PartRenderer part={statusPart()} channelChatMode={channelChatMode} runStatus={run.status} />
          </div>
        )
      ) : null}
      {contributionCards.map((card) => <ContributionCard card={card} key={card.id} />)}
    </div>
  );
}

function RunCapabilityPopover({
  capability,
  run,
}: {
  capability?: AgentProviderCapability;
  run: AgentSessionRun;
}) {
  return (
    <details className="agent-capability-popover">
      <summary title="Run details">
        <Info size={14} />
      </summary>
      <dl>
        <dt>run</dt><dd className="mono">{run.id}</dd>
        <dt>provider</dt><dd>{providerLabel(run.provider || "-")}</dd>
        <dt>session</dt><dd className="mono">{run.providerSessionId || "unknown"}</dd>
        <dt>started</dt><dd>{timeLabel(run.startedAt) || "-"}</dd>
        <dt>tokens</dt><dd>{usageLabel(run.usage)}</dd>
        <dt>stream</dt><dd>{boolLabel(capability?.streaming)}</dd>
        <dt>cancel</dt><dd>{boolLabel(capability?.cancel)}</dd>
        <dt>resume</dt><dd>{boolLabel(capability?.resume)}</dd>
        <dt>interrupt</dt><dd>{boolLabel(capability?.interrupt)}</dd>
        <dt>cost</dt><dd>{boolLabel(capability?.cost)}</dd>
        <dt>test mode</dt><dd>{boolLabel(capability?.test_mode)}</dd>
        <dt>tools</dt><dd>{boolLabel(capability?.tools)}</dd>
        <dt>context</dt><dd>{capability?.context || "unknown"}</dd>
        <dt>workdir</dt><dd>{capability?.workdir || "project"}</dd>
      </dl>
    </details>
  );
}

function isReplyPart(part: AgentSessionPart): boolean {
  return part.kind === "text" || part.kind === "error";
}

function PartRenderer({
  channelChatMode,
  part,
  runStatus,
}: {
  channelChatMode: boolean;
  part: AgentSessionPart;
  runStatus: AgentSessionStatus;
}) {
  const openPreview = useContext(PreviewOpenContext);
  const rawOutput = rawOutputRefFromRefs(part.refs);
  if (part.kind === "thinking") return <ThinkingPart part={part} runStatus={runStatus} />;
  if (isPreviewPart(part)) return <PreviewPart part={part} />;
  if (isProcessPart(part)) return <ToolOrStatusPart part={part} />;
  const isError = part.kind === "error" || part.state === "failed";
  const body = part.content || part.summary || "-";
  const isStreaming = runStatus === "streaming" || runStatus === "submitted" || part.state === "streaming" || part.state === "submitted";
  const canCopy = !isError && !isStreaming && body.trim().length > 0 && body !== "-";
  return (
    <div className={`agent-text-part ${isError ? "error" : ""}`}>
      {shouldShowTextPartTitle(part) ? <span className="agent-part-title">{part.title}</span> : null}
      {/* Render markdown on BOTH surfaces — kanban-agent replies previously fell
          back to plain <p>, showing raw markdown (**bold**, `code`, lists). */}
      <MarkdownText content={body} formatStandaloneJson isStreaming={isStreaming} />
      {/* Strip source-event provenance so a prose reply doesn't carry an
          `evt-... text` chip; keep real artifact refs. */}
      <AttachmentChips refs={omitProvenanceRefs(part.refs)} />
      {rawOutput?.raw_ref && openPreview ? (
        <button
          className="agent-inline-button"
          type="button"
          title="Open full raw output"
          onClick={() => openPreview(rawOutputPreviewPart(part, rawOutput))}
        >
          <Maximize2 size={12} />
          Open raw
        </button>
      ) : null}
      {canCopy ? <ReplyCopyButton text={body} /> : null}
    </div>
  );
}

const PROVENANCE_REF_KEYS = ["source_event_id", "source_event_seq", "source_event_type", "schema_version", "task_id"];

function omitProvenanceRefs(refs?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!refs) return refs;
  const out = { ...refs };
  for (const key of PROVENANCE_REF_KEYS) delete out[key];
  return out;
}

// Small copy affordance under a finished reply.
function ReplyCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) return;
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }).catch(() => undefined);
  };
  return (
    <button aria-label={copied ? "Copied" : "Copy reply"} className="agent-reply-copy" type="button" title={copied ? "Copied" : "Copy"} onClick={copy}>
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

function isProcessPart(part: AgentSessionPart): boolean {
  return ["tool", "tool_call", "tool_result", "command", "test_result", "status", "file_read", "file_change", "trace_ref", "action_proposal", "approval_request", "context_ledger"].includes(part.kind);
}

function isPreviewPart(part: AgentSessionPart): boolean {
  return ["code_preview", "diff_preview", "artifact_preview"].includes(part.kind);
}

function shouldShowTextPartTitle(part: AgentSessionPart): boolean {
  if (!part.title) return false;
  return !["Reply", "Response"].includes(part.title);
}

function ThinkingPart({ part, runStatus }: { part: AgentSessionPart; runStatus: AgentSessionStatus }) {
  const streaming = runStatus === "streaming" || part.state === "streaming" || part.state === "submitted";
  const [expanded, setExpanded] = useState(streaming);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    setExpanded(streaming);
  }, [streaming]);
  useEffect(() => {
    if (!streaming) return undefined;
    const id = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, [streaming]);
  // While streaming: live elapsed. Once done: the thinking DURATION
  // (updatedAt − startedAt), not now − startedAt — otherwise an old finished
  // thought shows a huge "26m" drift instead of the seconds it actually took.
  const elapsed = useMemo(() => {
    if (streaming) return elapsedLabel(part.startedAt, tick);
    const seconds = toolDurationSeconds(part);
    return seconds !== undefined ? `for ${formatToolDuration(seconds)}` : "";
  }, [streaming, part.startedAt, part.updatedAt, tick]);
  const preview = (part.summary || part.content || "").replace(/\s+/g, " ").slice(0, 96);
  return (
    <div className="agent-thinking-part">
      <button className="agent-thinking-header" type="button" onClick={() => setExpanded((value) => !value)}>
        <Hourglass size={14} />
        <span className={streaming ? "agent-shimmer" : ""}>{streaming ? "Thinking" : "Thought"}</span>
        {elapsed ? <span className="mono muted">{elapsed}</span> : null}
        {!expanded && preview ? <span className="muted truncate">{preview}</span> : null}
        <ChevronRight className={expanded ? "open" : ""} size={14} />
      </button>
      {expanded && part.content ? (
        <div className="agent-thinking-body">
          <MarkdownText content={part.content} isStreaming={streaming} />
        </div>
      ) : null}
    </div>
  );
}

function ToolOrStatusPart({ part }: { part: AgentSessionPart }) {
  const openPreview = useContext(PreviewOpenContext);
  const isError = part.state === "failed" || part.kind === "error";
  const body = part.content || part.summary || part.state;
  const isCommand = part.kind === "command" || /exit code|stdout|stderr|pytest|uv run|npm test/i.test(body);
  const isTest = part.kind === "test_result" || /failed tests?|passed|pytest|test result/i.test(`${part.title} ${body}`);
  // Elapsed/spinner apply ONLY to real tool operations. A "status"
  // placeholder (e.g. the "Started"/"Queued"/"Working" part) is not a timed
  // op — showing a tool timer on it makes a finished run tick forever.
  const isToolish = TOOLISH_PART_KINDS.includes(part.kind);
  const running = isToolish && (part.state === "streaming" || part.state === "submitted");
  const liveSeconds = useLiveSeconds(running, part.startedAt);
  const duration = isToolish ? (running ? liveSeconds : toolDurationSeconds(part)) : undefined;
  // Parameters (best-effort from the source event payload) + output. Any tool
  // with either becomes expandable, not just command/test.
  // ToolCard (trigger row → Parameters + Output panels).
  const parameters = toolParameters(part);
  const output = part.content || (isCommand || isTest ? part.summary || "" : "");
  const rawOutput = rawOutputRefFromRefs(part.refs);
  const hasDetail = Boolean(parameters) || Boolean(output) || Boolean(rawOutput?.raw_ref);
  const detailLabel = isTest ? "test evidence" : isCommand ? "command output" : "details";
  return (
    <div className={`agent-tool-row ${isError ? "error" : ""} ${isCommand ? "command-card" : ""} ${isTest ? "test-card" : ""}`.trim()}>
      <ToolStatusIcon isError={isError} isTest={isTest} isToolish={isToolish} part={part} />
      <span className="agent-tool-title">{cleanToolTitle(part.title)}</span>
      <span className="agent-tool-summary">{part.summary || body}</span>
      {duration !== undefined ? <span className="agent-tool-duration mono muted">{formatToolDuration(duration)}</span> : null}
      {hasDetail ? (
        <details className="agent-tool-detail">
          <summary>{detailLabel}</summary>
          {parameters ? (
            <div className="agent-tool-section">
              <span className="agent-tool-section-label">parameters</span>
              <ToolOutput output={parameters} />
            </div>
          ) : null}
          {output ? (
            <div className="agent-tool-section">
              {parameters ? <span className="agent-tool-section-label">output</span> : null}
              <ToolOutput output={output} />
              {rawOutput?.raw_ref && openPreview ? (
                <button
                  className="agent-inline-button"
                  type="button"
                  title="Open full raw output"
                  onClick={() => openPreview(rawOutputPreviewPart(part, rawOutput))}
                >
                  <Maximize2 size={12} />
                  Open raw
                </button>
              ) : null}
            </div>
          ) : rawOutput?.raw_ref && openPreview ? (
            <div className="agent-tool-section">
              <button
                className="agent-inline-button"
                type="button"
                title="Open full raw output"
                onClick={() => openPreview(rawOutputPreviewPart(part, rawOutput))}
              >
                <Maximize2 size={12} />
                Open raw
              </button>
            </div>
          ) : null}
        </details>
      ) : null}
      <AttachmentChips refs={part.refs} />
    </div>
  );
}

// Best-effort tool parameters from the source event payload (the part model
// carries no structured args dict). Returns pretty JSON for an args object, a
// bare command string, or null when nothing usable is present.
function toolParameters(part: AgentSessionPart): string | null {
  const payload = part.sourceEvent && typeof part.sourceEvent.payload === "object"
    ? part.sourceEvent.payload as Record<string, unknown>
    : null;
  if (!payload) return null;
  const args = payload.args ?? payload.input ?? payload.parameters ?? payload.arguments ?? payload.tool_input;
  if (args && typeof args === "object") return JSON.stringify(args, null, 2);
  if (typeof payload.command === "string" && payload.command.trim()) return payload.command;
  return null;
}

function rawOutputPreviewPart(part: AgentSessionPart, rawOutput: RawOutputRef): AgentSessionPart {
  return {
    ...part,
    id: `${part.id}-raw-output`,
    kind: "artifact_preview",
    title: `${part.title || "Output"} raw`,
    summary: rawOutputLabel(rawOutput),
    content: rawOutput.preview || part.content || part.summary || "",
    contentRef: rawOutput.raw_ref,
    refs: {
      ...(part.refs ?? {}),
      raw_output: rawOutput,
    },
  };
}

// A contiguous tool run: older tools fold into a "See N steps" details,
// the streaming tail (or in-progress tools) render directly so current
// activity stays visible. Count labels the full run, not just the folded
// part. See toolGrouping.segmentRunParts.
function ToolStepsSegment({
  channelChatMode,
  runStatus,
  segment,
}: {
  channelChatMode: boolean;
  runStatus: AgentSessionStatus;
  segment: ToolRunSegment;
}) {
  const renderParts = (parts: AgentSessionPart[]) =>
    parts.map((part) => (
      <PartRenderer channelChatMode={channelChatMode} key={part.id} part={part} runStatus={runStatus} />
    ));
  // stream-ux axis 3: the live run labels its visible tail with a tool-call
  // ordinal ("Tool call 5"), so a long grounding turn shows progress instead
  // of a black box. Completed runs keep the plain "See N steps" fold.
  const liveCallCount = segment.live ? toolCallCount([...segment.grouped, ...segment.standalone]) : 0;
  const liveCountLine = liveCallCount > 0 ? (
    <div className="agent-tool-live-count mono">Tool call {liveCallCount}</div>
  ) : null;
  if (segment.grouped.length === 0) return <>{liveCountLine}{renderParts(segment.standalone)}</>;
  const n = segment.total;
  return (
    <>
      <details className="agent-tool-steps">
        <summary>
          <ChevronRight className="agent-run-detail-chevron" size={13} />
          <span>See {n} step{n === 1 ? "" : "s"}</span>
        </summary>
        <div className="agent-tool-steps-body">{renderParts(segment.grouped)}</div>
      </details>
      {liveCountLine}
      {renderParts(segment.standalone)}
    </>
  );
}

// Tool kinds that represent a timed operation (get spinner/elapsed + category
// icon). Status / proposal / ledger parts are placeholders — never timed.
const TOOLISH_PART_KINDS = ["tool", "tool_call", "tool_result", "command", "test_result", "file_read", "file_change"];

// 4-state status icon: a running tool shows a spinner; error / cancelled take
// priority; tools get a category icon; status placeholders get a clock (no
// spinner — they are not running operations).
function ToolStatusIcon({ isError, isTest, isToolish, part }: { isError: boolean; isTest: boolean; isToolish: boolean; part: AgentSessionPart }) {
  if (isToolish && (part.state === "streaming" || part.state === "submitted")) return <Loader2 className="agent-tool-spin" size={14} />;
  if (isError) return <AlertTriangle size={14} />;
  if (part.state === "cancelled" || part.state === "stale") return <CircleSlash size={14} />;
  if (isTest) return <ListChecks size={14} />;
  if (isToolish) {
    const Icon = iconForToolName(part.toolName || cleanToolTitle(part.title));
    return <Icon size={14} />;
  }
  return <Clock3 size={14} />;
}

function toolDurationSeconds(part: AgentSessionPart): number | undefined {
  if (!part.startedAt || !part.updatedAt) return undefined;
  const start = Date.parse(part.startedAt);
  const end = Date.parse(part.updatedAt);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return undefined;
  return Math.max(0, (end - start) / 1000);
}

// Live elapsed for an in-progress tool; ticks every 500ms while active.
function useLiveSeconds(active: boolean, startedAt?: string): number | undefined {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => setTick((value) => value + 1), 500);
    return () => window.clearInterval(id);
  }, [active]);
  if (!startedAt) return undefined;
  const start = Date.parse(startedAt);
  if (!Number.isFinite(start)) return undefined;
  return Math.max(0, (Date.now() - start) / 1000);
}

// Output preview with line/char caps, gradient fade, hidden-count stats,
// expand/collapse, and copy-of-full-text.
function ToolOutput({ output }: { output: string }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const formatted = useMemo(() => prettyPrintIfJson(output), [output]);
  const collapsedPreview = useMemo(() => getOutputPreview(formatted), [formatted]);
  const preview = useMemo(() => getOutputPreview(formatted, expanded), [formatted, expanded]);
  const canExpand = collapsedPreview.isTruncated;
  useEffect(() => setExpanded(false), [formatted]);
  const copy = () => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) return;
    void navigator.clipboard.writeText(output).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => undefined);
  };
  return (
    <div className="agent-tool-output">
      <div className={`agent-tool-output-body ${canExpand && !expanded ? "clipped" : ""}`.trim()}>
        <pre>{preview.text}</pre>
        {canExpand && !expanded ? <div className="agent-tool-output-fade" /> : null}
      </div>
      <div className="agent-tool-output-bar">
        <span className="mono muted">{formatOutputStats(expanded ? preview : collapsedPreview)}</span>
        <span className="agent-tool-output-actions">
          {canExpand ? (
            <button className="agent-inline-button" type="button" onClick={() => setExpanded((value) => !value)}>
              {expanded ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
              {expanded ? "Collapse" : "Expand"}
            </button>
          ) : null}
          <button aria-label={copied ? "Copied" : "Copy output"} className="agent-inline-button" type="button" onClick={copy}>
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </span>
      </div>
    </div>
  );
}

function previewProfileOf(part: AgentSessionPart): "diff" | "code" | "artifact" {
  return part.kind === "diff_preview" ? "diff" : part.kind === "code_preview" ? "code" : "artifact";
}

function PreviewPart({ part }: { part: AgentSessionPart }) {
  const profile = previewProfileOf(part);
  // In fullscreen, the card opens into the side pane; otherwise it stays inline.
  const openPreview = useContext(PreviewOpenContext);
  return (
    <div className={`agent-preview-card ${openPreview ? "openable" : ""}`.trim()}>
      <div className="agent-preview-card-head">
        {profile === "diff" ? <GitCompare size={14} /> : <FileText size={14} />}
        <strong>{part.title || profile}</strong>
        <span className="badge">{profile}</span>
        {openPreview ? (
          <button aria-label="Open preview" className="agent-preview-open" type="button" title="Open in split preview" onClick={() => openPreview(part)}>
            <Maximize2 size={13} />
          </button>
        ) : null}
      </div>
      {part.summary ? <p>{part.summary}</p> : null}
      {part.content ? <MarkdownText content={part.content} /> : null}
      <AttachmentChips refs={part.refs} />
    </div>
  );
}

// Fullscreen side pane: renders a single preview (code/diff/artifact) beside
// the chat, so the conversation stays readable while the artifact is inspected.
function PreviewPane({ part, onClose, projectId }: { part: AgentSessionPart; onClose: () => void; projectId?: string }) {
  const profile = previewProfileOf(part);
  const rawOutput = rawOutputRefFromRefs(part.refs);
  const rawRef = part.contentRef || rawOutput?.raw_ref || "";
  const [rawContent, setRawContent] = useState("");
  const [rawNextOffset, setRawNextOffset] = useState<number | null>(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [rawError, setRawError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setRawContent("");
    setRawNextOffset(null);
    setRawError("");
    if (!rawRef) return undefined;
    let cancelled = false;
    setRawLoading(true);
    void getAgentSessionRawOutput(projectId, rawRef).then((page) => {
      if (cancelled) return;
      setRawContent(page.content);
      setRawNextOffset(page.next_offset);
    }).catch((err) => {
      if (!cancelled) setRawError(err instanceof Error ? err.message : String(err));
    }).finally(() => {
      if (!cancelled) setRawLoading(false);
    });
    return () => { cancelled = true; };
  }, [projectId, rawRef]);
  const loadMoreRaw = () => {
    if (!rawRef || rawNextOffset === null || rawLoading) return;
    setRawLoading(true);
    void getAgentSessionRawOutput(projectId, rawRef, { offset: rawNextOffset }).then((page) => {
      setRawContent((current) => `${current}${page.content}`);
      setRawNextOffset(page.next_offset);
      setRawError("");
    }).catch((err) => {
      setRawError(err instanceof Error ? err.message : String(err));
    }).finally(() => setRawLoading(false));
  };
  const copyRaw = () => {
    const text = rawContent || part.content || "";
    if (!text || typeof navigator === "undefined" || !navigator.clipboard?.writeText) return;
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }).catch(() => undefined);
  };
  return (
    <section className="agent-preview-pane" aria-label="Preview">
      <header className="agent-preview-pane-head">
        {profile === "diff" ? <GitCompare size={14} /> : <FileText size={14} />}
        <strong className="truncate">{part.title || profile}</strong>
        <span className="badge">{profile}</span>
        {rawOutput ? <span className="mono muted">{rawOutputLabel(rawOutput)}</span> : null}
        <button aria-label={copied ? "Copied" : "Copy preview"} className="agent-inline-button" type="button" onClick={copyRaw}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
        <button aria-label="Close preview" className="agent-inline-button agent-preview-close" type="button" onClick={onClose}>
          <XCircle size={15} />
        </button>
      </header>
      <div className="agent-preview-pane-body">
        {part.summary ? <p className="muted">{part.summary}</p> : null}
        {rawRef ? (
          <>
            {rawLoading && !rawContent ? <p className="muted">Loading raw output...</p> : null}
            {rawError ? <p className="agent-action-warning">{rawError}</p> : null}
            {rawContent ? (
              <div className="agent-tool-output raw">
                <div className="agent-tool-output-body">
                  <pre>{rawContent}</pre>
                </div>
                {rawNextOffset !== null ? (
                  <div className="agent-tool-output-bar">
                    <span className="mono muted">partial raw output loaded</span>
                    <button className="agent-inline-button" disabled={rawLoading} type="button" onClick={loadMoreRaw}>
                      {rawLoading ? "Loading" : "Load more"}
                    </button>
                  </div>
                ) : null}
              </div>
            ) : !rawLoading ? <p className="muted">No raw output content.</p> : null}
          </>
        ) : part.content ? <MarkdownText content={part.content} /> : <p className="muted">No preview content.</p>}
        <AttachmentChips refs={part.refs} />
      </div>
    </section>
  );
}

function AttachmentChips({ refs }: { refs?: Record<string, unknown> }) {
  const items = refItems(refs);
  if (!items.length) return null;
  return (
    <div className="agent-ref-chips" aria-label="Context ledger">
      {items.map((item, index) => (
        <span className={`agent-ref-chip profile-${item.profile || "text"}`} key={`${item.kind}-${item.id || item.name}-${index}`}>
          <Paperclip size={12} />
          <span>{item.name || item.id || item.kind}</span>
          {item.meta ? <small>{item.meta}</small> : null}
        </span>
      ))}
    </div>
  );
}

function StackedCards({
  actionBusyId,
  cards,
  onAnswerQuestion,
  onApproveProposal,
  onRejectProposal,
  onReviseProposal,
  onSubmitPlan,
  onChatAboutPlan,
  onCancelQueued,
  onAdoptResearchResult,
}: {
  actionBusyId: string;
  cards: AgentSessionCard[];
  onAnswerQuestion?: (card: AgentSessionCard) => void;
  onApproveProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onRejectProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onReviseProposal?: (proposal: AgentSessionActionProposal, cardId: string) => void;
  onSubmitPlan?: (request: AgentSessionPlanRequest, response: AgentSessionPlanResponse, cardId: string) => void;
  onChatAboutPlan?: (request: AgentSessionPlanRequest, cardId: string) => void;
  onCancelQueued?: (cardId: string) => void;
  onAdoptResearchResult?: (card: AgentSessionCard, cardId: string) => void;
}) {
  return (
    <div className="agent-stacked-cards">
      {cards.map((card) => {
        if (card.payload?.preparing === true) {
          return (
            <div
              className="agent-stack-card plan"
              data-agent-card-kind="preparing"
              data-testid="agent-card-preparing"
              key={card.id}
            >
              <div className="agent-action-receipt muted">
                <Loader2 aria-hidden="true" className="spin" size={14} />
                <span className="agent-plan-receipt-copy">
                  <small>Preparing</small>
                  <strong>{card.title}</strong>
                </span>
              </div>
            </div>
          );
        }
        const isPlan = card.kind === "plan" || card.kind === "question";
        const isApprove = card.kind === "approve" || card.kind === "proposal";
        const isResult = card.kind === "workflow-result";
        if (card.kind === "contribution") {
          return <ContributionCard card={card} key={card.id} />;
        }
        const planCompleted = Boolean(isPlan && card.planRequest?.response);
        const compatibilityClass = isPlan
          ? "plan question"
          : isApprove
            ? "approve proposal"
            : card.kind;
        const proposalExecuted = card.payload?.resolution === "executed";
        const presentation = card.proposal
          ? actionPresentation(card.proposal.action)
          : null;
        const receiptSubject = card.proposal
          ? actionImpactRows(card.proposal.action, card.proposal.payload)
            .find((row) => row.label !== "action")?.value
          : "";
        return (
        <div
          className={`agent-stack-card ${compatibilityClass} ${card.status === "completed" ? "is-completed" : ""}`}
          data-agent-card-kind={card.kind}
          data-plan-request-id={isPlan ? card.planRequest?.requestEventId : undefined}
          data-plan-request-revision={isPlan ? card.planRequest?.revision : undefined}
          data-proposal-action={isApprove ? card.proposal?.action : undefined}
          data-testid={`agent-card-${isPlan ? "plan" : isApprove ? "approve" : card.kind}`}
          key={card.id}
        >
          {!planCompleted && !(isApprove && card.status === "completed") ? (
            <div className="agent-interaction-copy">
              <span className="agent-card-kind">
                {isPlan ? "Question" : isApprove ? "Confirmation" : isResult ? "Result" : card.kind}
              </span>
              <strong>{presentation?.title || card.title}</strong>
              {card.body ? <p>{card.body}</p> : null}
              {isApprove && card.proposal ? (
                <ActionPreviewBody proposal={card.proposal} refs={card.refs} />
              ) : isResult ? (
                <WorkflowResultIdentity refs={card.refs} />
              ) : card.refs ? (
                <AttachmentChips refs={card.refs} />
              ) : null}
            </div>
          ) : null}
          {isApprove && card.proposal ? (
            card.status === "completed" ? (
              <div className={`agent-action-receipt ${proposalExecuted ? "" : "muted"}`}>
                {proposalExecuted
                  ? <Check aria-hidden="true" size={14} />
                  : <XCircle aria-hidden="true" size={14} />}
                <span className="agent-plan-receipt-copy">
                  <small>{receiptSubject || card.title}</small>
                  <strong>
                    {proposalExecuted
                      ? presentation?.completedLabel || "Action completed"
                      : "Cancelled"}
                  </strong>
                </span>
              </div>
            ) : (
              <ApproveInteractionActions
                busy={actionBusyId === card.id}
                proposal={card.proposal}
                onApprove={onApproveProposal
                  ? () => onApproveProposal(card.proposal as AgentSessionActionProposal, card.id)
                  : undefined}
                onReject={onRejectProposal
                  ? () => onRejectProposal(card.proposal as AgentSessionActionProposal, card.id)
                  : undefined}
                onRevise={onReviseProposal
                  ? () => onReviseProposal(card.proposal as AgentSessionActionProposal, card.id)
                  : undefined}
              />
            )
          ) : null}
          {isPlan && card.planRequest ? (
            card.status === "stale" ? (
              <div className="agent-plan-receipt muted">
                <CircleSlash aria-hidden="true" size={14} />
                <span>Superseded</span>
              </div>
            ) : (
              <PlanInteractionForm
                busy={actionBusyId === card.id}
                request={card.planRequest}
                onChatAbout={onChatAboutPlan
                  ? () => onChatAboutPlan(card.planRequest as AgentSessionPlanRequest, card.id)
                  : undefined}
                onSubmit={onSubmitPlan
                  ? (response) => onSubmitPlan(card.planRequest as AgentSessionPlanRequest, response, card.id)
                  : undefined}
              />
            )
          ) : null}
          {isPlan && !card.planRequest && onAnswerQuestion ? (
            <button className="agent-inline-button primary" type="button" onClick={() => onAnswerQuestion(card)}>
              {card.actionLabel || "Answer"}
            </button>
          ) : null}
          {card.kind === "queue" && onCancelQueued ? (
            <button className="agent-inline-button" type="button" onClick={() => onCancelQueued(card.id)}>
              Cancel
            </button>
          ) : null}
          {isResult ? (
            card.status === "completed" ? (
              <div className="agent-action-receipt">
                <Check aria-hidden="true" size={14} />
                <span className="agent-plan-receipt-copy">
                  <small>{card.title}</small>
                  <strong>Adopted</strong>
                </span>
              </div>
            ) : onAdoptResearchResult ? (
              <button
                className="agent-inline-button primary"
                disabled={actionBusyId === card.id}
                type="button"
                onClick={() => onAdoptResearchResult(card, card.id)}
              >
                {actionBusyId === card.id
                  ? <Loader2 className="spin" size={14} />
                  : <CheckCircle2 size={14} />}
                Adopt result
              </button>
            ) : null
          ) : null}
        </div>
      )})}
    </div>
  );
}

function isSettledTurn(turn: AgentSessionTurn): boolean {
  const runActive = turn.runs.some((run) => (
    run.status === "streaming"
    || run.status === "submitted"
    || run.status === "queued"
    || run.status === "waiting_input"
  ));
  const cardActive = turn.cards.some((card) => (
    card.status !== "completed" && card.status !== "stale" && card.status !== "cancelled"
  ));
  return !runActive && !cardActive;
}

function ContributionCard({ card }: { card: AgentSessionCard }) {
  const payload = card.payload ?? {};
  const presentation = presentContribution(payload);
  const references = contributionReferencePresentation(payload, card.refs);
  if (!presentation.sections.length && !references.total) return null;
  return (
    <div
      className="agent-contribution-card"
      data-agent-card-kind="contribution"
      data-testid="agent-card-contribution"
    >
      {presentation.sections.flatMap((section) => section.visibleRows.map((row, index) => {
        const label = contributionRowLabel(section.key, row.label);
        return (
          <div className={`agent-contribution-action tone-${section.key}`} key={row.id || `${section.key}-${index}`}>
            <span aria-hidden="true" className="agent-contribution-action-icon">
              {section.key === "risks" ? <AlertTriangle size={14} /> : <GitCompare size={14} />}
            </span>
            <span className="agent-contribution-action-copy">
              <strong>{section.title}</strong>
              {label ? <small>{label}</small> : null}
              <span>{row.text}</span>
            </span>
          </div>
        );
      }))}
      {references.total ? (
        <details className="agent-contribution-evidence">
          <summary>
            <Paperclip aria-hidden="true" size={12} />
            {references.label}
            <ChevronRight aria-hidden="true" className="agent-contribution-chevron" size={13} />
          </summary>
          <AttachmentChips refs={references.refs} />
        </details>
      ) : null}
    </div>
  );
}

function WorkflowResultIdentity({
  refs,
}: {
  refs?: Record<string, unknown>;
}) {
  const artifactRef = String(refs?.artifact_ref ?? "").trim();
  const artifactDigest = String(refs?.artifact_digest ?? "").trim();
  if (!artifactRef && !artifactDigest) return null;
  return (
    <div className="agent-action-preview">
      <dl>
        {artifactRef ? (
          <>
            <dt>artifact</dt>
            <dd className="mono">{artifactRef}</dd>
          </>
        ) : null}
        {artifactDigest ? (
          <>
            <dt>digest</dt>
            <dd className="mono">{artifactDigest}</dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}

function ActionPreviewBody({ proposal, refs }: { proposal: AgentSessionActionProposal; refs?: Record<string, unknown> }) {
  const rows = actionImpactRows(proposal.action, proposal.payload);
  const summaryLabels = new Set([
    "channel",
    "name",
    "objective",
    "patch",
    "task",
    "template",
    "title",
    "topic",
    "workflow",
  ]);
  const userRows = rows.filter((row) => row.label !== "action");
  const preferredRows = userRows.filter((row) => summaryLabels.has(row.label));
  const summaryRows = (preferredRows.length ? preferredRows : userRows).slice(0, 3);
  const detailRows = rows.filter((row) => !summaryRows.includes(row));
  const hasRefs = refItems(refs).length > 0;
  const isPatch = proposal.action === "apply-patch-proposal";
  return (
    <div className="agent-action-preview">
      {summaryRows.length ? <dl>
        {summaryRows.map((row) => (
          <Fragment key={`${row.label}-${row.value}`}>
            <dt>{row.label}</dt>
            <dd className={row.label === "task" || row.label === "patch" ? "mono" : ""}>{row.value}</dd>
          </Fragment>
        ))}
      </dl> : null}
      {proposal.validationError ? <p className="agent-action-warning">{proposal.validationError}</p> : null}
      {isPatch ? <p className="agent-action-warning">Patch apply is gated: preview, dirty-tree guard, audit, and rollback artifact are required before execution.</p> : null}
      {detailRows.length || hasRefs ? (
        <details className="agent-interaction-details">
          <summary>Details</summary>
          {detailRows.length ? <dl>
            {detailRows.map((row) => (
              <Fragment key={`${row.label}-${row.value}`}>
                <dt>{row.label}</dt>
                <dd className={row.label === "task" || row.label === "patch" ? "mono" : ""}>{row.value}</dd>
              </Fragment>
            ))}
          </dl> : null}
          {hasRefs ? <AttachmentChips refs={refs} /> : null}
        </details>
      ) : null}
    </div>
  );
}

function threadById(conversation: AgentConversation, threadId: string): AgentSessionThread | undefined {
  return conversation.threads.find((thread) => thread.id === threadId);
}

function SplitDivider({ onResize }: { onResize: (value: number) => void }) {
  function startDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const grid = event.currentTarget.parentElement;
    if (!grid) return;
    const rect = grid.getBoundingClientRect();
    const move = (next: PointerEvent) => {
      const ratio = ((next.clientX - rect.left) / rect.width) * 100;
      onResize(Math.min(72, Math.max(28, Math.round(ratio))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    move(event.nativeEvent);
  }
  return (
    <button
      aria-label="Resize split pane"
      className="agent-split-divider"
      type="button"
      onPointerDown={startDrag}
    />
  );
}

function splitStorageKey(conversationId: string): string {
  return `zf.agentSessionSplit.${conversationId}`;
}

function readSplitPercent(conversationId: string): number {
  if (typeof window === "undefined") return 50;
  const value = Number(window.localStorage.getItem(splitStorageKey(conversationId)));
  return Number.isFinite(value) && value >= 28 && value <= 72 ? value : 50;
}

function saveSplitPercent(conversationId: string, value: number): void {
  if (typeof window !== "undefined") window.localStorage.setItem(splitStorageKey(conversationId), String(value));
}

function statusClass(status: AgentSessionStatus): string {
  if (status === "streaming" || status === "submitted") return "streaming";
  if (status === "failed") return "failed";
  if (status === "cancelled" || status === "stale") return "cancelled";
  if (status === "waiting_input" || status === "queued") return "waiting";
  if (status === "completed") return "completed";
  return "idle";
}

function statusIcon(status: AgentSessionStatus) {
  const className = "agent-run-status-icon";
  if (status === "failed") return <XCircle className={className} size={15} />;
  if (status === "completed") return <CheckCircle2 className={className} size={15} />;
  if (status === "cancelled" || status === "stale") return <PauseCircle className={className} size={15} />;
  if (status === "streaming" || status === "submitted") return <Hourglass className={className} size={15} />;
  return <Bot className={className} size={15} />;
}

function statusLabel(status: AgentSessionStatus): string {
  if (status === "streaming" || status === "submitted") return "Working";
  if (status === "queued" || status === "waiting_input") return "Waiting";
  if (status === "completed") return "Done";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  if (status === "stale") return "Stale";
  return "Idle";
}

function timeLabel(value?: string): string {
  return value ? value.slice(11, 19) || value : "";
}

function elapsedLabel(startedAt?: string, tick = 0): string {
  void tick;
  if (!startedAt) return "";
  const started = Date.parse(startedAt);
  if (!Number.isFinite(started)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (seconds < 1) return "";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function providerLabel(provider: string): string {
  if (provider === "claude-headless" || provider === "claude-code") return "Claude";
  if (provider === "codex-headless" || provider === "codex") return "Codex";
  return provider || "-";
}

function usageLabel(usage?: Record<string, unknown>): string {
  if (!usage) return "unknown";
  const input = Number(usage.input_tokens ?? usage.input ?? 0);
  const output = Number(usage.output_tokens ?? usage.output ?? 0);
  if (!input && !output) return "unknown";
  return `${input.toLocaleString()} in / ${output.toLocaleString()} out`;
}

function boolLabel(value: boolean | undefined): string {
  if (value === undefined) return "unknown";
  return value ? "yes" : "no";
}

type RefChipItem = PreviewItem;

function refItems(refs?: Record<string, unknown>): RefChipItem[] {
  return previewItemsFromRefs(refs);
}
