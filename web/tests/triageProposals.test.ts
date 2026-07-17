import {
  mergeAutopilotDescriptors,
  pendingProposalDescriptor,
  type AutopilotProposalDescriptor,
} from "../src/app/triageProposals.js";
import type { PendingKanbanProposal } from "../src/api/client.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function pending(overrides: Partial<PendingKanbanProposal>): PendingKanbanProposal {
  return {
    proposal_event_id: "evt-1",
    ts: "2026-07-09T00:00:00Z",
    action: "create-task",
    requested_action: "create-task",
    reason: "",
    valid: true,
    validation_error: "",
    title: "Backend CRUD",
    payload: { title: "Backend CRUD", contract: { behavior: "x" } },
    turn_id: "t1",
    conversation_id: "c1",
    thread_key: "k1",
    ...overrides,
  };
}

// The core regression: a durable pending proposal must yield an Accept-able
// descriptor even when NO live event carries it (aged past the event window).
function testDurableProposalSurvivesEmptyLive(): void {
  const merged = mergeAutopilotDescriptors([pending({ proposal_event_id: "evt-A" })], []);
  assert(merged.length === 1, "durable pending proposal must render with no live events");
  assert(merged[0].proposalId === "evt-A", "descriptor keeps the durable proposal id for Accept");
  assert(merged[0].valid === true && merged[0].action === "create-task", "Accept target preserved");
  assert(merged[0].title === "Backend CRUD", "title carried for the Accept card");
}

// Dedup: a proposal present in both the durable projection and the live event
// slice must appear once (durable wins), never a double Accept card.
function testDurableAndLiveDedup(): void {
  const live: AutopilotProposalDescriptor[] = [{
    proposalId: "evt-A",
    action: "create-task",
    valid: true,
    actionPayload: { title: "Backend CRUD" },
    title: "Backend CRUD",
    metaKind: "proposal",
    metaSeverity: "medium",
    taskId: "",
  }];
  const merged = mergeAutopilotDescriptors([pending({ proposal_event_id: "evt-A" })], live);
  assert(merged.length === 1, "same proposal in durable + live must not double");
  assert(merged[0].proposalId === "evt-A", "dedup keeps the shared proposal id");
}

// Live-only proposals (e.g. autopilot.proposal.created with no durable kanban
// entry) still contribute so nothing regresses for the Feishu-surface path.
function testLiveOnlyProposalContributes(): void {
  const live: AutopilotProposalDescriptor[] = [{
    proposalId: "evt-live",
    action: "create-task",
    valid: true,
    actionPayload: { title: "Live only" },
    title: "Live only",
    metaKind: "proposal",
    metaSeverity: "medium",
    taskId: "",
  }];
  const merged = mergeAutopilotDescriptors([pending({ proposal_event_id: "evt-A" })], live);
  assert(merged.length === 2, "durable + a distinct live proposal both render");
  const ids = merged.map((item) => item.proposalId).sort();
  assert(ids[0] === "evt-A" && ids[1] === "evt-live", "both proposals present, durable first");
}

function testDescriptorReadsTaskId(): void {
  const d = pendingProposalDescriptor(pending({ payload: { title: "T", task_id: "TASK-9" } }));
  assert(d.taskId === "TASK-9", "task_id surfaces for the Edit action");
}

testDurableProposalSurvivesEmptyLive();
testDurableAndLiveDedup();
testLiveOnlyProposalContributes();
testDescriptorReadsTaskId();

// eslint-disable-next-line no-console
console.log("triageProposals.test.ts OK");

// frontend-stress 2026-07-15: notice wording — a task_id in the result does not
// mean "created"; only create actions create, everything else "executed".
import { proposalRunNotice } from "../src/app/triageProposals.js";
{
  const a = proposalRunNotice("create-task", "建任务甲", "TASK-1");
  if (!a.includes("created from")) throw new Error("create-task should say created: " + a);
  const b = proposalRunNotice("update-task", "改优先级", "TASK-1");
  if (b.includes("created")) throw new Error("update-task must not say created: " + b);
  if (!b.includes("executed") || !b.includes("TASK-1")) throw new Error("update-task should say executed (TASK-1): " + b);
  const c = proposalRunNotice("request-fanout", "扇出", "");
  if (c.includes("created") || !c.includes("executed")) throw new Error("no-taskid action should say executed: " + c);
  console.log("proposalRunNotice OK");
}
