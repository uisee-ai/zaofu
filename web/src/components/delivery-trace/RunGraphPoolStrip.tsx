// RunGraphPoolStrip (T-刀①.5) — lane/pool occupancy strip under the Run Graph
// legend. Lane set is derived from execution_graph node owner names matching
// `-lane-\d` (assigned_to / affinity.actual_owner / planned.owner_instance);
// projects without lane roles render nothing. Right end carries the run-level
// ☠ stuck count from overview-pulse when available (omitted otherwise).
import { useMemo } from "react";

import type { DeliveryTraceNode, OverviewPulse } from "../../api/types";

const LANE_RE = /-lane-\d+/;
// busy = agent actively holds work; queued = work staged for the lane.
const BUSY_STATUSES = new Set(["in_progress", "dispatched", "review", "test", "judge"]);
const QUEUED_STATUSES = new Set(["ready", "queued", "rework"]);

interface LaneCell {
  lane: string;
  state: "busy" | "queued" | "idle";
  taskId: string;
}

function laneOwners(node: DeliveryTraceNode): string[] {
  return [
    node.actual.assigned_to,
    node.actual.affinity?.actual_owner ?? "",
    node.planned.owner_instance ?? "",
  ].filter((owner) => owner && LANE_RE.test(owner));
}

function deriveLanes(nodes: DeliveryTraceNode[]): LaneCell[] {
  const lanes = new Map<string, { busy: string; queued: boolean }>();
  for (const node of nodes) {
    for (const lane of laneOwners(node)) {
      if (!lanes.has(lane)) lanes.set(lane, { busy: "", queued: false });
    }
  }
  for (const node of nodes) {
    if (node.superseded) continue;
    const status = node.actual.status;
    for (const lane of laneOwners(node)) {
      const cell = lanes.get(lane)!;
      if (BUSY_STATUSES.has(status) && !cell.busy) cell.busy = node.task_id;
      if (QUEUED_STATUSES.has(status)) cell.queued = true;
    }
  }
  return Array.from(lanes.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([lane, cell]) => ({
      lane,
      state: cell.busy ? "busy" : cell.queued ? "queued" : "idle",
      taskId: cell.busy,
    }));
}

export function RunGraphPoolStrip({
  nodes,
  pulse,
}: {
  nodes: DeliveryTraceNode[];
  pulse?: OverviewPulse | null;
}) {
  const lanes = useMemo(() => deriveLanes(nodes), [nodes]);
  if (!lanes.length) return null;
  const stuck = pulse?.attention?.sm_stuck_observed;
  const respawnStreak = pulse?.run_pulse?.respawn_failed_streak;
  return (
    <div className="rg-pool-strip" data-testid="rg-pool-strip" aria-label="Lane pool occupancy">
      {lanes.map((cell) => (
        <span
          key={cell.lane}
          className={`rg-lane rg-lane-${cell.state}`}
          data-testid={`rg-lane-${cell.lane}`}
          title={`${cell.lane} · ${cell.state}${cell.taskId ? ` · ${cell.taskId}` : ""}`}
        >
          <span className="rg-lane-name">{cell.lane}</span>
          <span className="rg-lane-state">{cell.state === "busy" ? cell.taskId : cell.state}</span>
        </span>
      ))}
      {typeof stuck === "number" && (
        <span
          className={`rg-pool-stuck${stuck > 0 ? " is-bad" : ""}`}
          data-testid="rg-pool-stuck"
          title={`overview-pulse: stuck ${stuck}${typeof respawnStreak === "number" ? ` · respawn-failed streak ${respawnStreak}` : ""}`}
        >
          ☠ stuck {stuck}
        </span>
      )}
    </div>
  );
}
