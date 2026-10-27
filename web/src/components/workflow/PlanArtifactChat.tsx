import { useEffect, useRef, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { reviewPlanArtifact } from "../../api/client";
import PlanReviewDocument from "./PlanReviewDocument";
import type { PlanQuote } from "./planReviewDocument";
import { MarkdownText } from "../agent-session/MarkdownText";
import { MessageSquare, X } from "lucide-react";
import PreviewAgentReply from "./PreviewAgentReply";

export default function PlanArtifactChat({ preview, projectId, disabled, onBusy, onApplied }: {
  preview: PlanPreview; projectId: string; disabled: boolean; onBusy: (busy: boolean) => void;
  onApplied?: () => Promise<void>;
}) {
  const [selection, setName] = useState("accepted_plan");
  const name = selection === "" ? "" : preview.artifacts?.some(a => a.name === selection) ? selection
    : preview.artifacts?.find(a => a.name === "accepted_plan")?.name || "";
  const [context, setContext] = useState<{ body: string; digest: string; editable: boolean } | null>(null);
  const [quote, setQuote] = useState<{ text: string; start: number; end: number } | null>(null);
  const [quoteAction, setQuoteAction] = useState<{ quote: PlanQuote; left: number; top: number } | null>(null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const [messages, setMessages] = useState<{ role: string; text: string }[]>([]);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [contextError, setContextError] = useState("");
  const [reload, setReload] = useState(0);
  const conversation = useRef<HTMLDivElement>(null);
  const epoch = useRef(0);
  const draft = result?.draft_ref as Record<string, unknown> | undefined;
  const unavailableReason = disabled ? "当前页面没有编辑权限。"
    : !name ? "当前未绑定完整 Plan 正文，摘要仅供阅读，无法引用修改。"
    : context && !context.editable ? preview.status === "rejected" ? "此版本已退回，等待新版 Plan Preview。" : "当前 Plan 已处理，修改需要重新规划。" : "";
  const artifact = preview.artifacts?.find(a => a.name === name);
  const binding = JSON.stringify([preview.plan_id, preview.status, name, artifact?.ref, artifact?.sha256]);
  useEffect(() => {
    const generation = ++epoch.current;
    setContext(null); setContextError(""); setQuote(null); setResult(null); setError(""); setWorking(false); onBusy(false);
    setQuoteAction(null); setMessage(""); setMessages([]);
    if (!disabled && name) void reviewPlanArtifact(projectId, { operation: "context", plan_id: preview.plan_id, name }).then(value => {
      if (generation !== epoch.current) return;
      if (!value.ok) { setContextError(String(value.reason)); return; }
      setContext(value as unknown as NonNullable<typeof context>);
    }).catch(reason => { if (generation === epoch.current) setContextError(String(reason)); });
    return () => { epoch.current++; onBusy(false); };
  }, [projectId, binding, disabled, reload]);
  useEffect(() => {
    if (conversation.current) conversation.current.scrollTop = conversation.current.scrollHeight;
  }, [messages, result, working, error, open]);
  function capture(selected: PlanQuote | null, rect?: DOMRect) {
    if (!selected || !rect || working || draft || !context?.editable || disabled) { setQuoteAction(null); return; }
    setQuoteAction({ quote: selected, left: Math.max(8, Math.min(rect.left, window.innerWidth - 160)), top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 48)) });
  }
  function edit(selected: PlanQuote) {
    setQuote(selected); setQuoteAction(null); setOpen(true);
    window.getSelection()?.removeAllRanges();
  }
  async function send() {
    if (!context || !quote || !message.trim() || working || draft || disabled) return;
    const generation = epoch.current;
    setWorking(true); onBusy(true); setError("");
    setMessages(previous => [...previous, { role: "You", text: message }]);
    let hold = false;
    try {
      const value = await reviewPlanArtifact(projectId, { operation: "discuss", mode: "chat", plan_id: preview.plan_id,
        name, digest: context.digest, quote, message });
      if (generation !== epoch.current) return;
      if (!value.ok) setError(String(value.reason)); else {
        setResult(value); hold = !!value.draft_ref; setMessage("");
        setMessages(previous => [...previous, { role: "Agent", text: String(value.answer || "") }]);
      }
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) { setWorking(false); onBusy(hold); } }
  }
  async function apply() {
    if (!draft || working || disabled) return;
    const generation = epoch.current;
    setWorking(true); onBusy(true); setError("");
    try {
      const value = await reviewPlanArtifact(projectId, { operation: "apply", draft_ref: draft });
      if (generation !== epoch.current) return;
      if (!value.ok) setError(String(value.reason));
      else {
        setResult({ preview: value.candidate_preview });
        setContext(previous => previous && { ...previous, editable: false });
        setMessages(previous => [...previous, { role: "Agent", text: "已提交局部修订，等待 Planner 生成新版 PRD / Plan 预览，再由你批准。" }]);
        // Keep approval locked until polling observes the rejected/replanned version.
        await onApplied?.();
      }
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) setWorking(false); }
  }
  return <section className="plan-artifact-review" aria-label="PRD Plan Agent Chat">
    <label>Document <select aria-label="Plan review artifact" value={name} disabled={disabled || working || !!draft} onChange={e => setName(e.target.value)}>
      <option value="">Plan Preview</option>
      {preview.artifacts?.map(a => <option key={a.name} value={a.name}>{a.name === "accepted_plan" ? "Plan" : a.name}</option>)}
    </select></label>
    {unavailableReason && <p role="status">{unavailableReason}</p>}
    {contextError && <p role="alert">文档加载失败：{contextError} <button type="button" onClick={() => setReload(value => value + 1)}>重试</button></p>}
    {!context && !contextError && !disabled && name && <p role="status">正在读取已绑定文档…</p>}
    {(context || !name || (disabled && name === "accepted_plan")) && <PlanReviewDocument key={`${preview.plan_id}:${name}`} body={context?.body ?? preview.markdown ?? ""}
      disabled={disabled || working || !!draft || !context?.editable} onSelect={capture}
      agentControls={<>
        <button type="button" data-preview-agent-trigger aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>Agent</button>
        <div ref={setAnchor} className="plan-agent-anchor" />
      </>} />}
    {quoteAction && !disabled && !working && !draft && context?.editable && <button type="button" className="workflow-quote-action plan-selection-action"
      data-preview-agent-trigger style={{ position: "fixed", left: quoteAction.left, top: quoteAction.top }}
      onPointerDown={event => event.preventDefault()} onClick={() => edit(quoteAction.quote)}><MessageSquare size={14} />引用到 Agent</button>}
    <PreviewAgentReply anchor={anchor} open={open} onClose={() => setOpen(false)} label="Plan Agent">
      <section className="workflow-agent-chat">
        <div ref={conversation} className="workflow-agent-conversation" hidden={!messages.length && !working && !error && !unavailableReason} aria-live="polite">
          {unavailableReason && <p className="workflow-agent-empty" data-testid="plan-agent-availability">{unavailableReason}</p>}
          {messages.map((item, index) => <article className="workflow-agent-message" key={index}>
            <span className={`workflow-agent-avatar ${item.role === "Agent" ? "is-agent" : ""}`} aria-hidden="true">{item.role === "Agent" ? "A" : "Y"}</span>
            <div><strong>{item.role}</strong><p>{item.text}</p>
              {draft && index === messages.length - 1 && <div className="workflow-agent-diff" data-testid="plan-review-diff">
                <pre className="is-before">− {String(result?.before || "")}</pre><pre className="is-after">+ {String(result?.after || "")}</pre>
                <div className="workflow-agent-diff-actions"><button type="button" className="primary" disabled={working || disabled} onClick={() => void apply()}>Apply</button>
                  <button type="button" disabled={working} onClick={() => { setResult(null); onBusy(false); }}>Discard</button></div>
              </div>}
            </div>
          </article>)}
          {working && <p role="status">Agent 正在处理…</p>}{error && <p role="alert">{error}</p>}
        </div>
        {quote && <blockquote data-testid="plan-review-quote"><span>{quote.text}</span><button type="button" aria-label="Clear quote" disabled={working || !!draft} onClick={() => setQuote(null)}><X size={14} /></button></blockquote>}
        <form className="workflow-agent-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <textarea aria-label="Plan artifact discussion" value={message} disabled={disabled || working || !!draft || !context?.editable}
            onChange={e => setMessage(e.target.value)} placeholder={quote ? "回复…例如调整这段验收条件" : "先在文档中选中并引用一个片段"}
            onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
          <footer><small>{quote ? "⌘ / Ctrl + Enter" : "选中文字后点击「引用到 Agent」"}</small><button type="submit" className="primary" disabled={disabled || working || !!draft || !context?.editable || !quote || !message.trim()}>Send</button></footer>
        </form>
      </section>
    </PreviewAgentReply>
    {!!result?.preview && !draft && <details><summary>修改后的完整文档预览（候选）</summary><MarkdownText content={String(result.preview)} /></details>}
  </section>;
}
