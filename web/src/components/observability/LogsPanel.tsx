// Diagnostics Logs tab (doc 82 §8.2/§9). Read-only rows from
// /api/projects/{id}/diagnostics/logs; human-readable summary first,
// raw event stays behind the event ref.
import { useEffect, useState } from "react";

import { getDiagnosticsLogs } from "../../api/client";
import type { DiagnosticsLogRow } from "../../api/types";

const LEVELS = ["INFO", "WARN", "ERROR"] as const;

interface LogsPanelProps {
  projectId?: string;
}

export function LogsPanel({ projectId }: LogsPanelProps) {
  const [rows, setRows] = useState<DiagnosticsLogRow[]>([]);
  const [level, setLevel] = useState<string>("INFO");
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDiagnosticsLogs(projectId, { limit: 200, level })
      .then((page) => {
        if (cancelled) return;
        setRows(page.rows);
        setError("");
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, level]);

  return (
    <section className="subsection" data-testid="diagnostics-logs">
      <div className="inline-heading">
        <h3>Logs</h3>
        <span className="muted">event summaries · newest first</span>
        <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Minimum level">
          {LEVELS.map((lv) => (
            <option key={lv} value={lv}>{lv}+</option>
          ))}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && rows.length === 0 && <p className="muted">Loading…</p>}
      {!loading && rows.length === 0 && !error && (
        <p className="muted">No log rows at this level yet.</p>
      )}
      {rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Level</th>
              <th>Source</th>
              <th>Task</th>
              <th>Role</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.raw_event_ref}>
                <td>{row.timestamp.replace("T", " ").slice(0, 19)}</td>
                <td>
                  <span className={`badge badge-${row.level === "ERROR" ? "err" : row.level === "WARN" ? "warn" : "info"}`}>
                    {row.level}
                  </span>
                </td>
                <td>{row.source}</td>
                <td>{row.task_id || "-"}</td>
                <td>{row.role || "-"}</td>
                <td>{row.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
