import { useState } from "react";
import "./libreDiscussion.css";
import type { ChannelDetail } from "../../api/types";
import type { ChannelPrdCandidate } from "../../api/channelDelivery";
import { MarkdownText } from "../agent-session/MarkdownText";
export type PrdMaterials = { text?: string; includeProjectBrief?: boolean; retryRequestId?: string };

export function LibreDiscussionNotice({ detail, threadId }: { detail: ChannelDetail | null; threadId: string }) {
  const rows = Object.values(detail?.libre_rounds ?? {}).filter((row) => row.thread_id === threadId);
  const pending = rows.filter((row) => !Object.keys((row.publication as object) ?? {}).length);
  const warningCount = rows.reduce((sum, row) => sum + (Array.isArray(row.warnings) ? row.warnings.length : 0), 0);
  const questions = Array.isArray(detail?.open_questions) ? detail.open_questions : Object.values(detail?.open_questions ?? {});
  const raw = questions.filter((q) => q.thread_id === threadId && q.status === "open" && q.dedup_status === "failed_raw");
  return <div className="channel-control-panel libre-discussion-notice" data-testid="libre-discussion-notice">
    <p className="muted">自由讨论：直接发消息由主持人接话，@角色单独交流，@all让全体成员参与本轮。你决定何时生成 PRD。</p>
    {warningCount ? <p role="alert">有成员的问题或邀请建议未能应用，请查看其回复后继续沟通；聊天和 PRD 入口仍可使用。</p> : null}
    {pending.length ? <p role="status">{pending.length} 轮回复或问题正在整理；你可以继续聊天。</p> : null}
    {raw.length ? <p role="status">有 {raw.length} 道问题未完成去重，已按原题呈现，可以直接回答。</p> : null}
  </div>;
}

export function LibrePrdControls({ requests, actionReady, busy, onGenerate, onCancel, onRecover, onReadCandidate }: {
  requests: Array<Record<string, unknown>>; actionReady: boolean; busy: boolean;
  onGenerate: (materials: PrdMaterials) => Promise<void>;
  onCancel?: (requestId: string) => Promise<void>;
  onRecover?: (requestId: string) => Promise<void>;
  onReadCandidate?: (requestId: string) => Promise<ChannelPrdCandidate>;
}) {
  const [text, setText] = useState("");
  const [includeProjectBrief, setIncludeBrief] = useState(false);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [candidate, setCandidate] = useState<ChannelPrdCandidate | null>(null);
  const [loadingCandidate, setLoadingCandidate] = useState(false);
  const submit = async (materials: PrdMaterials) => {
    setWorking(true); setError("");
    try { await onGenerate(materials); }
    catch (reason: unknown) { setError(String(reason instanceof Error ? reason.message : reason)); }
    finally { setWorking(false); }
  };
  const latest = requests.at(-1);
  const needsRecovery = latest?.recovery_required === true;
  const snapshot = requests.slice().reverse().map((r) => r.prd_input_snapshot as Record<string, unknown> | undefined).find((s) => s?.cutoff_at);
  return <section className="channel-control-panel libre-prd-controls" data-testid="libre-prd-controls">
    <strong>按当前内容生成 PRD</strong>
    <p className="muted">采用点击时已保存的聊天和答案。后续回复与补充留给下一版，生成期间可以继续聊天。</p>
    <label><input type="checkbox" checked={includeProjectBrief} onChange={(e) => setIncludeBrief(e.target.checked)} /> 同时采用项目简介</label>
    <textarea className="filter-input" aria-label="补充 PRD 原材料" placeholder="可选：补充说明或粘贴其他原材料" value={text} onChange={(e) => setText(e.target.value)} />
    {!actionReady ? <p role="status">当前会话尚未获得操作权限，请先完成页面授权。</p> : null}
    {snapshot?.cutoff_at ? <p>本次材料截至 {String(snapshot.cutoff_at)}；后续补充用于下一版。</p> : null}
    {latest ? <p role="status">当前生成请求：{({ requested: "排队或生成中", repair_requested: "正在修复格式", proposed: "草稿已生成，待评估", completed: "草稿已生成，待评估", blocked: "生成失败，请查看原因", failed: "生成失败", cancelled: "已停止采用本次结果" } as Record<string, string>)[String(latest.status)] || "已受理"}</p> : null}
    {latest?.reason && ["failed", "blocked"].includes(String(latest.status)) ? <p role="status">失败原因：{String(latest.reason)}</p> : null}
    {latest?.contract_error ? <details><summary>查看当前校验未通过的原因</summary><p>{String(latest.contract_error)}</p></details> : null}
    {needsRecovery ? <div role="status">
      <p>原请求的最终交付结果尚未确认，重新生成暂不可用。可以检查并结束失败请求，不再采用其迟到结果；聊天、原材料和旧版 PRD 保留，之后由你手动重试。</p>
      {onRecover ? <button className="icon-button" type="button" disabled={!actionReady || busy || working} onClick={() => {
        setWorking(true); setError("");
        void onRecover(String(latest.request_id))
          .catch((reason: unknown) => setError(String(reason instanceof Error ? reason.message : reason)))
          .finally(() => setWorking(false));
      }}>检查并结束失败请求</button> : null}
    </div> : null}
    {(latest?.candidate_available || latest?.checkpoint_draft_available) && onReadCandidate ? <button className="icon-button" type="button" disabled={loadingCandidate} onClick={() => {
      setLoadingCandidate(true); setError("");
      void onReadCandidate(String(latest.request_id)).then(setCandidate)
        .catch((reason: unknown) => setError(String(reason instanceof Error ? reason.message : reason)))
        .finally(() => setLoadingCandidate(false));
    }}>{loadingCandidate ? "读取草稿…" : latest?.candidate_available ? "查看未通过校验的候选稿" : "查看本次草稿（尚未发布）"}</button> : null}
    {candidate && candidate.request_id === latest?.request_id ? <details open className="libre-prd-candidate">
      <summary>{candidate.status === "checkpoint_draft" ? "本次已保存草稿 · 尚未发布" : "未通过校验的候选内容，不能用于创建任务"}</summary>
      {candidate.reason ? <p role="status">{candidate.reason}</p> : null}
      <MarkdownText content={candidate.markdown} />
    </details> : null}
    {onCancel && latest && !needsRecovery && ["requested", "repair_requested"].includes(String(latest.status)) ? <button className="icon-button" type="button" disabled={!actionReady || busy || working} onClick={() => {
      void onCancel(String(latest.request_id)).catch((reason: unknown) => setError(String(reason instanceof Error ? reason.message : reason)));
    }}>停止本次生成（保留聊天和旧版 PRD）</button> : null}
    {latest && ["blocked", "failed", "cancelled"].includes(String(latest.status)) ? <button className="icon-button" type="button" disabled={!actionReady || busy || working || needsRecovery} onClick={() => {
      void submit({ retryRequestId: String(latest.request_id) });
    }}>{latest.synthesis_pipeline === "checkpointed.v1" ? "重试未完成部分（沿用原材料）" : "重试本次（沿用原材料）"}</button> : null}
    {error ? <p role="alert">{error}</p> : null}
    <button className="icon-button primary" type="button" disabled={!actionReady || busy || working || needsRecovery} onClick={() => {
      void submit({ text, includeProjectBrief });
    }}>{busy || working ? "提交中…" : "生成 PRD / 查看本次进度"}</button>
  </section>;
}
