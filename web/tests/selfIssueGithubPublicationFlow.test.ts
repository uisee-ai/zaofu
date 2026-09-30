import * as selfIssue from "../src/components/orchestrator/selfIssue.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const retryTarget = (selfIssue as unknown as Record<string, unknown>)[
  "selfIssueGithubEvidenceRetryTarget"
];
assert(
  typeof retryTarget === "function",
  "recoverable GitHub evidence must expose its original publication intent",
);

const failed = (retryTarget as (
  card: Record<string, unknown>,
) => { provider: string; intentId: string } | null)({
  intent_ids: { github: "pub-github-1" },
  publication_batch: {
    evidence_comments: {
      github: { status: "publish_failed" },
    },
  },
});
assert(failed?.provider === "github", "GitHub must be selected for media recovery");
assert(failed?.intentId === "pub-github-1", "recovery must reuse the original intent");

const complete = (retryTarget as (
  card: Record<string, unknown>,
) => { provider: string; intentId: string } | null)({
  intent_ids: { github: "pub-github-1" },
  publication_batch: {
    evidence_comments: {
      github: { status: "published" },
    },
  },
});
assert(complete === null, "published evidence must not offer a duplicate retry");
