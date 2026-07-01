// Velocity / Quality / Economy strip on Overview. Read-only display of the
// kernel 12-metric snapshot (MetricsCollector is the only computer — I7);
// missing values render as "-", queue-wait stays a placeholder until a
// task-level ready signal exists.
import type { MetricsSnapshotProjection, TaskFlowStats } from "../../api/types";

interface MetricsStripProps {
  metrics: MetricsSnapshotProjection | null | undefined;
  taskFlow: TaskFlowStats | null | undefined;
}

interface MetricItem {
  label: string;
  value: string;
}

function fmt(value: unknown, digits = 2, suffix = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value.toFixed(digits)}${suffix}`;
}

function fmtPercent(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

export function MetricsStrip({ metrics: rawMetrics, taskFlow }: MetricsStripProps) {
  // Always render with stable layout; missing kernel metrics show as "-".
  const metrics: MetricsSnapshotProjection = rawMetrics ?? {};
  const groups: Array<{ title: string; items: MetricItem[] }> = [
    {
      title: "Velocity",
      items: [
        {
          label: "throughput",
          value: taskFlow && taskFlow.done_24h > 0
            ? `${fmt(taskFlow.throughput_per_hour_24h, 2)}/h (24h)`
            : `${fmt(metrics.throughput_per_hour, 2)}/h`,
        },
        { label: "lead time", value: fmt(metrics.avg_task_duration_minutes, 0, " min") },
        { label: "queue wait", value: "-" },
      ],
    },
    {
      title: "Quality",
      items: [
        { label: "vcr", value: fmt(metrics.vcr, 2) },
        { label: "rework", value: fmtPercent(metrics.rework_ratio) },
        { label: "scope viol.", value: fmtPercent(metrics.scope_violation_rate) },
      ],
    },
    {
      title: "Economy",
      items: [
        { label: "cost/task", value: typeof metrics.cost_per_task === "number" ? `$${metrics.cost_per_task.toFixed(2)}` : "-" },
        { label: "token/task", value: typeof metrics.token_per_task === "number" ? `${Math.round(metrics.token_per_task / 1000)}k` : "-" },
        { label: "budget breach", value: fmtPercent(metrics.budget_breach_rate) },
      ],
    },
  ];
  return (
    <section className="subsection metrics-strip" data-testid="overview-metrics-strip">
      <div className="inline-heading">
        <h3 className="section-title">Velocity / Quality / Economy</h3>
        <span className="muted">
          kernel 12-metric · window {fmt(metrics.window_hours, 1, "h")}
        </span>
      </div>
      <div className="metrics-strip-groups">
        {groups.map((group) => (
          <div className="metrics-strip-group" key={group.title}>
            <span className="muted">{group.title}</span>
            {/* KV 规范①:键 tertiary、值 500;数值带单位走 tabular+sans(非 id/hash 不用 mono)。 */}
            {group.items.map((item) => (
              <div className="metrics-strip-item kv" key={item.label}>
                <span className="kv-key">{item.label}</span>
                <strong className="kv-value">{item.value}</strong>
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

export function sparkline(buckets: number[] | undefined): string {
  if (!buckets || buckets.length === 0) return "";
  const blocks = "▁▂▃▄▅▆▇█";
  const max = Math.max(...buckets, 1);
  return buckets.map((count) => blocks[Math.min(7, Math.round((count / max) * 7))]).join("");
}
