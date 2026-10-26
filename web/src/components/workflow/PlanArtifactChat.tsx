import { useEffect, useRef, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { reviewPlanArtifact } from "../../api/client";
import { previewQuote } from "./workflowReviewModel";
import { MarkdownText } from "../agent-session/MarkdownText";

export default function PlanArtifactChat({ preview, projectId, disabled, onBusy }: {
  preview: PlanPreview; projectId: string; disabled: boolean; onBusy: (busy: boolean) => void;
}) {
  const [selection, setName] = useState("accepted_plan");
  const name = preview.artifacts?.some(a => a.name === selection) ? selection
    : preview.artifacts?.find(a => a.name === "accepted_plan")?.name || preview.artifacts?.[0]?.name || "";
  const [context, setContext] = useState<{ body: string; digest: string; editable: boolean } | null>(null);
  const [quote, setQuote] = useState<{ text: string; start: number; end: number } | null>(null);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const epoch = useRef(0);
  const draft = result?.draft_ref as Record<string, unknown> | undefined;
  useEffect(() => {
    const generation = ++epoch.current;
    setContext(null); setQuote(null); setResult(null); setError(""); setWorking(false); onBusy(false);
    if (!disabled && name) void reviewPlanArtifact(projectId, { operation: "context", plan_id: preview.plan_id, name }).then(value => {
      if (generation !== epoch.current) return;
      if (!value.ok) { setError(String(value.reason)); return; }
      setContext(value as unknown as NonNullable<typeof context>);
    }).catch(reason => { if (generation === epoch.current) setError(String(reason)); });
    return () => { epoch.current++; onBusy(false); };
  }, [projectId, preview.plan_id, preview.status, name, disabled]);
  function capture() {
    if (working || draft) return;
    const selected = previewQuote(window.getSelection(), root.current);
    if (selected) setQuote({ text: selected.text, start: selected.start, end: selected.end });
  }
  async function send(mode: "discuss" | "revise") {
    if (!context || !quote || !message.trim()) return;
    const generation = epoch.current;
    setWorking(true); onBusy(true); setError("");
    let hold = false;
    try {
      const value = await reviewPlanArtifact(projectId, { operation: "discuss", mode, plan_id: preview.plan_id,
        name, digest: context.digest, quote, message });
      if (generation !== epoch.current) return;
      if (!value.ok) setError(String(value.reason)); else { setResult(value); hold = !!value.draft_ref; }
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) { setWorking(false); onBusy(hold); } }
  }
  async function apply() {
    const generation = epoch.current;
    setWorking(true); onBusy(true); setError("");
    try {
      const value = await reviewPlanArtifact(projectId, { operation: "apply", draft_ref: draft });
      if (generation !== epoch.current) return;
      if (!value.ok) setError(String(value.reason));
      else { setResult({ answer: "已提交局部修订，等待 Planner 生成新版 PRD / Plan 预览。", preview: value.candidate_preview }); onBusy(false); }
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) setWorking(false); }
  }
  return <section className="workflow-clarification" aria-label="PRD Plan Agent Chat">
    <h3>PRD / Plan · Agent Chat</h3>
    <label>审阅文档 <select aria-label="Plan review artifact" value={name} disabled={working || !!draft} onChange={e => setName(e.target.value)}>
      {preview.artifacts?.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
    </select></label>
    <p>在原文中选择要讨论或修改的片段。确认局部 diff 后重新规划，再审核新版本。</p>
    <div ref={root} onMouseUp={capture} onKeyUp={capture}><pre data-review-field="plan-artifact" data-testid="plan-review-source" tabIndex={0} style={{ maxHeight: 340, overflow: "auto", whiteSpace: "pre-wrap" }}>{context?.body || "请选择已绑定文档"}</pre></div>
    {quote && <blockquote>{quote.text}</blockquote>}
    <textarea aria-label="Plan artifact discussion" value={message} disabled={working || !!draft} onChange={e => setMessage(e.target.value)} placeholder="说明选中片段应如何修改" />
    <div className="sdlc-toolbar"><button type="button" disabled={disabled || working || !!draft || !quote || !message.trim()} onClick={() => void send("discuss")}>讨论选中片段</button>
      <button type="button" disabled={disabled || working || !!draft || !quote || !message.trim() || !context?.editable} onClick={() => void send("revise")}>修改文档片段</button></div>
    {working && <p role="status">Agent 正在处理…</p>}{error && <p role="alert">{error}</p>}{!!result?.answer && <p>{String(result.answer)}</p>}
    {draft && <div data-testid="plan-review-diff"><p>修改前</p><pre>{String(result?.before || "")}</pre><p>修改后</p><pre>{String(result?.after || "")}</pre>
      <button type="button" disabled={working || disabled} onClick={() => void apply()}>确认局部修改并重新规划</button><button type="button" disabled={working} onClick={() => { setResult(null); onBusy(false); }}>放弃修改</button></div>}
    {!!result?.preview && <details open><summary>修改后的完整文档预览（候选）</summary><MarkdownText content={String(result.preview)} /></details>}
  </section>;
}
