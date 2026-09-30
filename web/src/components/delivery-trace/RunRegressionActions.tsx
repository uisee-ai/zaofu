import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { useEffect, useRef, useState } from "react";

import { getRegressionCases, postAction } from "../../api/client";
import type { RegressionCase } from "../../api/client";
import type { DeliveryTrace } from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { canonicalTraceIdForTask, isRegressionCaptureEligible } from "./runTraceRefs";

function newIdempotencyKey(prefix: string): string {
  const nonce = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${nonce}`;
}

export function RunRegressionActions({
  featureId,
  onOpenPage,
  projectId,
  taskId,
  trace,
}: {
  featureId: string;
  onOpenPage?: (page: PageId) => void;
  projectId?: string;
  taskId: string;
  trace: DeliveryTrace;
}) {
  useSettingsPreferences();
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [casesStatus, setCasesStatus] = useState<"loading" | "ready" | "error">("loading");
  const [casesError, setCasesError] = useState("");
  const [verdicts, setVerdicts] = useState<Record<string, boolean>>({});
  const [captured, setCaptured] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const busyRef = useRef(false);
  const captureIdempotencyRef = useRef("");
  const replayIdempotencyRefs = useRef<Record<string, string>>({});
  const canonicalTraceId = canonicalTraceIdForTask(trace, taskId);
  const captureEligible = isRegressionCaptureEligible(trace, taskId);

  useEffect(() => {
    let cancelled = false;
    setVerdicts({});
    setCaptured(false);
    setCases([]);
    setCasesError("");
    setCasesStatus("loading");
    setActionError("");
    setActionNotice("");
    captureIdempotencyRef.current = "";
    replayIdempotencyRefs.current = {};
    if (!projectId) {
      setCasesStatus("ready");
      return () => { cancelled = true; };
    }
    void getRegressionCases(projectId, featureId)
      .then((result) => {
        if (!cancelled) {
          setCases((result.cases ?? []).filter((item) => item.source_task_id === taskId));
          setCasesStatus("ready");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCasesStatus("error");
          setCasesError(error instanceof Error ? error.message : String(error));
        }
      });
    return () => { cancelled = true; };
  }, [featureId, projectId, taskId]);

  const capture = () => {
    if (!projectId || busyRef.current) return;
    if (!captureIdempotencyRef.current) {
      captureIdempotencyRef.current = newIdempotencyKey(
        `delivery-regression-capture:${projectId}:${featureId}:${taskId}`,
      );
    }
    busyRef.current = true;
    setBusyAction("capture");
    setActionError("");
    setActionNotice("");
    void postAction("capture-regression-case", {
      task_id: taskId,
      feature_id: featureId,
      assertions: ["rework==0", "scope_violation==0"],
      idempotency_key: captureIdempotencyRef.current,
    }, projectId)
      .then((result) => {
        if (!result.ok) {
          // The server produced a definitive verdict, so a later explicit
          // retry is a new attempt and must not replay this rejection.
          captureIdempotencyRef.current = "";
          throw new Error(result.reason || "Regression capture was rejected.");
        }
        if (result.status === "duplicate_pending") {
          setActionNotice("Capture is still processing; retry will reuse the same request.");
          return;
        }
        setCaptured(true);
      })
      .catch((error: unknown) => {
        // Transport/parse failures have an unknown server outcome. Preserve
        // the key so retry cannot duplicate an action that may have landed.
        setActionError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        busyRef.current = false;
        setBusyAction("");
      });
  };

  const replay = (caseId: string) => {
    if (!projectId || busyRef.current) return;
    if (!replayIdempotencyRefs.current[caseId]) {
      replayIdempotencyRefs.current[caseId] = newIdempotencyKey(
        `delivery-regression-replay:${projectId}:${caseId}`,
      );
    }
    busyRef.current = true;
    setBusyAction(`replay:${caseId}`);
    setActionError("");
    setActionNotice("");
    void postAction("replay-regression-case", {
      case_id: caseId,
      idempotency_key: replayIdempotencyRefs.current[caseId],
    }, projectId)
      .then((result) => {
        if (!result.ok) {
          delete replayIdempotencyRefs.current[caseId];
          throw new Error(result.reason || "Regression replay was rejected.");
        }
        if (result.status === "duplicate_pending") {
          setActionNotice("Replay is still processing; retry will reuse the same request.");
          return;
        }
        setVerdicts((current) => ({
          ...current,
          [caseId]: !!(result as { result?: { passed?: boolean } }).result?.passed,
        }));
      })
      .catch((error: unknown) => {
        setActionError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        busyRef.current = false;
        setBusyAction("");
      });
  };

  const openTrace = () => {
    if (!canonicalTraceId) return;
    const params = new URLSearchParams(window.location.search);
    params.set("page", "traces");
    params.set("trace_id", canonicalTraceId);
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}${window.location.hash}`);
    onOpenPage?.("traces");
  };

  return (
    <section className="run-regression-actions" data-testid="run-regression-actions">
      <div className="run-regression-head">
        <strong>{uiText("Task evidence")}</strong>
        <div>
          {canonicalTraceId ? (
            <button data-testid="run-open-trace" onClick={openTrace} type="button">Open in Trace</button>
          ) : null}
          {captureEligible && casesStatus === "ready" && cases.length === 0 ? (
            <button data-testid="run-capture-regression" disabled={!projectId || Boolean(busyAction) || captured} onClick={capture} type="button">
              {busyAction === "capture" ? "Capturing…" : captured ? "Captured" : "Capture regression"}
            </button>
          ) : null}
        </div>
      </div>
      {cases.length ? (
        <div className="run-regression-cases" data-testid="regression-cases">
          {cases.map((item) => (
            <div className="run-regression-case" data-testid="regression-case-row" key={item.case_id}>
              <span><strong>{item.case_id}</strong> · {(item.assertions ?? []).join(", ") || "no assertions"}</span>
              <button
                data-testid="regression-replay-btn"
                disabled={Boolean(busyAction) || verdicts[item.case_id] !== undefined}
                onClick={() => replay(item.case_id)}
                type="button"
              >
                {busyAction === `replay:${item.case_id}`
                  ? "Replaying…"
                  : verdicts[item.case_id] !== undefined ? "Replayed" : "Replay"}
              </button>
              {verdicts[item.case_id] !== undefined ? (
                <span className={verdicts[item.case_id] ? "tone-ok" : "tone-err"} data-testid="regression-verdict">
                  {verdicts[item.case_id] ? "Pass" : "Fail"}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      ) : casesStatus === "loading" ? (
        <span className="muted">{uiText("Loading regression cases…")}</span>
      ) : casesStatus === "ready" ? (
        <span className="muted">
          No regression case captured for this task.
        </span>
      ) : null}
      {casesStatus === "error" ? (
        <p className="tone-err" data-testid="regression-cases-error" role="alert">
          Regression cases unavailable: {casesError || "request failed"}
        </p>
      ) : null}
      {actionNotice ? <p className="muted" data-testid="run-regression-pending" role="status">{actionNotice}</p> : null}
      {actionError ? <p className="tone-err" data-testid="run-regression-error" role="alert">{actionError}</p> : null}
    </section>
  );
}
