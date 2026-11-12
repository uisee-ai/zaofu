import { useState } from "react";
import { MarkdownText } from "./MarkdownText";

export function ReadableAgentReply({ content, streaming }: { content: string; streaming: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const long = content.length > 6000;
  // Clamping is visual only: full copy/export and the original reply remain intact.
  return <div className="agent-readable-reply">
    <div className={long && !expanded ? "agent-readable-reply-clamped" : ""}>
      <MarkdownText content={content} formatStandaloneJson isStreaming={streaming} />
    </div>
    {long ? <button type="button" className="agent-inline-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? "收起完整回复" : "展开完整回复"}
    </button> : null}
  </div>;
}
