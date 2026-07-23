import { useEffect, useState } from "react";

import type { DeliveryTrace, Feature } from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { GoalCoveragePage } from "../goal-coverage/GoalCoveragePage";
import { DeliveryThickGraphView } from "./DeliveryThickGraphView";
import { DeliveryQualityDiagnostics } from "./DeliveryQualityDiagnostics";
import { DeliveryWorkView } from "./DeliveryWorkView";

type DeliveryMapLens = "coverage" | "work" | "diagnostics";

export function DeliveryMapView({
  feature,
  onOpenPage,
  onSelectTask,
  projectId,
  trace,
}: {
  feature: Feature | null;
  onOpenPage?: (page: PageId) => void;
  onSelectTask?: (taskId: string) => void;
  projectId: string;
  trace: DeliveryTrace;
}) {
  const [lens, setLens] = useState<DeliveryMapLens>("coverage");
  const [workClaimId, setWorkClaimId] = useState("");

  useEffect(() => {
    setLens("coverage");
    setWorkClaimId("");
  }, [trace.feature_id]);

  const features: Feature[] = feature ? [feature] : [{
    id: trace.feature_id,
    title: trace.feature_id,
    status: trace.status,
    priority: 0,
  }];

  return (
    <section className="delivery-map" data-testid="delivery-map">
      <div className="delivery-map-tabs" role="tablist" aria-label="Graph view">
        {(["coverage", "work", "diagnostics"] as const).map((item) => (
          <button
            aria-selected={lens === item}
            className={lens === item ? "active" : ""}
            data-testid={`delivery-map-lens-${item}`}
            key={item}
            onClick={() => setLens(item)}
            role="tab"
            type="button"
          >
            {item === "coverage" ? "Coverage" : item === "work" ? "Work" : "Diagnostics"}
          </button>
        ))}
      </div>

      {lens === "coverage" ? (
        <GoalCoveragePage
          deliveryTrace={trace}
          embedded
          features={features}
          onOpenWork={(claimId) => {
            setWorkClaimId(claimId);
            setLens("work");
          }}
          onSelectTask={onSelectTask}
          projectId={projectId}
        />
      ) : lens === "work" ? (
        <DeliveryWorkView
          focusedClaimId={workClaimId}
          onSelectTask={onSelectTask}
          trace={trace}
        />
      ) : (
        <section className="delivery-map-diagnostics" data-testid="delivery-map-diagnostics">
          {trace.thick_trace?.graph?.nodes?.length ? (
            <DeliveryThickGraphView
              onOpenPage={onOpenPage}
              onSelectTask={onSelectTask}
              trace={trace}
            />
          ) : (
            <div className="delivery-map-empty">No diagnostics projected for this delivery.</div>
          )}
          <DeliveryQualityDiagnostics
            featureId={trace.feature_id}
            projectId={projectId}
          />
        </section>
      )}
    </section>
  );
}
