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

const STATE_PRIORITY: Record<ChannelDiscussionAttention["state"], number> = {
  needs_input: 0,
  blocked: 1,
  running: 2,
  ready: 3,
  done: 4,
};

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function selectChannelDiscussionAttention(
  detail: ChannelDetail | null | undefined,
  preferredThreadId: string,
): ChannelDiscussionAttention | null {
  const rows = Object.values(detail?.discussion_attention ?? {});
  if (!rows.length) return null;
  const preferred = detail?.discussion_attention?.[preferredThreadId];
  if (preferred && preferred.state !== "done") return preferred;
  const selected = [...rows].sort((left, right) => (
    STATE_PRIORITY[left.state] - STATE_PRIORITY[right.state]
  ))[0] ?? null;
  return selected?.state !== "done" ? selected : preferred ?? selected;
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
