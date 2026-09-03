import { CircleX, Pause, Play } from "lucide-react";
import { useState } from "react";
import { postAction } from "../api/client";
import type { IssueTriageItem } from "../api/types";

const PAUSABLE_STATES = new Set(["triaging", "triage_recovering", "fixing", "fix_recovering", "verifying"]);
const PAUSED_STATES = new Set(["triage_paused", "fix_paused"]);

export function IssueTriageRunDialog({
  projectId,
  issue,
  onClose,
  onUpdated,
}: {
  projectId: string;
  issue: IssueTriageItem;
  onClose: () => void;
  onUpdated: (notice: string) => Promise<void>;
}) {
  const state = issue.workflow?.state ?? "";
  const runId = issue.workflow?.run_id ?? "";
  const queued = state === "triage_queued" || state === "fix_queued";
  const paused = PAUSED_STATES.has(state);
  const pausable = PAUSABLE_STATES.has(state);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | "">("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");

  const apply = async (kind: "pause" | "resume" | "cancel") => {
    if (!runId) {
      setError("This Issue no longer has a manageable Run.");
      return;
    }
    setBusy(kind);
    setError("");
    try {
      const action = `run-${kind}`;
      const result = await postAction(action, {
        run_id: runId,
        reason: `Operator ${kind} for GitHub Issue #${issue.number}`,
      }, projectId);
      if (!result.ok) {
        // A stale mirror can outlive a rejected/expired Run.  Treat that as a
        // refresh race instead of leaving the operator trapped in a red modal;
        // the refreshed projection will expose Start Triage again.
        if (result.status === "not_found" || /run not found/i.test(result.reason || "")) {
          await onUpdated(`The ${queued ? "queued " : ""}Run was no longer available; Issue state refreshed.`);
          onClose();
          return;
        }
        throw new Error(result.reason || `Unable to ${kind} this Run.`);
      }
      const notice = kind === "pause"
        ? `Pause requested for GitHub Issue #${issue.number}; already-dispatched work may finish.`
        : kind === "resume"
          ? `Run resumed for GitHub Issue #${issue.number}.`
          : `${queued ? "Queued Run" : "Run"} permanently cancelled for GitHub Issue #${issue.number}.`;
      await onUpdated(notice);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel issue-triage-start-modal" role="dialog" aria-modal="true" aria-labelledby="issue-run-title">
        <header className="section-heading">
          <div>
            <h2 id="issue-run-title">{queued ? `Cancel queued ${state === "fix_queued" ? "Fix" : "Triage"}` : "Manage Run"}</h2>
            <span className="muted">GitHub Issue #{issue.number} · {state}</span>
          </div>
          <button className="icon-button" disabled={Boolean(busy)} type="button" onClick={onClose}>Close</button>
        </header>
        <div className="modal-body issue-triage-start-body">
          <div className="issue-triage-start-selection"><strong>{issue.title}</strong><code>{runId}</code></div>
          {pausable ? (
            <div className="issue-run-control-section">
              <strong>Pause after current dispatch</strong>
              <p>Pause blocks new dispatches. Work already dispatched may finish before the pause takes effect.</p>
              <button className="icon-button" disabled={Boolean(busy)} type="button" onClick={() => void apply("pause")}>
                <Pause aria-hidden="true" size={15} />{busy === "pause" ? "Pausing…" : "Pause Run"}
              </button>
            </div>
          ) : null}
          {paused ? (
            <div className="issue-run-control-section">
              <strong>Resume from checkpoint</strong>
              <p>Resume reopens dispatch from the persisted Run state; completed work is not repeated.</p>
              <button className="icon-button primary" disabled={Boolean(busy)} type="button" onClick={() => void apply("resume")}>
                <Play aria-hidden="true" size={15} />{busy === "resume" ? "Resuming…" : "Resume Run"}
              </button>
            </div>
          ) : null}
          <div className="issue-run-permanent-warning">
            <strong>Permanent cancellation</strong>
            <p>This cannot be resumed. Cancellation does not roll back files already written to the local worktree.</p>
            <label>
              <input checked={confirmed} type="checkbox" onChange={(event) => setConfirmed(event.target.checked)} />
              I understand this Run is permanently cancelled and local files are not reverted.
            </label>
            <button className="icon-button danger" disabled={Boolean(busy) || !confirmed} type="button" onClick={() => void apply("cancel")}>
              <CircleX aria-hidden="true" size={15} />{busy === "cancel" ? "Cancelling…" : "Cancel permanently"}
            </button>
          </div>
          {error ? <div className="issue-triage-start-error" role="alert">{error}</div> : null}
        </div>
      </section>
    </div>
  );
}
