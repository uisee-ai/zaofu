import {
  selfIssueSlashAction,
  selfIssueEvaluationLocked,
  selfIssuePreviewIsReusable,
  selfIssueUnderstandingStatus,
} from "../src/components/orchestrator/selfIssue.js";

const action = selfIssueSlashAction(
  "/issue The board freezes after I approve a Fix proposal\n\nSteps:\n1. Open Triage\n2. Approve Fix",
);
if (!action || action.action !== "self-issue-capture") {
  throw new Error("/issue should resolve to self-issue-capture");
}
if (action.payload.direct_report !== true) {
  throw new Error("non-empty /issue must bypass the legacy questionnaire");
}
if (action.payload.portable_session !== true) {
  throw new Error("/issue must start the portable continuous Issue Session");
}
if (action.payload.description !== "The board freezes after I approve a Fix proposal\n\nSteps:\n1. Open Triage\n2. Approve Fix") {
  throw new Error("the complete free-form report must be preserved");
}
if (selfIssueUnderstandingStatus({
  analysis: { understanding: { status: "fallback" } },
}) !== "fallback") {
  throw new Error("Limited Draft understanding status must survive projection restore");
}
if (!selfIssueEvaluationLocked({ enrichment: { evaluation_locked: true } })) {
  throw new Error("Evaluation Lock must survive canonical projection restore");
}
if (!selfIssuePreviewIsReusable({
  draft: {
    revision: 1,
    publication_batch: {
      status: "previewed",
      publication_mode: "github",
      draft_revision: 1,
      previews: { github: { body: "## Describe the bug" } },
    },
  },
}, "github")) {
  throw new Error("A restored Session must reuse a preview nested under draft.publication_batch");
}
console.log("selfIssueFlow OK");
