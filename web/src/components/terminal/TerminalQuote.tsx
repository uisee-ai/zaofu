import { useState } from "react";
import type { Terminal } from "@xterm/xterm";
import type { TerminalSession } from "./types";

export interface QuoteTarget { kind: "channel" | "task"; id: string; title: string }
export interface QuoteDestinationProps {
  quoteTargets: QuoteTarget[];
  onQuote: (target: QuoteTarget, text: string) => void;
}

export function TerminalQuote({ terminal, session, hasSelection, quoteTargets, onQuote }: QuoteDestinationProps & {
  terminal: Terminal; session: TerminalSession; hasSelection: boolean;
}) {
  const [quote, setQuote] = useState("");
  const [targetKey, setTargetKey] = useState("");
  const key = (target: QuoteTarget) => target.kind + ":" + target.id;
  const selected = quoteTargets.find(target => key(target) === targetKey);
  function capture() {
    const text = terminal.getSelection();
    if (!text) return;
    const pos = terminal.getSelectionPosition();
    const lastLine = pos && Math.max(pos.start.y + 1, pos.end.y + Number(pos.end.x > 0));
    const lines = pos ? ` · lines ${pos.start.y + 1}–${lastLine}` : "";
    const fence = "`".repeat((text.match(/`+/g) || []).reduce((width, run) => Math.max(width, run.length), 2) + 1);
    setQuote(`${session.title} (${session.session_id} · g${session.generation})${lines} · ${new Date().toISOString()}\n${fence}\n${text}\n${fence}`);
  }
  if (!hasSelection && !quote) return null;
  return <div className="web-terminal-quote">
    {quote ? <fieldset>
      <legend>Quote terminal selection</legend>
      <select aria-label="Discussion destination" value={targetKey} onChange={e => setTargetKey(e.target.value)}>
        <option value="">Choose a discussion</option>
        {quoteTargets.map(target => <option key={key(target)} value={key(target)}>{target.kind} · {target.title}</option>)}
      </select>
      <textarea aria-label="Quoted text" rows={5} value={quote} onChange={e => setQuote(e.target.value)} />
      <div className="web-terminal-quote-actions">
      <button type="button" disabled={!selected || !quote.trim()} onClick={() => selected && onQuote(selected, quote)}>Open discussion draft</button>
      <button type="button" onClick={() => setQuote("")}>Cancel</button>
      </div>
    </fieldset> : <button type="button" onMouseDown={e => e.preventDefault()} onClick={capture}>Quote selection</button>}
  </div>;
}
