import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, FileCheck2, Gauge, PlayCircle } from "lucide-react";
import type { ActionResponse } from "../../api/types";
import {
  getFailureCandidatesProjection,
  getRealE2eMatrixProjection,
  getRunContractProjection,
  postAction,
} from "../../api/client";
import { KeyValuePanel, PreBlock, ProjectionMetricGrid, asRecord, asRecordArray, textValue } from "../../app/shared";
import type { ProjectionMetricSpec } from "../../app/sharedTypes";

interface ControlRoomPageProps {
  actionReady: boolean;
  projectId?: string;
}

export function ControlRoomPage({ actionReady, projectId }: ControlRoomPageProps) {
  const [runContract, setRunContract] = useState<Record<string, unknown> | null>(null);
  const [failureCandidates, setFailureCandidates] = useState<Record<string, unknown> | null>(null);
  const [realE2e, setRealE2e] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionResult, setActionResult] = useState<ActionResponse | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    try {
      const [contractValue, candidatesValue, realE2eValue] = await Promise.all([
        getRunContractProjection(projectId),
        getFailureCandidatesProjection(projectId),
        getRealE2eMatrixProjection(projectId),
      ]);
      setRunContract(contractValue);
      setFailureCandidates(candidatesValue);
      setRealE2e(realE2eValue);
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const contract = asRecord(runContract?.contract);
  const refs = asRecord(contract.refs);
  const failureItems = asRecordArray(failureCandidates?.items);
  const matrices = asRecordArray(realE2e?.matrices);
  const matrixSummary = asRecord(realE2e?.summary);
  const metrics = useMemo<ProjectionMetricSpec[]>(() => ([
    {
      icon: Gauge,
      label: "Contract",
      value: textValue(runContract?.status) || "missing",
      meta: textValue(contract.contract_digest).slice(0, 12) || "no digest",
      tone: textValue(runContract?.status) === "present" ? "ok" : "warn",
    },
    {
      icon: AlertTriangle,
      label: "Failures",
      value: Number(failureCandidates?.count ?? failureItems.length),
      meta: "failure candidates",
      tone: failureItems.length ? "warn" : "ok",
    },
    {
      icon: PlayCircle,
      label: "Real E2E",
      value: Number(matrixSummary.loaded ?? matrices.length),
      meta: `${Number(matrixSummary.missing ?? 0)} missing`,
      tone: Number(matrixSummary.missing ?? 0) ? "warn" : "info",
    },
    {
      icon: FileCheck2,
      label: "Refs",
      value: Object.values(refs).reduce<number>(
        (total, value) => total + (Array.isArray(value) ? value.length : 0),
        0,
      ),
      meta: "hydration refs",
      tone: "info",
    },
  ]), [contract.contract_digest, failureCandidates?.count, failureItems.length, matrixSummary.loaded, matrixSummary.missing, matrices.length, refs, runContract?.status]);

  async function materializeFailures() {
    const result = await postAction("failure-closeout", {
      kinds: ["backlog", "eval", "skill"],
      output_root: "artifacts/failure-closeout",
      source: "web-control-room",
    }, projectId);
    setActionResult(result);
    await load();
  }

  async function runRealE2e() {
    const result = await postAction("real-e2e-run", {
      source: "web-control-room",
    }, projectId);
    setActionResult(result);
    await load();
  }

  async function reviewRunContract() {
    const result = await postAction("run-contract-review", {
      decision: "reviewed",
      source: "web-control-room",
    }, projectId);
    setActionResult(result);
    await load();
  }

  async function activateFailureCloseout() {
    const lastManifest = textValue(asRecord(actionResult).manifest_ref);
    const manifestRef = window.prompt("manifest_ref", lastManifest || "artifacts/failure-closeout/failure-closeout-manifest.json");
    if (!manifestRef) return;
    const approvalRef = window.prompt("approval_ref", "");
    if (!approvalRef) return;
    const result = await postAction("failure-closeout-activate", {
      manifest_ref: manifestRef,
      approval_ref: approvalRef,
      source: "web-control-room",
    }, projectId);
    setActionResult(result);
    await load();
  }

  return (
    <div className="projection-page-shell control-room-page">
      <div className="section-heading projection-page-heading">
        <div>
          <h2>Control Room</h2>
          <span className="muted">run contract, failure closeout, and real E2E readiness</span>
        </div>
        <button className="icon-button" type="button" onClick={() => void load()}>
          Refresh
        </button>
      </div>
      <ProjectionMetricGrid metrics={metrics} />
      {error ? <p className="empty-text compact-error">{error}</p> : null}
      {loading ? <p className="muted">Loading control room…</p> : null}
      <div className="control-room-grid">
        <section className="subsection">
          <div className="inline-heading">
            <h3>Run Contract</h3>
            <div className="button-row compact">
              <span className="muted">{textValue(runContract?.status) || "unknown"}</span>
              <button
                className="icon-button"
                disabled={!actionReady || textValue(runContract?.status) !== "present"}
                type="button"
                onClick={() => void reviewRunContract()}
              >
                Review
              </button>
            </div>
          </div>
          <KeyValuePanel
            title="Contract Summary"
            rows={[
              { key: "digest", value: textValue(contract.contract_digest) || "-" },
              { key: "run_tag", value: textValue(contract.run_tag) || "-" },
              { key: "contract_ref", value: textValue(runContract?.run_contract_ref) || "-" },
              { key: "required_artifacts", value: String(asRecordArray(contract.required_delivery_artifacts).length) },
            ]}
          />
          <PreBlock value={JSON.stringify(refs, null, 2)} />
        </section>
        <section className="subsection">
          <div className="inline-heading">
            <h3>Failure Closeout</h3>
            <button
              className="primary-action"
              disabled={!actionReady || failureItems.length === 0}
              type="button"
              onClick={() => void materializeFailures()}
            >
              Materialize
            </button>
            <button
              className="icon-button"
              disabled={!actionReady}
              type="button"
              onClick={() => void activateFailureCloseout()}
            >
              Activate
            </button>
          </div>
          {failureItems.length ? (
            <div className="compact-list">
              {failureItems.slice(0, 8).map((item) => (
                <div className="compact-list-row" key={textValue(item.failure_id) || textValue(item.path)}>
                  <strong>{textValue(item.failure_id) || "failure"}</strong>
                  <span className="muted">{textValue(asRecord(item.event).type) || textValue(item.path)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-text">No failure candidates.</p>
          )}
          {actionResult ? <PreBlock value={JSON.stringify(actionResult, null, 2)} /> : null}
        </section>
        <section className="subsection">
          <div className="inline-heading">
            <h3>Real E2E Matrix</h3>
            <div className="button-row compact">
              <span className="muted">{matrices.length} matrix file(s)</span>
              <button
                className="primary-action"
                disabled={!actionReady || matrices.length === 0}
                type="button"
                onClick={() => void runRealE2e()}
              >
                Run
              </button>
            </div>
          </div>
          <PreBlock value={JSON.stringify(realE2e ?? {}, null, 2)} />
        </section>
      </div>
    </div>
  );
}
