import { ChevronDown, ChevronUp, MessageCircle } from "lucide-react";
import type { ReactNode } from "react";
import { taskUpdatePresentation } from "../kanban/TaskUpdatePreview";
import type { AgentSessionThread } from "../agent-session/types";

export function TaskChatHeader({ thread, hasMore, open, onToggle, accessControl }: {
  thread?: AgentSessionThread; hasMore: boolean; open: boolean; onToggle: () => void; accessControl?: ReactNode;
}) {
  const latest = thread?.turns.at(-1);
  const reply = latest?.runs.flatMap(run => run.parts).filter(part => part.kind === "text" || part.kind === "error").at(-1);
  const answer = reply?.content || reply?.summary;
  const card = latest?.cards.at(-1);
  const update = card?.proposal?.action === "update-task" ? card.proposal : undefined;
  let action = card?.title;
  if (update) {
    if (card?.payload?.resolution === "executed") action = taskUpdatePresentation(update.payload).completedLabel;
    else action = card?.status === "completed" ? "更新已取消" : "待确认任务更新";
  }
  const preview = answer || action || latest?.user?.content;
  const author = answer ? (reply?.kind === "error" ? "系统" : "zaofu agent") : card ? "任务操作" : "你";
  return <header className="reader-chat-header">
    <div><strong><MessageCircle size={15} aria-hidden="true" />Task conversation</strong>
      <span className="reader-chat-actions">{accessControl}{(latest || hasMore) && <button type="button" aria-expanded={open} onClick={onToggle}>
        {open ? "收起对话" : "展开对话"}{open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>}</span>
    </div>
    {!open && preview && <p className="reader-chat-preview"><span>{author}</span><span>{preview}</span></p>}
  </header>;
}
