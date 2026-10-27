import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { reviewWorkflowPreview } from "../../api/client";
import type { WorkflowRequestDetail } from "../../api/types";
import { asRecord } from "../../app/shared";
import { previewQuote, reviewBinding, reviewFieldLabel, reviewText, reviewablePreview, discussablePreview, previewUnavailableReason } from "./workflowReviewModel";
import PreviewAgentReply from "./PreviewAgentReply";

type Field = { value: unknown; editable: boolean; reason: string };
type Change = { field: string; before: unknown; after: unknown };

export function WorkflowPreviewReview({ detail, projectId, disabled, onBusy, onRefresh, children, open, onOpen, onClose, anchor }: {
  anchor: HTMLElement | null;
  open: boolean; onOpen: () => void; onClose: () => void;
  detail: WorkflowRequestDetail; projectId: string; disabled: boolean;
  onBusy: (busy: boolean) => void; onRefresh: () => Promise<void>; children: ReactNode;
}) {
  const binding = reviewBinding(detail);
  const identity = JSON.stringify(binding);
  const [fields, setFields] = useState<Record<string, Field>>({});
  const [field, setField] = useState("requirement.objective");
  const [quote, setQuote] = useState("");
  const [quoteRange, setQuoteRange] = useState<{ start: number; end: number } | null>(null);
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<{ role: string; text: string }[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const conversation = useRef<HTMLDivElement>(null);
  const [quoteAction, setQuoteAction] = useState<{ left: number; top: number } | null>(null);
  const epoch = useRef(0);
  const pending = reviewablePreview(detail);
  const canDiscuss = discussablePreview(detail);
  const unavailableReason = previewUnavailableReason(detail, !disabled);
  useEffect(() => {
    const element = conversation.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, draft, working, error, open]);
  useEffect(() => {
    const generation = ++epoch.current;
    setDraft(null); setChanges([]); setQuote(""); setQuoteAction(null); setError(""); setFields({}); setWorking(false);
    onBusy(false);
    if (canDiscuss && !disabled) {
      void reviewWorkflowPreview(projectId, { ...binding, operation: "context" }).then((result) => {
        if (epoch.current !== generation) return;
        if (!result.ok) { setError(String(result.reason)); return; }
        const next = result.fields as Record<string, Field>;
        setFields(next);
      }).catch((reason) => { if (epoch.current === generation) setError(String(reason)); });
    }
    return () => { epoch.current += 1; };
  }, [identity, projectId, pending, canDiscuss, disabled]);
  function captureQuote() {
    if (working || draft) return;
    const selected = previewQuote(window.getSelection(), root.current);
    if (!selected || (selected.field !== "yaml" && !fields[selected.field])) return;
    setField(selected.field); setQuote(selected.text); setQuoteRange(typeof fields[selected.field]?.value === "string" ? { start: selected.start, end: selected.end } : null);
    const rect = window.getSelection()!.getRangeAt(0).getBoundingClientRect();
    setQuoteAction({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 130)), top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 48)) });
  }
  async function send() {
    if (!message.trim() || working || draft || disabled || !canDiscuss || !Object.keys(fields).length) return;
    const generation = epoch.current;
    setQuoteAction(null);
    setWorking(true); onBusy(true); setError("");
    setMessages(previous => [...previous, { role: "You", text: message }]);
    let hasDraft = false;
    try {
      const result = await reviewWorkflowPreview(projectId, { ...binding, operation: "discuss", mode: "chat",
        message: quote && field === "yaml" ? `引用当前 YAML 片段（仅作修改上下文）：\n${quote}\n\n${message}` : message,
        ...(quote && field !== "yaml" ? { field, quote: { text: quote, ...quoteRange } } : {}) });
      if (generation !== epoch.current) return;
      if (!result.ok) { setError(String(result.reason)); return; }
      setMessages(previous => [...previous, { role: "Agent", text: String(result.answer || "") }]); setMessage("");
      const next = result.draft_ref ? asRecord(result.draft_ref) : null;
      hasDraft = Boolean(next); setDraft(next); setChanges((result.changes || []) as Change[]);
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) { setWorking(false); onBusy(hasDraft); } }
  }
  async function apply() {
    if (!draft || working) return;
    const generation = epoch.current;
    setQuoteAction(null);
    setWorking(true); onBusy(true); setError("");
    try {
      const result = await reviewWorkflowPreview(projectId, { operation: "apply", draft_ref: draft });
      if (generation !== epoch.current) return;
      if (!result.ok) { setError(String(result.reason)); return; }
      setDraft(null); setChanges([]); setQuote("");
      setMessages(previous => [...previous, { role: "Agent", text: "已更新 YAML。" }]);
      onBusy(false); await onRefresh();
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) setWorking(false); }
  }
  return <div ref={root} className="workflow-review-root" onMouseUp={captureQuote} onKeyUp={captureQuote}>
    {children}
    {canDiscuss && !disabled && quoteAction && !working && !draft ? <button type="button" className="workflow-quote-action" style={{ position: "fixed", ...quoteAction }} onMouseDown={(event) => event.preventDefault()} onClick={() => { setQuoteAction(null); window.getSelection()?.removeAllRanges(); onOpen(); }}>{pending ? "引用讨论 / 修改" : "引用讨论"}</button> : null}
    <PreviewAgentReply anchor={anchor} open={open} onClose={onClose} label="Workflow Agent">
      <section className="workflow-agent-chat" data-testid="workflow-preview-review">
        <div ref={conversation} className="workflow-agent-conversation" hidden={!messages.length && !unavailableReason && !working && !error} aria-live="polite">
          {unavailableReason && <p className="workflow-agent-empty" data-testid="workflow-agent-availability">{unavailableReason}</p>}
          {messages.map((item, index) => <article className="workflow-agent-message" key={index}>
            <span className={`workflow-agent-avatar ${item.role === "Agent" ? "is-agent" : ""}`} aria-hidden="true">{item.role === "Agent" ? "A" : "Y"}</span>
            <div><strong>{item.role}</strong><p data-testid={item.role === "Agent" ? "review-answer" : undefined}>{item.text}</p>
              {draft && index === messages.length - 1 && <div className="workflow-agent-diff" data-testid="review-diff">
                {changes.map(change => <div key={change.field}><strong>{reviewFieldLabel(change.field)}</strong>
                  <pre className="is-before">− {reviewText(change.before)}</pre><pre className="is-after">+ {reviewText(change.after)}</pre>
                </div>)}
                <div className="workflow-agent-diff-actions">
                  <button type="button" className="primary" disabled={working || disabled} onClick={() => void apply()}>Apply</button>
                  <button type="button" disabled={working} onClick={() => { setDraft(null); setChanges([]); onBusy(false); }}>Discard</button>
                </div>
              </div>}
            </div>
          </article>)}
          {working && <p role="status">Agent 正在处理…</p>}
          {error && <p role="alert">{error}</p>}
        </div>
        {quote && <blockquote data-testid="review-quote"><span>{quote}</span>
          <button type="button" aria-label="Clear quote" disabled={working || Boolean(draft)} onClick={() => setQuote("")}><X size={14} /></button>
        </blockquote>}
        <form className="workflow-agent-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <textarea ref={input} aria-label="Preview discussion" value={message} disabled={disabled || !canDiscuss || working || Boolean(draft)}
            onChange={event => setMessage(event.target.value)} placeholder={pending ? "回复…例如把 lanes 改成 2" : "询问当前 Workflow 的配置和执行方式"}
            onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
          <footer><small>⌘ / Ctrl + Enter</small><button type="submit" className="primary" disabled={disabled || !canDiscuss || working || Boolean(draft) || !Object.keys(fields).length || !message.trim()}>Send</button></footer>
        </form>
      </section>
    </PreviewAgentReply>
  </div>;
}
