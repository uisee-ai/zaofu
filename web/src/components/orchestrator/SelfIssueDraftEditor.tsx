import { forwardRef, memo, useEffect, useImperativeHandle, useRef, useState } from "react";
import { MarkdownText } from "../agent-session/MarkdownText";
import { DraftSaveConflict, IssueDraftAutosave, type AutosaveView } from "./issueDraftAutosave";
import { issueDraftBackupStore, type DraftSnapshot } from "./selfIssueDraftStore";

type Data = Record<string, unknown>;
const obj = (value: unknown): Data => value && typeof value === "object" ? value as Data : {};
const snapshot = (editor: Data): DraftSnapshot => ({
  title: String(editor.title || ""), body: String(editor.body || ""), revision: Number(editor.revision || 0),
  locked: ["published", "publishing", "partially_published", "outcome_unknown"].includes(String(editor.publication_state)),
});
const Preview = memo(({ body }: { body: string }) => <div aria-label="Issue Markdown preview"><MarkdownText content={body} /></div>);
export interface DraftEditorHandle { flush(): Promise<void>; persist(): Promise<void> }

// Only this component re-renders while typing. Reports and tool logs remain in
// the parent workspace and Markdown is not mounted in edit mode.
export const SelfIssueDraftEditor = forwardRef<DraftEditorHandle, {
  sessionId: string; editor: Data; preview: boolean; busy: boolean;
  onAction: (action: string, payload: Data) => unknown | Promise<unknown>;
  onSaved: (result: Data) => void;
}>(function SelfIssueDraftEditor({ sessionId, editor, preview, busy, onAction, onSaved }, ref) {
  const latest = useRef({ editor, onAction, onSaved });
  latest.current = { editor, onAction, onSaved };
  const controller = useRef<IssueDraftAutosave | null>(null);
  const [view, setView] = useState<AutosaveView>(() => ({ content: snapshot(editor), ready: false, status: "saved",
    revision: Number(editor.revision), remote: snapshot(editor), localError: "", remoteError: "", recovery: [] }));
  const [resolutionError, setResolutionError] = useState("");
  const [resolving, setResolving] = useState(false);
  const draftId = String(editor.draft_id);

  useEffect(() => {
    let alive = true;
    const request = async (action: string, payload: Data = {}) => {
      const result = obj(await latest.current.onAction(action, { session_id: sessionId, ...payload }));
      if (result.ok === false) {
        const reason = String(result.reason || result.status || "保存失败");
        if (["revision_conflict", "published_immutable"].includes(String(result.status))) throw new DraftSaveConflict(reason);
        throw new Error(reason);
      }
      return result;
    };
    const instance = new IssueDraftAutosave({
      scope: `${sessionId}:${draftId}`, clientId: crypto.randomUUID(), store: issueDraftBackupStore,
      remote: snapshot(latest.current.editor), changed: (next) => { if (alive) setView(next); },
      save: async (content, revision) => {
        const result = await request("self-issue-session-save-draft", { revision, ...content });
        const next = obj(result.editor);
        if (!Number(next.revision)) throw new Error("服务端未返回保存后的 Draft 版本");
        if (alive) latest.current.onSaved(result);
        return snapshot(next);
      },
      refresh: async () => {
        const result = await request("self-issue-session-get");
        const next = obj(result.editor);
        if (!Number(next.revision)) throw new Error("无法读取最新 Draft 版本");
        return snapshot(next);
      },
    });
    controller.current = instance;
    void instance.start();
    const flush = () => { void instance.persist(); void instance.flush().catch(() => undefined); };
    const visibility = () => { if (document.visibilityState === "hidden") flush(); };
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", flush);
    window.addEventListener("online", flush);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("online", flush);
      instance.dispose();
      if (controller.current === instance) controller.current = null;
    };
  }, [sessionId, draftId]);
  useEffect(() => { controller.current?.observe(snapshot(editor)); }, [editor.title, editor.body, editor.revision, editor.publication_state]);
  useImperativeHandle(ref, () => ({
    flush: async () => { await controller.current?.flush(); },
    persist: async () => { await controller.current?.persist(); },
  }), []);
  const disabled = busy || resolving || !view.ready || Boolean(view.remote.locked);
  const statusLabel = !view.ready ? "正在恢复本机草稿…" : view.status === "conflict" ? "保存冲突：本机与服务端内容均保留"
    : view.status === "saving" ? "正在同步服务端…" : view.status === "saved" ? `已保存 Draft v${view.revision}`
      : view.localError ? "本机保存失败" : ["local", "offline"].includes(view.status) ? "已保存在本机，等待同步" : "正在备份本机草稿…";
  const resolve = async (choice: "server" | "local", index?: number) => {
    setResolving(true); setResolutionError("");
    try { await controller.current?.resolve(choice, index === undefined ? undefined : view.recovery[index]); }
    catch (error) { setResolutionError(String(error)); }
    finally { setResolving(false); }
  };
  return <section aria-label="Issue Draft editor" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) void controller.current?.flush().catch(() => undefined);
  }}>
    <h4>Draft v{view.revision} · 自动保存</h4>
    <p role="status" aria-live="polite">{statusLabel}</p>
    {view.localError ? <p role="alert">本机备份不可用：{view.localError}。未同步内容刷新后可能丢失。</p> : null}
    {view.remoteError ? <p role="alert">服务端同步：{view.remoteError}</p> : null}
    {resolutionError ? <p role="alert">{resolutionError}</p> : null}
    {view.status === "offline" ? <button type="button" onClick={() => void controller.current?.flush().catch(() => undefined)}>重试同步</button> : null}
    {view.status === "conflict" ? <section aria-label="Draft 保存冲突">
      <details open><summary>服务端版本 v{view.remote.revision}</summary><pre>{view.remote.title}{"\n"}{view.remote.body}</pre></details>
      {view.recovery.map((backup, index) => <details key={backup.key} open><summary>本机未同步版本 {index + 1}</summary>
        <pre>{backup.title}{"\n"}{backup.body}</pre><button type="button" disabled={resolving || Boolean(view.remote.locked)} onClick={() => void resolve("local", index)}>恢复本机版本 {index + 1} 并同步</button>
      </details>)}
      {!view.recovery.length ? <button type="button" disabled={resolving || Boolean(view.remote.locked)} onClick={() => void resolve("local")}>保留当前编辑并同步</button> : null}
      <button type="button" disabled={resolving} onClick={() => void resolve("server")}>采用服务端版本，放弃本机修改</button>
    </section> : null}
    <input aria-label="Draft title" value={view.content.title} disabled={disabled} onChange={(e) => controller.current?.edit({ ...view.content, title: e.target.value })} />
    {preview ? <Preview body={view.content.body} /> : <textarea aria-label="Draft Markdown" value={view.content.body} disabled={disabled} rows={18} onChange={(e) => controller.current?.edit({ ...view.content, body: e.target.value })} />}
  </section>;
});
