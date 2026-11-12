import type { EventRecord } from "../../api/types.js";
import type { AgentSessionPart, AgentSessionThread } from "./types.js";
import { agentDeltaKind, eventSourceRefs, recordValue, textValue } from "./agentUiEvent.js";

/** Restore public process evidence after ephemeral Kanban deltas expire. */
export function restoreDurableReadableActivity(threads: Iterable<AgentSessionThread>, events: EventRecord[]): void {
  const runs = new Map([...threads].flatMap(thread => thread.turns.flatMap(turn => turn.runs.map(run => [run.id, run] as const))));
  for (const event of events) {
    const payload = recordValue(event.payload) || {};
    if (event.type !== "agent.session.part.delta" || payload.source !== "kanban-agent.headless") continue;
    const run = runs.get(textValue(payload.run_id));
    const id = textValue(payload.part_id);
    if (!run || !id) continue; // Do not create an unanchored or cross-surface turn.
    const kind = agentDeltaKind(payload);
    const refs = eventSourceRefs(event, recordValue(payload.refs));
    const raw = recordValue(refs.raw) || {};
    if (kind === "status" ? !raw.public_commentary : !["tool", "tool_call", "tool_result", "command", "test_result"].includes(kind)) continue;
    const outcome = textValue(payload.state);
    const state = outcome === "completed" || outcome === "failed" || outcome === "cancelled" || outcome === "idle" ? outcome : "streaming";
    const part: AgentSessionPart = { id, runId: run.id, kind, state,
      title: textValue(payload.tool || refs.tool) || "活动", toolName: textValue(payload.tool || refs.tool),
      toolCallId: textValue(payload.tool_call_id || raw.call_id) || undefined,
      content: textValue(payload.content || payload.delta), refs, updatedAt: event.ts,
      sourceEvent: event, sourceEventId: event.id, sourceEventSeq: event.seq };
    const existing = run.parts.find(item => item.id === id);
    if (existing) Object.assign(existing, part);
    else run.parts.push(part);
  }
}
