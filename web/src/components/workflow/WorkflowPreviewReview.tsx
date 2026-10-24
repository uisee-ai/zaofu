import { useEffect, useRef, useState, type ReactNode } from "react";
import { reviewWorkflowPreview } from "../../api/client";
import type { WorkflowRequestDetail } from "../../api/types";
import { asRecord } from "../../app/shared";
import { previewQuote, reviewBinding, reviewFieldLabel, reviewText } from "./workflowReviewModel";

type Field = { value: unknown; editable: boolean; reason: string };
type Change = { field: string; before: unknown; after: unknown };

export function WorkflowPreviewReview({ detail, projectId, disabled, onBusy, onRefresh, children }: {
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
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [quoteAction, setQuoteAction] = useState<{ left: number; top: number } | null>(null);
  const epoch = useRef(0);
  const pending = asRecord(detail.result).status === "proposed";
  useEffect(() => {
    const generation = ++epoch.current;
    setDraft(null); setChanges([]); setQuote(""); setQuoteAction(null); setAnswer(""); setError(""); setFields({}); setWorking(false);
    onBusy(false);
    if (pending && !disabled) {
      void reviewWorkflowPreview(projectId, { ...binding, operation: "context" }).then((result) => {
        if (epoch.current !== generation) return;
        if (!result.ok) { setError(String(result.reason)); return; }
        setFields(result.fields as Record<string, Field>);
      }).catch((reason) => { if (epoch.current === generation) setError(String(reason)); });
    }
    return () => { epoch.current += 1; };
  }, [identity, projectId, pending, disabled]);
  function captureQuote() {
    if (working || draft) return;
    const selected = previewQuote(window.getSelection(), root.current);
    if (!selected || !fields[selected.field]) return;
    setField(selected.field); setQuote(selected.text); setQuoteRange(typeof fields[selected.field]?.value === "string" ? { start: selected.start, end: selected.end } : null);
    const rect = window.getSelection()!.getRangeAt(0).getBoundingClientRect();
    setQuoteAction({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 130)), top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 48)) });
  }
  async function send(mode: "discuss" | "revise") {
    if (!message.trim() || working || draft) return;
    const generation = epoch.current;
    setQuoteAction(null);
    setWorking(true); onBusy(true); setError("");
    let hasDraft = false;
    try {
      const result = await reviewWorkflowPreview(projectId, { ...binding, operation: "discuss", mode, field, message,
        ...(quote ? { quote: { text: quote, ...quoteRange } } : {}) });
      if (generation !== epoch.current) return;
      if (!result.ok) { setError(String(result.reason)); return; }
      setAnswer(String(result.answer || ""));
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
      setDraft(null); setChanges([]); setAnswer("已生成新版 Preview，请审核后单独批准启动。");
      onBusy(false); await onRefresh();
    } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) setWorking(false); }
  }
  return <div ref={root} onMouseUp={captureQuote} onKeyUp={captureQuote}>
    {children}
    {pending && quoteAction && !working && !draft ? <button type="button" className="workflow-quote-action" style={{ position: "fixed", ...quoteAction }} onMouseDown={(event) => event.preventDefault()} onClick={() => { setQuoteAction(null); window.getSelection()?.removeAllRanges(); panel.current?.scrollIntoView({ behavior: "smooth", block: "center" }); panel.current?.querySelector("textarea")?.focus({ preventScroll: true }); }}>引用讨论 / 修改</button> : null}
    {pending ? <section ref={panel} className="workflow-clarification workflow-preview-review" data-testid="workflow-preview-review">
      <h3>Chat about this Preview</h3>
      <p>选中文字引用讨论，或选择一个字段微调。修改会生成新版预览，随后再批准执行。</p>
      <label>讨论位置<select aria-label="Review field" value={field} disabled={working || Boolean(draft)} onChange={(event) => { setField(event.target.value); setQuote(""); }}>
        {Object.keys(fields).map((key) => <option key={key} value={key}>{reviewFieldLabel(key)}</option>)}
      </select></label>
      <pre data-review-field={field} data-testid="review-field-text" tabIndex={0}>{reviewText(fields[field]?.value)}</pre>
      {quote ? <blockquote data-testid="review-quote"><small>引用 · {reviewFieldLabel(field)} · r{String(binding.revision)}</small><p>{quote}</p>
        <button type="button" disabled={working || Boolean(draft)} onClick={() => setQuote("")}>清除引用</button></blockquote> : null}
      <textarea aria-label="Preview discussion" value={message} disabled={working || Boolean(draft)} onChange={(event) => setMessage(event.target.value)} placeholder="例如：这条验收只补充空输入场景，其余保持不变" />
      <div className="agent-approve-actions">
        <button type="button" disabled={disabled || working || Boolean(draft) || !fields[field] || !message.trim()} onClick={() => void send("discuss")}>引用讨论 / Chat about</button>
        <button type="button" disabled={disabled || working || Boolean(draft) || !fields[field]?.editable || !message.trim()} onClick={() => void send("revise")}>提出局部修改</button>
      </div>
      {fields[field]?.reason ? <p>{fields[field].reason}</p> : null}
      {working ? <p role="status">处理中…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {answer ? <p data-testid="review-answer">{answer}</p> : null}
      {draft ? <div data-testid="review-diff">{changes.map((change) => <div key={change.field}><strong>{reviewFieldLabel(change.field)}</strong>
        <p>修改前</p><pre>{reviewText(change.before)}</pre><p>修改后</p><pre>{reviewText(change.after)}</pre></div>)}
        <p>未列出的字段保持不变。应用修改不会启动 Workflow。</p>
        <button type="button" disabled={working || disabled} onClick={() => void apply()}>应用修改并生成新版 Preview</button>
        <button type="button" disabled={working} onClick={() => { setDraft(null); setChanges([]); onBusy(false); }}>放弃修改</button>
      </div> : null}
    </section> : null}
  </div>;
}
