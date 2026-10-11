import { useId, useState } from "react";

export function ChannelFactScopeControl({ question, busy, disabled, onExclude }: {
  question: { id: string; text: string; sourceThreadId: string };
  busy: boolean;
  disabled: boolean;
  onExclude: (questionId: string, threadId: string, reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const inputId = useId();
  return <details className="channel-question-card" data-testid="channel-fact-scope-control">
    <summary>
      <span className="channel-question-eyebrow">待核实事实 · 范围处理</span>
      <strong>{question.text}</strong>
    </summary>
    <form className="channel-fact-scope-form" onSubmit={(event) => {
      event.preventDefault();
      if (!disabled && !busy && reason.trim()) {
        void onExclude(question.id, question.sourceThreadId, reason.trim());
      }
    }}>
      <p>如果该事实不属于本次定稿范围，可由你记录排除理由。范围排除不表示事实已经验证。</p>
      <label htmlFor={inputId}>范围排除理由</label>
      <textarea id={inputId} value={reason} disabled={disabled || busy}
        onChange={(event) => setReason(event.target.value)} rows={3} required />
      <button className="icon-button" type="submit" disabled={disabled || busy || !reason.trim()}>
        排除出本次定稿范围
      </button>
    </form>
  </details>;
}
