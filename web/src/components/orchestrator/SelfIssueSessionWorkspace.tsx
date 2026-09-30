import { useRef, useState } from "react";
import { Minus, X } from "lucide-react";
import { selfIssuePublishedUrls } from "./selfIssue";
import { SelfIssueDraftEditor, type DraftEditorHandle } from "./SelfIssueDraftEditor";

type RecordData = Record<string, unknown>;
type Action = (action: string, payload: RecordData) => unknown | Promise<unknown>;
const obj = (value: unknown): RecordData => value && typeof value === "object" && !Array.isArray(value) ? value as RecordData : {};
const list = (value: unknown): RecordData[] => Array.isArray(value) ? value.map(obj) : [];
const text = (value: unknown): string => value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);

function Activity({ items, load }: { items: RecordData[]; load: (id: string) => Promise<string> }) {
  const [results, setResults] = useState<Record<string, string>>({});
  return <details><summary>活动详情（{items.length}）</summary>{items.map((item, i) => {
    const id = text(item.id) || String(i);
    return <article key={id} className="self-issue-activity-item">
      <strong>{text(item.label)}</strong> <small>{text(item.at)} · {text(item.status)}</small>
      <div>{text(item.tool)} {text(item.path || item.target || item.pattern)}</div>
      {item.command ? <details><summary>执行命令</summary><pre>{text(item.command)}</pre></details> : null}
      {item.result_summary ? <pre>{text(item.result_summary)}</pre> : null}
      {item.result_ref || item.result_detail ? <details onToggle={(event) => {
        if (event.currentTarget.open && results[id] === undefined) {
          void load(id).then((body) => setResults((current) => ({ ...current, [id]: body })))
            .catch((error) => setResults((current) => ({ ...current, [id]: `读取失败：${String(error)}` })));
        }
      }}><summary>完整工具结果</summary><pre>{results[id] ?? "正在读取…"}</pre></details> : null}
    </article>;
  })}</details>;
}

export function SelfIssueSessionWorkspace({ card, onAction, onUpdate, onClose, onMinimize, publicationMode, authorizationSessionId }: {
  card: RecordData; onAction: Action; onUpdate: (value: RecordData) => void;
  onClose: () => void; onMinimize: () => void; publicationMode: string; authorizationSessionId?: string;
}) {
  const sessionId = text(card.session_id);
  const status = text(card.session_status || card.status);
  const workflow = obj(card.workflow);
  const sourceBinding = obj(card.source_binding);
  const editor = obj(card.editor);
  const history = list(workflow.reanalysis_history);
  const ready = ["ready_for_confirmation", "completed"].includes(status);
  const running = ["investigating", "created"].includes(status);
  const locked = ["published", "publishing", "partially_published", "outcome_unknown"].includes(text(editor.publication_state));
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [answer, setAnswer] = useState("");
  const [source, setSource] = useState(-1);
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [oldVersion, setOldVersion] = useState<RecordData | null>(null);
  const editorRef = useRef<DraftEditorHandle | null>(null);
  const report = source >= 0 ? obj(history[source]?.report) : ready ? obj(card.report) : obj(history.at(-1)?.report);
  const call = async (action: string, payload: RecordData = {}) => {
    const result = obj(await onAction(action, { session_id: sessionId, ...payload }));
    if (result.ok === false && result.status !== "authorization_required") {
      throw new Error(text(result.reason || result.message || result.status));
    }
    return result;
  };
  const save = async () => { await editorRef.current?.flush(); };
  const perform = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const loadActivity = async (id: string) => text((await call("self-issue-session-activity-result", { activity_id: id })).body);
  const generate = async () => {
    await save();
    const result = await call("self-issue-session-materialize", { report_index: source });
    onUpdate(result); setPreview(false);
    setNotice(`已生成 Draft v${Number(obj(result.editor).revision)}；此前版本仍保留`);
  };
  const publish = async () => {
    await save();
    const publication = await call("self-issue-preview", { draft_id: editor.draft_id, publication_mode: publicationMode });
    onUpdate(publication);
    const result = await call("self-issue-publish", { batch_id: publication.batch_id, implicit_publish: true });
    onUpdate(result);
    setNotice(result.status === "authorization_required" ? "请完成发布授权" : text(result.status));
  };
  const publishedUrls = selfIssuePublishedUrls(card);

  return <div role="dialog" aria-label="Issue Agent session" data-testid="self-issue-session-card" className="headless-pending-entry self-issue-draft-card expanded self-issue-workspace">
    <div className="self-issue-card-header"><strong>Issue Agent · {status}</strong><div className="self-issue-card-controls">
      <button aria-label="最小化 Issue Agent session" type="button" onClick={() => {
        // Folding keeps this workspace mounted. Never gate local navigation on
        // network saves, publication, or a recoverable Draft conflict.
        void editorRef.current?.persist();
        onMinimize();
      }}><Minus size={16} /></button>
      <button aria-label="关闭 Issue Agent session" type="button" disabled={busy} onClick={() => void perform(async () => { await save(); onClose(); })}><X size={16} /></button>
    </div></div>
    <div className="self-issue-workspace-scroll">
      <p role="status">{text(workflow.label || workflow.phase)} · {text(workflow.activity)}<br />{text(workflow.summary)}</p>
      {Object.keys(sourceBinding).length ? <details><summary>授权源码上下文</summary>
        <p>模式：{text(sourceBinding.mode || "current_project")} · 仓库根：{text(sourceBinding.repository_root || sourceBinding.workspace_root || "未绑定")} · revision：{text(sourceBinding.revision || "未解析")}</p>
      </details> : null}
      {error ? <p role="alert" className="operator-error">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {["paused", "failed"].includes(status) ? <section>
        <p>{text(workflow.interruption_source) === "user" ? "用户中断：未完成的分析不作为新报告产出。" : "分析中断："}{text(workflow.reason || card.reason)}</p>
        <button type="button" disabled={busy} onClick={() => void perform(async () => onUpdate(await call("self-issue-session-retry")))}>从最近安全检查点恢复</button>
      </section> : null}
      {card.question ? <section><p>{text(card.question)}</p><textarea aria-label="Issue Agent answer" value={answer} onChange={(e) => setAnswer(e.target.value)} />
        <button type="button" disabled={busy || !answer.trim()} onClick={() => void perform(async () => { onUpdate(await call("self-issue-session-answer", { answer })); setAnswer(""); })}>提交回答并继续</button>
      </section> : null}
      <section aria-label="各轮分析活动"><h4>分析活动</h4>
        {history.map((round, index) => <div key={index}><strong>{index === 0 ? "首次分析" : `再次分析 #${index}`}</strong><Activity items={list(round.activity_log)} load={loadActivity} /></div>)}
        <strong>{history.length ? `再次分析 #${history.length}` : "首次分析"}</strong><Activity items={list(workflow.activity_log)} load={loadActivity} />
      </section>
      <section aria-label="Final Issue Report"><h4>Issue Report</h4>
        {history.length ? <label>报告版本 <select aria-label="报告版本" value={source} onChange={(e) => { setSource(Number(e.target.value)); setSelected({}); }}>
          <option value={-1}>{ready ? "当前分析结果" : "最近完整报告（本轮未完成）"}</option>
          {history.map((_, i) => <option key={i} value={i}>{i === 0 ? "首次报告" : `报告 #${i}`}</option>)}
        </select></label> : null}
        {history.length && ready ? <details open><summary>原结果与再次分析结果对比（不会覆盖历史报告）</summary><div className="self-issue-report-comparison">
          <div><strong>原结果</strong><pre>{text(history.at(-1)?.report)}</pre></div><div><strong>再次分析结果</strong><pre>{text(card.report)}</pre></div>
        </div><details><summary>本轮证据</summary><pre>{text(obj(card.working_set).evidences)}</pre></details></details> : null}
        {Object.entries(report).map(([field, value]) => <div key={field} className="self-issue-report-field">
          <label><input type="checkbox" checked={field in selected} disabled={!ready || locked} onChange={(e) => setSelected((current) => {
            const next = { ...current }; if (e.target.checked) next[field] = ""; else delete next[field]; return next;
          })} /> <strong>{field}</strong></label><pre>{text(value)}</pre>
          {field in selected ? <textarea aria-label={`${field} 补充说明`} placeholder="针对这一项的 comment（可选）" value={selected[field]} onChange={(e) => setSelected({ ...selected, [field]: e.target.value })} /> : null}
        </div>)}
        <details><summary>证据与验证摘要</summary><pre>{text(source >= 0 ? { evidences: history[source]?.evidences, validation: history[source]?.validation } : { evidences: obj(card.working_set).evidences, validation: workflow.validation })}</pre></details>
      </section>
      {editor.draft_id ? <section aria-label="Issue Draft editor">
        <SelfIssueDraftEditor ref={editorRef} sessionId={sessionId} editor={editor} preview={preview} busy={busy} onAction={onAction} onSaved={(result) => onUpdate(result)} />
        {list(editor.attachments).length ? <details><summary>随 Issue 发布的附件（{list(editor.attachments).length}）</summary>
          {list(editor.attachments).map((item, i) => <p key={i}>{text(item.filename)} · source={text(item.source)} · {text(item.description)} · purpose: {text(item.purpose)} · SHA-256 {text(item.sha256)}</p>)}
        </details> : null}
        {list(editor.attachment_warnings).length ? <details open><summary>未加入发布附件的证据</summary><pre>{text(editor.attachment_warnings)}</pre></details> : null}
        <details><summary>Draft 历史版本</summary>{(Array.isArray(editor.versions) ? editor.versions : []).map((version) => <button key={text(version)} type="button" onClick={() => void perform(async () => setOldVersion(await call("self-issue-session-draft-version", { version })))}>v{text(version)}</button>)}
          {oldVersion ? <div><strong>{text(oldVersion.title)}</strong><pre>{text(oldVersion.body)}</pre></div> : null}
        </details>
      </section> : null}
      {text(card.status) === "authorization_required" && !card.transaction_id ? <button type="button" onClick={() => void perform(async () => {
        onUpdate(await call("self-issue-github-device-start", {
          session_id: authorizationSessionId || sessionId,
          draft_id: editor.draft_id, batch_id: card.batch_id, confirmation_id: card.confirmation_id,
        }));
      })}>授权 GitHub 发布</button> : null}
      {card.verification_uri ? <p><a href={text(card.verification_uri)} target="_blank" rel="noreferrer">打开授权页面</a> · {text(card.user_code)}</p> : null}
      {text(editor.publication_state) === "published" ? <p>Issue 已发布，可通过右上角 × 关闭会话。</p> : null}
      {Object.entries(publishedUrls).map(([provider, url]) => <p key={provider}><a href={url} target="_blank" rel="noreferrer">查看已发布 Issue（{provider}）</a></p>)}
      {["partially_published", "outcome_unknown", "publish_failed"].includes(text(editor.publication_state)) ? <button type="button" disabled={busy} onClick={() => void perform(async () => {
        onUpdate(await call("self-issue-recover", { intent_id: card.intent_id || Object.values(obj(card.intent_ids))[0] || Object.values(obj(obj(card.publication_batch).intent_ids))[0] }));
      })}>继续发布／核对发布结果</button> : null}
    </div>
    <div className="self-issue-publication-actions self-issue-workspace-actions">
      <button type="button" disabled={busy || !ready || locked || !Object.keys(selected).length} onClick={() => void perform(async () => {
        await save(); onUpdate(await call("self-issue-session-reanalyze", { selected_fields: Object.keys(selected), comment: JSON.stringify(selected) })); setSelected({}); setSource(-1);
      })}>再次分析</button>
      <button type="button" disabled={busy || locked || (!ready && source < 0)} onClick={() => void perform(generate)}>生成 Draft</button>
      <button type="button" disabled={!editor.draft_id} onClick={() => setPreview((value) => !value)}>{preview ? "编辑 Draft" : "查看预览"}</button>
      <button type="button" disabled={busy || !editor.draft_id || locked || running} onClick={() => void perform(publish)}>发布</button>
      {running ? <button type="button" disabled={busy} onClick={() => void perform(async () => onUpdate(await call("self-issue-session-pause")))}>中断分析</button> : null}
    </div>
  </div>;
}
