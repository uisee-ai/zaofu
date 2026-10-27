import { lazy, Suspense, useEffect, useRef } from "react";
import { Check, X, MessageSquare } from "lucide-react";
import type { PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";
const PlanReviewReader = lazy(() => import("./PlanReviewReader"));
const PlanArtifactChat = lazy(() => import("../workflow/PlanArtifactChat"));

export function PlanPreviewOverlay({
  projectId,
  preview,
  loading,
  busy,
  rejectReason,
  onRejectReason,
  editing,
  onRead,
  reviewBusy,
  onReviewBusy,
  onApplied,
  onClose,
  onApprove,
  onReject,
  onRepair,
  ownerDecision,
  decisionReady,
  actionKey,
}: {
  projectId?: string;
  preview: PlanPreview | null;
  loading: boolean;
  busy: string;
  rejectReason: string;
  onRejectReason: (value: string) => void;
  editing: boolean;
  onRead: () => void;
  reviewBusy: boolean;
  onReviewBusy: (value: boolean) => void;
  onApplied: () => Promise<void>;
  onClose: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRepair: () => void;
  ownerDecision: boolean;
  decisionReady: boolean;
  actionKey: string;
}) {
  const modal = useRef<HTMLDialogElement>(null);
  useEffect(() => { const element = modal.current; element?.showModal(); return () => element?.close(); }, []);
  const planId = preview?.plan_id || "";
  const disabled = Boolean(actionKey && busy.endsWith(`:${actionKey}`));
  return (
    <dialog ref={modal} className="plan-preview-overlay" aria-label="Plan preview" onCancel={(event) => { event.preventDefault(); if (!reviewBusy) onClose(); }}>
      <div className="plan-preview-toolbar">
        <div>
          <strong>{preview?.content_profile === "derived_plan_full" ? "完整 PRD Preview" : "Plan Preview"}</strong>
          <span className="mono">{planId || "loading"}</span>
          {preview?.content_profile === "derived_plan_full" && preview.markdown ? (
            <span className="muted">正文 {preview.markdown.length.toLocaleString()} 字符 · Task Map 为辅助拆分</span>
          ) : null}
        </div>
        <div className="plan-preview-toolbar-actions">
          <button type="button" className="delivery-action-button" onClick={onApprove} disabled={!preview || preview.status !== "pending" || !decisionReady || disabled || reviewBusy || loading || preview.content_kind === "unavailable"}>
            <Check size={14} /> {ownerDecision ? "Approve decisions" : "Approve"}
          </button>
          <input
            value={rejectReason}
            placeholder="reject reason"
            onChange={(event) => onRejectReason(event.target.value)}
            disabled={!preview}
          />
          <button type="button" className="delivery-action-button" onClick={onReject} disabled={!preview || preview.status !== "pending" || !decisionReady || disabled || reviewBusy || !rejectReason.trim()}>
            <X size={14} /> Reject
          </button>
          <button type="button" className="delivery-action-button" onClick={editing ? onRead : onRepair} disabled={!preview || disabled || reviewBusy}>
            <MessageSquare size={14} /> {editing ? "返回审阅" : "引用编辑"}
          </button>
          <button type="button" className="icon-button" title="Close preview" onClick={onClose} disabled={reviewBusy}>
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="plan-preview-body">
        <main className={`plan-preview-reader${editing ? " plan-preview-editing" : ""}`}>
          {!loading && preview?.content_kind === "digest_only" && <p role="status">仅有计划摘要，尚未绑定完整 Plan 正文。</p>}
          {!loading && preview?.content_kind === "unavailable" && <p role="alert">完整计划不可用：{preview.content_error || "未找到可读正文"}</p>}
          {loading ? <p className="muted">Loading...</p> : <Suspense fallback={<p className="muted">Loading...</p>}>
            {preview && (editing
              ? <PlanArtifactChat key={`${projectId}:${planId}`} preview={preview} projectId={projectId || "default"} disabled={disabled} onBusy={onReviewBusy} onApplied={onApplied} />
              : <PlanReviewReader key={`${projectId}:${planId}`} preview={preview} projectId={projectId || "default"} />)}
          </Suspense>}
        </main>
        <aside className="plan-preview-context">
          <p className="muted">{editing ? "选中文字，点击「引用到 Agent」说明修改要求，检查 Diff 后 Apply。" : "点击上方「引用编辑」，可选段并通过 Agent 修改。"}</p>
          <dl className="plan-approval-meta">
            <div><dt>正文</dt><dd>{planContentKindLabel(preview)}</dd></div>
            <div><dt>内容契约</dt><dd>{planContentProfileLabel(preview?.content_profile)}</dd></div>
            <div><dt>status</dt><dd>{preview?.status || "-"}</dd></div>
            <div><dt>stage</dt><dd>{preview?.stage_id || "-"}</dd></div>
            <div><dt>tasks</dt><dd>{preview?.task_count ?? preview?.task_map_summary?.task_count ?? "-"}</dd></div>
            <div><dt>decisions</dt><dd>{preview?.decision_count ?? preview?.owner_decision_items?.length ?? 0}</dd></div>
            <div><dt>trace</dt><dd>{preview?.trace_id || "-"}</dd></div>
          </dl>
          {preview?.review_packet && <details open>
            <summary>SDD/TDD 审核包 · {preview.review_packet.status}</summary>
            <ul>
              {preview.review_packet.sections.map((section) => <li key={section.id}>
                <strong>{section.title}</strong> <span className="muted">· {section.status}</span>
                {section.notes?.map((note, index) => <div className="muted" key={index}>{note}</div>)}
              </li>)}
            </ul>
            {(preview.review_packet.diagnostics?.length ?? 0) > 0 && <div role="alert">
              <strong>需要处理的诊断</strong>
              <ul>
                {preview.review_packet.diagnostics?.map((diagnostic) => <li key={diagnostic.code}>
                  <span className="mono">{diagnostic.code}</span> · {diagnostic.message}
                </li>)}
              </ul>
            </div>}
          </details>}
          {preview?.digest_markdown && preview.content_kind !== "digest_only" && <details>
            <summary>计划摘要（机械检查）</summary>
            <MarkdownText content={preview.digest_markdown} />
          </details>}
          {(preview?.owner_decision_items?.length ?? 0) > 0 && <details open>
            <summary>Owner decisions ({preview?.owner_decision_items?.length})</summary>
            <ol>
              {preview?.owner_decision_items?.map((item) => <li key={item.decision_id || item.question}>
                <strong>{item.decision_id || "Decision"}</strong>: {item.question || "Owner decision required"}
              </li>)}
            </ol>
          </details>}
          {(preview?.requirement_artifacts?.length ?? 0) > 0 && <details open>
            <summary>需求原文 ({preview?.requirement_artifacts?.length})</summary>
            <p className="muted">打开原始需求文档核对范围、章节和未决项。</p>
            <ul>
              {preview?.requirement_artifacts?.map((artifact) => <li key={artifact.name}>
                <a href={`/api/projects/${encodeURIComponent(projectId || "default")}/plans/${encodeURIComponent(planId)}/artifacts/${encodeURIComponent(artifact.name)}`} target="_blank" rel="noreferrer">{artifact.name}</a>
              </li>)}
            </ul>
          </details>}
          {(preview?.task_map_summary?.tasks?.length ?? 0) > 0 && <details>
            <summary>任务图 ({preview?.task_map_summary?.task_count ?? 0})</summary>
            <ul>
              {preview?.task_map_summary?.tasks?.map((task) => <li key={task.task_id || task.title}>
                <strong>{task.task_id}</strong> {task.title}
                {task.blocked_by ? <span className="muted"> · blocked by {task.blocked_by}</span> : null}
              </li>)}
            </ul>
          </details>}
          {preview?.task_map_markdown && <details>
            <summary>完整 Task Map 详情</summary>
            <MarkdownText content={preview.task_map_markdown} />
          </details>}
          {preview?.artifacts_error && <p role="alert">计划附件不可用：{preview.artifacts_error}</p>}
          {(preview?.artifacts?.length ?? 0) > 0 && <nav aria-label="Plan artifacts">
            <h3>工程附件（同版）</h3>
            {preview?.artifacts?.map((artifact) => <p key={artifact.name}>
              <a href={`/api/projects/${encodeURIComponent(projectId || "default")}/plans/${encodeURIComponent(planId)}/artifacts/${encodeURIComponent(artifact.name)}`} target="_blank" rel="noreferrer">{artifact.name}</a>
            </p>)}
          </nav>}
          <details><summary>版本与来源引用</summary>
            <pre className="delivery-raw-block">{JSON.stringify(preview?.refs ?? {}, null, 2)}</pre>
          </details>
        </aside>
      </div>
    </dialog>
  );
}

function planContentProfileLabel(profile?: string): string {
  switch (profile) {
    case "derived_plan_full": return "完整派生 Plan";
    case "summary_only": return "仅摘要";
    case "derived_plan": return "派生 Plan（需核对完整性）";
    default: return "未声明";
  }
}

function planContentKindLabel(preview: PlanPreview | null): string {
  if (!preview || preview.content_kind === "unavailable") return "不可用";
  if (preview.content_profile === "summary_only" || preview.content_kind === "digest_only") return "仅摘要";
  if (preview.content_profile === "derived_plan_full") return "完整 Plan";
  return "Plan 正文（完整性未声明）";
}
