import { useEffect, useLayoutEffect, useRef, useState, type RefObject, type SetStateAction } from "react";
import type { ChannelDetail } from "../../api/types";

export interface ComposerAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
  lastModified: number;
  uploadStatus?: "uploading" | "uploaded" | "failed";
  uploadError?: string;
  uploadedRef?: Record<string, unknown>;
}

export interface ChannelComposerDraft {
  schemaVersion: "channel-composer-draft.v1";
  clientMessageId: string;
  threadId: string;
  text: string;
  html: string;
  attachments: ComposerAttachment[];
  refs?: Record<string, unknown>;
  reason: string;
}

export function channelDraftStorageKey(projectId: string, channelId: string, threadId: string): string {
  return `zf.channelDraft:v2:${JSON.stringify([projectId, channelId, threadId])}`;
}

function readDraft(key: string, threadId: string): ChannelComposerDraft {
  const empty: ChannelComposerDraft = { schemaVersion: "channel-composer-draft.v1", threadId,
    text: "", html: "", attachments: [], clientMessageId: "", reason: "" };
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || "null") as ChannelComposerDraft | null;
    return value?.schemaVersion === empty.schemaVersion && value.threadId === threadId
      && typeof value.text === "string" && Array.isArray(value.attachments)
      ? { ...value, attachments: value.attachments.map((item) => item.uploadedRef ? item : { ...item, uploadStatus: "failed", uploadError: "正文不可用，请移除后重新上传" }) }
      : empty;
  } catch { return empty; }
}

export function useChannelComposerDraft(
  projectId: string, channelId: string, threadId: string, editor: RefObject<HTMLDivElement>,
) {
  const key = channelDraftStorageKey(projectId, channelId, threadId);
  const [record, setRecord] = useState(() => ({ key, draft: readDraft(key, threadId) }));
  // Adjust before commit so neither the editor nor persistence can use another scope's draft.
  if (record.key !== key) setRecord({ key, draft: readDraft(key, threadId) });
  const draft = record.draft;
  const currentKey = useRef(key);
  currentKey.current = key;
  useLayoutEffect(() => {
    if (editor.current) {
      if (draft.html) editor.current.innerHTML = draft.html;
      else editor.current.textContent = draft.text;
    }
  }, [key]);
  useEffect(() => {
    if (record.key !== key) return;
    if (draft.text || draft.attachments.length || draft.clientMessageId) {
      window.localStorage.setItem(key, JSON.stringify(draft));
    } else window.localStorage.removeItem(key);
  }, [key, record]);
  function update<K extends keyof ChannelComposerDraft>(field: K, value: SetStateAction<ChannelComposerDraft[K]>) {
    const html = editor.current?.innerHTML || "";
    setRecord((previous) => {
      if (previous.key !== key) return previous;
      const next = typeof value === "function"
        ? (value as (old: ChannelComposerDraft[K]) => ChannelComposerDraft[K])(previous.draft[field]) : value;
      return { key, draft: { ...previous.draft, [field]: next, ...(field === "text" ? { html } : {}) } };
    });
  }
  return {
    draftKey: key, currentDraftKey: currentKey,
    composerText: draft.text, setComposerText: (value: SetStateAction<string>) => update("text", value),
    composerAttachments: draft.attachments,
    setComposerAttachments: (value: SetStateAction<ComposerAttachment[]>) => update("attachments", value),
    composerRefs: draft.refs || {},
    setComposerRefs: (value: SetStateAction<Record<string, unknown> | undefined>) => update("refs", value),
    retryClientMessageId: draft.clientMessageId,
    setRetryClientMessageId: (value: SetStateAction<string>) => update("clientMessageId", value),
    composerError: draft.reason, setComposerError: (value: SetStateAction<string>) => update("reason", value),
  };
}

export function useChannelThreadSelection(projectId: string, channelId: string, detail: ChannelDetail | null) {
  const key = `zf.channelThread:v1:${JSON.stringify([projectId, channelId])}`;
  const [selection, setSelection] = useState(() => ({ key, id: window.localStorage.getItem(key) || "" }));
  const requested = selection.key === key ? selection.id : window.localStorage.getItem(key) || "";
  const scoped = detail?.channel_id === channelId ? detail : null;
  const ids = [...new Set([
    ...Object.keys(scoped?.discussions || {}),
    ...(scoped?.messages || []).map((row) => String(row.thread_id || "main")),
  ])];
  const activeId = requested || ids[0] || "main";
  useEffect(() => {
    if (detail?.channel_id === channelId) window.localStorage.setItem(key, activeId);
  }, [key, activeId, detail?.channel_id, channelId]);
  return [activeId, (id: string) => setSelection({ key, id })] as const;
}
