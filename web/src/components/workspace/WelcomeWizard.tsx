// First-run welcome wizard (orca-modeled: progress rail, detect-prefill,
// skip, resume). Shown only when onboarding is not completed/skipped; the
// gate lives server-side (core/workspace/onboarding.py). STEP 3 reuses the
// existing New Project modal so "add project" is one flow, not two.
import { useEffect, useMemo, useState } from "react";

import { getOnboarding, inspectBootstrap, updateOnboarding } from "../../api/client";
import type { BootstrapInspect } from "../../api/client";
import type { OnboardingStatus } from "../../api/types";

const TONE = {
  ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)",
  text: "var(--text)", muted: "var(--muted-foreground, #667)",
  faint: "var(--text-tertiary, #889)", line: "var(--line)",
  brand: "var(--brand, #4477dd)", panel: "var(--panel)", bg: "var(--bg)",
};

interface StepDef { id: string; num: number; title: string; subtitle: string }
const STEPS: StepDef[] = [
  { id: "backend", num: 1, title: "选后端", subtitle: "哪个 AI 后端驱动 agent" },
  { id: "preflight", num: 2, title: "环境自检", subtitle: "启动硬依赖当场验" },
  { id: "project", num: 3, title: "第一个项目", subtitle: "描述 · 探测 · 创建" },
  { id: "notifications", num: 4, title: "通知", subtitle: "卡点时怎么提醒(可选)" },
  { id: "launch", num: 5, title: "就绪", subtitle: "启动第一轮" },
];

interface Props {
  hasProject: boolean;               // whether a project got created (STEP 3)
  onOpenProjectWizard: (prefill?: { root?: string; preset?: string }) => void;
  onDone: () => void;                // completed or skipped -> re-fetch + dismiss
}

export function WelcomeWizard({ hasProject, onOpenProjectWizard, onDone }: Props) {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [backend, setBackend] = useState("");
  const [notif, setNotif] = useState("none");
  const [busy, setBusy] = useState(false);
  const [inspectRoot, setInspectRoot] = useState("");
  const [inspect, setInspect] = useState<BootstrapInspect | null>(null);
  const [inspectBusy, setInspectBusy] = useState(false);

  useEffect(() => {
    getOnboarding().then((s) => {
      setStatus(s);
      setStepIdx(Math.min(Math.max((s.step || 1) - 1, 0), STEPS.length - 1));
      if (s.backend) setBackend(s.backend);
      else {
        const pre = s.backends.find((b) => b.detected && !b.always_available)
          ?? s.backends.find((b) => b.always_available);
        if (pre) setBackend(pre.id);
      }
    }).catch(() => onDone());
  }, [onDone]);

  const cur = STEPS[stepIdx];
  const preflightOk = useMemo(
    () => (status?.preflight ?? []).every((c) => c.ok), [status]);

  async function persistStep(nextIdx: number) {
    setStepIdx(nextIdx);
    void updateOnboarding({ action: "step", step: nextIdx + 1, backend });
  }
  async function finish() {
    setBusy(true);
    await updateOnboarding({ action: "complete", backend, notifications: notif });
    onDone();
  }
  async function skipAll() {
    setBusy(true);
    await updateOnboarding({ action: "skip" });
    onDone();
  }

  if (!status) {
    return <div style={overlay}><div style={{ color: TONE.muted }}>加载引导…</div></div>;
  }

  const canContinue =
    (cur.id === "backend" && !!backend)
    || (cur.id === "preflight" && preflightOk)
    || (cur.id === "project" && hasProject)
    || cur.id === "notifications"
    || cur.id === "launch";

  return (
    <div style={overlay} data-testid="welcome-wizard">
      <div style={card}>
        {/* header + progress rail */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <div style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: TONE.muted }}>
            设置 ZaoFu · STEP {cur.num}/5
          </div>
          <button type="button" onClick={skipAll} disabled={busy} data-testid="welcome-skip"
            style={linkBtn}>跳过全部</button>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", margin: "8px 0 18px" }}>
          {STEPS.map((s, i) => (
            <div key={s.id} data-testid={`welcome-rail-${s.id}`}
              onClick={() => i <= stepIdx && persistStep(i)}
              style={{
                flex: 1, height: 4, borderRadius: 2, cursor: i <= stepIdx ? "pointer" : "default",
                background: i < stepIdx ? TONE.ok : i === stepIdx ? TONE.brand : TONE.line,
              }} />
          ))}
        </div>
        <h2 style={{ margin: "0 0 2px", fontSize: 18 }}>{cur.title}</h2>
        <div style={{ color: TONE.muted, fontSize: 13, marginBottom: 16 }}>{cur.subtitle}</div>

        <div style={{ minHeight: 220 }}>
          {cur.id === "backend" ? (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {status.backends.map((b) => (
                <button key={b.id} type="button" data-testid={`welcome-backend-${b.id}`}
                  onClick={() => setBackend(b.id)}
                  style={{
                    textAlign: "left", padding: "12px 14px", borderRadius: 8, cursor: "pointer",
                    border: `1px solid ${backend === b.id ? TONE.brand : TONE.line}`,
                    background: backend === b.id ? "color-mix(in oklab, var(--brand) 8%, transparent)" : TONE.panel,
                    opacity: b.detected ? 1 : 0.5,
                  }}>
                  <div style={{ fontWeight: 600 }}>
                    {backend === b.id ? "● " : "○ "}{b.id}
                    {b.detected ? <span style={{ color: TONE.ok, fontSize: 11, marginLeft: 8 }}>✓ 已检测</span>
                      : <span style={{ color: TONE.faint, fontSize: 11, marginLeft: 8 }}>未检测</span>}
                  </div>
                  <div style={{ fontSize: 11, color: TONE.faint, fontFamily: "var(--font-mono, monospace)", marginTop: 3 }}>
                    {b.path || b.note || "稍后装"}
                  </div>
                </button>
              ))}
            </div>
          ) : null}

          {cur.id === "preflight" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {status.preflight.map((c) => (
                <div key={c.name} data-testid={`welcome-preflight-${c.name}`}
                  style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 13 }}>
                  <span style={{ color: c.ok ? TONE.ok : TONE.err, fontWeight: 700 }}>{c.ok ? "✓" : "✗"}</span>
                  <span style={{ fontWeight: 600 }}>{c.name}</span>
                  <span style={{ color: c.ok ? TONE.muted : TONE.warn, fontSize: 12 }}>
                    {c.detail || (c.ok ? "通过" : "缺失")}
                  </span>
                </div>
              ))}
              {!preflightOk ? (
                <div style={{ marginTop: 8, fontSize: 12, color: TONE.warn }}>
                  有硬依赖缺失,装好后 <button type="button" style={linkBtn}
                    onClick={() => getOnboarding().then(setStatus)}>重验</button> —— 通过才能继续。
                </div>
              ) : (
                <div style={{ marginTop: 8, fontSize: 12, color: TONE.ok }}>环境就绪。</div>
              )}
            </div>
          ) : null}

          {cur.id === "project" ? (
            <div>
              <div style={{ fontSize: 13, color: TONE.muted, marginBottom: 10 }}>
                ZaoFu 套在已有代码库上跑。先探测目标项目,自动产出 setup/门禁/文档候选。
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <input data-testid="welcome-inspect-root" value={inspectRoot}
                  onChange={(e) => setInspectRoot(e.target.value)}
                  placeholder="目标目录,如 ~/workspace/hermes-refactor/cangjie"
                  style={{
                    flex: 1, font: "inherit", fontSize: 13, padding: "7px 10px",
                    border: `1px solid ${TONE.line}`, borderRadius: 7, background: TONE.panel, color: TONE.text,
                  }} />
                <button type="button" data-testid="welcome-inspect-btn" disabled={!inspectRoot.trim() || inspectBusy}
                  onClick={async () => {
                    setInspectBusy(true);
                    try { setInspect(await inspectBootstrap(inspectRoot.trim(), backend || 'claude')); }
                    catch { setInspect(null); }
                    finally { setInspectBusy(false); }
                  }}
                  style={{ ...ghostBtn, opacity: inspectRoot.trim() ? 1 : 0.4 }}>
                  {inspectBusy ? "探测中…" : "探测项目"}
                </button>
              </div>

              {inspect ? (
                <div data-testid="welcome-candidates" style={{ marginBottom: 12 }}>
                  {inspect.confidence === "low" || !inspect.candidates.length ? (
                    <div style={{ fontSize: 12.5, color: TONE.warn }}>
                      置信度低(空/新仓)—— 代码落地后再探,先用空模板创建。
                    </div>
                  ) : (
                    <>
                      <div style={{ fontSize: 12, color: TONE.faint, marginBottom: 6 }}>
                        探到 <b style={{ color: TONE.text }}>{inspect.stack}</b> · {inspect.layout} · 候选(创建时以 apply_profile 写入):
                      </div>
                      {inspect.candidates.map((c) => (
                        <div key={c.kind} data-testid={`welcome-cand-${c.kind}`}
                          style={{ fontSize: 12.5, padding: "4px 0", borderTop: `1px solid ${TONE.line}` }}>
                          <span style={{ color: TONE.ok }}>☑ </span>
                          <b>{c.label}</b>{" "}
                          <span style={{ fontFamily: "var(--font-mono, monospace)", color: TONE.muted }}>
                            {c.value ?? (c.values ? c.values.join(" · ") : Object.entries(c.facts ?? {}).map(([k, v]) => `${k}=${v}`).join(" · "))}
                          </span>
                          <div style={{ fontSize: 11, color: TONE.faint }}>{c.note}</div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              ) : null}

              {hasProject ? (
                <div data-testid="welcome-project-done" style={{ color: TONE.ok, fontSize: 13 }}>✓ 项目已创建,可继续。</div>
              ) : (
                <button type="button" data-testid="welcome-add-project"
                  onClick={() => onOpenProjectWizard({
                    root: inspect?.root || inspectRoot.trim() || undefined,
                    preset: inspect?.recommended_flow || undefined,
                  })}
                  style={primaryBtn}>+ 用探测结果创建项目(controller flow)</button>
              )}
            </div>
          ) : null}

          {cur.id === "notifications" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 13, color: TONE.muted }}>长程跑到卡点/需你裁决时,怎么提醒?</div>
              {[["none", "不用(dashboard 里看)"], ["feishu", "Feishu webhook"], ["desktop", "桌面通知"]].map(([id, label]) => (
                <label key={id} data-testid={`welcome-notif-${id}`}
                  style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer", fontSize: 13 }}>
                  <input type="radio" name="notif" checked={notif === id} onChange={() => setNotif(id)} />
                  {label}
                </label>
              ))}
              <div style={{ fontSize: 12, color: TONE.faint }}>以后设置里可改。</div>
            </div>
          ) : null}

          {cur.id === "launch" ? (
            <div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, marginBottom: 16 }}>
                <span>✓ 后端 <b>{backend || "—"}</b></span>
                <span>{preflightOk ? "✓" : "✗"} 环境自检</span>
                <span>{hasProject ? "✓ 项目已建" : "○ 未建项目"}</span>
                <span>✓ 通知 {notif}</span>
              </div>
              <div style={{ fontSize: 13, color: TONE.muted, marginBottom: 8 }}>
                完成后进入 dashboard,带你看:🔁 Loop 环怎么转 · ▦ Board 任务 · 📥 Inbox 你要裁的。
              </div>
            </div>
          ) : null}
        </div>

        {/* footer nav */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 20, paddingTop: 14, borderTop: `1px solid ${TONE.line}` }}>
          <button type="button" disabled={stepIdx === 0 || busy}
            onClick={() => persistStep(stepIdx - 1)} style={ghostBtn}>← 上一步</button>
          <div style={{ flex: 1, textAlign: "center", fontSize: 12, color: TONE.faint }}>
            {cur.num} / 5
          </div>
          {cur.id !== "launch" && cur.id !== "project" ? (
            <button type="button" onClick={() => persistStep(stepIdx + 1)} style={linkBtn}>跳过此步</button>
          ) : null}
          {cur.id === "launch" ? (
            <button type="button" data-testid="welcome-finish" disabled={busy}
              onClick={finish} style={primaryBtn}>🚀 完成,进入 dashboard</button>
          ) : (
            <button type="button" data-testid="welcome-continue" disabled={!canContinue || busy}
              onClick={() => persistStep(stepIdx + 1)}
              style={{ ...primaryBtn, opacity: canContinue ? 1 : 0.4 }}>继续 →</button>
          )}
        </div>
      </div>
    </div>
  );
}

const overlay: React.CSSProperties = {
  position: "fixed", inset: 0, zIndex: 1000, background: "var(--bg)",
  display: "grid", placeItems: "center", padding: 24,
};
const card: React.CSSProperties = {
  width: "100%", maxWidth: 640, background: "var(--panel)",
  border: "1px solid var(--line)", borderRadius: 12, padding: "22px 26px",
  boxShadow: "0 20px 60px rgba(0,0,0,.25)",
};
const primaryBtn: React.CSSProperties = {
  font: "inherit", fontSize: 13, fontWeight: 600, padding: "8px 16px", borderRadius: 8,
  border: "1px solid var(--brand)", background: "var(--brand)", color: "oklch(1 0 0)", cursor: "pointer",
};
const ghostBtn: React.CSSProperties = {
  font: "inherit", fontSize: 13, padding: "7px 14px", borderRadius: 8,
  border: "1px solid var(--line)", background: "var(--panel)", color: "var(--text)", cursor: "pointer",
};
const linkBtn: React.CSSProperties = {
  font: "inherit", fontSize: 12, padding: "4px 8px", border: "none",
  background: "none", color: "var(--muted-foreground, #667)", cursor: "pointer", textDecoration: "underline",
};
