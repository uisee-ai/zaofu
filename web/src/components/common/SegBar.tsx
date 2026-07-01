// SegBar — shared wait/active/rework horizontal segment bar (delivery slice 1).
// Colors come from styles.css tokens only: wait=neutral gray (muted-foreground
// mix), active=var(--brand), rework=var(--warn). Fixed-width track; hover
// title carries the formatted seconds for each segment.

interface SegBarProps {
  wait?: number | null;
  active?: number | null;
  rework?: number | null;
  /** Optional denominator (seconds); bar fills sum/totalHint of the track. */
  totalHint?: number | null;
  mini?: boolean;
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return minutes > 0 ? `${hours}h${minutes}m` : `${hours}h`;
  if (minutes > 0) return `${minutes}m`;
  return `${total}s`;
}

export function SegBar({ wait, active, rework, totalHint, mini }: SegBarProps) {
  const w = Math.max(0, wait ?? 0);
  const a = Math.max(0, active ?? 0);
  const r = Math.max(0, rework ?? 0);
  const total = Math.max(totalHint ?? 0, w + a + r);
  const pct = (v: number) => (total > 0 ? `${(v / total) * 100}%` : "0%");
  const title = `wait ${formatSeconds(wait)} · active ${formatSeconds(active)} · rework ${formatSeconds(rework)}`;
  return (
    <span
      className={`seg-bar${mini ? " seg-bar-mini" : ""}`}
      title={title}
      role="img"
      aria-label={title}
    >
      <span className="seg-wait" style={{ width: pct(w) }} />
      <span className="seg-active" style={{ width: pct(a) }} />
      <span className="seg-rework" style={{ width: pct(r) }} />
    </span>
  );
}
