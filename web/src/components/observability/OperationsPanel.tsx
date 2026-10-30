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
  if (!data) return <p className="muted">Loading operations status…</p>;
  const capabilities = data.provider_telemetry.capabilities;
  const exporter = data.otlp_exporter;
  const exporterCounters = exporter.counters;
  return (
    <div className="observability-resource-grid" data-testid="operations-observability">
      <section className="subsection">
        <div className="inline-heading"><h3>Operations</h3><span className="muted">{data.scope.kind}</span></div>
        <dl className="key-value-grid">
          <dt>Telemetry request</dt><dd>{data.provider_telemetry.requested_mode}</dd>
          <dt>Runtime logs</dt><dd>{data.runtime_logs.count}</dd>
          <dt>Warnings / errors</dt><dd>{(data.runtime_logs.levels.WARN ?? 0) + (data.runtime_logs.levels.ERROR ?? 0)}</dd>
          <dt>Metrics</dt><dd>{data.metrics.enabled ? `${data.metrics.counter_series} counters` : "disabled"}</dd>
          <dt>Operational attention</dt><dd>{data.alerts.enabled ? `${data.alerts.emitted_total} observed` : "disabled"}</dd>
        </dl>
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>OTLP Exporter</h3><span className="muted">{exporter.enabled ? exporter.health : "disabled"}</span></div>
        <dl className="key-value-grid">
          <dt>Backlog</dt><dd>{exporter.backlog_events} events</dd>
          <dt>Pending batch</dt><dd>{exporter.pending.event_count} events / {exporter.pending.span_count} spans</dd>
          <dt>Last success</dt><dd>{exporter.last_success_at || "-"}</dd>
          <dt>Last failure</dt><dd>{exporter.last_failure_class || "-"}</dd>
          <dt>Policy</dt><dd>{exporterCounters.sampled_out ?? 0} sampled · {exporterCounters.dropped_by_policy ?? 0} dropped · {exporterCounters.redacted_fields ?? 0} redacted</dd>
          <dt>Stream gaps</dt><dd>{data.alerts.last_sse_gap_sequence}</dd>
        </dl>
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>Delivery Boundary</h3><span className="muted">unchanged</span></div>
        <p className="muted">Delivery Graph, Run Graph, Fanout DAG, gates, and evidence remain in Delivery. This panel only exposes process health and provider capability.</p>
      </section>
      <section className="subsection" data-testid="owner-delivery-health">
        <div className="inline-heading"><h3>通知投递</h3></div>
        {!data.owner_delivery?.available ? <p className="muted">投递状态不可用。</p>
          : !data.owner_delivery.routes.some((route) => route.status === "failed") ? <p className="muted">暂无未恢复的投递失败。</p>
            : <ul>{data.owner_delivery.routes.filter((route) => route.status === "failed").map((route) => (
              <li key={`${route.target}:${route.route}`}>{route.target} / {route.route}：{route.reason || "投递失败"}（累计 {route.failure_count} 次）</li>
            ))}</ul>}
      </section>
      <section className="subsection">
        <div className="inline-heading"><h3>Provider Telemetry</h3><span className="muted">{capabilities.length} routes observed</span></div>
        {capabilities.length === 0 ? <p className="muted">No provider route has been observed yet.</p> : (
          <table className="data-table">
            <thead><tr><th>Provider</th><th>Route</th><th>Effective</th><th>Join</th><th>Signals</th><th>Reason</th></tr></thead>
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
