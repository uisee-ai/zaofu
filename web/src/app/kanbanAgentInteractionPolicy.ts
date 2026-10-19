import type { ActionResponse } from "../api/types";

export function shouldRefreshChannelsAfterAction(
  action: string,
  result: Pick<ActionResponse, "ok" | "applied_action" | "channel_id">,
): boolean {
  if (action.startsWith("channel")) return true;
  if (action !== "kanban-plan-apply" || !result.ok) return false;
  return String(result.applied_action ?? "").startsWith("channel")
    || Boolean(String(result.channel_id ?? "").trim());
}

export function planDiscussionBackend(
  planBackend: unknown,
  selectedBackend: string,
): string {
  return String(planBackend ?? "").trim() || selectedBackend;
}

export function profileRegistrationContinuation(
  action: string, payload: Record<string, unknown>,
): { request_event_id: string; request_id: string; revision: number } | null {
  if (action !== "channel-profile-register") return null;
  const raw = payload.plan_continuation;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const plan = raw as Record<string, unknown>;
  const eventId = String(plan.request_event_id || "");
  const requestId = String(plan.request_id || "");
  const revision = Number(plan.revision);
  return eventId && requestId && Number.isSafeInteger(revision) && revision > 0
    ? { request_event_id: eventId, request_id: requestId, revision }
    : null;
}
