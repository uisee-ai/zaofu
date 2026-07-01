// RUN PULSE band on Overview — overview-pulse.v1 run_pulse liveness strip.
// Null/missing values render as a muted "—": no-data is never shown as 0.
import type { RunPulse } from "../../api/types";
import { sparkline } from "./MetricsStrip";

const LAST_EVENT_WARN_SECONDS = 180;
const RESPAWN_CRIT_STREAK = 3;
const DEFAULT_BUCKET_SECONDS = 300;

function fmtAge(value: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${Math.round(value / 3600)}h`;
}

function PulseItem({ label, value, tone, hint }: {
  label: string;
  value: string | null;
  tone?: "warn" | "crit";
  hint?: string;
}) {
  return (
    <span className="pulse-band-item" title={hint}>
      <span className="muted">{label}</span>
      {value == null ? (
        <strong className="mono pulse-no-data">—</strong>
      ) : (
        <strong className={`mono${tone ? ` is-${tone}` : ""}`}>{value}</strong>
      )}
    </span>
  );
}

export function PulseBand({ pulse }: { pulse: RunPulse | null | undefined }) {
  if (!pulse) {
    return (
      <section className="pulse-band-section" data-testid="overview-pulse-band">
        <div className="pulse-band is-empty">
          <span className="pulse-band-label section-title">RUN PULSE</span>
          <span className="muted">no data</span>
        </div>
      </section>
    );
  }

  const lastAgeSeconds = pulse.last_event_age_seconds;
  const lastAge = fmtAge(lastAgeSeconds);
  const lastWarn = typeof lastAgeSeconds === "number" && lastAgeSeconds > LAST_EVENT_WARN_SECONDS;

  const loopStatus = typeof pulse.loop?.status === "string" && pulse.loop.status ? pulse.loop.status : null;
  const loopAge = fmtAge(pulse.loop?.age_seconds);
  const loopWarn = loopStatus === "stale" || loopStatus === "not_running";

  const buckets = Array.isArray(pulse.events_per_bucket) ? pulse.events_per_bucket : [];
  const bucketSeconds = typeof pulse.bucket_seconds === "number" && pulse.bucket_seconds > 0
    ? pulse.bucket_seconds
    : DEFAULT_BUCKET_SECONDS;
  const latestBucket = buckets.length ? buckets[buckets.length - 1] : null;
  const hasBucketEvents = buckets.some((count) => typeof count === "number" && count > 0);
  const perMinute = typeof latestBucket === "number"
    ? (latestBucket / (bucketSeconds / 60)).toFixed(1)
    : null;
  const eventRateLabel = perMinute === "0.0" ? "idle" : perMinute;

  const streak = typeof pulse.respawn_failed_streak === "number" ? pulse.respawn_failed_streak : null;
  const cooldown = pulse.respawn_cooldown_instances ?? [];

  const sessions = pulse.sessions;
  const sessActive = typeof sessions?.active === "number" ? sessions.active : null;
  const sessStale = typeof sessions?.stale === "number" ? sessions.stale : 0;
  const sessHint = sessions?.by_backend && Object.keys(sessions.by_backend).length
    ? Object.entries(sessions.by_backend).map(([k, v]) => `${k} ${v}`).join(" · ")
    : undefined;

  return (
    <section className="pulse-band-section" data-testid="overview-pulse-band">
      <div className="pulse-band">
        <span className="pulse-band-label section-title">RUN PULSE</span>
        <PulseItem label="last event" tone={lastWarn ? "warn" : undefined} value={lastAge} />
        <PulseItem
          label="loop"
          tone={loopWarn ? "warn" : undefined}
          value={loopStatus ? `${loopStatus}${loopAge ? ` · ${loopAge}` : ""}` : null}
        />
        <span className="pulse-band-item" title={`per-bucket counts, ${Math.round(bucketSeconds / 60)}m buckets, old → new`}>
          <span className="muted">events/min</span>
          {perMinute == null ? (
            <strong className="mono pulse-no-data">—</strong>
          ) : !hasBucketEvents ? (
            <strong className="mono pulse-no-data">idle</strong>
          ) : (
            <>
              {eventRateLabel === "idle" ? null : <span className="mono pulse-sparkline">{sparkline(buckets)}</span>}
              <strong className="mono">{eventRateLabel}</strong>
            </>
          )}
        </span>
        <PulseItem
          hint={cooldown.length ? `cooldown: ${cooldown.join(", ")}` : undefined}
          label="respawn streak"
          tone={streak != null && streak >= RESPAWN_CRIT_STREAK ? "crit" : undefined}
          value={streak == null ? null : `${streak}${cooldown.length ? ` · ${cooldown.length} cooldown` : ""}`}
        />
        <PulseItem
          hint={sessHint}
          label="sessions"
          tone={sessStale > 0 ? "warn" : undefined}
          value={sessActive == null ? null : `${sessActive} active${sessStale > 0 ? ` · ${sessStale} stale` : ""}`}
        />
      </div>
    </section>
  );
}
