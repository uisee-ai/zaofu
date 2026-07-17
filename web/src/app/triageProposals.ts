// Triage "Autopilot" queue proposal source.
//
// Bug (channel-kanban E2E 2026-07-09): the Triage proposal-only queue built its
// Accept cards only from the bounded live recent-events slice
// (events.filter(kanban.agent.action.proposed).slice(0, 12)). Kanban-agent
// proposals are ledger truth that must survive the event window and the
// originating browser session (see OrchestratorPanel + the
// /kanban-agent/pending-proposals projection). Once a still-pending proposal
// aged past the recent-events window, its Accept entry point silently vanished
// from Triage even though pending-proposals still listed it. This module makes
// the durable projection the source of truth and dedups it against live events.

import type { PendingKanbanProposal } from "../api/client";

export interface AutopilotProposalDescriptor {
  proposalId: string;
  action: string;
  valid: boolean;
  actionPayload: Record<string, unknown> | null;
  title: string;
  metaKind: string;
  metaSeverity: string;
  taskId: string;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  return String(value);
}

export function pendingProposalDescriptor(
  proposal: PendingKanbanProposal,
): AutopilotProposalDescriptor {
  const actionPayload =
    proposal.payload && typeof proposal.payload === "object"
      ? (proposal.payload as Record<string, unknown>)
      : {};
  return {
    proposalId: proposal.proposal_event_id,
    action: proposal.action,
    valid: Boolean(proposal.valid),
    actionPayload,
    title: proposal.title || proposal.action,
    metaKind: proposal.requested_action || "proposal",
    metaSeverity: "medium",
    taskId: asText(actionPayload.task_id),
  };
}

// Durable pending proposals take precedence; live descriptors only contribute
// proposals not already covered (freshly-arrived kanban proposals or
// non-kanban autopilot.proposal.created events). Dedup is by proposalId, which
// is the proposed event id shared by both surfaces.
export function mergeAutopilotDescriptors(
  pendingProposals: PendingKanbanProposal[],
  liveDescriptors: AutopilotProposalDescriptor[],
): AutopilotProposalDescriptor[] {
  const durable = pendingProposals.map(pendingProposalDescriptor);
  const seen = new Set(durable.map((item) => item.proposalId));
  const live = liveDescriptors.filter((item) => !seen.has(item.proposalId));
  return [...durable, ...live];
}

// A task_id in the result does not mean a task was CREATED — update-task also
// returns the task_id it mutated. Only the create actions "create"; every other
// executed proposal "executed" (frontend-stress 2026-07-15).
export function proposalRunNotice(action: string, label: string, taskId: string): string {
  const created = action === "create-task" || action === "idea-to-product";
  if (created && taskId) return `✓ ${taskId} created from “${label}”`;
  if (taskId) return `✓ executed “${label}” (${taskId})`;
  return `✓ executed “${label}”`;
}
