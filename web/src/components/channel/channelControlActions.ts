export function discussionModePayload(
  channelId: string,
  threadId: string,
  mode: string,
  defaultResponderId: string,
): Record<string, unknown> {
  return {
    channel_id: channelId,
    thread_id: threadId,
    mode,
    default_responder_id: defaultResponderId,
    source: "web-channel-discussion",
  };
}

export function synthesisRequestPayload(
  channelId: string,
  threadId: string,
  targetMemberId?: string,
): Record<string, unknown> {
  return {
    channel_id: channelId,
    thread_id: threadId,
    target_member_id: targetMemberId || undefined,
    reason: "operator requested channel synthesis",
    source: "web-channel-synthesis",
  };
}

export function ownerReportPayload(
  channelId: string,
  threadId: string,
): Record<string, unknown> {
  return {
    channel_id: channelId,
    thread_id: threadId,
    owner_id: "owner:operator",
    member_id: "operator",
    period: "current",
    reason: "generated from channel detail",
    source: "web-channel-owner-report",
  };
}

export function clearHistoryPayload(
  channelId: string,
  threadId: string,
): Record<string, unknown> {
  return {
    channel_id: channelId,
    thread_id: threadId,
    reason: "cleared from channel settings",
    source: "web-channel-settings",
  };
}

export function startDiscussionPayload(
  channelId: string,
  threadId: string,
  message: string,
  requirementMessageId: string,
  mode: string,
  restart = false,
): Record<string, unknown> {
  return {
    channel_id: channelId,
    thread_id: threadId,
    message,
    requirement_message_id: requirementMessageId || undefined,
    mode,
    restart,
    source: "web-channel-discussion",
  };
}
