import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
// First-run host setup. Project admission starts after this wizard.
import { useEffect, useMemo, useState } from "react";

import { getOnboarding, updateOnboarding } from "../../api/client";
import type { OnboardingStatus } from "../../api/types";

const TONE = {
  ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)",
  text: "var(--text)", muted: "var(--muted-foreground, #667)",
  faint: "var(--text-tertiary, #889)", line: "var(--line)",
  brand: "var(--brand, #4477dd)", panel: "var(--panel)", bg: "var(--bg)",
};

interface StepDef { id: string; num: number; title: string; subtitle: string }
const STEPS: StepDef[] = [
  { id: "backend", num: 1, title: "Provider", subtitle: "选择本机可用的 Coding Agent provider" },
  { id: "preflight", num: 2, title: "Environment", subtitle: "核实 ZaoFu 的宿主环境" },
  { id: "access", num: 3, title: "Access", subtitle: "授权当前浏览器执行受控操作" },
  { id: "ready", num: 4, title: "Ready", subtitle: "Workspace 已可使用" },
];

interface Props {
  accessReady: boolean;
  onDone: () => void;
  onSaveToken: (token: string) => void;
  tokenPresent: boolean;
}

export function WelcomeWizard({
  accessReady,
  onDone,
  onSaveToken,
  tokenPresent,
}: Props) {
  useSettingsPreferences();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [backend, setBackend] = useState("");
  const [mixedEnabled, setMixedEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tokenDraft, setTokenDraft] = useState("");

  useEffect(() => {
    getOnboarding().then((s) => {
      setStatus(s);
      setStepIdx(Math.min(Math.max((s.step || 1) - 1, 0), STEPS.length - 1));
      if (s.primary_backend || s.backend) setBackend(s.primary_backend || s.backend);
      else {
        const primaryBackends = s.backends.filter(
          (item) => item.id === "codex" || item.id === "claude-code",
        );
        const pre = primaryBackends.find((b) => b.detected)
          ?? primaryBackends[0];
        if (pre) setBackend(pre.id);
      }
      setMixedEnabled(Boolean(s.mixed_enabled && s.mixed_available));
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const cur = STEPS[stepIdx];
  const preflightOk = useMemo(
    () => (status?.preflight ?? []).every((c) => c.ok), [status]);

  async function persistStep(nextIdx: number) {
    if (!accessReady) {
      setStepIdx(nextIdx);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await updateOnboarding({
        action: "step",
        step: nextIdx + 1,
        primary_backend: backend,
        mixed_enabled: mixedEnabled,
      });
      setStepIdx(nextIdx);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function finish() {
    setBusy(true);
    setError("");
    try {
      await updateOnboarding({
        action: "complete",
        primary_backend: backend,
        mixed_enabled: mixedEnabled,
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function skipAll() {
    setBusy(true);
    setError("");
    try {
      await updateOnboarding({ action: "skip" });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!status) {
    return (
      <div style={overlay}>
        <div style={{ color: error ? TONE.err : TONE.muted }}>
          {error || "加载引导…"}
        </div>
      </div>
    );
  }

  const canContinue =
    (cur.id === "backend" && !!backend)
    || (cur.id === "preflight" && preflightOk)
    || (cur.id === "access" && accessReady)
    || cur.id === "ready";

  return (
    <div style={overlay} data-testid="welcome-wizard">
      <div style={card}>
        {/* header + progress rail */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <div style={{ fontSize: 11, letterSpacing: 0, textTransform: "uppercase", color: TONE.muted }}>
            设置 ZaoFu · STEP {cur.num}/{STEPS.length}
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
        {error ? (
          <div
            role="alert"
            data-testid="welcome-error"
            style={{ color: TONE.err, fontSize: 12.5, margin: "-6px 0 12px" }}
          >
            {error}
          </div>
        ) : null}

        <div style={{ minHeight: 220 }}>
          {cur.id === "backend" ? (
            <div style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {status.backends.filter(
                  (item) => item.id === "codex" || item.id === "claude-code",
                ).map((b) => (
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
              <label style={{
                display: "grid",
                gridTemplateColumns: "auto minmax(0, 1fr)",
                gap: 10,
                alignItems: "start",
                padding: "11px 12px",
                border: `1px solid ${TONE.line}`,
                borderRadius: 8,
                color: status.mixed_available ? TONE.text : TONE.faint,
              }}>
                <input
                  checked={mixedEnabled}
                  data-testid="welcome-mixed-enabled"
                  disabled={!status.mixed_available}
                  type="checkbox"
                  onChange={(event) => setMixedEnabled(event.target.checked)}
                />
                <span>
                  <strong style={{ display: "block", fontSize: 13 }}>{uiText("Mixed team")}</strong>
                  <span style={{ display: "block", marginTop: 2, fontSize: 11, color: TONE.faint }}>
                    {status.mixed_available
                      ? "独立 verify lane 使用另一 provider"
                      : "需要同时检测到 Codex 和 Claude Code"}
                  </span>
                </span>
              </label>
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

          {cur.id === "access" ? (
            <div>
              <div
                data-testid="welcome-action-token"
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto",
                  gap: 8,
                  alignItems: "center",
                  padding: 12,
                  border: `1px solid ${accessReady ? TONE.ok : TONE.line}`,
                  borderRadius: 8,
                  background: TONE.bg,
                }}
              >
                <input
                  aria-label="Web action token"
                  type="password"
                  value={tokenDraft}
                  onChange={(event) => setTokenDraft(event.target.value)}
                  placeholder={tokenPresent ? "替换 Web action token" : "输入 Web action token"}
                  style={{
                    minWidth: 0,
                    font: "inherit",
                    fontSize: 12.5,
                    padding: "7px 9px",
                    border: `1px solid ${TONE.line}`,
                    borderRadius: 6,
                    background: TONE.panel,
                    color: TONE.text,
                  }}
                />
                <button
                  type="button"
                  disabled={!tokenDraft.trim()}
                  onClick={() => {
                    onSaveToken(tokenDraft);
                    setTokenDraft("");
                    setError("");
                  }}
                  style={ghostBtn}
                >
                  {tokenPresent ? "替换 token" : "保存 token"}
                </button>
              </div>
              <div
                data-testid="welcome-access-status"
                style={{ marginTop: 12, fontSize: 13, color: accessReady ? TONE.ok : TONE.warn }}
              >
                {accessReady ? "当前浏览器已授权。" : "需要授权后才能完成设置。"}
              </div>
            </div>
          ) : null}

          {cur.id === "ready" ? (
            <div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, marginBottom: 16 }}>
                <span>{uiText("Provider")} <b>{backend || "未指定"}</b></span>
                <span>{uiText("Team")} <b>{mixedEnabled ? "mixed" : "single"}</b></span>
                <span>Environment {preflightOk ? "ready" : "skipped"}</span>
                <span>{uiText("Access")} {accessReady ? "granted" : "required"}</span>
              </div>
              <div style={{ fontSize: 13, color: TONE.muted, marginBottom: 8 }}>
                完成后进入 Workspace。Project 可在需要时通过 Add Project 打开或初始化。
              </div>
            </div>
          ) : null}
        </div>

        {/* footer nav */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 20, paddingTop: 14, borderTop: `1px solid ${TONE.line}` }}>
          <button type="button" disabled={stepIdx === 0 || busy}
            onClick={() => persistStep(stepIdx - 1)} style={ghostBtn}>← 上一步</button>
          <div style={{ flex: 1, textAlign: "center", fontSize: 12, color: TONE.faint }}>
            {cur.num} / {STEPS.length}
          </div>
          {cur.id !== "ready" && cur.id !== "access" ? (
            <button type="button" onClick={() => persistStep(stepIdx + 1)} style={linkBtn}>跳过此步</button>
          ) : null}
          {cur.id === "ready" ? (
            <button type="button" data-testid="welcome-finish" disabled={busy}
              onClick={finish} style={primaryBtn}>进入 Workspace</button>
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
  display: "grid", placeItems: "center", padding: 16, overflow: "hidden",
};
const card: React.CSSProperties = {
  boxSizing: "border-box", width: "100%", maxWidth: 640,
  maxHeight: "calc(100dvh - 32px)", overflowY: "auto", background: "var(--panel)",
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
