import type { OperatorInboxItem, OperatorInboxProjection } from "../../api/types";

export type InboxView = "all" | "decisions" | "unread" | "resolved" | "runtime_attention" | "automation";

export const INBOX_VIEWS: Array<{ value: InboxView; label: string }> = [
  { value: "all", label: "All" },
  { value: "decisions", label: "Needs action" },
  { value: "unread", label: "Unread" },
  { value: "resolved", label: "Resolved" },
  { value: "runtime_attention", label: "Runtime" },
  { value: "automation", label: "Automation" },
];

export function inboxItemsForView(
  inbox: OperatorInboxProjection | null, view: InboxView, retainedReadId = "",
): OperatorInboxItem[] {
  return (inbox?.items ?? []).filter((item) => {
    if (view === "all") return true;
    if (view === "unread") return item.unread === true || item.id === retainedReadId;
    if (view === "resolved") return item.status !== "pending" || item.category === "resolved";
    if (view === "decisions") return item.status === "pending" && item.actionability === "human_required";
    if (view === "runtime_attention") return item.status === "pending" && item.category === "runtime_attention";
    return item.status === "pending" && item.category === "automation_diagnostic";
  });
}

export function inboxStatusLabel(item: OperatorInboxItem): string {
  if (item.status !== "pending") return item.status;
  if (item.actionability === "human_required") return "Needs action";
  if (item.actionability === "informational") return "Update";
  return "Monitoring";
}
