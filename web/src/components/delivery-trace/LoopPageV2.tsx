// Loop page v2 — timeline + business-loop cards over loop-view.v1.
// Design lineage: doc130 (stage-loop contract), doc131 §3.3/§8 (promise,
// spine), 2026-07 five-run dry-run (mock v12 + gen3 reference prototypes).
// Canonical Loop product view. Legacy BehaviorLoopPage was retired by design 150.
import { useEffect, useState } from "react";

import type { LoopViewLoop, RecentEvent } from "../../api/types";
import type { LiveState } from "../../app/sharedTypes";
import "../../styles/20-loop-v2.css";
import { LoopTimeline } from "./LoopTimeline";
import { timelineTaskKey } from "./loopTimelineModel";
import { useLoopViewResource } from "./useLoopViewResource";

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
  liveEvents?: RecentEvent[];
  liveState?: LiveState;
  projectId?: string;
  onOpenTrace?: (traceId: string) => void;
}

const hhmm = (iso: string) => (iso ? iso.slice(11, 16) : "");

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
  idle: TONE.muted,
};

export function LoopPageV2({ liveEvents = [], liveState = "live", projectId }: Props) {
  const { error, view } = useLoopViewResource({ liveEvents, liveState, projectId });
  const [openLoop, setOpenLoop] = useState<string>("");
  const [openTask, setOpenTask] = useState<string | null>(null);

  const jumpToTask = (taskId: string) => {
    const index = view?.tasks.findIndex((task) => task.id === taskId) ?? -1;
    if (index < 0) return;
    const key = timelineTaskKey(view!.tasks[index]);
    setOpenTask(key);
    document.getElementById(`loop-row-${encodeURIComponent(key)}`)?.scrollIntoView({ block: "center", behavior: "instant" });
  };
  useEffect(() => setOpenTask(null), [projectId, view?.run.run_id, view?.run.first_ts]);

  useEffect(() => {
    // archived run defaults to post-mortem: all cards expanded is too loud;
    // open the worst loop instead
    if (!view) return;
    const worst = Object.values(view.loops)
      .sort((a, b) => (a.arc.state === "broken" ? -1 : 1) - (b.arc.state === "broken" ? -1 : 1))[0];
    if (view.run.latched && worst) setOpenLoop(worst.id);
  }, [view]);

  if (error) {
    return <div className="loop-v2-page loop-v2-state tone-err">loop-view failed: {error}</div>;
  }
  if (!view) return <div className="loop-v2-page loop-v2-state">Loading loop view…</div>;

  const { run, loops, stages, faults, companions } = view;
  const backflows = view.backflows ?? [];

  const card: React.CSSProperties = {
    background: "var(--panel)", border: `1px solid ${TONE.line}`, borderRadius: 10,
    padding: "14px 16px", marginBottom: 16,
  };
  const h3: React.CSSProperties = {
    margin: 0, fontSize: "var(--text-13-5)", fontWeight: 600, letterSpacing: 0,
    color: "var(--text)",
  };
  const chip: React.CSSProperties = {
    display: "inline-flex", gap: 6, alignItems: "center", padding: "3px 10px",
    borderRadius: 999, border: `1px solid ${TONE.line}`, fontSize: 12,
    background: "var(--panel)", color: "var(--text)", cursor: "default",
  };

  return (
    <div
      className="loop-v2-page"
      data-live-event-count={liveEvents.length}
      data-live-event-type={liveEvents[0]?.type ?? ""}
      data-testid="loop-page-v2"
    >
      <div className="section-heading loop-v2-page-heading">
        <div>
          <h2>Loop</h2>
          <span className="muted">convergence, rework, and recovery</span>
        </div>
      </div>

      {view.scope.kind === "project_recovery" ? (
        <div style={card} data-testid="loop-project-recovery-scope">
          <div style={h3}>Project recovery</div>
          <div style={{ color: TONE.muted, fontSize: 12, marginTop: 6 }}>
            No business delivery run is active. Recovery and operator activity remain observable below.
          </div>
        </div>
      ) : null}

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
      <div className="loop-v2-main-loop" style={card}>
        <div style={h3}>Main loop
          <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
            {" "}· stage chain discovered from events · hover a node for its stage-loop card
          </span>
        </div>
        <div className="loop-v2-stage-rail" style={{ display: "flex", gap: 0, marginTop: 12, position: "relative" }}>
          <div className="loop-v2-stage-rail-line" style={{
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
          <div className="loop-v2-close-node" style={{ position: "relative", flex: 1, minWidth: 90, textAlign: "center", zIndex: 1 }} data-testid="loop-close-node">
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
          <div className="loop-v2-backflows" style={{ position: "relative", height: 30 + backflows.length * 20 }} data-testid="loop-backflows">
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

      <LoopTimeline key={`${projectId}:${run.run_id ?? run.first_ts}`} view={view} openTask={openTask} onOpenTask={setOpenTask} />

      {/* event -> subscriber (131 §8.1, collapsed) */}
      {(view.subscriber_chains ?? []).length ? (
        <details style={{ ...card }} data-testid="loop-subscriber">
          <summary style={{ ...h3, cursor: "pointer" }}>Event → Subscriber
            <span style={{ textTransform: "none", letterSpacing: 0, color: TONE.faint }}>
              {" "}· why the next stage woke up · derived from real sequences
            </span>
          </summary>
          <table className="loop-v2-subscriber-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 8 }}>
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
