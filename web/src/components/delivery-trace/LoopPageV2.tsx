// Loop page v2 — timeline + business-loop cards over loop-view.v1.
// Design lineage: doc130 (stage-loop contract), doc131 §3.3/§8 (promise,
// spine), 2026-07 five-run dry-run (mock v12 + gen3 reference prototypes).
// Flag-gated (?v=2 / localStorage zf.loopV2); BehaviorLoopPage stays default.
import { useEffect, useMemo, useState } from "react";

import { getLoopView } from "../../api/client";
import type { LoopViewLoop, LoopViewProjection, LoopViewTask } from "../../api/types";

const GAP_BREAK_MS = 30 * 60 * 1000;
const BREAK_W = 4;

const TONE = {
  ok: "var(--ok)",
  warn: "var(--warn)",
  err: "var(--err)",
  muted: "var(--muted-foreground, #667)",
  faint: "var(--text-tertiary, #889)",
  line: "var(--line)",
  accent: "var(--brand, #4477dd)",
};

interface Props {
  projectId?: string;
  onOpenTrace?: (traceId: string) => void;
}

// ---------- time transform (gap compression; ported from gen3) ----------

interface Span { a: number; b: number; xa: number; xb: number }

function buildSpans(times: number[]): { spans: Span[]; breaks: Array<{ x: number; ms: number }> } {
  if (!times.length) return { spans: [], breaks: [] };
  const sorted = [...times].sort((p, q) => p - q);
  const windows: Array<[number, number]> = [];
  let cur: [number, number] = [sorted[0], sorted[0]];
  for (const t of sorted.slice(1)) {
    if (t - cur[1] > GAP_BREAK_MS) { windows.push(cur); cur = [t, t]; }
    else cur[1] = t;
  }
  windows.push(cur);
  const activeTotal = windows.reduce((s, w) => s + (w[1] - w[0]), 0) || 1;
  const avail = 100 - (windows.length - 1) * BREAK_W;
  const spans: Span[] = [];
  const breaks: Array<{ x: number; ms: number }> = [];
  let x = 0;
  windows.forEach((w, i) => {
    const wp = ((w[1] - w[0]) / activeTotal) * avail;
    spans.push({ a: w[0], b: w[1], xa: x, xb: x + wp });
    x += wp;
    if (i < windows.length - 1) {
      breaks.push({ x, ms: windows[i + 1][0] - w[1] });
      x += BREAK_W;
    }
  });
  return { spans, breaks };
}

function makeX(spans: Span[]): (t: number) => number {
  return (t: number) => {
    for (const s of spans) {
      if (t <= s.b + 1000) {
        if (t < s.a) return s.xa;
        return s.xa + ((t - s.a) / Math.max(s.b - s.a, 1)) * (s.xb - s.xa);
      }
    }
    return 100;
  };
}

const hhmm = (iso: string) => (iso ? iso.slice(11, 16) : "");
const ts = (iso: string) => (iso ? Date.parse(iso) : NaN);
function fmtDur(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s >= 3600) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m`;
  return `${s}s`;
}

// ---------- attempt segments with bundling (scale rule: no unreadable segs) ----------

interface Seg { l: number; r: number; state: "done" | "fail" | "open" | "uncounted"; title: string; bundle?: number }

function taskSegments(task: LoopViewTask, X: (t: number) => number, endT: number): Seg[] {
  const items: Seg[] = [];
  task.attempts.forEach((a, i) => {
    const start = ts(a.started_ts);
    if (!Number.isFinite(start)) return;
    const ref = a.terminal?.seq !== undefined ? ` · events.jsonl #${a.terminal.seq}` : ` · attempt ledger`;
    if (a.terminal) {
      const end = ts(a.terminal.ts);
      const fail = a.terminal.type.endsWith(".failed");
      items.push({
        l: X(start), r: Math.max(X(end), X(start) + 0.6),
        state: fail ? "fail" : "done",
        title: `attempt ${i + 1} · ${a.role} · ${hhmm(a.started_ts)}–${hhmm(a.terminal.ts)} · ${a.terminal.type}${ref}${a.terminal.reason ? ` · ${a.terminal.reason}` : ""}`,
      });
    } else if (!a.counted) {
      items.push({
        l: X(start), r: X(start) + 0.9, state: "uncounted",
        title: `attempt ${i + 1} · ${a.role} · OPEN superseded — uncounted (E5)${ref}`,
      });
    } else {
      items.push({
        l: X(start), r: Math.max(X(endT), X(start) + 1.2), state: "open",
        title: `attempt ${i + 1} · ${a.role} · OPEN since ${hhmm(a.started_ts)} · lease view${ref}`,
      });
    }
  });
  // bundle consecutive narrow items so nothing overlaps or truncates
  const MINW = 2.4, MAXGAP = 1.2, BUNDLE_MIN = 3;
  const out: Seg[] = [];
  let cluster: Seg[] = [];
  const flush = () => {
    if (!cluster.length) return;
    if (cluster.length >= BUNDLE_MIN) {
      const fails = cluster.filter((c) => c.state === "fail").length;
      out.push({
        l: cluster[0].l, r: Math.max(cluster[cluster.length - 1].r, cluster[0].l + 2.6),
        state: fails ? "fail" : "done", bundle: cluster.length,
        title: `bundle of ${cluster.length} attempts · ${fails} rejected · attempt ledger`,
      });
    } else out.push(...cluster);
    cluster = [];
  };
  for (const it of items) {
    const narrow = it.r - it.l < MINW;
    if (cluster.length && (it.l - cluster[cluster.length - 1].r > MAXGAP || !narrow)) flush();
    if (narrow) cluster.push(it); else out.push(it);
  }
  flush();
  // final clamp: acceptance is 0 overlapping segments
  out.sort((p, q) => p.l - q.l);
  for (let i = 1; i < out.length; i++) {
    if (out[i].l < out[i - 1].r) {
      out[i].l = out[i - 1].r + 0.1;
      out[i].r = Math.max(out[i].r, out[i].l + 0.5);
    }
  }
  return out;
}

const SEG_STYLE: Record<Seg["state"], React.CSSProperties> = {
  done: { background: "color-mix(in oklab, var(--ok) 12%, transparent)", border: `1px solid color-mix(in oklab, var(--ok) 45%, transparent)`, color: TONE.ok },
  fail: { background: "color-mix(in oklab, var(--err) 10%, transparent)", border: `1px solid color-mix(in oklab, var(--err) 45%, transparent)`, color: TONE.err },
  open: { background: "color-mix(in oklab, var(--brand) 10%, transparent)", border: `1px solid var(--brand)`, color: TONE.accent },
  uncounted: { background: "color-mix(in oklab, var(--muted) 8%, transparent)", border: `1px solid var(--line)`, color: TONE.muted },
};

// ---------- loop card chain SVG ----------

function LoopChainSvg({ loop }: { loop: LoopViewLoop }) {
  const NW = 104, NH = 38, GAP = 52, M = 12;
  const n = loop.shape.length;
  const w = M * 2 + n * NW + (n - 1) * GAP;
  const arcTone = loop.arc.state === "flow" ? TONE.ok : loop.arc.state === "active" ? TONE.warn : TONE.faint;
  const fromIdx = loop.shape.indexOf(loop.closure_edge[0]);
  const toIdx = loop.shape.indexOf(loop.closure_edge[1]);
  const xs = loop.shape.map((_, i) => M + i * (NW + GAP));
  const nodeStats = loop.node_stats ?? {};
  const subFor = (node: string) => {
    const stats = Object.entries(nodeStats[node] ?? {});
    return stats.length ? `${stats[0][0]} ${stats[0][1]}` : "";
  };
  return (
    <svg viewBox={`0 0 ${w} 132`} style={{ width: "100%", maxWidth: w, display: "block" }} aria-hidden="true">
      {loop.shape.map((node, i) => (
        <g key={node}>
          <rect x={xs[i]} y={22} width={NW} height={NH} rx={9}
            fill="var(--panel-2, var(--surface))" stroke={TONE.line} />
          <text x={xs[i] + NW / 2} y={40} textAnchor="middle" fill="var(--text)"
            fontSize={11.5} fontWeight={600}>{node}</text>
          {subFor(node) ? (
            <text x={xs[i] + NW / 2} y={53} textAnchor="middle" fill={TONE.muted}
              fontSize={9} fontFamily="var(--font-mono, monospace)">
              {subFor(node)}
            </text>
          ) : null}
          {i < n - 1 ? (
            <line x1={xs[i] + NW + 2} y1={22 + NH / 2} x2={xs[i + 1] - 3} y2={22 + NH / 2}
              stroke={TONE.faint} strokeWidth={1.4} />
          ) : null}
        </g>
      ))}
      {fromIdx >= 0 && toIdx >= 0 ? (
        <>
          <path
            d={`M ${xs[fromIdx] + NW / 2} ${22 + NH + 2} Q ${(xs[fromIdx] + xs[toIdx] + NW) / 2} 120 ${xs[toIdx] + NW / 2} ${22 + NH + 4}`}
            fill="none" stroke={arcTone} strokeWidth={1.8}
            strokeDasharray={loop.arc.state === "broken" ? "5 5" : undefined} />
          <text x={(xs[fromIdx] + xs[toIdx] + NW) / 2} y={112} textAnchor="middle"
            fill={arcTone} fontSize={10} fontFamily="var(--font-mono, monospace)">
            {loop.arc.label}
          </text>
        </>
      ) : null}
    </svg>
  );
}

function LoopChain({ loop }: { loop: LoopViewLoop }) {
  const NW = 104, GAP = 52, M = 12;
  const n = loop.shape.length;
  const w = M * 2 + n * NW + (n - 1) * GAP;
  const nodeStats = loop.node_stats ?? {};
  return (
    <div style={{ position: "relative", maxWidth: w }}>
      <LoopChainSvg loop={loop} />
      {loop.shape.map((node, i) => {
        const stats = Object.entries(nodeStats[node] ?? {});
        return (
          <span key={node} className="loopv2-node" data-testid={`loop-node-${node}`} style={{
            position: "absolute", left: `${((M + i * (NW + GAP)) / w) * 100}%`,
            width: `${(NW / w) * 100}%`, top: 0, height: 62, cursor: "default",
          }}>
            {stats.length ? (
              <span className="loopv2-nodecard" data-testid={`loop-node-hover-${node}`} style={{
                display: "none", position: "absolute", top: 64, left: "50%",
                transform: "translateX(-50%)", minWidth: 150, zIndex: 30,
                background: "var(--panel)", border: `1px solid ${TONE.line}`, borderRadius: 8,
                padding: "8px 10px", boxShadow: "0 10px 24px rgba(0,0,0,.16)",
                fontSize: 11.5, textAlign: "left", whiteSpace: "nowrap",
              }}>
                <b>{node}</b>
                {stats.map(([k, v]) => (
                  <span key={k} style={{ display: "flex", justifyContent: "space-between", gap: 12, color: TONE.muted, marginTop: 2 }}>
                    <span>{k}</span>
                    <span style={{ color: "var(--text)", fontFamily: "var(--font-mono, monospace)" }}>{v}</span>
                  </span>
                ))}
                <span style={{ display: "block", color: TONE.faint, fontSize: 10, marginTop: 4 }}>loop-view projection</span>
              </span>
            ) : null}
          </span>
        );
      })}
      <style>{`.loopv2-node:hover .loopv2-nodecard{display:block !important}`}</style>
    </div>
  );
}

// ---------- page ----------

const HEALTH_TONE: Record<string, string> = {
  closed: TONE.ok, converging: TONE.ok, diverging: TONE.warn, broken: TONE.err,
};

export function LoopPageV2({ projectId }: Props) {
  const [view, setView] = useState<LoopViewProjection | null>(null);
  const [error, setError] = useState<string>("");
  const [openLoop, setOpenLoop] = useState<string>("");
  const [openTask, setOpenTask] = useState<string>("");
  const jumpToTask = (taskId: string) => {
    setOpenTask(taskId);
    document.getElementById(`loop-row-${taskId}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  useEffect(() => {
    let cancelled = false;
    setView(null);
    setError("");
    getLoopView(projectId)
      .then((v) => { if (!cancelled) setView(v); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [projectId]);

  const timeline = useMemo(() => {
    if (!view) return null;
    const times: number[] = [];
    for (const task of view.tasks) {
      for (const a of task.attempts) {
        const t0 = ts(a.started_ts);
        if (Number.isFinite(t0)) times.push(t0);
        if (a.terminal) {
          const t1 = ts(a.terminal.ts);
          if (Number.isFinite(t1)) times.push(t1);
        }
      }
    }
    if (!times.length) return null;
    const { spans, breaks } = buildSpans(times);
    const X = makeX(spans);
    const endT = Math.max(...times);
    return { spans, breaks, X, endT };
  }, [view]);

  useEffect(() => {
    // archived run defaults to post-mortem: all cards expanded is too loud;
    // open the worst loop instead
    if (!view) return;
    const worst = Object.values(view.loops)
      .sort((a, b) => (a.arc.state === "broken" ? -1 : 1) - (b.arc.state === "broken" ? -1 : 1))[0];
    if (view.run.latched && worst) setOpenLoop(worst.id);
  }, [view]);

  if (error) return <div style={{ padding: 24, color: TONE.err }}>loop-view failed: {error}</div>;
  if (!view) return <div style={{ padding: 24, color: TONE.muted }}>Loading loop view…</div>;

  const { run, loops, stages, tasks, faults, companions, pump } = view;
  const backflows = view.backflows ?? [];
  const counters = view.health_counters;
  const counterLine = Object.entries(counters)
    .sort((a, b) => b[1] - a[1]).slice(0, 4)
    .map(([k, v]) => `${k} ${v}`).join(" · ");
  const brokenLoops = Object.values(loops).filter((l) => l.arc.state === "broken");

  const card: React.CSSProperties = {
    background: "var(--panel)", border: `1px solid ${TONE.line}`, borderRadius: 10,
    padding: "14px 18px", marginBottom: 12,
  };
  const h3: React.CSSProperties = {
    margin: 0, fontSize: 11, fontWeight: 600, letterSpacing: ".08em",
    textTransform: "uppercase", color: TONE.muted,
  };
  const chip: React.CSSProperties = {
    display: "inline-flex", gap: 6, alignItems: "center", padding: "3px 10px",
    borderRadius: 999, border: `1px solid ${TONE.line}`, fontSize: 12,
    background: "var(--panel)", color: "var(--text)", cursor: "default",
  };

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "16px 20px" }} data-testid="loop-page-v2">
      {/* hero — event-driven */}
      <div style={{
        ...card, display: "flex", gap: 14, alignItems: "center",
        borderColor: run.latched ? "color-mix(in oklab, var(--ok) 40%, transparent)" : "color-mix(in oklab, var(--warn) 45%, transparent)",
        background: run.latched
          ? "color-mix(in oklab, var(--ok) 5%, var(--panel))"
          : "color-mix(in oklab, var(--warn) 6%, var(--panel))",
      }} data-testid="loop-hero">
        <div style={{ fontSize: 18 }}>{run.latched ? "✓" : "⚠"}</div>
        <div>
          <div style={{ fontWeight: 650, fontSize: 14 }}>
            {run.latched
              ? `Run latched · promise ${run.promise.satisfied}/${run.promise.chain.length}`
              : `Run not latched · promise ${run.promise.satisfied}/${run.promise.chain.length}${brokenLoops.length ? ` · ${brokenLoops.length} broken loop${brokenLoops.length > 1 ? "s" : ""}` : ""}`}
          </div>
          <div style={{ fontSize: 12.5, color: TONE.muted, marginTop: 2 }}>
            {counterLine || `${run.semantic_event_count} semantic events`}
          </div>
        </div>
        {!run.latched && (counters["human.escalate"] ?? 0) > 0 ? (
          <button type="button" data-testid="loop-hero-inbox"
            onClick={() => {
              const q = new URLSearchParams(window.location.search);
              q.set("page", "inbox");
              q.delete("v");
              window.location.search = q.toString();
            }}
            style={{
              marginLeft: "auto", font: "inherit", fontSize: 12.5, fontWeight: 600,
              padding: "6px 14px", borderRadius: 7, cursor: "pointer",
              border: "1px solid var(--warn)", background: "var(--warn)",
              color: "oklch(1 0 0)", whiteSpace: "nowrap",
            }}>
            Open Inbox · {counters["human.escalate"]} escalations
          </button>
        ) : null}
      </div>

      {/* completion promise */}
      <div style={card} data-testid="loop-promise">
        <div style={h3}>Completion promise
          <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
            {" "}· {run.promise.source} · chain {run.promise.satisfied}/{run.promise.chain.length}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          {run.promise.chain.map((item) => (
            <span key={item.event} style={{
              ...chip,
              color: item.satisfied ? TONE.ok : TONE.warn,
              borderColor: item.satisfied ? TONE.line : "color-mix(in oklab, var(--warn) 50%, transparent)",
            }} title={item.ts || "outstanding"}>
              {item.satisfied ? "✓" : "✗"} {item.event}
              {item.seq !== undefined ? <span style={{ color: TONE.faint }}>#{item.seq}</span> : null}
            </span>
          ))}
        </div>
      </div>

      {/* main loop stepper with hover card */}
      <div style={card}>
        <div style={h3}>Main loop
          <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
            {" "}· stage chain discovered from events · hover a node for its stage-loop card
          </span>
        </div>
        <div style={{ display: "flex", gap: 0, marginTop: 12, position: "relative" }}>
          <div style={{
            position: "absolute", left: `${50 / (stages.length + 1)}%`,
            right: `${50 / (stages.length + 1)}%`, top: 15, height: 2,
            background: `linear-gradient(90deg, var(--ok) 0%, var(--ok) ${(stages.length - 1) / stages.length * 100}%, ${TONE.line} ${(stages.length - 1) / stages.length * 100}%)`,
            zIndex: 0,
          }} data-testid="loop-rail" />
          {stages.map((s) => (
            <div key={s.id} style={{ position: "relative", flex: 1, minWidth: 90, textAlign: "center", zIndex: 1 }}
              className="loopv2-stage" data-testid="loop-stage">
              <div style={{
                width: 30, height: 30, borderRadius: "50%", margin: "0 auto",
                display: "grid", placeItems: "center", fontSize: 10, fontWeight: 700,
                fontFamily: "var(--font-mono, monospace)", background: "var(--panel)",
                border: `2px solid ${s.warn ? TONE.warn : TONE.ok}`,
                color: s.warn ? TONE.warn : TONE.ok,
              }}>{s.rounds}r</div>
              {/* 长阶段名(assignment_correction / impl_exit_gate)会溢出 90px
                  节点格、压到相邻标签("ASSIGNMENT_CORRDIAGNOSIS")—— 截断 + title
                  hover 看全名(2026-07-13 cangjie-r14 实测叠加)。 */}
              <div style={{ fontSize: 11.5, fontWeight: 650, marginTop: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={s.id.split("-").slice(-1)[0].toUpperCase()}>
                {s.id.split("-").slice(-1)[0].toUpperCase()}
              </div>
              <div style={{ fontSize: 10.5, color: s.warn ? TONE.warn : TONE.muted, fontFamily: "var(--font-mono, monospace)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.rounds} rounds{s.last_status ? ` · ${s.last_status.split(".").pop()}` : ""}
              </div>
              <div className="loopv2-stagecard" style={{
                display: "none", position: "absolute", top: 62, left: "50%",
                transform: "translateX(-50%)", width: 230, zIndex: 30, textAlign: "left",
                background: "var(--panel)", border: `1px solid ${TONE.line}`, borderRadius: 8,
                padding: "9px 11px", boxShadow: "0 10px 24px rgba(0,0,0,.16)", fontSize: 11.5,
              }}>
                <b>{s.id}</b>
                <div style={{ color: TONE.muted }}>rounds {s.rounds} · {s.last_status || "active"}</div>
                <div style={{ color: TONE.faint }}>last activity {hhmm(s.last_ts)} UTC</div>
                <div style={{ color: TONE.faint, marginTop: 4, fontSize: 10 }}>summary of timeline data</div>
              </div>
            </div>
          ))}
          <div style={{ position: "relative", flex: 1, minWidth: 90, textAlign: "center", zIndex: 1 }} data-testid="loop-close-node">
            <div style={{
              width: 30, height: 30, borderRadius: "50%", margin: "0 auto",
              display: "grid", placeItems: "center", fontSize: 12, background: "var(--panel)",
              border: `2px ${run.latched ? "solid" : "dashed"} ${run.latched ? TONE.ok : TONE.line}`,
              color: run.latched ? TONE.ok : TONE.faint,
            }}>{run.latched ? "✓" : ""}</div>
            <div style={{ fontSize: 11.5, fontWeight: 650, marginTop: 4, color: run.latched ? "var(--text)" : TONE.faint }}>CLOSE</div>
            <div style={{ fontSize: 10.5, color: run.latched ? TONE.ok : TONE.faint, fontFamily: "var(--font-mono, monospace)" }}>
              {run.latched ? "run.completed" : "not latched"}
            </div>
          </div>
        </div>
        <style>{`.loopv2-stage:hover .loopv2-stagecard{display:block !important}`}</style>
        {backflows.length ? (
          <div style={{ position: "relative", height: 30 + backflows.length * 20 }} data-testid="loop-backflows">
            <svg viewBox="0 0 1000 100" preserveAspectRatio="none"
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} aria-hidden="true">
              {backflows.map((bf, bi) => {
                const idx = (id: string) => stages.findIndex((st) => st.id === id);
                const n = stages.length || 1;
                const fi = idx(bf.from_stage), ti = idx(bf.to_stage);
                if (fi < 0 || ti < 0) return null;
                const x1 = ((fi + 0.5) / n) * 1000, x2 = ((ti + 0.5) / n) * 1000;
                const tone = bf.kind === "replan" ? TONE.accent : TONE.warn;
                return (
                  <path key={bi} d={`M ${x1} 4 Q ${(x1 + x2) / 2} ${46 + bi * 22} ${x2} 6`}
                    fill="none" stroke={tone} strokeWidth={1.6}
                    strokeDasharray={bf.kind === "replan" ? "5 5" : undefined}
                    vectorEffect="non-scaling-stroke" />
                );
              })}
            </svg>
            {backflows.map((bf, bi) => {
              const idx = (id: string) => stages.findIndex((st) => st.id === id);
              const n = stages.length || 1;
              const fi = idx(bf.from_stage), ti = idx(bf.to_stage);
              if (fi < 0 || ti < 0) return null;
              const mid = (((fi + 0.5) / n) + ((ti + 0.5) / n)) / 2 * 100;
              return (
                <span key={bi} style={{
                  position: "absolute", left: `${mid}%`, top: 8 + bi * 20,
                  transform: "translateX(-50%)", fontSize: 10.5,
                  fontFamily: "var(--font-mono, monospace)", padding: "1px 8px",
                  borderRadius: 999, background: "var(--panel)",
                  border: `1px solid ${bf.kind === "replan" ? TONE.accent : TONE.warn}`,
                  color: bf.kind === "replan" ? TONE.accent : TONE.warn, whiteSpace: "nowrap",
                }} title={`${bf.from_stage} → ${bf.to_stage} · ${bf.kind} backflow, event-paired`}>
                  ↺ {bf.kind} ×{bf.count}
                </span>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* business loops: chips + accordion cards */}
      <div style={card}>
        <div style={h3}>Business loops
          <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
            {" "}· zero-state: absent families render no chip · click = expand card
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          {Object.values(loops).map((loop) => (
            <span key={loop.id} className="loopv2-bchip" style={{ position: "relative" }}>
              <button type="button" data-testid={`loop-chip-${loop.id}`}
                onClick={() => setOpenLoop(openLoop === loop.id ? "" : loop.id)}
                style={{
                  ...chip, cursor: "pointer",
                  borderColor: openLoop === loop.id ? TONE.accent
                    : loop.arc.state === "broken" ? "color-mix(in oklab, var(--err) 50%, transparent)"
                    : loop.health === "diverging" ? "color-mix(in oklab, var(--warn) 55%, transparent)"
                    : TONE.line,
                }}>
                {loop.label}
                <span style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)", color: HEALTH_TONE[loop.health] || TONE.muted }}>
                  {loop.arc.state === "broken" ? "broken" : loop.health}
                </span>
              </button>
              <span className="loopv2-bcard" data-testid={`loop-hover-${loop.id}`} style={{
                display: "none", position: "absolute", top: "calc(100% + 6px)", left: 0,
                width: 250, zIndex: 30, background: "var(--panel)",
                border: `1px solid ${TONE.line}`, borderRadius: 8, padding: "9px 11px",
                boxShadow: "0 10px 24px rgba(0,0,0,.16)", fontSize: 11.5, textAlign: "left",
              }}>
                <span style={{ display: "flex", justifyContent: "space-between" }}>
                  <b>{loop.label}</b>
                  <span style={{ color: HEALTH_TONE[loop.health] || TONE.muted, fontFamily: "var(--font-mono, monospace)", fontSize: 10.5 }}>
                    {loop.health}
                  </span>
                </span>
                <span style={{ display: "block", color: loop.arc.state === "broken" ? TONE.err : loop.arc.state === "active" ? TONE.warn : TONE.ok, fontFamily: "var(--font-mono, monospace)", fontSize: 10.5, marginTop: 3 }}>
                  arc {loop.arc.state} · {loop.arc.label}
                </span>
                {Object.entries(loop.counts).slice(0, 4).map(([k, v]) => (
                  <span key={k} style={{ display: "flex", justifyContent: "space-between", color: TONE.muted, marginTop: 2 }}>
                    <span>{k}</span><span style={{ color: "var(--text)", fontFamily: "var(--font-mono, monospace)" }}>{v}</span>
                  </span>
                ))}
                {loop.acct ? (
                  <span style={{ display: "flex", justifyContent: "space-between", color: TONE.muted, marginTop: 2 }}>
                    <span>open/recovered/exhausted</span>
                    <span style={{ color: "var(--text)", fontFamily: "var(--font-mono, monospace)" }}>
                      {loop.acct.open}/{loop.acct.recovered}/{loop.acct.exhausted}
                    </span>
                  </span>
                ) : null}
                <span style={{ display: "block", color: TONE.faint, fontSize: 10, marginTop: 5 }}>
                  summary of loop-view projection · click chip → full card
                </span>
              </span>
            </span>
          ))}
          <style>{`.loopv2-bchip:hover .loopv2-bcard{display:block !important}`}</style>
          {faults.map((f) => (
            <button key={f.kind} type="button" data-testid="fault-chip"
              onClick={() => loops[f.owner_loop] && setOpenLoop(f.owner_loop)}
              title={`owner loop: ${f.owner_loop} — click to open its card`}
              style={{
                ...chip, cursor: loops[f.owner_loop] ? "pointer" : "default", borderStyle: "dashed",
                color: f.kind === "human.escalate" || f.kind === "runtime.watcher.lag_warning" ? TONE.warn : TONE.err,
              }}>
              {f.kind.split(".").slice(-1)[0]} {f.count}
            </button>
          ))}
        </div>
        {openLoop && loops[openLoop] ? (
          <div style={{ marginTop: 12, borderTop: `1px dashed ${TONE.line}`, paddingTop: 12 }}
            data-testid={`loop-card-${openLoop}`}>
            <LoopChain loop={loops[openLoop]} />
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11.5, color: TONE.muted, marginTop: 4 }}>
              {Object.entries(loops[openLoop].counts).map(([k, v]) => (
                <span key={k}>{k}: <b style={{ color: "var(--text)" }}>{v}</b></span>
              ))}
              {(loops[openLoop].members ?? []).length ? (
                <span style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  members:
                  {(loops[openLoop].members ?? []).map((m) => (
                    <button key={m.id} type="button" data-testid="loop-member"
                      onClick={() => jumpToTask(m.id)}
                      title="click → jump to its timeline row and open the attempt projection"
                      style={{
                        font: "inherit", fontSize: 11, padding: "1px 8px", borderRadius: 999,
                        border: `1px solid ${TONE.line}`, background: "var(--panel)",
                        color: "var(--text)", cursor: "pointer",
                      }}>
                      {m.id} <span style={{ color: TONE.muted, fontFamily: "var(--font-mono, monospace)" }}>{m.note}</span>
                    </button>
                  ))}
                </span>
              ) : null}
              {loops[openLoop].acct ? (
                <span>open/recovered/exhausted:{" "}
                  <b style={{ color: "var(--text)" }}>
                    {loops[openLoop].acct!.open} / {loops[openLoop].acct!.recovered} / {loops[openLoop].acct!.exhausted}
                  </b>
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* attempt timeline */}
      <div style={card}>
        <div style={h3}>Loop timeline
          <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
            {" "}· one row per task · one segment per counted attempt · idle gaps compressed
            {tasks[0]?.source === "task_attempts.json" ? " · source: attempt ledger (spine)" : " · source: events"}
          </span>
        </div>
        {timeline ? (
          <div style={{ marginTop: 10 }}>
            <div style={{ position: "relative", height: 14, fontSize: 10, color: TONE.faint, fontFamily: "var(--font-mono, monospace)" }}>
              {/* gap 压缩后刻度挤在右端、和右对齐的 now/end 标签叠成一坨
                  (2026-07-13 cangjie-r14 实测)。按最小间距去重 + 右端留白给
                  now 标签,只渲染读得清的一组。 */}
              {(() => {
                const MIN_GAP = 6;        // % — 相邻刻度最小间距(标签 ~30px/轴 ~1000px)
                const RIGHT_RESERVE = 12; // % — 右端留给 now/end 标签
                let lastX = -Infinity;
                return timeline.spans
                  .filter((s) => s.xa <= 100 - RIGHT_RESERVE)
                  .filter((s) => { if (s.xa - lastX >= MIN_GAP) { lastX = s.xa; return true; } return false; })
                  .map((s) => (
                    <span key={s.xa} style={{ position: "absolute", left: `${s.xa}%`, whiteSpace: "nowrap" }}>
                      {new Date(s.a).toISOString().slice(11, 16)}
                    </span>
                  ));
              })()}
              <span style={{ position: "absolute", right: 0, whiteSpace: "nowrap" }}>
                {run.latched ? "end" : "now"} {new Date(timeline.endT).toISOString().slice(11, 16)} UTC
              </span>
            </div>
            <div style={{ position: "relative" }}>
              {timeline.breaks.map((b) => (
                <div key={b.x} title={`idle ${fmtDur(b.ms)} (compressed)`} style={{
                  position: "absolute", top: 0, bottom: 0, left: `${b.x}%`, width: `${BREAK_W}%`,
                  background: "repeating-linear-gradient(90deg, color-mix(in oklab, var(--muted) 12%, transparent) 0 6px, transparent 6px 12px)",
                }} />
              ))}
              {tasks.map((task) => {
                const segs = taskSegments(task, timeline.X, timeline.endT);
                const uncounted = task.attempts.length - task.counted;
                const opened = openTask === task.id;
                return (
                  <div key={task.id} id={`loop-row-${task.id}`} data-testid="loop-task-row"
                    onClick={() => setOpenTask(opened ? "" : task.id)}
                    style={{
                      display: "grid", gridTemplateColumns: "230px 1fr", gap: 12, alignItems: "center",
                      padding: "4px 0", cursor: "pointer", borderRadius: 6,
                      background: opened ? "color-mix(in oklab, var(--brand) 5%, transparent)" : undefined,
                    }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {task.id}
                        <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 10.5, color: TONE.muted, marginLeft: 6 }}>
                          {task.counted} att · {task.fails}✗{uncounted ? ` · ${uncounted} uncounted` : ""}
                        </span>
                      </div>
                    </div>
                    <div style={{ position: "relative", height: 22 }}>
                      {segs.map((seg, i) => (
                        <span key={i} title={seg.title} data-testid="loop-seg" style={{
                          position: "absolute", left: `${seg.l}%`, width: `${Math.max(seg.r - seg.l, 0.6)}%`,
                          top: 2, bottom: 2, borderRadius: 4, fontSize: 9.5, overflow: "hidden",
                          fontFamily: "var(--font-mono, monospace)", display: "flex", alignItems: "center",
                          paddingLeft: 4, whiteSpace: "nowrap",
                          ...SEG_STYLE[seg.state],
                        }}>
                          {seg.bundle ? `×${seg.bundle}` : seg.r - seg.l > 3.5 ? (seg.state === "open" ? "open" : "") : ""}
                        </span>
                      ))}
                    </div>
                    {opened ? (
                      <div data-testid="loop-attempt-drawer" onClick={(e) => e.stopPropagation()}
                        style={{
                          gridColumn: "1 / -1", cursor: "default", margin: "2px 0 6px",
                          border: `1px solid ${TONE.line}`, borderRadius: 8, padding: "8px 12px",
                          background: "color-mix(in oklab, var(--muted-foreground) 3%, transparent)",
                          maxHeight: 260, overflowY: "auto",
                        }}>
                        <div style={{ fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase", color: TONE.muted, marginBottom: 4 }}>
                          attempt projection · {task.source} · verbatim, read-only
                        </div>
                        {task.attempts.map((a, ai) => {
                          const fail = a.terminal?.type.endsWith(".failed");
                          return (
                            <div key={ai} style={{
                              display: "grid", gridTemplateColumns: "56px 110px 1fr", gap: 10,
                              padding: "3px 0", fontSize: 11.5,
                              borderTop: ai ? `1px solid ${TONE.line}` : undefined,
                              opacity: a.counted ? 1 : 0.55,
                            }}>
                              <span style={{ fontFamily: "var(--font-mono, monospace)", color: TONE.faint }}>
                                #{ai + 1}{a.counted ? "" : " u"}{a.orphan ? " ?" : ""}</span>
                              <span style={{ color: TONE.muted }}>{a.role || "—"}</span>
                              <span style={{ color: fail ? TONE.err : a.open ? TONE.accent : "var(--text)" }}>
                                {hhmm(a.started_ts)}{a.terminal ? `–${hhmm(a.terminal.ts)} · ${a.terminal.type}` : " · OPEN"}
                                {a.terminal?.reason ? <span style={{ color: TONE.muted }}> · {a.terminal.reason}</span> : null}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 10.5, color: TONE.faint, marginTop: 8 }}>
              <span>green = completed · red = rejected · blue = open (lease view) · grey = superseded/uncounted (E5)</span>
              <span>×N = bundled attempt churn</span>
              <span>pump: {pump.total} ticks not rendered (mechanical loop, outside the census){pump.lag_warnings ? ` · lag ⚠×${pump.lag_warnings}` : ""}</span>
            </div>
          </div>
        ) : (
          <div style={{ marginTop: 10, fontSize: 12.5, color: TONE.muted }}>
            No attempts recorded yet — the loop timeline appears with the first dispatch.
          </div>
        )}
      </div>

      {/* event -> subscriber (131 §8.1, collapsed) */}
      {(view.subscriber_chains ?? []).length ? (
        <details style={{ ...card }} data-testid="loop-subscriber">
          <summary style={{ ...h3, cursor: "pointer" }}>Event → Subscriber
            <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
              {" "}· why the next stage woke up · derived from real sequences
            </span>
          </summary>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 8 }}>
            <thead>
              <tr style={{ color: TONE.faint, textAlign: "left", fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase" }}>
                <th style={{ padding: "3px 8px" }}>event topic</th>
                <th style={{ padding: "3px 8px" }}>subscriber</th>
                <th style={{ padding: "3px 8px" }}>result</th>
              </tr>
            </thead>
            <tbody>
              {(view.subscriber_chains ?? []).map((c) => (
                <tr key={c.topic} style={{ borderTop: `1px solid ${TONE.line}` }}>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono, monospace)" }}>
                    {c.topic} <span style={{ color: TONE.faint }}>#{c.seq}</span></td>
                  <td style={{ padding: "4px 8px" }}>{c.subscriber}</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono, monospace)" }}>
                    {c.result} <span style={{ color: TONE.faint }}>#{c.result_seq}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}

      {/* companions (zero-state absent) */}
      {Object.keys(companions).length ? (
        <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span style={h3}>Companions</span>
          {companions.learning ? (
            <span style={chip}>learning · reflect ×{companions.learning.reflections}</span>
          ) : null}
          {companions.repair ? (
            <span style={chip}>repair · post-verify {companions.repair.post_verified}✓{companions.repair.blocked ? ` · ${companions.repair.blocked} blocked` : ""}</span>
          ) : null}
          {companions.human ? (
            <span style={{
              ...chip,
              borderColor: companions.human.escalations ? "color-mix(in oklab, var(--warn) 55%, transparent)" : TONE.line,
            }}>human · {companions.human.signals} signals{companions.human.escalations ? ` · escalate ×${companions.human.escalations}` : ""}</span>
          ) : null}
          {companions.lease ? (
            <span style={chip}>lease/retry (E5) ×{companions.lease.retry_scheduled}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
