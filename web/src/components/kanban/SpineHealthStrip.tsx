// 131-P0-5: read-only run health / explain strip for the Kanban board.
// Renders shadow-spine projections (runs/health/tasks); zero mutation routes.
import { getWorkflowSpine } from "../../api/client";
import { useEffect, useMemo, useState } from "react";

interface RunEntry {
  milestones: number;
  last_milestone: string;
  last_ts: string;
  attention: boolean;
}

interface TaskEntry {
  attempt_count: number;
  current_owner: string;
  last_terminal: string;
}

interface SpineDoc {
  runs: Record<string, RunEntry>;
  health: { counters: Record<string, number>; last_event_ts: string };
  tasks: Record<string, TaskEntry>;
}

const HEALTH_KEYS = [
  "human.escalate",
  "review.rejected",
  "verify.failed",
  "integration.failed",
] as const;

function asSpineDoc(raw: Record<string, unknown> | null): SpineDoc | null {
  if (!raw) return null;
  const runs = raw.runs;
  const health = raw.health;
  if (!runs || typeof runs !== "object" || "error" in (runs as object)) return null;
  return {
    runs: runs as Record<string, RunEntry>,
    health: (health && typeof health === "object" && !("error" in (health as object))
      ? health
      : { counters: {}, last_event_ts: "" }) as SpineDoc["health"],
    tasks: (raw.tasks && typeof raw.tasks === "object" && !("error" in (raw.tasks as object))
      ? raw.tasks
      : {}) as Record<string, TaskEntry>,
  };
}

export function SpineHealthStrip({ projectId }: { projectId?: string }) {
  const [doc, setDoc] = useState<SpineDoc | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void getWorkflowSpine(projectId)
        .then((raw) => {
          if (!cancelled) setDoc(asSpineDoc(raw));
        })
        .catch(() => {
          if (!cancelled) setDoc(null);
        });
    };
    load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId]);

  const runs = useMemo(() => Object.entries(doc?.runs ?? {}), [doc]);
  const counters = doc?.health.counters ?? {};
  const taskRows = useMemo(
    () => Object.entries(doc?.tasks ?? {}).sort((a, b) => b[1].attempt_count - a[1].attempt_count),
    [doc],
  );

  if (!doc || (runs.length === 0 && taskRows.length === 0)) return null;

  return (
    <div className="spine-health-strip" style={{
      display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center",
      padding: "0.35rem 0.6rem", fontSize: "0.8rem",
      border: "1px solid var(--border, #333)", borderRadius: 6, marginBottom: "0.5rem",
    }}>
      {runs.map(([pddId, run]) => (
        <span key={pddId} title={`last: ${run.last_milestone} @ ${run.last_ts}`}>
          <strong>{pddId}</strong>
          {" "}{run.milestones} 里程碑 · {run.last_milestone}
          {run.attention ? <span style={{ color: "#e5534b" }}> ⚠ attention</span> : null}
        </span>
      ))}
      {HEALTH_KEYS.filter((key) => (counters[key] ?? 0) > 0).map((key) => (
        <span key={key} className="muted" title={key}>
          {key.split(".").pop()}:{counters[key]}
        </span>
      ))}
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        style={{ marginLeft: "auto", fontSize: "0.75rem" }}
      >
        {expanded ? "收起 attempts" : `attempts (${taskRows.length})`}
      </button>
      {expanded ? (
        <table style={{ width: "100%", fontSize: "0.75rem" }}>
          <thead>
            <tr><th align="left">task</th><th align="left">attempts</th><th align="left">owner</th><th align="left">last terminal</th></tr>
          </thead>
          <tbody>
            {taskRows.map(([taskId, entry]) => (
              <tr key={taskId}>
                <td>{taskId}</td>
                <td>{entry.attempt_count}</td>
                <td>{entry.current_owner}</td>
                <td>{entry.last_terminal}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
