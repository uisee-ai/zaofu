import type { WebChatTurn } from "../../api/types.js";
import type { AgentSessionRun, AgentSessionStatus, AgentSessionThread } from "./types.js";

export function durableRunStatus(run: AgentSessionRun, turn?: WebChatTurn): AgentSessionStatus | undefined {
  if (!turn || !["waiting_input", "completed", "failed", "cancelled", "interrupted"].includes(turn.state)) return undefined;
  if (Date.parse(run.updatedAt || "") > Date.parse(turn.updated_at)) return undefined;
  if (turn.state === "failed") return "failed";
  if (turn.state === "cancelled" || turn.state === "interrupted") return "cancelled";
  return "completed";
}

// History pages may retain a start without its terminal event. Runtime facts
// settle those display fragments without inventing replies or answering cards.
export function reconcileDurableRuns(threads: Iterable<AgentSessionThread>, runtime: WebChatTurn[]): void {
  const byId = new Map(runtime.map(turn => [turn.turn_id, turn]));
  for (const thread of threads) for (const turn of thread.turns) for (const run of turn.runs) {
    const durable = byId.get(run.id);
    if (durable?.thread_id !== thread.id) continue;
    const status = durableRunStatus(run, durable);
    if (!status) continue;
    run.status = status;
    for (const part of run.parts) {
      if (["streaming", "submitted"].includes(part.state)) part.state = status;
    }
    turn.cards = turn.cards.filter(card => card.runId !== run.id || !card.payload?.preparing);
    if (status === "cancelled") {
      for (const card of turn.cards) {
        if (card.runId !== run.id || !["waiting_input", "submitted"].includes(card.status || "")) continue;
        card.status = "cancelled";
        card.payload = { ...card.payload, resolution: "cancelled" };
      }
    }
  }
}
