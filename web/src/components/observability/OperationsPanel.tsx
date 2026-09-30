import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { useEffect, useState } from "react";

import { getOperationsObservability } from "../../api/client";
import type { OperationsObservability } from "../../api/types";

function signalText(signals: Record<string, boolean>): string {
  return Object.entries(signals)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name)
    .join(", ") || "none";
}

export function OperationsPanel({ projectId }: { projectId?: string }) {
  useSettingsPreferences();
  const [data, setData] = useState<OperationsObservability | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getOperationsObservability(projectId)
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        setError("");
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">{uiText("Loading operations status…")}</p>;
  const capabilities = data.provider_telemetry.capabilities;
  const exporter = data.otlp_exporter;
  const exporterCounters = exporter.counters;
  return (
    <div className="observability-resource-grid" data-testid="operations-observability">
      <section className="subsection">
        <div className="inline-heading"><h3>{uiText("Operations")}</h3><span className="muted">{data.scope.kind}</span></div>
        <dl className="key-value-grid">
          <dt>Telemetry request</dt><dd>{data.provider_telemetry.requested_mode}</dd>
          <dt>{uiText("Runtime logs")}</dt><dd>{data.runtime_logs.count}</dd>
          <dt>{uiText("Warnings / errors")}</dt><dd>{(data.runtime_logs.levels.WARN ?? 0) + (data.runtime_logs.levels.ERROR ?? 0)}</dd>
          <dt>{uiText("Metrics")}</dt><dd>{data.metrics.enabled ? `${data.metrics.counter_series} counters` : "disabled"}</dd>
          <dt>{uiText("Operational attention")}</dt><dd>{data.alerts.enabled ? `${data.alerts.emitted_total} observed` : "disabled"}</dd>
        </dl>
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>OTLP Exporter</h3><span className="muted">{exporter.enabled ? exporter.health : "disabled"}</span></div>
        <dl className="key-value-grid">
          <dt>Backlog</dt><dd>{exporter.backlog_events} events</dd>
          <dt>{uiText("Pending batch")}</dt><dd>{exporter.pending.event_count} events / {exporter.pending.span_count} spans</dd>
          <dt>{uiText("Last success")}</dt><dd>{exporter.last_success_at || "-"}</dd>
          <dt>{uiText("Last failure")}</dt><dd>{exporter.last_failure_class || "-"}</dd>
          <dt>{uiText("Policy")}</dt><dd>{exporterCounters.sampled_out ?? 0} sampled · {exporterCounters.dropped_by_policy ?? 0} dropped · {exporterCounters.redacted_fields ?? 0} redacted</dd>
          <dt>{uiText("Stream gaps")}</dt><dd>{data.alerts.last_sse_gap_sequence}</dd>
        </dl>
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>{uiText("Delivery Boundary")}</h3><span className="muted">unchanged</span></div>
        <p className="muted">Delivery Graph, Run Graph, Fanout DAG, gates, and evidence remain in Delivery. This panel only exposes process health and provider capability.</p>
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>{uiText("Provider Telemetry")}</h3><span className="muted">{capabilities.length} routes observed</span></div>
        {capabilities.length === 0 ? <p className="muted">{uiText("No provider route has been observed yet.")}</p> : (
          <table className="data-table">
            <thead><tr><th>{uiText("Provider")}</th><th>Route</th><th>{uiText("Effective")}</th><th>{uiText("Join")}</th><th>{uiText("Signals")}</th><th>{uiText("Reason")}</th></tr></thead>
            <tbody>{capabilities.map((item) => (
              <tr key={`${item.provider}-${item.route}`}>
                <td>{item.provider}</td><td>{item.route}</td><td>{item.effective}</td><td>{item.join_kind}</td><td>{signalText(item.signals)}</td><td>{item.failure_class || "-"}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}
