import { reviewBinding, reviewText } from "../src/components/workflow/workflowReviewModel.js";
import type { WorkflowRequestDetail } from "../src/api/types.js";
const detail = { result: { request_id: "R", revision: 2, proposal_digest: "hash" } } as unknown as WorkflowRequestDetail;
if (JSON.stringify(reviewBinding(detail)) !== JSON.stringify({ request_id: "R", revision: 2, proposal_digest: "hash" })) throw new Error("Review must bind exact revision and digest");
if (reviewText("quoted text") !== "quoted text" || reviewText(2) !== "2" || reviewText(undefined) !== "") throw new Error("Quote text must preserve source content");
