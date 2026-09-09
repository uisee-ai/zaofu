import { SplitSquareHorizontal } from "lucide-react";
import { useEffect, useRef } from "react";
import type { AgentConversation, AgentSessionStatus } from "./types";

interface AgentThreadNavigationProps {
  conversation: AgentConversation;
  activeThreadId: string;
  allowSplit?: boolean;
  splitThreadId?: string;
  onActiveThreadChange?: (threadId: string) => void;
  onSplitThreadChange?: (threadId: string) => void;
}

export function AgentThreadNavigation({
  conversation,
  activeThreadId,
  allowSplit = false,
  splitThreadId = "",
  onActiveThreadChange,
  onSplitThreadChange,
}: AgentThreadNavigationProps) {
  const chipListRef = useRef<HTMLDivElement | null>(null);
  const activeThread = conversation.threads.find((thread) => thread.id === activeThreadId)
    ?? conversation.threads[0];
  const canSplit = allowSplit && conversation.threads.length > 1;

  useEffect(() => {
    const list = chipListRef.current;
    const active = list?.querySelector<HTMLElement>("[aria-selected='true']");
    if (!list || !active) return;
    const left = active.offsetLeft;
    const right = left + active.offsetWidth;
    if (left < list.scrollLeft) list.scrollLeft = left;
    else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth;
  }, [activeThread?.id, conversation.threads.length]);

  if (conversation.threads.length <= 1) return null;
  return (
    <div className="agent-thread-bar" data-testid="agent-thread-navigation">
      <div className="agent-thread-chips" ref={chipListRef} role="tablist" aria-label="Agent threads">
        {conversation.threads.map((thread) => (
          <button
            aria-selected={thread.id === activeThread?.id}
            className={`agent-thread-chip ${thread.id === activeThread?.id ? "active" : ""}`}
            key={thread.id}
            type="button"
            onClick={() => onActiveThreadChange?.(thread.id)}
          >
            <span className={`agent-thread-dot ${threadStatusClass(thread.status)}`} />
            <span>{thread.title}</span>
            {thread.unseenCount ? <span className="agent-thread-count">{formatUnreadCount(thread.unseenCount)}</span> : null}
          </button>
        ))}
      </div>
      {canSplit ? (
        <label className="agent-split-control">
          <SplitSquareHorizontal size={14} />
          <select
            aria-label="Compare with thread"
            value={splitThreadId}
            onChange={(event) => onSplitThreadChange?.(event.target.value)}
          >
            <option value="">single</option>
            {conversation.threads.filter((thread) => thread.id !== activeThread?.id).map((thread) => (
              <option key={thread.id} value={thread.id}>{thread.title}</option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

function threadStatusClass(status: AgentSessionStatus): string {
  if (status === "streaming" || status === "submitted") return "streaming";
  if (status === "failed") return "failed";
  if (status === "cancelled" || status === "stale") return "cancelled";
  if (status === "waiting_input" || status === "queued") return "waiting";
  if (status === "completed") return "completed";
  return "idle";
}

function formatUnreadCount(count: number): string {
  return count > 99 ? "99+" : String(count);
}
