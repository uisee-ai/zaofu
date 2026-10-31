import { createContext, useContext } from "react";
import type { PlanPreview } from "../../api/types";
import { object, planArtifactUrl, rows, strings, text } from "./planReviewModel";

export const PlanSourceContext = createContext<((name: string, anchor: string) => void) | null>(null);
export function SourceRefs({ refs, preview, projectId, label = "来源条款" }: { refs: string[]; preview: PlanPreview; projectId: string; label?: string }) {
  const open = useContext(PlanSourceContext);
  return refs.length ? <details className="plan-review-sources"><summary>{label}（{refs.length}）</summary>{refs.map((ref, i) => {
    const [path, anchor = ""] = ref.split("#", 2);
    const bound = preview.artifacts?.find(p => p.ref === path);
    return <p key={i}>{bound ? <a href={`${planArtifactUrl(projectId, preview.plan_id, bound.name)}${anchor ? `#${encodeURIComponent(anchor)}` : ""}`} target="_blank" rel="noreferrer" onClick={event => { if (open && /\.md$/.test(path)) { event.preventDefault(); open(bound.name, anchor); } }}>{ref}</a> : <code>{ref}</code>}</p>;
  })}</details> : <p className="muted">未提供{label}。</p>;
}
export function DecisionList({ decisions, preview, projectId }: { decisions: Record<string, unknown>[]; preview: PlanPreview; projectId: string }) {
  if (!decisions.length) return <p>未列出相关决定；不代表所有问题均已关闭。</p>;
  return <>{decisions.map((decision, i) => <details className="plan-review-card" key={`${text(decision.decision_id)}-${i}`}>
    <summary>{text(decision.decision_id)} · {text(decision.status) || "状态未声明"} · {text(decision.question || decision.title)}</summary>
    <p>负责人：{text(decision.named_owner || decision.owner) || "待指定"} · {text(decision.responsible_role)}</p>
    <p>最迟确认：{text(decision.enter_before || decision.latest_gate) || "未声明"} · 影响任务：{strings(decision.affected_task_ids).join("、") || text(decision.producer_task_id) || "未声明"}</p>
    <p>关闭条件：{text(decision.done_when || decision.closure_evidence) || "未声明"}</p>
    <p>当前阻塞：{decision.blocking === true ? "是" : decision.blocking === false ? "否（仍须遵守最迟确认条件）" : "未声明"}</p>
    <p>{text(decision.pending_boundary)} {text(decision.approval_scope)} {text(decision.blocker_impact)}</p>
    {rows(decision.options).map((option, index) => <p key={index}>{text(object(option).label)}</p>)}
    <SourceRefs refs={strings(decision.evidence_refs)} preview={preview} projectId={projectId} />
  </details>)}</>;
}
