import type {
  ChannelDetail,
  ChannelDiscussionAttention,
} from "../../api/types";

export type ChannelDiscussionAttentionAction =
  | "activity"
  | "questions"
  | "result"
  | "synthesize"
  | "restart";

export interface ChannelDiscussionAttentionPresentation {
  label: string;
  summary: string;
  tone: "info" | "warning" | "ready" | "blocked" | "done";
  action: ChannelDiscussionAttentionAction;
  actionLabel: string;
  visible: boolean;
}

export interface ChannelDiscussionControlPolicy {
  canStartOrRestart: boolean;
  canDrainReplies: boolean;
  canSynthesize: boolean;
  restart: boolean;
  startLabel: "Start discussion" | "Restart discussion";
}

export function selectDiscussionStartSource<T extends Record<string, unknown>>(
  messages: T[], threadId: string, restart: boolean, requirementMessageId: string,
): T | undefined {
  const candidates = messages.filter((message) => (
    String(message.thread_id || "main") === threadId
    && (message.role === "user" || message.member_id === "operator")
    && [message.text, message.message, message.summary].some((value) => typeof value === "string" && value.trim())
  ));
  const isReviewPrompt = (message: T) => {
    const refs = message.refs && typeof message.refs === "object"
      ? message.refs as Record<string, unknown> : {};
    return Boolean(refs.consensus_review_id || refs.source_requirement_message_id);
  };
  if (restart) {
    const markedSource = [...candidates].reverse().find((message) => {
      const refs = message.refs && typeof message.refs === "object"
        ? message.refs as Record<string, unknown> : {};
      return refs.evaluation_source_document === true;
    });
    if (markedSource) return markedSource;
    const pinned = candidates.find((message) => message.message_id === requirementMessageId);
    if (pinned && !isReviewPrompt(pinned)) return pinned;
  }
  return [...candidates].reverse().find((message) => !isReviewPrompt(message));
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function selectChannelDiscussionAttention(
  detail: ChannelDetail | null | undefined,
  preferredThreadId: string,
): ChannelDiscussionAttention | null {
  return detail?.discussion_attention?.[preferredThreadId] ?? null;
}

export function presentChannelDiscussionAttention(
  attention: ChannelDiscussionAttention,
): ChannelDiscussionAttentionPresentation {
  const executionState = attention.execution_state ?? (
    attention.active_agent_count > 0
      ? "running"
      : attention.state === "needs_input"
        ? "ready"
        : attention.state
  );
  const attentionKind = attention.attention_kind ?? (
    attention.owner_question_count > 0
      ? "question"
      : attention.state === "needs_input"
        ? "review"
        : "none"
  );
  const transition = attention.blocks_transition || (
    attentionKind === "question" ? "synthesis" : "consensus"
  );

  if (attention.reason === "member_invitation_pending") {
    return { label: "等待成员邀请决定", summary: "批准后成员将正式参评；邀请卡位于当前讨论中。",
      tone: "warning", action: "activity", actionLabel: "", visible: true };
  }
  if (attention.reason === "context_pack_rejected") {
    return { label: "评审来源需要处理", summary: "来源上下文未通过检查，后续评审尚未派发。请查看 Activity 中的具体原因。",
      tone: "blocked", action: "activity", actionLabel: "查看原因", visible: true };
  }
  if (attention.reason === "contribution_missing") {
    return { label: "角色贡献尚未完成", summary: `缺少 ${attention.missing_frozen_members?.join("、") || "必需角色"} 的冻结贡献；可重启讨论补齐，已有 Owner 决定仍保留。`,
      tone: "blocked", action: "restart", actionLabel: "恢复讨论", visible: true };
  }
  if (attention.reason === "discussion_stalled") {
    return { label: "PRD review blocked", summary: "Review revisions need diagnosis before another discussion",
      tone: "blocked", action: "restart", actionLabel: "Review blocker", visible: true };
  }
  if (["synthesis_superseded", "synthesis_missing"].includes(attention.reason)) {
    return { label: "综合尚未完成", summary: attention.reason === "synthesis_superseded"
      ? "已忽略旧轮次结果，请查看当前轮次的执行情况。" : "当前轮次缺少有效综合结果。",
      tone: "blocked", action: "restart", actionLabel: "查看阻塞", visible: true };
  }
  if (attentionKind === "consolidating") {
    if (attention.question_consolidation_status === "blocked") {
      return {
        label: "Question consolidation blocked",
        summary: "Restart the discussion to rebuild one canonical question ledger",
        tone: "blocked",
        action: "restart",
        actionLabel: "Review blocker",
        visible: true,
      };
    }
    return {
      label: "Consolidating questions",
      summary: "Building one canonical decision ledger before owner input",
      tone: "info",
      action: "activity",
      actionLabel: "",
      visible: true,
    };
  }

  if (attentionKind === "question") {
    const decisions = countLabel(attention.owner_question_count, "decision");
    if (executionState === "running") {
      return {
        label: `${countLabel(attention.active_agent_count, "agent")} responding`,
        summary: `${decisions} pending · blocks ${transition}`,
        tone: "info",
        action: "questions",
        actionLabel: `Review ${attention.owner_question_count}`,
        visible: true,
      };
    }
    return {
      label: "Waiting for you",
      summary: `${decisions} ${attention.owner_question_count === 1 ? "blocks" : "block"} ${transition}`,
      tone: "warning",
      action: "questions",
      actionLabel: `Answer ${attention.owner_question_count}`,
      visible: true,
    };
  }

  if (attentionKind === "review") {
    if (executionState === "running") {
      return {
        label: `${countLabel(attention.active_agent_count, "agent")} responding`,
        summary: `Result review pending · blocks ${transition}`,
        tone: "info",
        action: "result",
        actionLabel: "Review result",
        visible: true,
      };
    }
    return {
      label: "Waiting for review",
      summary: `Owner decision blocks ${transition}`,
      tone: "warning",
      action: "result",
      actionLabel: "Review result",
      visible: true,
    };
  }

  if (executionState === "running") {
    return {
      label: attention.active_agent_count > 0
        ? `${countLabel(attention.active_agent_count, "agent")} responding`
        : "Discussion work is in progress",
      summary: "",
      tone: "info",
      action: "activity",
      actionLabel: "",
      visible: true,
    };
  }
  if (executionState === "ready") {
    return {
      label: "Ready",
      summary: `${countLabel(attention.completed_reply_count, "reply", "replies")} complete`,
      tone: "ready",
      action: "synthesize",
      actionLabel: "Synthesize",
      visible: true,
    };
  }
  if (executionState === "blocked") {
    return {
      label: "Blocked",
      summary: attention.failed_reply_count > 0
        ? `${countLabel(attention.failed_reply_count, "reply failure")} needs attention`
        : "Discussion recovery is required",
      tone: "blocked",
      action: "restart",
      actionLabel: "Review blocker",
      visible: true,
    };
  }
  return {
    label: "Complete",
    summary: "Discussion complete",
    tone: "done",
    action: "result",
    actionLabel: "View result",
    visible: false,
  };
}

export function channelDiscussionControlPolicy(
  attention: ChannelDiscussionAttention | null,
  kernelState: string,
  hasRequirement: boolean,
): ChannelDiscussionControlPolicy {
  const restartsBlockedDiscussion = Boolean(attention?.can_restart);
  const startsNewDiscussion = (
    kernelState === "idle" && !restartsBlockedDiscussion
  );
  return {
    canStartOrRestart: Boolean(
      hasRequirement
      && (startsNewDiscussion || restartsBlockedDiscussion),
    ),
    canDrainReplies: Boolean(attention?.can_drain_replies),
    canSynthesize: Boolean(attention?.can_synthesize),
    restart: restartsBlockedDiscussion,
    startLabel: restartsBlockedDiscussion
      ? "Restart discussion"
      : "Start discussion",
  };
}
