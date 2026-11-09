import type { WebChatArtifact } from "../../api/types";
import type { AgentSessionPlanRequest } from "../agent-session/types";

export interface ComposerDraft {
  message: string;
  attachments: WebChatArtifact[];
  unreferencedTasks: string[];
  planDiscussion: AgentSessionPlanRequest | null;
}

export function composerDraftKey(projectId: string, threadId: string): string {
  return `zf.kanbanComposer.v1:${encodeURIComponent(projectId)}:${encodeURIComponent(threadId)}`;
}

export function readComposerDraft(storage: () => Pick<Storage, "getItem">, key: string): ComposerDraft {
  try {
    const value = JSON.parse(storage().getItem(key) || "null") as Partial<ComposerDraft> | null;
    return {
      message: typeof value?.message === "string" ? value.message : "",
      attachments: Array.isArray(value?.attachments) ? value.attachments : [],
      unreferencedTasks: Array.isArray(value?.unreferencedTasks)
        ? value.unreferencedTasks.filter((item): item is string => typeof item === "string") : [],
      planDiscussion: value?.planDiscussion && typeof value.planDiscussion.requestEventId === "string"
        && typeof value.planDiscussion.requestId === "string" && typeof value.planDiscussion.revision === "number"
        ? value.planDiscussion : null,
    };
  } catch {
    return { message: "", attachments: [], unreferencedTasks: [], planDiscussion: null };
  }
}
