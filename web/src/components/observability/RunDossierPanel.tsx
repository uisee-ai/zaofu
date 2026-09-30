import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { FileText } from "lucide-react";
import { useEffect, useState } from "react";

import { getRunDossier } from "../../api/client";
import type { GoalDossier } from "../../api/types";
import { PreBlock, ProjectionEmptyState, asRecord, asStringArray, textValue } from "../../app/shared";

type DossierSection = "goal" | "tasks" | "claims" | "evidence" | "gaps" | "closure";

export function RunDossierPanel({
  projectId,
  runId,
}: {
  projectId: string;
  runId: string;
}) {
  useSettingsPreferences();
  const [section, setSection] = useState<DossierSection>("goal");
  const [dossier, setDossier] = useState<GoalDossier | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!runId) {
      setDossier(null);
      setError("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    void getRunDossier(runId, projectId || undefined)
      .then((value) => {
        if (!cancelled) setDossier(value);
      })
      .catch((reason) => {
        if (!cancelled) {
          setDossier(null);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, runId]);

  const sections: Array<{ id: DossierSection; label: string }> = [
    { id: "goal", label: "Goal" },
    { id: "tasks", label: "Tasks" },
    { id: "claims", label: "Claims" },
    { id: "evidence", label: "Evidence" },
    { id: "gaps", label: "Gaps" },
    { id: "closure", label: "Closure" },
  ];
  if (loading) {
    return <section className="subsection run-dossier-panel"><p className="muted">{uiText("Loading Goal Dossier...")}</p></section>;
  }
  if (error || !dossier) {
    return (
      <section className="subsection run-dossier-panel">
        <ProjectionEmptyState
          state={{
            title: "Goal Dossier unavailable",
            description: error || "The run has no readable Dossier projection.",
            icon: FileText,
            compact: true,
          }}
        />
      </section>
    );
  }
  const goal = asRecord(dossier.goal);
  const terminal = asRecord(dossier.terminal);
  const counts = asRecord(dossier.state.task_counts);
  const matrix = dossier.claim_to_evidence ?? {};
  const claimSummary = asRecord(matrix.summary);
  const tasks = dossier.state.tasks ?? [];
  const claims = matrix.rows ?? [];
  return (
    <section className="subsection run-dossier-panel" data-testid="run-goal-dossier">
      <div className="inline-heading">
        <div>
          <h3>{uiText("Goal Dossier")}</h3>
          <span className="muted mono">{dossier.run_id}</span>
        </div>
        <span className={`badge badge-${textValue(terminal.status || goal.status) === "completed" ? "ok" : "warn"}`}>
          {textValue(terminal.status || goal.status) || "unknown"}
        </span>
      </div>
      <div className="tab-row compact-tabs run-dossier-tabs" aria-label="Goal Dossier sections">
        {sections.map((item) => (
          <button
            className={`tab-button ${section === item.id ? "active" : ""}`}
            key={item.id}
            type="button"
            onClick={() => setSection(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {section === "goal" ? (
        <div className="run-dossier-summary">
          <p className="run-dossier-objective">{textValue(goal.objective) || "Objective not recorded"}</p>
          <dl>
            <div><dt>{uiText("Tasks")}</dt><dd>{textValue(counts.terminal) || "0"}/{textValue(counts.total) || "0"}</dd></div>
            <div><dt>{uiText("Claims")}</dt><dd>{textValue(claimSummary.closed_claims) || "0"}/{textValue(claimSummary.mandatory_claims) || "0"}</dd></div>
            <div><dt>{uiText("Gaps")}</dt><dd>{dossier.gaps.length}</dd></div>
            <div><dt>{uiText("Evidence")}</dt><dd>{dossier.evidence_index.length}</dd></div>
            <div><dt>{uiText("Freshness")}</dt><dd>{dossier.freshness.status || "unknown"}</dd></div>
            <div><dt>{uiText("Fingerprint")}</dt><dd className="mono">{dossier.source_fingerprint.slice(0, 20)}</dd></div>
          </dl>
        </div>
      ) : null}
      {section === "tasks" ? (
        <DossierTable
          columns={["Task", "Status", "Owner", "Source"]}
          rows={tasks.map((task) => {
            const row = asRecord(task);
            return [
              textValue(row.id),
              textValue(row.status),
              textValue(row.assigned_to) || "-",
              textValue(row.status_source) || "-",
            ];
          })}
        />
      ) : null}
      {section === "claims" ? (
        <DossierTable
          columns={["Claim", "Tasks", "Verification", "Verdict"]}
          rows={claims.map((claim) => {
            const row = asRecord(claim);
            return [
              textValue(row.goal_claim_id) || textValue(row.claim),
              asStringArray(row.task_ids).join(", ") || "-",
              textValue(row.task_verification) || "unverified",
              textValue(row.verdict) || "unknown",
            ];
          })}
        />
      ) : null}
      {section === "evidence" ? (
        <DossierTable
          columns={["Ref", "Event", "Task"]}
          rows={dossier.evidence_index.map((evidence) => {
            const row = asRecord(evidence);
            return [
              textValue(row.ref),
              textValue(row.event_type),
              textValue(row.task_id) || "-",
            ];
          })}
        />
      ) : null}
      {section === "gaps" ? (
        <DossierTable
          columns={["Type", "Task", "Summary"]}
          rows={dossier.gaps.map((gap) => {
            const row = asRecord(gap);
            return [
              textValue(row.type),
              textValue(row.task_id) || "-",
              textValue(row.summary),
            ];
          })}
        />
      ) : null}
      {section === "closure" ? <PreBlock value={dossier.closure} /> : null}
    </section>
  );
}

function DossierTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  useSettingsPreferences();
  if (!rows.length) return <p className="muted run-dossier-empty">{uiText("No entries.")}</p>;
  return (
    <div className="run-dossier-table-wrap">
      <table className="run-dossier-table">
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${row[0]}-${rowIndex}`}>
              {row.map((value, index) => <td className={index === 0 ? "mono" : ""} key={`${columns[index]}-${index}`}>{value || "-"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
