import { ChevronDown, ChevronUp, MessageCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { AgentSessionThread } from "../agent-session/types";

export function TaskChatHeader({ thread, hasMore, open, onToggle, accessControl }: {
  thread?: AgentSessionThread; hasMore: boolean; open: boolean; onToggle: () => void; accessControl?: ReactNode;
}) {
  const latest = thread?.turns.at(-1);
  const reply = latest?.runs.flatMap(run => run.parts).filter(part => part.kind === "text" || part.kind === "error").at(-1);
  const preview = reply?.content || reply?.summary || latest?.cards.at(-1)?.title || latest?.user?.content;
  return <header className="reader-chat-header">
    <div><strong><MessageCircle size={15} aria-hidden="true" />Kanban Agent</strong>
      <span className="reader-chat-actions">{accessControl}{(latest || hasMore) && <button type="button" aria-expanded={open} onClick={onToggle}>
        {open ? "收起对话" : "展开对话"}{open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>}</span>
    </div>
    {!open && preview && <p className="reader-chat-preview"><span>最近一轮</span><span>{preview}</span></p>}
  </header>;
}
