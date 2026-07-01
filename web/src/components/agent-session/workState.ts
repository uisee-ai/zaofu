// Pure derivation of "is the agent working" + composer button status,
// shared by the channel composer (ChannelPage) and the orchestrator
// headless composer (OrchestratorPanel) so both morph identically. No
// React/DOM here — unit tested in tests/agentWorkState.test.ts.
//
// Working is
// derived 1:1 from the agent run status (server truth), with display-only
// suppression gates layered on top. No optimistic local bridging.

import type { AgentSessionStatus } from "./types";

/** The single button state the composer renders. */
export type ComposerStatus = "idle" | "submitted" | "streaming" | "error";

/**
 * True while the run owns the turn — drives the composer's Interrupt
 * affordance. `submitted` (sent, awaiting first token) counts as working.
 */
export function isWorking(status: AgentSessionStatus | undefined): boolean {
  return status === "streaming" || status === "submitted";
}

export interface ShowsWorkingGates {
  /** A pending approval/question owns the slot → not "working", waiting on the user. */
  hasPendingApproval?: boolean;
  /** Runner liveness: true online, false known-offline, undefined pre-poll. Only known-offline suppresses. */
  runnerOnline?: boolean;
}

/**
 * Display-only gate for the "Working…" shimmer / tab title. Suppressed
 * when the runner is known offline or an approval is parked on the user.
 */
export function showsWorking(
  status: AgentSessionStatus | undefined,
  gates: ShowsWorkingGates = {},
): boolean {
  if (gates.runnerOnline === false) return false;
  if (gates.hasPendingApproval) return false;
  return isWorking(status);
}

/**
 * Derive the composer submit button status from the active run status and
 * the local posting flag (POST in flight before the run status flips).
 * `posting` shows the spinner; a live run shows the interrupt square.
 */
export function deriveComposerStatus(
  runStatus: AgentSessionStatus | undefined,
  posting: boolean,
): ComposerStatus {
  if (runStatus === "streaming") return "streaming";
  if (posting || runStatus === "submitted") return "submitted";
  if (runStatus === "failed") return "error";
  return "idle";
}
