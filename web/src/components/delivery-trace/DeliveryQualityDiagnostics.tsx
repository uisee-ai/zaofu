import { useEffect, useState } from "react";

import {
  getRegressionCases,
  postAction,
} from "../../api/client";
import type { RegressionCase } from "../../api/client";
import { RegressionCasesPanel } from "./DeliveryTraceTabs";

export function DeliveryQualityDiagnostics({
  featureId,
  projectId,
}: {
  featureId: string;
  projectId: string;
}) {
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    setVerdicts({});
    void getRegressionCases(projectId, featureId)
      .then((result) => {
        if (!cancelled) setCases(result.cases ?? []);
      })
      .catch(() => {
        if (!cancelled) setCases([]);
      });
    return () => {
      cancelled = true;
    };
  }, [featureId, projectId]);

  const replayCase = (caseId: string) => {
    void postAction("replay-regression-case", { case_id: caseId }, projectId)
      .then((result) => {
        setVerdicts((current) => ({
          ...current,
          [caseId]: !!(result as { result?: { passed?: boolean } }).result?.passed,
        }));
      })
      .catch(() => undefined);
  };

  if (!cases.length) return null;

  return (
    <section className="delivery-quality-diagnostics" data-testid="delivery-quality-diagnostics">
      <RegressionCasesPanel cases={cases} onReplay={replayCase} verdicts={verdicts} />
    </section>
  );
}
