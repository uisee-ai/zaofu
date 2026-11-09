import type { OperatorInboxItem, OperatorInboxProjection } from "../src/api/types.js";
import { inboxItemsForView, inboxStatusLabel } from "../src/components/delivery-trace/inboxViewModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}
const decision: OperatorInboxItem = { id: "decision", title: "Review", kind: "approval", status: "pending", reviewable: true, actionability: "human_required", unread: false };
const report: OperatorInboxItem = { id: "report", title: "Review", kind: "run_delivery", status: "pending", actionability: "informational", unread: true };
const finished: OperatorInboxItem = { ...decision, id: "finished", status: "approved", unread: true };
const inbox = { items: [decision, report, finished] } as OperatorInboxProjection;
assert(inboxItemsForView(inbox, "all").length === 2, "only review requests enter Inbox");
assert(inboxItemsForView(inbox, "decisions")[0] === decision, "read approvals still need action");
assert(inboxItemsForView(inbox, "decisions").length === 1, "informational pending is not an approval");
assert(inboxItemsForView(inbox, "unread").length === 1, "removed notifications never enter unread");
assert(inboxItemsForView(inbox, "resolved")[0] === finished, "finished decisions remain accessible");
assert(inboxStatusLabel(report) === "Update", "pending delivery does not imply waiting for a decision");
assert(inboxStatusLabel(decision) === "Needs action", "read state does not resolve a decision");
decision.unread = false;
assert(inboxItemsForView(inbox, "unread", "decision").includes(decision), "current reading survives refresh");
assert(!inboxItemsForView(inbox, "unread").includes(decision), "leaving selection releases the read item");
assert(inboxItemsForView(null, "all").length === 0, "missing projection is handled");

for (const kind of ["human_decision", "channel_decision", "kanban_question", "owner_attention"]) {
  const legacy = { items: [{ ...decision, kind }] } as OperatorInboxProjection;
  for (const view of ["all", "decisions", "unread", "resolved"] as const) {
    assert(inboxItemsForView(legacy, view).length === 0, `legacy ${kind} excluded from ${view}`);
  }
}
assert(inboxItemsForView({ items: [{ ...decision, reviewable: false }] } as OperatorInboxProjection, "all").length === 0, "unsupported approvals cannot masquerade as reviews");
