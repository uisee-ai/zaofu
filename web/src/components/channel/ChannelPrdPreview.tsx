import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, Copy, Download, Maximize2, Minimize2, Play, RotateCcw, X } from "lucide-react";
import { getChannelPrdDocument, getChannelPrdEvaluation, type ChannelPrdDocument, type ChannelPrdEvaluationView, type ChannelPrdSelection } from "../../api/channelDelivery";
import { MarkdownText } from "../agent-session/MarkdownText";
import { prdMarkdownPages } from "./prdMarkdownPages";
import type { ChannelFlow } from "./channelFlow";
import { channelEvaluationFinding } from "./channelEvaluationFinding";

/** Mounted with an exact project/thread/PRD key; old reads cannot enable Process. */
export function ChannelPrdPreview({ selection, flow, actionReady, busy, onProcess, onEvaluate, onDiscuss, onClose, children }: {
  selection: ChannelPrdSelection; flow: ChannelFlow; actionReady: boolean; busy: boolean;
  onProcess: () => Promise<void>; onEvaluate: (role: "full" | "focused", retry: boolean) => Promise<void>;
  onDiscuss: () => void; onClose: () => void; children?: ReactNode;
}) {
  const [document, setDocument] = useState<ChannelPrdDocument>();
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [source, setSource] = useState(false);
  const [page, setPage] = useState(0);
  const [copied, setCopied] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [evaluation, setEvaluation] = useState<ChannelPrdEvaluationView>();
  const [evaluationError, setEvaluationError] = useState("");
  const [evaluationAttempt, setEvaluationAttempt] = useState(0);
  const [evaluatingRole, setEvaluatingRole] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const fullscreenButtonRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.close();
    if (fullscreen) dialog.showModal();
    else dialog.show();
    fullscreenButtonRef.current?.focus({ preventScroll: true });
  }, [fullscreen]);
  useEffect(() => {
    if (!flow.hasArtifact) return;
    const controller = new AbortController();
    setError("");
    setDocument(undefined);
    getChannelPrdDocument(selection, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.artifact_ref !== flow.artifactRef || value.artifact_digest.replace(/^sha256:/, "") !== flow.artifactDigest.replace(/^sha256:/, "")
        || value.revision !== flow.revision || value.thread_id !== selection.threadId || value.channel_id !== selection.channelId) {
        throw new Error("PRD version mismatch. Reopen the latest preview.");
      }
      setDocument(value);
    }).catch((reason) => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [attempt]); // Identity changes remount this component via its key.
  useEffect(() => {
    if (!document) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      void getChannelPrdEvaluation(selection, controller.signal).then((value) => {
        if (controller.signal.aborted) return;
        if (value.revision !== document.revision || value.artifact_digest.replace(/^sha256:/, "") !== document.artifact_digest.replace(/^sha256:/, "")) {
          throw new Error("Evaluation version mismatch. Reopen the latest PRD.");
        }
        setEvaluation(value);
        setEvaluationError("");
        if (value.evaluations.some((row) => row.status === "requested" || row.feedback?.status === "requested")) timer = setTimeout(load, 10000);
      }).catch((reason) => { if (!controller.signal.aborted) setEvaluationError(String(reason)); });
    };
    load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [document, evaluationAttempt]);
  const pages = useMemo(() => prdMarkdownPages(document?.markdown ?? ""), [document?.markdown]);
  const currentPage = pages[Math.min(page, pages.length - 1)];
  const changePage = (next: number) => {
    setPage(next);
    bodyRef.current?.scrollTo({ top: 0 });
  };
  const generating = flow.statuses.prd === "Generating";
  const disabledReason = generating ? "完整 PRD 生成并通过当前版本复审后，才能规划 Task。" : !document ? "Load and verify the full PRD first."
    : !flow.ready ? "Confirm the current PRD and resolve open questions first."
    : !actionReady ? "This session is read-only. Unlock actions to continue."
    : submitted ? "Planning requested. Review the plan in Kanban Agent."
    : flow.tasks.length ? "This version already has linked Tasks. Review them before requesting another plan. Execution still requires separate approval."
    : "Process opens Kanban Agent to plan from this PRD version. Task creation and Workflow execution each require approval; results return to this Channel.";
  return <dialog ref={dialogRef} className={`channel-document channel-prd-document${fullscreen ? " is-fullscreen" : ""}`}
    aria-label="PRD preview" aria-modal={fullscreen} data-testid="channel-prd-preview"
    onCancel={(event) => { event.preventDefault(); setFullscreen(false); }}>
    <div className="channel-document-toolbar">
      <span className="channel-prd-file-title"><strong>PRD{flow.revision ? `-v${flow.revision}` : ""}.md</strong> <small>{flow.statuses.prd}</small></span>
      <div>{document ? <>
        <div className="channel-prd-view-switch" role="group" aria-label="Document view">
          <button type="button" className="icon-button" aria-pressed={!source} onClick={() => setSource(false)}>Preview</button>
          <button type="button" className="icon-button" aria-pressed={source} onClick={() => setSource(true)}>Source</button>
        </div>
        <button type="button" className="icon-button" aria-label="Copy PRD" onClick={() => {
          void navigator.clipboard.writeText(document.markdown).then(() => setCopied(true)).catch(() => setActionError("Copy unavailable. Use Download instead."));
        }}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
        <button type="button" className="icon-button" aria-label="Download PRD" onClick={() => {
          const href = URL.createObjectURL(new Blob([document.markdown], { type: "text/markdown;charset=utf-8" }));
          const link = window.document.createElement("a"); link.href = href; link.download = `PRD-v${document.revision}.md`; link.click();
          setTimeout(() => URL.revokeObjectURL(href), 1000);
        }}><Download size={14} /></button>
      </> : null}
        <button ref={fullscreenButtonRef} type="button" className="icon-button"
          aria-label={fullscreen ? "Exit PRD fullscreen" : "Fullscreen PRD"}
          title={fullscreen ? "返回分屏（Esc）" : "全屏阅读"} onClick={() => setFullscreen(!fullscreen)}>
          {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button type="button" className="icon-button" title="Close drawer" onClick={onClose}><X size={14} /></button></div>
    </div>
    <div ref={bodyRef} className="channel-document-body">
      {flow.generationError ? <div role="alert" data-testid="channel-prd-generation-error">
        <p>PRD 生成受阻：{flow.generationError}</p>
        <p>请处理该原因后重试；从文件生成的草案，请重新打开原文件并选择“生成正式 PRD 草案”。</p>
      </div> : null}
      {!flow.hasArtifact ? <p role="status">{generating ? "正在生成完整 PRD。请等待当前综合结果，无需重复触发。" : "尚无完整 PRD。请先完成当前讨论与审查。"}</p>
        : !document && !error ? <p role="status">Loading full PRD…</p> : null}
      {error ? <div role="alert"><p>Preview unavailable. No summary has been substituted for the document.</p><small>{error}</small>
        <button className="icon-button" type="button" onClick={() => setAttempt(attempt + 1)}><RotateCcw size={14} />Retry</button></div> : null}
      {document ? <>
        {currentPage.plain && !source ? <p className="muted">本节内容过长，已使用分页原文显示；复制和下载包含完整文档。</p> : null}
        {source || currentPage.plain ? <pre data-testid="channel-prd-source">{currentPage.content}</pre>
          : <MarkdownText content={currentPage.content} />}
        <details className="channel-document-evidence"><summary>Version &amp; integrity</summary><code>{document.artifact_ref}</code><code>sha256:{document.artifact_digest}</code></details>
        <section className="channel-prd-evaluation" data-testid="channel-prd-evaluation">
          <strong>独立 PRD 评估</strong><p className="muted">未确认的群会把发现交给成员核实：文档缺陷进入修订，产品取舍交给 Owner。原始报告结论保留，执行仍需批准。新版 PRD 需重新评估。</p>
          {evaluationError ? <p role="alert">{evaluationError}</p> : null}
          {(["full", "focused"] as const).map((role) => {
            const row = evaluation?.evaluations.find((item) => item.role === role);
            const label = role === "full" ? "全文评估" : "权限与验收专项";
            return <div key={role} className="channel-prd-evaluation-row">
              <span>{label} · {row ? `${row.status}${row.verdict ? ` / ${row.verdict}` : ""}` : "未开始"}</span>
              <button type="button" className="icon-button" data-testid={`channel-prd-evaluate-${role}`}
                disabled={!actionReady || busy || !document || !!evaluatingRole || row?.status === "requested"}
                onClick={() => { setEvaluatingRole(role); setEvaluationError("");
                  void onEvaluate(role, row?.status === "failed" || row?.status === "invalid_evidence" || row?.status === "completed")
                    .then(() => setEvaluationAttempt((value) => value + 1))
                    .catch((reason) => setEvaluationError(String(reason)))
                    .finally(() => setEvaluatingRole("")); }}>
                {evaluatingRole === role ? "请求中…" : row?.status === "failed" || row?.status === "invalid_evidence" || row?.status === "completed" ? "重新评估" : "启动评估"}
              </button>
              {row?.reason ? <p role="alert">{row.reason}</p> : null}
              {row?.feedback ? <p data-testid="channel-evaluation-feedback" className="muted">
                群内处置：{row.feedback.status === "requested" ? "已送回成员评审" : row.feedback.status === "reviewed" ? "已记录逐项处置" : row.feedback.status === "skipped" ? "已跳过" : "提交失败"}
                {row.feedback.reason ? ` · ${row.feedback.reason}` : ""}
                {row.feedback.review ? <code>{row.feedback.review.ref}</code> : null}
              </p> : null}
              {row?.findings?.length ? <details><summary>发现 {row.findings.length} 项</summary>
                {row.findings.map((finding, index) => {
                  const display = channelEvaluationFinding(finding);
                  return <div key={index} data-testid="channel-evaluation-finding">
                    <p><strong>{display.severity}{display.criterion ? ` · ${display.criterion}` : ""}</strong> {display.body}</p>
                    {display.source ? <p className="muted">来源：{display.source}</p> : null}
                    {display.target ? <p className="muted">目标：{display.target}</p> : null}
                    <details><summary>原始数据</summary><pre>{JSON.stringify(finding, null, 2)}</pre></details>
                  </div>;
                })}
              </details> : null}
              {row?.report_ref ? <details><summary>报告证据</summary><code>{row.report_ref}</code><code>sha256:{row.report_digest}</code></details> : null}
            </div>;
          })}
        </section>
        {children}
      </> : null}
    </div>
    {document && pages.length > 1 ? <nav className="channel-document-pagination" aria-label="PRD pages">
      <button type="button" className="icon-button" disabled={page === 0} onClick={() => changePage(page - 1)}>Previous</button>
      <span>{page + 1} / {pages.length}</span>
      <button type="button" className="icon-button" disabled={page + 1 >= pages.length} onClick={() => changePage(page + 1)}>Next</button>
    </nav> : null}
    <footer className="channel-document-footer">
      <p>{disabledReason}</p>
      {actionError ? <p role="alert">{actionError}</p> : null}
      <div><button type="button" className="icon-button" onClick={onDiscuss}>Back to discussion</button>
        <button type="button" className="icon-button primary" data-testid="channel-create-task-from-prd"
          disabled={!document || !flow.ready || !actionReady || busy || submitting || submitted}
          onClick={() => {
            if (submitting) return;
            setActionError("");
            setSubmitting(true);
            void onProcess().then(() => { setSubmitted(true); setFullscreen(false); }).catch((reason) => setActionError(String(reason))).finally(() => setSubmitting(false));
          }}><Play size={14} />{submitting ? "Processing…" : submitted ? "Plan requested" : "Process"}</button>
      </div>
    </footer>
  </dialog>;
}
