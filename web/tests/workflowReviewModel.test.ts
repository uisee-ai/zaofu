import { reviewBinding, reviewText, reviewablePreview, discussablePreview, previewUnavailableReason } from "../src/components/workflow/workflowReviewModel.js";
import type { WorkflowRequestDetail } from "../src/api/types.js";
const detail = { result: { request_id: "R", revision: 2, proposal_digest: "hash" } } as unknown as WorkflowRequestDetail;
if (JSON.stringify(reviewBinding(detail)) !== JSON.stringify({ request_id: "R", revision: 2, proposal_digest: "hash" })) throw new Error("Review must bind exact revision and digest");
if (reviewText("quoted text") !== "quoted text" || reviewText(2) !== "2" || reviewText(undefined) !== "") throw new Error("Quote text must preserve source content");

const blocked = { result: { request_id: "R", revision: 3, status: "ready" }, proposal: { approval_status: "blocked", proposal_digest: "candidate" } } as unknown as WorkflowRequestDetail;
if (!reviewablePreview(blocked) || reviewBinding(blocked).proposal_digest !== "candidate") throw new Error("Blocked candidate must bind its own digest for repair");
if (reviewablePreview({ ...blocked, lifecycle: { submitted: true } } as WorkflowRequestDetail)) throw new Error("Submitted preview cannot be edited");
if (reviewablePreview({ ...blocked, proposal: {} })) throw new Error("Unprepared request has no editable candidate");

if (previewUnavailableReason(blocked, true)) throw new Error("Editable candidate must not show a lock reason");
if (!previewUnavailableReason(blocked, false).includes("权限")) throw new Error("Missing write access must be explained");
const running = { ...blocked, lifecycle: { submitted: true }, artifacts: { effective_config: { yaml: "frozen: true" } } } as unknown as WorkflowRequestDetail;
if (!discussablePreview(running) || reviewablePreview(running)) throw new Error("Frozen snapshots allow discussion, never editing");
if (!previewUnavailableReason(running, true).includes("提问讨论")) throw new Error("Running snapshot must explain discussion is available");
if (!previewUnavailableReason(running, false).includes("权限")) throw new Error("Missing token must take priority over the snapshot notice");
if (discussablePreview({ ...running, artifacts: {} } as WorkflowRequestDetail)) throw new Error("Missing snapshot cannot be discussed");
if (!previewUnavailableReason({ ...blocked, proposal: {} }, true).includes("预览")) throw new Error("Missing preview must explain the next step");
