import { useCallback, useReducer, useRef, useState } from "react";
import type { WebChatArtifact } from "../../api/types";
import type { AgentSessionPlanRequest } from "../agent-session/types";
import { composerDraftKey, readComposerDraft, type ComposerDraft } from "./kanbanComposerDraft";

export function useKanbanComposerDraft(projectId: string, threadId: string) {
  const key = composerDraftKey(projectId, threadId);
  const drafts = useRef<Record<string, ComposerDraft>>({});
  const [, render] = useReducer((revision: number) => revision + 1, 0);
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const draft = drafts.current[key] ?? readComposerDraft(() => window.localStorage, key);

  const update = useCallback((patch: Partial<ComposerDraft>) => {
    const next = { ...(drafts.current[key] ?? readComposerDraft(() => window.localStorage, key)), ...patch };
    drafts.current[key] = next;
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Keep the in-memory draft when storage is unavailable or full.
    }
    render();
  }, [key]);

  return {
    message: draft.message,
    attachments: draft.attachments,
    uploading: Boolean(uploading[key]),
    unreferencedTasks: draft.unreferencedTasks,
    planDiscussion: draft.planDiscussion,
    setPlanDiscussion: (planDiscussion: AgentSessionPlanRequest | null) => update({ planDiscussion }),
    setMessage: (message: string) => update({ message }),
    setAttachments: (attachments: WebChatArtifact[]) => update({ attachments }),
    setTaskReferenced: (taskId: string, referenced: boolean) => update({
      unreferencedTasks: referenced
        ? draft.unreferencedTasks.filter((item) => item !== taskId)
        : [...new Set([...draft.unreferencedTasks, taskId])],
    }),
    setUploading: (value: boolean) => setUploading((current) => ({ ...current, [key]: value })),
  };
}
