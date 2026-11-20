import type { OperatorInboxItem, OperatorInboxProjection } from "../../api/types";

export type InboxView = "all" | "decisions" | "unread" | "resolved";

export const INBOX_VIEWS: Array<{ value: InboxView; label: string }> = [
  { value: "all", label: "All" },
  { value: "decisions", label: "Needs action" },
  { value: "unread", label: "Unread" },
  { value: "resolved", label: "Resolved" },
];

export function inboxItemsForView(
  inbox: OperatorInboxProjection | null, view: InboxView, retainedReadId = "",
): OperatorInboxItem[] {
  return (inbox?.items ?? []).filter((item) => {
    if (!isInboxReview(item)) return false;
    if (view === "all") return true;
    if (view === "unread") return item.unread === true || item.id === retainedReadId;
    if (view === "resolved") return item.status !== "pending" || item.category === "resolved";
    if (view === "decisions") return (item.status === "pending" && item.actionability === "human_required") || item.status === "processing";
    return false;
  });
}

export function inboxStatusLabel(item: OperatorInboxItem): string {
  if (item.verification_required && item.status === "pending") return "Needs review";
  if (item.status === "processing") return "Decision submitted";
  if (item.status === "failed") return "Action failed";
  if (item.status !== "pending") return item.status;
  if (item.actionability === "human_required") return "Needs action";
  if (item.actionability === "informational") return "Update";
  return "Monitoring";
}

export function isInboxReview(item: OperatorInboxItem): boolean {
  if (item.kind === "plan_approval") return true;
  return item.kind === "approval" && (item.reviewable === true
    || item.owner_route === "workflow_proposal"
    || (item.owner_route === "plan_synth_owner_decision" && Boolean(item.proposal_ref && item.eval_ref)));
}
