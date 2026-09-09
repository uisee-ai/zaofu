import type { ChannelDetail } from "../api/types";

const ACTIVE_REPLY_STATES = new Set([
  "active",
  "dispatching",
  "in_progress",
  "pending",
  "processing",
  "queued",
  "running",
  "started",
  "streaming",
  "synthesizing",
]);

function text(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function positive(value: unknown): boolean {
  return Number(value ?? 0) > 0;
}

function hasActiveRecord(rows: Array<Record<string, unknown>> | undefined): boolean {
  return (rows ?? []).some((row) => {
    const state = text(
      row.status
      ?? row.state
      ?? row.execution_state
      ?? row.phase,
    );
    return ACTIVE_REPLY_STATES.has(state);
  });
}

function attentionNeedsConvergence(detail: ChannelDetail): boolean {
  return Object.values(detail.discussion_attention ?? {}).some((attention) => (
    attention.state === "running"
    || attention.execution_state === "running"
    || attention.attention_kind === "consolidating"
    || positive(attention.active_agent_count)
    || positive(attention.active_reply_count)
    || positive(attention.queued_reply_count)
    || positive(attention.running_reply_count)
  ));
}

function discussionNeedsConvergence(
  discussion: Record<string, unknown> | undefined,
): boolean {
  if (!discussion) return false;
  return ACTIVE_REPLY_STATES.has(text(
    discussion.status
    ?? discussion.state
    ?? discussion.execution_state
    ?? discussion.phase,
  ));
}

/**
 * SSE is the normal Channel update transport. During a provider-backed
 * discussion, this bounded predicate enables a short fresh-read fallback so a
 * dropped stream event cannot leave owner questions or synthesis stale until a
 * manual reload. It deliberately excludes settled `needs_input`/`ready`
 * states, so owner review does not become a permanent poll.
 */
export function channelNeedsLiveConvergence(detail: ChannelDetail | null): boolean {
  if (!detail) return false;
  if (positive(detail.pending_reply_count)) return true;
  if ((detail.running_replies?.length ?? 0) > 0) return true;
  if ((detail.queued_replies?.length ?? 0) > 0) return true;
  if ((detail.active_typing?.length ?? 0) > 0) return true;
  if (hasActiveRecord(detail.reply_requests)) return true;
  if (hasActiveRecord(detail.provider_runs)) return true;
  if (attentionNeedsConvergence(detail)) return true;
  if (discussionNeedsConvergence(detail.discussion)) return true;
  return Object.values(detail.discussions ?? {}).some((discussion) => (
    discussionNeedsConvergence(discussion)
  ));
}

export const CHANNEL_LIVE_CONVERGENCE_INTERVAL_MS = 2_500;
