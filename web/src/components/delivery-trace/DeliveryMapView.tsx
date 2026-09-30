import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { useEffect, useMemo, useRef, useState } from "react";

import { getDeliveryTrace } from "../../api/client";
import type { DeliveryTrace } from "../../api/types";
import { GoalCoveragePage } from "../goal-coverage/GoalCoveragePage";
import { GoalCoverageStatus } from "../goal-coverage/GoalCoverageStatus";
import {
  deliveryWorkGoalOptions,
  type DeliveryWorkGoalOption,
} from "./deliveryWorkGoalModel";

const loadDeliveryWorkView = () => import("./DeliveryWorkView");
type DeliveryWorkViewComponent = typeof import("./DeliveryWorkView").DeliveryWorkView;

type DeliveryMapLens = "coverage" | "work";

export function DeliveryMapView({
  onSelectTask,
  projectId,
  trace,
}: {
  onSelectTask?: (taskId: string) => void;
  projectId: string;
  trace: DeliveryTrace;
}) {
  useSettingsPreferences();
  const scopeKey = `${projectId}\u0000${trace.feature_id}`;
  const [lens, setLens] = useState<DeliveryMapLens>("coverage");
  const [selectionScope, setSelectionScope] = useState(scopeKey);
  const [selectedGoalId, setSelectedGoalId] = useState("");
  const [workTrace, setWorkTrace] = useState<DeliveryTrace | null>(null);
  const [WorkViewComponent, setWorkViewComponent] = useState<
    DeliveryWorkViewComponent | null
  >(null);
  const [workRequestError, setWorkRequestError] = useState("");
  const [workModuleError, setWorkModuleError] = useState("");
  const [retry, setRetry] = useState(0);
  const requestGeneration = useRef(0);
  const moduleGeneration = useRef(0);
  const goals = useMemo(() => deliveryWorkGoalOptions(trace), [trace]);
  const scopeIsCurrent = selectionScope === scopeKey;
  const selectedGoalAvailable = goals.some((goal) => (
    goal.expandable && goal.goalId === selectedGoalId
  ));

  useEffect(() => {
    requestGeneration.current += 1;
    moduleGeneration.current += 1;
    setSelectionScope(scopeKey);
    setLens("coverage");
    setSelectedGoalId("");
    setWorkTrace(null);
    setWorkRequestError("");
    setWorkModuleError("");
  }, [scopeKey]);

  useEffect(() => {
    if (!selectedGoalId) return;
    if (goals.some((goal) => goal.expandable && goal.goalId === selectedGoalId)) return;
    requestGeneration.current += 1;
    moduleGeneration.current += 1;
    setSelectedGoalId("");
    setWorkTrace(null);
    setWorkRequestError("");
    setWorkModuleError("");
  }, [goals, selectedGoalId]);

  useEffect(() => {
    if (
      lens !== "work"
      || !selectedGoalId
      || !selectedGoalAvailable
      || !projectId
      || !trace.feature_id
      || selectionScope !== scopeKey
    ) {
      return undefined;
    }
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    setWorkRequestError("");
    // Avoid a duplicate network read from React StrictMode's effect probe.
    const timer = window.setTimeout(() => {
      void getDeliveryTrace(trace.feature_id, projectId, undefined, {
        bypassCache: true,
        goalId: selectedGoalId,
        signal: controller.signal,
        view: "work",
      })
        .then((nextTrace) => {
          if (requestGeneration.current !== generation) return;
          if (nextTrace.work_scope && !nextTrace.work_scope.matched) {
            throw new Error("The selected Goal is no longer available. Choose a current Goal again.");
          }
          setWorkTrace(nextTrace);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || requestGeneration.current !== generation) return;
          setWorkRequestError(error instanceof Error ? error.message : String(error));
        });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    lens,
    projectId,
    retry,
    selectedGoalAvailable,
    selectedGoalId,
    scopeKey,
    selectionScope,
    trace.cursor?.last_seq,
    trace.feature_id,
    trace.generated_at,
  ]);

  const selectLens = (nextLens: DeliveryMapLens) => {
    setLens(nextLens);
    if (nextLens === "work") return;
    requestGeneration.current += 1;
    moduleGeneration.current += 1;
    setSelectedGoalId("");
    setWorkTrace(null);
    setWorkRequestError("");
    setWorkModuleError("");
  };

  const selectGoal = (goalId: string) => {
    // The selection authorizes both resources. Start the heavy graph module
    // beside the scoped request instead of adding a second confirmation step.
    preloadWorkModule();
    requestGeneration.current += 1;
    setSelectedGoalId(goalId);
    setWorkTrace(null);
    setWorkRequestError("");
  };

  const preloadWorkModule = () => {
    const generation = ++moduleGeneration.current;
    setWorkModuleError("");
    void loadDeliveryWorkView()
      .then((module) => {
        if (moduleGeneration.current !== generation) return;
        setWorkViewComponent(() => module.DeliveryWorkView);
      })
      .catch((error: unknown) => {
        if (moduleGeneration.current !== generation) return;
        setWorkModuleError(error instanceof Error ? error.message : String(error));
      });
  };

  const retryWork = () => {
    preloadWorkModule();
    setWorkRequestError("");
    setRetry((value) => value + 1);
  };

  const visibleSelectedGoalId = scopeIsCurrent ? selectedGoalId : "";
  const visibleWorkError = scopeIsCurrent ? workModuleError || workRequestError : "";
  const visibleWorkTrace = scopeIsCurrent ? workTrace : null;

  return (
    <section className="delivery-map" data-testid="delivery-map">
      <div className="delivery-map-tabs" role="tablist" aria-label="Graph view">
        {(["coverage", "work"] as const).map((item) => (
          <button
            aria-selected={lens === item}
            className={lens === item ? "active" : ""}
            data-testid={`delivery-map-lens-${item}`}
            key={item}
            onClick={() => selectLens(item)}
            role="tab"
            type="button"
          >
            {item === "coverage" ? "Coverage" : "Work"}
          </button>
        ))}
      </div>

      {lens === "coverage" ? (
        <GoalCoveragePage
          deliveryTrace={trace}
          onSelectTask={onSelectTask}
        />
      ) : (
        <div className="delivery-work-progressive" data-testid="delivery-work-progressive">
          <WorkGoalPicker
            goals={goals}
            onSelect={selectGoal}
            selectedGoalId={visibleSelectedGoalId}
          />
          {visibleWorkError ? (
            <div
              className="delivery-map-resource-state tone-error"
              data-testid="delivery-work-error"
              role="alert"
            >
              <strong>Work graph unavailable</strong>
              <span>{visibleWorkError}</span>
              <button type="button" onClick={retryWork}>{uiText("Retry")}</button>
            </div>
          ) : visibleWorkTrace && WorkViewComponent ? (
            <WorkViewComponent
              onSelectTask={onSelectTask}
              trace={visibleWorkTrace}
            />
          ) : visibleSelectedGoalId ? (
            <div
              aria-busy="true"
              aria-live="polite"
              className="delivery-map-resource-state"
              data-testid="delivery-work-loading"
              role="status"
            >
              <span aria-hidden="true" className="delivery-map-loading-spinner" />
              <strong>
                {visibleWorkTrace
                  ? "Rendering Work graph…"
                  : "Loading selected Goal…"}
              </strong>
              <span>Fetching bounded Goal data and the Work renderer.</span>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function WorkGoalPicker({
  goals,
  onSelect,
  selectedGoalId,
}: {
  goals: DeliveryWorkGoalOption[];
  onSelect: (goalId: string) => void;
  selectedGoalId: string;
}) {
  useSettingsPreferences();
  return (
    <section className="delivery-work-goal-picker" data-testid="delivery-work-goal-picker">
      <header>
        <div>
          <span className="eyebrow">{uiText("Work scope")}</span>
          <h3>{uiText("Select a Goal to load Work")}</h3>
        </div>
        <span className="muted">{uiText("Selecting a Goal loads its bounded Work tree.")}</span>
      </header>
      {goals.length ? (
        <fieldset aria-label={uiText("Work Goal")}>
          <legend className="sr-only">{uiText("Work Goal")}</legend>
          {goals.map((goal) => (
            <label
              className={selectedGoalId === goal.goalId ? "is-selected" : ""}
              key={goal.nodeId}
            >
              <input
                checked={selectedGoalId === goal.goalId}
                disabled={!goal.expandable}
                name="delivery-work-goal"
                onChange={() => onSelect(goal.goalId)}
                type="radio"
                value={goal.goalId}
              />
              <span>
                <strong>{goal.title}</strong>
                <small className="mono">{goal.goalId}</small>
              </span>
              <span>
                {goal.statusLabel ? (
                  <GoalCoverageStatus label={goal.statusLabel} status={goal.status} />
                ) : null}
                <small>{goal.claimCount} claim{goal.claimCount === 1 ? "" : "s"}</small>
                {!goal.expandable ? (
                  <small className="muted" data-testid="delivery-work-goal-unavailable">
                    {goal.reason}
                  </small>
                ) : null}
              </span>
            </label>
          ))}
        </fieldset>
      ) : (
        <p className="delivery-work-goal-empty">{uiText("No Goal summary is available for this feature.")}</p>
      )}
    </section>
  );
}
